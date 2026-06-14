"""CodingSession file/repo tools — confined to a workspace, deterministic."""
from pathlib import Path

from winpilot.agent.coding_tools import CodingSession, coding_tool_schemas


def sess(tmp_path: Path) -> CodingSession:
    return CodingSession(tmp_path)


def test_schemas_shape():
    tools = coding_tool_schemas()
    names = {t["function"]["name"] for t in tools}
    assert {"read_file", "write_file", "apply_patch", "list_dir", "search_text",
            "run_shell", "done", "fail"} <= names


def test_write_then_read_roundtrip(tmp_path):
    s = sess(tmp_path)
    r = s.dispatch("write_file", {"path": "a/b.txt", "content": "hello\nworld"})
    assert '"ok": true' in r.text
    assert (tmp_path / "a" / "b.txt").read_text(encoding="utf-8") == "hello\nworld"
    rd = s.dispatch("read_file", {"path": "a/b.txt"})
    assert "1\thello" in rd.text and "2\tworld" in rd.text


def test_apply_patch_unique(tmp_path):
    s = sess(tmp_path)
    s.dispatch("write_file", {"path": "f.py", "content": "x = 1\ny = 2\n"})
    r = s.dispatch("apply_patch", {"path": "f.py", "find": "y = 2", "replace": "y = 3"})
    assert '"ok": true' in r.text
    assert (tmp_path / "f.py").read_text(encoding="utf-8") == "x = 1\ny = 3\n"


def test_apply_patch_not_found(tmp_path):
    s = sess(tmp_path)
    s.dispatch("write_file", {"path": "f.py", "content": "a"})
    r = s.dispatch("apply_patch", {"path": "f.py", "find": "zzz", "replace": "q"})
    assert "未找到" in r.text


def test_apply_patch_ambiguous(tmp_path):
    s = sess(tmp_path)
    s.dispatch("write_file", {"path": "f.py", "content": "dup\ndup\n"})
    r = s.dispatch("apply_patch", {"path": "f.py", "find": "dup", "replace": "x"})
    assert "不唯一" in r.text or "2 次" in r.text


def test_list_dir_skips_junk(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "readme.md").write_text("x", encoding="utf-8")
    r = sess(tmp_path).dispatch("list_dir", {})
    assert "src/" in r.text
    assert "readme.md" in r.text
    assert "node_modules" not in r.text


def test_search_text_finds_matches(tmp_path):
    (tmp_path / "a.py").write_text("def foo():\n    return 42\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("x = foo()\n", encoding="utf-8")
    r = sess(tmp_path).dispatch("search_text", {"query": "foo"})
    assert "a.py:1" in r.text and "b.py:1" in r.text


def test_path_escape_is_rejected(tmp_path):
    r = sess(tmp_path).dispatch("read_file", {"path": "../../etc/passwd"})
    assert "超出工作区" in r.text


def test_done_and_fail_are_terminal(tmp_path):
    s = sess(tmp_path)
    assert s.dispatch("done", {"summary": "ok"}).terminal == "done"
    assert s.dispatch("fail", {"reason": "no"}).terminal == "fail"


def test_unknown_tool(tmp_path):
    assert "未知工具" in sess(tmp_path).dispatch("nope", {}).text
