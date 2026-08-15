"""WinPilot configuration: load/save config.json + runtime toggle state.

All runtime-mutable settings (perception toggles, priorities, model choice,
agent options) live here as a single source of truth. The web UI reads and
writes through this module via the server API.
"""
from __future__ import annotations

import json
import threading
from copy import deepcopy
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = ROOT_DIR.parents[1]
CONFIG_PATH = ROOT_DIR / "config.json"
TRACES_DIR = ROOT_DIR / "traces"
SCRIPTS_DIR = ROOT_DIR / "scripts"
TEMPLATES_IMG_DIR = ROOT_DIR / "templates_img"
BIN_DIR = ROOT_DIR / "winpilot" / "bin"
MEMORY_PATH = ROOT_DIR / "experience_memory.json"
MEMORY_DB_PATH = ROOT_DIR / "memory.db"

_DEFAULTS: dict[str, Any] = {
    "api": {
        "provider": "auto",
        "base_url": "https://api.deepseek.com",
        "api_key": "",
        "model": "deepseek-v4-flash",
        "temperature": 0.3,
        "max_tokens": 4096,
        "thinking": False,
        # Keep one provider request bounded.  The runner may retry a failed
        # request, but a single stalled decision must not hold the desktop
        # loop indefinitely.
        "request_timeout_s": 90,
        "request_total_timeout_s": 180,
        "max_retries": 2,
        "pricing": {
            "provider": "deepseek",
            "currency": "CNY",
            "input_per_m": 1.0,
            "cache_hit_input_per_m": 0.02,
            "output_per_m": 2.0,
        },
    },
    "vlm": {"enabled": False, "base_url": "", "api_key": "", "model": "",
            "thinking": False},
    "perception": {
        # Accessibility and OCR are optional semantic probes.  Native visual
        # execution must opt into them instead of inheriting them as ambient
        # truth for every task.
        "uia": {"enabled": False, "priority": 1},
        "win32": {"enabled": True, "priority": 2},
        "ocr": {"enabled": False, "priority": 3},
        "cv": {"enabled": True, "priority": 4},
        "vlm": {"enabled": False, "priority": 5},
    },
    "fusion": {"escalate_on_empty": False, "ocr_skip_when_uia_rich": True,
               "vlm_skip_when_uia_rich": True, "uia_rich_threshold": 20,
               "vlm_assist_threshold": 60, "observe_cache_ttl_s": 2.0,
               # When the main model can consume images, the screenshot is the
               # authoritative observation for pixel-rendered interfaces.  Do
               # not spend seconds serialising the same frame through
               # UIA/OCR/VLM unless a query explicitly asks for text grounding.
               "native_vision_direct": True,
               "screen_watch_autostart": True,
               "predictive_ocr_enabled": False,
               "predictive_ocr_prefer_cache": True,
               "incremental_perception_enabled": True,
               "incremental_perception_debounce_ms": 80,
               "incremental_perception_max_crops": 4,
               # Prefetch is opportunistic.  The foreground visual loop must
               # never wait seconds for background OCR; late results are
               # dropped and an explicit query can take the full path.
               "incremental_perception_wait_ms": 250,
               "incremental_perception_min_ratio": 0.00015,
               "incremental_perception_max_ratio": 0.35,
               "press_probe_settle_ms": 18,
               "press_release_settle_ms": 24},
    "agent": {
        "max_steps": 40,
        "max_retries": 2,
        "dry_run": False,
        "experience_memory": True,
        "shell_enabled": True,
        "world_model": True,
        "replan_on_fail": True,
        "planning_enabled": True,
        # Keep app/domain recipes opt-in.  The compact visual contract is the
        # default; matching a task keyword must not silently expand it again.
        "playbooks_enabled": False,
        "prompt_style": "compact",
        "tool_profile": "auto",
        "session_memory": True,
        "verify_done": True,
        "done_max_rejects": 2,
        "escalation_model": "",
        "llm_trace": True,
        "reflect_interval": 8,
    },
    "controller": {
        "physical_anchor_clicks": True,
        # Use provider-native reasoning for native visual decisions on known
        # compatible providers.  ``adaptive`` spends that budget on the first
        # visual read and on recovery/uncertainty, then leaves routine turns
        # lightweight.  It is a runtime policy, not a task or app recipe.
        "native_visual_thinking": True,
        "native_visual_thinking_mode": "adaptive",
        # Screen-first frames.  The model sees the real virtual desktop by
        # default so it can react to windows it never bound; a window crop is
        # an explicit opt-in and is still used only for a validated,
        # foreground, single-window target.
        "target_window_capture_enabled": False,
        # A model turn may commit one visual transaction.  The resulting real
        # frame is returned before another transaction is accepted.
        "visual_closed_loop": True,
        # Cancel low-level input when target-window pixels changed materially
        # while the model was deciding from the prior frame.
        "visual_preflight_change_ratio": 0.0025,
        # Prefer stability at the grounded action target so unrelated animated
        # pixels cannot livelock an otherwise safe visual transaction.
        "visual_preflight_target_change_ratio": 0.01,
        "visual_preflight_target_margin": 60,
        "visual_servo_mode": "adaptive",
        "visual_servo_recent_change_ms": 900,
        "visual_servo_strict": True,
        # Accessibility/OCR may be requested as diagnostics, but they must not
        # silently choose or move a target selected from authoritative pixels.
        "semantic_grounding_assist": False,
        "coordinate_semantic_snap": False,
    },
    "ocr": {
        "use_gpu": True,
        "min_confidence": 0.55,
        "warm_on_startup": False,
    },
    "search": {"everything_es_path": "", "everything_exe_path": ""},
    "coding": {"enabled": True, "workspace": "", "max_steps": 30},
    "integrations": {
        "chatgpt_desktop": {
            "enabled": True,
            "allow_desktop_mutation": True,
        },
    },
    "vnext": {
        "live_agent_enabled": True,
        # Generous real-task budgets: slow visual providers and multi-step
        # ordinary desktop tasks must not be cut off by a tight wall clock or
        # turn cap.  Completion quality beats bounded-run accounting.
        "max_turns": 200,
        "max_runtime_s": 7200,
        "max_model_calls": 400,
    },
    "server": {"host": "127.0.0.1", "port": 8765},
}

