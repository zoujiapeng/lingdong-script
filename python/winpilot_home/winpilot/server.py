"""FastAPI server: REST control surface + WebSocket event stream + static UI.

Runtime jobs queue on declared resources; desktop mutations remain serialized.
All BUS events stream to every authenticated connected client.

IMPORTANT: Start this server via serve.py only — never directly with uvicorn.
  Correct:  python serve.py [PORT] [HOST]
  Blocked:  uvicorn winpilot.server:app   ← intentionally rejected
"""
from __future__ import annotations

import asyncio
import ctypes
import json
import os
import queue
import re
import secrets
import sys
import threading
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from .automation_ir import (
    arp_graph_from_automation_ir,
    automation_ir_from_wps,
    automation_runtime_kind,
    validate_automation_ir,
    validate_automation_runtime,
    wps_from_automation_ir,
)
from .action.desktop_queue import DESKTOP_ACTION_QUEUE
from .config import CONFIG, ROOT_DIR, SCRIPTS_DIR, TRACES_DIR
from .model_adapter import model_profile_from_config
from .perception import ocr, search, vlm
from .recorder import human as human_mod
from .recorder import apprentice as apprentice_mod
from .recorder import trace as trace_mod
from .runtime_info import runtime_fingerprint
from .runtime_jobs import RUNTIME_JOBS, RuntimeJob
from .utils.log import BUS, logger
from .orchestrator.catalog import capability_catalog
from .orchestrator.contracts import (
    ArpGraph,
    ArpRunRequest,
    ArpRunStatus,
    ArpRuntimeVersion,
    GraphValidation,
    validate_graph,
)
from .orchestrator.goal_admission import (
    DesktopLeaseFailureRequest,
    DesktopLeaseRequest,
    GoalAdmissionError,
    GoalAdmissionUnavailable,
    GoalRunRequest,
    admit_goal,
    bind_client_execution_context,
)
from .orchestrator.agent_threads import (
    AGENT_THREADS,
    AgentThreadError,
)
from .orchestrator.projection import RUN_PROJECTIONS
from .orchestrator.runtime import ARP_RUNS, ArpExecution
from .tasking.artifacts import ArtifactStore

_RECIPE_ID_RE = re.compile(r"^[0-9a-f]{6,32}$")
CONTROL_TOKEN = os.environ.get("WINPILOT_CONTROL_TOKEN", "").strip()


class FlowFragmentProposalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    goal: str = Field(min_length=1, max_length=4000)
    base_fingerprint: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    document: dict[str, Any]
    catalog: list[dict[str, Any]] = Field(min_length=1, max_length=32)


def _started_by_direct_uvicorn() -> bool:
    argv = " ".join(str(arg).lower() for arg in sys.argv)
    return "uvicorn" in argv and "winpilot.server:app" in argv


# Guard: block direct uvicorn invocation. The only supported launcher is
# serve.py (used by the Electron process). Direct uvicorn starts break config
# path resolution and cause port confusion with the embedded server.
if _started_by_direct_uvicorn() and not os.environ.get("WINPILOT_LAUNCHED_VIA_SERVE"):
    print(
        "\n[WinPilot] ERROR: do NOT start this server with uvicorn directly.\n"
        "  Use:  python serve.py [PORT] [HOST]\n"
        "  The Electron app (灵动脚本) launches serve.py automatically.\n",
        file=sys.stderr,
    )
    sys.exit(1)

def _warm_perception_dimensions() -> None:
    if (
        "ocr" in CONFIG.enabled_dimensions()
        and bool(CONFIG.get("ocr", "warm_on_startup", default=False))
    ):
        started = ocr.warm_async()
        if started:
            logger.info("OCR 后台预热已启动")

    if search.warm_async():
        logger.info("Everything search IPC warmup started")


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    RUN_PROJECTIONS.hydrate_directory(
        TRACES_DIR,
        mark_incomplete_interrupted=True,
    )
    try:
        restored_threads = AGENT_THREADS.load_checkpoint()
        if restored_threads:
            logger.info(
                "Restored %s bounded Agent Thread continuations",
                restored_threads,
            )
    except AgentThreadError as exc:
        logger.warning("Agent Thread continuation was not restored: %s", exc)
    _warm_perception_dimensions()
    yield


app = FastAPI(title="WinPilot", docs_url=None, redoc_url=None, lifespan=_lifespan)

# The server binds to loopback and accepts only the production renderer's null
# origin plus the two Vite development origins. Every non-ping request still
# requires the per-process bearer token.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["null", "http://localhost:5200", "http://127.0.0.1:5200"],
    allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE"],
    allow_headers=["Authorization", "Content-Type", "X-WinPilot-Token"],
)


def _request_token(request: Request) -> str:
    authorization = request.headers.get("authorization", "")
    if authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return (
        request.headers.get("x-winpilot-token", "").strip()
        or request.query_params.get("token", "").strip()
    )


@app.middleware("http")
async def require_control_token(request: Request, call_next):
    # Browser preflight requests do not carry the eventual Authorization
    # header. Let CORSMiddleware validate the declared origin/method/headers;
    # the real request below remains token protected.
    if request.method == "OPTIONS":
        return await call_next(request)
    path = request.url.path
    protected = path.startswith("/api/") or path == "/mcp" or path.startswith("/mcp/")
    if protected and path != "/api/ping" and CONTROL_TOKEN:
        supplied = _request_token(request)
        if not supplied or not secrets.compare_digest(supplied, CONTROL_TOKEN):
            return JSONResponse({"detail": "Authentication required"}, status_code=401)
    return await call_next(request)


@app.get("/api/ping")
def api_ping() -> dict[str, bool]:
    return {"ok": True}


def _ensure_inside(path: Path, root: Path, what: str) -> Path:
    """Resolve and reject anything that escapes the expected directory."""
    resolved = path.resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError:
        raise HTTPException(404, f"{what}不存在") from None
    return resolved


def _public_trace_leaf(
    value: str,
    *,
    what: str,
    image: bool = False,
) -> str:
    """Accept one bounded Windows-safe public trace path component."""

    try:
        checked = trace_mod.windows_safe_leaf(value, what=what)
    except ValueError:
        raise HTTPException(404, f"{what} not found")
    if image and Path(checked).suffix.casefold() not in {".jpg", ".jpeg", ".png"}:
        raise HTTPException(404, f"{what} not found")
    return checked


def _public_trace_run_id(run_id: str) -> str:
    return _public_trace_leaf(run_id, what="trace")


def _is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


# ----------------------------------------------------------------- run state
_INTERACTIVE_RUNTIME_RESOURCE = "interactive_runtime"


class _RunState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.run_jobs: dict[str, str] = {}

    def job_for_run(self, run_id: str) -> RuntimeJob | None:
        with self.lock:
            job_id = self.run_jobs.get(str(run_id or ""), "")
        return RUNTIME_JOBS.get(job_id) if job_id else None

    def replay_busy(self) -> bool:
        return RUNTIME_JOBS.busy(kind="replay") or RUNTIME_JOBS.busy(kind="memory_verification")

    def agent_busy(self) -> bool:
        return RUNTIME_JOBS.busy(kind="agent")

    def busy_breakdown(self) -> dict[str, bool]:
        return {
            "agent": self.agent_busy(),
            "replay": self.replay_busy(),
            "human_recording": human_mod.RECORDER.is_recording(),
        }

    def busy(self) -> bool:
        return any(self.busy_breakdown().values()) or ARP_RUNS.busy()


STATE = _RunState()


