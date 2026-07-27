#!/usr/bin/env python3
"""Run the immutable paired legacy/vNext canary through the visible Electron UI.

The campaign contract is sealed before any managed Electron, target server,
browser, or desktop input monitor starts.  All twelve declared samples then run
strictly serially and independently.  Ordinary failures are retained and do
not truncate the matrix; only an enumerated safety abort can stop it.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import ctypes
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import psutil
import win32api
import win32con
import win32gui
import win32process
from PIL import ImageGrab


WORKSPACE = Path(__file__).resolve().parents[1]
SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
import client_e2e_run as client_harness  # noqa: E402
import vnext_canary_campaign as campaign_state  # noqa: E402
import vnext_canary_input_hook as input_hook  # noqa: E402


TARGET_SERVER = (
    WORKSPACE
    / "python"
    / "winpilot_home"
    / "eval_targets"
    / "vnext_canary_server.py"
)
DEFAULT_OUTPUT = WORKSPACE / "output" / "playwright" / "vnext-live-canary-e2e"
CANARY_TASK = "Complete the task shown in the current visible window exactly as instructed."
RUNTIMES = ("legacy", "vnext")
VNEXT_MODEL_ROLES = frozenset({
    "vnext_visual_actor",
    "vnext_visual_obligation_verifier",
})
FROZEN_RUNTIME_BUDGET = {
    "max_turns": 30,
    "max_runtime_s": 300,
    "max_model_calls": 60,
}
MUTEX_NAME = "Local\\WinPilot-vNext-Minimal-Live-Canary-E2E"
WAIT_OBJECT_0 = 0x00000000
WAIT_ABANDONED = 0x00000080
ORACLE_SETTLE_SECONDS = 1.0
CAMPAIGN_BRANCH = "feat/vnext-minimal-live-canary"
C1_BRANCH = "refactor/vnext-live-runtime-core"
C2_BRANCH = "refactor/vnext-atomic-desktop-adapter"
C3_BRANCH = "refactor/vnext-live-model-roles"
MASTER_COMMIT = "1aeb7c4aa882e795512ce491ebd17a3b5c9223d7"
C1_HEAD = "629caa4bfa6fa24d08e252e26479566ade6244fb"
C2_HEAD = campaign_state.C2_HEAD
C3_HEAD = "8366fa7d305d94000e7cf99e51e3e31a2e6742fc"
C2_ACTIONS_RUN_ID = "30237790573"
CONFIG_PATH = WORKSPACE / "python" / "winpilot_home" / "config.json"
ELECTRON_EXE = WORKSPACE / "node_modules" / "electron" / "dist" / "electron.exe"
PACKAGE_LOCK = WORKSPACE / "package-lock.json"
DIST_MAIN = WORKSPACE / "dist" / "main"
DIST_RENDERER = WORKSPACE / "dist" / "renderer"
TARGET_HTML = TARGET_SERVER.with_name("vnext_canary_target.html")
ORACLE_VERSION = "independent-vnext-canary-browser-os-join/1"
ALLOWED_CONTROLLER_ACTIONS = frozenset(
    {"click", "double_click", "scroll", "type_text", "hotkey"}
)
SOURCE_IDENTITY_PATHS = (
    "scripts/client_e2e_run.py",
    "scripts/client_vnext_canary_e2e_run.py",
    "scripts/vnext_canary_campaign.py",
    "scripts/vnext_canary_input_hook.py",
    "python/winpilot_home/eval_targets/vnext_canary_server.py",
    "python/winpilot_home/eval_targets/vnext_canary_target.html",
    "python/winpilot_home/winpilot/orchestrator/vnext_live_canary.py",
)
C2_ADAPTER_PATH = "python/winpilot_home/winpilot/vnext/desktop_adapter/atomic.py"
C2_TEST_PATH = "python/winpilot_home/tests/test_vnext_atomic_desktop_adapter.py"
C2_EVIDENCE_TEST_NODES = (
    "test_applied_order_is_raw_then_wait_then_capture",
    "test_non_completed_raw_receipt_does_not_wait",
    "test_partial_double_click_guard_failure_is_unknown_and_not_replayed",
    "test_unknown_raw_result_is_never_replayed",
    "test_wait_exception_after_completed_input_is_unknown_without_replay",
    "test_zero_settle_delay_does_not_call_wait",
)
_SECRET_CONFIG_KEYS = frozenset(
    {
        "access_token",
        "api_key",
        "apikey",
        "auth_token",
        "authorization",
        "bearer",
        "client_secret",
        "password",
        "private_key",
        "refresh_token",
        "secret",
        "token",
    }
)

# Order alternates by seed but is fixed before any result exists.
CANARY_MATRIX: tuple[dict[str, Any], ...] = (
    {
        "case": "click-double-4101",
        "family": "click_double",
        "scenario": "click_double",
        "seed": 4101,
        "runtime_order": ("legacy", "vnext"),
    },
    {
        "case": "click-double-4102",
        "family": "click_double",
        "scenario": "click_double",
        "seed": 4102,
        "runtime_order": ("vnext", "legacy"),
    },
    {
        "case": "scroll-roundtrip-5201",
        "family": "scroll_roundtrip",
        "scenario": "scroll_roundtrip",
        "seed": 5201,
        "runtime_order": ("legacy", "vnext"),
    },
    {
        "case": "scroll-roundtrip-5202",
        "family": "scroll_roundtrip",
        "scenario": "scroll_roundtrip",
        "seed": 5202,
        "runtime_order": ("vnext", "legacy"),
    },
    {
        "case": "type-hotkey-6301",
        "family": "type_hotkey",
        "scenario": "type_hotkey",
        "seed": 6301,
        "runtime_order": ("legacy", "vnext"),
    },
    {
        "case": "type-hotkey-6302",
        "family": "type_hotkey",
        "scenario": "type_hotkey",
        "seed": 6302,
        "runtime_order": ("vnext", "legacy"),
    },
)


class DesktopSuiteMutex:
    """Non-waiting cross-process lock for the entire physical desktop suite."""

    def __init__(self, name: str = MUTEX_NAME) -> None:
        self.name = name
        self.handle = 0

    def __enter__(self) -> "DesktopSuiteMutex":
        if os.name != "nt":
            raise RuntimeError("the paired canary requires Windows")
        kernel32 = ctypes.windll.kernel32
        handle = int(kernel32.CreateMutexW(None, False, self.name) or 0)
        if not handle:
            raise OSError("CreateMutexW failed")
        outcome = int(kernel32.WaitForSingleObject(handle, 0))
        if outcome not in {WAIT_OBJECT_0, WAIT_ABANDONED}:
            kernel32.CloseHandle(handle)
            raise RuntimeError(
                "another physical desktop E2E suite owns the named mutex"
            )
        self.handle = handle
        return self

    def __exit__(self, _exc_type: Any, _exc: Any, _tb: Any) -> None:
        if self.handle:
            ctypes.windll.kernel32.ReleaseMutex(self.handle)
            ctypes.windll.kernel32.CloseHandle(self.handle)
            self.handle = 0


def _utc_now() -> str:
    return campaign_state.utc_now()


def _run_text(command: list[str], *, cwd: Path = WORKSPACE) -> str:
    completed = subprocess.run(
        command,
        cwd=str(cwd),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if completed.returncode:
        detail = (completed.stderr or completed.stdout).strip()
        raise RuntimeError(
            f"preflight command failed ({command[0]} exit "
            f"{completed.returncode}): {detail[:500]}"
        )
    return completed.stdout.strip()


def _run_bytes(command: list[str], *, cwd: Path = WORKSPACE) -> bytes:
    completed = subprocess.run(
        command,
        cwd=str(cwd),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(
            f"preflight command failed ({command[0]} exit "
            f"{completed.returncode}): {detail[:500]}"
        )
    return bytes(completed.stdout)


def _git(*args: str) -> str:
    return _run_text(["git", *args])


def _gh_json(*args: str) -> Any:
    raw = _run_text(["gh", *args])
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("GitHub CLI returned invalid JSON") from exc


def _repo_slug() -> str:
    remote = _git("remote", "get-url", "origin")
    match = re.search(
        r"(?:github\.com[/:])([^/\s:]+)/([^/\s]+?)(?:\.git)?$",
        remote,
        flags=re.IGNORECASE,
    )
    if not match:
        raise RuntimeError("origin is not a canonical GitHub repository URL")
    return f"{match.group(1)}/{match.group(2)}"


def _remote_ref(ref: str) -> str:
    rows = [
        line.split()
        for line in _git("ls-remote", "origin", ref).splitlines()
        if line.strip()
    ]
    matches = [row[0] for row in rows if len(row) == 2 and row[1] == ref]
    if len(matches) != 1 or not re.fullmatch(r"[0-9a-f]{40}", matches[0]):
        raise RuntimeError(f"remote ref is missing or ambiguous: {ref}")
    return matches[0]


def _pr_identity(repo: str, number: int) -> dict[str, Any]:
    value = _gh_json(
        "pr",
        "view",
        str(number),
        "--repo",
        repo,
        "--json",
        "number,state,isDraft,baseRefName,headRefName,headRefOid,url",
    )
    if not isinstance(value, dict) or value.get("number") != number:
        raise RuntimeError(f"GitHub PR #{number} identity is unavailable")
    if value.get("state") != "OPEN" or value.get("isDraft") is not True:
        raise RuntimeError(f"GitHub PR #{number} must remain open and draft")
    head = str(value.get("headRefOid") or "")
    if not re.fullmatch(r"[0-9a-f]{40}", head):
        raise RuntimeError(f"GitHub PR #{number} has no exact head")
    return value


def _c4_pr_identity(repo: str, head: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    candidates = _gh_json(
        "pr",
        "list",
        "--repo",
        repo,
        "--state",
        "open",
        "--head",
        CAMPAIGN_BRANCH,
        "--json",
        "number,state,isDraft,baseRefName,headRefName,headRefOid,url",
    )
    if not isinstance(candidates, list):
        raise RuntimeError("GitHub C4 PR lookup did not return an array")
    exact = [
        item
        for item in candidates
        if isinstance(item, dict)
        and item.get("state") == "OPEN"
        and item.get("isDraft") is True
        and item.get("baseRefName") == C3_BRANCH
        and item.get("headRefName") == CAMPAIGN_BRANCH
        and item.get("headRefOid") == head
    ]
    if len(exact) != 1:
        raise RuntimeError(
            "campaign requires exactly one open Draft C4 PR at the local HEAD"
        )
    pr = exact[0]
    raw_checks = _gh_json(
        "pr",
        "checks",
        str(pr["number"]),
        "--repo",
        repo,
        "--json",
        "name,state,bucket,startedAt,completedAt,link,workflow",
    )
    if not isinstance(raw_checks, list) or not raw_checks:
        raise RuntimeError("C4 PR has no fresh GitHub Actions checks")
    checks: list[dict[str, Any]] = []
    commit_time = datetime.fromisoformat(
        _git("show", "-s", "--format=%cI", head).replace("Z", "+00:00")
    ).astimezone(timezone.utc)
    for raw in raw_checks:
        if not isinstance(raw, dict):
            raise RuntimeError("C4 PR check payload is malformed")
        bucket = str(raw.get("bucket") or "").casefold()
        state = str(raw.get("state") or "").casefold()
        if bucket not in {"pass", "skipping"} and state not in {
            "success",
            "neutral",
            "skipped",
        }:
            raise RuntimeError(
                f"C4 PR check is not green: {raw.get('name')!r} "
                f"state={raw.get('state')!r} bucket={raw.get('bucket')!r}"
            )
        started_raw = str(raw.get("startedAt") or "")
        completed_raw = str(raw.get("completedAt") or "")
        if not started_raw or not completed_raw:
            raise RuntimeError(f"C4 PR check is not complete: {raw.get('name')!r}")
        started = datetime.fromisoformat(
            started_raw.replace("Z", "+00:00")
        ).astimezone(timezone.utc)
        if started < commit_time:
            raise RuntimeError(
                f"C4 PR check predates the exact campaign commit: "
                f"{raw.get('name')!r}"
            )
        checks.append(
            {
                "name": str(raw.get("name") or ""),
                "workflow": str(raw.get("workflow") or ""),
                "state": str(raw.get("state") or ""),
                "bucket": str(raw.get("bucket") or ""),
                "started_at": started_raw,
                "completed_at": completed_raw,
                "link": str(raw.get("link") or ""),
            }
        )
    checks.sort(key=lambda item: (item["workflow"], item["name"], item["link"]))
    return pr, checks


def _workspace_electron_processes() -> list[dict[str, Any]]:
    expected_executable = os.path.normcase(os.path.normpath(str(ELECTRON_EXE.resolve())))
    workspace_token = os.path.normcase(os.path.normpath(str(WORKSPACE.resolve())))
    matches: list[dict[str, Any]] = []
    for process in psutil.process_iter(["pid", "exe", "cmdline", "create_time"]):
        try:
            executable = os.path.normcase(
                os.path.normpath(str(process.info.get("exe") or ""))
            )
            command_line = [str(item) for item in (process.info.get("cmdline") or [])]
            normalized_command = [
                os.path.normcase(os.path.normpath(item)) for item in command_line
            ]
            workspace_bound = any(
                workspace_token in item for item in normalized_command
            )
            if executable == expected_executable and workspace_bound:
                matches.append(
                    {
                        "pid": int(process.info["pid"]),
                        "create_time": float(process.info.get("create_time") or 0),
                        "executable": str(process.info.get("exe") or ""),
                        "command_line": command_line,
                    }
                )
        except (psutil.Error, OSError, ValueError):
            continue
    return sorted(matches, key=lambda item: item["pid"])


def _write_config_bytes(data: bytes) -> None:
    temporary = CONFIG_PATH.with_suffix(CONFIG_PATH.suffix + ".campaign-tmp")
    if temporary.exists():
        raise RuntimeError(
            "stale campaign config temporary exists; refusing to overwrite it"
        )
    try:
        with temporary.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(CONFIG_PATH)
    finally:
        if temporary.exists():
            temporary.unlink()


def _fixed_config() -> tuple[bytes, dict[str, Any], dict[str, Any]]:
    if CONFIG_PATH.is_symlink() or not CONFIG_PATH.is_file():
        raise RuntimeError("campaign config.json is unavailable or is a symlink")
    original_bytes = CONFIG_PATH.read_bytes()
    try:
        original = json.loads(original_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("campaign config.json is not valid UTF-8 JSON") from exc
    if not isinstance(original, dict):
        raise RuntimeError("campaign config.json must be an object")
    patched = deepcopy(original)
    api = patched.setdefault("api", {})
    vlm = patched.setdefault("vlm", {})
    agent = patched.setdefault("agent", {})
    vnext = patched.setdefault("vnext", {})
    if not all(isinstance(item, dict) for item in (api, vlm, agent, vnext)):
        raise RuntimeError("campaign model/runtime config sections must be objects")
    api["max_retries"] = 0
    vnext.update({"live_canary_enabled": True, **FROZEN_RUNTIME_BUDGET})
    api_model = str(api.get("model") or "").strip()
    vlm_model = str(vlm.get("model") or "").strip()
    if not api_model or api_model != vlm_model:
        raise RuntimeError(
            "paired campaign requires one identical non-empty API/VLM model"
        )
    if str(agent.get("escalation_model") or "").strip():
        raise RuntimeError("paired campaign forbids a legacy escalation model")
    done_judge_model = str(api.get("done_judge_model") or "").strip()
    if done_judge_model and done_judge_model != api_model:
        raise RuntimeError("done judge must use the exact paired campaign model")
    if agent.get("llm_trace") is not True:
        raise RuntimeError("paired campaign requires agent.llm_trace=true")
    contract = campaign_state.redacted_config_contract(patched)
    patched_bytes = (
        json.dumps(
            patched,
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")
    _write_config_bytes(patched_bytes)
    observed = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    if observed != patched:
        _write_config_bytes(original_bytes)
        raise RuntimeError("fixed campaign configuration did not persist exactly")
    return original_bytes, patched, contract


def _restore_config_bytes(original_bytes: bytes) -> dict[str, Any]:
    errors: list[str] = []
    try:
        _write_config_bytes(original_bytes)
    except Exception as exc:
        errors.append(f"{type(exc).__name__}: {exc}")
    exact = False
    try:
        exact = CONFIG_PATH.read_bytes() == original_bytes
    except Exception as exc:
        errors.append(f"{type(exc).__name__}: {exc}")
    if not exact:
        errors.append("config.json was not restored byte-for-byte")
    return {"passed": exact and not errors, "errors": errors}


def _project_config_like(template: Any, observed: Any) -> Any:
    if isinstance(template, dict):
        if not isinstance(observed, dict):
            raise RuntimeError("runtime config changed an object into another type")
        projected: dict[str, Any] = {}
        for key, value in template.items():
            if str(key).casefold() in _SECRET_CONFIG_KEYS:
                continue
            if key not in observed:
                raise RuntimeError(f"runtime config omitted frozen key {key!r}")
            projected[str(key)] = _project_config_like(value, observed[key])
        return projected
    if isinstance(template, list):
        if not isinstance(observed, list) or len(template) != len(observed):
            raise RuntimeError("runtime config changed a frozen list")
        return [
            _project_config_like(expected, actual)
            for expected, actual in zip(template, observed, strict=True)
        ]
    if observed != template:
        raise RuntimeError("runtime config changed a frozen behavior value")
    return deepcopy(observed)


def _git_blob_sha256(commit: str, relative_path: str) -> str:
    data = _run_bytes(["git", "show", f"{commit}:{relative_path}"])
    return hashlib.sha256(data).hexdigest()


def _source_hashes() -> dict[str, str]:
    hashes: dict[str, str] = {}
    for relative in SOURCE_IDENTITY_PATHS:
        path = WORKSPACE / relative
        if path.is_symlink() or not path.is_file():
            raise RuntimeError(f"campaign source file is unavailable: {relative}")
        hashes[relative] = campaign_state.sha256_file(path)
    return hashes


def _build_identity(head: str) -> dict[str, Any]:
    for path, label in (
        (ELECTRON_EXE, "fixed Electron executable"),
        (PACKAGE_LOCK, "package-lock.json"),
    ):
        if path.is_symlink() or not path.is_file():
            raise RuntimeError(f"{label} is unavailable")
    for path, label in ((DIST_MAIN, "dist/main"), (DIST_RENDERER, "dist/renderer")):
        if path.is_symlink() or not path.is_dir():
            raise RuntimeError(f"{label} build output is unavailable")
    return {
        "electron": {
            "path": str(ELECTRON_EXE.resolve()),
            "sha256": campaign_state.sha256_file(ELECTRON_EXE),
        },
        "package_lock": {
            "path": str(PACKAGE_LOCK.relative_to(WORKSPACE)).replace("\\", "/"),
            "sha256": campaign_state.sha256_file(PACKAGE_LOCK),
        },
        "main": campaign_state.tree_identity(DIST_MAIN),
        "renderer": campaign_state.tree_identity(DIST_RENDERER),
        "runtime": {
            "expected_loaded_commit": head,
            "expected_current_commit": head,
        },
    }


def _preflight_stack() -> tuple[dict[str, Any], dict[str, Any]]:
    if _git("status", "--porcelain=v2", "--untracked-files=all"):
        raise RuntimeError("campaign requires a clean committed worktree")
    branch = _git("branch", "--show-current")
    if branch != CAMPAIGN_BRANCH:
        raise RuntimeError(
            f"campaign must run from {CAMPAIGN_BRANCH!r}, observed {branch!r}"
        )
    head = _git("rev-parse", "HEAD")
    if not re.fullmatch(r"[0-9a-f]{40}", head):
        raise RuntimeError("local C4 HEAD is invalid")
    if _git("merge-base", "--is-ancestor", C3_HEAD, head) != "":
        # merge-base --is-ancestor is silent on success; _git raises otherwise.
        raise RuntimeError("unexpected output from git merge-base")
    remote_c4 = _remote_ref(f"refs/heads/{CAMPAIGN_BRANCH}")
    if remote_c4 != head:
        raise RuntimeError("local C4 HEAD does not equal the remote branch HEAD")
    repo = _repo_slug()
    prior = {
        27: _pr_identity(repo, 27),
        28: _pr_identity(repo, 28),
        29: _pr_identity(repo, 29),
    }
    expected = {
        27: ("master", C1_BRANCH, C1_HEAD),
        28: (C1_BRANCH, C2_BRANCH, C2_HEAD),
        29: (C2_BRANCH, C3_BRANCH, C3_HEAD),
    }
    for number, (base, branch_name, expected_head) in expected.items():
        value = prior[number]
        if (
            value.get("baseRefName") != base
            or value.get("headRefName") != branch_name
            or value.get("headRefOid") != expected_head
            or _remote_ref(f"refs/heads/{branch_name}") != expected_head
        ):
            raise RuntimeError(f"stack PR #{number} no longer has its exact identity")
    if _remote_ref("refs/heads/master") != MASTER_COMMIT:
        raise RuntimeError("remote master moved from the declared stack base")
    c4_pr, checks = _c4_pr_identity(repo, head)
    merge_ref = _remote_ref(f"refs/pull/{c4_pr['number']}/merge")
    stack = {
        "master_commit": MASTER_COMMIT,
        "c1": {"pr": 27, "base": "master", "head": C1_HEAD},
        "c2": {"pr": 28, "base": C1_BRANCH, "head": C2_HEAD},
        "c3": {"pr": 29, "base": C2_BRANCH, "head": C3_HEAD},
        "c4": {
            "base": C3_BRANCH,
            "commit": head,
            "pr_head": head,
            "merge_ref": merge_ref,
        },
    }
    admission = {
        "repository": repo,
        "branch": branch,
        "c4_pr_number": int(c4_pr["number"]),
        "c4_pr_url": str(c4_pr.get("url") or ""),
        "c4_remote_head": remote_c4,
        "fresh_checks": checks,
    }
    return stack, admission


def _build_campaign_manifest(
    *,
    campaign_id: str,
    created_at: str,
    stack: dict[str, Any],
    admission: dict[str, Any],
    model_config: dict[str, Any],
) -> dict[str, Any]:
    head = str(stack["c4"]["commit"])
    source = _source_hashes()
    build = _build_identity(head)
    browser = _browser_executable()
    target_contract = {
        "mode": "fresh_prevalidated_prebound_target",
        "target_discovery": False,
        "target_activation_or_launch": False,
        "title_template": "WinPilot vNext Canary {case_id}",
        "browser": {
            "path": str(browser),
            "sha256": campaign_state.sha256_file(browser),
        },
        "eval_server_sha256": source[
            "python/winpilot_home/eval_targets/vnext_canary_server.py"
        ],
        "eval_target_sha256": source[
            "python/winpilot_home/eval_targets/vnext_canary_target.html"
        ],
        "managed_electron": {
            "argv": [str(ELECTRON_EXE.resolve()), "."],
            "cold_start_per_attempt": True,
            "preexisting_workspace_electron_forbidden": True,
        },
        "allowed_actions": sorted(ALLOWED_CONTROLLER_ACTIONS),
        "campaign_admission": admission,
    }
    c2_evidence = {
        "c2_head": C2_HEAD,
        "actions_run_id": C2_ACTIONS_RUN_ID,
        "code_sha256": _git_blob_sha256(C2_HEAD, C2_ADAPTER_PATH),
        "tests_sha256": _git_blob_sha256(C2_HEAD, C2_TEST_PATH),
        "test_nodes": sorted(C2_EVIDENCE_TEST_NODES),
    }
    return campaign_state.build_manifest(
        campaign_id=campaign_id,
        created_at=created_at,
        stack=stack,
        source_identity={
            "harness_code_commit": head,
            "files": source,
        },
        build_identity=build,
        model_config=model_config,
        matrix=CANARY_MATRIX,
        task=CANARY_TASK,
        target=target_contract,
        runtime_budget=FROZEN_RUNTIME_BUDGET,
        oracle_version=ORACLE_VERSION,
        c2_unknown_no_replay=c2_evidence,
    )


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _json_get(url: str, *, timeout: float = 3.0) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"Cache-Control": "no-store"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        value = json.load(response)
    if not isinstance(value, dict):
        raise RuntimeError(f"expected JSON object from {url}")
    return value


def _wait_json(url: str, predicate: Any, *, timeout: float) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        try:
            last = _json_get(url)
            if predicate(last):
                return last
        except Exception:
            pass
        time.sleep(0.15)
    raise RuntimeError(f"timed out waiting for fixed canary state; last={last}")


def _settled_external_state(
    url: str,
    *,
    timeout: float = 5.0,
    stable_seconds: float = ORACLE_SETTLE_SECONDS,
) -> tuple[dict[str, Any], dict[str, Any]]:
    deadline = time.monotonic() + timeout
    last: dict[str, Any] = {}
    last_count = -1
    stable_since: float | None = None
    terminal_seen = False
    polls = 0
    while time.monotonic() < deadline:
        polls += 1
        last = _json_get(url)
        events = last.get("events")
        if not isinstance(events, list):
            events = []
        now = time.monotonic()
        count = len(events)
        if count != last_count:
            last_count = count
            stable_since = now
        terminal_seen = any(
            isinstance(item, dict)
            and item.get("type") in {"scenario_success", "wrong_input"}
            for item in events
        )
        if (
            terminal_seen
            and stable_since is not None
            and now - stable_since >= stable_seconds
        ):
            return last, {
                "passed": True,
                "terminal_seen": True,
                "event_count": count,
                "stable_seconds": round(now - stable_since, 3),
                "required_stable_seconds": stable_seconds,
                "polls": polls,
            }
        time.sleep(0.1)
    return last, {
        "passed": False,
        "terminal_seen": terminal_seen,
        "event_count": last_count,
        "stable_seconds": (
            round(time.monotonic() - stable_since, 3)
            if stable_since is not None
            else 0.0
        ),
        "required_stable_seconds": stable_seconds,
        "polls": polls,
        "errors": ["external event stream did not reach a stable terminal snapshot"],
    }


def _browser_executable() -> Path:
    candidates = (
        Path(os.environ.get("PROGRAMFILES", "")) / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("PROGRAMFILES(X86)", "")) / "Microsoft/Edge/Application/msedge.exe",
        Path(os.environ.get("PROGRAMFILES", "")) / "Microsoft/Edge/Application/msedge.exe",
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise RuntimeError("the closed canary requires an installed Chrome or Edge")


def _find_window(title: str, *, timeout: float = 25.0) -> int:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        matches: list[int] = []

        def collect(hwnd: int, _extra: Any) -> bool:
            if (
                win32gui.IsWindowVisible(hwnd)
                and win32gui.GetWindowText(hwnd) == title
            ):
                matches.append(int(hwnd))
            return True

        win32gui.EnumWindows(collect, None)
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise RuntimeError(f"target title is not unique: {title!r}")
        time.sleep(0.15)
    raise RuntimeError(f"target window did not appear: {title!r}")


def _focus_window(hwnd: int) -> dict[str, Any]:
    errors: list[str] = []
    for attempt in range(1, 6):
        try:
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            win32api.keybd_event(win32con.VK_MENU, 0, 0, 0)
            win32gui.SetForegroundWindow(hwnd)
        except Exception as exc:
            errors.append(f"{type(exc).__name__}: {exc}")
        finally:
            win32api.keybd_event(
                win32con.VK_MENU,
                0,
                win32con.KEYEVENTF_KEYUP,
                0,
            )
        time.sleep(0.15)
        if int(win32gui.GetForegroundWindow() or 0) == int(hwnd):
            return {"focused": True, "attempt": attempt, "errors": errors}
    raise RuntimeError(f"could not focus exact target HWND {hwnd}; errors={errors}")


def _window_identity(hwnd: int) -> dict[str, Any]:
    if not hwnd or not win32gui.IsWindow(hwnd):
        raise RuntimeError("target HWND is no longer live")
    _thread_id, process_id = win32process.GetWindowThreadProcessId(hwnd)
    process = psutil.Process(int(process_id))
    root_hwnd = int(win32gui.GetAncestor(hwnd, win32con.GA_ROOT) or hwnd)
    return {
        "hwnd": int(hwnd),
        "root_hwnd": root_hwnd,
        "title": win32gui.GetWindowText(hwnd),
        "class_name": win32gui.GetClassName(hwnd),
        "process_id": int(process_id),
        "executable": str(Path(process.exe()).resolve()),
        "rect": list(map(int, win32gui.GetWindowRect(hwnd))),
    }


def _capture_window(hwnd: int, path: Path) -> str:
    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    path.parent.mkdir(parents=True, exist_ok=True)
    ImageGrab.grab(
        bbox=(left, top, right, bottom),
        all_screens=True,
    ).save(path)
    return str(path)


def _process_tree_identities(
    process: subprocess.Popen[Any] | None,
) -> list[dict[str, Any]]:
    if process is None:
        return []
    try:
        root = psutil.Process(process.pid)
        processes = [root, *root.children(recursive=True)]
    except psutil.Error:
        return []
    identities: dict[int, dict[str, Any]] = {}
    for item in processes:
        try:
            identities[item.pid] = {
                "pid": int(item.pid),
                "create_time": float(item.create_time()),
            }
        except psutil.Error:
            continue
    return [identities[pid] for pid in sorted(identities)]


def _terminate_tree(
    process: subprocess.Popen[Any] | None,
    *,
    known_identities: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    expected = {
        int(item["pid"]): float(item["create_time"])
        for item in (known_identities or [])
        if type(item) is dict
        and type(item.get("pid")) is int
        and isinstance(item.get("create_time"), (int, float))
    }
    for item in _process_tree_identities(process):
        expected[int(item["pid"])] = float(item["create_time"])
    errors: list[str] = []

    def live_expected() -> list[psutil.Process]:
        live: list[psutil.Process] = []
        for pid, create_time in expected.items():
            try:
                candidate = psutil.Process(pid)
                if abs(float(candidate.create_time()) - create_time) <= 0.001:
                    live.append(candidate)
            except psutil.NoSuchProcess:
                continue
            except psutil.Error as exc:
                errors.append(f"pid {pid} inspection failed: {type(exc).__name__}: {exc}")
        return live

    initial = live_expected()
    for item in reversed(initial):
        try:
            item.terminate()
        except psutil.NoSuchProcess:
            continue
        except psutil.Error as exc:
            errors.append(
                f"pid {item.pid} terminate failed: {type(exc).__name__}: {exc}"
            )
    if initial:
        _gone, alive = psutil.wait_procs(initial, timeout=4)
    else:
        alive = []
    for item in alive:
        try:
            item.kill()
        except psutil.NoSuchProcess:
            continue
        except psutil.Error as exc:
            errors.append(f"pid {item.pid} kill failed: {type(exc).__name__}: {exc}")
    if alive:
        psutil.wait_procs(alive, timeout=4)
    remaining = sorted(item.pid for item in live_expected())
    return {
        "passed": not errors and not remaining,
        "expected": [
            {"pid": pid, "create_time": expected[pid]}
            for pid in sorted(expected)
        ],
        "remaining_pids": remaining,
        "errors": errors,
    }


def _remove_profile(path: Path) -> dict[str, Any]:
    errors: list[str] = []
    try:
        if path.exists():
            shutil.rmtree(path)
    except Exception as exc:
        errors.append(f"{type(exc).__name__}: {exc}")
    remains = path.exists()
    if remains:
        errors.append("temporary browser profile still exists after cleanup")
    return {
        "passed": not errors and not remains,
        "path": str(path),
        "exists_after_cleanup": remains,
        "errors": errors,
    }


def _config_request(
    driver: client_harness.ElectronClientDriver,
    *,
    method: str = "GET",
    patch: dict[str, Any] | None = None,
) -> dict[str, Any]:
    data = (
        json.dumps(patch, ensure_ascii=False).encode("utf-8")
        if patch is not None
        else None
    )
    request = urllib.request.Request(
        f"{driver._runtime_url}/api/config",
        headers={"Authorization": f"Bearer {driver._runtime_token}"},
        data=data,
        method=method,
    )
    if data is not None:
        request.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(request, timeout=3) as response:
        value = json.load(response)
    if not isinstance(value, dict):
        raise RuntimeError("runtime config response is not an object")
    return value


def _authenticated_config(
    driver: client_harness.ElectronClientDriver,
    *,
    frozen_template: dict[str, Any] | None = None,
) -> dict[str, Any]:
    value = _config_request(driver)
    api = value.get("api") if isinstance(value.get("api"), dict) else {}
    vlm = value.get("vlm") if isinstance(value.get("vlm"), dict) else {}
    agent = value.get("agent") if isinstance(value.get("agent"), dict) else {}
    vnext = value.get("vnext") if isinstance(value.get("vnext"), dict) else {}
    api_model = str(api.get("model") or "").strip()
    vlm_model = str(vlm.get("model") or "").strip()
    if not api_model or api_model != vlm_model:
        raise RuntimeError(
            f"paired canary requires one identical API/VLM model: "
            f"api={api_model!r}, vlm={vlm_model!r}"
        )
    if str(agent.get("escalation_model") or "").strip():
        raise RuntimeError("paired canary forbids a legacy escalation model")
    done_judge_model = str(api.get("done_judge_model") or "").strip()
    if done_judge_model and done_judge_model != api_model:
        raise RuntimeError("paired canary requires the done judge to use the same model")
    if agent.get("llm_trace") is not True:
        raise RuntimeError("paired canary requires agent.llm_trace=true")
    if api.get("max_retries") != 0:
        raise RuntimeError("paired canary requires api.max_retries=0")
    if vnext.get("live_canary_enabled") is not True:
        raise RuntimeError("vnext.live_canary_enabled must be explicitly true")
    observed_budget = {
        name: vnext.get(name)
        for name in FROZEN_RUNTIME_BUDGET
    }
    if observed_budget != FROZEN_RUNTIME_BUDGET:
        raise RuntimeError(
            "vNext runtime budget is not the fixed paired-canary budget: "
            f"{observed_budget!r}"
        )
    projected = (
        _project_config_like(frozen_template, value)
        if frozen_template is not None
        else value
    )
    return campaign_state.redacted_config_contract(projected)


def _enable_fixed_canary(
    driver: client_harness.ElectronClientDriver,
) -> dict[str, Any]:
    before = _config_request(driver)
    original = {
        "vnext": (
            dict(before.get("vnext"))
            if isinstance(before.get("vnext"), dict)
            else {}
        ),
        "api": {
            "max_retries": (
                before.get("api", {}).get("max_retries", 2)
                if isinstance(before.get("api"), dict)
                else 2
            ),
        },
    }
    try:
        _config_request(
            driver,
            method="PATCH",
            patch={
                "vnext": {
                    "live_canary_enabled": True,
                    **FROZEN_RUNTIME_BUDGET,
                },
                "api": {"max_retries": 0},
            },
        )
    except Exception as exc:
        try:
            restored = _restore_canary_config(driver, original)
        except Exception as restore_exc:
            raise RuntimeError(
                "canary enable failed and original config could not be restored: "
                f"enable={type(exc).__name__}: {exc}; "
                f"restore={type(restore_exc).__name__}: {restore_exc}"
            ) from exc
        if restored.get("passed") is not True:
            raise RuntimeError(
                "canary enable failed and config restoration was not exact: "
                f"{restored!r}"
            ) from exc
        raise
    return original


def _restore_canary_config(
    driver: client_harness.ElectronClientDriver,
    original: dict[str, Any],
) -> dict[str, Any]:
    original_vnext = (
        original.get("vnext")
        if isinstance(original.get("vnext"), dict)
        else {}
    )
    original_api = (
        original.get("api")
        if isinstance(original.get("api"), dict)
        else {}
    )
    restored = {
        "live_canary_enabled": (
            original_vnext.get("live_canary_enabled") is True
        ),
        **{
            name: original_vnext.get(name, value)
            for name, value in FROZEN_RUNTIME_BUDGET.items()
        },
    }
    restored_api = {
        "max_retries": original_api.get("max_retries", 2),
    }
    _config_request(
        driver,
        method="PATCH",
        patch={"vnext": restored, "api": restored_api},
    )
    observed = _config_request(driver)
    current = (
        observed.get("vnext")
        if isinstance(observed.get("vnext"), dict)
        else {}
    )
    current_api = (
        observed.get("api")
        if isinstance(observed.get("api"), dict)
        else {}
    )
    return {
        "passed": (
            all(current.get(key) == value for key, value in restored.items())
            and current_api.get("max_retries") == restored_api["max_retries"]
        ),
        "restored": restored,
        "restored_api": restored_api,
        "observed": {
            key: current.get(key)
            for key in restored
        },
        "observed_api": {
            "max_retries": current_api.get("max_retries"),
        },
    }


def _runtime_status_payload(
    driver: client_harness.ElectronClientDriver,
) -> dict[str, Any]:
    request = urllib.request.Request(
        f"{driver._runtime_url}/api/status",
        headers={"Authorization": f"Bearer {driver._runtime_token}"},
    )
    with urllib.request.urlopen(request, timeout=3) as response:
        value = json.load(response)
    if not isinstance(value, dict):
        raise RuntimeError("runtime status response is not an object")
    return value


def _verify_runtime_build(
    status: dict[str, Any],
    *,
    expected_commit: str,
) -> dict[str, Any]:
    runtime = status.get("runtime")
    runtime = runtime if isinstance(runtime, dict) else {}
    git = runtime.get("git")
    git = git if isinstance(git, dict) else {}
    loaded = str(git.get("loaded_commit") or "")
    current = str(git.get("current_commit") or "")
    dirty = git.get("dirty")
    errors: list[str] = []
    if loaded != expected_commit:
        errors.append("runtime loaded_commit differs from the manifest C4 commit")
    if current != expected_commit:
        errors.append("runtime current_commit differs from the manifest C4 commit")
    if dirty is not False:
        errors.append("runtime reports a dirty or indeterminate source tree")
    return {
        "passed": not errors,
        "expected_commit": expected_commit,
        "loaded_commit": loaded,
        "current_commit": current,
        "dirty": dirty,
        "branch": str(git.get("branch") or ""),
        "errors": errors,
    }


def _start_managed_electron(
    work_dir: Path,
    *,
    expected_commit: str,
    manifest_sealed_time_ns: int,
) -> tuple[
    subprocess.Popen[Any],
    client_harness.ElectronClientDriver,
    list[dict[str, Any]],
    dict[str, Any],
    Any,
]:
    if _workspace_electron_processes():
        raise RuntimeError("a workspace Electron appeared after manifest sealing")
    log_path = work_dir / "electron.log"
    log_stream = log_path.open("xb")
    environment = dict(os.environ)
    environment["ELECTRON_DISABLE_SECURITY_WARNINGS"] = "true"
    creation_flags = (
        int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
        if os.name == "nt"
        else 0
    )
    try:
        process = subprocess.Popen(
            [str(ELECTRON_EXE.resolve()), "."],
            cwd=str(WORKSPACE),
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=log_stream,
            stderr=subprocess.STDOUT,
            creationflags=creation_flags,
        )
    except Exception:
        log_stream.close()
        raise
    deadline = time.monotonic() + 150
    last_error = ""
    driver: client_harness.ElectronClientDriver | None = None
    while time.monotonic() < deadline:
        if process.poll() is not None:
            break
        try:
            candidate = client_harness.ElectronClientDriver(
                work_dir / "electron-bootstrap"
            )
            if candidate.identity.pid != process.pid:
                raise RuntimeError(
                    "visible Electron PID is not the managed cold-start PID"
                )
            driver = candidate
            break
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            time.sleep(0.25)
    if driver is None:
        identities = _process_tree_identities(process)
        cleanup = _terminate_tree(process, known_identities=identities)
        log_stream.close()
        raise RuntimeError(
            "managed Electron did not expose the visible fixed client: "
            f"{last_error}; cleanup={cleanup!r}"
        )
    process_identity = psutil.Process(process.pid)
    create_time_ns = int(float(process_identity.create_time()) * 1_000_000_000)
    if create_time_ns <= manifest_sealed_time_ns:
        identities = _process_tree_identities(process)
        cleanup = _terminate_tree(process, known_identities=identities)
        log_stream.close()
        raise RuntimeError(
            "managed Electron creation time does not follow manifest sealing: "
            f"cleanup={cleanup!r}"
        )
    identities = _process_tree_identities(process)
    if process.pid not in {item["pid"] for item in identities}:
        raise RuntimeError("managed Electron process tree identity is unavailable")
    status = _runtime_status_payload(driver)
    build_audit = _verify_runtime_build(status, expected_commit=expected_commit)
    if build_audit["passed"] is not True:
        cleanup = _terminate_tree(process, known_identities=identities)
        log_stream.close()
        raise RuntimeError(
            f"managed Electron runtime build is not exact: "
            f"{build_audit['errors']!r}; cleanup={cleanup!r}"
        )
    startup = {
        "process": {
            "pid": process.pid,
            "create_time": float(process_identity.create_time()),
            "executable": str(ELECTRON_EXE.resolve()),
            "command_line": [str(ELECTRON_EXE.resolve()), "."],
        },
        "visible_client": {
            "pid": driver.identity.pid,
            "hwnd": driver.identity.hwnd,
            "executable": driver.identity.executable,
            "command_line": driver.identity.command_line,
            "workspace_verified": driver.identity.workspace_verified,
        },
        "runtime_build": build_audit,
        "manifest_sealed_time_ns": manifest_sealed_time_ns,
        "process_create_time_ns": create_time_ns,
    }
    return process, driver, identities, startup, log_stream


def _stable_artifact_path(
    sample: dict[str, Any],
    attempt: int,
    suffix: str,
) -> str:
    case = re.sub(r"[^a-z0-9_-]+", "-", str(sample["case"]).casefold()).strip("-")
    runtime = str(sample["runtime"])
    return (
        f"samples/{int(sample['execution_order']):03d}-{case}-{runtime}/"
        f"attempt-{attempt}/{suffix}"
    )


def _seal_work_tree(
    campaign_dir: Path,
    sample: dict[str, Any],
    attempt: int,
    work_dir: Path,
) -> list[dict[str, Any]]:
    identities: list[dict[str, Any]] = []
    files = [
        path
        for path in sorted(work_dir.rglob("*"))
        if path.is_file() and not path.is_symlink()
    ]
    for index, path in enumerate(files, start=1):
        relative = path.relative_to(work_dir)
        safe_name = "-".join(relative.parts)
        safe_name = re.sub(r"[^A-Za-z0-9._-]+", "-", safe_name).strip("-")
        suffix = f"raw/{index:04d}-{safe_name or 'artifact'}"
        identities.append(
            campaign_state.write_immutable_artifact(
                campaign_dir,
                _stable_artifact_path(sample, attempt, suffix),
                path.read_bytes(),
                kind="raw_artifact",
            )
        )
    return identities


def _seal_value(
    campaign_dir: Path,
    sample: dict[str, Any],
    attempt: int,
    name: str,
    value: Any,
    *,
    kind: str,
) -> dict[str, Any]:
    return campaign_state.write_immutable_artifact(
        campaign_dir,
        _stable_artifact_path(sample, attempt, name),
        value,
        kind=kind,
    )


def _load_trace_events(client_result: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    path = Path(str((client_result.get("trace_binding") or {}).get("trace_file") or ""))
    if not path.is_file():
        return [], str(path)
    events: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        value = json.loads(line)
        if isinstance(value, dict):
            events.append(value)
    return events, str(path)


def _event_model(event: dict[str, Any]) -> str:
    direct = str(event.get("model") or "").strip()
    if direct:
        return direct
    request = event.get("request")
    if isinstance(request, dict):
        return str(request.get("model") or "").strip()
    return ""


def _forensic_json(directory: Path, pattern: str) -> tuple[list[dict[str, Any]], list[str]]:
    values: list[dict[str, Any]] = []
    errors: list[str] = []
    for path in sorted(directory.glob(pattern)):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(value, dict):
                raise ValueError("payload is not an object")
            values.append(value)
        except Exception as exc:
            errors.append(f"{path.name}: {type(exc).__name__}: {exc}")
    return values, errors


def _receipt_value(event: dict[str, Any], key: str) -> Any:
    metadata = event.get("metadata")
    if isinstance(metadata, dict) and key in metadata:
        return metadata.get(key)
    if key in event:
        return event.get(key)
    receipt = event.get("receipt")
    if isinstance(receipt, dict) and key in receipt:
        return receipt.get(key)
    envelope = event.get("envelope")
    if isinstance(envelope, dict):
        if key in envelope:
            return envelope.get(key)
        proposal = envelope.get("proposal")
        if isinstance(proposal, dict) and key in proposal:
            return proposal.get(key)
    return None


def _expected_target_trace(identity: dict[str, Any]) -> dict[str, Any]:
    rect = identity.get("rect")
    if (
        type(rect) is not list
        or len(rect) != 4
        or any(type(item) is not int for item in rect)
    ):
        raise ValueError("target identity has no exact Win32 rectangle")
    executable = os.path.normcase(
        os.path.normpath(str(identity.get("executable") or ""))
    )
    return {
        "hwnd": int(identity.get("hwnd") or 0),
        "root_hwnd": int(identity.get("root_hwnd") or 0),
        "process_id": int(identity.get("process_id") or 0),
        "class_name": str(identity.get("class_name") or ""),
        "region": [rect[0], rect[1], rect[2] - rect[0], rect[3] - rect[1]],
        "executable_identity_sha256": hashlib.sha256(
            executable.encode("utf-8")
        ).hexdigest(),
    }


def _trace_audit(
    client_result: dict[str, Any],
    *,
    runtime_version: str,
    expected_model: str,
    target_identity: dict[str, Any],
    family: str,
) -> dict[str, Any]:
    events, trace_file = _load_trace_events(client_result)
    errors: list[str] = []
    visible_result = (
        client_result.get("result")
        if isinstance(client_result.get("result"), dict)
        else {}
    )
    visible_run_id = str(visible_result.get("run_id") or "")
    visible_runtime = (
        visible_result.get("runtime")
        if isinstance(visible_result.get("runtime"), dict)
        else {}
    )
    visible_projection = (
        visible_runtime.get("current_run")
        if isinstance(visible_runtime.get("current_run"), dict)
        else {}
    )
    expected_target = _expected_target_trace(target_identity)
    target_hwnd = int(expected_target["hwnd"])
    starts = [
        item for item in events
        if item.get("type") == "arp_run_start"
        and (
            str(item.get("graph_name") or "") == CANARY_TASK
            or str((item.get("task_contract") or {}).get("task") or "")
            == CANARY_TASK
        )
    ]
    if len(starts) != 1:
        errors.append(f"expected one exact arp_run_start, observed {len(starts)}")
        start: dict[str, Any] = {}
    else:
        start = starts[0]
        if start.get("runtime_version") != runtime_version:
            errors.append("outer arp_run_start runtime_version mismatch")
    if not visible_run_id:
        errors.append("visible client did not bind a run id")
    if runtime_version == "vnext":
        if str(start.get("run_id") or "") != visible_run_id:
            errors.append("vNext outer run id is not the visible client run id")
        if (
            visible_projection.get("runtime_version") != "vnext"
            or visible_projection.get("no_legacy_fallback") is not True
        ):
            errors.append(
                "visible vNext projection does not prove the frozen runtime "
                "and no legacy fallback"
            )
    else:
        legacy_child = [
            item for item in events
            if item.get("type") == "agent_start"
            and str(item.get("run_id") or "") == visible_run_id
            and str(item.get("task") or "") == CANARY_TASK
        ]
        if str(start.get("run_id") or "") != visible_run_id and len(legacy_child) != 1:
            errors.append("legacy outer/child run binding is not exact")

    event_types = [str(item.get("type") or "") for item in events]
    frozen: dict[str, Any] = {}
    target_binding_id = ""
    mission_id = ""
    if runtime_version == "vnext":
        forbidden = [
            event_type for event_type in event_types
            if event_type.startswith("agent_")
            or "task_stage" in event_type
            or "fallback" in event_type
        ]
        forbidden.extend(
            str(item.get("type") or "")
            for item in events
            if (
                item.get("node_kind") == "task_stage"
                or (
                    isinstance(item.get("node"), dict)
                    and item["node"].get("kind") == "task_stage"
                )
                or item.get("legacy_fallback") is True
                or item.get("fallback_to_legacy") is True
            )
        )
        if forbidden:
            errors.append(
                "vNext trace contains legacy/task-stage/fallback events: "
                f"{forbidden!r}"
            )
        frozen_events = [
            item for item in events
            if item.get("type") == "vnext_canary_start"
            and isinstance(item.get("runtime_budget"), dict)
        ]
        if len(frozen_events) != 1:
            errors.append(
                f"expected one vNext frozen budget event, observed {len(frozen_events)}"
            )
        else:
            frozen = frozen_events[0]
            mission_id = str(frozen.get("mission_id") or "")
            if frozen.get("runtime_budget") != FROZEN_RUNTIME_BUDGET:
                errors.append("vNext frozen runtime budget mismatch")
            if frozen.get("no_legacy_fallback") is not True:
                errors.append("vNext start does not prove no_legacy_fallback=true")
            if str(frozen.get("run_id") or "") != visible_run_id:
                errors.append("vNext frozen event run id mismatch")
            if frozen.get("model") != expected_model:
                errors.append("vNext frozen start model differs from paired model")
            frozen_target = (
                frozen.get("target")
                if isinstance(frozen.get("target"), dict)
                else {}
            )
            target_binding_id = str(frozen_target.get("binding_id") or "")
            if not target_binding_id:
                errors.append("vNext frozen target binding id is missing")
            for key, expected in expected_target.items():
                if frozen_target.get(key) != expected:
                    errors.append(f"vNext frozen target identity mismatch: {key}")
    else:
        unexpected = [
            event_type for event_type in event_types
            if event_type.startswith("vnext_")
        ]
        if unexpected:
            errors.append(f"legacy trace contains vNext events: {unexpected!r}")

    model_event_types = (
        {"vnext_model_call"}
        if runtime_version == "vnext"
        else {"agent_decision", "agent_model_call"}
    )
    model_events = [
        item for item in events if item.get("type") in model_event_types
    ]
    models = [_event_model(item) for item in model_events]
    if not model_events:
        errors.append(
            "no model-call trace evidence for "
            + "/".join(sorted(model_event_types))
        )
    if runtime_version == "legacy" and not any(
        item.get("type") == "agent_decision" for item in model_events
    ):
        errors.append("legacy main agent_decision model evidence is missing")
    if any(str(item.get("run_id") or "") != visible_run_id for item in model_events):
        errors.append("model-call trace contains a different run id")
    if any(model != expected_model for model in models):
        errors.append(f"model trace differs from frozen model: {models!r}")
    if len(model_events) > FROZEN_RUNTIME_BUDGET["max_model_calls"]:
        errors.append("model-call ceiling exceeded")
    forensic_summary: dict[str, Any] = {}
    if runtime_version == "vnext":
        roles = [str(item.get("model_role") or "") for item in model_events]
        if (
            any(role not in VNEXT_MODEL_ROLES for role in roles)
            or not VNEXT_MODEL_ROLES.issubset(roles)
        ):
            errors.append(f"vNext model role trace is not exact and independent: {roles!r}")
        if any(str(item.get("mission_id") or "") != mission_id for item in model_events):
            errors.append("vNext model call mission binding mismatch")
    else:
        main_events = [
            item for item in model_events if item.get("type") == "agent_decision"
        ]
        auxiliary_events = [
            item for item in model_events if item.get("type") == "agent_model_call"
        ]
        if len(main_events) > FROZEN_RUNTIME_BUDGET["max_turns"]:
            errors.append("legacy main model-call ceiling exceeded")
        if any(item.get("accounted") is not True for item in auxiliary_events):
            errors.append("legacy auxiliary model call is not accounted")
        legacy_starts = [
            item for item in events
            if item.get("type") == "agent_start"
            and str(item.get("run_id") or "") == visible_run_id
        ]
        if len(legacy_starts) != 1 or legacy_starts[0].get("max_steps") != 30:
            errors.append("legacy agent start does not prove the same 30-turn budget")
        forensic_dir = client_harness.TRACE_ROOT / visible_run_id
        requests, request_errors = _forensic_json(
            forensic_dir,
            "*_llm_request.json",
        )
        attempts, attempt_errors = _forensic_json(
            forensic_dir,
            "*_llm_request_attempt.json",
        )
        done_requests, done_errors = _forensic_json(
            forensic_dir,
            "*_done_check_request.json",
        )
        forensic_errors = [*request_errors, *attempt_errors, *done_errors]
        if not forensic_dir.is_dir():
            forensic_errors.append("legacy child forensic directory is missing")
        if len(requests) != len(main_events) or len(attempts) != len(main_events):
            forensic_errors.append(
                "legacy main request/attempt files do not match agent_decision count"
            )
        if any(
            str(item.get("run_id") or "") != visible_run_id
            or item.get("model") != expected_model
            or item.get("max_attempts") != 1
            for item in requests
        ):
            forensic_errors.append(
                "legacy request forensic model/run/max_attempts is not exact"
            )
        if any(
            str(item.get("run_id") or "") != visible_run_id
            or item.get("attempt") != 1
            or item.get("retry_kind") != "initial_request"
            for item in attempts
        ):
            forensic_errors.append(
                "legacy provider attempt forensic contains retry or foreign binding"
            )
        done_judge_events = [
            item for item in auxiliary_events
            if item.get("purpose") == "done_judge"
        ]
        if len(done_requests) != len(done_judge_events) or any(
            str(item.get("run_id") or "") != visible_run_id
            or item.get("model") != expected_model
            for item in done_requests
        ):
            forensic_errors.append(
                "legacy done-judge request forensic is not exact"
            )
        if forensic_errors:
            errors.extend(forensic_errors)
        forensic_summary = {
            "directory": str(forensic_dir),
            "main_request_count": len(requests),
            "main_provider_attempt_count": len(attempts),
            "done_judge_request_count": len(done_requests),
            "errors": forensic_errors,
        }

    receipt_summary: dict[str, Any] = {
        "count": 0,
        "controller_actions": [],
        "decision_ids": [],
        "target_binding_ids": [],
    }
    done_events: list[dict[str, Any]] = []
    turn_events: list[dict[str, Any]] = []
    if runtime_version == "vnext":
        receipts = [
            item for item in events
            if item.get("type") == "vnext_raw_receipt"
        ]
        actions = [str(_receipt_value(item, "controller_action") or "") for item in receipts]
        decisions = [str(_receipt_value(item, "decision_id") or "") for item in receipts]
        bindings = [str(_receipt_value(item, "target_binding_id") or "") for item in receipts]
        outcomes = [str(_receipt_value(item, "outcome") or "") for item in receipts]
        run_ids = [str(_receipt_value(item, "run_id") or "") for item in receipts]
        hwnds = [int(_receipt_value(item, "target_hwnd") or 0) for item in receipts]
        receipt_summary = {
            "count": len(receipts),
            "controller_actions": actions,
            "decision_ids": decisions,
            "target_binding_ids": bindings,
            "outcomes": outcomes,
            "run_ids": run_ids,
            "target_hwnds": hwnds,
        }
        if not receipts:
            errors.append("vNext raw receipt trace evidence is missing")
        if (
            any(not item for item in decisions)
            or len(decisions) != len(set(decisions))
        ):
            errors.append("vNext receipt decision ids are missing or duplicated")
        if any(item != target_binding_id for item in bindings):
            errors.append("vNext receipt target binding is not the frozen binding")
        if any(item != visible_run_id for item in run_ids):
            errors.append("vNext receipt run binding mismatch")
        if any(item != target_hwnd for item in hwnds):
            errors.append("vNext receipt target HWND mismatch")
        if any(item != "applied" for item in outcomes):
            errors.append(f"vNext receipt contains non-applied outcomes: {outcomes!r}")
        if family == "click_double":
            if (
                actions.count("click") != 1
                or actions.count("double_click") != 1
                or len(actions) != 2
            ):
                errors.append(f"click/double receipt sequence is not exact: {actions!r}")
        elif family == "scroll_roundtrip":
            if (
                len(actions) < 2
                or len(actions) > FROZEN_RUNTIME_BUDGET["max_turns"]
                or any(item != "scroll" for item in actions)
            ):
                errors.append(
                    f"scroll receipts are not a bounded scroll-only sequence: {actions!r}"
                )
        elif family == "type_hotkey":
            if (
                actions.count("click") != 1
                or actions.count("type_text") != 1
                or actions.count("hotkey") != 2
                or len(actions) != 4
            ):
                errors.append(f"type/hotkey receipt sequence is not exact: {actions!r}")

        turn_events = [
            item for item in events if item.get("type") == "vnext_canary_turn"
        ]
        turn_indices = [item.get("turn_index") for item in turn_events]
        if turn_indices != list(range(len(turn_events))):
            errors.append(f"vNext turn indices are not contiguous: {turn_indices!r}")
        if any(
            str(item.get("run_id") or "") != visible_run_id
            or str(item.get("mission_id") or "") != mission_id
            or item.get("no_legacy_fallback") is not True
            for item in turn_events
        ):
            errors.append("vNext turn run/mission/fallback binding mismatch")
        done_events = [
            item for item in events if item.get("type") == "vnext_canary_done"
        ]
        if len(done_events) != 1:
            errors.append(
                f"expected one vNext terminal event, observed {len(done_events)}"
            )
        else:
            done = done_events[0]
            if (
                str(done.get("run_id") or "") != visible_run_id
                or str(done.get("mission_id") or "") != mission_id
                or done.get("runtime_version") != "vnext"
                or done.get("runtime_budget") != FROZEN_RUNTIME_BUDGET
                or done.get("no_legacy_fallback") is not True
                or done.get("status") != "mission_verified"
                or done.get("target_binding_id") != target_binding_id
                or done.get("turns_used") != len(turn_events)
                or done.get("model_calls") != len(model_events)
            ):
                errors.append("vNext terminal status/budget/count/binding is not exact")

    return {
        "passed": not errors,
        "trace_file": trace_file,
        "trace_event_count": len(events),
        "run_id": visible_run_id,
        "runtime_version": runtime_version,
        "outer_start_count": len(starts),
        "model_event_types": sorted(model_event_types),
        "model_call_count": len(model_events),
        "models": models,
        "forensic_summary": forensic_summary,
        "target_expectation": expected_target,
        "target_binding_id": target_binding_id,
        "turn_count": len(turn_events),
        "done_count": len(done_events),
        "receipt_summary": receipt_summary,
        "errors": errors,
    }


def _identity_audit(
    before: dict[str, Any],
    after: dict[str, Any],
    *,
    case_id: str,
) -> dict[str, Any]:
    errors: list[str] = []
    expected_title = f"WinPilot vNext Canary {case_id}"
    if before.get("title") != expected_title:
        errors.append("target title does not bind the unique case id")
    for key in (
        "hwnd",
        "root_hwnd",
        "title",
        "class_name",
        "process_id",
        "executable",
        "rect",
    ):
        if before.get(key) != after.get(key):
            errors.append(f"target identity changed during run: {key}")
    return {"passed": not errors, "before": before, "after": after, "errors": errors}


def _status_for_result(result: dict[str, Any]) -> str:
    outcome = str(((result.get("client_result") or {}).get("result") or {}).get("outcome") or "")
    if outcome == "timeout":
        return "timeout"
    if (
        not (result.get("target_identity_audit") or {}).get("passed")
        or not (result.get("trace_audit") or {}).get("passed")
        or not (result.get("client_result") or {}).get("execution_valid")
    ):
        return "invalid"
    if (
        not (result.get("external_oracle") or {}).get("passed")
        or not (result.get("client_result") or {}).get("passed")
    ):
        return "failed"
    return "passed"


def _trace_sources(
    client_result: dict[str, Any],
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    str,
]:
    events, trace_file = _load_trace_events(client_result)
    indexed: list[dict[str, Any]] = []
    for index, raw in enumerate(events, start=1):
        event = deepcopy(raw)
        event.setdefault("trace_sequence", index)
        indexed.append(event)
    starts = [
        event
        for event in indexed
        if event.get("type") == "desktop_action_queue"
        and event.get("state") == "start"
        and isinstance(event.get("metadata"), dict)
        and event["metadata"].get("owner") == "vnext_atomic_desktop_adapter"
    ]
    start_by_job: dict[str, list[dict[str, Any]]] = {}
    for event in starts:
        start_by_job.setdefault(str(event.get("job_id") or ""), []).append(event)
    dones: list[dict[str, Any]] = []
    for raw in indexed:
        if (
            raw.get("type") != "desktop_action_queue"
            or raw.get("state") not in {"done", "error"}
        ):
            continue
        job_id = str(raw.get("job_id") or "")
        candidates = start_by_job.get(job_id) or []
        if len(candidates) != 1:
            # Preserve the unbound event.  The pure reconciler will reject the
            # missing decision rather than guessing across queue jobs.
            dones.append(deepcopy(raw))
            continue
        start = candidates[0]
        metadata = deepcopy(start.get("metadata") or {})
        event = deepcopy(raw)
        event["metadata"] = metadata
        event["decision_id"] = str(metadata.get("decision_id") or "")
        event["run_id"] = str(metadata.get("run_id") or "")
        event["obligation_id"] = str(metadata.get("obligation_id") or "")
        event["action"] = str(start.get("action") or "")
        event["target_hwnd"] = start.get("target_hwnd")
        dones.append(event)
    receipts = [
        event for event in indexed if event.get("type") == "vnext_raw_receipt"
    ]
    turns = [
        event
        for event in indexed
        if event.get("type") == "vnext_canary_turn"
        and event.get("proposal_kind") == "desktop_action"
    ]
    return indexed, starts, dones, receipts, turns, trace_file


def _normalized_browser_events(
    external_state: dict[str, Any],
) -> list[dict[str, Any]]:
    raw_events = external_state.get("events")
    raw_events = raw_events if isinstance(raw_events, list) else []
    observations = external_state.get("action_observations")
    observations = observations if isinstance(observations, list) else []
    result: list[dict[str, Any]] = []
    for raw in raw_events:
        if not isinstance(raw, dict):
            continue
        event = deepcopy(raw)
        received = event.get("received_at")
        if type(received) in {int, float} and type(received) is not bool:
            event["wall_time_s"] = float(received)
        details = event.get("details")
        details = deepcopy(details) if isinstance(details, dict) else {}
        facts = details.get("event")
        facts = facts if isinstance(facts, dict) else {}
        for name in (
            "client_x",
            "client_y",
            "screen_x",
            "screen_y",
            "delta_x",
            "delta_y",
            "ctrl_key",
            "shift_key",
            "alt_key",
            "meta_key",
            "repeat",
        ):
            if name not in details and name in facts:
                details[name] = facts[name]
        event["details"] = details
        sequence = event.get("event_seq")
        matches = [
            item
            for item in observations
            if isinstance(item, dict)
            and type(sequence) is int
            and type(item.get("start_event_seq")) is int
            and type(item.get("end_event_seq")) is int
            and item["start_event_seq"] <= sequence <= item["end_event_seq"]
        ]
        if len(matches) == 1:
            observation = matches[0]
            event["external_action_index"] = observation.get(
                "external_action_index"
            )
            event["action"] = observation.get("action")
            if type(observation.get("actual_changed")) is bool:
                event["actual_changed"] = observation["actual_changed"]
        result.append(event)
    return result


def _queue_windows(
    starts: list[dict[str, Any]],
    dones: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    windows: list[dict[str, Any]] = []
    for start in starts:
        decision = str(_receipt_value(start, "decision_id") or "")
        job_id = str(start.get("job_id") or "")
        matches = [item for item in dones if str(item.get("job_id") or "") == job_id]
        begin = start.get("ts")
        end = matches[0].get("ts") if len(matches) == 1 else None
        if (
            decision
            and type(begin) in {int, float}
            and type(begin) is not bool
            and type(end) in {int, float}
            and type(end) is not bool
            and float(end) >= float(begin)
        ):
            windows.append(
                {
                    "decision_id": decision,
                    "action": str(start.get("action") or ""),
                    "start": float(begin),
                    "end": float(end),
                }
            )
    return windows


def _normalized_os_evidence(
    hook_snapshot: dict[str, Any],
    *,
    start_sequence: int,
    target_identity: dict[str, Any],
    starts: list[dict[str, Any]],
    dones: list[dict[str, Any]],
) -> dict[str, Any]:
    raw_events = hook_snapshot.get("events")
    raw_events = raw_events if isinstance(raw_events, list) else []
    sliced = [
        deepcopy(item)
        for item in raw_events
        if isinstance(item, dict)
        and type(item.get("hook_seq")) is int
        and int(item["hook_seq"]) > int(start_sequence)
    ]
    windows = _queue_windows(starts, dones)
    target_root = int(target_identity.get("root_hwnd") or 0)
    target_hwnd = int(target_identity.get("hwnd") or 0)
    relevant: list[dict[str, Any]] = []
    external_untrusted: list[dict[str, Any]] = []
    unexpected_injected_target: list[dict[str, Any]] = []
    type_map = {
        "button_down": "pointerdown",
        "button_up": "pointerup",
        "wheel": "wheel",
        "horizontal_wheel": "wheel",
        "key_down": "keydown",
        "system_key_down": "keydown",
        "key_up": "keyup",
        "system_key_up": "keyup",
    }
    for raw in sliced:
        if raw.get("mutation") is not True:
            continue
        if raw.get("injected") is not True:
            external_untrusted.append(raw)
            continue
        time_ns = raw.get("time_ns")
        wall_time = (
            float(time_ns) / 1_000_000_000.0
            if type(time_ns) is int and type(time_ns) is not bool
            else None
        )
        if wall_time is None:
            unexpected_injected_target.append(raw)
            continue
        device = str(raw.get("device") or "")
        target_matches = (
            int(raw.get("foreground_root_hwnd") or 0) == target_root
            if device == "keyboard"
            else (
                int(raw.get("foreground_root_hwnd") or 0) == target_root
                and int(raw.get("window_from_point_hwnd") or 0) != 0
            )
        )
        if not target_matches:
            continue
        matches = [
            window
            for window in windows
            if window["start"] <= wall_time <= window["end"]
        ]
        if len(matches) != 1:
            # The harness uses one balanced ALT tap solely to hand foreground
            # ownership to the already-admitted target before the runtime
            # acts.  It is recorded raw but is not a runtime action.
            if device == "keyboard" and int(raw.get("vk_code") or 0) == win32con.VK_MENU:
                continue
            unexpected_injected_target.append(raw)
            continue
        window = matches[0]
        event = deepcopy(raw)
        event["type"] = type_map.get(str(raw.get("kind") or ""), "unknown")
        event["kind"] = event["type"]
        event["sequence"] = int(raw["hook_seq"])
        event["wall_time_s"] = wall_time
        event["timestamp"] = wall_time
        event["trusted"] = True
        event["decision_id"] = window["decision_id"]
        event["action"] = window["action"]
        event["target_hwnd"] = target_hwnd
        event["root_hwnd"] = target_root
        relevant.append(event)
    return {
        "raw_events": sliced,
        "normalized_action_events": relevant,
        "external_untrusted_mutations": external_untrusted,
        "unexpected_injected_target_mutations": unexpected_injected_target,
        "dropped_event_count": int(hook_snapshot.get("dropped_event_count") or 0),
        "hook_seq": int(hook_snapshot.get("hook_seq") or 0),
        "thread_error": str(hook_snapshot.get("thread_error") or ""),
    }


def _legacy_os_evidence(
    hook_snapshot: dict[str, Any],
    *,
    start_sequence: int,
    target_identity: dict[str, Any],
) -> dict[str, Any]:
    raw_events = hook_snapshot.get("events")
    raw_events = raw_events if isinstance(raw_events, list) else []
    sliced = [
        deepcopy(item)
        for item in raw_events
        if isinstance(item, dict)
        and type(item.get("hook_seq")) is int
        and int(item["hook_seq"]) > int(start_sequence)
    ]
    target_root = int(target_identity.get("root_hwnd") or 0)
    type_map = {
        "button_down": "pointerdown",
        "button_up": "pointerup",
        "wheel": "wheel",
        "horizontal_wheel": "wheel",
        "key_down": "keydown",
        "system_key_down": "keydown",
        "key_up": "keyup",
        "system_key_up": "keyup",
    }
    normalized: list[dict[str, Any]] = []
    for raw in sliced:
        if raw.get("mutation") is not True or raw.get("injected") is not True:
            continue
        device = str(raw.get("device") or "")
        if int(raw.get("foreground_root_hwnd") or 0) != target_root:
            continue
        event = deepcopy(raw)
        event["type"] = type_map.get(str(raw.get("kind") or ""), "unknown")
        event["kind"] = event["type"]
        event["sequence"] = int(raw["hook_seq"])
        time_ns = raw.get("time_ns")
        if type(time_ns) is int and type(time_ns) is not bool:
            event["wall_time_s"] = float(time_ns) / 1_000_000_000.0
        event["trusted"] = True
        normalized.append(event)
    evidence = {
        "raw_events": sliced,
        "normalized_action_events": normalized,
        "external_untrusted_mutations": [
            deepcopy(item)
            for item in sliced
            if item.get("mutation") is True and item.get("injected") is not True
        ],
        "unexpected_injected_target_mutations": [],
        "dropped_event_count": int(hook_snapshot.get("dropped_event_count") or 0),
        "hook_seq": int(hook_snapshot.get("hook_seq") or 0),
        "thread_error": str(hook_snapshot.get("thread_error") or ""),
    }
    evidence["input_release_audit"] = _input_release_audit(normalized)
    return evidence


def _input_release_audit(events: list[dict[str, Any]]) -> dict[str, Any]:
    key_balance: dict[int, int] = {}
    button_balance: dict[str, int] = {}
    for event in events:
        event_type = str(event.get("type") or "")
        if event_type in {"keydown", "keyup"}:
            key = int(event.get("vk_code") or 0)
            key_balance[key] = key_balance.get(key, 0) + (
                1 if event_type == "keydown" else -1
            )
        elif event_type in {"pointerdown", "pointerup"}:
            button = str(event.get("button") or "")
            button_balance[button] = button_balance.get(button, 0) + (
                1 if event_type == "pointerdown" else -1
            )
    errors = [
        f"key {key} down/up balance is {balance}"
        for key, balance in sorted(key_balance.items())
        if balance != 0
    ]
    errors.extend(
        f"button {button!r} down/up balance is {balance}"
        for button, balance in sorted(button_balance.items())
        if balance != 0
    )
    return {
        "passed": not errors,
        "key_balance": {str(key): value for key, value in sorted(key_balance.items())},
        "button_balance": dict(sorted(button_balance.items())),
        "errors": errors,
    }


def _reconcile_vnext_actions(
    *,
    sample: dict[str, Any],
    attempt: int,
    client_result: dict[str, Any],
    external_state: dict[str, Any],
    hook_snapshot: dict[str, Any],
    hook_start_sequence: int,
    target_identity: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], str]:
    events, starts, dones, receipts, turns, trace_file = _trace_sources(
        client_result
    )
    visible = client_result.get("result")
    visible = visible if isinstance(visible, dict) else {}
    run_id = str(visible.get("run_id") or "")
    frozen = next(
        (
            event
            for event in events
            if event.get("type") == "vnext_canary_start"
            and str(event.get("run_id") or "") == run_id
        ),
        {},
    )
    frozen_target = frozen.get("target")
    frozen_target = frozen_target if isinstance(frozen_target, dict) else {}
    exact_target = deepcopy(target_identity) if target_identity else {}
    exact_target.update(
        {
            "target_binding_id": str(frozen_target.get("binding_id") or ""),
            "target_hwnd": int(
                frozen_target.get("hwnd")
                or exact_target.get("hwnd")
                or 0
            ),
            "root_hwnd": int(
                frozen_target.get("root_hwnd")
                or exact_target.get("root_hwnd")
                or 0
            ),
            "process_id": int(
                frozen_target.get("process_id")
                or exact_target.get("process_id")
                or 0
            ),
        }
    )
    if not exact_target:
        exact_target = {
            "target_binding_id": "unavailable",
            "target_hwnd": 0,
            "root_hwnd": 0,
            "process_id": 0,
        }
    os_evidence = _normalized_os_evidence(
        hook_snapshot,
        start_sequence=hook_start_sequence,
        target_identity=exact_target,
        starts=starts,
        dones=dones,
    )
    browser = _normalized_browser_events(external_state)
    reconciliation = campaign_state.reconcile_action_evidence(
        run_id=run_id or f"unavailable-{sample['sample_key']}-attempt-{attempt}",
        obligation_id="full-goal",
        target_identity=exact_target,
        queue_start_events=starts,
        queue_done_events=dones,
        receipt_events=receipts,
        turn_events=turns,
        browser_events=browser,
        os_events=os_evidence["normalized_action_events"],
    )
    release = _input_release_audit(os_evidence["normalized_action_events"])
    os_evidence["input_release_audit"] = release
    return reconciliation, os_evidence, trace_file


def _mutation_observed(
    client_result: dict[str, Any],
    external_state: dict[str, Any],
    os_evidence: dict[str, Any],
) -> bool:
    try:
        _events, starts, _dones, _receipts, _turns, _path = _trace_sources(
            client_result
        )
    except Exception:
        starts = []
    browser_events = external_state.get("events")
    browser_events = browser_events if isinstance(browser_events, list) else []
    trusted_browser = any(
        isinstance(item, dict)
        and item.get("trusted") is True
        and item.get("type")
        in {
            "pointerdown",
            "pointerup",
            "click",
            "dblclick",
            "wheel",
            "keydown",
            "keyup",
            "beforeinput",
            "input",
        }
        for item in browser_events
    )
    return bool(
        starts
        or trusted_browser
        or os_evidence.get("normalized_action_events")
    )


def _invalid_reason(
    result: dict[str, Any],
    *,
    mutation_observed: bool,
    os_evidence: dict[str, Any],
) -> str:
    if mutation_observed:
        return ""
    if os_evidence.get("external_untrusted_mutations"):
        return "external_user_input_pre_mutation"
    phase = str(result.get("failure_phase") or "")
    if phase == "electron_start":
        return "electron_not_started_pre_mutation"
    if phase == "target_integrity":
        return "eval_target_integrity_failure_pre_mutation"
    if phase in {"target_server", "target_browser"}:
        return "eval_target_not_started_pre_mutation"
    if phase in {"trace", "trace_binding"}:
        return "trace_binding_unavailable_pre_mutation"
    if phase in {"client_control", "client_run"}:
        return "harness_transport_failure_pre_mutation"
    return "control_plane_not_started_pre_mutation"


def _safety_reasons(
    *,
    reconciliation: dict[str, Any] | None,
    os_evidence: dict[str, Any],
    cleanup: dict[str, Any],
) -> list[str]:
    reasons: set[str] = set()
    if reconciliation is not None:
        reasons.update(
            str(item)
            for item in reconciliation.get("safety_abort_reasons") or []
            if str(item) in campaign_state.DEFAULT_SAFETY_ABORT_CONDITIONS
        )
    if os_evidence.get("external_untrusted_mutations"):
        reasons.add("unauthorized_desktop_input")
    if os_evidence.get("unexpected_injected_target_mutations"):
        reasons.add("unauthorized_desktop_input")
    if int(os_evidence.get("dropped_event_count") or 0) > 0:
        reasons.add("campaign_integrity_failure")
    if os_evidence.get("thread_error"):
        reasons.add("test_control_irrecoverable")
    release = os_evidence.get("input_release_audit")
    if isinstance(release, dict) and release.get("passed") is not True:
        reasons.add("input_release_failed")
    if cleanup.get("passed") is not True:
        reasons.add("test_control_irrecoverable")
    return sorted(reasons)


def run_runtime_case(
    sample: dict[str, Any],
    attempt: int,
    *,
    campaign_dir: Path,
    work_root: Path,
    frozen_config_template: dict[str, Any],
    model_config: dict[str, Any],
    manifest_sealed_time_ns: int,
    passive_hook: input_hook.PassiveInputHook,
) -> dict[str, Any]:
    runtime_version = str(sample.get("runtime") or "")
    if runtime_version not in RUNTIMES:
        raise ValueError(f"unsupported runtime {runtime_version!r}")
    if attempt not in {1, 2}:
        raise ValueError("campaign attempt must be 1 or 2")
    stable = (
        f"{int(sample['execution_order']):03d}-"
        f"{sample['case']}-{runtime_version}-attempt-{attempt}"
    )
    case_id = stable
    scenario = str(sample["scenario"])
    seed = int(sample["seed"])
    page_id = f"vnext-canary:{case_id}:{scenario}:{seed}"
    case_dir = work_root / stable
    case_dir.mkdir(parents=True, exist_ok=False)
    profile_dir = case_dir / "browser-profile"
    profile_dir.mkdir()
    hook_start = passive_hook.snapshot()
    hook_start_sequence = int(hook_start.get("hook_seq") or 0)
    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    state_query = urllib.parse.urlencode(
        {
            "case": case_id,
            "scenario": scenario,
            "seed": seed,
            "page": page_id,
        }
    )
    state_url = f"{base}/state?{state_query}"
    page_query = urllib.parse.urlencode(
        {"case": case_id, "scenario": scenario, "seed": seed}
    )
    target_url = f"{base}/vnext_canary_target.html?{page_query}"
    electron_process: subprocess.Popen[Any] | None = None
    server_process: subprocess.Popen[Any] | None = None
    browser_process: subprocess.Popen[Any] | None = None
    electron_identities: list[dict[str, Any]] = []
    server_identities: list[dict[str, Any]] = []
    browser_identities: list[dict[str, Any]] = []
    electron_log: Any = None
    server_log: Any = None
    browser_log: Any = None
    target_hwnd = 0
    target_before: dict[str, Any] = {}
    external_state: dict[str, Any] = {}
    external_verdict: dict[str, Any] = {}
    external_settlement: dict[str, Any] = {}
    client_result: dict[str, Any] = {}
    result: dict[str, Any] = {
        "format": "vnext-live-canary-sample-attempt/2",
        "sample_key": sample["sample_key"],
        "execution_order": sample["execution_order"],
        "attempt": attempt,
        "case": sample["case"],
        "case_id": case_id,
        "page_id": page_id,
        "family": sample["family"],
        "scenario": scenario,
        "seed": seed,
        "runtime_version": runtime_version,
        "task": CANARY_TASK,
        "runtime_budget": dict(FROZEN_RUNTIME_BUDGET),
        "frozen_config_sha256": model_config["config_sha256"],
        "hook_start_sequence": hook_start_sequence,
        "started_at": _utc_now(),
        "failure_phase": "electron_start",
        "status": "running",
    }
    try:
        (
            electron_process,
            driver,
            electron_identities,
            electron_startup,
            electron_log,
        ) = _start_managed_electron(
            case_dir,
            expected_commit=str(
                campaign_state.load_manifest(campaign_dir)["stack"]["c4"]["commit"]
            ),
            manifest_sealed_time_ns=manifest_sealed_time_ns,
        )
        result["electron_startup"] = electron_startup
        result["failure_phase"] = "client_control"
        observed_config = _authenticated_config(
            driver,
            frozen_template=frozen_config_template,
        )
        if observed_config != model_config:
            raise RuntimeError(
                "managed Electron behavior config differs from the sealed manifest"
            )
        result["config_audit"] = {
            "passed": True,
            "config_sha256": observed_config["config_sha256"],
            "model": observed_config["model"],
            "provider": observed_config["provider"],
            "adapter": observed_config["adapter"],
            "api_max_retries": observed_config["api_max_retries"],
        }

        result["failure_phase"] = "target_server"
        server_log = (case_dir / "target-server.log").open("xb")
        server_process = subprocess.Popen(
            [sys.executable, "-u", str(TARGET_SERVER), str(port)],
            cwd=str(TARGET_SERVER.parent),
            stdin=subprocess.DEVNULL,
            stdout=server_log,
            stderr=subprocess.STDOUT,
        )
        server_identities = _process_tree_identities(server_process)
        _wait_json(
            f"{base}/state",
            lambda value: value.get("ready") is True,
            timeout=12,
        )
        server_identities = _process_tree_identities(server_process)

        result["failure_phase"] = "target_integrity"
        with urllib.request.urlopen(
            f"{base}/vnext_canary_target.html",
            timeout=3,
        ) as response:
            served_target = response.read()
        served_sha = hashlib.sha256(served_target).hexdigest()
        source_sha = campaign_state.sha256_file(TARGET_HTML)
        if served_sha != source_sha:
            raise RuntimeError("served eval target differs from the sealed source")
        result["target_source_audit"] = {
            "passed": True,
            "served_sha256": served_sha,
            "source_sha256": source_sha,
        }

        result["failure_phase"] = "target_browser"
        browser = _browser_executable()
        result["browser_executable"] = str(browser)
        browser_log = (case_dir / "target-browser.log").open("xb")
        browser_process = subprocess.Popen(
            [
                str(browser),
                f"--app={target_url}",
                "--new-window",
                "--window-size=1100,760",
                "--window-position=80,60",
                "--force-device-scale-factor=1",
                f"--user-data-dir={profile_dir}",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-extensions",
                "--disable-background-mode",
                "--disable-backgrounding-occluded-windows",
                "--disable-renderer-backgrounding",
                "--disable-features=CalculateNativeWinOcclusion",
            ],
            stdin=subprocess.DEVNULL,
            stdout=browser_log,
            stderr=subprocess.STDOUT,
        )
        browser_identities = _process_tree_identities(browser_process)
        target_hwnd = _find_window(f"WinPilot vNext Canary {case_id}")
        ready = _wait_json(
            state_url,
            lambda value: any(
                isinstance(item, dict) and item.get("type") == "page_ready"
                for item in value.get("events") or []
            ),
            timeout=12,
        )
        browser_identities = _process_tree_identities(browser_process)
        result["target_ready"] = ready
        target_before = _window_identity(target_hwnd)
        result["target_before"] = target_before
        result["screenshots"] = {
            "target_before": _capture_window(
                target_hwnd,
                case_dir / "target-before.png",
            )
        }

        def handoff(
            handoff_driver: client_harness.ElectronClientDriver,
            _client_result: dict[str, Any],
        ) -> dict[str, Any]:
            observed = _authenticated_config(
                handoff_driver,
                frozen_template=frozen_config_template,
            )
            if observed != model_config:
                raise RuntimeError(
                    "campaign behavior config changed after visible submission"
                )
            evidence = _focus_window(target_hwnd)
            evidence["identity"] = _window_identity(target_hwnd)
            evidence["screenshot"] = _capture_window(
                target_hwnd,
                handoff_driver.output_dir / "target-after-submit.png",
            )
            return evidence

        def verify_external(_client_result: dict[str, Any]) -> dict[str, Any]:
            nonlocal external_state, external_settlement, external_verdict
            external_state, external_settlement = _settled_external_state(
                state_url
            )
            oracle = external_state.get("oracle")
            if not isinstance(oracle, dict):
                external_verdict = {
                    "source": "independent_vnext_canary_browser_oracle",
                    "passed": False,
                    "errors": ["oracle response is missing"],
                }
                return external_verdict
            external_verdict = dict(oracle)
            external_verdict["source"] = (
                "independent_vnext_canary_browser_oracle"
            )
            if external_settlement.get("passed") is not True:
                external_verdict["passed"] = False
                external_verdict.setdefault("errors", []).extend(
                    external_settlement.get("errors") or []
                )
            return external_verdict

        result["failure_phase"] = "client_run"
        client_result = client_harness.run_case(
            f"{sample['case']}-{runtime_version}-attempt-{attempt}",
            {
                "task": CANARY_TASK,
                "max_steps": FROZEN_RUNTIME_BUDGET["max_turns"],
                "timeout": FROZEN_RUNTIME_BUDGET["max_runtime_s"],
                "runtime_version": runtime_version,
            },
            output_root=case_dir / "client",
            reset_session=True,
            after_submit=handoff,
            independent_verifier=verify_external,
            expected_client_identity=driver.identity,
        )
        result["client_result"] = client_result
        if not external_state:
            external_state = _json_get(state_url)
        result["external_state"] = external_state
        result["external_settlement"] = external_settlement
        result["external_oracle"] = dict(
            external_verdict or external_state.get("oracle") or {}
        )
        if target_hwnd and win32gui.IsWindow(target_hwnd):
            result["screenshots"]["target_after"] = _capture_window(
                target_hwnd,
                case_dir / "target-after.png",
            )
            target_after = _window_identity(target_hwnd)
        else:
            target_after = {}
        result["target_identity_audit"] = _identity_audit(
            target_before,
            target_after,
            case_id=case_id,
        )
        result["failure_phase"] = "trace"
        result["trace_audit"] = _trace_audit(
            client_result,
            runtime_version=runtime_version,
            expected_model=str(model_config["model"]),
            target_identity=target_before,
            family=str(sample["family"]),
        )
        result["status"] = _status_for_result(result)
        result["passed"] = result["status"] == "passed"
    except Exception as exc:
        result["runner_error"] = f"{type(exc).__name__}: {exc}"
        result["status"] = "invalid"
        result["passed"] = False
        try:
            if server_process is not None and server_process.poll() is None:
                external_state = _json_get(state_url)
                result["external_state"] = external_state
                result["external_oracle"] = dict(
                    external_state.get("oracle") or {}
                )
        except Exception as state_exc:
            result["oracle_collection_error"] = (
                f"{type(state_exc).__name__}: {state_exc}"
            )
    finally:
        cleanup_errors: list[str] = []
        try:
            if target_hwnd and win32gui.IsWindow(target_hwnd):
                win32gui.PostMessage(target_hwnd, win32con.WM_CLOSE, 0, 0)
        except Exception as exc:
            cleanup_errors.append(f"target close: {type(exc).__name__}: {exc}")
        cleanup_parts: dict[str, Any] = {}
        for name, process, identities in (
            ("browser", browser_process, browser_identities),
            ("server", server_process, server_identities),
            ("electron", electron_process, electron_identities),
        ):
            try:
                cleanup_parts[name] = _terminate_tree(
                    process,
                    known_identities=identities,
                )
            except Exception as exc:
                cleanup_parts[name] = {
                    "passed": False,
                    "errors": [f"{type(exc).__name__}: {exc}"],
                }
            if cleanup_parts[name].get("passed") is not True:
                cleanup_errors.extend(cleanup_parts[name].get("errors") or [])
        for stream in (browser_log, server_log, electron_log):
            if stream is not None:
                try:
                    stream.flush()
                    os.fsync(stream.fileno())
                    stream.close()
                except Exception as exc:
                    cleanup_errors.append(
                        f"log close: {type(exc).__name__}: {exc}"
                    )
        profile_cleanup = _remove_profile(profile_dir)
        cleanup_parts["profile"] = profile_cleanup
        if profile_cleanup.get("passed") is not True:
            cleanup_errors.extend(profile_cleanup.get("errors") or [])
        target_still_live = bool(target_hwnd and win32gui.IsWindow(target_hwnd))
        if target_still_live:
            cleanup_errors.append("target HWND remains live after browser cleanup")
        residual_electron = _workspace_electron_processes()
        if residual_electron:
            cleanup_errors.append("managed workspace Electron remains after cleanup")
        result["cleanup"] = {
            "passed": not cleanup_errors,
            **cleanup_parts,
            "target_hwnd_live": target_still_live,
            "residual_workspace_electron": residual_electron,
            "errors": cleanup_errors,
        }
        if cleanup_errors:
            result["status"] = "invalid"
            result["passed"] = False

    hook_end = passive_hook.snapshot()
    reconciliation: dict[str, Any] | None = None
    trace_file = ""
    if runtime_version == "vnext":
        reconciliation, os_evidence, trace_file = _reconcile_vnext_actions(
            sample=sample,
            attempt=attempt,
            client_result=client_result,
            external_state=external_state,
            hook_snapshot=hook_end,
            hook_start_sequence=hook_start_sequence,
            target_identity=target_before,
        )
        result["action_reconciliation"] = reconciliation
        if reconciliation.get("passed") is not True:
            result["status"] = "failed"
            result["passed"] = False
    else:
        os_evidence = _legacy_os_evidence(
            hook_end,
            start_sequence=hook_start_sequence,
            target_identity=target_before or {"root_hwnd": 0},
        )
    result["os_input_evidence"] = os_evidence
    result["trace_file"] = trace_file
    mutation_observed = _mutation_observed(
        client_result,
        external_state,
        os_evidence,
    )
    result["first_trusted_mutation_observed"] = mutation_observed
    safety_reasons = _safety_reasons(
        reconciliation=reconciliation,
        os_evidence=os_evidence,
        cleanup=result["cleanup"],
    )
    result["safety_abort_reasons"] = safety_reasons
    invalid_reason = (
        _invalid_reason(
            result,
            mutation_observed=mutation_observed,
            os_evidence=os_evidence,
        )
        if result.get("status") == "invalid"
        else ""
    )
    result["invalid_reason"] = invalid_reason
    result["finished_at"] = _utc_now()

    artifacts = _seal_work_tree(
        campaign_dir,
        sample,
        attempt,
        case_dir,
    )
    if trace_file:
        trace_path = Path(trace_file)
        if trace_path.is_file() and not trace_path.is_symlink():
            artifacts.append(
                campaign_state.write_immutable_artifact(
                    campaign_dir,
                    _stable_artifact_path(
                        sample,
                        attempt,
                        "raw/runtime-trace.jsonl",
                    ),
                    trace_path.read_bytes(),
                    kind="runtime_trace",
                )
            )
    artifacts.append(
        _seal_value(
            campaign_dir,
            sample,
            attempt,
            "external-state.json",
            external_state,
            kind="external_state",
        )
    )
    artifacts.append(
        _seal_value(
            campaign_dir,
            sample,
            attempt,
            "os-input-evidence.json",
            os_evidence,
            kind="os_input_evidence",
        )
    )
    if reconciliation is not None:
        artifacts.append(
            _seal_value(
                campaign_dir,
                sample,
                attempt,
                "action-reconciliation.json",
                reconciliation,
                kind="action_reconciliation",
            )
        )
    artifacts.append(
        _seal_value(
            campaign_dir,
            sample,
            attempt,
            "attempt-result.json",
            result,
            kind="attempt_result",
        )
    )
    return {
        "status": str(result.get("status") or "invalid"),
        "passed": result.get("passed") is True,
        "invalid_reason": invalid_reason,
        "pre_mutation": not mutation_observed,
        "external_evidence": True,
        "first_trusted_mutation_observed": mutation_observed,
        "safety_abort_reasons": safety_reasons,
        "artifacts": artifacts,
        "work_dir": str(case_dir),
    }


def run_suite() -> dict[str, Any]:
    with DesktopSuiteMutex():
        output_root = DEFAULT_OUTPUT.resolve()
        ledger_path = CAMPAIGN_LEDGER.resolve()
        if ledger_path.is_file():
            try:
                existing = json.loads(ledger_path.read_text(encoding="utf-8"))
            except Exception as exc:
                existing = {"read_error": f"{type(exc).__name__}: {exc}"}
            return {
                "format": "vnext-live-canary-paired-suite/1",
                "status": "invalid",
                "passed": False,
                "ledger": str(ledger_path),
                "existing_campaign": existing,
                "errors": [
                    "the fixed paired campaign was already claimed; reruns are forbidden"
                ],
            }

        campaign_id = (
            datetime.now().strftime("%Y%m%d-%H%M%S")
            + "-"
            + uuid.uuid4().hex
        )
        suite_dir = output_root / f"{campaign_id}-paired-suite"
        manifest_path = suite_dir / "manifest.json"
        ledger: dict[str, Any] = {
            "format": "vnext-live-canary-campaign-ledger/1",
            "campaign_id": campaign_id,
            "claimed_at": _utc_now(),
            "status": "claimed",
            "manifest": str(manifest_path),
            "matrix": [
                {
                    "case": item["case"],
                    "family": item["family"],
                    "seed": item["seed"],
                    "runtime_order": list(item["runtime_order"]),
                }
                for item in CANARY_MATRIX
            ],
        }
        # The immutable claim is created while holding the physical desktop
        # mutex and before any manifest/run artifact. A crash still consumes
        # the one allowed campaign and therefore cannot be hidden by a rerun.
        _write_json(ledger_path, ledger)

        manifest = _new_manifest()
        manifest["campaign_id"] = campaign_id
        manifest["ledger"] = str(ledger_path)
        manifest["manifest"] = str(manifest_path)
        _write_json(manifest_path, manifest)
        preflight_driver: client_harness.ElectronClientDriver | None = None
        original_config: dict[str, Any] | None = None
        try:
            manifest["status"] = "preflight"
            _write_json(manifest_path, manifest)
            preflight_driver = client_harness.ElectronClientDriver(
                suite_dir / "preflight-client"
            )
            original_config = _enable_fixed_canary(preflight_driver)
            frozen_config = _authenticated_config(preflight_driver)
            manifest["frozen_config"] = frozen_config
            manifest["client"] = {
                "pid": preflight_driver.identity.pid,
                "hwnd": preflight_driver.identity.hwnd,
                "executable": preflight_driver.identity.executable,
                "command_line": preflight_driver.identity.command_line,
                "workspace_verified": preflight_driver.identity.workspace_verified,
            }
            manifest["status"] = "running"
            _write_json(manifest_path, manifest)
            for pair_index, spec in enumerate(CANARY_MATRIX):
                pair = manifest["pairs"][pair_index]
                pair["status"] = "running"
                pair["started_at"] = _utc_now()
                _write_json(manifest_path, manifest)
                for run_index, runtime_version in enumerate(spec["runtime_order"]):
                    slot = pair["runs"][run_index]
                    slot["status"] = "running"
                    slot["started_at"] = _utc_now()
                    _write_json(manifest_path, manifest)
                    run_result: dict[str, Any] = {
                        "runtime_version": runtime_version,
                        "status": "invalid",
                        "passed": False,
                        "runner_error": "run attempt did not return a result",
                    }
                    try:
                        run_result = run_runtime_case(
                            dict(spec),
                            str(runtime_version),
                            output_root=suite_dir / "runs",
                            frozen_config=frozen_config,
                            expected_client_identity=preflight_driver.identity,
                        )
                    except Exception as exc:
                        run_result = {
                            "runtime_version": runtime_version,
                            "status": "invalid",
                            "passed": False,
                            "runner_error": f"{type(exc).__name__}: {exc}",
                            "finished_at": _utc_now(),
                        }
                    finally:
                        # The slot is always replaced exactly once. No failed
                        # member is retried and no second sample can hide it.
                        slot.update(run_result)
                        _write_json(manifest_path, manifest)
                pair["passed"] = all(
                    item.get("status") == "passed" for item in pair["runs"]
                )
                pair["status"] = "passed" if pair["passed"] else "failed"
                pair["finished_at"] = _utc_now()
                _write_json(manifest_path, manifest)
                if not pair["passed"]:
                    manifest["stopped_after_pair"] = pair["case"]
                    break
            completed_pairs = [
                pair for pair in manifest["pairs"]
                if pair.get("status") in {"passed", "failed"}
            ]
            manifest["passed"] = (
                len(completed_pairs) == len(CANARY_MATRIX)
                and all(pair.get("passed") is True for pair in completed_pairs)
            )
            manifest["status"] = "passed" if manifest["passed"] else "failed"
        except Exception as exc:
            manifest["errors"].append(f"{type(exc).__name__}: {exc}")
            manifest["passed"] = False
            manifest["status"] = "invalid"
        finally:
            if preflight_driver is not None and original_config is not None:
                try:
                    restore = _restore_canary_config(
                        preflight_driver,
                        original_config,
                    )
                except Exception as exc:
                    restore = {
                        "passed": False,
                        "errors": [f"{type(exc).__name__}: {exc}"],
                    }
                manifest["config_restore"] = restore
                if restore.get("passed") is not True:
                    manifest["errors"].append(
                        "paired canary configuration restoration failed"
                    )
                    manifest["passed"] = False
                    manifest["status"] = "invalid"
            manifest["finished_at"] = _utc_now()
            _write_json(manifest_path, manifest)
            ledger.update(
                {
                    "status": manifest["status"],
                    "passed": manifest.get("passed") is True,
                    "finished_at": manifest["finished_at"],
                }
            )
            try:
                _write_json(ledger_path, ledger)
            except Exception as exc:
                manifest["errors"].append(
                    f"campaign ledger finalization failed: {type(exc).__name__}: {exc}"
                )
                manifest["passed"] = False
                manifest["status"] = "invalid"
                _write_json(manifest_path, manifest)
        return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    return parser.parse_args()


def main() -> int:
    parse_args()
    result = run_suite()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("passed") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
