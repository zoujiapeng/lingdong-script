"""Resolve installed application display names to discovered launch targets.

Display names and executable aliases come from the machine's Start Menu,
Get-StartApps output, App Paths registry, and shortcut metadata.  No product
identity or system-tool launch target is embedded in this module.

Sources (merged, deduped, scored):
- Start Menu ``*.lnk`` (user + common) — shortcut filename IS the display name
- ``Get-StartApps`` (covers UWP/Store apps and most desktop apps)
- App Paths registry (classic exe aliases)
- Everything filename search as a last resort (*.exe)
"""
from __future__ import annotations

import json
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

_START_MENU_DIRS = [
    Path(os.environ.get("APPDATA", "")) / "Microsoft/Windows/Start Menu/Programs",
    Path(os.environ.get("PROGRAMDATA", "")) / "Microsoft/Windows/Start Menu/Programs",
]
_SKIP_WORDS = ("卸载", "uninstall", "帮助", "help", "网站", "website", "readme")


_LOOKS_LIKE_PATH_OR_ASSOCIATION = re.compile(
    r"[\\/:]|\.(exe|lnk|bat|cmd|url|txt|pdf|docx?|xlsx?|pptx?)$",
    re.IGNORECASE,
)

def _unique_text(values) -> list[str]:
    """Return non-empty strings once, preserving the strongest-first order."""

    seen: set[str] = set()
    result: list[str] = []
    for raw in values:
        value = str(raw or "").strip()
        key = value.casefold()
        if value and key not in seen:
            seen.add(key)
            result.append(value)
    return result


def _start_app_entry(record: dict) -> dict | None:
    """Convert one StartApps catalog record into a launch candidate."""

    name = str(record.get("name") or record.get("Name") or "").strip()
    app_id = str(
        record.get("app_id") or record.get("AppID") or record.get("appid") or ""
    ).strip()
    if not name or not app_id:
        return None
    entry = {
        "name": name,
        "target": app_id,
        "kind": "appid",
        "app_id": app_id,
    }
    package_family = str(
        record.get("package_family") or record.get("PackageFamily") or ""
    ).strip()
    if package_family:
        entry["package_family"] = package_family
    executable = os.path.expandvars(str(
        record.get("executable") or record.get("Executable") or ""
    ).strip().strip('"'))
    process_name = re.split(r"[\\/]", executable)[-1] if executable else ""
    if process_name.casefold().endswith(".exe"):
        entry.update({
            "executable": executable,
            "process_name": process_name,
            "aliases": _unique_text([
                process_name,
                process_name[:-4],
                executable,
            ]),
        })
    return entry


def _parse_start_apps_output(output: str) -> list[dict]:
    """Parse one JSON catalog snapshot into normalized candidates."""

    try:
        decoded = json.loads(str(output or "[]"))
    except (json.JSONDecodeError, TypeError):
        return []
    records = decoded if isinstance(decoded, list) else [decoded]
    return [
        entry
        for record in records
        if isinstance(record, dict)
        for entry in [_start_app_entry(record)]
        if entry is not None
    ]


def _merge_entry_metadata(primary: dict, secondary: dict) -> dict:
    """Merge two catalog observations for the same launch identity."""

    merged = dict(primary)
    for key, value in secondary.items():
        if key != "aliases" and value not in (None, "") and not merged.get(key):
            merged[key] = value
    process_name = str(merged.get("process_name") or "")
    merged["aliases"] = _unique_text([
        process_name,
        process_name[:-4] if process_name.casefold().endswith(".exe") else "",
        merged.get("executable"),
        *(primary.get("aliases") or []),
        *(secondary.get("aliases") or []),
        secondary.get("name"),
    ])
    return merged


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

    Some Store apps have no classic ``.lnk`` or executable path. Console output
    is forced to UTF-8 so localized display names remain intact.
    """
    script = r"""
