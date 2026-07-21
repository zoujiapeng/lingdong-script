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
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .automation_ir import (
    arp_graph_from_automation_ir,
    automation_ir_from_wps,
    automation_runtime_kind,
    validate_automation_ir,
    validate_automation_runtime,
    wps_from_automation_ir,
)
from . import scheduler as agent_scheduler
from .action.desktop_queue import DESKTOP_ACTION_QUEUE
from .agent.loop import AgentRunner
from .agent.router import build_runner
from .agent.session import SESSION
from .config import CONFIG, ROOT_DIR, SCRIPTS_DIR, TRACES_DIR
from .eval import summary as eval_summary
from .model_adapter import model_profile_from_config
from .perception import ocr, search, vlm
from .recorder import human as human_mod
from .recorder import apprentice as apprentice_mod
from .recorder import trace as trace_mod
from .runtime_info import runtime_fingerprint, runtime_source_drifted
from .runtime_jobs import RUNTIME_JOBS, RuntimeJob
from .fast.perception_daemon import PERCEPTION_DAEMON
from .utils.log import BUS, logger
from .orchestrator.adapters import default_handlers, finalize_memory_feedback
from .orchestrator.catalog import capability_catalog
from .orchestrator.contracts import (
    ArpGraph,
    ArpRunRequest,
    GraphValidation,
    validate_graph,
)
from .orchestrator.projection import RUN_PROJECTIONS
from .orchestrator.runtime import ARP_RUNS, ArpExecution
from .orchestrator.runtime_selection import DesktopRuntimeSelection
from .orchestrator.graph_compiler import legacy_goal_graph_task
from .tasking.capability import CapabilityPlanner
from .tasking.artifacts import ArtifactStore
from .tasking.compiler import TaskContractCompiler
from .tasking.contracts import ContractCompileRequest
from .tasking.execution_context import capture_foreground_window_context

WEBUI_DIR = ROOT_DIR / "webui"
_RECIPE_ID_RE = re.compile(r"^[0-9a-f]{6,32}$")
CHATGPT_MCP = None
CHATGPT_MCP_APP = None
CHATGPT_MCP_BRIDGE = None
CHATGPT_MCP_ERROR = ""
CHATGPT_MCP_LIFESPAN_USED = False
CHATGPT_MCP_ACTIVITY = {
    "request_count": 0,
    "last_request_at": 0.0,
    "last_user_agent": "",
}
CHATGPT_MCP_ACTIVITY_LOCK = threading.Lock()
_CHATGPT_MCP_ACTIVE_TTL_S = 45.0
CONTROL_TOKEN = os.environ.get("WINPILOT_CONTROL_TOKEN", "").strip()


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
    global CHATGPT_MCP_LIFESPAN_USED
    RUN_PROJECTIONS.hydrate_directory(
        TRACES_DIR,
        mark_incomplete_interrupted=True,
    )
    _warm_perception_dimensions()
    _start_scheduler_thread()
    try:
        if CHATGPT_MCP_LIFESPAN_USED:
            _install_chatgpt_mcp()
        mcp_app = CHATGPT_MCP_APP
        CHATGPT_MCP_LIFESPAN_USED = True
        if mcp_app is None:
            yield
        else:
            async with mcp_app.router.lifespan_context(mcp_app):
                yield
    finally:
        _stop_scheduler_thread()
        PERCEPTION_DAEMON.shutdown()


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
        self.runner: AgentRunner | None = None
        self.runners: dict[str, AgentRunner] = {}
        self.run_jobs: dict[str, str] = {}

    def register_runner(self, runner: AgentRunner, job_id: str = "") -> None:
        with self.lock:
            self.runners[runner.run_id] = runner
            if job_id:
                self.run_jobs[runner.run_id] = job_id

    def bind_runner(self, runner: AgentRunner, job_id: str = "") -> None:
        self.register_runner(runner, job_id)
        with self.lock:
            self.runner = runner

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
_SCHEDULER_STOP = threading.Event()
_SCHEDULER_THREAD: threading.Thread | None = None


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

def _run_agent_job(
    runner: AgentRunner,
    *,
    schedule_id: str = "",
    schedule_name: str = "",
    expected_session_generation: int | None = None,
) -> dict[str, Any]:
    STATE.bind_runner(runner)
    trace_mod.RECORDER.start(runner.run_id)
    if schedule_id:
        agent_scheduler.mark_started(schedule_id, runner.run_id)
        BUS.publish(
            "schedule",
            action="start",
            schedule_id=schedule_id,
            run_id=runner.run_id,
            name=schedule_name,
        )
    BUS.publish("chat", run_id=runner.run_id, role="user", content=runner.task)
    try:
        result = runner.run()
        appended = SESSION.append(
            runner.task,
            runner.status,
            runner.result_text,
            runner.run_id,
            expected_generation=expected_session_generation,
        )
        BUS.publish(
            "session",
            action="append" if appended else "stale_append_ignored",
            count=len(SESSION.all()),
            run_id=runner.run_id,
        )
        if schedule_id:
            agent_scheduler.mark_finished(schedule_id, runner.run_id, runner.status)
            BUS.publish(
                "schedule",
                action="finished",
                schedule_id=schedule_id,
                run_id=runner.run_id,
                status=runner.status,
                result=result,
            )
        return {
            "ok": runner.status in {"done", "reply"},
            "run_id": runner.run_id,
            "status": runner.status,
            "result": result,
            **({"error": runner.result_text} if runner.status not in {"done", "reply"} else {}),
        }
    except Exception as exc:
        logger.exception("agent runtime job crashed")
        runner.status = "error"
        runner.result_text = f"Agent runtime job crashed: {exc}"
        appended = SESSION.append(
            runner.task,
            runner.status,
            runner.result_text,
            runner.run_id,
            expected_generation=expected_session_generation,
        )
        if schedule_id:
            agent_scheduler.mark_finished(schedule_id, runner.run_id, runner.status)
        BUS.publish("agent_error", run_id=runner.run_id, error=str(exc))
        BUS.publish("agent_done", run_id=runner.run_id, status=runner.status, result=runner.result_text)
        BUS.publish(
            "session",
            action="append" if appended else "stale_append_ignored",
            count=len(SESSION.all()),
            run_id=runner.run_id,
        )
        return {
            "ok": False,
            "run_id": runner.run_id,
            "status": runner.status,
            "error": runner.result_text,
        }
    finally:
        trace_mod.RECORDER.stop()


