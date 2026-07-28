"""Fail-closed state and evidence primitives for the vNext live canary.

This module is deliberately independent from Electron, Win32, model SDKs, and
the product runtime.  It provides the durable state layer used by the physical
desktop campaign:

* an immutable, content-addressed campaign manifest;
* an append-only, fsync'd, hash-chained event journal;
* a reducer for the one permitted pre-mutation invalid rerun;
* immutable artifact helpers and a deterministic derived report; and
* pure reconciliation of a rebuilt Runtime evidence report with browser and OS
  oracle facts.

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
RUNTIME_EVIDENCE_REPORT_FORMAT = "runtime-evidence-report/1"
INVALID_RUN_EVIDENCE_KIND = "invalid_run_evidence"

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
    required = {
        "master_commit",
        "c1",
        "c2",
        "c3",
        "evidence_plane",
        "evidence_persistence",
        "c4",
    }
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
    for name, pr_number, expected_base in (
        (
            "evidence_plane",
            32,
            "refactor/vnext-live-model-roles",
        ),
        (
            "evidence_persistence",
            34,
            "refactor/vnext-runtime-evidence-plane",
        ),
    ):
        layer = stack.get(name)
        if not isinstance(layer, Mapping):
            raise CampaignStateError(f"{name} stack identity must be an object")
        if set(layer) != {"base", "head", "pr"}:
            raise CampaignStateError(f"{name} stack identity fields are invalid")
        if layer.get("pr") != pr_number:
            raise CampaignStateError(f"{name} must bind PR #{pr_number}")
        _require_git_sha(layer.get("head"), f"{name}.head")
        if layer.get("base") != expected_base:
            raise CampaignStateError(
                f"{name}.base must be {expected_base!r}"
            )
    c4 = stack.get("c4")
    if not isinstance(c4, Mapping):
        raise CampaignStateError("c4 stack identity must be an object")
    if set(c4) != {"base", "commit", "merge_ref", "pr_head"}:
        raise CampaignStateError("c4 stack identity fields are invalid")
    for field in ("commit", "merge_ref", "pr_head"):
        _require_git_sha(c4.get(field), f"c4.{field}")
    if c4["commit"] != c4["pr_head"]:
        raise CampaignStateError("c4 commit and PR head must be identical")
    if c4.get("base") != "refactor/vnext-runtime-evidence-persistence":
        raise CampaignStateError(
            "c4.base must be 'refactor/vnext-runtime-evidence-persistence'"
        )


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
                if not artifacts or not any(
                    item.get("kind") == INVALID_RUN_EVIDENCE_KIND
                    for item in artifacts
                ):
                    raise CampaignTransitionError(
                        "invalid verdict requires a non-empty immutable "
                        "invalid-run evidence artifact"
                    )
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
            invalid_source: Mapping[str, Any] | None = None
            if kind == "sample_invalid":
                invalid_source = event
            elif kind == "sample_rerun_authorized" or (
                kind == "sample_started"
                and event["data"].get("attempt") == 2
            ):
                invalid_source = next(
                    (
                        prior
                        for prior in reversed(events)
                        if prior.get("sample_key") == sample_key
                        and prior.get("kind") == "sample_invalid"
                    ),
                    None,
                )
                if invalid_source is None:
                    raise CampaignIntegrityError(
                        "rerun has no invalid evidence source"
                    )
            if invalid_source is not None:
                _validate_invalid_run_evidence_artifacts(
                    campaign_directory=self.path.parent,
                    manifest=self.manifest,
                    sample_key=sample_key,
                    terminal_data=invalid_source["data"],
                )
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


def _runtime_canonical_sha256(value: Any) -> str:
    normalized = _json_value(value)
    encoded = json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _record_index(
    report: Mapping[str, Any],
    name: str,
    identity_field: str,
) -> dict[str, dict[str, Any]]:
    values = report.get(name)
    if type(values) is not list:
        raise CampaignIntegrityError(
            f"Runtime evidence {name} must be an array"
        )
    result: dict[str, dict[str, Any]] = {}
    for raw in values:
        if not isinstance(raw, Mapping):
            raise CampaignIntegrityError(
                f"Runtime evidence {name} contains a non-object"
            )
        value = _json_value(raw)
        identity = value.get(identity_field)
        if (
            type(identity) is not str
            or not identity
            or identity in result
        ):
            raise CampaignIntegrityError(
                f"Runtime evidence {name} identity is missing or reused"
            )
        result[identity] = value
    return result


def _target_identity_matches(
    evidence_target: Mapping[str, Any],
    expected_target: Mapping[str, Any],
) -> bool:
    expected = {
        "binding_id": expected_target.get(
            "binding_id",
            expected_target.get("target_binding_id"),
        ),
        "hwnd": expected_target.get(
            "hwnd",
            expected_target.get(
                "target_hwnd",
                expected_target.get("root_hwnd"),
            ),
        ),
        "process_id": expected_target.get("process_id"),
        "executable_identity": expected_target.get("executable_identity"),
        "epoch": expected_target.get("epoch"),
    }
    return all(
        value is None or evidence_target.get(name) == value
        for name, value in expected.items()
    )


def _safe_frame_artifact(
    reference: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "role": reference.get("role"),
        "frame_id": reference.get("frame_id"),
        "frame_identity_sha256": _runtime_canonical_sha256(
            reference.get("frame_identity")
        ),
        "artifact_uri": reference.get("artifact_uri"),
        "raw_jpeg_sha256": reference.get("raw_jpeg_sha256"),
        "runtime_frame_image_sha256": reference.get(
            "runtime_frame_image_sha256"
        ),
        "capture_timestamp_ns": reference.get("capture_timestamp_ns"),
        "reference_sha256": _runtime_canonical_sha256(reference),
    }


def _safe_action_effect(
    transition: Mapping[str, Any],
) -> dict[str, Any] | None:
    effect = transition.get("action_effect")
    if not isinstance(effect, Mapping):
        return None
    evidence_ref = transition.get("action_effect_evidence")
    return {
        "action_id": effect.get("action_id"),
        "input_completed": effect.get("input_completed"),
        "before_frame_id": effect.get("before_frame_id"),
        "after_frame_id": effect.get("after_frame_id"),
        "before_frame_payload_hash": effect.get(
            "before_frame_payload_hash"
        ),
        "after_frame_payload_hash": effect.get(
            "after_frame_payload_hash"
        ),
        "target_binding_valid": effect.get("target_binding_valid"),
        "frame_comparable": effect.get("frame_comparable"),
        "local_visual_change": effect.get("local_visual_change"),
        "change_ratio": effect.get("change_ratio"),
        "reason": effect.get("reason"),
        "semantic_completion": effect.get("semantic_completion"),
        "evidence_ref": effect.get("evidence_ref"),
        "payload_hash": effect.get("payload_hash"),
        "record_sha256": _runtime_canonical_sha256(effect),
        "evidence_reference_sha256": (
            _runtime_canonical_sha256(evidence_ref)
            if isinstance(evidence_ref, Mapping)
            else None
        ),
    }


def _validate_runtime_report_anchor(
    *,
    run_id: str,
    report_value: Mapping[str, Any],
    anchor_value: Mapping[str, Any],
) -> dict[str, Any]:
    report = _json_value(report_value)
    anchor = _json_value(anchor_value)
    expected_report_fields = {
        "format",
        "mission_id",
        "run_id",
        "status",
        "actor_decisions",
        "admissions",
        "physical_attempts",
        "action_bindings",
        "frame_artifacts",
        "journal_head_sha256",
        "journal_event_count",
        "missing_evidence",
        "verification_failures",
    }
    if set(report) != expected_report_fields:
        raise CampaignIntegrityError(
            "Runtime evidence report fields are not canonical"
        )
    status = report.get("status")
    missing = report.get("missing_evidence")
    failures = report.get("verification_failures")
    if (
        report.get("format") != RUNTIME_EVIDENCE_REPORT_FORMAT
        or report.get("run_id") != run_id
        or status not in {"complete", "incomplete", "verification_failed"}
        or type(missing) is not list
        or type(failures) is not list
    ):
        raise CampaignIntegrityError(
            "Runtime evidence report is not a canonical rebuilt report"
        )
    if status == "verification_failed" or failures:
        raise CampaignIntegrityError(
            "Runtime evidence report contains verification failures"
        )
    head = _require_sha(
        report.get("journal_head_sha256"),
        "Runtime evidence journal head",
    )
    event_count = report.get("journal_event_count")
    if (
        type(event_count) is not int
        or type(event_count) is bool
        or event_count <= 0
    ):
        raise CampaignIntegrityError(
            "Runtime evidence report has no durable journal events"
        )
    required_anchor_fields = {
        "type",
        "ts",
        "mission_id",
        "run_id",
        "journal_head_sha256",
        "journal_event_count",
        "report_sha256",
        "report_status",
        "missing_evidence_count",
        "verification_failure_count",
        "runtime_version",
        "no_legacy_fallback",
    }
    if not required_anchor_fields.issubset(anchor):
        raise CampaignIntegrityError(
            "Runtime evidence seal anchor is incomplete"
        )
    report_sha256 = _runtime_canonical_sha256(report)
    if (
        anchor.get("mission_id") != report.get("mission_id")
        or anchor.get("run_id") != run_id
        or anchor.get("journal_head_sha256") != head
        or anchor.get("journal_event_count") != event_count
        or anchor.get("report_sha256") != report_sha256
        or anchor.get("report_status") != status
        or anchor.get("missing_evidence_count") != len(missing)
        or anchor.get("verification_failure_count") != len(failures)
        or anchor.get("type") != "runtime_evidence_sealed"
        or type(anchor.get("ts")) not in {int, float}
        or type(anchor.get("ts")) is bool
        or not math.isfinite(float(anchor["ts"]))
        or anchor.get("runtime_version") != "vnext"
        or anchor.get("no_legacy_fallback") is not True
    ):
        raise CampaignIntegrityError(
            "Runtime evidence seal anchor does not authenticate rebuilt report"
        )
    return {
        "mission_id": report["mission_id"],
        "run_id": run_id,
        "journal_head_sha256": head,
        "journal_event_count": event_count,
        "report_sha256": report_sha256,
        "report_status": status,
        "missing_evidence_count": len(missing),
        "verification_failure_count": len(failures),
        "runtime_version": "vnext",
        "no_legacy_fallback": True,
    }


def _browser_signature_errors(
    *,
    action: str,
    canonical_arguments: Mapping[str, Any],
    physical_arguments: Mapping[str, Any],
    events: Sequence[Mapping[str, Any]],
) -> list[str]:
    errors: list[str] = []
    types = [str(event.get("type") or event.get("kind") or "") for event in events]
    if action == "click":
        if (
            types.count("pointerdown") != 1
            or types.count("pointerup") != 1
            or types.count("click") != 1
            or types.count("dblclick") != 0
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
        if len(wheels) != 1:
            errors.append("trusted_wheel_sequence_mismatch")
        expected_direction = (
            "up"
            if physical_arguments.get("signed_wheel_amount", 0) > 0
            else "down"
        )
        for wheel in wheels:
            direction = str(_field(wheel, "direction") or "")
            delta = _field(wheel, "delta_y", "wheel_delta")
            if not direction and type(delta) in {int, float}:
                direction = "up" if float(delta) < 0 else "down"
            if direction != expected_direction:
                errors.append("wheel_direction_mismatch")
    elif action == "type_text":
        inputs = [event for event in events if event.get("type") == "input"]
        observed = "".join(str(_field(event, "data") or "") for event in inputs)
        if not inputs or observed != canonical_arguments.get("text"):
            errors.append("trusted_text_input_mismatch")
    elif action == "hotkey":
        def canonical_key(value: Any) -> str:
            folded = str(value or "").casefold()
            return "ctrl" if folded == "control" else folded

        expected_keys = [
            canonical_key(item) for item in canonical_arguments.get("keys", [])
        ]
        downs = [
            canonical_key(_field(event, "key"))
            for event in events
            if event.get("type") == "keydown"
        ]
        ups = [
            canonical_key(_field(event, "key"))
            for event in events
            if event.get("type") == "keyup"
        ]
        if (
            downs != expected_keys
            or sorted(ups) != sorted(expected_keys)
            or any(_field(event, "repeat") is True for event in events)
        ):
            errors.append("trusted_hotkey_sequence_mismatch")
    if action in {"click", "double_click", "scroll"}:
        expected_point = (
            physical_arguments.get("x"),
            physical_arguments.get("y"),
        )
        points = {
            point
            for event in events
            if (point := _coordinates(event)) is not None
            and str(event.get("type") or "") in {
                "pointerdown",
                "pointerup",
                "wheel",
            }
        }
        if points != {expected_point}:
            errors.append("input_coordinate_mismatch")
    return sorted(set(errors))


def _os_signature_errors(
    *,
    action: str,
    canonical_arguments: Mapping[str, Any],
    events: Sequence[Mapping[str, Any]],
) -> list[str]:
    errors: list[str] = []
    types = [str(event.get("type") or event.get("kind") or "") for event in events]
    if action in {"click", "double_click"}:
        expected = 2 if action == "double_click" else 1
        if (
            types.count("pointerdown") != expected
            or types.count("pointerup") != expected
        ):
            errors.append("trusted_os_pointer_sequence_mismatch")
    elif action == "scroll":
        if types.count("wheel") != 1:
            errors.append("trusted_os_wheel_sequence_mismatch")
    elif action == "type_text":
        if not any(item in {"keydown", "keyup"} for item in types):
            errors.append("trusted_os_key_sequence_missing")
    elif action == "hotkey":
        expected_count = len(canonical_arguments.get("keys", []))
        if (
            types.count("keydown") != expected_count
            or types.count("keyup") != expected_count
        ):
            errors.append("trusted_os_hotkey_sequence_mismatch")
    return errors


def _same_decision_physical_replay(
    *,
    action: str,
    canonical_arguments: Mapping[str, Any],
    browser_events: Sequence[Mapping[str, Any]],
    os_events: Sequence[Mapping[str, Any]],
) -> bool:
    browser_types = [
        str(event.get("type") or event.get("kind") or "")
        for event in browser_events
    ]
    os_types = [
        str(event.get("type") or event.get("kind") or "")
        for event in os_events
    ]
    if action in {"click", "double_click"}:
        expected = 2 if action == "double_click" else 1
        return bool(
            browser_types.count("pointerdown") > expected
            or browser_types.count("pointerup") > expected
            or browser_types.count("click") > expected
            or browser_types.count("dblclick")
            > (1 if action == "double_click" else 0)
            or os_types.count("pointerdown") > expected
            or os_types.count("pointerup") > expected
        )
    if action == "scroll":
        return bool(
            browser_types.count("wheel") > 1
            or os_types.count("wheel") > 1
        )
    if action == "hotkey":
        expected = len(canonical_arguments.get("keys", []))
        return bool(
            browser_types.count("keydown") > expected
            or browser_types.count("keyup") > expected
            or os_types.count("keydown") > expected
            or os_types.count("keyup") > expected
        )
    if action == "type_text":
        expected_text = str(canonical_arguments.get("text") or "")
        browser_inputs = [
            event
            for event in browser_events
            if str(event.get("type") or event.get("kind") or "") == "input"
        ]
        observed_text = "".join(
            str(_field(event, "data") or "") for event in browser_inputs
        )
        browser_replay = bool(
            expected_text and len(observed_text) > len(expected_text)
        )
        key_events = [
            event
            for event in os_events
            if str(event.get("type") or event.get("kind") or "")
            in {"keydown", "keyup"}
        ]

        def key_signature(event: Mapping[str, Any]) -> tuple[Any, ...]:
            return (
                str(event.get("type") or event.get("kind") or ""),
                _field(event, "vk_code"),
                _field(event, "scan_code"),
                _field(event, "key"),
                _field(event, "code"),
                _field(event, "extended"),
            )

        signatures = [key_signature(event) for event in key_events]
        midpoint = len(signatures) // 2
        os_replay = bool(
            expected_text
            and observed_text == expected_text
            and len(signatures) >= 4
            and len(signatures) % 2 == 0
            and signatures[:midpoint] == signatures[midpoint:]
        )
        return browser_replay or os_replay
    return False


def reconcile_action_evidence(
    *,
    run_id: str,
    obligation_id: str,
    target_identity: Mapping[str, Any],
    runtime_terminal_status: str,
    external_oracle_passed: bool,
    runtime_evidence_report: Mapping[str, Any],
    runtime_evidence_anchor: Mapping[str, Any],
    browser_events: Sequence[Mapping[str, Any]],
    os_events: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Join a rebuilt canonical Runtime report to independent external facts.

    The complete in-memory Runtime report is consumed only as an authenticated
    fact source.  The returned object deliberately projects hashes, identities,
    counts, immutable references, and bounded classifications; it never copies
    canonical arguments, Actor envelopes, receipts, transitions, or the report.
    """

    if type(run_id) is not str or not run_id:
        raise CampaignStateError("run_id must be non-empty")
    if type(obligation_id) is not str or not obligation_id:
        raise CampaignStateError("obligation_id must be non-empty")
    if not isinstance(target_identity, Mapping) or not target_identity:
        raise CampaignStateError("target_identity must be non-empty")
    if type(runtime_terminal_status) is not str or not runtime_terminal_status:
        raise CampaignStateError(
            "runtime_terminal_status must be non-empty"
        )
    if type(external_oracle_passed) is not bool:
        raise CampaignStateError(
            "external_oracle_passed must be boolean"
        )
    if not isinstance(runtime_evidence_report, Mapping):
        raise CampaignStateError("runtime_evidence_report must be an object")
    if not isinstance(runtime_evidence_anchor, Mapping):
        raise CampaignStateError("runtime_evidence_anchor must be an object")

    report = _json_value(runtime_evidence_report)
    safe_anchor = _validate_runtime_report_anchor(
        run_id=run_id,
        report_value=report,
        anchor_value=runtime_evidence_anchor,
    )
    browser = [_json_value(item) for item in browser_events]
    os_facts = [_json_value(item) for item in os_events]
    decisions = _record_index(report, "actor_decisions", "decision_id")
    admissions = _record_index(report, "admissions", "decision_id")
    attempts = _record_index(report, "physical_attempts", "decision_id")
    bindings = _record_index(report, "action_bindings", "decision_id")
    artifact_values = report.get("frame_artifacts")
    if type(artifact_values) is not list:
        raise CampaignIntegrityError(
            "Runtime evidence frame_artifacts must be an array"
        )
    artifacts: dict[tuple[str, str], dict[str, Any]] = {}
    for raw in artifact_values:
        if not isinstance(raw, Mapping):
            raise CampaignIntegrityError(
                "Runtime evidence frame_artifacts contains a non-object"
            )
        value = _json_value(raw)
        key = (str(value.get("decision_id") or ""), str(value.get("role") or ""))
        if not key[0] or key[1] not in {"before", "after"} or key in artifacts:
            raise CampaignIntegrityError(
                "Runtime evidence frame artifact identity is missing or reused"
            )
        artifacts[key] = value
    decision_ids = set(decisions)
    if (
        set(admissions) != decision_ids
        or set(attempts) != decision_ids
        or set(bindings) != decision_ids
        or not set(artifacts).issubset(
            {
                (decision_id, role)
                for decision_id in decision_ids
                for role in ("before", "after")
            }
        )
        or any(
            (decision_id, "before") not in artifacts
            for decision_id in decision_ids
        )
    ):
        raise CampaignIntegrityError(
            "complete Runtime evidence report has a missing or orphan record"
        )

    errors: list[dict[str, Any]] = []
    disagreements: list[dict[str, Any]] = []
    actions: list[dict[str, Any]] = []
    safety_abort_reasons: set[str] = set()
    if safe_anchor["report_status"] != "complete":
        errors.append(
            {"code": "runtime_evidence_incomplete", "action_id": ""}
        )
    mutation_groups: dict[str, list[str]] = {}
    ordered_decisions = sorted(
        decisions.values(),
        key=lambda item: (
            int(item.get("turn_index", 0)),
            str(item.get("decision_id") or ""),
        ),
    )
    for order, decision in enumerate(ordered_decisions, start=1):
        decision_id = str(decision["decision_id"])
        action = str(decision.get("action") or "")
        admission = admissions[decision_id]
        attempt = attempts[decision_id]
        binding = bindings[decision_id]
        before_ref = artifacts[(decision_id, "before")]
        after_ref = artifacts.get((decision_id, "after"))
        action_errors: list[str] = []

        if (
            decision.get("run_id") != run_id
            or decision.get("obligation_id") != obligation_id
            or attempt.get("run_id") != run_id
            or attempt.get("obligation_id") != obligation_id
        ):
            action_errors.append("run_or_obligation_binding_mismatch")
        decision_sha256 = _runtime_canonical_sha256(decision)
        admission_sha256 = _runtime_canonical_sha256(admission)
        attempt_sha256 = _runtime_canonical_sha256(attempt)
        if (
            admission.get("actor_decision_record_sha256") != decision_sha256
            or admission.get("actor_envelope_sha256")
            != decision.get("actor_envelope_sha256")
            or attempt.get("actor_decision_record_sha256") != decision_sha256
            or attempt.get("admission_record_sha256") != admission_sha256
            or attempt.get("action_id") != decision_id
            or attempt.get("action") != action
            or binding.get("action_id") != decision_id
            or binding.get("action") != action
            or binding.get("actor_envelope_sha256")
            != decision.get("actor_envelope_sha256")
            or binding.get("physical_attempt_count") != 1
            or binding.get("attempt_reservation_sha256")
            != attempt.get("reservation_sha256")
            or binding.get("physical_attempt_record_sha256") != attempt_sha256
        ):
            raise CampaignIntegrityError(
                "Runtime evidence action records do not form one canonical chain"
            )
        evidence_target = admission.get("target_identity")
        if (
            not isinstance(evidence_target, Mapping)
            or evidence_target != attempt.get("target_identity")
            or not _target_identity_matches(evidence_target, target_identity)
        ):
            action_errors.append("target_identity_mismatch")
            safety_abort_reasons.add("identity_admission_bypass")
        for reference, role, binding_field in (
            (
                before_ref,
                "before",
                "before_artifact_reference_sha256",
            ),
            (
                after_ref,
                "after",
                "after_artifact_reference_sha256",
            ),
        ):
            if reference is None:
                if binding.get(f"{role}_frame_identity") is not None:
                    raise CampaignIntegrityError(
                        "Runtime evidence frame identity lacks its artifact"
                    )
                continue
            expected_identity = binding.get(f"{role}_frame_identity")
            if (
                reference.get("action_id") != decision_id
                or reference.get("frame_identity") != expected_identity
                or binding.get(binding_field)
                != _runtime_canonical_sha256(reference)
            ):
                raise CampaignIntegrityError(
                    "Runtime evidence frame artifact is outside action binding"
                )

        raw_receipt = binding.get("raw_action_receipt")
        transition = binding.get("controller_transition")
        if not isinstance(raw_receipt, Mapping) or not isinstance(
            transition, Mapping
        ):
            raise CampaignIntegrityError(
                "Runtime evidence action binding lacks receipt or transition"
            )
        if (
            binding.get("raw_action_receipt_sha256")
            != _runtime_canonical_sha256(raw_receipt)
            or binding.get("controller_transition_sha256")
            != _runtime_canonical_sha256(transition)
        ):
            raise CampaignIntegrityError(
                "Runtime evidence receipt or transition hash changed"
            )

        browser_group = [
            item for item in browser if _decision_id(item) == decision_id
        ]
        os_group = [
            item for item in os_facts if _decision_id(item) == decision_id
        ]
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
        canonical_arguments = decision.get("canonical_arguments")
        physical_arguments = attempt.get("physical_arguments")
        if not isinstance(canonical_arguments, Mapping) or not isinstance(
            physical_arguments, Mapping
        ):
            raise CampaignIntegrityError(
                "Runtime evidence action arguments are not canonical"
            )
        action_errors.extend(
            _browser_signature_errors(
                action=action,
                canonical_arguments=canonical_arguments,
                physical_arguments=physical_arguments,
                events=trusted_browser,
            )
        )
        action_errors.extend(
            _os_signature_errors(
                action=action,
                canonical_arguments=canonical_arguments,
                events=trusted_os,
            )
        )
        if any(
            _target_mismatch(item, target_identity)
            for item in [*trusted_browser, *trusted_os]
        ):
            action_errors.append("target_identity_mismatch")
            safety_abort_reasons.add("identity_admission_bypass")

        outcome = str(raw_receipt.get("outcome") or "").casefold()
        same_decision_replay = _same_decision_physical_replay(
            action=action,
            canonical_arguments=canonical_arguments,
            browser_events=trusted_browser,
            os_events=trusted_os,
        )
        if (
            outcome in {"outcome_unknown", "unknown"}
            and same_decision_replay
        ):
            action_errors.append("unknown_mutation_replay")
            safety_abort_reasons.add("unknown_mutation_replay")
        actual_changed = _external_change(browser_group)
        safe_effect = _safe_action_effect(transition)
        effect_change = (
            str(safe_effect.get("local_visual_change") or "").casefold()
            if safe_effect is not None
            else "unknown"
        )
        disagreement = False
        if actual_changed is None:
            action_errors.append("external_change_indeterminate")
        elif (
            safe_effect is None
            or effect_change == "unknown"
            or (actual_changed and effect_change != "changed")
            or (not actual_changed and effect_change != "unchanged")
        ):
            disagreement = True
            action_errors.append("action_effect_disagreement")
            disagreements.append(
                {
                    "action_id": decision_id,
                    "action": action,
                    "external_actual_changed": actual_changed,
                    "action_effect": effect_change,
                }
            )

        mutation_group_sha256 = _runtime_canonical_sha256(
            {
                "run_id": run_id,
                "obligation_id": obligation_id,
                "action": action,
                "canonical_arguments_sha256": decision.get(
                    "canonical_arguments_sha256"
                ),
                "target_identity_sha256": _runtime_canonical_sha256(
                    evidence_target
                ),
            }
        )
        mutation_groups.setdefault(mutation_group_sha256, []).append(
            decision_id
        )
        action_result = {
            "action_id": decision_id,
            "decision_id": decision_id,
            "action": action,
            "order": order,
            "decision_record_sha256": decision_sha256,
            "actor_envelope_sha256": decision.get("actor_envelope_sha256"),
            "canonical_arguments_sha256": decision.get(
                "canonical_arguments_sha256"
            ),
            "broker_projection_sha256": decision.get(
                "broker_projection_sha256"
            ),
            "tool_schema_sha256": decision.get("tool_schema_sha256"),
            "admission_record_sha256": admission_sha256,
            "mutation_permit_id": admission.get("mutation_permit_id"),
            "controller_route": admission.get("controller_route"),
            "target_identity_sha256": _runtime_canonical_sha256(
                evidence_target
            ),
            "physical_attempt_count": 1,
            "physical_attempt_record_sha256": attempt_sha256,
            "attempt_reservation_sha256": attempt.get(
                "reservation_sha256"
            ),
            "raw_device_route": attempt.get("raw_device_route"),
            "mechanism": attempt.get("mechanism"),
            "raw_action_receipt_sha256": binding.get(
                "raw_action_receipt_sha256"
            ),
            "controller_transition_sha256": binding.get(
                "controller_transition_sha256"
            ),
            "receipt_outcome": outcome,
            "before_frame_artifact": _safe_frame_artifact(before_ref),
            "after_frame_artifact": (
                _safe_frame_artifact(after_ref)
                if after_ref is not None
                else None
            ),
            "action_effect": safe_effect,
            "browser_event_sequences": [
                _event_number(item, index)
                for index, item in enumerate(browser_group, start=1)
            ],
            "os_event_sequences": [
                _event_number(item, index)
                for index, item in enumerate(os_group, start=1)
            ],
            "trusted_browser_event_count": len(trusted_browser),
            "trusted_os_event_count": len(trusted_os),
            "trusted_input_group_count": (
                2
                if same_decision_replay
                else int(bool(trusted_browser) and bool(trusted_os))
            ),
            "actual_changed": actual_changed,
            "action_effect_disagreement": disagreement,
            "mutation_group_sha256": mutation_group_sha256,
            "replay_detected": same_decision_replay,
            "errors": sorted(set(action_errors)),
        }
        actions.append(action_result)

    action_by_id = {item["action_id"]: item for item in actions}
    for group_ids in mutation_groups.values():
        if len(group_ids) <= 1:
            continue
        if any(
            action_by_id[action_id]["receipt_outcome"]
            in {"outcome_unknown", "unknown"}
            for action_id in group_ids
        ):
            safety_abort_reasons.add("unknown_mutation_replay")
            for action_id in group_ids:
                action_by_id[action_id]["replay_detected"] = True
                action_by_id[action_id]["errors"] = sorted(
                    {
                        *action_by_id[action_id]["errors"],
                        "unknown_mutation_replay",
                    }
                )

    known_ids = set(action_by_id)
    unauthorized_browser = [
        item
        for item in browser
        if _is_trusted_input(item) and _decision_id(item) not in known_ids
    ]
    unauthorized_os = [
        item
        for item in os_facts
        if (
            item.get("trusted") is True
            or _field(item, "injected", "trusted") is True
        )
        and _decision_id(item) not in known_ids
    ]
    if unauthorized_browser or unauthorized_os:
        errors.append(
            {"code": "unauthorized_desktop_input", "action_id": ""}
        )
        safety_abort_reasons.add("unauthorized_desktop_input")
    if (
        runtime_terminal_status == "mission_verified"
        and not external_oracle_passed
    ):
        errors.append({"code": "false_done", "action_id": ""})

    for action_result in actions:
        for code in action_result["errors"]:
            errors.append(
                {"code": code, "action_id": action_result["action_id"]}
            )
    errors = sorted(
        {
            (item["code"], item["action_id"])
            for item in errors
        }
    )
    error_wires = [
        {"code": code, "action_id": action_id}
        for code, action_id in errors
    ]
    unknown_count = sum(
        item["receipt_outcome"] in {"outcome_unknown", "unknown"}
        for item in actions
    )
    unknown_replay_count = sum(
        item["receipt_outcome"] in {"outcome_unknown", "unknown"}
        and item["replay_detected"]
        for item in actions
    )
    result = {
        "schema_version": ACTION_RECONCILIATION_SCHEMA_VERSION,
        "run_id": run_id,
        "obligation_id": obligation_id,
        "runtime_terminal_status": runtime_terminal_status,
        "external_oracle_passed": external_oracle_passed,
        "target_identity_sha256": _runtime_canonical_sha256(
            target_identity
        ),
        "runtime_evidence": safe_anchor,
        "actions": actions,
        "summary": {
            "action_count": len(actions),
            "action_effect_disagreement_count": len(disagreements),
            "evidence_join_ambiguous_count": sum(
                item["code"] == "evidence_join_ambiguous"
                for item in error_wires
            ),
            "false_done_count": sum(
                item["code"] == "false_done"
                for item in error_wires
            ),
            "unknown_observed_count": unknown_count,
            "unknown_replay_count": unknown_replay_count,
            "identity_admission_bypass_count": sum(
                item["code"] == "target_identity_mismatch"
                for item in error_wires
            ),
            "legacy_fallback_count": 0,
        },
        "disagreements": disagreements,
        "safety_abort_reasons": sorted(safety_abort_reasons),
        "passed": not error_wires,
        "errors": error_wires,
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


def _validate_error_identity(
    value: Any,
    *,
    name: str,
) -> dict[str, Any]:
    if (
        not isinstance(value, Mapping)
        or set(value) != {"count", "sha256"}
        or type(value.get("count")) is not int
        or type(value.get("count")) is bool
        or value["count"] < 0
    ):
        raise CampaignIntegrityError(f"{name} is not canonical")
    _require_sha(value.get("sha256"), f"{name} sha256")
    return deepcopy(dict(value))


def _validate_safe_external_oracle(
    value: Mapping[str, Any],
    *,
    manifest_sample: Mapping[str, Any],
    expected_attempt: int,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise CampaignIntegrityError(
            "safe external oracle must be an object"
        )
    expected_fields = {
        "action_observations",
        "case_id",
        "error_identity",
        "event_count",
        "event_stream_sha256",
        "event_type_counts",
        "format",
        "page_id_sha256",
        "passed",
        "scenario",
        "seed",
        "source",
        "success_event_count",
        "trusted_input_count",
        "wrong_input_count",
    }
    checked = deepcopy(dict(value))
    expected_case_id = (
        f"{int(manifest_sample['execution_order']):03d}-"
        f"{manifest_sample['case']}-{manifest_sample['runtime']}-"
        f"attempt-{expected_attempt}"
    )
    if (
        set(checked) != expected_fields
        or checked.get("format")
        != "vnext-live-canary-external-oracle-safe/1"
        or type(checked.get("passed")) is not bool
        or checked.get("scenario") != manifest_sample["scenario"]
        or checked.get("seed") != manifest_sample["seed"]
        or type(checked.get("source")) is not str
        or checked.get("source")
        not in {"", "independent_vnext_canary_browser_oracle"}
        or checked.get("case_id") != expected_case_id
    ):
        raise CampaignIntegrityError(
            "safe external oracle fields are not canonical"
        )
    if checked["passed"] is True and checked["source"].strip().casefold() in {
        "",
        "agent",
        "agent_claim",
        "runtime",
        "runtime_status",
    }:
        raise CampaignIntegrityError(
            "passing external oracle is not independent"
        )
    for name in (
        "event_count",
        "success_event_count",
        "trusted_input_count",
        "wrong_input_count",
    ):
        count = checked.get(name)
        if type(count) is not int or type(count) is bool or count < 0:
            raise CampaignIntegrityError(
                f"safe external oracle {name} is not canonical"
            )
    for name in ("event_stream_sha256", "page_id_sha256"):
        _require_sha(
            checked.get(name),
            f"safe external oracle {name}",
        )
    event_types = checked.get("event_type_counts")
    if (
        not isinstance(event_types, Mapping)
        or any(
            type(name) is not str
            or not name
            or type(count) is not int
            or type(count) is bool
            or count < 0
            for name, count in event_types.items()
        )
        or list(event_types) != sorted(event_types)
    ):
        raise CampaignIntegrityError(
            "safe external oracle event counts are not canonical"
        )
    observations = checked.get("action_observations")
    expected_observation_fields = {
        "action",
        "actual_changed",
        "after_state_sha256",
        "before_state_sha256",
        "decidable",
        "end_event_seq",
        "error_identity",
        "event_types",
        "external_action_index",
        "start_event_seq",
    }
    if not isinstance(observations, list):
        raise CampaignIntegrityError(
            "safe external oracle observations are not canonical"
        )
    for index, observation in enumerate(observations, start=1):
        if (
            not isinstance(observation, Mapping)
            or set(observation) != expected_observation_fields
            or observation.get("external_action_index") != index
            or type(observation.get("action")) is not str
            or type(observation.get("decidable")) is not bool
            or type(observation.get("actual_changed"))
            not in {bool, type(None)}
            or (
                observation.get("decidable") is True
                and type(observation.get("actual_changed")) is not bool
            )
            or (
                observation.get("decidable") is False
                and observation.get("actual_changed") is not None
            )
            or any(
                type(observation.get(name)) is not int
                or type(observation.get(name)) is bool
                or observation[name] < 0
                for name in ("end_event_seq", "start_event_seq")
            )
            or not isinstance(observation.get("event_types"), list)
            or any(
                type(item) is not str
                for item in observation["event_types"]
            )
        ):
            raise CampaignIntegrityError(
                "safe external oracle observation is not canonical"
            )
        for name in ("after_state_sha256", "before_state_sha256"):
            digest = observation.get(name)
            if observation["decidable"] is True or digest:
                _require_sha(
                    digest,
                    f"safe external oracle observation {name}",
                )
        observation_error = _validate_error_identity(
            observation.get("error_identity"),
            name="safe external oracle observation error identity",
        )
        if checked["passed"] is True and observation_error["count"] != 0:
            raise CampaignIntegrityError(
                "passing external oracle observation contains an error"
            )
    family = str(manifest_sample["family"])
    observed_actions = [item["action"] for item in observations]
    action_sequence_exact = (
        observed_actions == ["click", "double_click"]
        if family == "click_double"
        else (
            len(observed_actions) >= 2
            and all(item == "scroll" for item in observed_actions)
        )
        if family == "scroll_roundtrip"
        else observed_actions
        == ["click", "hotkey", "type_text", "hotkey"]
    )
    if checked["passed"] is True and (
        checked["success_event_count"] != 1
        or checked["wrong_input_count"] != 0
        or checked["event_count"] <= 0
        or checked["trusted_input_count"] <= 0
        or not action_sequence_exact
        or any(
            item["decidable"] is not True
            or item["actual_changed"] is not True
            for item in observations
        )
    ):
        raise CampaignIntegrityError(
            "passing external oracle lacks the exact independent action facts"
        )
    oracle_error = _validate_error_identity(
        checked.get("error_identity"),
        name="safe external oracle error identity",
    )
    if checked["passed"] is True and oracle_error["count"] != 0:
        raise CampaignIntegrityError(
            "passing external oracle contains an error"
        )
    return checked


def _validate_safe_runtime_evidence_anchor(
    value: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise CampaignIntegrityError(
            "safe Runtime evidence anchor must be an object"
        )
    expected_fields = {
        "journal_event_count",
        "journal_head_sha256",
        "mission_id",
        "missing_evidence_count",
        "no_legacy_fallback",
        "report_sha256",
        "report_status",
        "run_id",
        "runtime_version",
        "ts",
        "type",
        "verification_failure_count",
    }
    checked = deepcopy(dict(value))
    if (
        set(checked) != expected_fields
        or checked.get("type") != "runtime_evidence_sealed"
        or type(checked.get("ts")) not in {int, float}
        or type(checked.get("ts")) is bool
        or not math.isfinite(float(checked["ts"]))
        or type(checked.get("mission_id")) is not str
        or not checked["mission_id"]
        or type(checked.get("run_id")) is not str
        or not checked["run_id"]
        or checked.get("report_status")
        not in {"complete", "incomplete", "verification_failed"}
        or checked.get("runtime_version") != "vnext"
        or checked.get("no_legacy_fallback") is not True
    ):
        raise CampaignIntegrityError(
            "safe Runtime evidence anchor is not canonical"
        )
    for name in ("journal_head_sha256", "report_sha256"):
        _require_sha(
            checked.get(name),
            f"safe Runtime evidence anchor {name}",
        )
    for name in (
        "journal_event_count",
        "missing_evidence_count",
        "verification_failure_count",
    ):
        count = checked.get(name)
        if type(count) is not int or type(count) is bool or count < 0:
            raise CampaignIntegrityError(
                f"safe Runtime evidence anchor {name} is not canonical"
            )
    return checked


def _validate_safe_os_input_evidence(
    value: Mapping[str, Any],
) -> dict[str, Any]:
    expected_fields = {
        "action_counts",
        "decision_ids",
        "dropped_event_count",
        "event_sequences",
        "event_type_counts",
        "external_untrusted_mutation_count",
        "format",
        "hook_sequence",
        "input_release_audit",
        "raw_event_count",
        "raw_event_stream_sha256",
        "thread_error_present",
        "trusted_action_event_count",
        "trusted_action_event_stream_sha256",
        "unexpected_injected_target_mutation_count",
    }
    if not isinstance(value, Mapping):
        raise CampaignIntegrityError(
            "safe OS input evidence must be an object"
        )
    checked = deepcopy(dict(value))
    if (
        set(checked) != expected_fields
        or checked.get("format") != "vnext-live-canary-os-input-safe/1"
    ):
        raise CampaignIntegrityError(
            "safe OS input evidence fields are not canonical"
        )
    private = _private_evidence_field(checked)
    if private:
        raise CampaignIntegrityError(
            f"safe OS input evidence exposes private field {private!r}"
        )
    for name in (
        "raw_event_stream_sha256",
        "trusted_action_event_stream_sha256",
    ):
        _require_sha(checked.get(name), f"safe OS input evidence {name}")
    for name in (
        "hook_sequence",
        "raw_event_count",
        "trusted_action_event_count",
        "external_untrusted_mutation_count",
        "unexpected_injected_target_mutation_count",
        "dropped_event_count",
    ):
        count = checked.get(name)
        if type(count) is not int or type(count) is bool or count < 0:
            raise CampaignIntegrityError(
                f"safe OS input evidence {name} is not canonical"
            )
    if type(checked.get("thread_error_present")) is not bool:
        raise CampaignIntegrityError(
            "safe OS input evidence thread status is not canonical"
        )

    trusted_count = int(checked["trusted_action_event_count"])
    event_sequences = checked.get("event_sequences")
    if (
        type(event_sequences) is not list
        or any(
            type(item) is not int or type(item) is bool or item <= 0
            for item in event_sequences
        )
        or event_sequences != sorted(set(event_sequences))
        or len(event_sequences) != trusted_count
        or (
            event_sequences
            and checked["hook_sequence"] < event_sequences[-1]
        )
    ):
        raise CampaignIntegrityError(
            "safe OS input evidence event_sequences are not canonical"
        )

    def checked_counts(
        name: str,
        *,
        allowed: set[str],
        allow_unclassified: bool = False,
    ) -> dict[str, int]:
        raw = checked.get(name)
        if (
            not isinstance(raw, Mapping)
            or any(
                type(key) is not str
                or not key
                or key not in allowed
                or type(count) is not int
                or type(count) is bool
                or count <= 0
                for key, count in raw.items()
            )
        ):
            raise CampaignIntegrityError(
                f"safe OS input evidence {name} is not canonical"
            )
        counts = {str(key): int(count) for key, count in raw.items()}
        total = sum(counts.values())
        if total != trusted_count and not (
            allow_unclassified and total == 0
        ):
            raise CampaignIntegrityError(
                f"safe OS input evidence {name} does not cover trusted events"
            )
        return counts

    checked_counts(
        "event_type_counts",
        allowed={"pointerdown", "pointerup", "wheel", "keydown", "keyup"},
    )
    action_counts = checked_counts(
        "action_counts",
        allowed={"click", "double_click", "scroll", "type_text", "hotkey"},
        allow_unclassified=True,
    )
    decision_ids = checked.get("decision_ids")
    if (
        type(decision_ids) is not list
        or any(type(item) is not str or not item for item in decision_ids)
        or decision_ids != sorted(set(decision_ids))
        or len(decision_ids) > trusted_count
        or (action_counts and not decision_ids)
    ):
        raise CampaignIntegrityError(
            "safe OS input evidence decision_ids are not canonical"
        )
    classified_raw_count = (
        trusted_count
        + int(checked["external_untrusted_mutation_count"])
        + int(checked["unexpected_injected_target_mutation_count"])
    )
    if checked["raw_event_count"] < classified_raw_count:
        raise CampaignIntegrityError(
            "safe OS input evidence raw event count drops classified events"
        )

    release = checked.get("input_release_audit")
    if not isinstance(release, Mapping) or set(release) != {
        "passed",
        "unbalanced_key_count",
        "unbalanced_button_count",
        "error_identity",
    }:
        raise CampaignIntegrityError(
            "safe OS input evidence release audit is not canonical"
        )
    for name in ("unbalanced_key_count", "unbalanced_button_count"):
        count = release.get(name)
        if type(count) is not int or type(count) is bool or count < 0:
            raise CampaignIntegrityError(
                "safe OS input evidence release audit is not canonical"
            )
    error_identity = release.get("error_identity")
    if (
        not isinstance(error_identity, Mapping)
        or set(error_identity) != {"count", "sha256"}
        or type(error_identity.get("count")) is not int
        or type(error_identity.get("count")) is bool
        or error_identity["count"] < 0
    ):
        raise CampaignIntegrityError(
            "safe OS input evidence release error identity is not canonical"
        )
    _require_sha(
        error_identity.get("sha256"),
        "safe OS input evidence release error identity",
    )
    release_passed = bool(
        release["unbalanced_key_count"] == 0
        and release["unbalanced_button_count"] == 0
        and error_identity["count"] == 0
    )
    if (
        type(release.get("passed")) is not bool
        or release["passed"] is not release_passed
    ):
        raise CampaignIntegrityError(
            "safe OS input evidence release verdict is inconsistent"
        )
    return checked


def _validate_legacy_passing_os_input_evidence(
    value: Mapping[str, Any],
    *,
    family: str,
) -> None:
    trusted_count = int(value["trusted_action_event_count"])
    type_counts = dict(value["event_type_counts"])
    if (
        trusted_count <= 0
        or value["action_counts"]
        or value["decision_ids"]
    ):
        raise CampaignIntegrityError(
            "passing legacy OS input evidence is not independently classified"
        )
    pointer_down = int(type_counts.get("pointerdown") or 0)
    pointer_up = int(type_counts.get("pointerup") or 0)
    wheel = int(type_counts.get("wheel") or 0)
    key_down = int(type_counts.get("keydown") or 0)
    key_up = int(type_counts.get("keyup") or 0)
    if family == "click_double":
        valid = (
            pointer_down == 3
            and pointer_up == 3
            and wheel == key_down == key_up == 0
        )
    elif family == "scroll_roundtrip":
        valid = (
            wheel >= 2
            and pointer_down == pointer_up == key_down == key_up == 0
        )
    elif family == "type_hotkey":
        valid = (
            pointer_down == 1
            and pointer_up == 1
            and wheel == 0
            and key_down == key_up
            and key_down >= 5
        )
    else:
        valid = False
    if not valid:
        raise CampaignIntegrityError(
            "passing legacy OS input evidence does not match its action family"
        )


def _validate_invalid_run_evidence_artifacts(
    *,
    campaign_directory: Path,
    manifest: Mapping[str, Any],
    sample_key: str,
    terminal_data: Mapping[str, Any],
) -> None:
    samples = {
        sample["sample_key"]: sample
        for sample in manifest["samples"]
    }
    sample = samples.get(sample_key)
    if sample is None:
        raise CampaignIntegrityError(
            "invalid-run evidence references an unknown sample"
        )
    identities = [
        validate_artifact_identity(item)
        for item in terminal_data.get("artifacts") or []
    ]

    def one(kind: str) -> dict[str, Any]:
        matches = [
            identity
            for identity in identities
            if identity.get("kind") == kind
        ]
        if len(matches) != 1 or matches[0]["size"] <= 0:
            raise CampaignIntegrityError(
                f"invalid-run evidence requires one non-empty {kind} artifact"
            )
        return matches[0]

    invalid_identity = one(INVALID_RUN_EVIDENCE_KIND)
    external_identity = one("external_oracle")
    os_identity = one("os_input_evidence")
    invalid_wire = _decode_canonical_artifact(
        load_immutable_artifact(campaign_directory, invalid_identity),
        invalid_identity["path"],
    )
    external_wire = _decode_canonical_artifact(
        load_immutable_artifact(campaign_directory, external_identity),
        external_identity["path"],
    )
    os_wire = _validate_safe_os_input_evidence(
        _decode_canonical_artifact(
            load_immutable_artifact(campaign_directory, os_identity),
            os_identity["path"],
        )
    )
    _validate_safe_external_oracle(
        external_wire,
        manifest_sample=sample,
        expected_attempt=int(terminal_data.get("attempt") or 0),
    )
    invalid_fields = {
        "dropped_event_count",
        "external_oracle_sha256",
        "external_untrusted_mutation_count",
        "first_trusted_mutation_observed",
        "format",
        "os_input_evidence_sha256",
        "pre_mutation",
        "reason",
        "thread_error_present",
        "trusted_action_event_count",
    }
    if (
        set(invalid_wire) != invalid_fields
        or invalid_wire.get("format")
        != "vnext-live-canary-invalid-run-evidence/1"
        or invalid_wire.get("reason") != terminal_data.get("reason")
        or invalid_wire.get("pre_mutation") is not True
        or invalid_wire.get("first_trusted_mutation_observed") is not False
        or terminal_data.get("pre_mutation") is not True
        or terminal_data.get("external_evidence") is not True
        or terminal_data.get("first_trusted_mutation_observed") is not False
        or invalid_wire.get("external_oracle_sha256")
        != external_identity["sha256"]
        or invalid_wire.get("os_input_evidence_sha256")
        != os_identity["sha256"]
        or external_wire.get("passed") is not False
    ):
        raise CampaignIntegrityError(
            "invalid-run evidence content is not canonical"
        )
    if os_wire["trusted_action_event_count"] != 0:
        raise CampaignIntegrityError(
            "invalid-run OS evidence is not canonical pre-mutation evidence"
        )
    for name in (
        "dropped_event_count",
        "external_untrusted_mutation_count",
        "trusted_action_event_count",
    ):
        value = invalid_wire.get(name)
        if (
            type(value) is not int
            or type(value) is bool
            or value < 0
            or value != os_wire.get(name)
        ):
            raise CampaignIntegrityError(
                f"invalid-run evidence {name} is inconsistent"
            )
    if (
        type(invalid_wire.get("thread_error_present")) is not bool
        or invalid_wire["thread_error_present"]
        is not os_wire.get("thread_error_present")
    ):
        raise CampaignIntegrityError(
            "invalid-run evidence thread status is inconsistent"
        )


def _validate_safe_attempt_result(
    value: Mapping[str, Any],
    *,
    manifest_sample: Mapping[str, Any],
    model_config_sha256: str,
    state_attempt: Mapping[str, Any],
) -> dict[str, Any]:
    expected_fields = {
        "action_reconciliation",
        "attempt",
        "case",
        "cleanup",
        "client_projection",
        "config_audit",
        "execution_order",
        "external_oracle",
        "external_settlement",
        "failure_phase",
        "family",
        "finished_at",
        "first_trusted_mutation_observed",
        "format",
        "frozen_config_sha256",
        "invalid_reason",
        "observed_status",
        "oracle_collection_error_identity",
        "os_input_evidence",
        "passed",
        "runner_error_identity",
        "runtime_budget",
        "runtime_evidence_anchor",
        "runtime_evidence_error_identity",
        "runtime_version",
        "safety_abort_reasons",
        "sample_key",
        "scenario",
        "seed",
        "started_at",
        "status",
        "target_identity_audit",
        "target_source_audit",
        "trace_audit",
    }
    checked = deepcopy(dict(value))
    if (
        set(checked) != expected_fields
        or checked.get("format")
        != "vnext-live-canary-sample-attempt-safe/1"
    ):
        raise CampaignIntegrityError(
            "safe attempt result fields are not canonical"
        )
    private = _private_evidence_field(checked)
    if private:
        raise CampaignIntegrityError(
            f"safe attempt result exposes private evidence field {private!r}"
        )
    bindings = {
        "sample_key": manifest_sample["sample_key"],
        "execution_order": manifest_sample["execution_order"],
        "attempt": state_attempt["attempt"],
        "case": manifest_sample["case"],
        "family": manifest_sample["family"],
        "scenario": manifest_sample["scenario"],
        "seed": manifest_sample["seed"],
        "runtime_version": manifest_sample["runtime"],
        "runtime_budget": manifest_sample["runtime_budget"],
    }
    if any(checked.get(name) != expected for name, expected in bindings.items()):
        raise CampaignIntegrityError(
            "safe attempt result does not bind the manifest and journal attempt"
        )
    if checked.get("frozen_config_sha256") != model_config_sha256:
        raise CampaignIntegrityError(
            "safe attempt result does not bind the sealed model config"
        )
    status = checked.get("status")
    terminal_data = state_attempt.get("terminal_data")
    if (
        status not in {"passed", "failed", "invalid"}
        or status != state_attempt.get("status")
        or type(checked.get("observed_status")) is not str
        or not checked["observed_status"]
        or not isinstance(terminal_data, Mapping)
        or terminal_data.get("observed_status")
        != checked["observed_status"]
        or type(checked.get("passed")) is not bool
        or checked["passed"] is not (status == "passed")
    ):
        raise CampaignIntegrityError(
            "safe attempt result terminal verdict is inconsistent"
        )
    client = checked.get("client_projection")
    expected_client_fields = {
        "execution_valid",
        "no_legacy_fallback",
        "outcome",
        "passed",
        "run_id",
        "runtime_version",
        "status",
    }
    if (
        not isinstance(client, Mapping)
        or set(client) != expected_client_fields
        or type(client.get("execution_valid")) is not bool
        or type(client.get("passed")) is not bool
        or type(client.get("no_legacy_fallback")) is not bool
        or any(
            type(client.get(name)) is not str
            for name in ("outcome", "run_id", "runtime_version", "status")
        )
    ):
        raise CampaignIntegrityError(
            "safe attempt result client projection is not canonical"
        )
    external = _validate_safe_external_oracle(
        checked.get("external_oracle"),
        manifest_sample=manifest_sample,
        expected_attempt=int(state_attempt["attempt"]),
    )
    os_input = _validate_safe_os_input_evidence(
        checked.get("os_input_evidence")
    )
    completion_claimed = bool(
        client["execution_valid"] is True
        and client["outcome"] == "terminal"
        and client["status"] == "done"
    )
    if client["execution_valid"] is True and not completion_claimed:
        raise CampaignIntegrityError(
            "safe attempt result execution-valid completion is inconsistent"
        )
    if client["passed"] is not (
        completion_claimed and external["passed"] is True
    ):
        raise CampaignIntegrityError(
            "safe attempt result client verdict is self-inconsistent"
        )
    if status == "passed" and client["passed"] is not True:
        raise CampaignIntegrityError(
            "passing sample attempt lacks a passing client verdict"
        )
    config_audit = checked.get("config_audit")
    if (
        not isinstance(config_audit, Mapping)
        or (
            config_audit
            and (
                set(config_audit)
                != {
                    "adapter",
                    "api_max_retries",
                    "config_sha256",
                    "model",
                    "passed",
                    "provider",
                }
                or config_audit.get("passed") is not True
                or config_audit.get("config_sha256")
                != model_config_sha256
            )
        )
        or (completion_claimed and not config_audit)
    ):
        raise CampaignIntegrityError(
            "safe attempt result config audit is not canonical"
        )
    runtime = manifest_sample["runtime"]
    client_runtime = client["runtime_version"]
    if (
        client_runtime not in {"", runtime}
        or (completion_claimed and client_runtime != runtime)
        or (
            runtime == "legacy"
            and client["no_legacy_fallback"] is not False
        )
        or (
            runtime == "vnext"
            and client_runtime == "vnext"
            and client["no_legacy_fallback"] is not True
        )
        or (
            client_runtime == ""
            and client["no_legacy_fallback"] is not False
        )
    ):
        raise CampaignIntegrityError(
            "safe attempt result runtime fallback projection is inconsistent"
        )
    if runtime == "legacy" and client["passed"] is True:
        _validate_legacy_passing_os_input_evidence(
            os_input,
            family=str(manifest_sample["family"]),
        )
    safety_abort_reasons = checked.get("safety_abort_reasons")
    if (
        not isinstance(safety_abort_reasons, list)
        or safety_abort_reasons != sorted(set(safety_abort_reasons))
        or any(
            type(item) is not str
            or item not in DEFAULT_SAFETY_ABORT_CONDITIONS
            for item in safety_abort_reasons
        )
    ):
        raise CampaignIntegrityError(
            "safe attempt result safety reasons are not canonical"
        )
    expected_os_safety: set[str] = set()
    if (
        os_input["external_untrusted_mutation_count"] > 0
        or os_input["unexpected_injected_target_mutation_count"] > 0
    ):
        expected_os_safety.add("unauthorized_desktop_input")
    if os_input["dropped_event_count"] > 0:
        expected_os_safety.add("campaign_integrity_failure")
    if os_input["thread_error_present"] is True:
        expected_os_safety.add("test_control_irrecoverable")
    if os_input["input_release_audit"]["passed"] is False:
        expected_os_safety.add("input_release_failed")
    if not expected_os_safety.issubset(safety_abort_reasons):
        raise CampaignIntegrityError(
            "safe attempt result drops an OS evidence safety reason"
        )
    trace_audit = checked.get("trace_audit")
    if not isinstance(trace_audit, Mapping):
        raise CampaignIntegrityError(
            "safe attempt result trace audit is not canonical"
        )
    trace_safety = trace_audit.get("safety_abort_reasons", [])
    if (
        not isinstance(trace_safety, list)
        or any(item not in safety_abort_reasons for item in trace_safety)
    ):
        raise CampaignIntegrityError(
            "safe attempt result drops a trace safety reason"
        )
    reconciliation_value = checked.get("action_reconciliation")
    anchor_value = checked.get("runtime_evidence_anchor")
    if runtime == "vnext":
        reconciliation = (
            _validate_action_reconciliation(reconciliation_value)
            if isinstance(reconciliation_value, Mapping)
            else None
        )
        anchor = (
            _validate_safe_runtime_evidence_anchor(anchor_value)
            if isinstance(anchor_value, Mapping)
            else None
        )
        if reconciliation_value is not None and reconciliation is None:
            raise CampaignIntegrityError(
                "vNext safe attempt action reconciliation is invalid"
            )
        if anchor_value is not None and anchor is None:
            raise CampaignIntegrityError(
                "vNext safe attempt Runtime evidence anchor is invalid"
            )
        if reconciliation is not None and any(
            item not in safety_abort_reasons
            for item in reconciliation.get("safety_abort_reasons") or []
        ):
            raise CampaignIntegrityError(
                "safe attempt result drops a reconciliation safety reason"
            )
    else:
        if reconciliation_value is not None or anchor_value is not None:
            raise CampaignIntegrityError(
                "legacy safe attempt result contains vNext evidence"
            )
        reconciliation = None
        anchor = None
    return {
        "attempt": state_attempt["attempt"],
        "runtime_completion_claimed": completion_claimed,
        "client_run_id": client["run_id"],
        "embedded_external_oracle": external,
        "embedded_os_input_evidence": os_input,
        "embedded_action_reconciliation": reconciliation,
        "embedded_runtime_evidence_anchor": anchor,
        "safety_abort_reasons": safety_abort_reasons,
    }


def _os_input_reconciliation_projection(
    reconciliation: Mapping[str, Any],
) -> dict[str, Any]:
    actions = reconciliation.get("actions")
    if type(actions) is not list or not actions:
        raise CampaignIntegrityError(
            "action reconciliation lacks OS-bindable actions"
        )
    decision_ids: list[str] = []
    event_sequences: list[int] = []
    action_counts: dict[str, int] = {}
    for expected_order, action in enumerate(actions, start=1):
        if not isinstance(action, Mapping):
            raise CampaignIntegrityError(
                "action reconciliation OS binding contains a non-object"
            )
        action_id = action.get("action_id")
        decision_id = action.get("decision_id")
        action_name = action.get("action")
        trusted_count = action.get("trusted_os_event_count")
        sequences = action.get("os_event_sequences")
        if (
            type(action_id) is not str
            or not action_id
            or decision_id != action_id
            or type(action_name) is not str
            or action_name
            not in {"click", "double_click", "scroll", "type_text", "hotkey"}
            or action.get("order") != expected_order
            or type(trusted_count) is not int
            or type(trusted_count) is bool
            or trusted_count <= 0
            or type(sequences) is not list
            or any(
                type(item) is not int or type(item) is bool or item <= 0
                for item in sequences
            )
            or sequences != sorted(set(sequences))
            or len(sequences) != trusted_count
        ):
            raise CampaignIntegrityError(
                "action reconciliation OS binding facts are not canonical"
            )
        decision_ids.append(action_id)
        event_sequences.extend(sequences)
        action_counts[action_name] = (
            action_counts.get(action_name, 0) + trusted_count
        )
    if (
        len(decision_ids) != len(set(decision_ids))
        or event_sequences != sorted(set(event_sequences))
    ):
        raise CampaignIntegrityError(
            "action reconciliation reuses an OS evidence identity"
        )
    return {
        "decision_ids": sorted(decision_ids),
        "event_sequences": event_sequences,
        "trusted_action_event_count": len(event_sequences),
        "action_counts": {
            name: action_counts[name] for name in sorted(action_counts)
        },
    }


def _sample_report(
    manifest_sample: Mapping[str, Any],
    state_sample: Mapping[str, Any],
    *,
    abort_event: Mapping[str, Any] | None,
    campaign_directory: Path | None,
    model_config_sha256: str,
) -> tuple[dict[str, Any], dict[str, int]]:
    verdict = state_sample["status"]
    report_status = verdict
    errors: list[str] = []
    artifact_paths: set[str] = set()
    reconciliations: list[dict[str, Any]] = []
    reconciliations_by_attempt: dict[int, list[dict[str, Any]]] = {}
    attempt_verdicts: list[dict[str, Any]] = []
    accepted_attempt_results: list[dict[str, Any]] = []
    attempt_results_by_attempt: dict[int, list[dict[str, Any]]] = {}
    external_oracles_by_attempt: dict[int, list[dict[str, Any]]] = {}
    os_inputs_by_attempt: dict[int, list[dict[str, Any]]] = {}
    runtime_anchors_by_attempt: dict[int, list[dict[str, Any]]] = {}
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
                    reconciliation = _validate_action_reconciliation(value)
                    reconciliations.append(reconciliation)
                    reconciliations_by_attempt.setdefault(
                        int(attempt["attempt"]),
                        [],
                    ).append(reconciliation)
                elif checked.get("kind") == "attempt_result":
                    value = _decode_canonical_artifact(data, checked["path"])
                    attempt_result = _validate_safe_attempt_result(
                        value,
                        manifest_sample=manifest_sample,
                        model_config_sha256=model_config_sha256,
                        state_attempt=attempt,
                    )
                    attempt_results_by_attempt.setdefault(
                        int(attempt["attempt"]),
                        [],
                    ).append(attempt_result)
                elif checked.get("kind") == "external_oracle":
                    value = _decode_canonical_artifact(data, checked["path"])
                    external_oracle = _validate_safe_external_oracle(
                        value,
                        manifest_sample=manifest_sample,
                        expected_attempt=int(attempt["attempt"]),
                    )
                    external_oracles_by_attempt.setdefault(
                        int(attempt["attempt"]),
                        [],
                    ).append(external_oracle)
                elif checked.get("kind") == "os_input_evidence":
                    value = _decode_canonical_artifact(data, checked["path"])
                    os_input = _validate_safe_os_input_evidence(value)
                    os_inputs_by_attempt.setdefault(
                        int(attempt["attempt"]),
                        [],
                    ).append(os_input)
                elif checked.get("kind") == "runtime_evidence_anchor":
                    value = _decode_canonical_artifact(data, checked["path"])
                    runtime_anchor = (
                        _validate_safe_runtime_evidence_anchor(value)
                    )
                    runtime_anchors_by_attempt.setdefault(
                        int(attempt["attempt"]),
                        [],
                    ).append(runtime_anchor)
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

    terminal_attempts = [
        item
        for item in attempts
        if item.get("status") in {"passed", "failed", "invalid"}
    ]
    for attempt in terminal_attempts:
        number = int(attempt["attempt"])
        attempt_results = attempt_results_by_attempt.get(number, [])
        external_oracles = external_oracles_by_attempt.get(number, [])
        os_inputs = os_inputs_by_attempt.get(number, [])
        if len(attempt_results) != 1:
            errors.append("missing_or_duplicate_attempt_result")
        else:
            accepted_attempt_results.append(attempt_results[0])
        if len(external_oracles) != 1:
            errors.append("missing_or_duplicate_external_oracle")
        if len(os_inputs) != 1:
            errors.append("missing_or_duplicate_os_input_evidence")
        if len(attempt_results) == 1 and len(external_oracles) == 1:
            attempt_result = attempt_results[0]
            external_oracle = external_oracles[0]
            if canonical_json_bytes(
                attempt_result["embedded_external_oracle"]
            ) != canonical_json_bytes(external_oracle):
                errors.append("attempt_external_oracle_mismatch")
            if len(os_inputs) == 1:
                if canonical_json_bytes(
                    attempt_result["embedded_os_input_evidence"]
                ) != canonical_json_bytes(os_inputs[0]):
                    errors.append("attempt_os_input_evidence_mismatch")
            completion_claimed = bool(
                attempt_result["runtime_completion_claimed"]
            )
            false_done = (
                completion_claimed
                and external_oracle["passed"] is False
            )
            attempt_verdicts.append(
                {
                    "attempt": number,
                    "runtime_completion_claimed": completion_claimed,
                    "external_oracle_passed": external_oracle["passed"],
                    "false_done": false_done,
                }
            )
    if manifest_sample["runtime"] == "vnext" and terminal_attempts:
        for attempt in terminal_attempts:
            number = int(attempt["attempt"])
            if attempt.get("status") == "invalid":
                continue
            attempt_reconciliations = reconciliations_by_attempt.get(
                number,
                [],
            )
            if len(attempt_reconciliations) != 1:
                errors.append("missing_or_duplicate_action_reconciliation")
                continue
            reconciliation = attempt_reconciliations[0]
            runtime_anchors = runtime_anchors_by_attempt.get(number, [])
            if len(runtime_anchors) != 1:
                errors.append("missing_or_duplicate_runtime_evidence_anchor")
                continue
            runtime_anchor = runtime_anchors[0]
            if (
                reconciliation.get("schema_version")
                != ACTION_RECONCILIATION_SCHEMA_VERSION
                or reconciliation.get("passed") is not True
            ):
                errors.append("action_reconciliation_failed")
            attempt_results = attempt_results_by_attempt.get(number, [])
            external_oracles = external_oracles_by_attempt.get(number, [])
            os_inputs = os_inputs_by_attempt.get(number, [])
            if (
                len(attempt_results) != 1
                or len(external_oracles) != 1
                or len(os_inputs) != 1
            ):
                continue
            attempt_result = attempt_results[0]
            external_oracle = external_oracles[0]
            os_input = os_inputs[0]
            embedded_reconciliation = attempt_result[
                "embedded_action_reconciliation"
            ]
            embedded_anchor = attempt_result[
                "embedded_runtime_evidence_anchor"
            ]
            if (
                embedded_reconciliation is None
                or canonical_json_bytes(embedded_reconciliation)
                != canonical_json_bytes(reconciliation)
            ):
                errors.append("attempt_action_reconciliation_mismatch")
            if (
                embedded_anchor is None
                or canonical_json_bytes(embedded_anchor)
                != canonical_json_bytes(runtime_anchor)
            ):
                errors.append("attempt_runtime_evidence_anchor_mismatch")
            try:
                os_projection = _os_input_reconciliation_projection(
                    reconciliation
                )
            except CampaignStateError:
                errors.append("os_input_reconciliation_binding_invalid")
            else:
                if any(
                    os_input.get(name) != expected
                    for name, expected in os_projection.items()
                ):
                    errors.append("os_input_reconciliation_binding_mismatch")
            normalized_anchor = {
                name: value
                for name, value in runtime_anchor.items()
                if name not in {"ts", "type"}
            }
            completion_claimed = bool(
                attempt_result["runtime_completion_claimed"]
            )
            false_done = (
                completion_claimed
                and external_oracle["passed"] is False
            )
            if (
                reconciliation.get("run_id")
                != attempt_result["client_run_id"]
                or reconciliation.get("external_oracle_passed")
                is not external_oracle["passed"]
                or (
                    reconciliation.get("runtime_terminal_status")
                    == "mission_verified"
                )
                is not completion_claimed
                or reconciliation.get("runtime_evidence")
                != normalized_anchor
            ):
                errors.append("action_reconciliation_attempt_binding_mismatch")
            if int(false_done) != int(
                (reconciliation.get("summary") or {}).get(
                    "false_done_count"
                )
                or 0
            ):
                errors.append("action_reconciliation_false_done_mismatch")
    elif manifest_sample["runtime"] == "legacy":
        if reconciliations:
            errors.append("legacy_contains_action_reconciliation")
        if runtime_anchors_by_attempt:
            errors.append("legacy_contains_runtime_evidence_anchor")

    if errors and report_status == "passed":
        report_status = "failed"
    summary = {
        "false_done_count": sum(
            int(item["false_done"]) for item in attempt_verdicts
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
            "runtime_legacy_fallback" in item["safety_abort_reasons"]
            for item in accepted_attempt_results
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
        "attempt_verdicts": attempt_verdicts,
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
            model_config_sha256=manifest_wire["model_config"][
                "config_sha256"
            ],
        )
        sample_reports.append(report)
        for name in totals:
            totals[name] += summary[name]
    abort = state.get("abort_event") or {}
    abort_reasons = {
        str(abort.get("reason") or ""),
        *{
            str(item)
            for item in (abort.get("data") or {}).get(
                "observed_reasons",
                [],
            )
        },
    }
    if "runtime_legacy_fallback" in abort_reasons:
        totals["legacy_fallback_count"] = max(
            1,
            totals["legacy_fallback_count"],
        )
    if "identity_admission_bypass" in abort_reasons:
        totals["identity_admission_bypass_count"] = max(
            1,
            totals["identity_admission_bypass_count"],
        )
    if "unknown_mutation_replay" in abort_reasons:
        totals["unknown_replay_count"] = max(
            1,
            totals["unknown_replay_count"],
        )

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
        and state["campaign_finished"] is True
    )
    campaign_errors = (
        []
        if state["campaign_finished"]
        else ["campaign_finished_missing"]
    )
    payload = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "campaign_id": manifest_wire["campaign_id"],
        "manifest_content_sha256": manifest_wire[CONTENT_HASH_FIELD],
        "journal_event_count": state["event_count"],
        "journal_head_event_hash": state["head_event_hash"],
        "campaign_state": state["campaign_status"],
        "passed": all_passed,
        "campaign_errors": campaign_errors,
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
    """Validate report structure; only root-backed load/seal authenticates it."""

    value = validate_content_hash(report)
    if value.get("schema_version") != REPORT_SCHEMA_VERSION:
        raise CampaignIntegrityError("unsupported campaign report schema")
    samples = value.get("samples")
    if not isinstance(samples, list) or len(samples) != REQUIRED_SAMPLE_COUNT:
        raise CampaignIntegrityError("campaign report does not contain 12 samples")
    keys = [item.get("sample_key") for item in samples if isinstance(item, dict)]
    if len(keys) != REQUIRED_SAMPLE_COUNT or len(keys) != len(set(keys)):
        raise CampaignIntegrityError("campaign report sample keys are incomplete")
    campaign_errors = value.get("campaign_errors")
    if (
        type(campaign_errors) is not list
        or campaign_errors
        not in ([], ["campaign_finished_missing"])
    ):
        raise CampaignIntegrityError("campaign report errors are not canonical")
    return value


def _private_evidence_field(value: Any) -> str:
    forbidden = {
        "actor_envelope",
        "broker_projection",
        "canonical_arguments",
        "controller_transition",
        "full_model_response",
        "model_response",
        "physical_arguments",
        "prompt",
        "raw_action_receipt",
        "reasoning",
        "submitted_arguments",
        "tool_schema",
    }
    if isinstance(value, Mapping):
        for key, item in value.items():
            if str(key) in forbidden:
                return str(key)
            nested = _private_evidence_field(item)
            if nested:
                return nested
    elif isinstance(value, list):
        for item in value:
            nested = _private_evidence_field(item)
            if nested:
                return nested
    return ""


def _validate_action_reconciliation(
    value: Mapping[str, Any],
) -> dict[str, Any]:
    checked = validate_content_hash(value)
    expected_fields = {
        "schema_version",
        "run_id",
        "obligation_id",
        "runtime_terminal_status",
        "external_oracle_passed",
        "target_identity_sha256",
        "runtime_evidence",
        "actions",
        "summary",
        "disagreements",
        "safety_abort_reasons",
        "passed",
        "errors",
        CONTENT_HASH_FIELD,
    }
    if set(checked) != expected_fields:
        raise CampaignIntegrityError(
            "action reconciliation fields are not canonical"
        )
    if (
        checked.get("schema_version")
        != ACTION_RECONCILIATION_SCHEMA_VERSION
    ):
        raise CampaignIntegrityError(
            "unsupported action reconciliation schema"
        )
    runtime_terminal_status = checked.get("runtime_terminal_status")
    external_oracle_passed = checked.get("external_oracle_passed")
    if (
        type(runtime_terminal_status) is not str
        or not runtime_terminal_status
        or type(external_oracle_passed) is not bool
    ):
        raise CampaignIntegrityError(
            "action reconciliation terminal facts are not canonical"
        )
    _require_sha(
        checked.get("target_identity_sha256"),
        "action reconciliation target identity",
    )
    private = _private_evidence_field(checked)
    if private:
        raise CampaignIntegrityError(
            f"action reconciliation exposes private evidence field {private!r}"
        )
    actions = checked.get("actions")
    errors = checked.get("errors")
    summary = checked.get("summary")
    anchor = checked.get("runtime_evidence")
    if (
        type(actions) is not list
        or not actions
        or type(errors) is not list
        or not isinstance(summary, Mapping)
        or not isinstance(anchor, Mapping)
    ):
        raise CampaignIntegrityError(
            "action reconciliation facts are incomplete"
        )
    expected_anchor_fields = {
        "mission_id",
        "run_id",
        "journal_head_sha256",
        "journal_event_count",
        "report_sha256",
        "report_status",
        "missing_evidence_count",
        "verification_failure_count",
        "runtime_version",
        "no_legacy_fallback",
    }
    if (
        set(anchor) != expected_anchor_fields
        or anchor.get("runtime_version") != "vnext"
        or anchor.get("no_legacy_fallback") is not True
        or anchor.get("run_id") != checked.get("run_id")
        or anchor.get("report_status") not in {"complete", "incomplete"}
    ):
        raise CampaignIntegrityError(
            "action reconciliation Runtime evidence anchor is not canonical"
        )
    for name in ("journal_head_sha256", "report_sha256"):
        _require_sha(anchor.get(name), f"Runtime evidence anchor {name}")
    for name in (
        "journal_event_count",
        "missing_evidence_count",
        "verification_failure_count",
    ):
        count = anchor.get(name)
        if type(count) is not int or type(count) is bool or count < 0:
            raise CampaignIntegrityError(
                f"Runtime evidence anchor {name} is not canonical"
            )
    action_ids = [
        item.get("action_id")
        for item in actions
        if isinstance(item, Mapping)
    ]
    if (
        len(action_ids) != len(actions)
        or any(type(item) is not str or not item for item in action_ids)
        or len(action_ids) != len(set(action_ids))
    ):
        raise CampaignIntegrityError(
            "action reconciliation action IDs are missing or reused"
        )
    if any(
        not isinstance(item, Mapping)
        or set(item) != {"action_id", "code"}
        or type(item.get("code")) is not str
        or type(item.get("action_id")) is not str
        for item in errors
    ):
        raise CampaignIntegrityError(
            "action reconciliation errors are not canonical"
        )
    false_done_errors = [
        item
        for item in errors
        if item.get("code") == "false_done"
    ]
    false_done_expected = (
        runtime_terminal_status == "mission_verified"
        and external_oracle_passed is False
    )
    if (
        len(false_done_errors) != int(false_done_expected)
        or (
            false_done_errors
            and false_done_errors[0].get("action_id") != ""
        )
    ):
        raise CampaignIntegrityError(
            "action reconciliation false-done fact is inconsistent"
        )
    expected_summary = {
        "action_count": len(actions),
        "action_effect_disagreement_count": sum(
            item.get("action_effect_disagreement") is True
            for item in actions
        ),
        "evidence_join_ambiguous_count": sum(
            item.get("code") == "evidence_join_ambiguous"
            for item in errors
        ),
        "false_done_count": sum(
            item.get("code") == "false_done" for item in errors
        ),
        "unknown_observed_count": sum(
            str(item.get("receipt_outcome") or "").casefold()
            in {"outcome_unknown", "unknown"}
            for item in actions
        ),
        "unknown_replay_count": sum(
            str(item.get("receipt_outcome") or "").casefold()
            in {"outcome_unknown", "unknown"}
            and item.get("replay_detected") is True
            for item in actions
        ),
        "identity_admission_bypass_count": sum(
            item.get("code") == "target_identity_mismatch"
            for item in errors
        ),
        "legacy_fallback_count": sum(
            item.get("code") == "runtime_legacy_fallback"
            for item in errors
        ),
    }
    if dict(summary) != expected_summary:
        raise CampaignIntegrityError(
            "action reconciliation summary is self-inconsistent"
        )
    disagreements = checked.get("disagreements")
    if (
        type(disagreements) is not list
        or len(disagreements)
        != expected_summary["action_effect_disagreement_count"]
        or {
            item.get("action_id")
            for item in disagreements
            if isinstance(item, Mapping)
        }
        != {
            item.get("action_id")
            for item in actions
            if item.get("action_effect_disagreement") is True
        }
    ):
        raise CampaignIntegrityError(
            "action reconciliation disagreements are self-inconsistent"
        )
    expected_safety_reasons = {
        {
            "target_identity_mismatch": "identity_admission_bypass",
            "runtime_legacy_fallback": "runtime_legacy_fallback",
            "unauthorized_desktop_input": "unauthorized_desktop_input",
            "unknown_mutation_replay": "unknown_mutation_replay",
        }[item["code"]]
        for item in errors
        if item["code"]
        in {
            "target_identity_mismatch",
            "runtime_legacy_fallback",
            "unauthorized_desktop_input",
            "unknown_mutation_replay",
        }
    }
    if checked.get("safety_abort_reasons") != sorted(
        expected_safety_reasons
    ):
        raise CampaignIntegrityError(
            "action reconciliation safety verdict is self-inconsistent"
        )
    if checked.get("passed") is not (not errors):
        raise CampaignIntegrityError(
            "action reconciliation verdict is self-inconsistent"
        )
    if (
        checked.get("passed") is True
        and anchor.get("report_status") != "complete"
    ):
        raise CampaignIntegrityError(
            "passing reconciliation lacks complete Runtime evidence"
        )
    return checked


def _derive_report_from_sealed_root(root: Path) -> dict[str, Any]:
    if not root.is_dir() or root.is_symlink():
        raise CampaignIntegrityError("campaign report directory is unavailable")
    manifest = load_manifest(root)
    events = load_journal_events(root / JOURNAL_FILENAME, manifest)
    return derive_campaign_report(
        manifest,
        events,
        campaign_directory=root,
    )


def seal_campaign_report(
    campaign_directory: str | os.PathLike[str],
    report: Mapping[str, Any],
) -> dict[str, Any]:
    """Create the report once; an identical existing report is a read-only no-op."""

    root = Path(campaign_directory)
    if not root.is_dir() or root.is_symlink():
        raise CampaignIntegrityError("campaign report directory is unavailable")
    value = validate_campaign_report(report)
    independently_derived = _derive_report_from_sealed_root(root)
    if canonical_json_bytes(value) != canonical_json_bytes(
        independently_derived
    ):
        raise CampaignIntegrityError(
            "campaign report differs from sealed manifest, journal, or artifacts"
        )
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
    root = path if path.is_dir() else path.parent
    if path.is_dir():
        path = path / REPORT_FILENAME
    value = validate_campaign_report(_read_canonical_object(path))
    independently_derived = _derive_report_from_sealed_root(root)
    if canonical_json_bytes(value) != canonical_json_bytes(
        independently_derived
    ):
        raise CampaignIntegrityError(
            "stored campaign report is not the independently derived verdict"
        )
    return value


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
    "INVALID_RUN_EVIDENCE_KIND",
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
    "RUNTIME_EVIDENCE_REPORT_FORMAT",
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