def _public_runtime_replay_job(job: RuntimeJob) -> dict[str, Any]:
    status = {
        "waiting_resources": "queued",
        "canceled": "stopped",
    }.get(job.status, job.status)
    result = job.result if isinstance(job.result, dict) else None
    return {
        "replay_id": job.id,
        "script": str(job.metadata.get("script") or ""),
        "status": status,
        "runtime_status": job.status,
        "ok": result.get("ok") if result is not None else (False if status in {"failed", "stopped"} else None),
        "error": job.error,
        "created_at": job.created_at,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
        "result": job.result,
        "resources": list(job.resources),
    }


def _public_memory_verification_job(job: RuntimeJob) -> dict[str, Any]:
    status = {
        "waiting_resources": "queued",
        "canceled": "stopped",
    }.get(job.status, job.status)
    result = job.result if isinstance(job.result, dict) else None
    return {
        "replay_id": job.id,
        "memory_id": str(job.metadata.get("memory_id") or ""),
        "script": str(job.metadata.get("script") or ""),
        "status": status,
        "runtime_status": job.status,
        "ok": result.get("ok") if result is not None else (False if status in {"failed", "stopped"} else None),
        "error": job.error or (str(result.get("error") or "") if result else ""),
        "created_at": job.created_at,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
        "result": result,
        "resources": list(job.resources),
    }


# -------------------------------------------------------------------- models
class MemoryAddRequest(BaseModel):
    task: str
    app: str = ""
    steps: list[str] = []
    conditions: list[str] = []
    evidence: list[str] = []


class MemoryPatchRequest(BaseModel):
    task: str | None = None
    app: str | None = None
    steps: list[str] | None = None
    conditions: list[str] | None = None
    evidence: list[str] | None = None
    verification: list[dict[str, Any]] | None = None
    provisional: bool | None = None


class MemorySearchRequest(BaseModel):
    query: str
    limit: int = 12


class MemoryVerifyRequest(BaseModel):
    replay_id: str


class ReplayRequest(BaseModel):
    script: str


class ExportRequest(BaseModel):
    name: str | None = None


class ThreadExportRequest(ExportRequest):
    run_ids: list[str] = Field(default_factory=list, max_length=40)


class ScriptSaveRequest(BaseModel):
    script: dict[str, Any]


class ApprenticeStartRequest(BaseModel):
    task: str = ""
    name: str = ""
    app: str = ""
    verification: list[dict[str, Any]] = Field(default_factory=list)


class ArpGateDecisionRequest(BaseModel):
    node_id: str
    approved: bool
    comment: str = ""


class AutomationIrRunRequest(BaseModel):
    automation: dict[str, Any]
    inputs: dict[str, Any] = Field(default_factory=dict)


# ----------------------------------------------------------------- endpoints
def _thread_result_projection(
    projection: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if projection is None:
        return None
    completed = AGENT_THREADS.completed_run(str(projection.get("run_id") or ""))
    if completed is None:
        return projection
    restored = dict(projection)
    restored.update({
        "goal": completed["goal"],
        "status": completed["status"],
        "result": completed["result"],
        "error": "",
        "phase": "terminal",
        "manual_paused": False,
        "current_node_id": None,
        "current_tool": None,
        "updated_at": completed["updated_at"],
        "finished_at": completed["updated_at"],
    })
    return restored


def _current_run_projection() -> dict[str, Any] | None:
    """Prefer the active parent ARP over an unbound child event projection."""
    execution = ARP_RUNS.current()
    if execution and execution.status.value in {"queued", "running", "paused"}:
        parent = RUN_PROJECTIONS.get(execution.run_id)
        if parent is not None:
            return _thread_result_projection(parent)
    return _thread_result_projection(RUN_PROJECTIONS.current())


@app.get("/api/status")
def status() -> dict[str, Any]:
    busy = STATE.busy_breakdown()
    arp_running = ARP_RUNS.busy()
    model_profile = model_profile_from_config(CONFIG.get("api", default={}))
    vnext_config = CONFIG.get("vnext", default={})
    vnext_config = vnext_config if isinstance(vnext_config, dict) else {}
    vnext_budget = {
        name: (
            value
            if type(value) is int and value > 0
            else 0
        )
        for name in ("max_turns", "max_runtime_s", "max_model_calls")
        for value in (vnext_config.get(name),)
    }
    return {
        "running": any(busy.values()) or arp_running,
        "busy": busy,
        "agent_running": busy["agent"],
        "replay_running": busy["replay"],
        "arp_running": arp_running,
        "human_recording": busy["human_recording"],
        "ocr_provider": ocr.active_provider(),
        "vlm_configured": vlm.is_configured(),
        "vlm_reason": vlm.configuration_issue() or None,
        "enabled_dimensions": CONFIG.enabled_dimensions(),
        "is_admin": _is_admin(),
        "shell_enabled": bool(CONFIG.get("agent", "shell_enabled", default=True)),
        "runtime": runtime_fingerprint(),
        "model_profile": {
            "provider": model_profile.provider,
            "model": model_profile.model,
            "supports_vision": model_profile.supports_vision,
        },
        "vnext": {
            "live_agent_enabled": (
                vnext_config.get("live_agent_enabled") is True
            ),
            "budget": {
                "max_turns": vnext_budget["max_turns"],
                "max_runtime_s": vnext_budget["max_runtime_s"],
                "max_model_calls": vnext_budget["max_model_calls"],
            },
        },
        "desktop_action_queue": DESKTOP_ACTION_QUEUE.status(),
        # The platform status is polled by the workbench.  Never duplicate a
        # completed job's full ARP/result payload here; selected run/job detail
        # has dedicated endpoints.
        "runtime_jobs": RUNTIME_JOBS.list(20, include_result=False),
        "current_run": _current_run_projection(),
    }


# ------------------------------------------------------- unified control plane
@app.get("/api/v2/platform")
def platform_overview() -> dict[str, Any]:
    return {
        "api_version": "2",
        "product": "WinPilot ARP",
        "status": status(),
        "current_run": _current_run_projection(),
        "capabilities": [item.model_dump(mode="json") for item in capability_catalog()],
        "contracts": {
            "arp": "arp/1",
            "task_contract": "task-contract/1",
            "automation_ir": "automation-ir/1",
            "event_stream": "/ws/events",
            "mcp": "/mcp",
        },
    }


@app.get("/api/v2/runs")
def platform_runs(limit: int = 50) -> dict[str, Any]:
    runs = RUN_PROJECTIONS.list(max(1, min(limit, 200)))
    return {
        "ok": True,
        "runs": [_thread_result_projection(run) for run in runs],
    }


@app.get("/api/v2/jobs")
def platform_jobs(limit: int = 50) -> dict[str, Any]:
    return {
        "ok": True,
        "jobs": RUNTIME_JOBS.list(
            max(1, min(limit, 200)),
            include_result=False,
        ),
    }


@app.get("/api/v2/desktop-context")
def desktop_context(exclude_process_id: int = 0) -> dict[str, Any]:
    """Give the native Visual Actor a neutral full-desktop workspace."""
    from .tasking.execution_context import capture_desktop_workspace_context

    return {
        "ok": True,
        "target": capture_desktop_workspace_context(),
    }


@app.get("/api/v2/jobs/{job_id}")
def platform_job(job_id: str) -> dict[str, Any]:
    job = RUNTIME_JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "runtime job not found")
    return {"ok": True, "job": job.snapshot()}


def _artifact_store_for_run(
    run_id: str,
    execution: ArpExecution | None = None,
) -> ArtifactStore | None:
    if execution is not None:
        live_store = execution.runtime_state.get("artifact_store")
        if isinstance(live_store, ArtifactStore):
            return live_store
    root = TRACES_DIR / run_id / "artifacts"
    if not (root / "manifest.json").is_file():
        return None
    try:
        return ArtifactStore(run_id, root)
    except (OSError, TypeError, ValueError):
        return None


