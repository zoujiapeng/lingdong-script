"""Fail-closed state and evidence primitives for the vNext live canary.

This module is deliberately independent from Electron, Win32, model SDKs, and
the product runtime.  It provides the durable state layer used by the physical
desktop campaign:

* an immutable, content-addressed campaign manifest;
* an append-only, fsync'd, hash-chained event journal;
* a reducer for the one permitted pre-mutation invalid rerun;
* immutable artifact helpers and a deterministic derived report; and
* pure reconciliation of queue, receipt, ActionEffect, browser, and OS facts.

The manifest and report use canonical UTF-8 JSON terminated by one LF.  A
``content_sha256`` field hashes the same canonical object with that field
removed.  Journal event hashes follow the same rule for ``event_hash``.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import threading
from typing import Any


MANIFEST_FILENAME = "campaign-manifest.json"
JOURNAL_FILENAME = "campaign-events.jsonl"
REPORT_FILENAME = "campaign-report.json"

MANIFEST_SCHEMA_VERSION = "vnext-live-canary-campaign-manifest/1"
JOURNAL_SCHEMA_VERSION = "vnext-live-canary-campaign-event/1"
REPORT_SCHEMA_VERSION = "vnext-live-canary-campaign-report/1"
ACTION_RECONCILIATION_SCHEMA_VERSION = "vnext-action-reconciliation/1"

MILESTONE = "milestone/vnext-pure-kernel-stack-20260726"
CAMPAIGN_SAMPLE_KEY = "__campaign__"
CONTENT_HASH_FIELD = "content_sha256"
ZERO_EVENT_HASH = "0" * 64
C2_HEAD = "1b4532271da2210c5d75a3355744af1842ec1790"

RUNTIMES = ("legacy", "vnext")
FAMILIES = ("click_double", "scroll_roundtrip", "type_hotkey")
REQUIRED_SAMPLE_COUNT = 12

DEFAULT_INVALID_REASON_ALLOWLIST = (
    "control_plane_not_started_pre_mutation",
    "electron_not_started_pre_mutation",
    "eval_target_integrity_failure_pre_mutation",
    "eval_target_not_started_pre_mutation",
    "external_user_input_pre_mutation",
    "harness_transport_failure_pre_mutation",
    "trace_binding_unavailable_pre_mutation",
)

DEFAULT_SAFETY_ABORT_CONDITIONS = (
    "campaign_integrity_failure",
    "identity_admission_bypass",
    "input_release_failed",
    "runtime_legacy_fallback",
    "test_control_irrecoverable",
    "unauthorized_desktop_input",
    "unknown_mutation_replay",
)

DEFAULT_RERUN_RULE = {
    "max_same_seed_reruns": 1,
    "requires_allowlisted_invalid_reason": True,
    "requires_external_evidence": True,
    "requires_pre_first_mutation": True,
    "requires_unchanged_identity": [
        "commit",
        "build",
        "config",
        "task",
        "seed",
        "runtime_budget",
        "oracle",
    ],
}

DEFAULT_ORACLE_CHECKS = (
    "action_effect_external_state",
    "duplicate_input",
    "exact_target_identity",
    "no_legacy_fallback",
    "run_trace_commit_binding",
    "trusted_key_events",
    "trusted_pointer_events",
    "trusted_wheel_events",
    "unknown_no_replay",
)

JOURNAL_EVENT_KINDS = frozenset(
    {
        "campaign_started",
        "campaign_finished",
        "safety_abort",
        "sample_started",
        "sample_passed",
        "sample_failed",
        "sample_invalid",
        "sample_rerun_authorized",
    }
)

_GLOBAL_EVENT_KINDS = frozenset(
    {"campaign_started", "campaign_finished"}
)
_SAMPLE_TERMINAL_EVENT_KINDS = frozenset(
    {"sample_passed", "sample_failed", "sample_invalid"}
)
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_GIT_SHA_RE = re.compile(r"[0-9a-f]{40}")
_SAFE_IDENTIFIER_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:@+-]{0,239}")
_SAFE_CASE_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,119}")
_RESERVED_EVENT_CLAIM_KEYS = frozenset(
    {
        "budget",
        "build",
        "commit",
        "config",
        "oracle",
        "runtime_budget",
        "seed",
        "task",
    }
)
_SECRET_CONFIG_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "authorization",
        "auth_token",
        "bearer",
        "client_secret",
        "password",
        "private_key",
        "secret",
        "token",
        "access_token",
        "refresh_token",
    }
)
_TRUSTED_INPUT_EVENT_TYPES = frozenset(
    {
        "beforeinput",
        "click",
        "dblclick",
        "input",
        "keydown",
        "keyup",
        "pointerdown",
        "pointerup",
        "wheel",
    }
)


class CampaignStateError(ValueError):
    """Base class for invalid campaign state or input."""


class CampaignIntegrityError(CampaignStateError):
    """Raised when immutable content or a hash chain cannot be trusted."""


class CampaignTransitionError(CampaignStateError):
    """Raised when a journal event requests an illegal state transition."""


class CampaignAlreadyExistsError(CampaignIntegrityError):
    """Raised when an exclusive campaign object already exists."""


@dataclass(frozen=True, slots=True)
class ImmutableWriteResult:
    """Identity returned after an immutable file is created or verified."""

    path: str
    sha256: str
    size: int
    created: bool
    kind: str = ""

    def to_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "path": self.path,
            "sha256": self.sha256,
            "size": self.size,
            "created": self.created,
        }
        if self.kind:
            value["kind"] = self.kind
        return value


def utc_now() -> str:
    """Return an explicit UTC timestamp suitable for a journal event."""

    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _json_value(value: Any, *, path: str = "$") -> Any:
    if value is None or type(value) in {bool, int, str}:
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise CampaignStateError(f"{path} contains a non-finite number")
        return value
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if type(key) is not str or not key:
                raise CampaignStateError(
                    f"{path} contains a non-string or empty object key"
                )
            result[key] = _json_value(item, path=f"{path}.{key}")
        return result
    if isinstance(value, Sequence) and not isinstance(
        value, (str, bytes, bytearray)
    ):
        return [
            _json_value(item, path=f"{path}[{index}]")
            for index, item in enumerate(value)
        ]
    raise CampaignStateError(
        f"{path} contains a non-JSON value of type {type(value).__name__}"
    )


def canonical_json_bytes(value: Any) -> bytes:
    """Encode canonical JSON as UTF-8 with exactly one trailing LF."""

    normalized = _json_value(value)
    try:
        encoded = json.dumps(
            normalized,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise CampaignStateError(f"value is not canonical JSON: {exc}") from exc
    return encoded.encode("utf-8") + b"\n"


def content_sha256(
    value: Mapping[str, Any],
    *,
    hash_field: str = CONTENT_HASH_FIELD,
) -> str:
    """Hash a canonical object after removing its self-hash field."""

    if not isinstance(value, Mapping):
        raise CampaignStateError("content-hashed value must be an object")
    payload = {key: item for key, item in value.items() if key != hash_field}
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def with_content_hash(
    value: Mapping[str, Any],
    *,
    hash_field: str = CONTENT_HASH_FIELD,
) -> dict[str, Any]:
    """Return a deep immutable-wire copy with a freshly attached self hash."""

    if hash_field in value:
        raise CampaignStateError(
            f"refusing to replace existing self hash field {hash_field!r}"
        )
    payload = _json_value(value)
    if not isinstance(payload, dict):
        raise CampaignStateError("content-hashed value must be an object")
    payload[hash_field] = content_sha256(payload, hash_field=hash_field)
    return payload


def validate_content_hash(
    value: Mapping[str, Any],
    *,
    hash_field: str = CONTENT_HASH_FIELD,
) -> dict[str, Any]:
    """Validate and return a defensive JSON copy of a self-hashed object."""

    normalized = _json_value(value)
    if not isinstance(normalized, dict):
        raise CampaignIntegrityError("content-hashed value must be an object")
    observed = normalized.get(hash_field)
    if type(observed) is not str or not _SHA256_RE.fullmatch(observed):
        raise CampaignIntegrityError(
            f"{hash_field} must be a lowercase SHA-256 digest"
        )
    expected = content_sha256(normalized, hash_field=hash_field)
    if observed != expected:
        raise CampaignIntegrityError(
            f"{hash_field} mismatch: expected {expected}, observed {observed}"
        )
    return normalized


def sha256_file(path: str | os.PathLike[str]) -> str:
    """Return the SHA-256 of one regular, non-symlink file."""

    candidate = Path(path)
    if candidate.is_symlink() or not candidate.is_file():
        raise CampaignIntegrityError(
            f"hash target is not a regular non-symlink file: {candidate}"
        )
    digest = hashlib.sha256()
    with candidate.open("rb") as stream:
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def tree_identity(
    root: str | os.PathLike[str],
    *,
    label: str = "",
) -> dict[str, Any]:
    """Return a stable per-file and aggregate SHA identity for a file tree."""

    root_path = Path(root)
    if root_path.is_symlink() or not root_path.is_dir():
        raise CampaignIntegrityError(
            f"tree root is not a regular directory: {root_path}"
        )
    files: list[dict[str, Any]] = []
    for candidate in sorted(
        root_path.rglob("*"),
        key=lambda item: item.relative_to(root_path).as_posix(),
    ):
        relative = candidate.relative_to(root_path).as_posix()
        if candidate.is_symlink():
            raise CampaignIntegrityError(
                f"tree identity refuses symlink {relative!r}"
            )
        if candidate.is_dir():
            continue
        if not candidate.is_file():
            raise CampaignIntegrityError(
                f"tree identity refuses non-file {relative!r}"
            )
        files.append(
            {
                "path": relative,
                "sha256": sha256_file(candidate),
                "size": candidate.stat().st_size,
            }
        )
    if not files:
        raise CampaignIntegrityError("tree identity requires at least one file")
    material = {"label": label, "files": files}
    return {
        **material,
        "sha256": hashlib.sha256(canonical_json_bytes(material)).hexdigest(),
    }


def sha256_tree(
    root: str | os.PathLike[str],
    *,
    label: str = "",
) -> str:
    """Return only the aggregate SHA-256 from :func:`tree_identity`."""

    return str(tree_identity(root, label=label)["sha256"])


def _is_secret_config_key(key: str) -> bool:
    lowered = key.strip().casefold().replace("-", "_")
    return (
        lowered in _SECRET_CONFIG_KEYS
        or lowered.endswith("_api_key")
        or lowered.endswith("_access_token")
        or lowered.endswith("_auth_token")
        or lowered.endswith("_password")
        or lowered.endswith("_private_key")
        or lowered.endswith("_secret")
    )


def _redact_config(value: Any, *, path: str = "$") -> Any:
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if type(key) is not str or not key:
                raise CampaignStateError(f"{path} has an invalid config key")
            if _is_secret_config_key(key):
                continue
            result[key] = _redact_config(item, path=f"{path}.{key}")
        return result
    if isinstance(value, Sequence) and not isinstance(
        value, (str, bytes, bytearray)
    ):
        return [
            _redact_config(item, path=f"{path}[{index}]")
            for index, item in enumerate(value)
        ]
    return _json_value(value, path=path)


def redacted_config_contract(
    config: Mapping[str, Any],
    *,
    adapter: str = "OpenAICompatibleAdapter",
) -> dict[str, Any]:
    """Freeze the model contract and hash a recursively secret-free config."""

    if not isinstance(config, Mapping):
        raise CampaignStateError("config must be an object")
    redacted = _redact_config(config)
    if not isinstance(redacted, dict):
        raise CampaignStateError("redacted config must be an object")
    api = redacted.get("api")
    vlm = redacted.get("vlm")
    api = api if isinstance(api, dict) else {}
    vlm = vlm if isinstance(vlm, dict) else {}
    provider = str(api.get("provider") or "").strip()
    model = str(api.get("model") or "").strip()
    adapter_name = str(api.get("adapter") or adapter or "").strip()
    temperature = api.get("temperature")
    max_tokens = api.get("max_tokens")
    if not provider or not model or not adapter_name:
        raise CampaignStateError(
            "provider, model, and adapter must be non-empty"
        )
    if (
        type(temperature) not in {int, float}
        or type(temperature) is bool
        or not math.isfinite(float(temperature))
    ):
        raise CampaignStateError("temperature must be a finite number")
    if type(max_tokens) is not int or type(max_tokens) is bool or max_tokens <= 0:
        raise CampaignStateError("max_tokens must be a positive integer")
    contract = {
        "provider": provider,
        "model": model,
        "adapter": adapter_name,
        "temperature": float(temperature),
        "max_tokens": max_tokens,
        "api_max_retries": api.get("max_retries"),
        "vlm_provider": str(vlm.get("provider") or provider),
        "vlm_model": str(vlm.get("model") or model),
        "redacted_config": redacted,
        "config_sha256": hashlib.sha256(
            canonical_json_bytes(redacted)
        ).hexdigest(),
    }
    return _json_value(contract)


def _positive_budget(value: Mapping[str, Any]) -> dict[str, int]:
    names = ("max_turns", "max_runtime_s", "max_model_calls")
    result: dict[str, int] = {}
    for name in names:
        item = value.get(name)
        if type(item) is not int or type(item) is bool or item <= 0:
            raise CampaignStateError(
                f"runtime budget {name} must be a positive integer"
            )
        result[name] = item
    if set(value) != set(names):
        raise CampaignStateError(
            f"runtime budget fields must be exactly {names!r}"
        )
    return result


def expand_sample_matrix(
    matrix: Sequence[Mapping[str, Any]],
    *,
    task: str,
    target: Mapping[str, Any],
    runtime_budget: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Expand six fixed pairs into twelve stable, ordered sample contracts."""

    if type(task) is not str or not task.strip():
        raise CampaignStateError("task must be non-empty")
    if not isinstance(target, Mapping) or not target:
        raise CampaignStateError("target contract must be a non-empty object")
    budget = _positive_budget(runtime_budget)
    target_wire = _json_value(target)
    if len(matrix) != REQUIRED_SAMPLE_COUNT // len(RUNTIMES):
        raise CampaignStateError("matrix must contain exactly six pairs")

    samples: list[dict[str, Any]] = []
    seen_cases: set[str] = set()
    for pair_order, raw_spec in enumerate(matrix, start=1):
        if not isinstance(raw_spec, Mapping):
            raise CampaignStateError("each matrix item must be an object")
        allowed_fields = {
            "case",
            "family",
            "runtime_order",
            "scenario",
            "seed",
        }
        if set(raw_spec) != allowed_fields:
            raise CampaignStateError(
                "matrix item fields must be exactly "
                f"{tuple(sorted(allowed_fields))!r}"
            )
        case = str(raw_spec.get("case") or "")
        family = str(raw_spec.get("family") or "")
        scenario = str(raw_spec.get("scenario") or "")
        seed = raw_spec.get("seed")
        runtime_order = tuple(raw_spec.get("runtime_order") or ())
        if not _SAFE_CASE_RE.fullmatch(case) or case in seen_cases:
            raise CampaignStateError(f"case is invalid or duplicated: {case!r}")
        seen_cases.add(case)
        if family not in FAMILIES or scenario != family:
            raise CampaignStateError(
                f"family/scenario is outside the fixed campaign: "
                f"{family!r}/{scenario!r}"
            )
        if type(seed) is not int or type(seed) is bool or seed <= 0:
            raise CampaignStateError("seed must be a positive integer")
        if len(runtime_order) != 2 or set(runtime_order) != set(RUNTIMES):
            raise CampaignStateError(
                "runtime_order must contain legacy and vnext exactly once"
            )
        for runtime_position, runtime in enumerate(runtime_order, start=1):
            execution_order = len(samples) + 1
            sample_key = f"{execution_order:03d}:{case}:{runtime}"
            sample_target = deepcopy(target_wire)
            sample_target.update(
                {
                    "case": case,
                    "scenario": scenario,
                }
            )
            samples.append(
                {
                    "sample_key": sample_key,
                    "execution_order": execution_order,
                    "pair_order": pair_order,
                    "runtime_position": runtime_position,
                    "case": case,
                    "family": family,
                    "scenario": scenario,
                    "runtime": runtime,
                    "seed": seed,
                    "task": task.strip(),
                    "target": sample_target,
                    "runtime_budget": dict(budget),
                }
            )

    _validate_samples(samples)
    return samples


