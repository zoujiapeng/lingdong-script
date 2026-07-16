from __future__ import annotations

import pytest


@pytest.mark.parametrize(
    ("task", "expected_target"),
    [
        ("打开记事本", "记事本"),
        ("启动计算器", "计算器"),
        ("运行终端", "终端"),
        ("请帮我打开浏览器", "浏览器"),
        ("open Notepad", "Notepad"),
        ("launch Calculator", "Calculator"),
        ("start Terminal", "Terminal"),
    ],
)
def test_complete_launch_expressions_compile_to_typed_open_application(
    task: str,
    expected_target: str,
) -> None:
    from winpilot.tasking.compiler import TaskContractCompiler

    contract = TaskContractCompiler().compile(task, cold_start=True)

    assert contract.intent.operation == ["open"]
    assert contract.intent.destination == expected_target
    assert contract.intent.provenance["destination"] == "application_launch_relation"
    assert [stage.type.value for stage in contract.stages] == ["OpenApplication"]
    assert contract.stages[0].parameters["app"] == expected_target
    assert contract.stages[0].obligations[0].predicate == "application.open"


@pytest.mark.parametrize("task", ["打开报告.docx", "open https://example.test"])
def test_opening_media_or_url_is_not_claimed_as_application_launch(task: str) -> None:
    from winpilot.tasking.compiler import TaskContractCompiler

    contract = TaskContractCompiler().compile(task, cold_start=True)

    assert "open" not in contract.intent.operation
    assert [stage.type.value for stage in contract.stages] == ["CreateOrEditArtifact"]
