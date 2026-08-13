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


@pytest.mark.parametrize(
    ("command", "shell_name", "expected"),
    [
        (
            'powershell -Command "$value=7; Write-Output $value"',
            "powershell",
            "$value=7; Write-Output $value",
        ),
        (
            'PowerShell.exe -NoProfile -ExecutionPolicy Bypass -Command "Get-Process"',
            "powershell",
            "Get-Process",
        ),
        ('cmd /c "echo ready"', "cmd", "echo ready"),
    ],
)
def test_redundant_selected_shell_wrapper_is_unwrapped(
    command: str,
    shell_name: str,
    expected: str,
) -> None:
    assert shell._unwrap_redundant_shell_command(command, shell_name) == expected


def test_other_shell_wrapper_is_left_to_the_command_body() -> None:
    command = 'cmd /c "echo from cmd"'

    assert shell._unwrap_redundant_shell_command(command, "powershell") == command


@pytest.mark.parametrize(
    "command",
    [
        (
            "Stop-Process -Name "
            f"{shell.os.path.splitext(shell.os.path.basename(shell.sys.executable))[0]} "
            "-Force"
        ),
        f"taskkill /F /IM {shell.os.path.basename(shell.sys.executable)}",
        f"Stop-Process -Id {shell.os.getpid()} -Force",
    ],
)
def test_runtime_host_termination_is_the_one_process_kill_boundary(
    command: str,
) -> None:
    result = shell._runtime_host_termination_guard(command)

    assert result is not None
    assert result["blocked"] is True
    assert "intended child PID" in result["error"]


def test_targeted_child_process_kill_remains_available() -> None:
    command = f"Stop-Process -Id {shell.os.getpid() + 100000} -Force"

    assert shell._runtime_host_termination_guard(command) is None


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


@pytest.mark.skipif(
    not hasattr(subprocess, "CREATE_NO_WINDOW"),
    reason="Windows PowerShell contract",
)
def test_powershell_preserves_native_exit_code_and_stderr(monkeypatch) -> None:
    monkeypatch.setattr(shell, "shell_enabled", lambda: True)

    result = shell.run(
        'cmd /c "echo ACTIONABLE 1>&2 & exit /b 23"',
        shell="powershell",
    )

    assert result["ok"] is False
    assert result["exit_code"] == 23
    assert result["error"] == "command exited with code 23"
    assert "ACTIONABLE" in result["output"]


@pytest.mark.skipif(
    not hasattr(subprocess, "CREATE_NO_WINDOW"),
    reason="Windows PowerShell contract",
)
def test_powershell_uses_final_statement_status(monkeypatch) -> None:
    monkeypatch.setattr(shell, "shell_enabled", lambda: True)

    result = shell.run(
        "cmd /c exit 23; Write-Output recovered",
        shell="powershell",
    )

    assert result["ok"] is True
    assert result["exit_code"] == 0
    assert result["output"].strip() == "recovered"


@pytest.mark.skipif(
    not hasattr(subprocess, "CREATE_NO_WINDOW"),
    reason="Windows PowerShell contract",
)
def test_powershell_preserves_formatted_object_output(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(shell, "shell_enabled", lambda: True)
    escaped = str(tmp_path).replace("'", "''")

    result = shell.run(
        f"Get-Item -LiteralPath '{escaped}' | Select-Object Name, FullName",
        shell="powershell",
    )

    assert result["ok"] is True
    assert "FullName" in result["output"]
    assert tmp_path.name in result["output"]


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


def test_write_file_preserves_literal_multiline_utf8(tmp_path) -> None:
    target = tmp_path / "nested" / "go_game.py"
    content = "import tkinter as tk\nlabel = '围棋 <黑白> & 原样'\n"

    result = shell.write_file(str(target), content)

    assert result == {
        "ok": True,
        "path": str(target),
        "bytes": len(content.encode("utf-8")),
        "output": (
            f"wrote {len(content.encode('utf-8'))} UTF-8 bytes to {target}"
        ),
    }
    assert target.read_text(encoding="utf-8") == content
