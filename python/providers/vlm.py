"""
VLM Provider 抽象 — 截图→描述/坐标 的视觉模型后端

支持两种后端:
- gpt-4o-mini: 复用 OpenAIProvider，传入 base64 image
- local: 现有 llama-server 调用

仅在 visual_mode=True 且经验库 OpenCV 匹配失败时调用。
非 VLM 路径（OCR、UIA、模板匹配）不受影响。
"""
from __future__ import annotations

import base64
import json
import logging
import os
import time
from abc import ABC, abstractmethod
from typing import Optional

logger = logging.getLogger("lobster.vlm")

# ── 默认配置 ──────────────────────────────────────────────────
LOCAL_VLM_HOST = os.getenv("VLM_HOST", "http://localhost:8084")
LOCAL_VLM_MODEL = "qwen2.5-vl:3b"
GPT4o_MINI_MODEL = os.getenv("VLM_MODEL", "gpt-4o-mini")


def _is_multimodal_enabled() -> bool:
    """检查多模态功能是否开启。"""
    val = os.environ.get("LLM_MULTIMODAL", "")
    return val.lower() not in ("false", "0", "no", "")


def _screenshot_b64() -> str:
    """截图并返回 JPEG base64 字符串。"""
    import cv2
    from perception.vision import ScreenCapture
    img = ScreenCapture.capture()
    _, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 85])
    return base64.b64encode(buf).decode()


# ── 抽象基类 ──────────────────────────────────────────────────

class VLMProvider(ABC):
    """VLM 后端抽象。返回统一格式: {content, input_tokens, output_tokens, model, error}"""

    @abstractmethod
    def analyze(self, prompt: str, image_b64: str = "",
                temperature: float = 0.1, max_tokens: int = 512) -> dict:
        ...

    def analyze_screen(self, prompt: str, **kwargs) -> dict:
        """截图后分析。"""
        try:
            image_b64 = _screenshot_b64()
        except Exception as e:
            return {"content": "", "error": f"截图失败: {e}",
                    "input_tokens": 0, "output_tokens": 0, "model": ""}
        return self.analyze(prompt, image_b64, **kwargs)


# ── GPT-4o-mini 实现 ─────────────────────────────────────────

class GPT4oMiniProvider(VLMProvider):
    """通过 OpenAI 兼容 API 调用 GPT-4o-mini 做视觉分析。

    使用与 DeepSeek/OpenAI 相同的 api_key/api_base 配置。
    """

    def __init__(self, api_key: str = "", api_base: str = ""):
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self.api_base = (api_base or os.environ.get("OPENAI_API_BASE", "")
                         or "https://api.openai.com/v1").rstrip("/")

    def analyze(self, prompt: str, image_b64: str = "",
                temperature: float = 0.1, max_tokens: int = 512) -> dict:
        if not self.api_key:
            return {"content": "", "error": "缺少 OpenAI API Key，请在设置中配置",
                    "input_tokens": 0, "output_tokens": 0, "model": GPT4o_MINI_MODEL}

        if not image_b64:
            return self.analyze_screen(prompt, temperature=temperature, max_tokens=max_tokens)

        import requests as _req

        payload = {
            "model": GPT4o_MINI_MODEL,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}},
                ],
            }],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        try:
            resp = _req.post(
                f"{self.api_base}/chat/completions",
                headers={"Content-Type": "application/json",
                         "Authorization": f"Bearer {self.api_key}"},
                json=payload,
                timeout=120,
            )
            resp.raise_for_status()
            body = resp.json()
            content = body["choices"][0]["message"]["content"]
            usage = body.get("usage", {})
            return {
                "content": content,
                "input_tokens": usage.get("prompt_tokens", 0),
                "output_tokens": usage.get("completion_tokens", 0),
                "model": GPT4o_MINI_MODEL,
            }
        except Exception as e:
            return {"content": "", "error": str(e),
                    "input_tokens": 0, "output_tokens": 0, "model": GPT4o_MINI_MODEL}


# ── 本地 llama-server 实现 ───────────────────────────────────