$ErrorActionPreference = 'SilentlyContinue'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$shell = New-Object -ComObject Shell.Application
$appsFolder = $shell.Namespace('shell:AppsFolder')
$packageByFamily = @{}
@(Get-AppxPackage) | ForEach-Object {
    $packageByFamily[[string]$_.PackageFamilyName] = $_
}
$records = @(Get-StartApps | ForEach-Object {
    $appId = [string]$_.AppID
    $shellItem = $null
    try { $shellItem = $appsFolder.ParseName($appId) } catch {}
    $executable = ''
    $packageFamily = ''
    if ($shellItem) {
        $executable = [string]$shellItem.ExtendedProperty('System.Link.TargetParsingPath')
        $packageFamily = [string]$shellItem.ExtendedProperty('System.AppUserModel.PackageFamilyName')
    }
    if (-not $packageFamily -and $appId.Contains('!')) {
        $candidateFamily = $appId.Split('!')[0]
        if ($packageByFamily.ContainsKey($candidateFamily)) {
            $packageFamily = $candidateFamily
        }
    }
    $package = $null
    if ($packageFamily -and $packageByFamily.ContainsKey($packageFamily)) {
        $package = $packageByFamily[$packageFamily]
    }
    if (-not $executable -and $package -and $appId.Contains('!')) {
        $applicationId = $appId.Split('!')[-1]
        try {
            $manifest = Get-AppxPackageManifest -Package $package
            $manifestApp = @($manifest.Package.Applications.Application) |
                Where-Object { [string]$_.Id -eq $applicationId } |
                Select-Object -First 1
            $manifestExecutable = [Environment]::ExpandEnvironmentVariables(
                [string]$manifestApp.Executable
            )
            if ($manifestExecutable) {
                if ([IO.Path]::IsPathRooted($manifestExecutable)) {
                    $executable = $manifestExecutable
                } else {
                    $executable = Join-Path ([string]$package.InstallLocation) $manifestExecutable
                }
            }
        } catch {}
    }
    [pscustomobject]@{
        name = [string]$_.Name
        app_id = $appId
        executable = $executable
        package_family = $packageFamily
    }
})
ConvertTo-Json -InputObject $records -Compress -Depth 3
""".strip()
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=20, creationflags=subprocess.CREATE_NO_WINDOW,
        )
        return _parse_start_apps_output(out.stdout or "")
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


def _index(*, force_refresh: bool = False) -> list[dict]:
    global _cache
    with _cache_lock:
        if (
            not force_refresh
            and _cache is not None
            and time.monotonic() - _cache[0] < _CACHE_TTL_S
        ):
            return _cache[1]
    entries = (
        _start_menu_entries()
        + _uwp_entries()
        + _app_paths_entries()
    )
    # One physical launch identity may be reported by several sources/names;
    # conversely, two shortcuts with the same display name may target distinct
    # installs.  Deduplicate by normalized target and retain alternate names as
    # aliases instead of dropping a launchable target by (name, kind).
    seen: dict[str, int] = {}
    deduped: list[dict] = []
    for e in entries:
        raw_target = os.path.expandvars(str(e.get("target") or "").strip().strip('"'))
        key = os.path.normcase(os.path.normpath(raw_target)).casefold()
        if not key:
            continue
        existing_index = seen.get(key)
        if existing_index is not None:
            deduped[existing_index] = _merge_entry_metadata(
                deduped[existing_index], e,
            )
            continue
        copied = dict(e)
        if e.get("aliases"):
            copied["aliases"] = list(e.get("aliases") or [])
        seen[key] = len(deduped)
        deduped.append(copied)
    with _cache_lock:
        _cache = (time.monotonic(), deduped)
    logger.info("应用索引就绪: %d 条 (开始菜单+StartApps+App Paths)", len(deduped))
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


def resolve(
    query: str,
    max_results: int = 5,
    *,
    force_refresh: bool = False,
) -> list[dict]:
    """Installed-app candidates for a human name, best match first."""
    q = (query or "").strip().lower()
    if not q:
        return []
    from .app_aliases import expand as expand_app_aliases

    query_variants = list(dict.fromkeys(expand_app_aliases(q) or [q]))

    def candidates(entries: list[dict]) -> list[dict]:
        scored = []
        for entry in entries:
            score, literal_score, matched_term = _score_entry(
                entry, query_variants, q,
            )
            if score:
                # Shortcuts are the most reliable launch targets; AppID next.
                score += {"lnk": 2, "appid": 1}.get(entry["kind"], 0)
                scored.append((score, literal_score, matched_term, entry))
        scored.sort(key=lambda item: (
            -item[0],
            -item[1],
            str(item[3].get("name") or "").casefold(),
        ))
        return [
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
                    else (
                        "target_alias"
                        if _normalized(matched_term)
                        != _normalized(entry.get("name") or "")
                        else "literal_or_alias"
                    )
                ),
            }
            for score, _literal, matched_term, entry in scored[:max_results]
        ]

    with _cache_lock:
        had_cached_index = _cache is not None
    results = candidates(_index(force_refresh=force_refresh) if force_refresh else _index())
    if not results and had_cached_index and not force_refresh:
        # Install/uninstall and shortcut changes are common while the desktop
        # client is running.  A miss is cheap enough to justify one forced
        # multi-source refresh before declaring the app absent.
        results = candidates(_index(force_refresh=True))
    if results:
        return results
    # Last resort: search executable filenames without routing an app lookup
    # through the public ``*.exe`` file-search guard.  The structured filters
    # work for both Everything and the bounded native fallback.
    try:
        from . import search
        executable_query = re.sub(
            r"\.exe\s*$", "", str(query), flags=re.IGNORECASE,
        ).strip()
        hits = search.search(
            executable_query,
            max_results=max_results,
            kind="file",
            extensions=["exe"],
        )
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


def _activate_existing_instance(exe_path: str) -> dict | None:
    """Find windows owned by ``exe_path`` without guessing among peers.

    A non-``None`` result means an instance exists, even if Windows did not
    foreground it.  Callers must not start another instance in that case.
    """
    import win32gui

    try:
        norm = os.path.normcase(os.path.normpath(exe_path))
    except Exception:
        return None
    found_visible: list[int] = []
    found_hidden: list[int] = []
    try:
        win32gui.EnumWindows(
            lambda hwnd, _unused: (
                _collect_existing_window(
                    hwnd,
                    norm=norm,
                    visible=found_visible,
                    hidden=found_hidden,
                )
            )
            or True,
            None,
        )
    except Exception:
        return None
    return _existing_window_outcome(found_visible, found_hidden)


def _collect_existing_window(
    hwnd: int,
    *,
    norm: str,
    visible: list[int],
    hidden: list[int],
) -> bool:
    """Bucket one top-level window by visibility for a resolved exe path."""
    import win32gui
    import win32process

    try:
        is_visible = bool(
            win32gui.IsWindowVisible(hwnd) and not win32gui.IsIconic(hwnd)
        )
    except Exception:
        return True
    try:
        owner_pid = win32process.GetWindowThreadProcessId(hwnd)[1]
        owner_exe = os.path.normcase(
            os.path.normpath(_process_exe(owner_pid))
        )
    except Exception:
        return True
    if owner_exe != norm:
        return True
    if is_visible:
        visible.append(hwnd)
    else:
        hidden.append(hwnd)
    return True


def _activate_window(hwnd: int) -> int | None:
    """Bring one window to the foreground, working around the Windows
    foreground lock.

    A background process cannot normally ``SetForegroundWindow``: Windows
    restricts foreground changes to the process that owns the foreground or
    most recently received input.  ``AttachThreadInput`` temporarily joins
    the target window's input queue so the activation request is treated as
    if it came from that thread, which is the standard, mechanical way to
    raise a real application window from an automation process.  Falls back
    to the plain API when attachment is unavailable.
    """
    import ctypes

    import win32con
    import win32gui
    import win32process

    hwnd = int(hwnd)
    try:
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    except Exception:
        pass
    try:
        foreground = win32gui.GetForegroundWindow()
        target_thread = win32process.GetWindowThreadProcessId(hwnd)[0]
        attached = False
        if foreground and foreground != hwnd:
            try:
                current_thread = ctypes.windll.kernel32.GetCurrentThreadId()
                foreground_thread = win32process.GetWindowThreadProcessId(
                    foreground
                )[0]
                if ctypes.windll.user32.AttachThreadInput(
                    current_thread, foreground_thread, True
                ):
                    ctypes.windll.user32.AttachThreadInput(
                        current_thread, target_thread, True
                    )
                    attached = True
            except Exception:
                attached = False
        try:
            win32gui.SetForegroundWindow(hwnd)
            win32gui.BringWindowToTop(hwnd)
        except Exception:
            pass
        if attached:
            try:
                ctypes.windll.user32.AttachThreadInput(
                    current_thread, target_thread, False
                )
                ctypes.windll.user32.AttachThreadInput(
                    current_thread, foreground_thread, False
                )
            except Exception:
                pass
    except Exception:
        try:
            win32gui.SetForegroundWindow(hwnd)
        except Exception:
            pass
    return int(hwnd) if _foreground_root_hwnd() == _root_hwnd(hwnd) else None


def _root_hwnd(hwnd: int) -> int:
    """Return the top-level root for one hwnd."""
    import win32gui

    value = int(hwnd or 0)
    if not value:
        return 0
    get_ancestor = getattr(win32gui, "GetAncestor", None)
    if get_ancestor is None:
        return value
    try:
        return int(get_ancestor(value, 2) or value)  # GA_ROOT
    except Exception:
        return value


def _foreground_root_hwnd() -> int:
    import win32gui

    try:
        return _root_hwnd(int(win32gui.GetForegroundWindow() or 0))
    except Exception:
        return 0


def _window_candidate_facts(hwnds: list[int]) -> list[dict]:
    """Return a bounded hint for the model; identity comes from the process."""
    import win32gui

    facts: list[dict] = []
    for hwnd in hwnds[:3]:
        try:
            title = str(win32gui.GetWindowText(hwnd) or "").strip()[:120]
        except Exception:
            title = ""
        try:
            visible = bool(win32gui.IsWindowVisible(hwnd))
            minimized = bool(win32gui.IsIconic(hwnd))
        except Exception:
            visible = False
            minimized = False
        facts.append({
            "hwnd": int(hwnd),
            "title": title,
            "visible": visible,
            "minimized": minimized,
        })
    return facts


def _existing_window_outcome(
    visible: list[int],
    hidden: list[int],
) -> dict | None:
    """Choose only when Windows or uniqueness makes the choice unambiguous."""
    import win32con
    import win32gui

    candidates = list(dict.fromkeys([*visible, *hidden]))
    if not candidates:
        return None
    candidate_count = len(candidates)
    result = {
        "candidate_count": candidate_count,
        "candidates": _window_candidate_facts(candidates),
    }
    foreground_root = _foreground_root_hwnd()
    for hwnd in candidates:
        if foreground_root and _root_hwnd(hwnd) == foreground_root:
            result.update({
                "activated_hwnd": int(hwnd),
                "note": (
                    f"{candidate_count} existing window candidate(s); current "
                    "foreground is one; no new instance started"
                ),
            })
            return result

    if candidate_count > 1:
        hidden_candidates = set(hidden)
        if hidden_candidates:
            for hwnd in candidates:
                if hwnd not in hidden_candidates:
                    continue
                try:
                    win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
                except Exception:
                    pass
        result["note"] = (
            f"{candidate_count} existing window candidates; no new instance "
            "started; hidden candidates were asked to restore; re-observe "
            "the current screen and choose visually"
        )
        return result

    hwnd = candidates[0]
    if not visible:
        try:
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        except Exception:
            pass
    activated = _activate_window(hwnd)
    if activated:
        result.update({
            "activated_hwnd": int(activated),
            "note": "1 existing window activated; no new instance started",
        })
    else:
        result["note"] = (
            "1 existing window found but it did not become foreground; no new "
            "instance started; re-observe the current screen"
        )
    return result


def _window_process_name(hwnd: int) -> str:
    """Return the owning process's executable file name (lowercased), or ''."""
    try:
        import win32process

        pid = win32process.GetWindowThreadProcessId(int(hwnd))[1]
        return os.path.basename(_process_exe(pid)).casefold()
    except Exception:
        return ""


