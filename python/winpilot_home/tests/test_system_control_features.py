from __future__ import annotations

import builtins
import sys
from pathlib import Path


PYTHON_ROOT = Path(__file__).resolve().parents[2]
if str(PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(PYTHON_ROOT))


def _without_pycaw(monkeypatch):
    original_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name == "pycaw.pycaw" or name.startswith("pycaw."):
            raise ImportError("pycaw intentionally unavailable")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)


def test_volume_set_fails_closed_when_exact_backend_is_missing(
    monkeypatch,
):
    from features import system_control_features

    _without_pycaw(monkeypatch)

    assert system_control_features.volume_set(5) is False


def test_volume_mute_does_not_claim_unknown_fallback_state(
    monkeypatch,
):
    from features import system_control_features

    _without_pycaw(monkeypatch)

    result = system_control_features.volume_mute(False)

    assert result["success"] is False
    assert "pycaw" in result["error"]
