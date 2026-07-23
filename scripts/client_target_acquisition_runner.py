"""Thin visible-client runner for external target-acquisition fixtures."""
from __future__ import annotations

import ctypes
import hashlib
import json
import os
import uuid
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from client_target_acquisition_e2e import target_acquisition_verdict
from client_target_acquisition_fixture import (
    AcquisitionFixture,
    FixtureUnavailable,
)


def _fingerprint(value: dict[str, Any]) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _interactive_desktop() -> bool:
    return bool(
        os.name == "nt"
        and int(ctypes.windll.user32.GetForegroundWindow() or 0) > 0
    )


def run_target_acquisition_case(
    name: str,
    source: dict[str, Any],
    *,
    output_root: Path,
    group_id: str | None,
    pair_id: str | None,
    client_harness: Any,
    evidence_projector: Callable[[dict[str, Any]], dict[str, Any]],
) -> dict[str, Any]:
    seed, mode = int(source["seed"]), str(source["mode"])
    case_id = f"client-{name}-{uuid.uuid4().hex[:10]}"
    case_dir = output_root / case_id
    case_dir.mkdir(parents=True, exist_ok=True)
    result: dict[str, Any] = {
        "name": name, "case_id": case_id, "scenario": "target_acquisition",
        "seed": seed, "runtime_version": "vnext",
        "group_id": group_id or f"{name}:seed-{seed}", "pair_id": pair_id,
        "entrypoint": "electron_client_windows_uia",
        "cold_start_required": True, "target_application_mode": mode,
        "ground_truth": "independent_windows_process_window_and_launch_facts",
        "started_at": datetime.now().astimezone().isoformat(),
    }
    fixture: AcquisitionFixture | None = None
    try:
        if not _interactive_desktop():
            raise FixtureUnavailable(
                "an unlocked interactive Windows desktop is required",
            )
        fixture = AcquisitionFixture.create(seed)
        baseline = fixture.prepare(mode)
        task = str(source["task_template"]).format(
            public_name=fixture.public_name,
        )
        config = {
            "format": "client-target-acquisition-evaluation-config/1",
            "name": name, "scenario": "target_acquisition", "seed": seed,
            "mode": mode, "task": task,
            "max_steps": int(source["max_steps"]),
            "timeout": int(source["timeout"]),
            "entrypoint": "electron_client_windows_uia",
            "cold_start_required": True,
        }
        result.update({
            "task": task, "public_fixture_name": fixture.public_name,
            "evaluation_config": config,
            "evaluation_config_fingerprint": _fingerprint(config),
        })

        def before_submit(driver: Any, _partial: dict[str, Any]) -> dict[str, Any]:
            staged = fixture.stage_targetless_handoff(baseline["instances"])
            return {
                **staged,
                "desktop_screenshot": driver.capture_desktop(
                    "targetless-before-submit.png",
                ),
            }

        def handoff(driver: Any, _partial: dict[str, Any]) -> dict[str, Any]:
            return {
                "foreground_hwnd": int(
                    ctypes.windll.user32.GetForegroundWindow() or 0
                ),
                "desktop_screenshot": driver.capture_desktop(
                    "targetless-after-submit.png",
                ),
                "focus_mutated": False,
            }

        def verify(client_result: dict[str, Any]) -> dict[str, Any]:
            assert fixture is not None
            return target_acquisition_verdict(
                mode=mode, fixture=fixture, baseline=baseline,
                client_result=client_result,
            )

        client_result = client_harness.run_case(
            name,
            {
                "task": task, "max_steps": int(source["max_steps"]),
                "timeout": int(source["timeout"]), "runtime_version": "vnext",
            },
            output_root=case_dir / "client", reset_session=True,
            before_submit=before_submit,
            after_submit=handoff, independent_verifier=verify,
            submit_without_focus=True,
        )
        result["client_result"] = client_result
        result["targetless_precondition"] = dict(
            client_result.get("before_submit_handoff") or {},
        )
        result.update(evidence_projector(client_result))
        result["external_verdict"] = dict(
            client_result.get("task_verdict") or {},
        )
        result["passed"] = client_result.get("passed") is True
    except Exception as exc:
        result["runner_error"] = f"{type(exc).__name__}: {exc}"
        result["external_verdict"] = {
            "source": "external_target_acquisition_oracle",
            "oracle_status": "unavailable", "status": "harness_unavailable",
            "passed": False, "errors": [result["runner_error"]],
        }
        result["passed"] = False
    finally:
        cleanup = (
            fixture.cleanup()
            if fixture is not None
            else {"passed": True, "errors": [], "skipped": True}
        )
        result["fixture_cleanup"] = cleanup
        if cleanup.get("passed") is not True:
            result["passed"] = False
        result["finished_at"] = datetime.now().astimezone().isoformat()
        report = case_dir / "report.json"
        report.write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8",
        )
        result["report"] = str(report)
        print(json.dumps(
            {"event": "visual_case_finished", **result},
            ensure_ascii=False,
        ), flush=True)
    return result


__all__ = ["run_target_acquisition_case"]