def _activate_existing_by_process_name(process_name: str) -> dict | None:
    """Activate a window owned by a running process file name.

    Process-name activation is stronger than title matching: it finds the
    real application window even when its title is a document/song/chat name
    ("江南 - 林俊杰") that never contains the display name ("网易云音乐").
    Visible windows are preferred; a tray-hidden/minimized window of the
    same process is restored and activated as a fallback.
    """
    import win32gui

    needle = str(process_name or "").strip().casefold()
    if not needle:
        return None
    if os.path.basename(needle) != needle:
        needle = os.path.basename(needle)
    found_visible: list[int] = []
    found_hidden: list[int] = []
    try:
        win32gui.EnumWindows(
            lambda hwnd, _unused: (
                _collect_existing_by_process(
                    hwnd,
                    needle=needle,
                    visible=found_visible,
                    hidden=found_hidden,
                )
            )
            or True,
            None,
        )
    except Exception:
        return None
    return _existing_window_outcome(found_visible, found_hidden)


def _collect_existing_by_process(
    hwnd: int,
    *,
    needle: str,
    visible: list[int],
    hidden: list[int],
) -> bool:
    """Bucket one top-level window by visibility for a process-name match."""
    import win32gui

    try:
        is_visible = bool(
            win32gui.IsWindowVisible(hwnd) and not win32gui.IsIconic(hwnd)
        )
    except Exception:
        return True
    try:
        owner_name = _window_process_name(hwnd)
    except Exception:
        return True
    if owner_name != needle:
        return True
    if is_visible:
        visible.append(hwnd)
    else:
        hidden.append(hwnd)
    return True


