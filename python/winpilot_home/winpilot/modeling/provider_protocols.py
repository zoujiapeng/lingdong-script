"""Clean-room wire adapters for non-Chat-Completions model APIs.

The Agent keeps one OpenAI-style ``messages``/``tools`` contract.  This module
only translates that contract at the provider boundary and normalizes one
non-streaming response back to the shape already consumed by the Actor.  It
does not own retries, task routing, prompts, or tool execution.
"""

from __future__ import annotations

import base64
import json
import math
import threading
import uuid
from collections import OrderedDict
from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

_FAMILY_ALIASES = {
    "openai_responses": "openai_responses",
    "responses": "openai_responses",
    "anthropic": "anthropic",
    "claude": "anthropic",
    "gemini": "gemini",
    "google": "gemini",
}
_MAX_REPLAY_ITEMS = 256
_ANTHROPIC_IMAGE_MEDIA_TYPES = frozenset(
    {"image/jpeg", "image/png", "image/gif", "image/webp"}
)


class ProviderProtocolError(ValueError):
    """The canonical request or provider response violates its wire contract."""


class ProviderHTTPError(RuntimeError):
    """Structured HTTP failure understood by ``ModelCallRuntime``."""

    def __init__(self, status_code: int, body: Any) -> None:
        self.status_code = int(status_code)
        self.body = body
        super().__init__(f"provider HTTP {self.status_code}")


class RequestsJSONTransport:
    """One JSON POST, with connection reuse and no retry owner."""

    def __init__(self, session: Any | None = None) -> None:
        if session is None:
            import requests

            session = requests.Session()
        self._session = session

    def post_json(
        self,
        *,
        url: str,
        headers: Mapping[str, str],
        body: Mapping[str, Any],
        timeout: float,
    ) -> Mapping[str, Any]:
        response = self._session.post(
            url,
            headers=dict(headers),
            json=dict(body),
            timeout=timeout,
        )
        try:
            payload = response.json()
        except Exception as exc:
            payload = {"error": {"message": response.text or "non-JSON response"}}
            if int(response.status_code) < 400:
                raise ProviderProtocolError(
                    "provider returned non-JSON success"
                ) from exc
        if int(response.status_code) >= 400:
            raise ProviderHTTPError(int(response.status_code), payload)
        if not isinstance(payload, Mapping):
            raise ProviderProtocolError("provider response must be a JSON object")
        return payload

    def close(self) -> None:
        close = getattr(self._session, "close", None)
        if callable(close):
            close()


def normalize_provider_family(value: str) -> str:
    key = str(value or "").strip().lower().replace("-", "_")
    family = _FAMILY_ALIASES.get(key)
    if family is None:
        raise ProviderProtocolError(f"unsupported provider family {value!r}")
    return family


def provider_endpoint(family: str, base_url: str, model: str) -> str:
    family = normalize_provider_family(family)
    base = str(base_url or "").strip().rstrip("/")
    if not base:
        base = {
            "openai_responses": "https://api.openai.com/v1",
            "anthropic": "https://api.anthropic.com/v1",
            "gemini": "https://generativelanguage.googleapis.com/v1beta",
        }[family]
    parsed = urlsplit(base)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ProviderProtocolError("provider base_url must be an absolute HTTP URL")
    path = parsed.path.rstrip("/")
    if not path:
        path = "/v1beta" if family == "gemini" else "/v1"
    lower = path.lower()
    if family == "openai_responses":
        if not lower.endswith("/responses"):
            path += "/responses"
    elif family == "anthropic":
        if not lower.endswith("/messages"):
            path += "/messages"
    elif not lower.endswith(":generatecontent"):
        path += f"/models/{quote(model, safe='')}:generateContent"
    return urlunsplit(parsed._replace(path=path))


def provider_headers(
    family: str,
    api_key: str,
    custom_headers: Mapping[str, str] | None = None,
) -> dict[str, str]:
    family = normalize_provider_family(family)
    headers = {"content-type": "application/json"}
    if family == "openai_responses":
        headers["authorization"] = f"Bearer {api_key}"
    elif family == "anthropic":
        headers.update({"x-api-key": api_key, "anthropic-version": "2023-06-01"})
    else:
        headers["x-goog-api-key"] = api_key
    if custom_headers:
        headers.update({str(key): str(value) for key, value in custom_headers.items()})
    return headers


