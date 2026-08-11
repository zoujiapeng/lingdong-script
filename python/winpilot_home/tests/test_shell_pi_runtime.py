"""Focused tests for the Pi-style shell result semantics."""
from __future__ import annotations

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