class LocalVLMProvider(VLMProvider):
    """通过 llama.cpp server (WSL2) 调用本地量化 VLM 模型。"""

    def __init__(self, host: str = "", model: str = ""):
        self.host = (host or LOCAL_VLM_HOST).rstrip("/")
        self.model = model or LOCAL_VLM_MODEL

    def analyze(self, prompt: str, image_b64: str = "",
                temperature: float = 0.1, max_tokens: int = 512) -> dict:
        if not image_b64:
            return self.analyze_screen(prompt, temperature=temperature, max_tokens=max_tokens)

        import urllib.request, urllib.error

        url = f"{self.host}/v1/chat/completions"
        payload = json.dumps({
            "model": self.model,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}},
                ],
            }],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }).encode()

        req = urllib.request.Request(url, data=payload,
                                     headers={"Content-Type": "application/json"})
        try:
            resp = urllib.request.urlopen(req, timeout=120)
            body = json.loads(resp.read())
            content = body["choices"][0]["message"]["content"]
            usage = body.get("usage", {})
            return {
                "content": content,
                "input_tokens": usage.get("prompt_tokens", 0),
                "output_tokens": usage.get("completion_tokens", 0),
                "model": self.model,
            }
        except urllib.error.HTTPError as e:
            return {"content": "", "error": f"VLM HTTP {e.code}: {e.read().decode(errors='replace')[:200]}",
                    "input_tokens": 0, "output_tokens": 0, "model": self.model}
        except urllib.error.URLError as e:
            return {"content": "", "error": f"无法连接 VLM server ({self.host}): {e.reason}",
                    "input_tokens": 0, "output_tokens": 0, "model": self.model}
        except Exception as e:
            return {"content": "", "error": str(e),
                    "input_tokens": 0, "output_tokens": 0, "model": self.model}


# ── 工厂 ──────────────────────────────────────────────────────

def get_vlm_provider() -> VLMProvider:
    """根据环境变量 / settings 返回当前 VLM provider。

    选择逻辑:
    1. VLM_BACKEND=local → LocalVLMProvider
    2. VLM_BACKEND=gpt-4o-mini → GPT4oMiniProvider
    3. 若 LLM_MODEL 包含 mimo/vision/omni/gpt-4/claude-3 → 使用 LLM 配置的 OpenAI 兼容提供者
    4. 默认: gpt-4o-mini
    """
    backend = os.environ.get("VLM_BACKEND", "").strip().lower()

    if backend == "local":
        return LocalVLMProvider()

    # 明确指定 gpt-4o-mini
    if backend == "gpt-4o-mini":
        api_key = os.environ.get("OPENAI_API_KEY", "")
        api_base = os.environ.get("OPENAI_API_BASE", "")
        return GPT4oMiniProvider(api_key=api_key, api_base=api_base)

    # 自动检测：如果 LLM_MODEL 是多模态模型，使用 LLM 的 API 配置
    model = os.environ.get("LLM_MODEL", "").strip().lower()
    mimo_keywords = ("mimo", "vision", "omni", "gpt-4", "claude-3", "claude-4",
                     "gemini", "qwen-vl", "deepseek-vl", "glm-4v")
    if model and any(kw in model for kw in mimo_keywords):
        api_key = os.environ.get("ANTHROPIC_API_KEY", "") or os.environ.get("OPENAI_API_KEY", "")
        api_base = os.environ.get("LLM_API_BASE", "") or os.environ.get("OPENAI_API_BASE", "")
        return GPT4oMiniProvider(api_key=api_key, api_base=api_base)

    # 默认: gpt-4o-mini
    api_key = os.environ.get("OPENAI_API_KEY", "")
    api_base = os.environ.get("OPENAI_API_BASE", "")
    return GPT4oMiniProvider(api_key=api_key, api_base=api_base)


def estimate_cost(input_tokens: int, output_tokens: int, provider: str = "gpt-4o-mini") -> str:
    """估算 VLM 调用的费用。"""
    if provider == "gpt-4o-mini":
        cost_in = (input_tokens / 1_000_000) * 0.15
        cost_out = (output_tokens / 1_000_000) * 0.60
    else:
        return ""
    total = cost_in + cost_out
    if total < 0.0001:
        return "< $0.0001"
    return f"${total:.4f}"