def _message_list(request: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    messages = request.get("messages")
    if isinstance(messages, (str, bytes)) or not isinstance(messages, Sequence):
        raise ProviderProtocolError("messages must be an array")
    if any(not isinstance(message, Mapping) for message in messages):
        raise ProviderProtocolError("every message must be an object")
    return list(messages)


def _content_parts(content: Any) -> list[Mapping[str, Any]]:
    if content is None or content == "":
        return []
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    if isinstance(content, (str, bytes)) or not isinstance(content, Sequence):
        raise ProviderProtocolError("message content must be text or an array")
    if any(not isinstance(part, Mapping) for part in content):
        raise ProviderProtocolError("message content parts must be objects")
    return list(content)


def _data_image(part: Mapping[str, Any]) -> tuple[str, str, str]:
    raw = part.get("image_url")
    detail = "auto"
    if isinstance(raw, Mapping):
        detail = str(raw.get("detail") or detail)
        raw = raw.get("url")
    if not isinstance(raw, str) or not raw.startswith("data:") or ";base64," not in raw:
        raise ProviderProtocolError("native provider images require a base64 data URL")
    header, data = raw.split(",", 1)
    media_type = header[5:].split(";", 1)[0].strip().lower()
    if not media_type.startswith("image/") or not data:
        raise ProviderProtocolError("image data URL has an invalid media type")
    try:
        base64.b64decode(data, validate=True)
    except Exception as exc:
        raise ProviderProtocolError("image data URL has invalid base64") from exc
    return raw, media_type, detail


def _call_parts(
    message: Mapping[str, Any],
) -> list[tuple[str, str, str, Mapping[str, Any]]]:
    calls = message.get("tool_calls")
    if calls is None:
        calls = []
    if isinstance(calls, (str, bytes)) or not isinstance(calls, Sequence):
        raise ProviderProtocolError("tool_calls must be an array")
    result = []
    seen_ids: set[str] = set()
    for call in calls:
        if not isinstance(call, Mapping) or call.get("type") != "function":
            raise ProviderProtocolError("only function tool calls are supported")
        function = call.get("function")
        if not isinstance(function, Mapping):
            raise ProviderProtocolError("tool call requires a function object")
        call_id = call.get("id")
        name = function.get("name")
        arguments = function.get("arguments")
        if (
            not isinstance(call_id, str)
            or not call_id.strip()
            or not isinstance(name, str)
            or not name.strip()
            or not isinstance(arguments, str)
        ):
            raise ProviderProtocolError(
                "tool call requires id, name, and JSON arguments"
            )
        call_id = call_id.strip()
        name = name.strip()
        try:
            parsed = json.loads(arguments)
        except json.JSONDecodeError as exc:
            raise ProviderProtocolError("tool call arguments must be JSON") from exc
        if not isinstance(parsed, Mapping):
            raise ProviderProtocolError("tool call arguments must be a JSON object")
        if call_id in seen_ids:
            raise ProviderProtocolError("tool call ids must be unique per message")
        seen_ids.add(call_id)
        result.append((call_id, name, arguments, parsed))
    return result


def _canonical_tools(request: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    tools = request.get("tools")
    if tools is None:
        tools = []
    if isinstance(tools, (str, bytes)) or not isinstance(tools, Sequence):
        raise ProviderProtocolError("tools must be an array")
    result = []
    seen_names: set[str] = set()
    for tool in tools:
        if not isinstance(tool, Mapping) or tool.get("type") != "function":
            raise ProviderProtocolError("only function tools are supported")
        function = tool.get("function")
        if not isinstance(function, Mapping):
            raise ProviderProtocolError("function tool requires a name")
        raw_name = function.get("name")
        if not isinstance(raw_name, str) or not raw_name.strip():
            raise ProviderProtocolError("function tool requires a name")
        if not isinstance(function.get("parameters"), Mapping):
            raise ProviderProtocolError("function tool requires a parameters schema")
        name = raw_name.strip()
        if name in seen_names:
            raise ProviderProtocolError("function tool names must be unique")
        seen_names.add(name)
        result.append(tool)
    return result


def _tool_result_text(message: Mapping[str, Any]) -> tuple[str, str]:
    call_id = message.get("tool_call_id")
    if not isinstance(call_id, str) or not call_id.strip():
        raise ProviderProtocolError("tool message requires tool_call_id")
    call_id = call_id.strip()
    content = message.get("content")
    if isinstance(content, str):
        return call_id, content
    return call_id, json.dumps(content, ensure_ascii=False, separators=(",", ":"))


def _chosen_function(value: Any) -> str | None:
    if not isinstance(value, Mapping) or value.get("type") != "function":
        return None
    function = value.get("function")
    if isinstance(function, Mapping):
        name = function.get("name")
    else:
        name = value.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ProviderProtocolError("chosen function requires a name")
    return name.strip()


def _responses_request(
    request: Mapping[str, Any],
    replay: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    input_items: list[dict[str, Any]] = []
    for message in _message_list(request):
        role = str(message.get("role") or "")
        if role == "tool":
            call_id, output = _tool_result_text(message)
            input_items.append(
                {"type": "function_call_output", "call_id": call_id, "output": output}
            )
            continue
        if role not in {"system", "developer", "user", "assistant"}:
            raise ProviderProtocolError(f"unsupported message role {role!r}")
        calls = _call_parts(message)
        exact_sidecars = [
            replay.get(call_id, {})
            for call_id, _, _, _ in calls
            if replay.get(call_id, {}).get("responses_output") is not None
        ]
        if exact_sidecars:
            if role != "assistant" or len(exact_sidecars) != len(calls):
                raise ProviderProtocolError("Responses replay state is incomplete")
            exact_output = exact_sidecars[0]["responses_output"]
            if any(
                sidecar["responses_output"] != exact_output
                for sidecar in exact_sidecars[1:]
            ):
                raise ProviderProtocolError("Responses replay state is inconsistent")
            for call_id, name, arguments, _ in calls:
                sidecar = replay[call_id]
                if (
                    sidecar.get("tool_name") != name
                    or sidecar.get("tool_arguments") != arguments
                ):
                    raise ProviderProtocolError(
                        "Responses canonical tool call differs from native replay"
                    )
            input_items.extend(deepcopy(exact_output))
            continue

        content: Any
        parts = _content_parts(message.get("content"))
        if len(parts) == 1 and parts[0].get("type") == "text":
            content = str(parts[0].get("text") or "")
        else:
            content = []
            for part in parts:
                if part.get("type") == "text":
                    content.append(
                        {"type": "input_text", "text": str(part.get("text") or "")}
                    )
                elif part.get("type") == "image_url":
                    url, _, detail = _data_image(part)
                    content.append(
                        {"type": "input_image", "image_url": url, "detail": detail}
                    )
                else:
                    raise ProviderProtocolError("unsupported Responses content part")
        if content not in ("", []):
            input_items.append({"type": "message", "role": role, "content": content})
        for call_id, name, arguments, _ in calls:
            input_items.append(
                {
                    "type": "function_call",
                    "call_id": call_id,
                    "name": name,
                    "arguments": arguments,
                }
            )

    body: dict[str, Any] = {"model": request.get("model"), "input": input_items}
    tools = []
    for tool in _canonical_tools(request):
        function = tool["function"]
        native = {
            "type": "function",
            "name": function["name"],
            "parameters": deepcopy(function["parameters"]),
            "strict": bool(function.get("strict", False)),
        }
        if function.get("description") is not None:
            native["description"] = function["description"]
        tools.append(native)
    if tools:
        body["tools"] = tools
        choice = request.get("tool_choice", "auto")
        chosen = _chosen_function(choice)
        tool_names = {tool["name"] for tool in tools}
        if chosen:
            if chosen not in tool_names:
                raise ProviderProtocolError("chosen function is not declared")
            body["tool_choice"] = {"type": "function", "name": chosen}
        elif choice == "any":
            body["tool_choice"] = "required"
        elif choice in {"auto", "required", "none"}:
            body["tool_choice"] = choice
        else:
            raise ProviderProtocolError("unsupported Responses tool_choice")
        body["include"] = ["reasoning.encrypted_content"]
    max_tokens = request.get(
        "max_output_tokens",
        request.get("max_completion_tokens", request.get("max_tokens")),
    )
    if max_tokens is not None:
        body["max_output_tokens"] = max_tokens
    for key in ("temperature", "top_p", "reasoning", "store"):
        if key in request:
            body[key] = deepcopy(request[key])
    if "reasoning_effort" in request and "reasoning" not in body:
        body["reasoning"] = {"effort": request["reasoning_effort"]}
    return body


def _anthropic_blocks(
    content: Any,
    *,
    allow_images: bool,
) -> list[dict[str, Any]]:
    blocks = []
    for part in _content_parts(content):
        if part.get("type") == "text":
            text = part.get("text")
            if not isinstance(text, str):
                raise ProviderProtocolError("Anthropic text content must be a string")
            blocks.append({"type": "text", "text": text})
        elif part.get("type") == "image_url":
            if not allow_images:
                raise ProviderProtocolError(
                    "Anthropic system and assistant content must be text-only"
                )
            _, media_type, _ = _data_image(part)
            if media_type not in _ANTHROPIC_IMAGE_MEDIA_TYPES:
                raise ProviderProtocolError(
                    f"Anthropic does not support image media type {media_type!r}"
                )
            raw = part["image_url"]
            url = raw.get("url") if isinstance(raw, Mapping) else raw
            blocks.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": media_type,
                        "data": str(url).split(",", 1)[1],
                    },
                }
            )
        else:
            raise ProviderProtocolError("unsupported Anthropic content part")
    return blocks


