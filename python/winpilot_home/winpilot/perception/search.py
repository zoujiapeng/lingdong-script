"""Fast local filename search with Everything and a bounded native fallback.

The bundled ``es.exe`` talks to an installed Everything instance and normally
returns in milliseconds. If Everything is unavailable, a time-bounded
``os.scandir`` walk returns partial results instead of blocking the agent on a
large recursive PowerShell scan.
"""
from __future__ import annotations

import fnmatch
import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from ..config import BIN_DIR, CONFIG
from ..utils.log import BUS, logger

_DEFAULT_MAX = 50
_DEFAULT_FALLBACK_TIMEOUT_S = 2.0
_EVERYTHING_IPC_TIMEOUT_MS = 500
_EVERYTHING_PROCESS_TIMEOUT_S = 1.0
_everything_client_tried = False
_warm_lock = threading.Lock()
_warm_thread: threading.Thread | None = None

_SORT_NAMES = {
    "name": "name",
    "path": "path",
    "date_modified": "date-modified",
    "size": "size",
}


def find_es() -> str | None:
    """Locate es.exe: config override, bundled binary, install dirs, PATH."""
    override = CONFIG.get("search", "everything_es_path", default="")
    candidates = [override] if override else []
    candidates.extend([
        str(BIN_DIR / "es.exe"),
        r"C:\Program Files\Everything\es.exe",
        r"C:\Program Files (x86)\Everything\es.exe",
    ])
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return str(candidate)
    return shutil.which("es") or shutil.which("es.exe")


def _find_everything_exe() -> str | None:
    configured = CONFIG.get("search", "everything_exe_path", default="")
    candidates = (
        configured,
        r"C:\Program Files\Everything\Everything.exe",
        r"C:\Program Files (x86)\Everything\Everything.exe",
        shutil.which("Everything.exe"),
    )
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return str(candidate)
    return None


