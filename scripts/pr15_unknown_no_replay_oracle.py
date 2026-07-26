#!/usr/bin/env python3
"""Deterministic legacy ``run_action_batch`` unknown-outcome oracle for PR #15.

The parent process starts a fresh Python worker.  The worker imports WinPilot
from the requested checkout, executes the real legacy ToolSession ->
run_action_batch -> Controller -> scroll path, and replaces only the final
Windows input seams with recording fakes.

The selected background-message backend raises *after* its call is recorded,
which models the important uncertain boundary: Windows may have accepted the
wheel message before the caller observed an exception.  A safe implementation
must return an unknown mechanical outcome without selecting another backend or
replaying the mutation.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import random
import subprocess
import sys
import traceback
from types import MethodType
from typing import Any, Callable


BASELINE_SHA = "dab1bbf88e506dd4947347f7da5127f3bf90ab18"
CANDIDATE_SHA = "10a707dcf1e535d54efc7579a55a43876943cdb4"
ORACLE_SCHEMA = "pr15-legacy-unknown-no-replay/v1"


class InjectedOutcomeUnknown(RuntimeError):
    """Raised after the deterministic selected-backend call is recorded."""


def _utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        encoding="utf-8",
        errors="replace",
    )
    return completed.stdout.strip()


def _harness_revision() -> dict[str, Any]:
    script = Path(__file__).resolve()
    repo = script.parent.parent
    revision: dict[str, Any] = {
        "script": script.name,
        "file_sha256": _sha256(script),
    }
    try:
        revision.update({
            "git_head": _git(repo, "rev-parse", "HEAD"),
            "git_branch": _git(repo, "branch", "--show-current"),
            "git_status": _git(
                repo, "status", "--short", "--", str(script.relative_to(repo)),
            ),
        })
    except Exception as exc:
        revision["git_metadata_error"] = f"{type(exc).__name__}: {exc}"
    return revision


def _json_safe(value: Any) -> Any:
    if value is None or type(value) in {bool, int, float, str}:
        return value
    if isinstance(value, dict):
        return {
            str(key): _json_safe(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        try:
            return _json_safe(to_dict())
        except Exception:
            pass
    return repr(value)


def _write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )


def _role_for_sha(sha: str) -> str:
    if sha == BASELINE_SHA:
        return "baseline_legacy_fallback_reference"
    if sha == CANDIDATE_SHA:
        return "candidate_unknown_no_replay_subject"
    return "unsupported_commit"


def _raw_backend_payload(value: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "python_type": f"{type(value).__module__}.{type(value).__name__}",
    }
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        try:
            converted = to_dict()
        except Exception as exc:
            payload["serialization_error"] = f"{type(exc).__name__}: {exc}"
        else:
            if isinstance(converted, dict):
                payload.update(_json_safe(converted))
    else:
        completed = getattr(value, "input_completed", "not-present")
        if completed != "not-present":
            payload["input_completed"] = completed
        facts = getattr(value, "facts", None)
        if facts is not None:
            payload["facts"] = _json_safe(dict(facts))
    return payload


def _event_summary(events: list[dict[str, Any]]) -> dict[str, Any]:
    retained_types = {
        "agent_batch_action_start",
        "agent_batch_action",
        "desktop_action_queue",
        "action",
        "input_cleanup",
    }
    retained = [
        _json_safe(event)
        for event in events
        if str(event.get("type") or "") in retained_types
    ]
    starts = [
        event for event in events
        if event.get("type") == "agent_batch_action_start"
    ]
    finishes = [
        event for event in events
        if event.get("type") == "agent_batch_action"
    ]
    scroll_starts = [
        event for event in starts if event.get("tool") == "scroll"
    ]
    scroll_finishes = [
        event for event in finishes if event.get("tool") == "scroll"
    ]
    envelopes = [
        event.get("action_envelope")
        for event in scroll_starts
        if isinstance(event.get("action_envelope"), dict)
    ]
    budgets = [
        int(envelope.get("retry_budget") or 0)
        for envelope in envelopes
    ]
    return {
        "retained_events": retained,
        "counts": {
            "retained": len(retained),
            "batch_action_start_total": len(starts),
            "batch_action_finish_total": len(finishes),
            "scroll_action_start": len(scroll_starts),
            "scroll_action_finish": len(scroll_finishes),
        },
        "scroll_action_envelopes": _json_safe(envelopes),
        "retry_budget": {
            "declared_values": budgets,
            "maximum": max(budgets) if budgets else None,
            "zero_budget_proved": bool(budgets) and all(value == 0 for value in budgets),
        },
    }


def _first_result_data(batch: dict[str, Any]) -> dict[str, Any]:
    results = batch.get("results")
    if not isinstance(results, list) or not results:
        return {}
    first = results[0]
    if not isinstance(first, dict):
        return {}
    data = first.get("data")
    return dict(data) if isinstance(data, dict) else {}


def _evaluate(
    *,
    role: str,
    commit_ok: bool,
    clean_checkout: bool,
    import_roots_ok: bool,
    batch: dict[str, Any],
    raw_backend_results: list[dict[str, Any]],
    backend_events: list[dict[str, Any]],
    event_evidence: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, int], dict[str, Any]]:
    event_kinds = [str(event.get("kind") or "") for event in backend_events]
    selected_calls = event_kinds.count("post_message")
    physical_moves = event_kinds.count("physical_pointer_move")
    physical_wheels = event_kinds.count("physical_sendinput_wheel")
    cursor_restores = event_kinds.count("cursor_restore")
    mechanisms: list[str] = []
    if selected_calls:
        mechanisms.append("background_window_message")
    if physical_wheels:
        mechanisms.append("physical_sendinput")
    mechanism_switches = int(
        "background_window_message" in mechanisms
        and "physical_sendinput" in mechanisms
    )
    counts = {
        "selected_backend_calls": selected_calls,
        "typed_scroll_backend_returns": len(raw_backend_results),
        "physical_pointer_moves": physical_moves,
        "physical_wheel_calls": physical_wheels,
        "cursor_restores": cursor_restores,
        "mechanism_switches": mechanism_switches,
        # A physical wheel selected after the injected uncertain background
        # call is the legacy replay/fallback this oracle is intended to detect.
        "mutation_replays": physical_wheels if selected_calls else 0,
        "controller_scroll_starts": int(
            event_evidence.get("counts", {}).get("scroll_action_start") or 0
        ),
        "controller_scroll_finishes": int(
            event_evidence.get("counts", {}).get("scroll_action_finish") or 0
        ),
        "controller_action_count": int(
            batch.get("metrics", {}).get("action_count") or 0
        ) if isinstance(batch.get("metrics"), dict) else 0,
    }
    first_data = _first_result_data(batch)
    typed_first = raw_backend_results[0] if raw_backend_results else {}
    typed_facts = (
        typed_first.get("facts")
        if isinstance(typed_first.get("facts"), dict)
        else {}
    )
    raw_receipt: dict[str, Any]
    if raw_backend_results:
        input_completed = typed_first.get("input_completed", "not-captured")
        outcome = (
            "outcome_unknown"
            if input_completed is None
            else "applied"
            if input_completed is True
            else "not_applied"
        )
        raw_receipt = {
            "available": True,
            "source": "captured_execute_scroll_input_return",
            "action": "scroll",
            "outcome": outcome,
            "input_completed": input_completed,
            "backend_result": typed_first,
            "legacy_adapter_facts": first_data,
        }
    else:
        raw_receipt = {
            "available": False,
            "source": "baseline_legacy_untyped_scroll",
            "action": "scroll",
            "outcome": (
                "legacy_physical_fallback_applied"
                if first_data.get("ok") is True
                and first_data.get("via") == "sendinput_restored"
                else "legacy_reference_not_observed"
            ),
            "input_completed": (
                True
                if first_data.get("ok") is True
                and first_data.get("via") == "sendinput_restored"
                else None
            ),
            "legacy_adapter_facts": first_data,
        }

    common_checks = {
        "commit_matches_expected": commit_ok,
        "checkout_has_no_tracked_changes": clean_checkout,
        "imports_resolved_inside_checkout": import_roots_ok,
        "one_controller_scroll_action": (
            counts["controller_action_count"] == 1
            and counts["controller_scroll_starts"] == 1
            and counts["controller_scroll_finishes"] == 1
        ),
        "retry_budget_is_zero": bool(
            event_evidence.get("retry_budget", {}).get("zero_budget_proved")
        ),
        "selected_backend_called_once": selected_calls == 1,
    }

    if role == "candidate_unknown_no_replay_subject":
        role_checks = {
            "typed_unknown_receipt_observed": (
                len(raw_backend_results) == 1
                and typed_first.get("input_completed") is None
                and typed_facts.get("error") == "post_message_outcome_unknown"
                and raw_receipt.get("outcome") == "outcome_unknown"
            ),
            "legacy_adapter_does_not_claim_success": (
                batch.get("ok") is False
                and first_data.get("ok") is False
                and first_data.get("error") == "post_message_outcome_unknown"
            ),
            "no_physical_fallback": physical_moves == 0 and physical_wheels == 0,
            "no_mechanism_switch": mechanism_switches == 0,
            "no_mutation_replay": counts["mutation_replays"] == 0,
        }
        all_checks = {**common_checks, **role_checks}
        passed = all(all_checks.values())
        verdict = {
            "status": "pass" if passed else "fail",
            "verdict": (
                "candidate_unknown_no_replay_proved"
                if passed else "candidate_contract_not_proved"
            ),
            "grounded": True,
            "comparison_role": role,
            "role_expectation_pass": passed,
            "unknown_no_replay_pass": passed,
            "checks": all_checks,
            "first_failed_check": next(
                (name for name, ok in all_checks.items() if not ok),
                None,
            ),
            "proof_basis": (
                "recorded selected backend call, captured typed mechanical "
                "receipt, full ToolSession transaction, and absence of any "
                "recorded physical fallback seam"
            ),
        }
    elif role == "baseline_legacy_fallback_reference":
        role_checks = {
            "untyped_legacy_path_observed": len(raw_backend_results) == 0,
            "legacy_physical_fallback_observed": (
                physical_moves == 1
                and physical_wheels == 1
                and cursor_restores == 1
            ),
            "legacy_mechanism_switch_observed": mechanism_switches == 1,
            "legacy_replay_observed": counts["mutation_replays"] == 1,
            "legacy_adapter_reports_fallback_success": (
                batch.get("ok") is True
                and first_data.get("ok") is True
                and first_data.get("via") == "sendinput_restored"
            ),
        }
        all_checks = {**common_checks, **role_checks}
        passed = all(all_checks.values())
        verdict = {
            "status": "reference_observed" if passed else "fail",
            "verdict": (
                "baseline_legacy_fallback_reference_observed"
                if passed else "baseline_reference_not_observed"
            ),
            "grounded": True,
            "comparison_role": role,
            "role_expectation_pass": passed,
            # Deliberately false: the baseline is the known behavior reference,
            # not an implementation of the new unknown/no-replay contract.
            "unknown_no_replay_pass": False,
            "checks": all_checks,
            "first_failed_check": next(
                (name for name, ok in all_checks.items() if not ok),
                None,
            ),
            "proof_basis": (
                "recorded selected background-message call followed by the "
                "recorded legacy physical wheel fallback in the same full "
                "ToolSession transaction"
            ),
        }
    else:
        verdict = {
            "status": "invalid",
            "verdict": "unsupported_commit",
            "grounded": False,
            "comparison_role": role,
            "role_expectation_pass": False,
            "unknown_no_replay_pass": False,
            "checks": common_checks,
            "first_failed_check": "supported_commit",
        }
    return verdict, counts, raw_receipt


def _run_worker(args: argparse.Namespace) -> int:
    started_at = _utc_now()
    checkout = Path(args.checkout).resolve()
    output = Path(args.output).resolve()
    expected_sha = str(args.expected_sha).strip().lower()
    role = _role_for_sha(expected_sha)
    report: dict[str, Any] = {
        "schema": ORACLE_SCHEMA,
        "started_at": started_at,
        "finished_at": None,
        "harness_revision": _harness_revision(),
        "seed": int(args.seed),
        "comparison_role": role,
        "entrypoint": "legacy_toolsession_run_action_batch_integration",
        "outer_run_ids": [],
        "child_run_ids": [],
        "runtime_run_identity": "not_applicable_deterministic_in_process_oracle",
        "model_configuration": {
            "provider": "not_applicable",
            "model": "not_applicable",
            "reason": "unknown-no-replay is injected below the model boundary",
        },
        "screenshots": {
            "before": "not_applicable_no_desktop_input_is_executed",
            "after": "not_applicable_all_final_input_seams_are_recording_fakes",
        },
        "execution_process": {
            "mode": "fresh_subprocess",
            "pid": os.getpid(),
            "parent_pid": os.getppid(),
            "winpilot_preimported": any(
                name == "winpilot" or name.startswith("winpilot.")
                for name in sys.modules
            ),
        },
        "product": {
            "checkout": str(checkout),
            "expected_commit": expected_sha,
        },
    }
    event_queue = None
    bus = None
    try:
        if not checkout.is_dir():
            raise RuntimeError(f"checkout does not exist: {checkout}")
        actual_sha = _git(checkout, "rev-parse", "HEAD").lower()
        tracked_status = _git(
            checkout, "status", "--porcelain", "--untracked-files=normal",
        )
        role = _role_for_sha(actual_sha) if role == "unsupported_commit" else role
        report["comparison_role"] = role
        report["product"].update({
            "actual_commit": actual_sha,
            "commit_matches_expected": actual_sha == expected_sha,
            "tracked_status": tracked_status,
            "clean_tracked_worktree": tracked_status == "",
        })
        if actual_sha != expected_sha:
            raise RuntimeError(
                f"checkout HEAD {actual_sha} does not match expected {expected_sha}"
            )
        if role == "unsupported_commit":
            raise RuntimeError(
                "expected commit is not the PR #15 baseline or candidate"
            )

        product_root = (checkout / "python" / "winpilot_home").resolve()
        if not (product_root / "winpilot").is_dir():
            raise RuntimeError(f"WinPilot package not found under {product_root}")
        sys.path.insert(0, str(product_root))
        os.chdir(checkout)

        from winpilot.action import executor
        from winpilot.agent import scope_guard
        from winpilot.agent.tool_result import ToolResult
        from winpilot.agent.tools import ToolSession
        from winpilot.utils.log import BUS

        bus = BUS
        import_origins = {
            "winpilot.action.executor": str(
                Path(sys.modules["winpilot.action.executor"].__file__).resolve()
            ),
            "winpilot.agent.tools": str(
                Path(sys.modules["winpilot.agent.tools"].__file__).resolve()
            ),
            "winpilot.agent.tool_result": str(
                Path(sys.modules["winpilot.agent.tool_result"].__file__).resolve()
            ),
        }
        import_roots_ok = all(
            Path(origin).is_relative_to(product_root)
            for origin in import_origins.values()
        )
        report["product"].update({
            "import_root": str(product_root),
            "import_origins": import_origins,
            "imports_resolved_inside_checkout": import_roots_ok,
        })
        if not import_roots_ok:
            raise RuntimeError("one or more product imports escaped the checkout")

        rng = random.Random(int(args.seed))
        target_hwnd = 400_000 + rng.randrange(1, 50_000)
        x = rng.randrange(240, 721)
        y = rng.randrange(180, 541)
        amount = -rng.randrange(1, 7)
        action_args = {"x": x, "y": y, "amount": amount}
        report["deterministic_input"] = {
            "target_hwnd": target_hwnd,
            "action": "scroll",
            "args": action_args,
        }

        backend_events: list[dict[str, Any]] = []
        raw_backend_results: list[dict[str, Any]] = []
        sequence = 0

        def record(kind: str, **payload: Any) -> None:
            nonlocal sequence
            sequence += 1
            backend_events.append({
                "sequence": sequence,
                "kind": kind,
                **_json_safe(payload),
            })

        def window_from_point(point: Any) -> int:
            record("window_from_point", point=list(point))
            return target_hwnd

        def post_message(*call_args: Any, **call_kwargs: Any) -> None:
            record(
                "post_message",
                mechanism="background_window_message",
                call_accepted_by_python=True,
                injected_outcome="outcome_unknown_after_selected_call",
                positional_arg_count=len(call_args),
                keyword_names=sorted(call_kwargs),
            )
            raise InjectedOutcomeUnknown(
                "deterministic unknown outcome after selected backend call"
            )

        def raw_move_mouse(move_x: int, move_y: int) -> None:
            record(
                "physical_pointer_move",
                mechanism="physical_sendinput",
                x=int(move_x),
                y=int(move_y),
                intercepted=True,
            )

        def send_inputs(inputs: list[Any]) -> None:
            record(
                "physical_sendinput_wheel",
                mechanism="physical_sendinput",
                input_count=len(inputs),
                intercepted=True,
            )

        def cursor_pos() -> tuple[int, int]:
            saved = (x + 17, y + 19)
            record("cursor_position_read", position=list(saved))
            return saved

        def restore_cursor(
            saved: Any,
            expected_agent_pos: Any,
            hwnd: int | None,
        ) -> bool:
            record(
                "cursor_restore",
                saved=list(saved) if saved is not None else None,
                expected_agent_pos=(
                    list(expected_agent_pos)
                    if expected_agent_pos is not None else None
                ),
                hwnd=hwnd,
                intercepted=True,
            )
            return True

        # Deterministic final-device seams.  No mouse, keyboard, window focus,
        # or Electron process is touched by this oracle.
        executor._dry_run = lambda: False
        executor._no_mouse_steal = lambda: True
        executor.multi_input_cooperation_enabled = lambda: False
        executor.cooperative_input_guard = lambda *unused, **unused_kw: None
        executor._same_window_root = lambda *unused, **unused_kw: True
        executor.win32gui.WindowFromPoint = window_from_point
        executor.win32gui.PostMessage = post_message
        executor._raw_move_mouse = raw_move_mouse
        executor._send_inputs = send_inputs
        executor._cursor_pos = cursor_pos
        executor._restore_cursor_after_agent_input = restore_cursor
        scope_guard.ensure_scope = lambda *unused, **unused_kw: None
        scope_guard.take_scope_recovery = lambda *unused, **unused_kw: None

        if hasattr(executor, "execute_scroll_input"):
            typed_scroll = executor.execute_scroll_input

            def capture_typed_scroll(*call_args: Any, **call_kwargs: Any) -> Any:
                value = typed_scroll(*call_args, **call_kwargs)
                raw_backend_results.append(_raw_backend_payload(value))
                return value

            executor.execute_scroll_input = capture_typed_scroll

        session = ToolSession(task="PR15 deterministic unknown no replay oracle")
        session.run_id = f"pr15-unknown-{actual_sha[:8]}-{int(args.seed)}"
        session.hwnd = target_hwnd
        session.world_enabled = False
        session._window_alive = lambda candidate: int(candidate or 0) == target_hwnd
        controller = session._fast_controller()

        def deterministic_auto_verify(
            self: Any,
            region: Any,
            source_action: str,
            source_data: dict[str, Any],
        ) -> ToolResult:
            return ToolResult(json.dumps({
                "ok": True,
                "verified": False,
                "source_action": source_action,
                "effect_observed": False,
                "postcondition_satisfied": False,
                "summary_kind": "deterministic_no_capture_oracle",
            }))

        controller._auto_visual_verify = MethodType(
            deterministic_auto_verify, controller,
        )

        event_queue = BUS.subscribe()
        tool_result = session.dispatch("run_action_batch", {
            "subgoal": "exercise one selected legacy scroll backend",
            "actions": [{"scroll": action_args}],
            "stop_on_error": True,
            "sync": False,
        })
        events: list[dict[str, Any]] = []
        while not event_queue.empty():
            events.append(event_queue.get_nowait())
        BUS.unsubscribe(event_queue)
        event_queue = None

        raw_tool_text = str(getattr(tool_result, "text", "") or "")
        try:
            batch = json.loads(raw_tool_text)
        except json.JSONDecodeError:
            batch = {
                "_non_json_tool_result": raw_tool_text,
            }
        if not isinstance(batch, dict):
            batch = {"_non_object_tool_result": _json_safe(batch)}

        event_evidence = _event_summary(events)
        verdict, counts, raw_receipt = _evaluate(
            role=role,
            commit_ok=actual_sha == expected_sha,
            clean_checkout=tracked_status == "",
            import_roots_ok=import_roots_ok,
            batch=batch,
            raw_backend_results=raw_backend_results,
            backend_events=backend_events,
            event_evidence=event_evidence,
        )
        report.update({
            "backend_events": backend_events,
            "call_counts": counts,
            "mechanisms_observed": [
                mechanism
                for mechanism, present in (
                    ("background_window_message", counts["selected_backend_calls"] > 0),
                    ("physical_sendinput", counts["physical_wheel_calls"] > 0),
                )
                if present
            ],
            "raw_backend_returns": raw_backend_results,
            "raw_receipt": raw_receipt,
            "retry_budget": event_evidence["retry_budget"],
            "event_evidence": event_evidence,
            "transaction": _json_safe(batch.get("transaction", {})),
            "full_legacy_run_action_batch_result": _json_safe(batch),
            "grounded_verdict": verdict,
            "action_count": counts["controller_action_count"],
            "duplicate_input_count": counts["mutation_replays"],
            "first_failure_owner": (
                None
                if verdict.get("role_expectation_pass") is True
                else "test_harness_or_legacy_runtime"
            ),
            "oracle_execution_passed": bool(
                verdict.get("role_expectation_pass")
            ),
        })
    except Exception as exc:
        report.update({
            "oracle_execution_passed": False,
            "error": {
                "type": type(exc).__name__,
                "message": str(exc),
                "traceback": traceback.format_exc(),
            },
            "grounded_verdict": {
                "status": "invalid",
                "verdict": "oracle_execution_failed",
                "grounded": False,
                "comparison_role": report.get("comparison_role"),
                "role_expectation_pass": False,
                "unknown_no_replay_pass": False,
                "first_failed_check": "oracle_execution",
            },
        })
    finally:
        if event_queue is not None and bus is not None:
            try:
                bus.unsubscribe(event_queue)
            except Exception:
                pass
        report["finished_at"] = _utc_now()
        _write_report(output, report)

    verdict = report.get("grounded_verdict", {})
    print(json.dumps({
        "output": str(output),
        "commit": report.get("product", {}).get("actual_commit"),
        "comparison_role": report.get("comparison_role"),
        "verdict": verdict.get("verdict"),
        "role_expectation_pass": verdict.get("role_expectation_pass"),
        "unknown_no_replay_pass": verdict.get("unknown_no_replay_pass"),
    }, ensure_ascii=False))
    return 0 if report.get("oracle_execution_passed") is True else 1


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run the PR #15 legacy ToolSession unknown/no-replay integration "
            "oracle against one exact checkout."
        )
    )
    parser.add_argument("--checkout", required=True)
    parser.add_argument("--expected-sha", required=True)
    parser.add_argument("--seed", required=True, type=int)
    parser.add_argument("--output", required=True)
    parser.add_argument("--_worker", action="store_true", help=argparse.SUPPRESS)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    if args._worker:
        return _run_worker(args)
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--checkout", str(Path(args.checkout).resolve()),
        "--expected-sha", str(args.expected_sha),
        "--seed", str(int(args.seed)),
        "--output", str(Path(args.output).resolve()),
        "--_worker",
    ]
    completed = subprocess.run(command, check=False)
    return int(completed.returncode)


if __name__ == "__main__":
    raise SystemExit(main())