def _append_message(
    messages: list[dict[str, Any]], role: str, blocks: list[dict[str, Any]]
) -> None:
    if not blocks:
        return
    if messages and messages[-1]["role"] == role:
        messages[-1]["content"].extend(blocks)
    else:
        messages.append({"role": role, "content": blocks})


def _append_gemini_content(
    contents: list[dict[str, Any]],
    role: str,
    parts: list[dict[str, Any]],
) -> None:
    if not parts:
        return
    if contents and contents[-1]["role"] == role:
        contents[-1]["parts"].extend(parts)
    else:
        contents.append({"role": role, "parts": parts})


def _anthropic_request(
    request: Mapping[str, Any],
    replay: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    messages: list[dict[str, Any]] = []
    system: list[dict[str, Any]] = []
    for message in _message_list(request):
        role = str(message.get("role") or "")
        if role in {"system", "developer"}:
            system.extend(_anthropic_blocks(message.get("content"), allow_images=False))
            continue
        if role == "tool":
            call_id, output = _tool_result_text(message)
            _append_message(
                messages,
                "user",
                [{"type": "tool_result", "tool_use_id": call_id, "content": output}],
            )
            continue
        if role not in {"user", "assistant"}:
            raise ProviderProtocolError(f"unsupported message role {role!r}")
        calls = _call_parts(message)
        thinking_blocks: list[dict[str, Any]] = []
        for call_id, _, _, _ in calls:
            candidate = replay.get(call_id, {}).get("anthropic_thinking") or []
            if candidate:
                thinking_blocks = deepcopy(candidate)
                break
        blocks = thinking_blocks + _anthropic_blocks(
            message.get("content"), allow_images=role == "user"
        )
        for call_id, name, _, parsed in calls:
            blocks.append(
                {"type": "tool_use", "id": call_id, "name": name, "input": dict(parsed)}
            )
        _append_message(messages, role, blocks)

    max_tokens = request.get("max_tokens", request.get("max_completion_tokens"))
    if max_tokens is None:
        raise ProviderProtocolError("Anthropic requests require max_tokens")
    body: dict[str, Any] = {
        "model": request.get("model"),
        "max_tokens": max_tokens,
        "messages": messages,
    }
    if system:
        body["system"] = system
    tools = []
    for tool in _canonical_tools(request):
        function = tool["function"]
        native = {
            "name": function["name"],
            "input_schema": deepcopy(function["parameters"]),
        }
        if function.get("description") is not None:
            native["description"] = function["description"]
        if function.get("strict") is True:
            native["strict"] = True
        tools.append(native)
    if tools:
        body["tools"] = tools
        choice = request.get("tool_choice", "auto")
        chosen = _chosen_function(choice)
        if chosen:
            if chosen not in {tool["name"] for tool in tools}:
                raise ProviderProtocolError("chosen function is not declared")
            body["tool_choice"] = {"type": "tool", "name": chosen}
        elif choice == "required":
            body["tool_choice"] = {"type": "any"}
        elif choice in {"auto", "any", "none"}:
            body["tool_choice"] = {"type": choice}
        else:
            raise ProviderProtocolError("unsupported Anthropic tool_choice")
    for key in ("temperature", "top_p", "top_k", "thinking"):
        if key in request:
            body[key] = deepcopy(request[key])
    if "stop" in request:
        body["stop_sequences"] = deepcopy(request["stop"])
    return body


def _json_result(value: str) -> dict[str, Any]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {"result": value}
    return dict(parsed) if isinstance(parsed, Mapping) else {"result": parsed}


def _gemini_request(
    request: Mapping[str, Any],
    replay: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    contents: list[dict[str, Any]] = []
    system_parts: list[dict[str, Any]] = []
    call_names: dict[str, str] = {}
    for message in _message_list(request):
        for call_id, name, _, _ in _call_parts(message):
            call_names[call_id] = name
    for call_id, sidecar in replay.items():
        name = sidecar.get("tool_name")
        if isinstance(name, str) and name:
            call_names.setdefault(call_id, name)

    for message in _message_list(request):
        role = str(message.get("role") or "")
        if role in {"system", "developer"}:
            for part in _content_parts(message.get("content")):
                if part.get("type") != "text":
                    raise ProviderProtocolError(
                        "Gemini system instructions must be text"
                    )
                system_parts.append({"text": str(part.get("text") or "")})
            continue
        if role == "tool":
            call_id, output = _tool_result_text(message)
            name = call_names.get(call_id)
            if not name:
                raise ProviderProtocolError(
                    "Gemini tool result has no matching function name"
                )
            function_response = {
                "name": name,
                "response": _json_result(output),
            }
            sidecar = replay.get(call_id, {})
            if "gemini_native_call_id" in sidecar:
                native_call_id = sidecar["gemini_native_call_id"]
                if native_call_id is not None:
                    function_response["id"] = native_call_id
            elif call_id.startswith("call_gemini_"):
                raise ProviderProtocolError("Gemini native replay state is unavailable")
            else:
                function_response["id"] = call_id
            part = {"functionResponse": function_response}
            _append_gemini_content(contents, "user", [part])
            continue
        if role not in {"user", "assistant"}:
            raise ProviderProtocolError(f"unsupported message role {role!r}")
        calls = _call_parts(message)
        exact_sidecars = [
            replay.get(call_id, {})
            for call_id, _, _, _ in calls
            if replay.get(call_id, {}).get("gemini_content") is not None
        ]
        if exact_sidecars:
            if role != "assistant" or len(exact_sidecars) != len(calls):
                raise ProviderProtocolError("Gemini replay state is incomplete")
            exact_content = exact_sidecars[0]["gemini_content"]
            if any(
                sidecar["gemini_content"] != exact_content
                for sidecar in exact_sidecars[1:]
            ):
                raise ProviderProtocolError("Gemini replay state is inconsistent")
            for call_id, name, arguments, _ in calls:
                sidecar = replay[call_id]
                if (
                    sidecar.get("tool_name") != name
                    or sidecar.get("tool_arguments") != arguments
                ):
                    raise ProviderProtocolError(
                        "Gemini canonical tool call differs from native replay"
                    )
            contents.append(deepcopy(exact_content))
            continue
        parts: list[dict[str, Any]] = []
        for part in _content_parts(message.get("content")):
            if part.get("type") == "text":
                parts.append({"text": str(part.get("text") or "")})
            elif part.get("type") == "image_url":
                _, media_type, _ = _data_image(part)
                raw = part["image_url"]
                url = raw.get("url") if isinstance(raw, Mapping) else raw
                parts.append(
                    {
                        "inlineData": {
                            "mimeType": media_type,
                            "data": str(url).split(",", 1)[1],
                        }
                    }
                )
            else:
                raise ProviderProtocolError("unsupported Gemini content part")
        for call_id, name, _, parsed in calls:
            native_call = {"id": call_id, "name": name, "args": dict(parsed)}
            parts.append({"functionCall": native_call})
        _append_gemini_content(
            contents, "model" if role == "assistant" else "user", parts
        )

    body: dict[str, Any] = {"contents": contents}
    if system_parts:
        body["systemInstruction"] = {"parts": system_parts}
    declarations = []
    for tool in _canonical_tools(request):
        function = tool["function"]
        native = {
            "name": function["name"],
            "parametersJsonSchema": deepcopy(function["parameters"]),
        }
        if function.get("description") is not None:
            native["description"] = function["description"]
        declarations.append(native)
    if declarations:
        body["tools"] = [{"functionDeclarations": declarations}]
        choice = request.get("tool_choice", "auto")
        chosen = _chosen_function(choice)
        if chosen:
            if chosen not in {item["name"] for item in declarations}:
                raise ProviderProtocolError("chosen function is not declared")
            body["toolConfig"] = {
                "functionCallingConfig": {
                    "mode": "ANY",
                    "allowedFunctionNames": [chosen],
                }
            }
        elif choice in {"required", "any"}:
            body["toolConfig"] = {"functionCallingConfig": {"mode": "ANY"}}
        elif choice == "none":
            body["toolConfig"] = {"functionCallingConfig": {"mode": "NONE"}}
        elif choice != "auto":
            raise ProviderProtocolError("unsupported Gemini tool_choice")
    config: dict[str, Any] = {}
    max_tokens = request.get("max_completion_tokens", request.get("max_tokens"))
    if max_tokens is not None:
        config["maxOutputTokens"] = max_tokens
    for source, target in (
        ("temperature", "temperature"),
        ("top_p", "topP"),
        ("top_k", "topK"),
    ):
        if source in request:
            config[target] = request[source]
    if "thinking" in request:
        config["thinkingConfig"] = deepcopy(request["thinking"])
    if config:
        body["generationConfig"] = config
    return body


def build_provider_request(
    family: str,
    request: Mapping[str, Any],
    *,
    replay: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    family = normalize_provider_family(family)
    if not isinstance(request.get("model"), str) or not request["model"].strip():
        raise ProviderProtocolError("model must be non-empty")
    if request.get("stream") is True:
        raise ProviderProtocolError("native provider adapter is non-streaming")
    replay = replay or {}
    if family == "openai_responses":
        return _responses_request(request, replay)
    if family == "anthropic":
        return _anthropic_request(request, replay)
    return _gemini_request(request, replay)


def _chat_response(
    *,
    response_id: str,
    model: str,
    content: str,
    reasoning: str,
    tool_calls: list[dict[str, Any]],
    finish_reason: str,
    usage: Mapping[str, Any] | None,
) -> dict[str, Any]:
    message: dict[str, Any] = {"role": "assistant", "content": content}
    if reasoning:
        message["reasoning_content"] = reasoning
    if tool_calls:
        message["tool_calls"] = tool_calls
    return {
        "id": response_id,
        "object": "chat.completion",
        "model": model,
        "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}],
        "usage": dict(usage or {}),
    }


def _synthetic_gemini_call_id() -> str:
    return f"call_gemini_{uuid.uuid4().hex}"


def _json_object_arguments(value: Any, provider: str) -> tuple[str, Mapping[str, Any]]:
    if not isinstance(value, str):
        raise ProviderProtocolError(f"{provider} tool arguments must be JSON text")
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ProviderProtocolError(
            f"{provider} tool arguments must be valid JSON"
        ) from exc
    if not isinstance(parsed, Mapping):
        raise ProviderProtocolError(f"{provider} tool arguments must be a JSON object")
    return value, parsed


def _responses_failure(payload: Mapping[str, Any]) -> str | None:
    error = payload.get("error")
    if error is not None:
        if isinstance(error, Mapping):
            message = str(error.get("message") or error.get("code") or "error")
        else:
            message = str(error)
        return f"Responses API error: {message}"
    status = str(payload.get("status") or "").strip().lower()
    if status == "incomplete":
        details = payload.get("incomplete_details")
        reason = details.get("reason") if isinstance(details, Mapping) else None
        return f"Responses API returned incomplete output: {reason or 'unknown reason'}"
    if status and status != "completed":
        return f"Responses API returned nonterminal status {status!r}"
    return None


def _require_completed_responses_item(item: Mapping[str, Any], label: str) -> None:
    status = str(item.get("status") or "").strip().lower()
    if status and status != "completed":
        raise ProviderProtocolError(f"Responses {label} has status {status!r}")


def _normalize_responses(
    payload: Mapping[str, Any], model: str
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    failure = _responses_failure(payload)
    if failure:
        raise ProviderProtocolError(failure)
    output = payload.get("output")
    if isinstance(output, (str, bytes)) or not isinstance(output, Sequence):
        raise ProviderProtocolError("Responses output must be an array")
    if any(not isinstance(item, Mapping) for item in output):
        raise ProviderProtocolError("Responses output items must be objects")
    native_output = deepcopy([dict(item) for item in output])
    text: list[str] = []
    reasoning: list[str] = []
    calls: list[dict[str, Any]] = []
    replay: dict[str, dict[str, Any]] = {}
    call_arguments: dict[str, str] = {}
    for item in output:
        item_type = item.get("type")
        if item_type == "reasoning":
            _require_completed_responses_item(item, "reasoning item")
            summary = item.get("summary", [])
            if isinstance(summary, (str, bytes)) or not isinstance(summary, Sequence):
                raise ProviderProtocolError(
                    "Responses reasoning summary must be an array"
                )
            for part in summary:
                if not isinstance(part, Mapping):
                    raise ProviderProtocolError(
                        "Responses reasoning summary items must be objects"
                    )
                if isinstance(part.get("text"), str):
                    reasoning.append(part["text"])
        elif item_type == "message":
            _require_completed_responses_item(item, "output message")
            content = item.get("content")
            if isinstance(content, (str, bytes)) or not isinstance(content, Sequence):
                raise ProviderProtocolError(
                    "Responses message content must be an array"
                )
            for part in content:
                if not isinstance(part, Mapping):
                    raise ProviderProtocolError(
                        "Responses message content items must be objects"
                    )
                if part.get("type") in {"output_text", "text"} and isinstance(
                    part.get("text"), str
                ):
                    text.append(part["text"])
                elif part.get("type") == "reasoning_text" and isinstance(
                    part.get("text"), str
                ):
                    reasoning.append(part["text"])
                elif part.get("type") == "refusal":
                    refusal = str(part.get("refusal") or "model refusal")
                    raise ProviderProtocolError(
                        f"Responses API refused the request: {refusal}"
                    )
        elif item_type == "function_call":
            _require_completed_responses_item(item, "function call")
            call_id = item.get("call_id")
            name = item.get("name")
            if not isinstance(call_id, str) or not call_id.strip():
                raise ProviderProtocolError("Responses function call requires call_id")
            if not isinstance(name, str) or not name.strip():
                raise ProviderProtocolError("Responses function call requires name")
            call_id = call_id.strip()
            name = name.strip()
            if call_id in call_arguments:
                raise ProviderProtocolError(
                    "Responses function call ids must be unique"
                )
            arguments, _ = _json_object_arguments(item.get("arguments"), "Responses")
            call = {
                "id": call_id,
                "type": "function",
                "function": {"name": name, "arguments": arguments},
            }
            calls.append(call)
            call_arguments[call_id] = arguments
    if not calls and not text:
        raise ProviderProtocolError("Responses API returned no usable output")
    for call in calls:
        call_id = call["id"]
        replay[call_id] = {
            "responses_output": deepcopy(native_output),
            "tool_name": call["function"]["name"],
            "tool_arguments": call_arguments[call_id],
        }
    finish = "tool_calls" if calls else "stop"
    response = _chat_response(
        response_id=str(payload.get("id") or ""),
        model=str(payload.get("model") or model),
        content="".join(text),
        reasoning="\n".join(reasoning),
        tool_calls=calls,
        finish_reason=finish,
        usage=(
            payload.get("usage") if isinstance(payload.get("usage"), Mapping) else None
        ),
    )
    return response, replay


def _normalize_anthropic(
    payload: Mapping[str, Any], model: str
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    if payload.get("type") == "error" or payload.get("error") is not None:
        error = payload.get("error")
        message = error.get("message") if isinstance(error, Mapping) else error
        raise ProviderProtocolError(
            f"Anthropic API error: {message or 'unknown error'}"
        )
    blocks = payload.get("content")
    if isinstance(blocks, (str, bytes)) or not isinstance(blocks, Sequence):
        raise ProviderProtocolError("Anthropic content must be an array")
    if any(not isinstance(block, Mapping) for block in blocks):
        raise ProviderProtocolError("Anthropic content blocks must be objects")
    text: list[str] = []
    reasoning: list[str] = []
    thinking_blocks: list[dict[str, Any]] = []
    calls: list[dict[str, Any]] = []
    replay: dict[str, dict[str, Any]] = {}
    seen_call_ids: set[str] = set()
    for block in blocks:
        block_type = block.get("type")
        if block_type == "text":
            if not isinstance(block.get("text"), str):
                raise ProviderProtocolError("Anthropic text block requires text")
            text.append(block["text"])
        elif block_type in {"thinking", "redacted_thinking"}:
            thinking_blocks.append(deepcopy(dict(block)))
            if isinstance(block.get("thinking"), str):
                reasoning.append(block["thinking"])
        elif block_type == "tool_use":
            call_id = block.get("id")
            name = block.get("name")
            arguments_object = block.get("input")
            if not isinstance(call_id, str) or not call_id.strip():
                raise ProviderProtocolError("Anthropic tool use requires id")
            if not isinstance(name, str) or not name.strip():
                raise ProviderProtocolError("Anthropic tool use requires name")
            if not isinstance(arguments_object, Mapping):
                raise ProviderProtocolError(
                    "Anthropic tool input must be a JSON object"
                )
            call_id = call_id.strip()
            name = name.strip()
            if call_id in seen_call_ids:
                raise ProviderProtocolError("Anthropic tool ids must be unique")
            seen_call_ids.add(call_id)
            arguments = json.dumps(
                arguments_object, ensure_ascii=False, separators=(",", ":")
            )
            calls.append(
                {
                    "id": call_id,
                    "type": "function",
                    "function": {"name": name, "arguments": arguments},
                }
            )
            replay[call_id] = {
                "anthropic_thinking": deepcopy(thinking_blocks),
                "tool_name": name,
            }
    stop = str(payload.get("stop_reason") or "")
    finish = {"tool_use": "tool_calls", "max_tokens": "length", "end_turn": "stop"}.get(
        stop, stop or "stop"
    )
    if not calls and not text:
        raise ProviderProtocolError("Anthropic API returned no usable output")
    response = _chat_response(
        response_id=str(payload.get("id") or ""),
        model=str(payload.get("model") or model),
        content="".join(text),
        reasoning="\n".join(reasoning),
        tool_calls=calls,
        finish_reason=finish,
        usage=(
            payload.get("usage") if isinstance(payload.get("usage"), Mapping) else None
        ),
    )
    return response, replay


def _normalize_gemini(
    payload: Mapping[str, Any], model: str
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    if payload.get("error") is not None:
        error = payload.get("error")
        message = error.get("message") if isinstance(error, Mapping) else error
        raise ProviderProtocolError(f"Gemini API error: {message or 'unknown error'}")
    candidates = payload.get("candidates") or []
    if (
        isinstance(candidates, (str, bytes))
        or not isinstance(candidates, Sequence)
        or not candidates
    ):
        raise ProviderProtocolError("Gemini response has no candidate")
    candidate = candidates[0]
    if not isinstance(candidate, Mapping) or not isinstance(
        candidate.get("content"), Mapping
    ):
        raise ProviderProtocolError("Gemini candidate has no content")
    native_content = deepcopy(dict(candidate["content"]))
    if native_content.get("role") != "model":
        raise ProviderProtocolError("Gemini candidate content must have model role")
    parts = native_content.get("parts")
    if isinstance(parts, (str, bytes)) or not isinstance(parts, Sequence):
        raise ProviderProtocolError("Gemini content parts must be an array")
    if any(not isinstance(part, Mapping) for part in parts):
        raise ProviderProtocolError("Gemini content parts must be objects")
    text: list[str] = []
    reasoning: list[str] = []
    calls: list[dict[str, Any]] = []
    replay: dict[str, dict[str, Any]] = {}
    response_id = str(payload.get("responseId") or payload.get("response_id") or "")
    seen_call_ids: set[str] = set()
    for part in parts:
        if "text" in part and not isinstance(part.get("text"), str):
            raise ProviderProtocolError("Gemini text part requires text")
        if isinstance(part.get("text"), str):
            (reasoning if part.get("thought") is True else text).append(part["text"])
        if "functionCall" in part:
            function = part.get("functionCall")
        elif "function_call" in part:
            function = part.get("function_call")
        else:
            continue
        if not isinstance(function, Mapping):
            raise ProviderProtocolError("Gemini function call must be an object")
        name = function.get("name")
        args = function.get("args", {})
        if not isinstance(name, str) or not name.strip():
            raise ProviderProtocolError("Gemini function call requires name")
        if not isinstance(args, Mapping):
            raise ProviderProtocolError("Gemini function args must be a JSON object")
        name = name.strip()
        arguments = json.dumps(args, ensure_ascii=False, separators=(",", ":"))
        if "id" in function:
            native_call_id = function.get("id")
            if not isinstance(native_call_id, str) or not native_call_id.strip():
                raise ProviderProtocolError("Gemini function call id must be non-empty")
            native_call_id = native_call_id.strip()
            call_id = native_call_id
        else:
            native_call_id = None
            call_id = _synthetic_gemini_call_id()
        if call_id in seen_call_ids:
            raise ProviderProtocolError("Gemini function call ids must be unique")
        seen_call_ids.add(call_id)
        signature = (
            part.get("thoughtSignature")
            if "thoughtSignature" in part
            else part.get("thought_signature")
        )
        if signature is not None and (not isinstance(signature, str) or not signature):
            raise ProviderProtocolError(
                "Gemini thought signature must be non-empty text"
            )
        call: dict[str, Any] = {
            "id": call_id,
            "type": "function",
            "function": {"name": name, "arguments": arguments},
        }
        if isinstance(signature, str) and signature:
            call["extra_content"] = {"google": {"thought_signature": signature}}
        calls.append(call)
        replay[call_id] = {
            "gemini_content": deepcopy(native_content),
            "gemini_native_call_id": native_call_id,
            "tool_name": name,
            "tool_arguments": arguments,
        }
    if not calls and not text:
        raise ProviderProtocolError("Gemini API returned no usable output")
    metadata = payload.get("usageMetadata") or payload.get("usage_metadata") or {}
    usage: dict[str, Any] = {}
    if isinstance(metadata, Mapping):
        prompt = int(
            metadata.get("promptTokenCount") or metadata.get("prompt_token_count") or 0
        )
        cached = int(
            metadata.get("cachedContentTokenCount")
            or metadata.get("cached_content_token_count")
            or 0
        )
        candidate_tokens = int(
            metadata.get("candidatesTokenCount")
            or metadata.get("candidates_token_count")
            or 0
        )
        thoughts = int(
            metadata.get("thoughtsTokenCount")
            or metadata.get("thoughts_token_count")
            or 0
        )
        completion = candidate_tokens + thoughts
        if "totalTokenCount" in metadata:
            total = int(metadata["totalTokenCount"])
        elif "total_token_count" in metadata:
            total = int(metadata["total_token_count"])
        else:
            total = prompt + completion
        usage = {
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": total,
            "reasoning_tokens": thoughts,
            "prompt_tokens_details": {"cached_tokens": cached},
            "completion_tokens_details": {"reasoning_tokens": thoughts},
        }
    finish_raw = str(
        candidate.get("finishReason") or candidate.get("finish_reason") or ""
    )
    finish = (
        "tool_calls"
        if calls
        else {"MAX_TOKENS": "length", "STOP": "stop"}.get(
            finish_raw, finish_raw.lower() or "stop"
        )
    )
    response = _chat_response(
        response_id=response_id,
        model=str(payload.get("modelVersion") or payload.get("model_version") or model),
        content="".join(text),
        reasoning="\n".join(reasoning),
        tool_calls=calls,
        finish_reason=finish,
        usage=usage,
    )
    return response, replay


def normalize_provider_response(
    family: str,
    payload: Mapping[str, Any],
    *,
    model: str,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    family = normalize_provider_family(family)
    if not isinstance(payload, Mapping):
        raise ProviderProtocolError("provider response must be a JSON object")
    if family == "openai_responses":
        return _normalize_responses(payload, model)
    if family == "anthropic":
        return _normalize_anthropic(payload, model)
    return _normalize_gemini(payload, model)


class ProviderProtocolAdapter:
    """Facade compatible with the existing frozen model-call boundary."""

    def __init__(
        self,
        *,
        family: str,
        base_url: str,
        api_key: str,
        model: str,
        timeout: float,
        custom_headers: Mapping[str, str] | None = None,
        transport: Any | None = None,
    ) -> None:
        self.family = normalize_provider_family(family)
        self.base_url = str(base_url or "")
        self.api_key = str(api_key or "")
        self.model = str(model or "").strip()
        self.timeout = float(timeout)
        if not self.model or self.timeout <= 0:
            raise ProviderProtocolError("model and positive timeout are required")
        self.custom_headers = dict(custom_headers or {})
        self.transport = transport or RequestsJSONTransport()
        self._replay: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._lock = threading.Lock()

    def prepare_chat_completion(self, **kwargs: Any) -> dict[str, Any]:
        request = dict(kwargs)
        request.setdefault("model", self.model)
        if request["model"] != self.model:
            raise ProviderProtocolError("request model differs from adapter model")
        with self._lock:
            replay = deepcopy(dict(self._replay))
        prepared = build_provider_request(self.family, request, replay=replay)
        if "timeout" in request:
            prepared["timeout"] = request["timeout"]
        return prepared

    def send_prepared_chat_completion(self, params: dict[str, Any]) -> Any:
        body = dict(params)
        timeout = body.pop("timeout", self.timeout)
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not math.isfinite(float(timeout))
            or float(timeout) <= 0
        ):
            raise ProviderProtocolError(
                "request timeout must be a positive finite number"
            )
        payload = self.transport.post_json(
            url=provider_endpoint(self.family, self.base_url, self.model),
            headers=provider_headers(self.family, self.api_key, self.custom_headers),
            body=body,
            timeout=float(timeout),
        )
        response, replay = normalize_provider_response(
            self.family, payload, model=self.model
        )
        if replay:
            with self._lock:
                for call_id, sidecar in replay.items():
                    self._replay[call_id] = sidecar
                    self._replay.move_to_end(call_id)
                while len(self._replay) > _MAX_REPLAY_ITEMS:
                    self._replay.popitem(last=False)
        return response

    def close(self) -> None:
        close = getattr(self.transport, "close", None)
        if callable(close):
            close()


__all__ = [
    "ProviderProtocolError",
    "ProviderHTTPError",
    "RequestsJSONTransport",
    "ProviderProtocolAdapter",
    "normalize_provider_family",
    "provider_endpoint",
    "provider_headers",
    "build_provider_request",
    "normalize_provider_response",
]
