"""Small pluggable web-search capability for the unified Agent.

Serper is used when configured; otherwise Bing's public RSS result feed gives
the model titles, URLs, and snippets without driving a browser or scraping a
rendered result page. Search is retrieval only: opening or acting on a result
still uses the normal browser/desktop capability and evidence rules.
"""
from __future__ import annotations

import html
from html.parser import HTMLParser
import json
import os
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from typing import Any

from ..config import CONFIG
from ..utils.log import BUS

_USER_AGENT = "WinPilot/1.0 (+local desktop agent)"


def _plain(value: Any, limit: int = 500) -> str:
    text = re.sub(r"<[^>]+>", " ", html.unescape(str(value or "")))
    return re.sub(r"\s+", " ", text).strip()[:limit]


def _serper(query: str, limit: int, api_key: str, timeout_s: float) -> list[dict[str, str]]:
    request = urllib.request.Request(
        "https://google.serper.dev/search",
        data=json.dumps({"q": query, "num": limit}).encode("utf-8"),
        headers={
            "X-API-KEY": api_key,
            "Content-Type": "application/json",
            "User-Agent": _USER_AGENT,
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout_s) as response:
        payload = json.loads(response.read().decode("utf-8", errors="replace"))
    return [
        {
            "title": _plain(item.get("title"), 240),
            "url": str(item.get("link") or "")[:1000],
            "snippet": _plain(item.get("snippet")),
        }
        for item in (payload.get("organic") or [])[:limit]
        if isinstance(item, dict) and item.get("link")
    ]


def _duckduckgo_result_url(value: str) -> str:
    href = html.unescape(str(value or "").strip())
    if href.startswith("//"):
        href = "https:" + href
    parsed = urllib.parse.urlsplit(href)
    redirected = urllib.parse.parse_qs(parsed.query).get("uddg", [])
    return str(redirected[0] if redirected else href).strip()


class _DuckDuckGoLiteParser(HTMLParser):
    def __init__(self, limit: int) -> None:
        super().__init__(convert_charrefs=True)
        self.limit = limit
        self.results: list[dict[str, str]] = []
        self._in_title = False
        self._in_snippet = False
        self._href = ""
        self._buffer: list[str] = []

    @staticmethod
    def _classes(attrs: list[tuple[str, str | None]]) -> set[str]:
        value = next((value for key, value in attrs if key == "class"), "") or ""
        return set(value.split())

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        classes = self._classes(attrs)
        if tag == "a" and "result-link" in classes and len(self.results) < self.limit:
            self._in_title = True
            self._href = next((value for key, value in attrs if key == "href"), "") or ""
            self._buffer = []
        elif tag == "td" and "result-snippet" in classes and self.results:
            self._in_snippet = True
            self._buffer = []

    def handle_data(self, data: str) -> None:
        if self._in_title or self._in_snippet:
            self._buffer.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._in_title:
            self._in_title = False
            url = _duckduckgo_result_url(self._href)
            title = _plain(" ".join(self._buffer), 240)
            if url and title:
                self.results.append({"title": title, "url": url[:1000], "snippet": ""})
            self._buffer = []
        elif tag == "td" and self._in_snippet:
            self._in_snippet = False
            if self.results:
                self.results[-1]["snippet"] = _plain(" ".join(self._buffer))
            self._buffer = []


def _duckduckgo_lite(query: str, limit: int, timeout_s: float) -> list[dict[str, str]]:
    url = "https://lite.duckduckgo.com/lite/?" + urllib.parse.urlencode({"q": query})
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout_s) as response:
        body = response.read().decode("utf-8", errors="replace")
    parser = _DuckDuckGoLiteParser(limit)
    parser.feed(body)
    return parser.results[:limit]


def _bing_rss(query: str, limit: int, timeout_s: float) -> list[dict[str, str]]:
    url = "https://www.bing.com/search?" + urllib.parse.urlencode({
        "format": "rss",
        "q": query,
    })
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout_s) as response:
        root = ET.fromstring(response.read())
    results: list[dict[str, str]] = []
    for item in root.findall("./channel/item")[:limit]:
        link = str(item.findtext("link") or "").strip()
        if not link:
            continue
        results.append({
            "title": _plain(item.findtext("title"), 240),
            "url": link[:1000],
            "snippet": _plain(item.findtext("description")),
        })
    return results


def search(query: str, max_results: int = 5, timeout_s: float = 10.0) -> dict[str, Any]:
    started = time.monotonic()
    query = str(query or "").strip()
    limit = max(1, min(10, int(max_results)))
    timeout_s = max(1.0, min(float(timeout_s), 20.0))
    if not query:
        return {"ok": False, "engine": "none", "query": "", "results": [], "error": "query is empty"}

    configured = CONFIG.get("search", "serper_api_key", default="") or os.environ.get("SERPER_API_KEY", "")
    engine = "serper" if configured else "duckduckgo-lite"
    error = ""
    try:
        results = (
            _serper(query, limit, str(configured), timeout_s)
            if configured
            else _duckduckgo_lite(query, limit, timeout_s)
        )
        if not results and not configured:
            engine = "bing-rss-fallback"
            results = _bing_rss(query, limit, timeout_s)
            error = "DuckDuckGo Lite returned no results; used Bing RSS"
    except Exception as exc:
        # A configured paid provider should not make search less available.
        try:
            engine = "duckduckgo-lite-fallback"
            results = _duckduckgo_lite(query, limit, timeout_s)
            error = f"{'Serper' if configured else 'DuckDuckGo Lite'} failed; used DuckDuckGo Lite: {exc}"
            if not results:
                engine = "bing-rss-fallback"
                results = _bing_rss(query, limit, timeout_s)
                error += "; DuckDuckGo returned no results, used Bing RSS"
        except Exception as fallback_exc:
            try:
                engine = "bing-rss-fallback"
                results = _bing_rss(query, limit, timeout_s)
                error = f"primary search failed: {exc}; DuckDuckGo failed: {fallback_exc}; used Bing RSS"
            except Exception as bing_exc:
                results = []
                error = f"search failed: {exc}; DuckDuckGo failed: {fallback_exc}; Bing RSS failed: {bing_exc}"
    elapsed_ms = int((time.monotonic() - started) * 1000)
    payload = {
        "ok": bool(results),
        "engine": engine,
        "query": query,
        "count": len(results),
        "results": results,
        "elapsed_ms": elapsed_ms,
    }
    if error:
        payload["warning" if results else "error"] = error[:500]
    BUS.publish("action", action="web_search", engine=engine, count=len(results), elapsed_ms=elapsed_ms)
    return payload
