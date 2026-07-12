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
import shutil
import subprocess
import threading
import time
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
    for base in _START_MENU_DIRS:
        if not base.exists():
            continue
        try:
            for lnk in base.rglob("*.lnk"):
                name = lnk.stem
                if any(w in name.lower() for w in _SKIP_WORDS):
                    continue
                entries.append({"name": name, "target": str(lnk), "kind": "lnk"})
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


def _score(name_lower: str, query_lower: str) -> int:
    if name_lower == query_lower:
        return 100
    if name_lower.startswith(query_lower):
        return 80
    if query_lower in name_lower:
        return 60
    return 0


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
        (
            max(_score(entry["name"].lower(), variant) for variant in query_variants),
            _score(entry["name"].lower(), q),
            entry,
        )
        for entry in (_system_tool_entries() + _wps_suite_entries())
    ]
    fast_scored = [item for item in fast_scored if item[0]]
    if fast_scored:
        # Preserve the user's localized/literal name when an alias has the
        # same launch target and score.  The returned name is also evidence
        # and a later title hint, so turning 画图 into "paint" is observable.
        fast_scored.sort(key=lambda item: (-item[0], -item[1]))
        return [entry for _score_value, _literal_score, entry in fast_scored[:max_results]]
    scored = []
    for e in _index():
        s = max(_score(e["name"].lower(), variant) for variant in query_variants)
        if s:
            # shortcuts are the most reliable launch targets; appid next
            s += {"lnk": 2, "appid": 1}.get(e["kind"], 0)
            scored.append((s, e))
    scored.sort(key=lambda t: -t[0])
    results = [e for _s, e in scored[:max_results]]
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
        }
    except OSError as exc:
        return {"ok": False, "error": str(exc), "target": target}