def _shortcut_target(lnk_path: str) -> str:
    """Resolve a .lnk shortcut to its target executable path, or ''."""
    try:
        import win32com.client  # noqa: PLC0415

        shell = win32com.client.Dispatch("WScript.Shell")
        shortcut = shell.CreateShortcut(str(lnk_path))
        return str(shortcut.TargetPath or "")
    except Exception:
        return ""


def _process_exe(pid: int) -> str:
    try:
        import ctypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid)
        )
        if not handle:
            return ""
        try:
            size = ctypes.c_ulong(32768)
            buf = ctypes.create_unicode_buffer(size.value)
            if not ctypes.windll.kernel32.QueryFullProcessImageNameW(
                handle, 0, buf, ctypes.byref(size)
            ):
                return ""
            return buf.value or ""
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    except Exception:
        return ""


def _activate_resolved_entry(entry: dict) -> dict | None:
    """Find an existing instance by executable identity, never window title."""
    kind = str(entry.get("kind") or "")
    target = str(entry.get("target") or "")
    candidates: list[str] = []
    if kind == "exe":
        candidates.append(target)
    elif kind == "lnk":
        candidates.append(_shortcut_target(target))
    for candidate in candidates:
        if candidate:
            existing = _activate_existing_instance(candidate)
            if existing is not None:
                return existing
    # Store/UWP apps resolve to an AppID whose on-disk exe lives behind a
    # WindowsApps VFS path that the running process may not report.  Match
    # the process file name instead, so a running "cloudmusic" window is
    # activated even though its title is a song name, not the display name.
    process_name = str(entry.get("process_name") or "")
    if process_name:
        existing = _activate_existing_by_process_name(process_name)
        if existing is not None:
            return existing
    return None