def _try_start_scheduled(entry: dict[str, Any]) -> bool:
    task = str(entry.get("task") or "").strip()
    schedule_id = str(entry.get("id") or "")
    if not task or not schedule_id:
        return False
    reminder = agent_scheduler.reminder_message(task)
    if reminder:
        run_id = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
        job_id = f"schedule-{schedule_id}-{run_id}"
        agent_scheduler.mark_started(schedule_id, run_id)
        BUS.publish("schedule", action="queued", schedule_id=schedule_id, run_id=run_id)

        def deliver_notification(_stop: threading.Event) -> dict[str, Any]:
            BUS.publish(
                "schedule",
                action="start",
                schedule_id=schedule_id,
                run_id=run_id,
                name=str(entry.get("name") or ""),
            )
            BUS.publish(
                "user_notification",
                run_id=run_id,
                title=str(entry.get("name") or "提醒"),
                body=reminder,
                kind="info",
                schedule_id=schedule_id,
            )
            agent_scheduler.mark_finished(schedule_id, run_id, "done")
            result = {"ok": True, "run_id": run_id, "status": "done", "result": reminder}
            BUS.publish(
                "schedule",
                action="finished",
                schedule_id=schedule_id,
                run_id=run_id,
                status="done",
                result=result,
            )
            return result

        RUNTIME_JOBS.submit(
            "notification",
            deliver_notification,
            resources=(),
            metadata={"run_id": run_id, "task": task, "schedule_id": schedule_id},
            job_id=job_id,
            on_finish=lambda job: (
                agent_scheduler.mark_finished(schedule_id, run_id, "canceled")
                if job.status == "canceled"
                else None
            ),
        )
        return True
    max_steps = entry.get("max_steps")
    try:
        max_steps_override = int(max_steps) if max_steps else None
    except (TypeError, ValueError):
        max_steps_override = None
    stop_event = threading.Event()
    session_generation, session_context = SESSION.snapshot()
    runner = build_runner(
        task,
        stop_event=stop_event,
        session_context=session_context,
        max_steps_override=max_steps_override,
    )
    job_id = f"schedule-{schedule_id}-{runner.run_id}"
    STATE.register_runner(runner, job_id)
    agent_scheduler.mark_started(schedule_id, runner.run_id)
    BUS.publish("schedule", action="queued", schedule_id=schedule_id, run_id=runner.run_id)
    RUNTIME_JOBS.submit(
        "agent",
        lambda _stop: _run_agent_job(
            runner,
            schedule_id=schedule_id,
            schedule_name=str(entry.get("name") or ""),
            expected_session_generation=session_generation,
        ),
        resources=(_INTERACTIVE_RUNTIME_RESOURCE,),
        metadata={"run_id": runner.run_id, "task": task, "schedule_id": schedule_id},
        job_id=job_id,
        stop_event=stop_event,
        on_finish=lambda job: (
            agent_scheduler.mark_finished(schedule_id, runner.run_id, "canceled")
            if job.status == "canceled" and runner.status == "pending"
            else None
        ),
    )
    return True


def _scheduler_loop() -> None:
    agent_scheduler.reset_running_flags()
    while not _SCHEDULER_STOP.wait(1.0):
        for entry in agent_scheduler.due_tasks(limit=1):
            _try_start_scheduled(entry)


def _start_scheduler_thread() -> None:
    global _SCHEDULER_THREAD
    if _SCHEDULER_THREAD is not None and _SCHEDULER_THREAD.is_alive():
        return
    _SCHEDULER_STOP.clear()
    _SCHEDULER_THREAD = threading.Thread(target=_scheduler_loop, daemon=True, name="agent-scheduler")
    _SCHEDULER_THREAD.start()


def _stop_scheduler_thread() -> None:
    _SCHEDULER_STOP.set()


# -------------------------------------------------------------------- models
class ChatRequest(BaseModel):
    task: str
    max_steps: int | None = None


class AgentScheduleRequest(BaseModel):
    task: str
    name: str = ""
    run_at: str = ""
    delay_seconds: float | None = None
    interval_seconds: float | None = None
    enabled: bool = True
    max_steps: int | None = None