def _validate_samples(samples: Sequence[Mapping[str, Any]]) -> None:
    if len(samples) != REQUIRED_SAMPLE_COUNT:
        raise CampaignStateError(
            f"manifest must contain exactly {REQUIRED_SAMPLE_COUNT} samples"
        )
    keys: set[str] = set()
    cases: dict[str, list[Mapping[str, Any]]] = {}
    families: dict[str, set[int]] = {family: set() for family in FAMILIES}
    for index, sample in enumerate(samples, start=1):
        required = {
            "case",
            "execution_order",
            "family",
            "pair_order",
            "runtime",
            "runtime_budget",
            "runtime_position",
            "sample_key",
            "scenario",
            "seed",
            "target",
            "task",
        }
        if set(sample) != required:
            raise CampaignStateError(
                f"sample fields must be exactly {tuple(sorted(required))!r}"
            )
        key = sample.get("sample_key")
        if (
            type(key) is not str
            or not _SAFE_IDENTIFIER_RE.fullmatch(key)
            or key in keys
        ):
            raise CampaignStateError(f"sample key is invalid or duplicated: {key!r}")
        keys.add(key)
        if sample.get("execution_order") != index:
            raise CampaignStateError("sample execution_order must be contiguous")
        runtime = sample.get("runtime")
        family = sample.get("family")
        scenario = sample.get("scenario")
        seed = sample.get("seed")
        case = sample.get("case")
        if runtime not in RUNTIMES:
            raise CampaignStateError(f"invalid sample runtime {runtime!r}")
        if family not in FAMILIES or scenario != family:
            raise CampaignStateError("sample family/scenario mismatch")
        if type(seed) is not int or type(seed) is bool or seed <= 0:
            raise CampaignStateError("sample seed must be positive")
        if type(case) is not str or not _SAFE_CASE_RE.fullmatch(case):
            raise CampaignStateError("sample case is invalid")
        if type(sample.get("task")) is not str or not sample["task"].strip():
            raise CampaignStateError("sample task must be non-empty")
        if not isinstance(sample.get("target"), Mapping) or not sample["target"]:
            raise CampaignStateError("sample target must be non-empty")
        _positive_budget(sample["runtime_budget"])
        cases.setdefault(case, []).append(sample)
        families[str(family)].add(int(seed))

    if set(cases) != {str(sample["case"]) for sample in samples}:
        raise CampaignStateError("case index is inconsistent")
    for case, members in cases.items():
        if (
            len(members) != 2
            or {member["runtime"] for member in members} != set(RUNTIMES)
            or len({member["seed"] for member in members}) != 1
            or len({member["family"] for member in members}) != 1
        ):
            raise CampaignStateError(
                f"case {case!r} is not an exact legacy/vnext pair"
            )
    if any(len(seeds) != 2 for seeds in families.values()):
        raise CampaignStateError(
            "each action family must have exactly two predeclared seeds"
        )


def _require_sha(value: Any, name: str) -> str:
    if type(value) is not str or not _SHA256_RE.fullmatch(value):
        raise CampaignStateError(f"{name} must be a lowercase SHA-256 digest")
    return value


def _require_git_sha(value: Any, name: str) -> str:
    if type(value) is not str or not _GIT_SHA_RE.fullmatch(value):
        raise CampaignStateError(f"{name} must be a lowercase Git commit SHA")
    return value


def _validate_timestamp(value: Any, name: str) -> str:
    if type(value) is not str or not value:
        raise CampaignStateError(f"{name} must be a non-empty ISO timestamp")
    candidate = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise CampaignStateError(f"{name} is not an ISO timestamp") from exc
    if parsed.tzinfo is None:
        raise CampaignStateError(f"{name} must include a timezone")
    return value


def _validate_stack(stack: Mapping[str, Any]) -> None:
    required = {"master_commit", "c1", "c2", "c3", "c4"}
    if set(stack) != required:
        raise CampaignStateError(
            f"stack fields must be exactly {tuple(sorted(required))!r}"
        )
    _require_git_sha(stack.get("master_commit"), "master_commit")
    for name, pr_number in (("c1", 27), ("c2", 28), ("c3", 29)):
        layer = stack.get(name)
        if not isinstance(layer, Mapping):
            raise CampaignStateError(f"{name} stack identity must be an object")
        if set(layer) != {"base", "head", "pr"}:
            raise CampaignStateError(f"{name} stack identity fields are invalid")
        if layer.get("pr") != pr_number:
            raise CampaignStateError(f"{name} must bind PR #{pr_number}")
        _require_git_sha(layer.get("head"), f"{name}.head")
        if type(layer.get("base")) is not str or not layer["base"]:
            raise CampaignStateError(f"{name}.base must be non-empty")
    c4 = stack.get("c4")
    if not isinstance(c4, Mapping):
        raise CampaignStateError("c4 stack identity must be an object")
    if set(c4) != {"base", "commit", "merge_ref", "pr_head"}:
        raise CampaignStateError("c4 stack identity fields are invalid")
    for field in ("commit", "merge_ref", "pr_head"):
        _require_git_sha(c4.get(field), f"c4.{field}")
    if c4["commit"] != c4["pr_head"]:
        raise CampaignStateError("c4 commit and PR head must be identical")
    if type(c4.get("base")) is not str or not c4["base"]:
        raise CampaignStateError("c4.base must be non-empty")


def _validate_source_identity(value: Mapping[str, Any]) -> None:
    if set(value) != {"files", "harness_code_commit"}:
        raise CampaignStateError(
            "source_identity requires files and harness_code_commit"
        )
    _require_git_sha(
        value.get("harness_code_commit"),
        "harness_code_commit",
    )
    files = value.get("files")
    if not isinstance(files, Mapping) or not files:
        raise CampaignStateError("source_identity.files must be non-empty")
    for raw_path, digest in files.items():
        _safe_relative_path(raw_path)
        _require_sha(digest, f"source_identity.files[{raw_path!r}]")


def _identity_digest(value: Any, name: str) -> str:
    if type(value) is str:
        return _require_sha(value, name)
    if isinstance(value, Mapping):
        return _require_sha(value.get("sha256"), f"{name}.sha256")
    raise CampaignStateError(f"{name} must be a digest or identity object")