def _ensure_everything_client() -> bool:
    """Start a user-session Everything client when only its service is alive."""
    global _everything_client_tried
    if _everything_client_tried:
        return False
    _everything_client_tried = True
    executable = _find_everything_exe()
    if not executable:
        return False
    try:
        subprocess.Popen(
            [executable, "-startup"],
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        logger.info("Started Everything user-session client for IPC")
        return True
    except Exception as exc:
        logger.warning("Cannot start Everything user-session client: %s", exc)
        return False


def _normalized_extensions(
    extensions: list[str] | tuple[str, ...] | None,
) -> list[str]:
    values: list[str] = []
    for raw in extensions or []:
        extension = str(raw or "").strip().casefold().lstrip(".*")
        valid = extension.replace("_", "").replace("-", "").isalnum()
        if extension and valid and extension not in values:
            values.append(extension)
    return values[:20]


def _run_es(
    executable: str,
    query: str,
    max_results: int,
    path: str | None,
    *,
    kind: str = "any",
    extensions: list[str] | None = None,
    sort: str = "path",
):
    arguments = [
        executable,
        "-timeout",
        str(_EVERYTHING_IPC_TIMEOUT_MS),
        "-n",
        str(max_results),
    ]
    if path:
        arguments += ["-path", path]
    if kind == "file":
        arguments.append("/a-d")
    elif kind == "folder":
        arguments.append("/ad")
    arguments += ["-sort", _SORT_NAMES.get(sort, "path")]

    # ES combines separate positional arguments with AND. A single argv value
    # containing spaces is treated as a phrase, so preserve explicit quoted
    # phrases and split normal terms before adding filters.
    query_terms = re.findall(r'"[^"]*"|\S+', str(query).strip())
    arguments.extend(query_terms or [str(query).strip()])
    extension_values = _normalized_extensions(extensions)
    if extension_values:
        arguments.append(f"ext:{';'.join(extension_values)}")
    return subprocess.run(
        arguments,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=_EVERYTHING_PROCESS_TIMEOUT_S,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def _search_everything(
    query: str,
    max_results: int,
    path: str | None,
    *,
    kind: str = "any",
    extensions: list[str] | None = None,
    sort: str = "path",
) -> dict[str, Any] | None:
    """Return Everything results, or ``None`` when ES is not installed."""
    executable = find_es()
    if not executable:
        return None
    try:
        result = _run_es(
            executable,
            query,
            max_results,
            path,
            kind=kind,
            extensions=extensions,
            sort=sort,
        )
        # ES return code 8 means the user-session IPC window is absent. Start
        # it for the next query, but do not block this query on process startup;
        # the bounded native scan can produce an immediate partial/full answer.
        if result.returncode == 8 and _ensure_everything_client():
            return {
                "engine": "everything-warming",
                "results": [],
                "count": 0,
                "hint": (
                    "Everything user-session IPC is starting. This query uses "
                    "the bounded local scan; a later query should use the index."
                ),
            }
    except Exception as exc:
        logger.warning("es.exe invocation failed: %s", exc)
        return {
            "engine": "everything-failed",
            "results": [],
            "count": 0,
            "es_error": str(exc)[:200],
            "hint": "Everything query failed; using the bounded local scan.",
        }

    if result.returncode != 0:
        logger.info("es.exe exit code %s: %s", result.returncode, result.stderr.strip()[:120])
        return {
            "engine": "everything-failed",
            "es_error": result.stderr.strip()[:200],
            "results": [],
            "count": 0,
            "hint": (
                "Everything IPC is unavailable. WinPilot tried to start the "
                "user-session client and will use a bounded local scan instead."
            ),
        }
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    return {
        "engine": "everything",
        "results": lines[:max_results],
        "count": len(lines[:max_results]),
    }


def warm_async() -> bool:
    """Prime Everything IPC in the background during WinPilot startup."""
    global _warm_thread
    if not find_es() or not _find_everything_exe():
        return False
    with _warm_lock:
        if _warm_thread is not None and _warm_thread.is_alive():
            return False

        def worker() -> None:
            _search_everything("__winpilot_ipc_probe__", 1, None)

        _warm_thread = threading.Thread(
            target=worker,
            daemon=True,
            name="winpilot-everything-warmup",
        )
        _warm_thread.start()
    return True


def _search_scandir(
    query: str,
    max_results: int,
    path: str | None,
    *,
    kind: str = "any",
    extensions: list[str] | None = None,
    sort: str = "path",
    timeout_s: float = _DEFAULT_FALLBACK_TIMEOUT_S,
) -> dict[str, Any]:
    """Bounded native fallback that reports whether its result is partial."""
    root = Path(path or Path.home()).expanduser()
    if not root.exists() or not root.is_dir():
        return {
            "engine": "scandir",
            "results": [],
            "count": 0,
            "scope": str(root),
            "error": "search path is not an existing directory",
        }

    pattern = query.casefold() if any(char in query for char in "*?") else f"*{query.casefold()}*"
    extension_values = set(_normalized_extensions(extensions))
    deadline = time.monotonic() + max(0.1, float(timeout_s))
    results: list[str] = []
    stack: list[Path] = [root]
    scanned_entries = 0
    timed_out = False
    priority_names = {"desktop": 4, "documents": 3, "downloads": 2, "pictures": 1}

    while stack and len(results) < max_results:
        if time.monotonic() >= deadline:
            timed_out = True
            break
        current = stack.pop()
        try:
            entries = list(os.scandir(current))
        except (OSError, PermissionError):
            continue
        children: list[Path] = []
        for entry in entries:
            scanned_entries += 1
            if time.monotonic() >= deadline:
                timed_out = True
                break
            try:
                is_directory = entry.is_dir(follow_symlinks=False)
                is_file = entry.is_file(follow_symlinks=False)
            except OSError:
                continue
            if is_directory and not entry.is_symlink():
                children.append(Path(entry.path))
            kind_matches = (
                kind == "any"
                or (kind == "file" and is_file)
                or (kind == "folder" and is_directory)
            )
            suffix = Path(entry.name).suffix.casefold().lstrip(".")
            extension_matches = not extension_values or (is_file and suffix in extension_values)
            if (
                kind_matches
                and extension_matches
                and fnmatch.fnmatch(entry.name.casefold(), pattern)
            ):
                results.append(str(Path(entry.path)))
                if len(results) >= max_results:
                    break
        children.sort(key=lambda item: priority_names.get(item.name.casefold(), 0))
        stack.extend(children)

    if sort == "name":
        results.sort(key=lambda value: Path(value).name.casefold())
    elif sort in {"date_modified", "size"}:
        def stat_key(value: str) -> float:
            try:
                stat = Path(value).stat()
                return stat.st_mtime if sort == "date_modified" else float(stat.st_size)
            except OSError:
                return -1.0

        results.sort(key=stat_key, reverse=True)
    else:
        results.sort(key=str.casefold)
    return {
        "engine": "scandir",
        "results": results[:max_results],
        "count": len(results[:max_results]),
        "scope": str(root),
        "partial": timed_out,
        "scanned_entries": scanned_entries,
    }


def search(
    query: str,
    max_results: int = _DEFAULT_MAX,
    path: str | None = None,
    *,
    kind: str = "any",
    extensions: list[str] | None = None,
    sort: str = "path",
) -> dict[str, Any]:
    """Search names using Everything first and a bounded local fallback."""
    started = time.monotonic()
    kind = kind if kind in {"any", "file", "folder"} else "any"
    sort = sort if sort in _SORT_NAMES else "path"
    max_results = max(1, min(500, int(max_results)))

    def finish(result: dict[str, Any]) -> dict[str, Any]:
        result["elapsed_ms"] = int((time.monotonic() - started) * 1000)
        result["query"] = str(query or "")
        result["kind"] = kind
        BUS.publish(
            "action",
            action="search_result",
            engine=result.get("engine", "none"),
            count=int(result.get("count") or 0),
            elapsed_ms=result["elapsed_ms"],
        )
        return result

    if not query or not query.strip():
        return finish({
            "engine": "none",
            "results": [],
            "count": 0,
            "error": "query is empty",
        })
    lowered = query.lower().strip()
    if not path and (".exe" in lowered or lowered.endswith("*.lnk")):
        return finish({
            "engine": "none",
            "results": [],
            "count": 0,
            "hint": (
                "search_files is for user files, not installed-app launch. "
                "Use find_app, find_program, or launch for applications."
            ),
        })

    BUS.publish("action", action="search_files", query=query, path=path or "")
    everything_result = _search_everything(
        query,
        max_results,
        path,
        kind=kind,
        extensions=extensions,
        sort=sort,
    )
    if everything_result is not None and everything_result.get("engine") == "everything":
        return finish(everything_result)

    fallback = _search_scandir(
        query,
        max_results,
        path,
        kind=kind,
        extensions=extensions,
        sort=sort,
    )
    if everything_result is not None and everything_result.get("hint"):
        fallback["everything_hint"] = everything_result["hint"]
    return finish(fallback)