def _existing_launch_result(
    existing: dict,
    *,
    target: str,
    arguments: list[str],
    name: str,
    aliases: list[str],
) -> dict:
    """Project concise existing-instance facts into the launch result."""
    result = {
        "ok": True,
        "via": "existing",
        "target": target,
        "arguments": arguments,
        "name": name,
        "aliases": aliases,
        "candidate_count": int(existing.get("candidate_count") or 0),
        "candidates": list(existing.get("candidates") or [])[:3],
        "note": str(existing.get("note") or "existing instance found"),
    }
    activated_hwnd = existing.get("activated_hwnd")
    if activated_hwnd:
        result["activated_hwnd"] = int(activated_hwnd)
    return result


def launch_application(
    app: str,
    arguments: str = "",
    *,
    resolver: Callable[[str], list[dict]] | None = None,
) -> dict:
    """Launch one generic application without any per-app adapter.

    ``app`` may be a display name, executable alias, ``.lnk``/``.exe`` path,
    or a file/URL association.  The existing installed-app index resolves
    display names and aliases; an exact PATH executable (for example
    ``notepad`` or ``calc``) is resolved through ``shutil.which`` before the
    fuzzy index, because Windows system tools are not Start Menu entries;
    paths and associations fall back to ``os.startfile``.  Returns
    ``{ok, via, target, arguments}`` or ``{ok: False, error}``.
    """
    from collections.abc import Callable

    name = str(app or "").strip()
    if not name:
        return {"ok": False, "error": "application name must not be empty"}
    arguments_list = [str(arguments or "").strip()]
    arguments_list = [item for item in arguments_list if item]
    # An exact bare command name (for example ``notepad`` or ``calc``) is
    # resolved through PATH before the fuzzy installed-app index: Windows
    # system tools are not Start Menu entries, and a fuzzy match such as
    # "Notepad++" must not shadow the exact command the user named.
    path = (
        shutil.which(name)
        if not _LOOKS_LIKE_PATH_OR_ASSOCIATION.search(name)
        else None
    )
    # If the application is already running with a visible top-level window,
    # activate that window instead of starting a second instance.  A GUI
    # agent needs "bring the app to the front" semantics for "it is already
    # open", which a bare start cannot provide.
    if path:
        existing = _activate_existing_instance(path)
        if existing is not None:
            return _existing_launch_result(
                existing,
                target=path,
                arguments=arguments_list,
                name=name,
                aliases=[],
            )
    if path:
        try:
            if arguments_list:
                subprocess.Popen(
                    [path, *arguments_list],
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
            else:
                os.startfile(path)  # noqa: S606
            return {
                "ok": True,
                "via": "path",
                "target": path,
                "arguments": arguments_list,
                "name": name,
                "aliases": [],
            }
        except OSError as exc:
            return {"ok": False, "error": str(exc), "target": name}
    resolved = None
    if resolver is not None:
        try:
            resolved = resolver(name)
        except Exception as exc:
            return {"ok": False, "error": f"application resolution failed: {exc}"}
    else:
        try:
            resolved = resolve(name, max_results=1)
        except Exception as exc:
            return {"ok": False, "error": f"application resolution failed: {exc}"}
    if isinstance(resolved, list) and resolved:
        entry = dict(resolved[0])
        # Prefer activating an already-running instance over starting a new
        # one: a GUI agent needs "the app is already open, bring it to the
        # front" semantics for every display-name launch, not just bare PATH
        # commands.
        existing = _activate_resolved_entry(entry)
        if existing is not None:
            return _existing_launch_result(
                existing,
                target=str(entry.get("target") or ""),
                arguments=arguments_list,
                name=name,
                aliases=list(entry.get("aliases") or []),
            )
        if arguments_list:
            entry["arguments"] = arguments_list
        return launch_target(entry)
    try:
        os.startfile(name)  # noqa: S606
        return {
            "ok": True,
            "via": "startfile",
            "target": name,
            "arguments": arguments_list,
            "name": name,
            "aliases": [],
        }
    except OSError as exc:
        return {"ok": False, "error": str(exc), "target": name}