def _arp_detail_snapshot(
    run_id: str,
    execution: ArpExecution | None = None,
) -> dict[str, Any] | None:
    snapshot = execution.to_dict() if execution else RUN_PROJECTIONS.arp_snapshot(run_id)
    if snapshot is None:
        return None
    store = _artifact_store_for_run(run_id, execution)
    if store is not None:
        snapshot["artifacts"] = store.snapshot()
    return snapshot


@app.post("/api/v2/jobs/{job_id}/cancel")
def cancel_platform_job(job_id: str) -> dict[str, Any]:
    job = RUNTIME_JOBS.cancel(job_id)
    if not job:
        raise HTTPException(404, "runtime job not found")
    run_id = str(job.metadata.get("run_id") or "")
    execution = ARP_RUNS.get(run_id) if run_id else None
    if execution:
        execution.cancel()
    return {"ok": True, "job": job.snapshot()}


@app.get("/api/v2/runs/current")
def platform_current_run() -> dict[str, Any]:
    projection = _current_run_projection()
    execution = ARP_RUNS.current()
    run_id = str((projection or {}).get("run_id") or "")
    if execution is not None and execution.run_id != run_id:
        execution = None
    return {
        "ok": True,
        "run": projection,
        "arp": _arp_detail_snapshot(run_id, execution) if run_id else None,
    }


@app.get("/api/v2/runs/{run_id}")
def platform_run(run_id: str) -> dict[str, Any]:
    projection = _thread_result_projection(RUN_PROJECTIONS.get(run_id))
    arp = ARP_RUNS.get(run_id)
    if not projection and not arp:
        raise HTTPException(404, "run not found")
    return {
        "ok": True,
        "run": projection,
        "arp": _arp_detail_snapshot(run_id, arp),
    }


@app.post("/api/v2/runs/{run_id}/cancel")
def cancel_platform_run(run_id: str) -> dict[str, Any]:
    """Cancel an ARP run through one frontend-safe route."""
    projection = RUN_PROJECTIONS.get(run_id)
    execution = ARP_RUNS.get(run_id)
    job = STATE.job_for_run(run_id)

    if not any((projection, execution, job)):
        raise HTTPException(404, "run not found")

    execution_active = bool(
        execution
        and execution.status.value in {"queued", "running", "paused"}
    )
    job_active = bool(job and job.status in RUNTIME_JOBS.ACTIVE)
    if not any((execution_active, job_active)):
        raise HTTPException(409, "run is not active")

    if execution_active:
        execution.cancel()
    if job:
        RUNTIME_JOBS.cancel(job.id)

    return {
        "ok": True,
        "run": RUN_PROJECTIONS.get(run_id),
        "arp": _arp_detail_snapshot(run_id, execution),
    }


@app.post("/api/v2/runs/{run_id}/pause")
def pause_platform_run(run_id: str) -> dict[str, Any]:
    """Cooperatively pause an active ARP run at its next safe boundary."""
    execution = ARP_RUNS.get(run_id)
    if execution is None:
        raise HTTPException(404, "run not found")
    if not execution.pause():
        raise HTTPException(409, "run cannot be paused")
    return {
        "ok": True,
        "run": RUN_PROJECTIONS.get(run_id),
        "arp": _arp_detail_snapshot(run_id, execution),
    }


@app.post("/api/v2/runs/{run_id}/resume")
def resume_platform_run(run_id: str) -> dict[str, Any]:
    """Resume a run paused manually by the frontend."""
    execution = ARP_RUNS.get(run_id)
    if execution is None:
        raise HTTPException(404, "run not found")
    if not execution.resume():
        raise HTTPException(409, "run is not manually paused")
    return {
        "ok": True,
        "run": RUN_PROJECTIONS.get(run_id),
        "arp": _arp_detail_snapshot(run_id, execution),
    }


@app.get("/api/v2/runs/{run_id}/artifacts/{artifact_id}")
def platform_run_artifact(run_id: str, artifact_id: str):
    """Serve only a hash-verified artifact registered to the requested run."""
    execution = ARP_RUNS.get(run_id)
    projection = RUN_PROJECTIONS.get(run_id)
    if not execution and (
        not projection or str(projection.get("kind") or "") != "arp"
    ):
        raise HTTPException(404, "run not found")
    store = _artifact_store_for_run(run_id, execution)
    if store is None:
        raise HTTPException(404, "artifact store not found")
    try:
        artifact = store.get(artifact_id)
    except (KeyError, ValueError, OSError) as exc:
        raise HTTPException(404, str(exc)) from exc
    if artifact.run_id != run_id:
        raise HTTPException(404, "artifact does not belong to the requested run")
    return FileResponse(
        artifact.managed_path,
        media_type=artifact.mime_type,
        headers={
            "Cache-Control": "private, max-age=3600",
            "ETag": f'"{artifact.sha256}"',
            "X-WinPilot-Artifact-Id": artifact.artifact_id,
        },
    )


@app.post("/api/v2/arp/validate")
def validate_arp_graph(graph: ArpGraph) -> dict[str, Any]:
    return validate_graph(graph).model_dump(mode="json")


@app.post("/api/v2/automation-ir/validate")
def validate_automation_ir_execution(automation: dict[str, Any]) -> dict[str, Any]:
    if automation_runtime_kind(automation) == "wps_replay":
        error = validate_automation_runtime(automation)
        if error:
            return GraphValidation(valid=False, errors=[error]).model_dump(mode="json")
        return GraphValidation(
            valid=True,
            warnings=["WPS replay runtime is sequential and has no ARP entry node"],
        ).model_dump(mode="json")
    try:
        graph = arp_graph_from_automation_ir(automation, require_valid=False)
    except ValueError as exc:
        return GraphValidation(valid=False, errors=[str(exc)]).model_dump(mode="json")
    return validate_graph(graph).model_dump(mode="json")


def _terminalize_unstarted_vnext_execution(
    execution: ArpExecution,
    *,
    status: ArpRunStatus,
    detail: str,
    trace_status: str,
) -> bool:
    """Close one vNext execution that never entered ``ArpExecution.run``."""

    if (
        execution.runtime_state.get("runtime_version") != "vnext"
        or status not in {ArpRunStatus.CANCELED, ArpRunStatus.FAILED}
    ):
        return False
    with execution._lock:
        lifecycle = execution.runtime_state.get("runtime_lifecycle")
        if isinstance(lifecycle, dict) and lifecycle.get("terminal") is True:
            return False
        if execution.started_at is not None:
            return False
        if execution.status not in {
            ArpRunStatus.QUEUED,
            ArpRunStatus.PAUSED,
            ArpRunStatus.CANCELED,
            ArpRunStatus.FAILED,
        }:
            return False
        if status is ArpRunStatus.CANCELED:
            execution.stop_event.set()
            execution.pause_controller.resume()
        execution.status = status
        execution.current_node_id = None
        execution.error = str(detail)
        execution.finished_at = execution.finished_at or time.time()
        execution.runtime_state["no_legacy_fallback"] = True
        execution.runtime_state["trace_status"] = str(trace_status)
        execution.runtime_state["trace_error"] = (
            str(detail) if trace_status == "failed_to_start" else ""
        )
        execution.runtime_state["runtime_lifecycle"] = {
            "phase": "pre_start",
            "terminal": True,
            "status": status.value,
            "detail": str(detail),
            "trace_status": str(trace_status),
            "no_legacy_fallback": True,
        }
    execution._publish_run_done()
    return True


