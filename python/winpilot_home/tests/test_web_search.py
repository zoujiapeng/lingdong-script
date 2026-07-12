class _Response:
    def __init__(self, body: bytes):
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self):
        return self._body


def test_duckduckgo_search_returns_compact_structured_results(monkeypatch):
    from winpilot.perception import web_search

    page = b"""<html><table>
    <tr><td><a href='//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.test%2Fproject&amp;x=1'
      class='result-link'>Example &amp; Result</a></td></tr>
    <tr><td class='result-snippet'><b>Useful</b> project summary.</td></tr>
    </table></html>"""
    monkeypatch.setattr(web_search.CONFIG, "get", lambda *_args, **_kwargs: "")
    monkeypatch.delenv("SERPER_API_KEY", raising=False)
    monkeypatch.setattr(web_search.urllib.request, "urlopen", lambda *_args, **_kwargs: _Response(page))

    result = web_search.search("example project", max_results=3)

    assert result["ok"] is True
    assert result["engine"] == "duckduckgo-lite"
    assert result["results"] == [{
        "title": "Example & Result",
        "url": "https://example.test/project",
        "snippet": "Useful project summary.",
    }]


def test_configured_serper_failure_falls_back_to_bing(monkeypatch):
    from winpilot.perception import web_search

    monkeypatch.setattr(web_search.CONFIG, "get", lambda *_args, **_kwargs: "secret")
    monkeypatch.setattr(web_search, "_serper", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("down")))
    monkeypatch.setattr(
        web_search,
        "_duckduckgo_lite",
        lambda *_args, **_kwargs: [{"title": "Fallback", "url": "https://example.test", "snippet": "ok"}],
    )

    result = web_search.search("query")

    assert result["ok"] is True
    assert result["engine"] == "duckduckgo-lite-fallback"
    assert "Serper failed" in result["warning"]
