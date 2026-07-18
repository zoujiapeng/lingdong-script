"""Installed-app discovery: resolve human app names (incl. Chinese display
names) to launchable targets.

Why: ``find_app`` only sees RUNNING processes and ``launch`` only takes exe
names/paths — asked to open 网易云音乐 the agent had no way to learn that the
binary is ``cloudmusic.exe`` and got lost searching the disk. Display names
live in Start-Menu shortcuts and the UWP app list; this module indexes those.

Sources (merged, deduped, scored):
- Start Menu ``*.lnk`` (user + common) — shortcut filename IS the display name
- ``Get-StartApps`` (covers UWP/Store apps and most desktop apps)
- App Paths registry (classic exe aliases)
- Everything filename search as a last resort (*.exe)
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading
import time
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path

from ..utils.log import logger

_CACHE_TTL_S = 300.0
_cache_lock = threading.Lock()
_cache: tuple[float, list[dict]] | None = None

_SYSTEM_TOOL_TARGETS = [
    ("notepad", "notepad.exe"),
    ("记事本", "notepad.exe"),
    ("calculator", "calc.exe"),
    ("计算器", "calc.exe"),
    ("paint", "mspaint.exe"),
    ("画图", "mspaint.exe"),
    ("画板", "mspaint.exe"),
    ("command prompt", "cmd.exe"),
    ("命令提示符", "cmd.exe"),
    ("powershell", "powershell.exe"),
    ("terminal", "wt.exe"),
    ("终端", "wt.exe"),
]

_START_MENU_DIRS = [
    Path(os.environ.get("APPDATA", "")) / "Microsoft/Windows/Start Menu/Programs",
    Path(os.environ.get("PROGRAMDATA", "")) / "Microsoft/Windows/Start Menu/Programs",
]
_SKIP_WORDS = ("卸载", "uninstall", "帮助", "help", "网站", "website", "readme")


def _start_menu_entries() -> list[dict]:
    entries: list[dict] = []
    shortcut_shell = None
    try:
        import win32com.client

        shortcut_shell = win32com.client.Dispatch("WScript.Shell")
    except Exception:
        shortcut_shell = None
    for base in _START_MENU_DIRS:
        if not base.exists():
            continue
        try:
            for lnk in base.rglob("*.lnk"):
                name = lnk.stem
                if any(w in name.lower() for w in _SKIP_WORDS):
                    continue
                aliases: list[str] = []
                if shortcut_shell is not None:
                    try:
                        shortcut = shortcut_shell.CreateShortcut(str(lnk))
                        target_path = str(shortcut.TargetPath or "").strip()
                        arguments = str(shortcut.Arguments or "").strip()
                        if target_path:
                            aliases.extend([
                                Path(target_path).stem,
                                Path(target_path).parent.name,
                                target_path,
                            ])
                        if arguments:
                            aliases.append(arguments)
                    except Exception:
                        pass
                entries.append({
                    "name": name,
                    "target": str(lnk),
                    "kind": "lnk",
                    "aliases": list(dict.fromkeys(item for item in aliases if item)),
                })
        except OSError as exc:
            logger.debug("开始菜单扫描失败 %s: %s", base, exc)
    return entries


def _uwp_entries() -> list[dict]:
    """Get-StartApps: display name -> AppID (covers UWP + many desktop apps).

    Store apps (e.g. 网易云音乐) have NO classic .lnk/.exe — this list is the
    only way to find them. Console output must be forced to UTF-8: PowerShell
    5.1 pipes in the ANSI codepage (GBK here) which would mangle Chinese names.
    """
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "[Console]::OutputEncoding=[Text.Encoding]::UTF8; "
             "Get-StartApps | ForEach-Object { $_.Name + '|' + $_.AppID }"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=15, creationflags=subprocess.CREATE_NO_WINDOW,
        )
        entries = []
        for line in (out.stdout or "").splitlines():
            if "|" not in line:
                continue
            name, appid = line.split("|", 1)
            name, appid = name.strip(), appid.strip()
            if name and appid:
                entries.append({"name": name, "target": appid, "kind": "appid"})
        return entries
    except (subprocess.SubprocessError, OSError) as exc:
        logger.warning("Get-StartApps 失败: %s", exc)
        return []


def _app_paths_entries() -> list[dict]:
    import winreg

    entries: list[dict] = []
    for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            key = winreg.OpenKey(
                root, r"Software\Microsoft\Windows\CurrentVersion\App Paths")
        except OSError:
            continue
        try:
            for i in range(winreg.QueryInfoKey(key)[0]):
                try:
                    sub = winreg.EnumKey(key, i)
                    with winreg.OpenKey(key, sub) as sk:
                        path, _ = winreg.QueryValueEx(sk, None)
                    if path:
                        entries.append({"name": Path(sub).stem,
                                        "target": str(path).strip('"'),
                                        "kind": "exe"})
                except OSError:
                    continue
        finally:
            key.Close()
    return entries


def _system_tool_entries() -> list[dict]:
    """Stable Windows inbox tools that may not appear in StartApps/App Paths."""
    windir = os.environ.get("WINDIR", r"C:\Windows")
    system32 = Path(windir) / "System32"
    entries: list[dict] = []
    for name, exe in _SYSTEM_TOOL_TARGETS:
        path = system32 / exe
        target = str(path) if path.exists() else shutil.which(exe)
        if target:
            entries.append({"name": name, "target": str(target), "kind": "exe"})
    return entries


def _wps_suite_entries() -> list[dict]:
    """Discover separately launchable WPS suite components.

    StartApps often exposes only the umbrella "WPS Office" entry, which makes
    Writer, Spreadsheets, and Presentation indistinguishable. Resolve the real
    installed component executables without pinning a WPS version directory.
    """
    roots = [
        Path(os.environ.get("LOCALAPPDATA", "")) / "Kingsoft/WPS Office",
        Path(os.environ.get("ProgramFiles", "")) / "Kingsoft/WPS Office",
        Path(os.environ.get("ProgramFiles(x86)", "")) / "Kingsoft/WPS Office",
    ]
    components = (
        ("wps.exe", ("/prometheus", "/wps"), ("WPS Writer", "WPS文字")),
        ("et.exe", ("/prometheus", "/et"), ("WPS Spreadsheets", "WPS表格")),
        ("wpp.exe", ("/prometheus", "/wpp"), ("WPS Presentation", "WPS演示")),
    )
    entries: list[dict] = []
    for executable, arguments, names in components:
        candidates: list[Path] = []
        for root in roots:
            if not root.exists():
                continue
            candidates.extend(
                path for path in root.glob(f"*/office6/{executable}")
                if "utility" not in {part.casefold() for part in path.parts}
            )
            direct = root / "office6" / executable
            if direct.is_file():
                candidates.append(direct)
        if not candidates:
            continue
        target = max(
            dict.fromkeys(candidates),
            key=lambda path: path.stat().st_mtime if path.exists() else 0.0,
        )
        entries.extend(
            {
                "name": name,
                "target": str(target),
                "kind": "exe",
                "arguments": list(arguments),
            }
            for name in names
        )
    return entries


def _index() -> list[dict]:
    global _cache
    with _cache_lock:
        if _cache is not None and time.monotonic() - _cache[0] < _CACHE_TTL_S:
            return _cache[1]
    entries = (
        _start_menu_entries()
        + _uwp_entries()
        + _app_paths_entries()
        + _wps_suite_entries()
        + _system_tool_entries()
    )
    # Dedupe by (lowered name, kind) keeping first occurrence.
    seen: set[tuple[str, str]] = set()
    deduped = []
    for e in entries:
        key = (e["name"].lower(), e["kind"])
        if key not in seen:
            seen.add(key)
            deduped.append(e)
    with _cache_lock:
        _cache = (time.monotonic(), deduped)
    logger.info("应用索引就绪: %d 条 (开始菜单+UWP+注册表)", len(deduped))
    return deduped


def _normalized(value: str) -> str:
    value = unicodedata.normalize("NFKC", str(value or "")).casefold().strip()
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", value)


def _score(name_lower: str, query_lower: str) -> int:
    """Score literal and typo-tolerant names without product-specific rules."""

    name = _normalized(name_lower)
    query = _normalized(query_lower)
    if not name or not query:
        return 0
    if name == query:
        return 100
    if name.startswith(query):
        return 82
    if query in name:
        return 72
    if len(name) >= 4 and len(query) >= 4:
        ratio = SequenceMatcher(None, name, query).ratio()
        if ratio >= 0.78:
            return 45 + int(ratio * 20)
    return 0


def _entry_terms(entry: dict) -> list[str]:
    target = str(entry.get("target") or "")
    terms = [str(entry.get("name") or "")]
    terms.extend(str(item) for item in (entry.get("aliases") or []) if str(item).strip())
    if target:
        terms.extend([
            Path(target).stem,
            Path(target).parent.name,
        ])
        terms.extend(
            item for item in re.split(r"[^0-9A-Za-z\u4e00-\u9fff]+", target)
            if len(item) >= 3
        )
    return list(dict.fromkeys(item for item in terms if item.strip()))


def _score_entry(entry: dict, variants: list[str], literal_query: str) -> tuple[int, int, str]:
    best = 0
    literal = 0
    matched_term = ""
    name = str(entry.get("name") or "")
    for term in _entry_terms(entry):
        term_score = max((_score(term, variant) for variant in variants), default=0)
        # Display-name matches remain slightly stronger than executable/path
        # aliases, but an exact discovered binary alias must still beat an
        # unrelated fuzzy display name.
        if term != name and term_score:
            term_score = max(1, term_score - 3)
        if term_score > best:
            best, matched_term = term_score, term
        literal = max(literal, _score(term, literal_query))
    return best, literal, matched_term


def resolve(query: str, max_results: int = 5) -> list[dict]:
    """Installed-app candidates for a human name, best match first."""
    q = (query or "").strip().lower()
    if not q:
        return []
    from .app_aliases import expand as expand_app_aliases

    query_variants = list(dict.fromkeys(expand_app_aliases(q) or [q]))
    # Inbox tools have stable launch targets. Resolve them before the slower
    # StartApps/shortcut index so a cold desktop task does not pay that scan.
    fast_scored = [
        (*_score_entry(entry, query_variants, q)[:2], entry)
        for entry in (_system_tool_entries() + _wps_suite_entries())
    ]
    fast_scored = [item for item in fast_scored if item[0]]
    if fast_scored:
        # Preserve the user's localized/literal name when an alias has the
        # same launch target and score.  The returned name is also evidence
        # and a later title hint, so turning 画图 into "paint" is observable.
        fast_scored.sort(key=lambda item: (-item[0], -item[1]))
        return [
            {
                **entry,
                "match_score": score,
                "match_kind": "literal_or_alias",
            }
            for score, _literal_score, entry in fast_scored[:max_results]
        ]
    scored = []
    for e in _index():
        s, literal_score, matched_term = _score_entry(e, query_variants, q)
        if s:
            # shortcuts are the most reliable launch targets; appid next
            s += {"lnk": 2, "appid": 1}.get(e["kind"], 0)
            scored.append((s, literal_score, matched_term, e))
    scored.sort(key=lambda item: (-item[0], -item[1], str(item[3].get("name") or "").casefold()))
    results = [
        {
            **entry,
            "aliases": list(dict.fromkeys([
                *(str(item) for item in (entry.get("aliases") or []) if str(item).strip()),
                str(matched_term or ""),
            ])),
            "match_score": score,
            "matched_term": matched_term,
            "match_kind": (
                "fuzzy"
                if score < 70
                else ("target_alias" if _normalized(matched_term) != _normalized(entry.get("name") or "") else "literal_or_alias")
            ),
        }
        for score, _literal, matched_term, entry in scored[:max_results]
    ]
    if results:
        return results
    # Last resort: Everything filename search for an exe.
    try:
        from . import search
        hits = search.search(f"{query}*.exe", max_results=max_results)
        return [{"name": Path(p).stem, "target": p, "kind": "exe"}
                for p in (hits.get("results") or [])
                if not any(w in p.lower() for w in _SKIP_WORDS)]
    except Exception as exc:
        logger.debug("Everything 应用兜底失败: %s", exc)
        return []


def launch_target(entry: dict) -> dict:
    """Launch a resolved candidate. Returns {ok, via, target}."""
    kind, target = entry.get("kind"), entry.get("target", "")
    arguments = [str(item) for item in (entry.get("arguments") or []) if str(item).strip()]
    try:
        if kind == "appid":
            subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{target}"])
        elif kind == "exe" and arguments:
            subprocess.Popen(
                [target, *arguments],
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        else:  # lnk / exe — startfile resolves shortcuts natively
            os.startfile(target)  # noqa: S606
        return {
            "ok": True,
            "via": kind,
            "target": target,
            "arguments": arguments,
            "name": entry.get("name"),
            "aliases": list(entry.get("aliases") or []),
        }
    except OSError as exc:
        return {"ok": False, "error": str(exc), "target": target}