def _validate_build_identity(value: Mapping[str, Any]) -> None:
    required = {"electron", "main", "package_lock", "renderer", "runtime"}
    if set(value) != required:
        raise CampaignStateError(
            f"build_identity fields must be exactly {tuple(sorted(required))!r}"
        )
    for name in ("electron", "main", "package_lock", "renderer"):
        _identity_digest(value.get(name), f"build_identity.{name}")
    runtime = value.get("runtime")
    if not isinstance(runtime, Mapping):
        raise CampaignStateError("build_identity.runtime must be an object")
    if set(runtime) != {"expected_current_commit", "expected_loaded_commit"}:
        raise CampaignStateError(
            "runtime build identity must bind loaded/current commits"
        )
    _require_git_sha(
        runtime.get("expected_loaded_commit"),
        "runtime.expected_loaded_commit",
    )
    _require_git_sha(
        runtime.get("expected_current_commit"),
        "runtime.expected_current_commit",
    )
    if (
        runtime["expected_loaded_commit"]
        != runtime["expected_current_commit"]
    ):
        raise CampaignStateError("runtime loaded/current commits must match")


def _validate_model_config(value: Mapping[str, Any]) -> None:
    required = {
        "adapter",
        "api_max_retries",
        "config_sha256",
        "max_tokens",
        "model",
        "provider",
        "redacted_config",
        "temperature",
        "vlm_model",
        "vlm_provider",
    }
    if set(value) != required:
        raise CampaignStateError(
            f"model_config fields must be exactly {tuple(sorted(required))!r}"
        )
    for name in ("adapter", "model", "provider", "vlm_model", "vlm_provider"):
        if type(value.get(name)) is not str or not value[name]:
            raise CampaignStateError(f"model_config.{name} must be non-empty")
    temperature = value.get("temperature")
    if (
        type(temperature) not in {int, float}
        or type(temperature) is bool
        or not math.isfinite(float(temperature))
    ):
        raise CampaignStateError("model temperature must be finite")
    if (
        type(value.get("max_tokens")) is not int
        or type(value.get("max_tokens")) is bool
        or value["max_tokens"] <= 0
    ):
        raise CampaignStateError("model max_tokens must be positive")
    if value.get("api_max_retries") != 0:
        raise CampaignStateError("model config must freeze SDK retries at zero")
    _require_sha(value.get("config_sha256"), "model_config.config_sha256")
    redacted = value.get("redacted_config")
    if not isinstance(redacted, Mapping):
        raise CampaignStateError("redacted_config must be an object")
    if hashlib.sha256(canonical_json_bytes(redacted)).hexdigest() != value[
        "config_sha256"
    ]:
        raise CampaignIntegrityError("redacted config hash mismatch")
    for key in _walk_mapping_keys(redacted):
        if _is_secret_config_key(key):
            raise CampaignIntegrityError(
                f"redacted config still contains secret key {key!r}"
            )


def _walk_mapping_keys(value: Any) -> Iterable[str]:
    if isinstance(value, Mapping):
        for key, item in value.items():
            yield str(key)
            yield from _walk_mapping_keys(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_mapping_keys(item)


def _validate_oracle(value: Mapping[str, Any]) -> None:
    if set(value) != {"checks", "version"}:
        raise CampaignStateError("external_oracle requires version and checks")
    if type(value.get("version")) is not str or not value["version"]:
        raise CampaignStateError("external oracle version must be non-empty")
    checks = value.get("checks")
    if (
        not isinstance(checks, list)
        or not checks
        or any(type(item) is not str or not item for item in checks)
        or checks != sorted(set(checks))
    ):
        raise CampaignStateError(
            "external oracle checks must be sorted, unique strings"
        )


def _validate_c2_evidence(value: Mapping[str, Any]) -> None:
    required = {"actions_run_id", "c2_head", "code_sha256", "test_nodes", "tests_sha256"}
    if set(value) != required:
        raise CampaignStateError(
            f"C2 evidence fields must be exactly {tuple(sorted(required))!r}"
        )
    if value.get("c2_head") != C2_HEAD:
        raise CampaignStateError(f"C2 evidence must bind {C2_HEAD}")
    for name in ("code_sha256", "tests_sha256"):
        _require_sha(value.get(name), f"c2_unknown_no_replay.{name}")
    nodes = value.get("test_nodes")
    if (
        not isinstance(nodes, list)
        or not nodes
        or any(type(item) is not str or not item for item in nodes)
        or nodes != sorted(set(nodes))
    ):
        raise CampaignStateError("C2 test_nodes must be sorted and unique")
    run = value.get("actions_run_id")
    if type(run) not in {int, str} or not str(run):
        raise CampaignStateError("C2 Actions run id must be non-empty")


def build_manifest(
    *,
    campaign_id: str,
    created_at: str,
    stack: Mapping[str, Any],
    source_identity: Mapping[str, Any],
    build_identity: Mapping[str, Any],
    model_config: Mapping[str, Any],
    matrix: Sequence[Mapping[str, Any]],
    task: str,
    target: Mapping[str, Any],
    runtime_budget: Mapping[str, Any],
    oracle_version: str,
    oracle_checks: Sequence[str] = DEFAULT_ORACLE_CHECKS,
    c2_unknown_no_replay: Mapping[str, Any],
    entrypoint: str = "visible_electron_windows_uia",
    invalid_reason_allowlist: Sequence[str] = DEFAULT_INVALID_REASON_ALLOWLIST,
    rerun_rule: Mapping[str, Any] = DEFAULT_RERUN_RULE,
    safety_abort_conditions: Sequence[str] = DEFAULT_SAFETY_ABORT_CONDITIONS,
) -> dict[str, Any]:
    """Build and validate a complete, self-hashed immutable manifest."""

    payload = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "campaign_id": campaign_id,
        "created_at": created_at,
        "milestone": MILESTONE,
        "entrypoint": entrypoint,
        "campaign_sample_key": CAMPAIGN_SAMPLE_KEY,
        "stack": _json_value(stack),
        "source_identity": _json_value(source_identity),
        "build_identity": _json_value(build_identity),
        "model_config": _json_value(model_config),
        "samples": expand_sample_matrix(
            matrix,
            task=task,
            target=target,
            runtime_budget=runtime_budget,
        ),
        "external_oracle": {
            "version": oracle_version,
            "checks": sorted(set(oracle_checks)),
        },
        "invalid_reason_allowlist": sorted(set(invalid_reason_allowlist)),
        "rerun_rule": _json_value(rerun_rule),
        "safety_abort_conditions": sorted(set(safety_abort_conditions)),
        "c2_unknown_no_replay": _json_value(c2_unknown_no_replay),
    }
    _validate_manifest_payload(payload)
    manifest = with_content_hash(payload)
    return validate_manifest(manifest)


def _validate_manifest_payload(payload: Mapping[str, Any]) -> None:
    required = {
        "build_identity",
        "c2_unknown_no_replay",
        "campaign_id",
        "campaign_sample_key",
        "created_at",
        "entrypoint",
        "external_oracle",
        "invalid_reason_allowlist",
        "milestone",
        "model_config",
        "rerun_rule",
        "safety_abort_conditions",
        "samples",
        "schema_version",
        "source_identity",
        "stack",
    }
    if set(payload) != required:
        raise CampaignStateError(
            f"manifest payload fields must be exactly {tuple(sorted(required))!r}"
        )
    if payload.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        raise CampaignStateError("unsupported campaign manifest schema")
    campaign_id = payload.get("campaign_id")
    if (
        type(campaign_id) is not str
        or not _SAFE_IDENTIFIER_RE.fullmatch(campaign_id)
    ):
        raise CampaignStateError("campaign_id is not a safe stable identifier")
    _validate_timestamp(payload.get("created_at"), "manifest created_at")
    if payload.get("milestone") != MILESTONE:
        raise CampaignStateError(f"manifest milestone must be {MILESTONE!r}")
    if payload.get("campaign_sample_key") != CAMPAIGN_SAMPLE_KEY:
        raise CampaignStateError("manifest campaign sample key is not canonical")
    if type(payload.get("entrypoint")) is not str or not payload["entrypoint"]:
        raise CampaignStateError("manifest entrypoint must be non-empty")
    stack = payload.get("stack")
    source = payload.get("source_identity")
    build = payload.get("build_identity")
    model = payload.get("model_config")
    oracle = payload.get("external_oracle")
    c2 = payload.get("c2_unknown_no_replay")
    for name, value in (
        ("stack", stack),
        ("source_identity", source),
        ("build_identity", build),
        ("model_config", model),
        ("external_oracle", oracle),
        ("c2_unknown_no_replay", c2),
    ):
        if not isinstance(value, Mapping):
            raise CampaignStateError(f"{name} must be an object")
    _validate_stack(stack)
    _validate_source_identity(source)
    _validate_build_identity(build)
    _validate_model_config(model)
    _validate_oracle(oracle)
    _validate_c2_evidence(c2)
    samples = payload.get("samples")
    if not isinstance(samples, list):
        raise CampaignStateError("manifest samples must be an array")
    _validate_samples(samples)
    invalid = payload.get("invalid_reason_allowlist")
    if invalid != sorted(DEFAULT_INVALID_REASON_ALLOWLIST):
        raise CampaignStateError(
            "invalid reason allowlist cannot be expanded or narrowed"
        )
    if payload.get("rerun_rule") != DEFAULT_RERUN_RULE:
        raise CampaignStateError("rerun rule must be the exact one-rerun rule")
    aborts = payload.get("safety_abort_conditions")
    if aborts != sorted(DEFAULT_SAFETY_ABORT_CONDITIONS):
        raise CampaignStateError(
            "safety abort conditions cannot be expanded or narrowed"
        )