class AgentSchedulePatch(BaseModel):
    task: str | None = None
    name: str | None = None
    run_at: str | None = None
    delay_seconds: float | None = None
    interval_seconds: float | None = None
    enabled: bool | None = None
    max_steps: int | None = None


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


class ScriptSaveRequest(BaseModel):
    script: dict[str, Any]


class HumanStartRequest(BaseModel):
    name: str | None = None


class ApprenticeStartRequest(BaseModel):
    task: str
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
def _current_run_projection() -> dict[str, Any] | None:
    """Prefer the active parent ARP over an unbound child event projection."""
    execution = ARP_RUNS.current()
    if execution and execution.status.value in {"queued", "running", "paused"}:
        parent = RUN_PROJECTIONS.get(execution.run_id)
        if parent is not None:
            return parent
    return RUN_PROJECTIONS.current()


@app.post("/api/chat")
def start_chat(req: ChatRequest) -> dict[str, Any]:
    task = req.task.strip()
    if not task:
        raise HTTPException(400, "任务不能为空")
    visible_chars = [char for char in task if not char.isspace()]
    if len(visible_chars) >= 4 and visible_chars.count("?") / len(visible_chars) >= 0.5:
        raise HTTPException(
            400,
            "任务文本疑似在客户端编码时损坏（大量 ?）；请使用 UTF-8 JSON 重新提交",
        )
    with STATE.lock:
        if human_mod.RECORDER.is_recording():
            raise HTTPException(409, "已有任务在运行，请先停止")
        stop_event = threading.Event()
        session_generation, session_context = SESSION.snapshot()
        max_steps = req.max_steps if req.max_steps and req.max_steps > 0 else None
        runner = build_runner(
            task,
            stop_event=stop_event,
            session_context=session_context,
            max_steps_override=max_steps,
        )
        job_id = f"agent-{runner.run_id}"
        STATE.runners[runner.run_id] = runner
        STATE.run_jobs[runner.run_id] = job_id
        job = RUNTIME_JOBS.submit(
            "agent",
            lambda _stop: _run_agent_job(
                runner,
                expected_session_generation=session_generation,
            ),
            resources=(_INTERACTIVE_RUNTIME_RESOURCE,),
            metadata={"run_id": runner.run_id, "task": task},
            job_id=job_id,
            stop_event=stop_event,
        )
    return {"run_id": runner.run_id, "job_id": job.id, "status": job.status}


@app.post("/api/stop")
def stop_run() -> dict[str, Any]:
    canceled_jobs = []
    for job in RUNTIME_JOBS.active(resource=_INTERACTIVE_RUNTIME_RESOURCE):
        RUNTIME_JOBS.cancel(job.id)
        canceled_jobs.append(job.id)
    current_arp = ARP_RUNS.current()
    if current_arp and current_arp.status.value in {"queued", "running", "paused"}:
        current_arp.cancel()
    return {"ok": True, "canceled_jobs": canceled_jobs}


@app.post("/api/interject")
def interject(req: ChatRequest) -> dict[str, Any]:
    """Inject a hint into the running agent (steer it mid-task)."""
    text = req.task.strip()
    if not text:
        raise HTTPException(400, "内容不能为空")
    runner = STATE.runner
    job = STATE.job_for_run(runner.run_id) if runner else None
    agent_alive = bool(job and job.status == "running") or ARP_RUNS.busy()
    if not runner or not agent_alive:
        raise HTTPException(409, "当前没有运行中的任务")
    runner.interject(text)
    return {"ok": True}


