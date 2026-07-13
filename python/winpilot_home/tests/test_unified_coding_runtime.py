from pathlib import Path
import sys
import types

# tool_schemas imports the optional VLM/screenshot stack. Keep this unit test
# runnable on non-Windows/lightweight CI where mss is not installed.
sys.modules.setdefault("mss", types.SimpleNamespace(mss=lambda: None))

from winpilot.agent.coding_tools import CodingSession
from winpilot.agent.tool_schemas import tool_schemas


def test_tool_schemas_add_coding_runtime_tools_only_when_enabled(monkeypatch):
    # The developer's ignored config may disable shell globally.  This test is
    # about Coding Runtime composition, not that machine-local policy.
    monkeypatch.setattr("winpilot.agent.tool_schemas.shell.shell_enabled", lambda: True)
    base = {t["function"]["name"] for t in tool_schemas(coding_runtime=False)}
    coding = {t["function"]["name"] for t in tool_schemas(coding_runtime=True)}

    assert "apply_patch" not in base
    assert "github_search" not in base
    assert {"apply_patch", "github_search", "run_shell", "list_dir", "read_file"} <= coding


def test_coding_runtime_search_text_respects_path(tmp_path: Path):
    (tmp_path / "src").mkdir()
    (tmp_path / "other").mkdir()
    (tmp_path / "src" / "a.py").write_text("needle = 1\n", encoding="utf-8")
    (tmp_path / "other" / "b.py").write_text("needle = 2\n", encoding="utf-8")

    result = CodingSession(tmp_path).dispatch("search_text", {"query": "needle", "path": "src"})

    assert "src/a.py:1" in result.text
    assert "other/b.py" not in result.text