_lock = threading.Lock()


def _merge(base: dict, override: dict) -> dict:
    """Deep-merge override into a copy of base (immutable)."""
    result = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


def _canonicalize_live_agent_config(raw: dict[str, Any]) -> dict[str, Any]:
    """Migrate the retired live-agent key at the config input boundary."""
    normalized = deepcopy(raw)
    vnext = normalized.get("vnext")
    if not isinstance(vnext, dict):
        return normalized

    legacy_key = "live_canary_enabled"
    canonical_key = "live_agent_enabled"
    if legacy_key not in vnext:
        return normalized
    if canonical_key in vnext:
        canonical_value = vnext[canonical_key]
        legacy_value = vnext[legacy_key]
        same_value = (
            type(canonical_value) is type(legacy_value)
            and canonical_value == legacy_value
        )
        if not same_value:
            raise ValueError(
                "config.vnext.live_agent_enabled conflicts with the retired "
                "config.vnext.live_canary_enabled value"
            )
    vnext.setdefault(canonical_key, vnext[legacy_key])
    del vnext[legacy_key]
    return normalized


class Config:
    """Thread-safe config holder backed by config.json."""

    def __init__(self) -> None:
        self._data: dict[str, Any] = deepcopy(_DEFAULTS)
        self.reload()

    def reload(self) -> None:
        if CONFIG_PATH.exists():
            with _lock:  # read+merge inside the lock: no lost-update vs update()
                try:
                    on_disk = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError) as exc:
                    raise RuntimeError(f"config.json 解析失败: {exc}") from exc
                on_disk = _canonicalize_live_agent_config(on_disk)
                self._data = _merge(_DEFAULTS, on_disk)
        else:
            self.save()

    def save(self) -> None:
        with _lock:
            temporary = CONFIG_PATH.with_suffix(CONFIG_PATH.suffix + ".tmp")
            temporary.write_text(
                json.dumps(self._data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            temporary.replace(CONFIG_PATH)

    def get(self, *path: str, default: Any = None) -> Any:
        with _lock:
            node: Any = self._data
            for key in path:
                if not isinstance(node, dict) or key not in node:
                    return default
                node = node[key]
            return deepcopy(node)

    def set(self, value: Any, *path: str) -> None:
        if not path:
            raise ValueError("set() 需要至少一级路径")
        with _lock:
            node = self._data
            for key in path[:-1]:
                node = node.setdefault(key, {})
            node[path[-1]] = deepcopy(value)
        self.save()

    def snapshot(self) -> dict[str, Any]:
        with _lock:
            return deepcopy(self._data)

    def update(self, patch: dict[str, Any]) -> dict[str, Any]:
        """Deep-merge a patch dict (from the web UI) and persist."""
        patch = _canonicalize_live_agent_config(patch)
        with _lock:
            self._data = _merge(self._data, patch)
        self.save()
        return self.snapshot()

    # -- convenience accessors -------------------------------------------
    def enabled_dimensions(self) -> list[str]:
        """Perception dimension names, enabled only, sorted by priority."""
        dims = self.get("perception", default={})
        enabled = [
            (name, opts.get("priority", 99))
            for name, opts in dims.items()
            if opts.get("enabled")
        ]
        return [name for name, _ in sorted(enabled, key=lambda item: item[1])]


CONFIG = Config()
