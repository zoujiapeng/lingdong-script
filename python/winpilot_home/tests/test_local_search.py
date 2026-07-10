from pathlib import Path
import subprocess
from types import SimpleNamespace


def test_find_es_prefers_bundled_binary(tmp_path, monkeypatch):
    from winpilot.perception import search

    bundled = tmp_path / "es.exe"
    bundled.write_bytes(b"test")
    monkeypatch.setattr(search, "BIN_DIR", tmp_path)
    monkeypatch.setattr(search.CONFIG, "get", lambda *_args, default=None: default)

    assert search.find_es() == str(bundled)


def test_everything_starts_client_without_blocking_current_query(monkeypatch):
    from winpilot.perception import search

    calls = []

    def run_es(*args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=8, stdout="", stderr="no IPC")

    monkeypatch.setattr(search, "find_es", lambda: r"C:\tools\es.exe")
    monkeypatch.setattr(search, "_run_es", run_es)
    monkeypatch.setattr(search, "_ensure_everything_client", lambda: True)

    out = search._search_everything("result", 10, None, kind="file")

    assert out["engine"] == "everything-warming"
    assert out["results"] == []
    assert len(calls) == 1
    assert calls[0][1]["kind"] == "file"


def test_everything_returns_indexed_results(monkeypatch):
    from winpilot.perception import search

    monkeypatch.setattr(search, "find_es", lambda: r"C:\tools\es.exe")
    monkeypatch.setattr(
        search,
        "_run_es",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout="C:\\Users\\zou\\Desktop\\result.txt\n",
            stderr="",
        ),
    )

    out = search._search_everything("result", 10, None, kind="file")

    assert out["engine"] == "everything"
    assert out["results"] == [r"C:\Users\zou\Desktop\result.txt"]


def test_everything_timeout_returns_bounded_fallback_hint(monkeypatch):
    from winpilot.perception import search

    monkeypatch.setattr(search, "find_es", lambda: r"C:\tools\es.exe")
    monkeypatch.setattr(
        search,
        "_run_es",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            subprocess.TimeoutExpired("es.exe", 1.0)
        ),
    )

    out = search._search_everything("result", 10, None)

    assert out["engine"] == "everything-failed"
    assert "bounded local scan" in out["hint"]


def test_scandir_fallback_filters_kind_extension_and_sort(tmp_path):
    from winpilot.perception import search

    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "report-old.pdf").write_text("old", encoding="utf-8")
    (reports / "report-new.pdf").write_text("new", encoding="utf-8")
    (reports / "report.txt").write_text("text", encoding="utf-8")
    (reports / "report-folder").mkdir()

    files = search._search_scandir(
        "report",
        10,
        str(tmp_path),
        kind="file",
        extensions=[".pdf"],
        sort="name",
        timeout_s=1.0,
    )
    folders = search._search_scandir(
        "report",
        10,
        str(tmp_path),
        kind="folder",
        sort="name",
        timeout_s=1.0,
    )

    assert [Path(item).name for item in files["results"]] == [
        "report-new.pdf",
        "report-old.pdf",
    ]
    assert [Path(item).name for item in folders["results"]] == [
        "report-folder",
        "reports",
    ]
    assert files["partial"] is False


def test_search_uses_bounded_fallback_and_reports_latency(tmp_path, monkeypatch):
    from winpilot.perception import search

    (tmp_path / "needle.txt").write_text("x", encoding="utf-8")
    monkeypatch.setattr(search, "_search_everything", lambda *args, **kwargs: None)

    out = search.search("needle", path=str(tmp_path), kind="file")

    assert out["engine"] == "scandir"
    assert out["results"] == [str(tmp_path / "needle.txt")]
    assert out["elapsed_ms"] >= 0
    assert out["query"] == "needle"
    assert out["kind"] == "file"


def test_everything_warmup_runs_in_background(monkeypatch):
    import threading

    from winpilot.perception import search

    called = threading.Event()
    monkeypatch.setattr(search, "find_es", lambda: r"C:\tools\es.exe")
    monkeypatch.setattr(search, "_find_everything_exe", lambda: r"C:\tools\Everything.exe")
    monkeypatch.setattr(
        search,
        "_search_everything",
        lambda *args, **kwargs: called.set() or {"engine": "everything", "results": [], "count": 0},
    )
    monkeypatch.setattr(search, "_warm_thread", None)

    assert search.warm_async() is True
    assert called.wait(timeout=1.0)