def _finalize_vnext_trace(
    execution: ArpExecution,
    *,
    trace_status: str,
    detail: str = "",
) -> None:
    """Freeze the trace outcome into the vNext execution before its done event.

    Trace recording is a diagnostic side channel: it records ``trace_status``
    and ``trace_error`` but never rewrites the run status. A completed mission
    must not become FAILED because the recorder stopped late or lost ownership.
    """

    with execution._lock:
        execution.runtime_state["no_legacy_fallback"] = True
        execution.runtime_state["trace_status"] = str(trace_status)
        execution.runtime_state["trace_error"] = str(detail)
        execution.finished_at = time.time()
        execution.runtime_state["runtime_lifecycle"] = {
            "phase": "finalized",
            "terminal": True,
            "status": execution.status.value,
            "detail": str(execution.error or ""),
            "trace_status": str(trace_status),
            "no_legacy_fallback": True,
        }


def _start_arp_execution(
    graph: ArpGraph,
    inputs: dict[str, Any],
    *,
    runtime_version: ArpRuntimeVersion = ArpRuntimeVersion.LEGACY,
    frozen_config: dict[str, Any] | None = None,
    goal_admission: dict[str, Any] | None = None,
    thread_id: str = "",
    turn_id: str = "",
    thread_history: tuple[dict[str, str], ...] = (),
) -> ArpExecution:
    if (
        goal_admission is not None
        and runtime_version is not ArpRuntimeVersion.VNEXT
    ):
        raise RuntimeError(
            "server-owned goal admission may only start the vNext runtime"
        )
    validation = validate_graph(graph)
    if not validation.valid:
        raise ValueError("; ".join(validation.errors))
    if human_mod.RECORDER.is_recording():
        raise RuntimeError("a human or apprentice recording is active")

    job_ref: dict[str, str] = {}
    trace_owner_lock = threading.Lock()
    trace_started = False

    def on_start(started: ArpExecution) -> None:
        nonlocal trace_started
        trace_mod.RECORDER.start(started.run_id)
        if runtime_version is ArpRuntimeVersion.VNEXT:
            with trace_owner_lock:
                trace_started = True
            started.runtime_state["trace_status"] = "recording"

    def on_finish(finished: ArpExecution) -> None:
        nonlocal trace_started
        if runtime_version is ArpRuntimeVersion.VNEXT:
            with trace_owner_lock:
                owns_trace = trace_started
                trace_started = False
            if not owns_trace:
                return
            try:
                recorder_owns_run = trace_mod.RECORDER.owns(
                    finished.run_id
                )
            except Exception as exc:
                _finalize_vnext_trace(
                    finished,
                    trace_status="stop_failed",
                    detail=str(exc),
                )
                return
            if not recorder_owns_run:
                _finalize_vnext_trace(
                    finished,
                    trace_status="ownership_lost",
                    detail="vNext trace recorder no longer owns this run",
                )
                return
            try:
                trace_mod.RECORDER.stop()
            except Exception as exc:
                _finalize_vnext_trace(
                    finished,
                    trace_status="stop_failed",
                    detail=str(exc),
                )
                return
            _finalize_vnext_trace(
                finished,
                trace_status="stopped",
            )
            return
        from .orchestrator.adapters import finalize_memory_feedback

        try:
            finalize_memory_feedback(finished)
        finally:
            trace_mod.RECORDER.stop()

    if runtime_version is ArpRuntimeVersion.VNEXT:
        if frozen_config is None:
            raise RuntimeError("vNext live Agent config was not frozen")
        if not thread_id or not turn_id:
            thread_id, turn_id, generated_history = (
                AGENT_THREADS.begin_turn(graph.name)
            )
            thread_history = tuple(generated_history)
        from .orchestrator.live_agent import (
            prepare_live_vnext_execution,
        )

        try:
            execution = prepare_live_vnext_execution(
                graph,
                inputs,
                frozen_config,
                run_manager=ARP_RUNS,
                thread_id=thread_id,
                turn_id=turn_id,
                history=thread_history,
            )
        except Exception as exc:
            AGENT_THREADS.fail_turn(turn_id, str(exc))
            raise
    else:
        from .orchestrator.adapters import default_handlers

        execution = ARP_RUNS.prepare(
            graph,
            default_handlers(),
            inputs=inputs,
        )
    execution.runtime_state["runtime_version"] = runtime_version.value
    if goal_admission is not None:
        execution.runtime_state["goal_admission"] = dict(goal_admission)
    if runtime_version is ArpRuntimeVersion.VNEXT:
        try:
            AGENT_THREADS.bind_run(
                thread_id=thread_id,
                turn_id=turn_id,
                run_id=execution.run_id,
            )
        except Exception as exc:
            AGENT_THREADS.fail_turn(turn_id, str(exc))
            raise
    job_id = f"arp-job-{execution.run_id}"
    job_ref["job_id"] = job_id
    with STATE.lock:
        STATE.run_jobs[execution.run_id] = job_id

    def worker(_stop: threading.Event) -> dict[str, Any]:
        try:
            snapshot = ARP_RUNS.run_prepared(
                execution,
                on_start=on_start,
                on_finish=on_finish,
            )
        except Exception as exc:
            if runtime_version is not ArpRuntimeVersion.VNEXT:
                raise
            _terminalize_unstarted_vnext_execution(
                execution,
                status=ArpRunStatus.FAILED,
                detail=f"vNext trace failed to start: {exc}",
                trace_status="failed_to_start",
            )
            snapshot = execution.to_dict()
        if (
            runtime_version is ArpRuntimeVersion.VNEXT
            and execution.started_at is None
            and snapshot.get("status") == ArpRunStatus.CANCELED.value
        ):
            _terminalize_unstarted_vnext_execution(
                execution,
                status=ArpRunStatus.CANCELED,
                detail="vNext run canceled before acquiring runtime resources",
                trace_status="not_started",
            )
            snapshot = execution.to_dict()
        ok = snapshot.get("status") in {"done", "reply"}
        return {
            "ok": ok,
            "run_id": execution.run_id,
            "runtime_version": runtime_version.value,
            "run": snapshot,
            **({"error": snapshot.get("error") or "ARP run failed"} if not ok else {}),
        }

    def on_job_finish(job: RuntimeJob) -> None:
        if (
            runtime_version is not ArpRuntimeVersion.VNEXT
            or job.started_at is not None
        ):
            return
        canceled = job.status == "canceled" or job.stop_event.is_set()
        _terminalize_unstarted_vnext_execution(
            execution,
            status=(
                ArpRunStatus.CANCELED
                if canceled
                else ArpRunStatus.FAILED
            ),
            detail=(
                "vNext run canceled before acquiring runtime resources"
                if canceled
                else (
                    job.error
                    or "vNext runtime job failed before worker start"
                )
            ),
            trace_status="not_started",
        )

    try:
        RUNTIME_JOBS.submit(
            "arp",
            worker,
            resources=(_INTERACTIVE_RUNTIME_RESOURCE,),
            metadata={
                "run_id": execution.run_id,
                "graph_id": graph.id,
                "graph_name": graph.name,
                "runtime_version": runtime_version.value,
            },
            job_id=job_id,
            stop_event=execution.stop_event,
            on_finish=(
                on_job_finish
                if runtime_version is ArpRuntimeVersion.VNEXT
                else None
            ),
        )
    except Exception as exc:
        if runtime_version is ArpRuntimeVersion.VNEXT:
            _terminalize_unstarted_vnext_execution(
                execution,
                status=ArpRunStatus.FAILED,
                detail=f"vNext runtime job submission failed: {exc}",
                trace_status="not_started",
            )
        with STATE.lock:
            STATE.run_jobs.pop(execution.run_id, None)
        if runtime_version is ArpRuntimeVersion.VNEXT:
            AGENT_THREADS.fail_turn(turn_id, str(exc))
        raise
    return execution


