"""Focused tests for the Pi-style shell result semantics."""
from __future__ import annotations

import subprocess

import pytest

from winpilot.action import shell


def test_tail_truncation_keeps_the_latest_lines() -> None:
    source = "\n".join(f"line-{index}" for index in range(2501))

    output, meta = shell._truncate_tail(source)

    assert meta["truncated"] is True
    assert meta["truncated_by"] == "lines"
    assert meta["total_lines"] == 2501
    assert meta["shown_lines"] == 2000
    assert output.splitlines()[0] == "line-501"
    assert output.splitlines()[-1] == "line-2500"


def test_tail_truncation_never_breaks_a_utf8_character() -> None:
    output, meta = shell._truncate_tail(
        "前缀-甲乙丙丁",
        max_lines=20,
        max_bytes=7,
    )

    assert meta["truncated"] is True
    assert meta["truncated_by"] == "bytes"
    assert "�" not in output
    assert output.endswith("丙丁")


def test_nonzero_result_is_actionable_and_preserves_full_output_path(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        shell,
        "_save_full_output",
        lambda _text: r"C:\Temp\full-shell.log",
    )

    result = shell._shape_result(
        stdout="\n".join(f"line-{index}" for index in range(2501)),
        stderr="bad command",
        ok=False,
        exit_code=7,
        error="command exited with code 7",
    )

    assert result["ok"] is False
    assert result["exit_code"] == 7
    assert result["output_meta"]["truncated"] is True
    assert result["full_output_path"] == r"C:\Temp\full-shell.log"
    assert result["next_step"] == (
        "use the output to correct the command or choose another tool"
    )


def test_timeout_result_gives_the_model_partial_state() -> None:
    result = shell._shape_result(
        stdout="partial progress",
        stderr="",
        ok=False,
        exit_code=None,
        error="command timed out",
        timeout=True,
    )

    assert result["timeout"] is True
    assert result["output"] == "partial progress"
    assert result["next_step"] == (
        "this attempt timed out; use partial output and current state "
        "to choose the next step"
    )


def test_selected_result_below_generic_boundary_is_not_truncated() -> None:
    selected = "a" * 12_000 + "MATCH_IN_THE_MIDDLE" + "z" * 12_000

    result = shell._shape_result(
        stdout=selected,
        stderr="",
        ok=True,
        exit_code=0,
    )

    assert result["output"] == selected
    assert "MATCH_IN_THE_MIDDLE" in result["output"]
    assert result["output_meta"]["truncated"] is False
    assert "full_output_path" not in result


def test_result_above_generic_boundary_is_recoverable(monkeypatch) -> None:
    monkeypatch.setattr(
        shell,
        "_save_full_output",
        lambda _text: r"C:\Temp\full-shell.log",
    )

    result = shell._shape_result(
        stdout="x" * (shell._PI_MAX_OUTPUT_BYTES + 1000),
        stderr="",
        ok=True,
        exit_code=0,
    )

    assert len(result["output"].encode("utf-8")) <= shell._PI_MAX_OUTPUT_BYTES
    assert result["output_meta"]["truncated"] is True
    assert result["output_meta"]["truncated_by"] == "bytes"
    assert result["full_output_path"] == r"C:\Temp\full-shell.log"


@pytest.mark.skipif(
    not hasattr(subprocess, "CREATE_NO_WINDOW"),
    reason="Windows PowerShell contract",
)
def test_powershell_default_file_writes_are_utf8(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(shell, "shell_enabled", lambda: True)
    target = tmp_path / "unicode-source.py"
    escaped = str(target).replace("'", "''")

    result = shell.run(
        f"Set-Content -LiteralPath '{escaped}' -Value 'label = \"dash — 中文\"'",
        shell="powershell",
    )

    assert result["ok"] is True
    assert target.read_text(encoding="utf-8-sig") == 'label = "dash — 中文"\n'


def test_empty_command_publishes_the_rejection(monkeypatch) -> None:
    events = []
    monkeypatch.setattr(shell, "shell_enabled", lambda: True)
    monkeypatch.setattr(
        shell.BUS,
        "publish",
        lambda event_type, **payload: events.append(
            {"type": event_type, **payload}
        ),
    )

    result = shell.run(" ")

    assert result == {"ok": False, "error": "empty command"}
    assert events == [
        {
            "type": "action",
            "action": "shell_blocked",
            "command": "",
            "reason": "empty command",
            "operation_id": events[0]["operation_id"],
        }
    ]
    assert len(events[0]["operation_id"]) == 32