@app.get("/api/status")
def status() -> dict[str, Any]:
    runner = STATE.runner
    busy = STATE.busy_breakdown()
    arp_running = ARP_RUNS.busy()
    model_profile = model_profile_from_config(CONFIG.get("api", default={}))
    return {
        "running": any(busy.values()) or arp_running,
        "busy": busy,
        "agent_running": busy["agent"],
        "replay_running": busy["replay"],
        "arp_running": arp_running,
        "human_recording": busy["human_recording"],
        "run_id": runner.run_id if runner else None,
        "run_status": runner.status if runner else None,
        "usage": dict(runner.usage_total) if runner else None,
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
        "desktop_action_queue": DESKTOP_ACTION_QUEUE.status(),
        # The platform status is polled by the workbench.  Never duplicate a
        # completed job's full ARP/result payload here; selected run/job detail
        # has dedicated endpoints.
        "runtime_jobs": RUNTIME_JOBS.list(20, include_result=False),
        "perception_daemon": PERCEPTION_DAEMON.status(),
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


@app.get("/api/v2/capabilities")
def platform_capabilities() -> dict[str, Any]:
    return {
        "ok": True,
        "capabilities": [item.model_dump(mode="json") for item in capability_catalog()],
    }


@app.get("/api/v2/runs")
def platform_runs(limit: int = 50) -> dict[str, Any]:
    return {"ok": True, "runs": RUN_PROJECTIONS.list(max(1, min(limit, 200)))}


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
    """Bind the external foreground window after the native client yields."""
    return {
        "ok": True,
        "target": capture_foreground_window_context(
            max(0, int(exclude_process_id or 0)),
        ),
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
    projection = RUN_PROJECTIONS.get(run_id)
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
    """Cancel either an Agent or ARP run through one frontend-safe route."""
    projection = RUN_PROJECTIONS.get(run_id)
    execution = ARP_RUNS.get(run_id)
    job = STATE.job_for_run(run_id)
    with STATE.lock:
        runner = STATE.runners.get(run_id)

    if not any((projection, execution, job, runner)):
        raise HTTPException(404, "run not found")

    execution_active = bool(
        execution
        and execution.status.value in {"queued", "running", "paused"}
    )
    job_active = bool(job and job.status in RUNTIME_JOBS.ACTIVE)
    runner_stop_event = getattr(runner, "stop_event", None)
    runner_active = bool(runner_stop_event and not runner_stop_event.is_set())
    if not any((execution_active, job_active, runner_active)):
        raise HTTPException(409, "run is not active")

    if execution_active:
        execution.cancel()
    if job:
        RUNTIME_JOBS.cancel(job.id)
    if runner_stop_event:
        runner_stop_event.set()

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


@app.post("/api/v2/task-contracts/compile")
def compile_task_contract(req: ContractCompileRequest) -> dict[str, Any]:
    try:
        session_generation, session_context = SESSION.snapshot()
        contract, graph = TaskContractCompiler().compile_graph(
            req.task,
            max_steps=req.max_steps,
            cold_start=req.cold_start,
            session_context=session_context,
            require_gui=req.require_gui,
            allow_unsatisfied=True,
        )
        graph.metadata["session_generation"] = session_generation
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {
        "ok": True,
        "needs_input": bool(contract.clarification_questions),
        "clarification_questions": list(contract.clarification_questions),
        "contract": contract.model_dump(mode="json"),
        "graph": graph.model_dump(mode="json"),
    }


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


def _start_arp_execution(graph: ArpGraph, inputs: dict[str, Any]) -> ArpExecution:
    desktop_runtime_selection = DesktopRuntimeSelection.from_config(
        CONFIG.get("vnext", "runtime_version", default=None)
    )
    current_session_generation = SESSION.generation()
    raw_session_generation = graph.metadata.get("session_generation")
    expected_session_generation = (
        current_session_generation
        if raw_session_generation is None
        else int(raw_session_generation)
    )
    if expected_session_generation != current_session_generation:
        raise RuntimeError(
            "Session context changed after this task contract was compiled. "
            "Compile it again before starting the run."
        )
    contract_data = graph.metadata.get("task_contract")
    task_contract = None
    if isinstance(contract_data, dict):
        from .tasking.contracts import TaskContract

        task_contract = TaskContract.model_validate(contract_data)
        CapabilityPlanner.assert_satisfiable(task_contract)
        CapabilityPlanner.assert_runtime_satisfiable(task_contract)
    validation = validate_graph(graph)
    if not validation.valid:
        raise ValueError("; ".join(validation.errors))
    if human_mod.RECORDER.is_recording():
        raise RuntimeError("a human or apprentice recording is active")

    shadow_config = CONFIG.get("vnext", "profiler_shadow", default={}) or {}
    profiler_shadow_enabled = bool(
        isinstance(shadow_config, dict) and shadow_config.get("enabled") is True
    )
    profiler_shadow_hook = None
    profiler_shadow_setup_error: str | None = None
    if profiler_shadow_enabled and task_contract is not None:
        try:
            from .vnext.profiling import (
                LegacyTaskProfilerShadowHook,
                OpenAITaskProfilerModel,
            )

            profiler_shadow_hook = LegacyTaskProfilerShadowHook(
                OpenAITaskProfilerModel.from_api_config(
                    CONFIG.get("api", default={}) or {},
                ),
            )
        except Exception as exc:
            # Shadow setup is observational and must never block the legacy
            # product path. Record only the error type; provider configuration
            # details may contain secrets.
            profiler_shadow_setup_error = type(exc).__name__

    broker_shadow_config = CONFIG.get("vnext", "broker_shadow", default={}) or {}
    broker_shadow_enabled = bool(
        isinstance(broker_shadow_config, dict)
        and broker_shadow_config.get("enabled") is True
    )
    broker_shadow_state: dict[str, Any] = {
        "observer": None,
        "pending_runners": [],
        "attached_runners": [],
    }

    job_ref: dict[str, str] = {}

    def on_runner(runner: AgentRunner) -> None:
        STATE.bind_runner(runner, job_ref.get("job_id", ""))
        if not broker_shadow_enabled:
            return
        observer = broker_shadow_state.get("observer")
        if observer is None:
            broker_shadow_state["pending_runners"].append(runner)
            return
        from .vnext.capabilities import attach_legacy_broker_shadow

        attach_legacy_broker_shadow(runner, observer)
        broker_shadow_state["attached_runners"].append(runner)

    def on_start(started: ArpExecution) -> None:
        trace_mod.RECORDER.start(started.run_id)
        profiler_record = None
        if profiler_shadow_enabled:
            if profiler_shadow_hook is None or task_contract is None:
                trace_mod.RECORDER.record_sidecar(
                    {
                        "type": "vnext_profiler_shadow",
                        "runtime_version": "legacy",
                        "status": "safe_default",
                        "profile_call_count": 0,
                        "request_retry_count": 0,
                        "elapsed_ms": None,
                        "proposal": None,
                        "profile": None,
                        "error_kind": profiler_shadow_setup_error or "Unavailable",
                    },
                    run_id=started.run_id,
                )
            else:
                try:
                    profiler_record = profiler_shadow_hook.profile_run(
                        run_id=started.run_id,
                        contract=task_contract,
                        trace=trace_mod.RECORDER,
                        stop_event=started.stop_event,
                    )
                    started.runtime_state["vnext_profiler_shadow"] = (
                        profiler_record.to_dict()
                    )
                except Exception as exc:
                    trace_mod.RECORDER.record_sidecar(
                        {
                            "type": "vnext_profiler_shadow",
                            "runtime_version": "legacy",
                            "status": "safe_default",
                            "profile_call_count": None,
                            "request_retry_count": None,
                            "elapsed_ms": None,
                            "proposal": None,
                            "profile": None,
                            "error_kind": type(exc).__name__,
                        },
                        run_id=started.run_id,
                    )

        if not broker_shadow_enabled:
            return
        if task_contract is None:
            trace_mod.RECORDER.record_sidecar(
                {
                    "type": "vnext_broker_shadow",
                    "runtime_version": "legacy",
                    "status": "unavailable",
                    "reason": "task_contract_unavailable",
                },
                run_id=started.run_id,
            )
            return
        try:
            from .vnext.capabilities import (
                attach_legacy_broker_shadow,
                build_legacy_broker_shadow_observer,
                legacy_local_capability_registry,
            )
            from .vnext.mission import project_legacy_contract
            from .vnext.profiling import ExecutionPolicy

            mission_id = f"mission-{started.run_id}"
            definition = project_legacy_contract(
                task_contract,
                mission_id=mission_id,
                run_id=started.run_id,
            )
            profile = getattr(profiler_record, "profile", None)
            broker_shadow_local_capabilities = (
                legacy_local_capability_registry().snapshot()
            )
            if profile is None:
                profile = ExecutionPolicy().safe_default(
                    mission_id,
                    definition.goal_revision,
                    reason="broker_shadow_without_profiler",
                    available_capabilities=broker_shadow_local_capabilities,
                )
            observer = build_legacy_broker_shadow_observer(
                run_id=started.run_id,
                contract=task_contract,
                profile=profile,
                trace=trace_mod.RECORDER,
                available_capabilities=broker_shadow_local_capabilities,
            )
            broker_shadow_state["observer"] = observer
            for runner in broker_shadow_state["pending_runners"]:
                attach_legacy_broker_shadow(runner, observer)
                broker_shadow_state["attached_runners"].append(runner)
            broker_shadow_state["pending_runners"].clear()
            started.runtime_state["vnext_broker_shadow"] = {
                "status": "ready",
                "enforced": False,
                "profile_id": profile.profile_id,
            }
        except Exception as exc:
            trace_mod.RECORDER.record_sidecar(
                {
                    "type": "vnext_broker_shadow",
                    "runtime_version": "legacy",
                    "status": "unavailable",
                    "reason": type(exc).__name__,
                },
                run_id=started.run_id,
            )

    def on_finish(finished: ArpExecution) -> None:
        try:
            finalize_memory_feedback(finished)
        finally:
            try:
                snapshot = finished.to_dict()
                contract_data = finished.graph.metadata.get("task_contract")
                contract_data = contract_data if isinstance(contract_data, dict) else {}
                artifact_snapshot = snapshot.get("artifacts")
                artifacts = (
                    artifact_snapshot.get("artifacts")
                    if isinstance(artifact_snapshot, dict)
                    and isinstance(artifact_snapshot.get("artifacts"), list)
                    else []
                )
                raw_result = snapshot.get("result")
                result_text = (
                    raw_result
                    if isinstance(raw_result, str)
                    else json.dumps(raw_result, ensure_ascii=False)
                )
                if not str(result_text or "").strip():
                    result_text = str(snapshot.get("error") or "")
                appended = SESSION.append(
                    str(contract_data.get("task") or finished.graph.name or ""),
                    str(snapshot.get("status") or ""),
                    str(result_text or ""),
                    finished.run_id,
                    intent=(
                        contract_data.get("intent")
                        if isinstance(contract_data.get("intent"), dict)
                        else {}
                    ),
                    artifacts=artifacts,
                    expected_generation=expected_session_generation,
                )
                BUS.publish(
                    "session",
                    action="append" if appended else "stale_append_ignored",
                    count=len(SESSION.all()),
                    run_id=finished.run_id,
                )
            finally:
                if profiler_shadow_hook is not None:
                    profiler_shadow_hook.close(finished.run_id)
                observer = broker_shadow_state.get("observer")
                if observer is not None:
                    from .vnext.capabilities import detach_legacy_broker_shadow

                    for runner in broker_shadow_state.get("attached_runners", ()):
                        detach_legacy_broker_shadow(runner, observer)
                broker_shadow_state.clear()
                trace_mod.RECORDER.stop()

    execution = ARP_RUNS.prepare(
        graph,
        default_handlers(on_runner),
        inputs=inputs,
        desktop_runtime_selection=desktop_runtime_selection,
    )
    job_id = f"arp-job-{execution.run_id}"
    job_ref["job_id"] = job_id
    with STATE.lock:
        STATE.run_jobs[execution.run_id] = job_id

    def worker(_stop: threading.Event) -> dict[str, Any]:
        snapshot = ARP_RUNS.run_prepared(
            execution,
            on_start=on_start,
            on_finish=on_finish,
        )
        ok = snapshot.get("status") == "done"
        return {
            "ok": ok,
            "run_id": execution.run_id,
            "run": snapshot,
            **({"error": snapshot.get("error") or "ARP run failed"} if not ok else {}),
        }

    RUNTIME_JOBS.submit(
        "arp",
        worker,
        resources=(_INTERACTIVE_RUNTIME_RESOURCE,),
        metadata={"run_id": execution.run_id, "graph_id": graph.id, "graph_name": graph.name},
        job_id=job_id,
        stop_event=execution.stop_event,
    )
    return execution


@app.post("/api/v2/arp/runs")
def start_arp_run(req: ArpRunRequest) -> dict[str, Any]:
    if runtime_source_drifted():
        raise HTTPException(
            409,
            "WinPilot Python sources changed after this service started. "
            "Restart the runtime before starting another frontend task.",
        )
    try:
        graph = req.graph
        legacy_task = legacy_goal_graph_task(graph)
        if legacy_task is not None:
            task, max_steps = legacy_task
            session_generation, session_context = SESSION.snapshot()
            _contract, graph = TaskContractCompiler().compile_graph(
                task,
                max_steps=max_steps,
                session_context=session_context,
            )
            graph.metadata["session_generation"] = session_generation
        execution = _start_arp_execution(graph, req.inputs)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {
        "ok": True,
        "run_id": execution.run_id,
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


@app.post("/api/v2/arp/runs/{run_id}/gate")
def decide_arp_gate(run_id: str, req: ArpGateDecisionRequest) -> dict[str, Any]:
    if not ARP_RUNS.decide_gate(run_id, req.node_id, req.approved, req.comment):
        raise HTTPException(409, "run is not waiting at this human gate")
    return {"ok": True}


def _chatgpt_bridge_status() -> dict[str, Any]:
    runtime_status = status()
    enabled = bool(CONFIG.get("integrations", "chatgpt_desktop", "enabled", default=True))
    server_ready = CHATGPT_MCP_APP is not None
    with CHATGPT_MCP_ACTIVITY_LOCK:
        activity = dict(CHATGPT_MCP_ACTIVITY)
    last_request_at = float(activity.get("last_request_at") or 0.0)
    client_active = bool(
        enabled
        and server_ready
        and last_request_at
        and time.time() - last_request_at <= _CHATGPT_MCP_ACTIVE_TTL_S
    )
    current = RUN_PROJECTIONS.current()
    current_summary = None
    if current:
        current_summary = {
            key: current.get(key)
            for key in (
                "run_id", "kind", "status", "phase", "goal", "graph_id",
                "current_node_id", "current_tool", "error", "metrics",
            )
        }
        current_summary["evidence_count"] = len(current.get("evidence") or [])
    return {
        "ok": server_ready,
        "integration": "chatgpt_desktop",
        "transport": "mcp_streamable_http",
        "planner": (
            "connected ChatGPT/Codex desktop MCP client"
            if client_active
            else "ChatGPT/Codex desktop via MCP when connected"
        ),
        "requires_openai_api_key": False,
        "enabled": enabled,
        "server_ready": server_ready,
        "client_active": client_active,
        "connection_status": (
            "disabled" if not enabled
            else "active" if client_active
            else "ready" if server_ready
            else "error"
        ),
        "last_client_seen_at": last_request_at or None,
        "request_count": int(activity.get("request_count") or 0),
        "last_client": str(activity.get("last_user_agent") or ""),
        "security_mode": "loopback_only",
        "proxy_bypass": "NO_PROXY=127.0.0.1,localhost",
        "allow_desktop_mutation": bool(
            CONFIG.get("integrations", "chatgpt_desktop", "allow_desktop_mutation", default=True)
        ),
        "runtime": {
            key: runtime_status.get(key)
            for key in (
                "running", "busy", "agent_running", "replay_running", "arp_running",
                "human_recording", "run_id", "run_status", "ocr_provider",
                "vlm_configured", "vlm_reason", "enabled_dimensions", "is_admin",
                "shell_enabled", "desktop_action_queue",
            )
        },
        "world_model_enabled": bool(CONFIG.get("agent", "world_model", default=True)),
        "current_run": current_summary,
        "capabilities": [item.model_dump(mode="json") for item in capability_catalog()],
        **({"error": CHATGPT_MCP_ERROR} if CHATGPT_MCP_ERROR else {}),
    }


def _chatgpt_bridge_start_graph(graph: ArpGraph, inputs: dict[str, Any]) -> dict[str, Any]:
    try:
        execution = _start_arp_execution(graph, inputs)
    except (RuntimeError, ValueError) as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "run_id": execution.run_id, "run": execution.to_dict()}


def _chatgpt_bridge_get_run(run_id: str) -> dict[str, Any]:
    projection = RUN_PROJECTIONS.get(run_id)
    execution = ARP_RUNS.get(run_id)
    if not projection and not execution:
        return {"ok": False, "error": "run not found", "run_id": run_id}
    return {
        "ok": True,
        "run": projection,
        "arp": execution.to_dict() if execution else None,
    }


def _chatgpt_bridge_cancel_run(run_id: str) -> dict[str, Any]:
    if not ARP_RUNS.cancel(run_id):
        return {"ok": False, "error": "active ARP run not found", "run_id": run_id}
    job = STATE.job_for_run(run_id)
    if job:
        RUNTIME_JOBS.cancel(job.id)
    execution = ARP_RUNS.get(run_id)
    return {"ok": True, "run": execution.to_dict() if execution else None}


def _chatgpt_bridge_decide_gate(
    run_id: str,
    node_id: str,
    approved: bool,
    comment: str,
) -> dict[str, Any]:
    if not ARP_RUNS.decide_gate(run_id, node_id, approved, comment):
        return {"ok": False, "error": "run is not waiting at this human gate"}
    return {"ok": True}


@app.get("/api/v2/integrations/chatgpt")
async def chatgpt_integration(request: Request) -> dict[str, Any]:
    endpoint = str(request.base_url).rstrip("/") + "/mcp"
    tool_names = []
    if CHATGPT_MCP is not None:
        tool_names = [tool.name for tool in await CHATGPT_MCP.list_tools()]
    return {
        **_chatgpt_bridge_status(),
        "endpoint": endpoint,
        "tool_count": len(tool_names),
        "tools": tool_names,
        "registration": {
            "client": "Codex desktop",
            "command": f"codex mcp add winpilot --url {endpoint}",
            "environment": "NO_PROXY=127.0.0.1,localhost",
        },
    }


@app.get("/api/agent-schedules")
def agent_schedules(enabled_only: bool = False) -> dict[str, Any]:
    return {
        "ok": True,
        "schedules": agent_scheduler.list_tasks(enabled_only=enabled_only),
    }


@app.post("/api/agent-schedules")
def create_agent_schedule(req: AgentScheduleRequest) -> dict[str, Any]:
    try:
        entry = agent_scheduler.create_task(
            req.task,
            name=req.name,
            run_at=req.run_at,
            delay_seconds=req.delay_seconds,
            interval_seconds=req.interval_seconds,
            enabled=req.enabled,
            max_steps=req.max_steps,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, "schedule": entry}


@app.patch("/api/agent-schedules/{schedule_id}")
def update_agent_schedule(schedule_id: str, req: AgentSchedulePatch) -> dict[str, Any]:
    try:
        entry = agent_scheduler.update_task(
            schedule_id,
            req.model_dump(exclude_unset=True),
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if entry is None:
        raise HTTPException(404, "schedule not found")
    return {"ok": True, "schedule": entry}


@app.delete("/api/agent-schedules/{schedule_id}")
def cancel_agent_schedule(schedule_id: str) -> dict[str, Any]:
    ok = agent_scheduler.cancel_task(schedule_id)
    if not ok:
        raise HTTPException(404, "schedule not found")
    return {"ok": True, "id": schedule_id}


@app.post("/api/elevate")
def elevate() -> dict[str, Any]:
    """Relaunch WinPilot elevated (one UAC prompt), then exit this instance."""
    if _is_admin():
        return {"ok": True, "already_admin": True}
    run_py = str(ROOT_DIR / "run.py")
    try:
        # ShellExecuteW "runas" triggers the UAC consent dialog.
        rc = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", sys.executable, f'"{run_py}"', str(ROOT_DIR), 1)
        if rc <= 32:
            return {"ok": False, "error": f"提权失败（用户可能取消了 UAC），代码 {rc}"}
    except Exception as exc:
        raise HTTPException(500, f"提权失败: {exc}") from exc
    BUS.publish("config", changed=["elevate"], note="正在以管理员身份重启…")
    # Give the new elevated process time to bind the port, then exit ours.
    threading.Timer(1.5, lambda: __import__("os")._exit(0)).start()
    return {"ok": True, "restarting": True}


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
    CONFIG.update(patch)
    BUS.publish("config", changed=list(patch.keys()))
    return get_config()


@app.get("/api/traces")
def traces() -> list[dict[str, Any]]:
    return trace_mod.list_traces()


@app.get("/api/evals")
def evals(limit: int = 20) -> dict[str, Any]:
    limit = max(1, min(int(limit or 20), 100))
    reports = eval_summary.list_eval_reports(limit=limit)
    return eval_summary.aggregate_reports(reports)


@app.get("/api/trace/{run_id}/summary")
def trace_summary(run_id: str) -> dict[str, Any]:
    summary = eval_summary.summarize_trace(run_id)
    if not summary.get("run_id") and not summary.get("tool_count"):
        raise HTTPException(404, "trace summary not found")
    return summary


@app.get("/api/trace/{run_id}")
def trace_detail(run_id: str) -> list[dict[str, Any]]:
    events = trace_mod.read_trace(run_id)
    if not events:
        raise HTTPException(404, "trace 不存在")
    return events


@app.get("/api/trace/{run_id}/shot/{name}")
def trace_screenshot(run_id: str, name: str):
    path = _ensure_inside(TRACES_DIR / run_id / name, TRACES_DIR, "截图")
    if not path.exists():
        raise HTTPException(404, "截图不存在")
    return FileResponse(path)


@app.post("/api/export/{run_id}")
def export(run_id: str, req: ExportRequest | None = None) -> dict[str, Any]:
    try:
        path = trace_mod.export_script(run_id, name=(req.name if req else None))
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, "script": path.name}


@app.post("/api/export/{run_id}/ir")
def export_ir(run_id: str, req: ExportRequest | None = None) -> dict[str, Any]:
    try:
        path = trace_mod.export_automation_ir(run_id, name=(req.name if req else None))
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, "ir": path.name}


@app.get("/api/scripts")
def scripts() -> list[dict[str, Any]]:
    return trace_mod.list_scripts()


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


@app.post("/api/human/start")
def human_start(req: HumanStartRequest | None = None) -> dict[str, Any]:
    """Begin a human-demonstration recording (captures clicks/keys with
    perception selectors into a .wps.json)."""
    with STATE.lock:
        if STATE.busy():
            raise HTTPException(409, "已有任务/重放/录制在运行")
        try:
            human_mod.RECORDER.start((req.name if req else None) or "")
        except Exception as exc:  # pynput unavailable etc.
            raise HTTPException(500, f"无法启动录制: {exc}") from exc
    return {"ok": True}


@app.post("/api/human/stop")
def human_stop() -> dict[str, Any]:
    result = human_mod.RECORDER.stop()
    if not result.get("ok"):
        raise HTTPException(400, result.get("error", "停止失败"))
    return result


@app.get("/api/human/status")
def human_status() -> dict[str, Any]:
    return {"recording": human_mod.RECORDER.is_recording()}


@app.get("/api/v2/replay/modes")
def replay_modes() -> dict[str, Any]:
    return {"ok": True, "modes": list(apprentice_mod.REPLAY_MODES)}


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

    if runtime_source_drifted():
        raise HTTPException(409, "runtime sources changed; restart before verification")
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


@app.get("/api/session")
def session_list() -> list[dict[str, Any]]:
    return SESSION.all()


@app.delete("/api/session")
def session_clear() -> dict[str, Any]:
    generation = SESSION.clear()
    BUS.publish("session", action="clear", count=0, generation=generation)
    return {"ok": True, "generation": generation}


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


# ------------------------------------------------------------------- static
@app.get("/")
def index() -> FileResponse:
    return FileResponse(WEBUI_DIR / "index.html")


app.mount("/static", StaticFiles(directory=str(WEBUI_DIR)), name="static")


def _install_chatgpt_mcp() -> None:
    global CHATGPT_MCP, CHATGPT_MCP_APP, CHATGPT_MCP_BRIDGE, CHATGPT_MCP_ERROR
    CHATGPT_MCP = None
    CHATGPT_MCP_APP = None
    CHATGPT_MCP_BRIDGE = None
    CHATGPT_MCP_ERROR = ""
    try:
        from .integrations.chatgpt_mcp import ChatGPTBridgeHooks, build_chatgpt_mcp

        hooks = ChatGPTBridgeHooks(
            status=_chatgpt_bridge_status,
            start_graph=_chatgpt_bridge_start_graph,
            get_run=_chatgpt_bridge_get_run,
            cancel_run=_chatgpt_bridge_cancel_run,
            decide_gate=_chatgpt_bridge_decide_gate,
            busy=STATE.busy,
            enabled=lambda: bool(
                CONFIG.get("integrations", "chatgpt_desktop", "enabled", default=True)
            ),
            mutation_allowed=lambda: bool(
                CONFIG.get(
                    "integrations",
                    "chatgpt_desktop",
                    "allow_desktop_mutation",
                    default=True,
                )
            ),
        )
        CHATGPT_MCP, CHATGPT_MCP_APP, CHATGPT_MCP_BRIDGE = build_chatgpt_mcp(hooks)
    except Exception as exc:
        CHATGPT_MCP_ERROR = str(exc)
        logger.exception("ChatGPT desktop MCP bridge failed to initialize")


_install_chatgpt_mcp()
if CHATGPT_MCP_APP is not None:
    # Existing API/static routes are registered first; this root mount only
    # receives the MCP protocol path and keeps the canonical URL at /mcp.
    class _ChatGPTMcpDelegate:
        async def __call__(self, scope, receive, send) -> None:
            current = CHATGPT_MCP_APP
            if current is None:
                await JSONResponse(
                    {"error": CHATGPT_MCP_ERROR or "ChatGPT MCP bridge unavailable"},
                    status_code=503,
                )(scope, receive, send)
                return
            if scope.get("type") == "http" and str(scope.get("path") or "").startswith("/mcp"):
                headers = {
                    bytes(key).decode("latin-1").casefold(): bytes(value).decode("latin-1")
                    for key, value in scope.get("headers") or []
                }
                with CHATGPT_MCP_ACTIVITY_LOCK:
                    CHATGPT_MCP_ACTIVITY["request_count"] += 1
                    CHATGPT_MCP_ACTIVITY["last_request_at"] = time.time()
                    CHATGPT_MCP_ACTIVITY["last_user_agent"] = headers.get("user-agent", "unknown MCP client")[:240]
            await current(scope, receive, send)

    app.mount("/", _ChatGPTMcpDelegate(), name="chatgpt-desktop-mcp")