@app.post("/api/v2/flow-fragments/proposals")
def propose_flow_fragment(req: FlowFragmentProposalRequest) -> dict[str, Any]:
    """Return an inert proposal; the renderer remains the only document owner."""
    frozen_config = CONFIG.snapshot()

    def worker(stop_event: threading.Event) -> dict[str, Any]:
        from .orchestrator.live_agent import propose_live_flow_fragment

        return propose_live_flow_fragment(
            req.goal,
            base_fingerprint=req.base_fingerprint,
            document=req.document,
            catalog=req.catalog,
            config_snapshot=frozen_config,
            stop_event=stop_event,
        )

    job = RUNTIME_JOBS.submit(
        "flow_fragment",
        worker,
        resources=(_INTERACTIVE_RUNTIME_RESOURCE,),
        metadata={"purpose": "flow_fragment_proposal"},
    )
    return {
        "ok": True,
        "proposal_job_id": job.id,
        "job": job.snapshot(include_result=False),
    }


@app.post("/api/v2/goals/runs")
def start_goal_run(req: GoalRunRequest) -> dict[str, Any]:
    """Admit one product goal without accepting a client-owned graph/runtime."""
    turn_id = ""
    try:
        frozen_config = CONFIG.snapshot()
        vnext_config = frozen_config.get("vnext")
        admission = admit_goal(
            req.goal,
            live_gui_enabled=(
                isinstance(vnext_config, dict)
                and vnext_config.get("live_agent_enabled") is True
            ),
        )
        from .orchestrator.live_agent import (
            build_exact_live_vnext_graph,
        )

        graph = build_exact_live_vnext_graph(admission.goal)
        thread_id, turn_id, history = AGENT_THREADS.begin_turn(
            admission.goal,
            thread_id=req.thread_id.strip(),
        )
        audit = admission.to_dict()
        execution = _start_arp_execution(
            graph,
            {},
            runtime_version=admission.runtime_version,
            frozen_config=frozen_config,
            goal_admission=audit,
            thread_id=thread_id,
            turn_id=turn_id,
            thread_history=tuple(history),
        )
    except GoalAdmissionUnavailable as exc:
        raise HTTPException(409, str(exc)) from exc
    except AgentThreadError as exc:
        if turn_id:
            AGENT_THREADS.fail_turn(turn_id, str(exc))
        raise HTTPException(409, str(exc)) from exc
    except (GoalAdmissionError, ValueError) as exc:
        if turn_id:
            AGENT_THREADS.fail_turn(turn_id, str(exc))
        logger.warning("Goal admission rejected before run start: %s", exc)
        raise HTTPException(400, str(exc)) from exc
    except RuntimeError as exc:
        if turn_id:
            AGENT_THREADS.fail_turn(turn_id, str(exc))
        raise HTTPException(409, str(exc)) from exc
    return {
        "ok": True,
        "run_id": execution.run_id,
        "thread_id": thread_id,
        "turn_id": turn_id,
        "runtime_version": admission.runtime_version.value,
        "admission": audit,
        "run": execution.to_dict(),
        "graph": graph.model_dump(mode="json"),
    }


@app.get("/api/v2/threads/{thread_id}")
def get_agent_thread(thread_id: str) -> dict[str, Any]:
    try:
        thread = AGENT_THREADS.snapshot(thread_id)
    except AgentThreadError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"ok": True, "thread": thread}


@app.get("/api/v2/threads")
def list_agent_threads(limit: int = 100) -> dict[str, Any]:
    return {
        "ok": True,
        "threads": AGENT_THREADS.snapshots(max(1, min(limit, 100))),
    }


@app.post("/api/v2/goals/runs/{run_id}/desktop-lease")
def bind_goal_desktop_lease(
    run_id: str,
    req: DesktopLeaseRequest,
) -> dict[str, Any]:
    execution = ARP_RUNS.get(run_id)
    if execution is None:
        raise HTTPException(404, "run not found")
    if execution.status not in {
        ArpRunStatus.QUEUED,
        ArpRunStatus.RUNNING,
        ArpRunStatus.PAUSED,
    }:
        raise HTTPException(409, "run is already terminal")
    from .orchestrator.live_agent import (
        DesktopLeaseError,
        DesktopLeaseSlot,
    )

    lease = execution.runtime_state.get("_desktop_lease")
    if not isinstance(lease, DesktopLeaseSlot):
        raise HTTPException(409, "run has no desktop lease")
    try:
        inputs = bind_client_execution_context(
            req.execution_context,
            task_id=execution.graph.id,
        )
        target = lease.bind(
            run_id=run_id,
            lease_id=req.lease_id,
            inputs=inputs,
        )
    except (DesktopLeaseError, GoalAdmissionError, ValueError) as exc:
        execution.runtime_state["desktop_lease"] = lease.snapshot()
        raise HTTPException(409, str(exc)) from exc
    execution.inputs.update(inputs)
    execution.runtime_state["desktop_lease"] = lease.snapshot()
    execution.runtime_state["target_binding_id"] = target.binding_id
    return {
        "ok": True,
        "run_id": run_id,
        "lease": lease.snapshot(),
        "target": target.trace_identity(),
    }


@app.post("/api/v2/goals/runs/{run_id}/desktop-lease/fail")
def fail_goal_desktop_lease(
    run_id: str,
    req: DesktopLeaseFailureRequest,
) -> dict[str, Any]:
    execution = ARP_RUNS.get(run_id)
    if execution is None:
        raise HTTPException(404, "run not found")
    from .orchestrator.live_agent import (
        DesktopLeaseError,
        DesktopLeaseSlot,
    )

    lease = execution.runtime_state.get("_desktop_lease")
    if not isinstance(lease, DesktopLeaseSlot):
        raise HTTPException(409, "run has no desktop lease")
    try:
        lease.fail(
            run_id=run_id,
            lease_id=req.lease_id,
            reason=req.reason,
        )
    except DesktopLeaseError as exc:
        raise HTTPException(409, str(exc)) from exc
    execution.runtime_state["desktop_lease"] = lease.snapshot()
    return {"ok": True, "run_id": run_id, "lease": lease.snapshot()}