def validate_manifest(manifest: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a self-hashed manifest and return a defensive JSON copy."""

    normalized = validate_content_hash(manifest)
    payload = {
        key: item
        for key, item in normalized.items()
        if key != CONTENT_HASH_FIELD
    }
    _validate_manifest_payload(payload)
    return normalized


def _fsync_stream(stream: Any) -> None:
    stream.flush()
    os.fsync(stream.fileno())


def _exclusive_write(path: Path, data: bytes) -> None:
    try:
        with path.open("xb") as stream:
            stream.write(data)
            _fsync_stream(stream)
    except FileExistsError as exc:
        raise CampaignAlreadyExistsError(
            f"immutable path already exists: {path}"
        ) from exc


def seal_manifest(
    campaign_directory: str | os.PathLike[str],
    manifest: Mapping[str, Any],
) -> Path:
    """Exclusively create a new directory and seal its immutable manifest."""

    validated = validate_manifest(manifest)
    directory = Path(campaign_directory)
    if not directory.parent.is_dir():
        raise CampaignIntegrityError(
            f"campaign parent directory does not exist: {directory.parent}"
        )
    try:
        directory.mkdir(exist_ok=False)
    except FileExistsError as exc:
        raise CampaignAlreadyExistsError(
            f"campaign directory already exists: {directory}"
        ) from exc
    path = directory / MANIFEST_FILENAME
    try:
        _exclusive_write(path, canonical_json_bytes(validated))
    except Exception:
        # The consumed directory is intentionally retained.  A later run must
        # fail closed rather than treating a partial campaign as a fresh one.
        raise
    return path


def _read_canonical_object(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise CampaignIntegrityError(f"canonical object is unavailable: {path}")
    raw = path.read_bytes()
    if not raw.endswith(b"\n") or raw.endswith(b"\n\n"):
        raise CampaignIntegrityError(
            f"canonical object is not terminated by exactly one LF: {path}"
        )
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CampaignIntegrityError(
            f"canonical object cannot be decoded: {path}: {exc}"
        ) from exc
    if not isinstance(value, dict):
        raise CampaignIntegrityError(f"canonical object is not an object: {path}")
    if canonical_json_bytes(value) != raw:
        raise CampaignIntegrityError(f"object bytes are not canonical: {path}")
    return value


def load_manifest(
    path_or_directory: str | os.PathLike[str],
) -> dict[str, Any]:
    """Load, canonical-byte-check, and validate a sealed manifest."""

    path = Path(path_or_directory)
    if path.is_dir():
        path = path / MANIFEST_FILENAME
    return validate_manifest(_read_canonical_object(path))


def _safe_relative_path(value: Any) -> str:
    if type(value) is not str or not value or "\\" in value:
        raise CampaignIntegrityError("artifact path must be a POSIX relative path")
    pure = PurePosixPath(value)
    if (
        pure.is_absolute()
        or any(part in {"", ".", ".."} for part in pure.parts)
        or str(pure) != value
    ):
        raise CampaignIntegrityError(f"unsafe relative path: {value!r}")
    return value


def _resolve_under(
    root: Path,
    relative: str,
    *,
    create_parents: bool = False,
) -> Path:
    safe = _safe_relative_path(relative)
    root_resolved = root.resolve()
    candidate = root.joinpath(*PurePosixPath(safe).parts)
    parent = candidate.parent
    if create_parents:
        parent.mkdir(parents=True, exist_ok=True)
    elif not parent.is_dir():
        raise CampaignIntegrityError(
            f"artifact parent directory is unavailable: {relative!r}"
        )
    current = root
    for part in PurePosixPath(safe).parts[:-1]:
        current = current / part
        if current.is_symlink():
            raise CampaignIntegrityError(
                f"artifact path traverses a symlink: {relative!r}"
            )
    resolved_parent = parent.resolve()
    try:
        resolved_parent.relative_to(root_resolved)
    except ValueError as exc:
        raise CampaignIntegrityError(
            f"artifact path escapes campaign directory: {relative!r}"
        ) from exc
    return candidate


def _artifact_identity(
    *,
    relative_path: str,
    digest: str,
    size: int,
    kind: str = "",
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "path": _safe_relative_path(relative_path),
        "sha256": _require_sha(digest, "artifact sha256"),
        "size": size,
    }
    if type(size) is not int or type(size) is bool or size < 0:
        raise CampaignStateError("artifact size must be a non-negative integer")
    if kind:
        if type(kind) is not str or not _SAFE_CASE_RE.fullmatch(kind):
            raise CampaignStateError("artifact kind is invalid")
        result["kind"] = kind
    return result


def validate_artifact_identity(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the relative path, size, digest, and optional artifact kind."""

    if not isinstance(value, Mapping):
        raise CampaignStateError("artifact identity must be an object")
    allowed = {"kind", "path", "sha256", "size"}
    required = {"path", "sha256", "size"}
    if not required.issubset(value) or not set(value).issubset(allowed):
        raise CampaignStateError("artifact identity fields are invalid")
    if "kind" in value and (
        type(value.get("kind")) is not str or not value["kind"]
    ):
        raise CampaignStateError("artifact identity kind must be a string")
    return _artifact_identity(
        relative_path=value["path"],
        digest=value["sha256"],
        size=value["size"],
        kind=value.get("kind") or "",
    )


def write_immutable_artifact(
    campaign_directory: str | os.PathLike[str],
    relative_path: str,
    value: bytes | bytearray | str | Mapping[str, Any] | Sequence[Any],
    *,
    kind: str = "",
) -> dict[str, Any]:
    """Write one campaign artifact exactly once and return its stable identity."""

    root = Path(campaign_directory)
    if not root.is_dir() or root.is_symlink():
        raise CampaignIntegrityError("campaign artifact root is unavailable")
    path = _resolve_under(root, relative_path, create_parents=True)
    if isinstance(value, (bytes, bytearray)):
        data = bytes(value)
    elif type(value) is str:
        data = value.encode("utf-8")
    else:
        data = canonical_json_bytes(value)
    _exclusive_write(path, data)
    return _artifact_identity(
        relative_path=_safe_relative_path(relative_path),
        digest=hashlib.sha256(data).hexdigest(),
        size=len(data),
        kind=kind,
    )


def load_immutable_artifact(
    campaign_directory: str | os.PathLike[str],
    identity: Mapping[str, Any],
) -> bytes:
    """Load an artifact only after path, size, and SHA identity all match."""

    root = Path(campaign_directory)
    checked = validate_artifact_identity(identity)
    path = _resolve_under(root, checked["path"])
    if path.is_symlink() or not path.is_file():
        raise CampaignIntegrityError(
            f"artifact is unavailable: {checked['path']!r}"
        )
    data = path.read_bytes()
    if len(data) != checked["size"]:
        raise CampaignIntegrityError(
            f"artifact size mismatch: {checked['path']!r}"
        )
    observed = hashlib.sha256(data).hexdigest()
    if observed != checked["sha256"]:
        raise CampaignIntegrityError(
            f"artifact hash mismatch: {checked['path']!r}"
        )
    return data


def _contains_reserved_claim(value: Any) -> str:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if str(key) in _RESERVED_EVENT_CLAIM_KEYS:
                return str(key)
            nested = _contains_reserved_claim(item)
            if nested:
                return nested
    elif isinstance(value, list):
        for item in value:
            nested = _contains_reserved_claim(item)
            if nested:
                return nested
    return ""


def _event_material(event: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: item for key, item in event.items() if key != "event_hash"
    }


def _event_hash(event: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        canonical_json_bytes(_event_material(event))
    ).hexdigest()


def _validate_event_shape(
    event: Mapping[str, Any],
    *,
    manifest: Mapping[str, Any],
    expected_sequence: int,
    expected_previous_hash: str,
) -> dict[str, Any]:
    normalized = _json_value(event)
    if not isinstance(normalized, dict):
        raise CampaignIntegrityError("journal event must be an object")
    required = {
        "data",
        "event_hash",
        "kind",
        "previous_event_hash",
        "sample_key",
        "schema_version",
        "sequence",
        "timestamp",
    }
    if set(normalized) != required:
        raise CampaignIntegrityError(
            f"journal event fields must be exactly {tuple(sorted(required))!r}"
        )
    if normalized.get("schema_version") != JOURNAL_SCHEMA_VERSION:
        raise CampaignIntegrityError("unsupported journal event schema")
    if normalized.get("sequence") != expected_sequence:
        raise CampaignIntegrityError(
            f"journal sequence mismatch at {expected_sequence}"
        )
    if normalized.get("previous_event_hash") != expected_previous_hash:
        raise CampaignIntegrityError(
            f"journal hash chain breaks at sequence {expected_sequence}"
        )
    _validate_timestamp(normalized.get("timestamp"), "journal timestamp")
    kind = normalized.get("kind")
    if kind not in JOURNAL_EVENT_KINDS:
        raise CampaignIntegrityError(f"unknown journal event kind {kind!r}")
    sample_key = normalized.get("sample_key")
    known = {
        sample["sample_key"] for sample in manifest["samples"]
    } | {manifest["campaign_sample_key"]}
    if sample_key not in known:
        raise CampaignIntegrityError(
            f"journal event references unknown sample {sample_key!r}"
        )
    if kind in _GLOBAL_EVENT_KINDS and sample_key != CAMPAIGN_SAMPLE_KEY:
        raise CampaignIntegrityError(
            f"global event {kind!r} must use campaign sample key"
        )
    if kind.startswith("sample_") and sample_key == CAMPAIGN_SAMPLE_KEY:
        raise CampaignIntegrityError(
            f"sample event {kind!r} cannot use campaign sample key"
        )
    if kind == "safety_abort" and sample_key not in known:
        raise CampaignIntegrityError("safety abort sample is unknown")
    data = normalized.get("data")
    if not isinstance(data, dict):
        raise CampaignIntegrityError("journal event data must be an object")
    forbidden = _contains_reserved_claim(data)
    if forbidden:
        raise CampaignIntegrityError(
            f"journal event attempts to override manifest field {forbidden!r}"
        )
    observed_hash = normalized.get("event_hash")
    _require_sha(observed_hash, "journal event_hash")
    expected_hash = _event_hash(normalized)
    if observed_hash != expected_hash:
        raise CampaignIntegrityError(
            f"journal event hash mismatch at sequence {expected_sequence}"
        )
    return normalized


def make_journal_event(
    *,
    sequence: int,
    previous_event_hash: str,
    timestamp: str,
    sample_key: str,
    kind: str,
    data: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Construct one canonical hash-chained event without writing it."""

    if type(sequence) is not int or type(sequence) is bool or sequence <= 0:
        raise CampaignStateError("event sequence must be a positive integer")
    _require_sha(previous_event_hash, "previous_event_hash")
    _validate_timestamp(timestamp, "journal timestamp")
    if kind not in JOURNAL_EVENT_KINDS:
        raise CampaignStateError(f"unknown journal event kind {kind!r}")
    if type(sample_key) is not str or not sample_key:
        raise CampaignStateError("journal sample key must be non-empty")
    payload = _json_value(data or {})
    if not isinstance(payload, dict):
        raise CampaignStateError("journal data must be an object")
    event = {
        "schema_version": JOURNAL_SCHEMA_VERSION,
        "sequence": sequence,
        "previous_event_hash": previous_event_hash,
        "timestamp": timestamp,
        "sample_key": sample_key,
        "kind": kind,
        "data": payload,
    }
    event["event_hash"] = _event_hash(event)
    return event


def _validate_attempt_number(data: Mapping[str, Any]) -> int:
    attempt = data.get("attempt")
    if type(attempt) is not int or type(attempt) is bool or attempt not in {1, 2}:
        raise CampaignTransitionError("sample attempt must be 1 or 2")
    return attempt


def _terminal_artifacts(data: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw = data.get("artifacts", [])
    if not isinstance(raw, list):
        raise CampaignTransitionError("terminal artifacts must be an array")
    identities = [validate_artifact_identity(item) for item in raw]
    paths = [item["path"] for item in identities]
    if len(paths) != len(set(paths)):
        raise CampaignTransitionError(
            "terminal event contains duplicated artifact paths"
        )
    return identities


def _initial_reducer_state(
    manifest: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "campaign_status": "sealed",
        "campaign_started": False,
        "campaign_finished": False,
        "abort_event": None,
        "samples": {
            sample["sample_key"]: {
                "sample_key": sample["sample_key"],
                "execution_order": sample["execution_order"],
                "status": "planned",
                "attempts": [],
                "rerun_used": False,
                "rerun_authorized": False,
            }
            for sample in manifest["samples"]
        },
        "head_event_hash": ZERO_EVENT_HASH,
        "event_count": 0,
    }


def _running_sample(state: Mapping[str, Any]) -> str:
    running = [
        key
        for key, sample in state["samples"].items()
        if sample["status"] == "running"
    ]
    if len(running) > 1:
        raise CampaignTransitionError("more than one sample is running")
    return running[0] if running else ""


def _next_planned_sample(state: Mapping[str, Any]) -> str:
    planned = [
        sample
        for sample in state["samples"].values()
        if sample["status"] == "planned"
    ]
    if not planned:
        return ""
    return min(planned, key=lambda item: item["execution_order"])["sample_key"]


def _later_sample_has_started(
    state: Mapping[str, Any],
    sample_key: str,
) -> bool:
    current_order = state["samples"][sample_key]["execution_order"]
    return any(
        sample["execution_order"] > current_order and sample["attempts"]
        for sample in state["samples"].values()
    )


def reduce_campaign_state(
    manifest: Mapping[str, Any],
    events: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Reduce a verified journal into strict campaign and sample state."""

    manifest_wire = validate_manifest(manifest)
    state = _initial_reducer_state(manifest_wire)
    previous_hash = ZERO_EVENT_HASH
    for expected_sequence, raw_event in enumerate(events, start=1):
        event = _validate_event_shape(
            raw_event,
            manifest=manifest_wire,
            expected_sequence=expected_sequence,
            expected_previous_hash=previous_hash,
        )
        kind = event["kind"]
        sample_key = event["sample_key"]
        data = event["data"]

        if state["campaign_finished"]:
            raise CampaignTransitionError(
                "journal contains an event after campaign_finished"
            )
        if state["abort_event"] is not None and kind != "campaign_finished":
            raise CampaignTransitionError(
                "only campaign_finished may follow safety_abort"
            )

        if kind == "campaign_started":
            if state["campaign_started"] or expected_sequence != 1 or data:
                raise CampaignTransitionError(
                    "campaign_started must be the empty first event"
                )
            state["campaign_started"] = True
            state["campaign_status"] = "running"

        elif kind == "sample_started":
            if not state["campaign_started"]:
                raise CampaignTransitionError(
                    "sample cannot start before campaign_started"
                )
            if _running_sample(state):
                raise CampaignTransitionError(
                    "desktop samples must run strictly serially"
                )
            attempt = _validate_attempt_number(data)
            sample = state["samples"][sample_key]
            if attempt == 1:
                if sample["status"] != "planned" or sample["attempts"]:
                    raise CampaignTransitionError(
                        "first sample attempt is duplicated or out of state"
                    )
                if sample_key != _next_planned_sample(state):
                    raise CampaignTransitionError(
                        "sample start violates manifest execution order"
                    )
            else:
                if (
                    sample["status"] != "rerun_authorized"
                    or not sample["rerun_authorized"]
                    or sample["rerun_used"]
                ):
                    raise CampaignTransitionError(
                        "second attempt was not exactly authorized"
                    )
                sample["rerun_used"] = True
                sample["rerun_authorized"] = False
            sample["attempts"].append(
                {
                    "attempt": attempt,
                    "status": "running",
                    "start_event_sequence": event["sequence"],
                    "start_event_hash": event["event_hash"],
                }
            )
            sample["status"] = "running"

        elif kind in _SAMPLE_TERMINAL_EVENT_KINDS:
            sample = state["samples"][sample_key]
            attempt = _validate_attempt_number(data)
            if sample["status"] != "running" or not sample["attempts"]:
                raise CampaignTransitionError(
                    "sample terminal event has no running attempt"
                )
            current = sample["attempts"][-1]
            if current["attempt"] != attempt or current["status"] != "running":
                raise CampaignTransitionError(
                    "sample terminal attempt does not match the running attempt"
                )
            artifacts = _terminal_artifacts(data)
            if kind == "sample_invalid":
                reason = data.get("reason")
                if reason not in manifest_wire["invalid_reason_allowlist"]:
                    raise CampaignTransitionError(
                        f"invalid reason is not allowlisted: {reason!r}"
                    )
                if (
                    data.get("external_evidence") is not True
                    or data.get("pre_mutation") is not True
                    or data.get("first_trusted_mutation_observed") is not False
                ):
                    raise CampaignTransitionError(
                        "invalid verdict requires external pre-mutation evidence"
                    )
                verdict = "invalid"
            elif kind == "sample_passed":
                verdict = "passed"
            else:
                verdict = "failed"
            current.update(
                {
                    "status": verdict,
                    "terminal_event_sequence": event["sequence"],
                    "terminal_event_hash": event["event_hash"],
                    "artifacts": artifacts,
                    "terminal_data": deepcopy(data),
                }
            )
            sample["status"] = verdict

        elif kind == "sample_rerun_authorized":
            sample = state["samples"][sample_key]
            if (
                sample["status"] != "invalid"
                or len(sample["attempts"]) != 1
                or sample["rerun_used"]
                or sample["rerun_authorized"]
                or _later_sample_has_started(state, sample_key)
            ):
                raise CampaignTransitionError(
                    "invalid sample is not eligible for its single rerun"
                )
            invalid_attempt = sample["attempts"][0]
            if (
                data.get("invalid_event_sequence")
                != invalid_attempt.get("terminal_event_sequence")
                or data.get("invalid_event_hash")
                != invalid_attempt.get("terminal_event_hash")
            ):
                raise CampaignTransitionError(
                    "rerun authorization does not bind the invalid verdict"
                )
            sample["rerun_authorized"] = True
            sample["status"] = "rerun_authorized"

        elif kind == "safety_abort":
            if not state["campaign_started"]:
                raise CampaignTransitionError(
                    "safety abort cannot precede campaign_started"
                )
            reason = data.get("reason")
            if reason not in manifest_wire["safety_abort_conditions"]:
                raise CampaignTransitionError(
                    f"unknown safety abort reason {reason!r}"
                )
            state["abort_event"] = {
                "sequence": event["sequence"],
                "event_hash": event["event_hash"],
                "sample_key": sample_key,
                "reason": reason,
                "data": deepcopy(data),
            }
            running_key = _running_sample(state)
            if running_key:
                state["samples"][running_key]["status"] = "safety_aborted"
                state["samples"][running_key]["attempts"][-1]["status"] = (
                    "safety_aborted"
                )
            for sample in state["samples"].values():
                if sample["status"] in {
                    "planned",
                    "rerun_authorized",
                }:
                    sample["status"] = "not_run_due_to_safety_abort"
                    sample["abort_event_sequence"] = event["sequence"]
                    sample["abort_event_hash"] = event["event_hash"]
            state["campaign_status"] = "safety_aborted"

        elif kind == "campaign_finished":
            if not state["campaign_started"] or data:
                raise CampaignTransitionError(
                    "campaign_finished requires an empty payload"
                )
            unfinished = [
                sample["sample_key"]
                for sample in state["samples"].values()
                if sample["status"] in {
                    "planned",
                    "rerun_authorized",
                    "running",
                }
            ]
            if unfinished and state["abort_event"] is None:
                raise CampaignTransitionError(
                    f"campaign cannot finish with unrun samples: {unfinished!r}"
                )
            state["campaign_finished"] = True
            if state["abort_event"] is None:
                all_passed = all(
                    sample["status"] == "passed"
                    for sample in state["samples"].values()
                )
                state["campaign_status"] = (
                    "passed" if all_passed else "failed"
                )

        previous_hash = event["event_hash"]
        state["head_event_hash"] = previous_hash
        state["event_count"] = expected_sequence

    return deepcopy(state)


def load_journal_events(
    path: str | os.PathLike[str],
    manifest: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Load and fully revalidate canonical JSONL, hashes, and transitions."""

    journal_path = Path(path)
    if journal_path.is_symlink() or not journal_path.is_file():
        raise CampaignIntegrityError(f"journal is unavailable: {journal_path}")
    raw = journal_path.read_bytes()
    if not raw or not raw.endswith(b"\n"):
        raise CampaignIntegrityError("journal is empty or contains a truncated line")
    raw_lines = raw.splitlines(keepends=True)
    events: list[dict[str, Any]] = []
    previous_hash = ZERO_EVENT_HASH
    manifest_wire = validate_manifest(manifest)
    for sequence, raw_line in enumerate(raw_lines, start=1):
        if not raw_line.endswith(b"\n") or raw_line == b"\n":
            raise CampaignIntegrityError(
                f"journal line {sequence} is blank or truncated"
            )
        try:
            value = json.loads(raw_line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CampaignIntegrityError(
                f"journal line {sequence} is invalid JSON: {exc}"
            ) from exc
        if not isinstance(value, dict):
            raise CampaignIntegrityError(
                f"journal line {sequence} is not an object"
            )
        if canonical_json_bytes(value) != raw_line:
            raise CampaignIntegrityError(
                f"journal line {sequence} is not canonical JSON"
            )
        checked = _validate_event_shape(
            value,
            manifest=manifest_wire,
            expected_sequence=sequence,
            expected_previous_hash=previous_hash,
        )
        events.append(checked)
        previous_hash = checked["event_hash"]
    reduce_campaign_state(manifest_wire, events)
    return events


class CampaignJournal:
    """Append-only, fully revalidated, fsync'd campaign event journal."""

    def __init__(
        self,
        path: str | os.PathLike[str],
        manifest: Mapping[str, Any],
    ) -> None:
        self.path = Path(path)
        self.manifest = validate_manifest(manifest)
        self._lock = threading.Lock()

    @classmethod
    def create(
        cls,
        path: str | os.PathLike[str],
        manifest: Mapping[str, Any],
        *,
        timestamp: str | None = None,
    ) -> "CampaignJournal":
        """Exclusively create a journal with its canonical start event."""

        instance = cls(path, manifest)
        if not instance.path.parent.is_dir():
            raise CampaignIntegrityError("journal parent directory is unavailable")
        event = make_journal_event(
            sequence=1,
            previous_event_hash=ZERO_EVENT_HASH,
            timestamp=timestamp or utc_now(),
            sample_key=CAMPAIGN_SAMPLE_KEY,
            kind="campaign_started",
            data={},
        )
        _validate_event_shape(
            event,
            manifest=instance.manifest,
            expected_sequence=1,
            expected_previous_hash=ZERO_EVENT_HASH,
        )
        reduce_campaign_state(instance.manifest, [event])
        _exclusive_write(instance.path, canonical_json_bytes(event))
        # Read back after fsync so the caller never proceeds on a partial claim.
        instance.load()
        return instance

    @classmethod
    def open(
        cls,
        path: str | os.PathLike[str],
        manifest: Mapping[str, Any],
    ) -> "CampaignJournal":
        """Open an existing journal only after full verification."""

        instance = cls(path, manifest)
        instance.load()
        return instance

    def load(self) -> list[dict[str, Any]]:
        """Return a fresh defensive copy of all fully verified events."""

        return deepcopy(load_journal_events(self.path, self.manifest))

    def state(self) -> dict[str, Any]:
        """Return the current strict reducer state."""

        return reduce_campaign_state(self.manifest, self.load())

    def append(
        self,
        *,
        sample_key: str,
        kind: str,
        data: Mapping[str, Any] | None = None,
        timestamp: str | None = None,
    ) -> dict[str, Any]:
        """Append one legal event, flush+fsync, then verify the entire chain."""

        with self._lock:
            events = self.load()
            expected_size = self.path.stat().st_size
            previous = events[-1]["event_hash"]
            event = make_journal_event(
                sequence=len(events) + 1,
                previous_event_hash=previous,
                timestamp=timestamp or utc_now(),
                sample_key=sample_key,
                kind=kind,
                data=data or {},
            )
            _validate_event_shape(
                event,
                manifest=self.manifest,
                expected_sequence=len(events) + 1,
                expected_previous_hash=previous,
            )
            reduce_campaign_state(self.manifest, [*events, event])
            flags = os.O_WRONLY | os.O_APPEND
            if hasattr(os, "O_BINARY"):
                flags |= os.O_BINARY
            descriptor = os.open(self.path, flags)
            try:
                if os.fstat(descriptor).st_size != expected_size:
                    raise CampaignIntegrityError(
                        "journal changed between verification and append"
                    )
                with os.fdopen(descriptor, "ab", closefd=False) as stream:
                    stream.write(canonical_json_bytes(event))
                    _fsync_stream(stream)
            finally:
                os.close(descriptor)
            verified = self.load()
            if verified[-1] != event:
                raise CampaignIntegrityError(
                    "journal append did not round-trip exactly"
                )
            return deepcopy(event)


def _field(event: Mapping[str, Any], *names: str) -> Any:
    containers: list[Mapping[str, Any]] = [event]
    for key in ("metadata", "receipt", "details", "runtime_snapshot", "envelope"):
        value = event.get(key)
        if isinstance(value, Mapping):
            containers.append(value)
    for container in containers:
        for name in names:
            if name in container:
                return container.get(name)
    return None


def _decision_id(event: Mapping[str, Any]) -> str:
    return str(_field(event, "decision_id", "action_id") or "")


def _action_name(event: Mapping[str, Any]) -> str:
    return str(
        _field(
            event,
            "controller_action",
            "action",
            "action_type",
        )
        or ""
    )


def _event_number(event: Mapping[str, Any], fallback: int) -> int:
    for name in ("sequence", "event_seq", "trace_sequence"):
        value = event.get(name)
        if type(value) is int and type(value) is not bool and value > 0:
            return value
    return fallback


def _event_time(event: Mapping[str, Any]) -> float | None:
    for container in (
        event,
        event.get("details") if isinstance(event.get("details"), Mapping) else {},
        event.get("metadata") if isinstance(event.get("metadata"), Mapping) else {},
    ):
        for name in (
            "received_at",
            "timestamp",
            "ts",
            "wall_time_s",
        ):
            value = container.get(name)
            if type(value) in {int, float} and type(value) is not bool:
                if math.isfinite(float(value)):
                    return float(value)
        value = container.get("wall_time_ms")
        if type(value) in {int, float} and type(value) is not bool:
            if math.isfinite(float(value)):
                return float(value) / 1000.0
    return None


def _receipt_outcome(event: Mapping[str, Any]) -> str:
    value = _field(event, "outcome")
    return str(value or "").casefold()


def _mechanism(event: Mapping[str, Any]) -> str:
    direct = _field(event, "mechanism", "backend", "via")
    if direct:
        return str(direct)
    receipt = event.get("receipt")
    if isinstance(receipt, Mapping):
        backend = receipt.get("backend_result")
        if isinstance(backend, Mapping):
            facts = backend.get("facts")
            if isinstance(facts, Mapping):
                return str(
                    facts.get("via")
                    or facts.get("mechanism")
                    or facts.get("backend")
                    or ""
                )
    return ""


def _is_trusted_input(event: Mapping[str, Any]) -> bool:
    event_type = str(event.get("type") or event.get("kind") or "")
    trusted = event.get("trusted")
    if trusted is None:
        trusted = _field(event, "trusted", "is_trusted", "injected")
    return event_type in _TRUSTED_INPUT_EVENT_TYPES and trusted is True


def _event_matches_action(event: Mapping[str, Any], action: str) -> bool:
    event_type = str(event.get("type") or event.get("kind") or "")
    if action in {"click", "double_click"}:
        return event_type in {"pointerdown", "pointerup", "click", "dblclick"}
    if action == "scroll":
        return event_type in {"wheel", "scroll_state", "scroll_checkpoint"}
    if action == "type_text":
        return event_type in {"beforeinput", "input", "keydown", "keyup"}
    if action == "hotkey":
        return event_type in {"keydown", "keyup"}
    return False


def _target_mismatch(
    event: Mapping[str, Any],
    target_identity: Mapping[str, Any],
) -> str:
    for key in (
        "target_binding_id",
        "target_hwnd",
        "process_id",
        "root_hwnd",
    ):
        expected = target_identity.get(key)
        observed = _field(event, key)
        if expected is not None and observed is not None and observed != expected:
            return key
    return ""


def _coordinates(event: Mapping[str, Any]) -> tuple[int, int] | None:
    details = event.get("details")
    details = details if isinstance(details, Mapping) else event
    for x_name, y_name in (
        ("screen_x", "screen_y"),
        ("x", "y"),
        ("client_x", "client_y"),
    ):
        x = details.get(x_name)
        y = details.get(y_name)
        if type(x) is int and type(y) is int:
            return x, y
    return None


def _queue_point(event: Mapping[str, Any]) -> tuple[int, int] | None:
    value = _field(event, "point")
    if (
        isinstance(value, list)
        and len(value) == 2
        and all(type(item) is int for item in value)
    ):
        return int(value[0]), int(value[1])
    x = _field(event, "x")
    y = _field(event, "y")
    if type(x) is int and type(y) is int:
        return x, y
    return None


def _external_change(
    events: Sequence[Mapping[str, Any]],
) -> bool | None:
    explicit: list[bool] = []
    for event in events:
        for container in (
            event,
            event.get("details")
            if isinstance(event.get("details"), Mapping)
            else {},
        ):
            value = container.get("actual_changed")
            if type(value) is bool:
                explicit.append(value)
    if explicit:
        return explicit[0] if len(set(explicit)) == 1 else None

    pairs = (
        ("dom_before", "dom_after"),
        ("state_before", "state_after"),
        ("value_before", "value_after"),
        ("scroll_top_before", "scroll_top_after"),
        ("counter_before", "counter_after"),
        ("selection_before", "selection_after"),
    )
    before: Any = None
    after: Any = None
    for event in events:
        details = event.get("details")
        containers = (
            event,
            details if isinstance(details, Mapping) else {},
        )
        for container in containers:
            for before_name, after_name in pairs:
                if before is None and before_name in container:
                    before = container.get(before_name)
                if after_name in container:
                    after = container.get(after_name)
    if before is None or after is None:
        # Existing type-text evidence naturally exposes value_before/value.
        for event in events:
            details = event.get("details")
            details = details if isinstance(details, Mapping) else {}
            if before is None and "value_before" in details:
                before = details.get("value_before")
            if "value" in details:
                after = details.get("value")
    if before is None or after is None:
        return None
    return canonical_json_bytes(before) != canonical_json_bytes(after)


def _action_effect(turn: Mapping[str, Any]) -> Mapping[str, Any] | None:
    direct = turn.get("action_effect")
    if isinstance(direct, Mapping):
        return direct
    snapshot = turn.get("runtime_snapshot")
    if isinstance(snapshot, Mapping):
        effect = snapshot.get("action_effect")
        if isinstance(effect, Mapping):
            return effect
    return None


def _validate_browser_signature(
    action: str,
    queue_start: Mapping[str, Any],
    events: Sequence[Mapping[str, Any]],
) -> list[str]:
    errors: list[str] = []
    types = [str(event.get("type") or "") for event in events]
    if action == "click":
        if (
            types.count("pointerdown") != 1
            or types.count("pointerup") != 1
            or types.count("click") != 1
            or "dblclick" in types
        ):
            errors.append("trusted_click_sequence_mismatch")
    elif action == "double_click":
        if (
            types.count("pointerdown") != 2
            or types.count("pointerup") != 2
            or types.count("click") != 2
            or types.count("dblclick") != 1
        ):
            errors.append("trusted_double_click_sequence_mismatch")
    elif action == "scroll":
        wheels = [event for event in events if event.get("type") == "wheel"]
        if not wheels:
            errors.append("trusted_wheel_missing")
        amount = _field(queue_start, "amount")
        for wheel in wheels:
            delta = _field(wheel, "delta_y")
            direction = str(_field(wheel, "direction") or "")
            if type(amount) is int and amount != 0:
                expected = "up" if amount > 0 else "down"
                observed = (
                    direction
                    or (
                        "down"
                        if type(delta) in {int, float} and delta > 0
                        else "up"
                        if type(delta) in {int, float} and delta < 0
                        else ""
                    )
                )
                if observed != expected:
                    errors.append("wheel_direction_mismatch")
    elif action == "type_text":
        inputs = [
            event for event in events if event.get("type") == "input"
        ]
        if not inputs:
            errors.append("trusted_text_input_missing")
        expected_text = _field(queue_start, "text")
        if type(expected_text) is str:
            observed_text = "".join(
                str(_field(event, "data") or "") for event in inputs
            )
            if observed_text != expected_text:
                errors.append("typed_text_mismatch")
    elif action == "hotkey":
        downs = [
            event for event in events if event.get("type") == "keydown"
        ]
        ups = [event for event in events if event.get("type") == "keyup"]
        if not downs or not ups:
            errors.append("trusted_hotkey_sequence_missing")
        if any(_field(event, "repeat") is True for event in [*downs, *ups]):
            errors.append("repeated_key_event")
        if ups:
            last = ups[-1]
            for modifier in ("ctrl_key", "shift_key", "alt_key", "meta_key"):
                if _field(last, modifier) is True:
                    errors.append("modifier_not_released")
    point = _queue_point(queue_start)
    if point is not None and action in {"click", "double_click", "scroll"}:
        observed_points = {
            candidate
            for event in events
            if (candidate := _coordinates(event)) is not None
            and event.get("type") in {"pointerdown", "pointerup", "wheel"}
        }
        if not observed_points or observed_points != {point}:
            errors.append("input_coordinate_mismatch")
    return sorted(set(errors))


def reconcile_action_evidence(
    *,
    run_id: str,
    obligation_id: str,
    target_identity: Mapping[str, Any],
    queue_start_events: Sequence[Mapping[str, Any]],
    queue_done_events: Sequence[Mapping[str, Any]],
    receipt_events: Sequence[Mapping[str, Any]],
    turn_events: Sequence[Mapping[str, Any]],
    browser_events: Sequence[Mapping[str, Any]],
    os_events: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Uniquely reconcile every admitted action across independent evidence.

    Queue start events are treated as admitted backend-attempt facts.  Events
    should expose ``decision_id``, action, target identity, a monotonic sequence,
    and start/end timestamps.  Browser and OS events may bind a decision
    directly; otherwise they must fall in exactly one non-overlapping queue
    window and match the action signature.  No fuzzy time tolerance is used.
    """

    if type(run_id) is not str or not run_id:
        raise CampaignStateError("run_id must be non-empty")
    if type(obligation_id) is not str or not obligation_id:
        raise CampaignStateError("obligation_id must be non-empty")
    if not isinstance(target_identity, Mapping) or not target_identity:
        raise CampaignStateError("target_identity must be non-empty")

    starts = [_json_value(item) for item in queue_start_events]
    dones = [_json_value(item) for item in queue_done_events]
    receipts = [_json_value(item) for item in receipt_events]
    turns = [_json_value(item) for item in turn_events]
    browser = [_json_value(item) for item in browser_events]
    os_facts = [_json_value(item) for item in os_events]

    errors: list[dict[str, Any]] = []
    disagreements: list[dict[str, Any]] = []
    actions: list[dict[str, Any]] = []
    safety_abort_reasons: set[str] = set()

    start_ids = [_decision_id(item) for item in starts]
    if any(not item for item in start_ids) or len(start_ids) != len(set(start_ids)):
        errors.append(
            {
                "code": "duplicate_or_missing_backend_attempt_id",
                "action_id": "",
            }
        )

    windows: dict[str, tuple[float | None, float | None]] = {}
    for decision in set(start_ids):
        if not decision:
            continue
        candidates = [item for item in starts if _decision_id(item) == decision]
        done_candidates = [item for item in dones if _decision_id(item) == decision]
        if len(candidates) == 1 and len(done_candidates) == 1:
            windows[decision] = (
                _event_time(candidates[0]),
                _event_time(done_candidates[0]),
            )

    def external_candidates(
        source: Sequence[Mapping[str, Any]],
        decision: str,
        action: str,
    ) -> list[Mapping[str, Any]]:
        explicit = [
            item
            for item in source
            if _decision_id(item) == decision
            and _event_matches_action(item, action)
        ]
        if explicit:
            return explicit
        start_time, done_time = windows.get(decision, (None, None))
        if start_time is not None and done_time is not None:
            if done_time < start_time:
                return []
            return [
                item
                for item in source
                if _event_matches_action(item, action)
                and (item_time := _event_time(item)) is not None
                and start_time <= item_time <= done_time
            ]
        same_action_starts = [
            item for item in starts if _action_name(item) == action
        ]
        if len(same_action_starts) == 1:
            return [
                item for item in source if _event_matches_action(item, action)
            ]
        return []

    ordered_starts = sorted(
        enumerate(starts, start=1),
        key=lambda pair: _event_number(pair[1], pair[0]),
    )
    for fallback_order, start in ordered_starts:
        decision = _decision_id(start)
        action = _action_name(start)
        if not decision:
            continue
        action_errors: list[str] = []
        done_matches = [item for item in dones if _decision_id(item) == decision]
        receipt_matches = [
            item for item in receipts if _decision_id(item) == decision
        ]
        turn_matches = [item for item in turns if _decision_id(item) == decision]
        if len(done_matches) != 1:
            action_errors.append("queue_done_not_unique")
        if len(receipt_matches) != 1:
            action_errors.append("raw_receipt_not_unique")
        if len(turn_matches) != 1:
            action_errors.append("turn_action_effect_not_unique")
        if action not in {
            "click",
            "double_click",
            "scroll",
            "type_text",
            "hotkey",
        }:
            action_errors.append("unsupported_action")

        related = [
            start,
            *done_matches,
            *receipt_matches,
            *turn_matches,
        ]
        for item in [*done_matches, *receipt_matches, *turn_matches]:
            observed_action = _action_name(item)
            if (
                observed_action
                and observed_action != action
                and not (
                    action == "double_click" and observed_action == "click"
                )
            ):
                action_errors.append("action_binding_mismatch")
        if any(
            str(_field(item, "run_id") or run_id) != run_id
            for item in related
        ):
            action_errors.append("run_binding_mismatch")
        if any(
            str(_field(item, "obligation_id") or obligation_id)
            != obligation_id
            for item in related
        ):
            action_errors.append("obligation_binding_mismatch")
        mismatches = {
            mismatch
            for item in related
            if (mismatch := _target_mismatch(item, target_identity))
        }
        if mismatches:
            action_errors.append("target_identity_mismatch")
            safety_abort_reasons.add("identity_admission_bypass")

        browser_group = external_candidates(browser, decision, action)
        os_group = external_candidates(os_facts, decision, action)
        trusted_browser = [
            item for item in browser_group if _is_trusted_input(item)
        ]
        trusted_os = [
            item
            for item in os_group
            if item.get("trusted") is True
            or _field(item, "injected", "trusted") is True
        ]
        if not trusted_browser or not trusted_os:
            action_errors.append("evidence_join_ambiguous")
        action_errors.extend(
            _validate_browser_signature(action, start, trusted_browser)
        )

        # Every independently timestamped event must belong to one window.
        for item in [*trusted_browser, *trusted_os]:
            explicit_id = _decision_id(item)
            if explicit_id and explicit_id != decision:
                action_errors.append("evidence_join_ambiguous")
                continue
            item_time = _event_time(item)
            if explicit_id or item_time is None:
                continue
            candidates = [
                other
                for other, (begin, end) in windows.items()
                if begin is not None
                and end is not None
                and begin <= item_time <= end
            ]
            if candidates != [decision]:
                action_errors.append("evidence_join_ambiguous")

        outcome = (
            _receipt_outcome(receipt_matches[0])
            if len(receipt_matches) == 1
            else ""
        )
        mechanisms = {
            value
            for item in [start, *done_matches, *receipt_matches, *os_group]
            if (value := _mechanism(item))
        }
        if len(mechanisms) > 1:
            action_errors.append("mechanism_switch")
        fallback = any(
            _field(
                item,
                "fallback_to_legacy",
                "legacy_fallback",
                "mechanism_switch",
            )
            is True
            or "fallback" in str(item.get("type") or "").casefold()
            for item in related
        )
        if fallback:
            action_errors.append("runtime_legacy_fallback")
            safety_abort_reasons.add("runtime_legacy_fallback")

        actual_changed = _external_change(browser_group)
        effect = _action_effect(turn_matches[0]) if len(turn_matches) == 1 else None
        if (
            effect is not None
            and str(effect.get("action_id") or "") != decision
        ):
            action_errors.append("action_effect_binding_mismatch")
        effect_change = (
            str(effect.get("local_visual_change") or "").casefold()
            if effect is not None
            else "unknown"
        )
        disagreement = False
        if actual_changed is None:
            action_errors.append("external_change_indeterminate")
        elif (
            effect_change == "unknown"
            or (actual_changed and effect_change != "changed")
            or (not actual_changed and effect_change != "unchanged")
        ):
            disagreement = True
            action_errors.append("action_effect_disagreement")
            disagreements.append(
                {
                    "action_id": decision,
                    "action": action,
                    "external_actual_changed": actual_changed,
                    "action_effect": effect_change,
                }
            )

        is_unknown = outcome in {"outcome_unknown", "unknown"}
        backend_attempt_count = len(
            [item for item in starts if _decision_id(item) == decision]
        )
        raw_receipt_count = len(receipt_matches)
        trusted_input_group_count = 1 if trusted_browser or trusted_os else 0
        replay_detected = (
            backend_attempt_count != 1
            or raw_receipt_count != 1
            or len(mechanisms) > 1
            or len(done_matches) > 1
        )
        if is_unknown:
            # Multiple high-level groups are exposed either by explicit foreign
            # action IDs or duplicated queue facts.  Browser and OS records for
            # the same physical input remain one cross-source group.
            browser_ids = {
                _decision_id(item)
                for item in trusted_browser
                if _decision_id(item)
            }
            os_ids = {
                _decision_id(item) for item in trusted_os if _decision_id(item)
            }
            if (
                browser_ids - {decision}
                or os_ids - {decision}
                or replay_detected
            ):
                action_errors.append("unknown_mutation_replay")
                safety_abort_reasons.add("unknown_mutation_replay")

        for code in sorted(set(action_errors)):
            errors.append({"code": code, "action_id": decision})
        actions.append(
            {
                "action_id": decision,
                "decision_id": decision,
                "action": action,
                "order": fallback_order,
                "backend_attempt_count": backend_attempt_count,
                "raw_receipt_count": raw_receipt_count,
                "receipt_outcome": outcome,
                "mechanisms": sorted(mechanisms),
                "browser_event_sequences": [
                    _event_number(item, index)
                    for index, item in enumerate(browser_group, start=1)
                ],
                "os_event_sequences": [
                    _event_number(item, index)
                    for index, item in enumerate(os_group, start=1)
                ],
                "trusted_input_group_count": trusted_input_group_count,
                "actual_changed": actual_changed,
                "action_effect": deepcopy(effect) if effect is not None else None,
                "action_effect_disagreement": disagreement,
                "replay_detected": replay_detected,
                "errors": sorted(set(action_errors)),
            }
        )

    assigned_browser = {
        sequence
        for action in actions
        for sequence in action["browser_event_sequences"]
    }
    assigned_os = {
        sequence
        for action in actions
        for sequence in action["os_event_sequences"]
    }
    all_trusted_browser = {
        _event_number(item, index)
        for index, item in enumerate(browser, start=1)
        if _is_trusted_input(item)
    }
    if all_trusted_browser - assigned_browser:
        errors.append(
            {
                "code": "unauthorized_desktop_input",
                "action_id": "",
            }
        )
        safety_abort_reasons.add("unauthorized_desktop_input")
    all_trusted_os = {
        _event_number(item, index)
        for index, item in enumerate(os_facts, start=1)
        if item.get("trusted") is True
        or _field(item, "injected", "trusted") is True
    }
    if all_trusted_os - assigned_os:
        errors.append(
            {
                "code": "unauthorized_desktop_input",
                "action_id": "",
            }
        )
        safety_abort_reasons.add("unauthorized_desktop_input")

    external_task_passed = (
        sum(item.get("type") == "scenario_success" for item in browser) == 1
        and not any(item.get("type") == "wrong_input" for item in browser)
    )
    terminal_claimed = any(
        str(_field(item, "status") or "").casefold()
        in {"mission_verified", "verified", "done"}
        for item in turns
    )
    false_done_count = int(terminal_claimed and not external_task_passed)
    if false_done_count:
        errors.append({"code": "false_done", "action_id": ""})

    unknown_count = sum(
        action["receipt_outcome"] in {"outcome_unknown", "unknown"}
        for action in actions
    )
    unknown_replay_count = sum(
        action["receipt_outcome"] in {"outcome_unknown", "unknown"}
        and action["replay_detected"]
        for action in actions
    )
    result = {
        "schema_version": ACTION_RECONCILIATION_SCHEMA_VERSION,
        "run_id": run_id,
        "obligation_id": obligation_id,
        "target_identity": _json_value(target_identity),
        "actions": actions,
        "summary": {
            "action_count": len(actions),
            "action_effect_disagreement_count": len(disagreements),
            "evidence_join_ambiguous_count": sum(
                item["code"] == "evidence_join_ambiguous" for item in errors
            ),
            "false_done_count": false_done_count,
            "unknown_observed_count": unknown_count,
            "unknown_replay_count": unknown_replay_count,
            "identity_admission_bypass_count": sum(
                item["code"] == "target_identity_mismatch" for item in errors
            ),
            "legacy_fallback_count": sum(
                item["code"] == "runtime_legacy_fallback" for item in errors
            ),
        },
        "disagreements": disagreements,
        "safety_abort_reasons": sorted(safety_abort_reasons),
        "passed": not errors,
        "errors": errors,
    }
    return with_content_hash(result)


def _decode_canonical_artifact(data: bytes, path: str) -> dict[str, Any]:
    if not data.endswith(b"\n"):
        raise CampaignIntegrityError(
            f"JSON artifact is truncated or non-canonical: {path}"
        )
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CampaignIntegrityError(
            f"JSON artifact cannot be decoded: {path}: {exc}"
        ) from exc
    if not isinstance(value, dict) or canonical_json_bytes(value) != data:
        raise CampaignIntegrityError(
            f"JSON artifact bytes are not canonical: {path}"
        )
    return value


def _sample_report(
    manifest_sample: Mapping[str, Any],
    state_sample: Mapping[str, Any],
    *,
    abort_event: Mapping[str, Any] | None,
    campaign_directory: Path | None,
) -> tuple[dict[str, Any], dict[str, int]]:
    verdict = state_sample["status"]
    report_status = verdict
    errors: list[str] = []
    artifact_paths: set[str] = set()
    reconciliations: list[dict[str, Any]] = []
    attempts = deepcopy(state_sample["attempts"])
    for attempt in attempts:
        identities = attempt.get("artifacts") or []
        for identity in identities:
            checked = validate_artifact_identity(identity)
            if checked["path"] in artifact_paths:
                errors.append("duplicate_artifact_path")
            artifact_paths.add(checked["path"])
            if campaign_directory is None:
                errors.append("artifact_root_missing")
                continue
            try:
                data = load_immutable_artifact(campaign_directory, checked)
                if checked.get("kind") == "action_reconciliation":
                    value = _decode_canonical_artifact(data, checked["path"])
                    reconciliations.append(validate_content_hash(value))
            except CampaignStateError as exc:
                errors.append(f"artifact_integrity:{exc}")

    if verdict == "planned":
        if abort_event is not None:
            report_status = "not_run_due_to_safety_abort"
        else:
            report_status = "failed"
            errors.append("missing_declared_sample")
    elif verdict in {"running", "rerun_authorized"}:
        if abort_event is not None:
            report_status = "not_run_due_to_safety_abort"
        else:
            report_status = "failed"
            errors.append("incomplete_sample")
    elif verdict == "safety_aborted":
        report_status = "safety_aborted"

    if manifest_sample["runtime"] == "vnext" and state_sample["attempts"]:
        terminal_attempts = [
            item
            for item in attempts
            if item.get("status") in {"passed", "failed", "invalid"}
        ]
        if terminal_attempts and len(reconciliations) != len(terminal_attempts):
            errors.append("missing_or_duplicate_action_reconciliation")
        for reconciliation in reconciliations:
            if (
                reconciliation.get("schema_version")
                != ACTION_RECONCILIATION_SCHEMA_VERSION
                or reconciliation.get("passed") is not True
            ):
                errors.append("action_reconciliation_failed")

    if errors and report_status == "passed":
        report_status = "failed"
    summary = {
        "false_done_count": sum(
            int((item.get("summary") or {}).get("false_done_count") or 0)
            for item in reconciliations
        ),
        "unknown_observed_count": sum(
            int((item.get("summary") or {}).get("unknown_observed_count") or 0)
            for item in reconciliations
        ),
        "unknown_replay_count": sum(
            int((item.get("summary") or {}).get("unknown_replay_count") or 0)
            for item in reconciliations
        ),
        "action_effect_disagreement_count": sum(
            int(
                (item.get("summary") or {}).get(
                    "action_effect_disagreement_count"
                )
                or 0
            )
            for item in reconciliations
        ),
        "identity_admission_bypass_count": sum(
            int(
                (item.get("summary") or {}).get(
                    "identity_admission_bypass_count"
                )
                or 0
            )
            for item in reconciliations
        ),
        "legacy_fallback_count": sum(
            int((item.get("summary") or {}).get("legacy_fallback_count") or 0)
            for item in reconciliations
        ),
    }
    sample_report = {
        "sample_key": manifest_sample["sample_key"],
        "execution_order": manifest_sample["execution_order"],
        "family": manifest_sample["family"],
        "runtime": manifest_sample["runtime"],
        "journal_verdict": verdict,
        "report_status": report_status,
        "attempts": attempts,
        "action_reconciliations": reconciliations,
        "errors": sorted(set(errors)),
    }
    if report_status == "not_run_due_to_safety_abort" and abort_event is not None:
        sample_report["abort_event_sequence"] = abort_event["sequence"]
        sample_report["abort_event_hash"] = abort_event["event_hash"]
    return sample_report, summary


def derive_campaign_report(
    manifest: Mapping[str, Any],
    events: Sequence[Mapping[str, Any]],
    *,
    campaign_directory: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """Derive a byte-stable canonical verdict from immutable campaign facts."""

    manifest_wire = validate_manifest(manifest)
    state = reduce_campaign_state(manifest_wire, events)
    root = Path(campaign_directory) if campaign_directory is not None else None
    if root is not None and (not root.is_dir() or root.is_symlink()):
        raise CampaignIntegrityError("campaign artifact directory is unavailable")

    all_artifact_paths: set[str] = set()
    for sample_state in state["samples"].values():
        for attempt in sample_state["attempts"]:
            for identity in attempt.get("artifacts") or []:
                path = validate_artifact_identity(identity)["path"]
                if path in all_artifact_paths:
                    raise CampaignIntegrityError(
                        f"artifact path is reused across attempts: {path!r}"
                    )
                all_artifact_paths.add(path)

    sample_reports: list[dict[str, Any]] = []
    totals = {
        "false_done_count": 0,
        "unknown_observed_count": 0,
        "unknown_replay_count": 0,
        "action_effect_disagreement_count": 0,
        "identity_admission_bypass_count": 0,
        "legacy_fallback_count": 0,
    }
    for sample in manifest_wire["samples"]:
        report, summary = _sample_report(
            sample,
            state["samples"][sample["sample_key"]],
            abort_event=state["abort_event"],
            campaign_directory=root,
        )
        sample_reports.append(report)
        for name in totals:
            totals[name] += summary[name]

    counts: dict[str, int] = {}
    for sample in sample_reports:
        status = sample["report_status"]
        counts[status] = counts.get(status, 0) + 1
    missing = [
        sample["sample_key"]
        for sample in sample_reports
        if "missing_declared_sample" in sample["errors"]
    ]
    all_passed = bool(
        len(sample_reports) == REQUIRED_SAMPLE_COUNT
        and all(item["report_status"] == "passed" for item in sample_reports)
        and not any(totals.values())
        and state["abort_event"] is None
    )
    payload = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "campaign_id": manifest_wire["campaign_id"],
        "manifest_content_sha256": manifest_wire[CONTENT_HASH_FIELD],
        "journal_event_count": state["event_count"],
        "journal_head_event_hash": state["head_event_hash"],
        "campaign_state": state["campaign_status"],
        "passed": all_passed,
        "abort_event": deepcopy(state["abort_event"]),
        "samples": sample_reports,
        "sample_status_counts": {
            key: counts[key] for key in sorted(counts)
        },
        "missing_sample_keys": missing,
        "summary": totals,
        "unknown_supplemental_evidence": (
            deepcopy(manifest_wire["c2_unknown_no_replay"])
            if totals["unknown_observed_count"] == 0
            else None
        ),
    }
    return with_content_hash(payload)


def validate_campaign_report(report: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the canonical report envelope and matrix completeness."""

    value = validate_content_hash(report)
    if value.get("schema_version") != REPORT_SCHEMA_VERSION:
        raise CampaignIntegrityError("unsupported campaign report schema")
    samples = value.get("samples")
    if not isinstance(samples, list) or len(samples) != REQUIRED_SAMPLE_COUNT:
        raise CampaignIntegrityError("campaign report does not contain 12 samples")
    keys = [item.get("sample_key") for item in samples if isinstance(item, dict)]
    if len(keys) != REQUIRED_SAMPLE_COUNT or len(keys) != len(set(keys)):
        raise CampaignIntegrityError("campaign report sample keys are incomplete")
    return value


def seal_campaign_report(
    campaign_directory: str | os.PathLike[str],
    report: Mapping[str, Any],
) -> dict[str, Any]:
    """Create the report once; an identical existing report is a read-only no-op."""

    root = Path(campaign_directory)
    if not root.is_dir() or root.is_symlink():
        raise CampaignIntegrityError("campaign report directory is unavailable")
    value = validate_campaign_report(report)
    data = canonical_json_bytes(value)
    path = root / REPORT_FILENAME
    if path.exists():
        if path.is_symlink() or not path.is_file() or path.read_bytes() != data:
            raise CampaignAlreadyExistsError(
                "existing campaign report differs from derived bytes"
            )
        return ImmutableWriteResult(
            path=REPORT_FILENAME,
            sha256=hashlib.sha256(data).hexdigest(),
            size=len(data),
            created=False,
            kind="campaign_report",
        ).to_dict()
    _exclusive_write(path, data)
    return ImmutableWriteResult(
        path=REPORT_FILENAME,
        sha256=hashlib.sha256(data).hexdigest(),
        size=len(data),
        created=True,
        kind="campaign_report",
    ).to_dict()


def load_campaign_report(
    path_or_directory: str | os.PathLike[str],
) -> dict[str, Any]:
    """Load and validate an immutable canonical campaign report."""

    path = Path(path_or_directory)
    if path.is_dir():
        path = path / REPORT_FILENAME
    return validate_campaign_report(_read_canonical_object(path))


__all__ = [
    "ACTION_RECONCILIATION_SCHEMA_VERSION",
    "C2_HEAD",
    "CAMPAIGN_SAMPLE_KEY",
    "CONTENT_HASH_FIELD",
    "CampaignAlreadyExistsError",
    "CampaignIntegrityError",
    "CampaignJournal",
    "CampaignStateError",
    "CampaignTransitionError",
    "DEFAULT_INVALID_REASON_ALLOWLIST",
    "DEFAULT_ORACLE_CHECKS",
    "DEFAULT_RERUN_RULE",
    "DEFAULT_SAFETY_ABORT_CONDITIONS",
    "FAMILIES",
    "JOURNAL_EVENT_KINDS",
    "JOURNAL_FILENAME",
    "JOURNAL_SCHEMA_VERSION",
    "MANIFEST_FILENAME",
    "MANIFEST_SCHEMA_VERSION",
    "MILESTONE",
    "REPORT_FILENAME",
    "REPORT_SCHEMA_VERSION",
    "REQUIRED_SAMPLE_COUNT",
    "RUNTIMES",
    "ZERO_EVENT_HASH",
    "build_manifest",
    "canonical_json_bytes",
    "content_sha256",
    "derive_campaign_report",
    "expand_sample_matrix",
    "load_campaign_report",
    "load_immutable_artifact",
    "load_journal_events",
    "load_manifest",
    "make_journal_event",
    "reconcile_action_evidence",
    "redacted_config_contract",
    "reduce_campaign_state",
    "seal_campaign_report",
    "seal_manifest",
    "sha256_file",
    "sha256_tree",
    "tree_identity",
    "utc_now",
    "validate_artifact_identity",
    "validate_campaign_report",
    "validate_content_hash",
    "validate_manifest",
    "with_content_hash",
    "write_immutable_artifact",
]