@app.post("/api/v2/arp/runs")
def start_arp_run(req: ArpRunRequest) -> dict[str, Any]:
    try:
        graph = req.graph
        runtime_version = req.runtime_version
        frozen_config: dict[str, Any] | None = None
        if runtime_version is not ArpRuntimeVersion.LEGACY:
            frozen_config = CONFIG.snapshot()
            vnext_config = frozen_config.get("vnext")
            if (
                not isinstance(vnext_config, dict)
                or vnext_config.get("live_agent_enabled") is not True
            ):
                raise RuntimeError(
                    "vNext live Agent is disabled; no legacy fallback was run"
                )
        execution = _start_arp_execution(
            graph,
            req.inputs,
            runtime_version=runtime_version,
            frozen_config=frozen_config,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {
        "ok": True,
        "run_id": execution.run_id,
        "runtime_version": req.runtime_version.value,
        "run": execution.to_dict(),
        "graph": execution.graph.model_dump(mode="json"),
        "contract": execution.graph.metadata.get("task_contract"),
    }


@app.post("/api/v2/automation-ir/runs")
def start_automation_ir_run(req: AutomationIrRunRequest) -> dict[str, Any]:
    error = validate_automation_runtime(req.automation)
    if error:
        raise HTTPException(400, error)
    runtime_kind = automation_runtime_kind(req.automation)
    if runtime_kind == "wps_replay":
        replay_id = f"replay-{uuid.uuid4().hex[:12]}"
        script = wps_from_automation_ir(req.automation)
        stop_event = threading.Event()

        def replay_worker(runtime_stop: threading.Event) -> dict[str, Any]:
            BUS.publish(
                "replay",
                state="job_start",
                replay_id=replay_id,
                automation_id=req.automation.get("id"),
            )
            result = trace_mod.Replayer(runtime_stop).run_script(
                script,
                script_name=f"{req.automation.get('id') or 'automation-ir'}.automation.json",
            )
            BUS.publish(
                "replay",
                state="job_done",
                replay_id=replay_id,
                ok=bool(result.get("ok", False)),
                status="stopped" if runtime_stop.is_set() else "done",
            )
            return result

        job = RUNTIME_JOBS.submit(
            "replay",
            replay_worker,
            resources=(_INTERACTIVE_RUNTIME_RESOURCE,),
            metadata={"automation_id": req.automation.get("id")},
            job_id=replay_id,
            stop_event=stop_event,
        )
        return {
            "ok": True,
            "runtime_kind": runtime_kind,
            "automation_id": req.automation.get("id"),
            "replay_id": replay_id,
            "job": _public_runtime_replay_job(job),
        }
    try:
        graph = arp_graph_from_automation_ir(req.automation)
        execution = _start_arp_execution(graph, req.inputs)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {
        "ok": True,
        "runtime_kind": runtime_kind,
        "automation_id": req.automation.get("id"),
        "run_id": execution.run_id,
        "run": execution.to_dict(),
    }


@app.get("/api/v2/arp/runs/{run_id}")
def get_arp_run(run_id: str) -> dict[str, Any]:
    execution = ARP_RUNS.get(run_id)
    if not execution:
        raise HTTPException(404, "ARP run not found")
    return {"ok": True, "run": execution.to_dict()}


@app.post("/api/v2/arp/runs/{run_id}/cancel")
def cancel_arp_run(run_id: str) -> dict[str, Any]:
    if not ARP_RUNS.cancel(run_id):
        raise HTTPException(404, "ARP run not found")
    job = STATE.job_for_run(run_id)
    if job:
        RUNTIME_JOBS.cancel(job.id)
    return {"ok": True, "run": ARP_RUNS.get(run_id).to_dict()}


@app.get("/api/v2/integrations/chatgpt")
async def chatgpt_integration(request: Request) -> dict[str, Any]:
    """Legacy ChatGPT desktop MCP bridge was removed with the old Agent line.

    The endpoint stays as a truthful status source for the settings panel.
    """
    return {
        "ok": False,
        "integration": "chatgpt_desktop",
        "transport": "removed",
        "planner": "unavailable",
        "requires_openai_api_key": False,
        "enabled": False,
        "server_ready": False,
        "client_active": False,
        "connection_status": "removed",
        "last_client_seen_at": None,
        "request_count": 0,
        "last_client": "",
        "security_mode": "loopback_only",
        "runtime": {},
        "current_run": None,
        "capabilities": [item.model_dump(mode="json") for item in capability_catalog()],
        "error": "legacy ChatGPT desktop MCP bridge removed",
    }


@app.get("/api/config")
def get_config() -> dict[str, Any]:
    snapshot = CONFIG.snapshot()
    key = snapshot.get("api", {}).get("api_key", "")
    if key:
        snapshot["api"]["api_key"] = "***" + key[-4:]
    vkey = snapshot.get("vlm", {}).get("api_key", "")
    if vkey:
        snapshot["vlm"]["api_key"] = "***" + vkey[-4:]
    return snapshot


def _canonicalize_model_aliases(patch: dict[str, Any]) -> None:
    """Normalize common UI spellings before persisting provider model IDs."""
    for section in ("api", "vlm"):
        sec = patch.get(section)
        if not isinstance(sec, dict):
            continue
        model = str(sec.get("model") or "").strip()
        if model.casefold() == "mimo-v2.5pro":
            sec["model"] = "mimo-v2.5-pro"


@app.patch("/api/config")
def patch_config(patch: dict[str, Any]) -> dict[str, Any]:
    # Don't let the masked placeholder overwrite the real key.
    for section in ("api", "vlm"):
        sec = patch.get(section)
        if isinstance(sec, dict) and str(sec.get("api_key", "")).startswith("***"):
            sec.pop("api_key")
    _canonicalize_model_aliases(patch)
    try:
        CONFIG.update(patch)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    BUS.publish("config", changed=list(patch.keys()))
    return get_config()


@app.get("/api/traces")
def traces() -> list[dict[str, Any]]:
    """Trace run browser for the Electron Platform workbench (Studio surface)."""
    return trace_mod.list_traces()


_PUBLIC_TRACE_DETAIL_LIMIT = 2_000


@app.get("/api/trace/{run_id}")
def trace_detail(
    run_id: str,
    limit: int = _PUBLIC_TRACE_DETAIL_LIMIT,
) -> list[dict[str, Any]]:
    events = trace_mod.read_trace(
        _public_trace_run_id(run_id),
        limit=max(1, min(int(limit), _PUBLIC_TRACE_DETAIL_LIMIT)),
        include_integrity=True,
    )
    if not events or (
        len(events) == 1
        and events[0].get("type") == "trace_read_integrity"
        and events[0].get("total_lines") == 0
    ):
        raise HTTPException(404, "trace 不存在")
    return events


@app.get("/api/trace/{run_id}/shot/{name}")
def trace_screenshot(run_id: str, name: str):
    run_id = _public_trace_run_id(run_id)
    name = _public_trace_leaf(
        name,
        what="trace screenshot",
        image=True,
    )
    if not (TRACES_DIR / f"{run_id}.jsonl").is_file():
        raise HTTPException(404, "trace screenshot not found")
    path = _ensure_inside(TRACES_DIR / run_id / name, TRACES_DIR, "截图")
    path = _ensure_inside(
        path,
        TRACES_DIR / run_id,
        "trace screenshot",
    )
    if not path.is_file():
        raise HTTPException(404, "截图不存在")
    return FileResponse(path)


@app.post("/api/export/thread/{thread_id}/ir")
def export_thread_ir(thread_id: str, req: ThreadExportRequest | None = None) -> dict[str, Any]:
    try:
        path = trace_mod.export_thread_automation_ir(
            _public_trace_run_id(thread_id),
            name=(req.name if req else None),
            run_ids=[_public_trace_run_id(item) for item in req.run_ids] if req else None,
        )
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, "ir": path.name}


@app.post("/api/export/{run_id}")
def export(run_id: str, req: ExportRequest | None = None) -> dict[str, Any]:
    try:
        path = trace_mod.export_script(
            _public_trace_run_id(run_id),
            name=(req.name if req else None),
        )
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, "script": path.name}


@app.post("/api/export/{run_id}/ir")
def export_ir(run_id: str, req: ExportRequest | None = None) -> dict[str, Any]:
    try:
        path = trace_mod.export_automation_ir(
            _public_trace_run_id(run_id),
            name=(req.name if req else None),
        )
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, "ir": path.name}


def _script_path(file: str) -> Path:
    if not (file.endswith(".wps.json") or file.endswith(".automation.json")):
        raise HTTPException(400, "file must end in .automation.json or .wps.json")
    return _ensure_inside(SCRIPTS_DIR / file, SCRIPTS_DIR, "脚本")


def _ir_path(file: str) -> Path:
    if not file.endswith(".automation.json"):
        raise HTTPException(400, "IR file name must end with .automation.json")
    return _ensure_inside(SCRIPTS_DIR / file, SCRIPTS_DIR, "Automation IR")


@app.get("/api/script/{file}")
def get_script(file: str) -> dict[str, Any]:
    """Read canonical Automation IR or a legacy WPS compatibility document."""
    path = _script_path(file)
    if not path.exists():
        raise HTTPException(404, "脚本不存在")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise HTTPException(400, f"脚本解析失败: {exc}") from exc
    if data.get("format") == "automation-ir/1":
        error = validate_automation_ir(data)
    else:
        error = trace_mod.validate_script(data)
    if error:
        raise HTTPException(400, f"script validation failed: {error}")
    return data


@app.get("/api/script/{file}/ir")
def get_script_ir(file: str) -> dict[str, Any]:
    script = get_script(file)
    ir = script if script.get("format") == "automation-ir/1" else automation_ir_from_wps(script)
    err = validate_automation_ir(ir)
    if err:
        raise HTTPException(400, f"Automation IR validation failed: {err}")
    return ir


@app.get("/api/automation-ir/{file}")
def get_automation_ir(file: str) -> dict[str, Any]:
    path = _ir_path(file)
    if not path.exists():
        raise HTTPException(404, "Automation IR not found")
    try:
        ir = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise HTTPException(400, f"Automation IR parse failed: {exc}") from exc
    err = validate_automation_ir(ir)
    if err:
        raise HTTPException(400, f"Automation IR validation failed: {err}")
    return ir


@app.get("/api/scripts")
def scripts() -> list[dict[str, Any]]:
    """Script library for the Electron Platform workbench (Studio surface)."""
    return trace_mod.list_scripts()


@app.put("/api/script/{file}")
def put_script(file: str, req: ScriptSaveRequest) -> dict[str, Any]:
    """Save an edited .wps.json after structural validation (reject unknown
    tools / malformed nesting before it can ever be replayed)."""
    path = _script_path(file)
    err = (
        validate_automation_runtime(req.script)
        if req.script.get("format") == "automation-ir/1"
        else trace_mod.validate_script(req.script)
    )
    if err:
        raise HTTPException(400, f"脚本校验失败: {err}")
    SCRIPTS_DIR.mkdir(exist_ok=True)
    path.write_text(
        json.dumps(req.script, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"ok": True, "file": path.name}


@app.post("/api/v2/replay/apprentice/start")
def apprentice_start(req: ApprenticeStartRequest) -> dict[str, Any]:
    with STATE.lock:
        if STATE.busy():
            raise HTTPException(409, "another agent, replay, or recording is active")
        try:
            session = apprentice_mod.APPRENTICE.start(
                req.task,
                name=req.name,
                app=req.app,
                verification=req.verification,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except Exception as exc:
            raise HTTPException(500, f"cannot start apprentice lesson: {exc}") from exc
    return {"ok": True, "session": session}


@app.post("/api/v2/replay/apprentice/stop")
def apprentice_stop() -> dict[str, Any]:
    try:
        return {"ok": True, **apprentice_mod.APPRENTICE.stop()}
    except RuntimeError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(500, f"cannot finish apprentice lesson: {exc}") from exc


@app.get("/api/v2/replay/apprentice/status")
def apprentice_status() -> dict[str, Any]:
    return apprentice_mod.APPRENTICE.status()


@app.post("/api/replay")
def replay(req: ReplayRequest) -> dict[str, Any]:
    script_path = _ensure_inside(SCRIPTS_DIR / req.script, SCRIPTS_DIR, "脚本")
    if not script_path.exists():
        raise HTTPException(404, "脚本不存在")
    try:
        replay_document = json.loads(script_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise HTTPException(400, f"script parse failed: {exc}") from exc
    if (
        replay_document.get("format") == "automation-ir/1"
        and automation_runtime_kind(replay_document) == "arp_graph"
    ):
        raise HTTPException(
            400,
            "ARP-backed Automation IR must run through /api/v2/automation-ir/runs",
        )
    with STATE.lock:
        if human_mod.RECORDER.is_recording():
            raise HTTPException(409, "已有任务在运行")
        replay_id = f"replay-{uuid.uuid4().hex[:12]}"
        stop_event = threading.Event()
        def worker(runtime_stop: threading.Event) -> dict[str, Any]:
            BUS.publish("replay", state="job_start", replay_id=replay_id, script=req.script)
            try:
                result = trace_mod.replay(script_path, stop_event=runtime_stop)
                status = "stopped" if runtime_stop.is_set() and not result.get("ok", False) else "done"
                BUS.publish("replay", state="job_done", replay_id=replay_id,
                            ok=bool(result.get("ok", False)), status=status)
                return result
            except Exception as exc:
                logger.exception("重放失败")
                BUS.publish("replay", state="error", replay_id=replay_id, error=str(exc))
                raise

        job = RUNTIME_JOBS.submit(
            "replay",
            worker,
            resources=(_INTERACTIVE_RUNTIME_RESOURCE,),
            metadata={"script": req.script},
            job_id=replay_id,
            stop_event=stop_event,
        )
        public = _public_runtime_replay_job(job)
    return {"ok": True, "replay_id": replay_id, "job": public}


@app.get("/api/replay/{replay_id}")
def replay_status(replay_id: str) -> dict[str, Any]:
    job = RUNTIME_JOBS.get(replay_id)
    if not job or job.kind != "replay":
        raise HTTPException(404, "replay job not found")
    return _public_runtime_replay_job(job)


@app.post("/api/replay/{replay_id}/cancel")
def replay_cancel(replay_id: str) -> dict[str, Any]:
    job = RUNTIME_JOBS.get(replay_id)
    if not job or job.kind != "replay":
        raise HTTPException(404, "replay job not found")
    if job.status in {"done", "failed", "canceled"}:
        return {"ok": True, "job": _public_runtime_replay_job(job)}
    job = RUNTIME_JOBS.cancel(replay_id)
    public = _public_runtime_replay_job(job)
    BUS.publish("replay", state="job_cancel", replay_id=replay_id)
    return {"ok": True, "job": public}


@app.get("/api/memory")
def memory_list() -> list[dict[str, Any]]:
    from .memory import experience
    return experience.all_recipes()


@app.post("/api/memory")
def memory_add(req: MemoryAddRequest) -> dict[str, Any]:
    from .memory import experience
    if not req.task.strip():
        raise HTTPException(400, "task 不能为空")
    return experience.add(
        req.task.strip(),
        req.app.strip(),
        req.steps,
        conditions=req.conditions,
        evidence=req.evidence,
    )


@app.post("/api/memory/search")
def memory_graph_search(req: MemorySearchRequest) -> dict[str, Any]:
    from .memory import experience, retriever

    query = req.query.strip()
    if not query:
        raise HTTPException(400, "query cannot be empty")
    store = experience._store()
    _frame, candidates = retriever.recall(query, store, limit=max(1, min(req.limit, 50)))
    return {
        "ok": True,
        "query": query,
        "results": [
            {
                "id": c.node.get("id"),
                "kind": c.node.get("kind"),
                "title": c.node.get("title"),
                "summary": c.node.get("summary"),
                "status": c.node.get("status"),
                "confidence": c.node.get("confidence"),
                "app": c.node.get("app"),
                "score": round(c.score, 3),
                "reasons": c.reasons,
            }
            for c in candidates[: req.limit]
        ],
    }


@app.put("/api/memory/{recipe_id}")
def memory_update(recipe_id: str, req: MemoryPatchRequest) -> dict[str, Any]:
    from .memory import experience
    if not _RECIPE_ID_RE.fullmatch(recipe_id):
        raise HTTPException(404, "记忆条目不存在")
    # exclude_unset distinguishes "field not sent" from explicit false/empty.
    patch = req.model_dump(exclude_unset=True)
    if not patch:
        raise HTTPException(400, "没有要修改的字段")
    if patch.get("verification"):
        evidence_error = trace_mod.validate_evidence_requirements(patch["verification"])
        if evidence_error:
            raise HTTPException(400, evidence_error)
    try:
        updated = experience.update(recipe_id, patch)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    if updated is None:
        raise HTTPException(404, "记忆条目不存在")
    return updated


@app.get("/api/memory/{recipe_id}/lineage")
def memory_lineage(recipe_id: str) -> dict[str, Any]:
    from .memory import experience

    store = experience._store()
    node = store.get_node(recipe_id)
    if not node:
        raise HTTPException(404, "memory not found")
    return {
        "ok": True,
        "node": node,
        "related": store.related(recipe_id, direction="both", limit=100),
        "attempts": store.attempts_for_node(recipe_id),
    }


@app.get("/api/memory/{recipe_id}/related")
def memory_related(recipe_id: str) -> dict[str, Any]:
    from .memory import experience

    store = experience._store()
    if not store.get_node(recipe_id):
        raise HTTPException(404, "memory not found")
    return {"ok": True, "id": recipe_id, "related": store.related(recipe_id, direction="both", limit=100)}


@app.post("/api/memory/{recipe_id}/retire")
def memory_retire(recipe_id: str) -> dict[str, Any]:
    from .memory import experience

    store = experience._store()
    if not store.retire_node(recipe_id):
        raise HTTPException(404, "memory not found")
    return {"ok": True, "id": recipe_id}


@app.post("/api/memory/{recipe_id}/verification-runs")
def memory_verification_start(recipe_id: str) -> dict[str, Any]:
    from .memory import experience
    if not _RECIPE_ID_RE.fullmatch(recipe_id):
        raise HTTPException(404, "memory not found")
    if any(
        str(job.metadata.get("memory_id") or "") == recipe_id
        for job in RUNTIME_JOBS.active(kind="memory_verification")
    ):
        raise HTTPException(409, "memory verification is already queued or running")
    store = experience._store()
    node = store.get_node(recipe_id)
    if not node:
        raise HTTPException(404, "memory not found")
    if node.get("kind") not in {"skill", "procedure"}:
        raise HTTPException(409, "memory is not a replayable action candidate")
    if node.get("status") in {"retired", "superseded"}:
        raise HTTPException(409, "retired memory cannot be verified")
    payload = dict(node.get("payload") or {})
    script_name = str(payload.get("script") or "").strip()
    if not script_name:
        raise HTTPException(409, "memory has no replayable script")
    verification = payload.get("verification") or []
    evidence_error = trace_mod.validate_evidence_requirements(verification)
    if evidence_error:
        raise HTTPException(409, evidence_error)
    script_path = _ensure_inside(SCRIPTS_DIR / script_name, SCRIPTS_DIR, "script")
    if not script_path.is_file():
        raise HTTPException(409, "memory script is missing")
    script_digest = experience.script_sha256(script_name)
    if not script_digest:
        raise HTTPException(409, "memory script cannot be hashed")
    try:
        document = json.loads(script_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise HTTPException(400, f"script parse failed: {exc}") from exc
    if document.get("format") == "automation-ir/1":
        error = validate_automation_ir(document)
        script = wps_from_automation_ir(document) if not error else {}
    else:
        script = document
        error = trace_mod.validate_script(script)
    if error:
        raise HTTPException(400, f"script validation failed: {error}")
    script["evidence"] = {"required": verification}
    verification_snapshot = json.loads(json.dumps(verification, ensure_ascii=False, sort_keys=True))
    replay_id = f"verify-{uuid.uuid4().hex[:12]}"

    def verification_worker(runtime_stop: threading.Event) -> dict[str, Any]:
        current = store.get_node(recipe_id)
        current_payload = dict((current or {}).get("payload") or {})
        if (
            not current
            or current.get("status") in {"retired", "superseded"}
            or str(current_payload.get("script") or "") != script_name
            or current_payload.get("verification") != verification_snapshot
            or experience.script_sha256(script_name) != script_digest
        ):
            store.record_attempt(
                recipe_id,
                "verification_replay_failed",
                run_id=replay_id,
                status="candidate_changed",
                success=False,
                detail={"script": script_name, "script_sha256": script_digest},
            )
            return {
                "ok": False,
                "error": "memory candidate changed while verification was queued",
                "memory_id": recipe_id,
                "replay_id": replay_id,
            }
        BUS.publish(
            "memory",
            action="verification_replay_start",
            id=recipe_id,
            replay_id=replay_id,
            script=script_name,
        )
        result = trace_mod.Replayer(runtime_stop).run_script(
            script,
            script_name=script_name,
        )
        promoted = experience.promote_skill_from_replay(
            recipe_id,
            replay_id,
            result,
            expected_script=script_name,
            expected_script_sha256=script_digest,
            expected_verification=verification_snapshot,
        )
        ok = promoted is not None
        output = {
            **result,
            "ok": ok,
            "memory": promoted,
            "memory_id": recipe_id,
            "replay_id": replay_id,
        }
        if not ok:
            output["error"] = str(
                result.get("error")
                or (result.get("postflight") or {}).get("error")
                or "structured replay evidence did not pass"
            )
        BUS.publish(
            "memory",
            action="verification_replay_done",
            id=recipe_id,
            replay_id=replay_id,
            ok=ok,
        )
        return output

    job = RUNTIME_JOBS.submit(
        "memory_verification",
        verification_worker,
        resources=(_INTERACTIVE_RUNTIME_RESOURCE,),
        metadata={
            "memory_id": recipe_id,
            "script": script_name,
            "script_sha256": script_digest,
        },
        job_id=replay_id,
    )
    return {"ok": True, "job": _public_memory_verification_job(job)}


@app.get("/api/memory/{recipe_id}/verification-runs/{replay_id}")
def memory_verification_status(recipe_id: str, replay_id: str) -> dict[str, Any]:
    job = RUNTIME_JOBS.get(replay_id)
    if (
        not job
        or job.kind != "memory_verification"
        or str(job.metadata.get("memory_id") or "") != recipe_id
    ):
        raise HTTPException(404, "memory verification replay not found")
    return _public_memory_verification_job(job)


@app.post("/api/memory/{recipe_id}/verify")
def memory_verify(recipe_id: str, req: MemoryVerifyRequest) -> dict[str, Any]:
    """Compatibility endpoint: inspect a real verification replay, never mutate."""
    job = RUNTIME_JOBS.get(req.replay_id)
    if (
        not job
        or job.kind != "memory_verification"
        or str(job.metadata.get("memory_id") or "") != recipe_id
    ):
        raise HTTPException(404, "memory verification replay not found")
    public = _public_memory_verification_job(job)
    if job.status in RUNTIME_JOBS.ACTIVE:
        raise HTTPException(409, "memory verification replay is still running")
    if not public.get("ok"):
        raise HTTPException(409, public.get("error") or "memory verification failed")
    return public


@app.delete("/api/memory/{recipe_id}")
def memory_delete(recipe_id: str) -> dict[str, Any]:
    from .memory import experience
    if not _RECIPE_ID_RE.fullmatch(recipe_id):
        raise HTTPException(404, "记忆条目不存在")
    if not experience.delete(recipe_id):
        raise HTTPException(404, "记忆条目不存在")
    return {"ok": True}


@app.delete("/api/memory")
def memory_clear() -> dict[str, Any]:
    from .memory import experience
    experience.clear()
    return {"ok": True}


# ---------------------------------------------------------------- websocket
@app.websocket("/ws/events")
async def ws_events(ws: WebSocket) -> None:
    if CONTROL_TOKEN:
        authorization = ws.headers.get("authorization", "")
        header_token = authorization[7:].strip() if authorization.lower().startswith("bearer ") else ""
        supplied = header_token or ws.query_params.get("token", "").strip()
        if not supplied or not secrets.compare_digest(supplied, CONTROL_TOKEN):
            await ws.close(code=4401, reason="Authentication required")
            return
    await ws.accept()
    q = BUS.subscribe()
    try:
        while True:
            try:
                event = await asyncio.to_thread(q.get, True, 1.0)
            except queue.Empty:
                # heartbeat doubles as disconnect detection
                await ws.send_text(json.dumps({"type": "ping"}))
                continue
            await ws.send_text(json.dumps(event, ensure_ascii=False, default=str))
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        BUS.unsubscribe(q)
