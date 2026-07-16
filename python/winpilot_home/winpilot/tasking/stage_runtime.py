"""Deterministic state machine for one typed task stage."""
from __future__ import annotations

import ctypes
import json
import time
from typing import Any

from ..agent.router import build_runner
from ..agent.runtime_profile import RuntimeProfile, build_runtime_profile
from ..agent.tools import ToolSession
from ..orchestrator.contracts import ArpNode
from ..orchestrator.runtime import NodeResult
from ..perception import win32
from ..utils.log import BUS
from .adapters import CommunicationAdapter, DomainAdapterRegistry
from .artifacts import ArtifactStore
from .capability import CapabilityPlanner
from .completion import CompletionContractVerifier
from .contracts import StageType, TaskContract, TaskIntent, TaskStage
from .evidence import EvidenceRegistry
from .interaction_stage_runtime import (
    INTERACTION_STAGE_TYPES,
    InteractionStageEngine,
)
from .recovery import RecoveryPolicy


def _json(text: str) -> dict[str, Any]:
    try:
        value = json.loads(str(text or "{}"))
        return value if isinstance(value, dict) else {}
    except (TypeError, json.JSONDecodeError):
        return {}


def _raise_stage_failure(result: dict[str, Any], default_type: str, default_message: str) -> None:
    error = RuntimeError(str(result.get("error") or default_message))
    error.failure_type = str(result.get("failure_type") or default_type)
    error.stage_details = {"adapter_result": dict(result)}
    raise error


class StageRuntime:
    def __init__(self) -> None:
        self.capabilities = CapabilityPlanner()
        self.recovery = RecoveryPolicy()
        self.interactions = InteractionStageEngine(stage_output=self._stage_output)

    @staticmethod
    def _recovery_attempt(
        context: dict[str, Any],
        stage: TaskStage,
        failure_type: str,
    ) -> int:
        """Persist failure attempts across graph revisits of the same stage."""
        attempts = context["runtime_state"].setdefault(
            "stage_recovery_attempts", {},
        )
        key = f"{stage.id}:{str(failure_type or 'unknown').casefold()}"
        attempt = int(attempts.get(key) or 0) + 1
        attempts[key] = attempt
        return attempt

    @staticmethod
    def _clear_recovery_attempts(
        context: dict[str, Any],
        stage: TaskStage,
    ) -> None:
        attempts = context["runtime_state"].get("stage_recovery_attempts")
        if not isinstance(attempts, dict):
            return
        prefix = f"{stage.id}:"
        for key in [item for item in attempts if str(item).startswith(prefix)]:
            attempts.pop(key, None)

    @staticmethod
    def _contract(context: dict[str, Any]) -> TaskContract:
        graph = context.get("graph") if isinstance(context.get("graph"), dict) else {}
        metadata = graph.get("metadata") if isinstance(graph.get("metadata"), dict) else {}
        return TaskContract.model_validate(metadata.get("task_contract") or {})

    @staticmethod
    def _store(context: dict[str, Any]) -> ArtifactStore:
        state = context["runtime_state"]
        store = state.get("artifact_store")
        if not isinstance(store, ArtifactStore):
            store = ArtifactStore(context["run_id"])
            state["artifact_store"] = store
        return store

    @staticmethod
    def _evidence(context: dict[str, Any]) -> EvidenceRegistry:
        state = context["runtime_state"]
        registry = state.get("evidence_registry")
        if not isinstance(registry, EvidenceRegistry):
            registry = EvidenceRegistry(context["run_id"])
            state["evidence_registry"] = registry
        return registry

    @staticmethod
    def _stage_scoped_intent(
        contract: TaskContract,
        stage: TaskStage,
        goal: str,
    ) -> TaskIntent:
        """Project the compiler-owned task intent onto one executable stage.

        Semantic child runners must not parse their generated stage prompt as a
        second source of task truth.  They also must not inherit unrelated
        communication or live-interaction state from a larger cross-application
        contract.  This projection keeps semantic fields already resolved by
        the compiler while deriving the active operation from the typed stage.
        """
        operation_by_stage = {
            StageType.OPEN_APPLICATION: "open",
            StageType.FOCUS_WINDOW: "focus",
            StageType.CREATE_OR_EDIT_ARTIFACT: "edit",
            StageType.DRAW_ARTIFACT: "draw",
            StageType.CAPTURE_ARTIFACT: "capture",
            StageType.SAVE_ARTIFACT: "save",
            StageType.OPEN_CONVERSATION: "send",
            StageType.RESOLVE_RECIPIENT: "send",
            StageType.SEND_TEXT: "send",
            StageType.SEND_ARTIFACT: "send",
            StageType.VERIFY_ARTIFACT: "verify",
            StageType.VERIFY_DELIVERY: "send",
            StageType.OBSERVE_SCENE: "interact",
            StageType.INFER_INTERACTION: "interact",
            StageType.PERFORM_TURN: "interact",
            StageType.VERIFY_LOCAL_EFFECT: "interact",
            StageType.WAIT_FOR_EXTERNAL_CHANGE: "interact",
        }
        communication_stages = {
            StageType.OPEN_CONVERSATION,
            StageType.RESOLVE_RECIPIENT,
            StageType.SEND_TEXT,
            StageType.SEND_ARTIFACT,
            StageType.VERIFY_DELIVERY,
        }
        interaction_stages = {
            StageType.OBSERVE_SCENE,
            StageType.INFER_INTERACTION,
            StageType.PERFORM_TURN,
            StageType.VERIFY_LOCAL_EFFECT,
            StageType.WAIT_FOR_EXTERNAL_CHANGE,
        }

        payload = contract.intent.model_dump(mode="json")
        parameters = stage.parameters or {}
        provenance = dict(payload.get("provenance") or {})
        provenance.update({
            "stage_scope": f"{contract.task_id}:{stage.id}",
            "stage_operation": "task_contract_stage",
        })
        payload.update({
            "original_text": str(goal or ""),
            "operation": [operation_by_stage.get(stage.type, "execute")],
            "source": str(
                parameters.get("source")
                or parameters.get("app")
                or payload.get("source")
                or ""
            ),
            "subject": str(parameters.get("subject") or payload.get("subject") or ""),
            "quality": str(parameters.get("quality") or payload.get("quality") or "normal"),
            "ambiguous_fields": [],
            "provenance": provenance,
        })
        if stage.type not in communication_stages:
            payload.update({
                "channel": "",
                "recipient": "",
                "destination": "",
                "send_mode": "send",
            })
        if stage.type not in interaction_stages:
            payload["interaction"] = {"active": False}
        return TaskIntent.model_validate(payload)

    @staticmethod
    def _stage_communication_context(
        stage: TaskStage,
        intent: TaskIntent,
    ) -> dict[str, Any]:
        communication_stages = {
            StageType.OPEN_CONVERSATION,
            StageType.RESOLVE_RECIPIENT,
            StageType.SEND_TEXT,
            StageType.SEND_ARTIFACT,
            StageType.VERIFY_DELIVERY,
        }
        active = stage.type in communication_stages
        return {
            "active": active,
            "recipient": intent.recipient if active else "",
            "channel": (intent.channel or intent.destination) if active else "",
            "send_mode": intent.send_mode if active else "send",
        }

    @staticmethod
    def _resolve_hwnd(source: str) -> int:
        source = str(source or "").strip()
        if source and source not in {"active_window", "foreground", "current"}:
            match = win32.find_window(source)
            if match:
                return int(match.get("hwnd") or 0)
            # An explicit artifact source must never silently degrade to an
            # unrelated foreground application.
            return 0
        try:
            return int(ctypes.windll.user32.GetForegroundWindow() or 0)
        except Exception:
            return 0

    @staticmethod
    def _artifact_ref(context: dict[str, Any], ref: str) -> str:
        refs = context["runtime_state"].setdefault("artifact_refs", {})
        return str(refs.get(ref) or ref or "")

    @staticmethod
    def _has_successful_draw_mutation(
        history: list[tuple[str, dict[str, Any], str]] | tuple[tuple[str, dict[str, Any], str], ...],
    ) -> bool:
        """Require controller evidence that pixels were actually authored.

        A semantic judge is not allowed to turn an untouched/previous canvas
        into success.  This deliberately accepts controller transactions and
        direct draw tools as equivalent implementation paths.
        """

        def executed(value: Any) -> bool:
            if isinstance(value, list):
                return any(executed(item) for item in value)
            if not isinstance(value, dict):
                return False
            try:
                if int(value.get("executed_draw_count") or 0) > 0:
                    return True
            except (TypeError, ValueError):
                pass
            action = str(
                value.get("action") or value.get("tool") or value.get("name") or ""
            ).strip().casefold()
            if (
                action == "draw_strokes"
                and value.get("blocked") is not True
                and any(value.get(key) is True for key in ("ok", "verified", "applied", "action_applied"))
            ):
                return True
            return any(executed(item) for item in value.values())

        for name, _args, result_text in history:
            payload = _json(result_text)
            if str(name or "").strip().casefold() == "draw_strokes":
                if payload.get("ok") is True and payload.get("blocked") is not True:
                    return True
            if executed(payload):
                return True
        return False

    @staticmethod
    def _draw_boundary_criteria(subject: str, quality: str) -> str:
        base = (
            f"Judge only the current drawing canvas. It must visibly contain a clearly "
            f"recognizable {subject}, not merely unrelated geometry, guide marks, or an "
            "unfinished fragment. The drawing stage does not need to show later capture or "
            "communication steps."
        )
        if quality == "detailed":
            return base + " The user requested polished or detailed work, so require visibly coherent high detail."
        return base + " Normal quality only requires a coherent recognizable subject; do not require decorative detail."

    def _verify_draw_boundary(
        self,
        *,
        goal: str,
        subject: str,
        quality: str,
        app: str,
        hwnd: int,
        run_id: str,
        stop_event: Any,
        stage_id: str,
        region: dict[str, int] | None = None,
    ) -> dict[str, Any]:
        if not hwnd:
            return {
                "ok": False,
                "verified": False,
                "semantic_visual_verified": False,
                "error": "draw stage has no bound target window for boundary verification",
            }
        session = ToolSession(
            stop_event=stop_event,
            task=goal,
            runtime_profile=RuntimeProfile(desktop=True, coding=False),
            task_intent={
                "original_text": goal,
                "operation": ["verify"],
                "send_mode": "send",
            },
            communication_context={"active": False},
        )
        session.run_id = run_id
        elapsed_ms = 0
        try:
            session._remember_target(hwnd, app)
            verify_args: dict[str, Any] = {
                "criteria": self._draw_boundary_criteria(subject, quality),
            }
            if isinstance(region, dict) and all(
                key in region for key in ("left", "top", "width", "height")
            ):
                verify_args.update({key: int(region[key]) for key in ("left", "top", "width", "height")})
            model_started = time.monotonic()
            try:
                try:
                    verdict = _json(session.dispatch("vlm_verify", verify_args).text)
                finally:
                    elapsed_ms = max(
                        0,
                        int((time.monotonic() - model_started) * 1000),
                    )
            except BaseException as exc:
                # Preserve failed/cancelled verifier time for the stage-level
                # failure trace without converting the original exception.
                try:
                    exc.model_elapsed_ms = elapsed_ms
                except Exception:
                    pass
                raise
        finally:
            session.close()
        verdict["elapsed_ms"] = elapsed_ms
        BUS.publish(
            "task_stage_boundary_verify",
            run_id=run_id,
            stage_id=stage_id,
            stage_type=StageType.DRAW_ARTIFACT.value,
            provider="semantic_visual_judge",
            verified=bool(
                verdict.get("ok") is True
                and verdict.get("verified") is True
                and verdict.get("semantic_visual_verified") is True
            ),
            elapsed_ms=elapsed_ms,
            verdict=verdict,
        )
        return verdict

    def _adapter(
        self,
        context: dict[str, Any],
        contract: TaskContract,
        stop_event,
    ) -> CommunicationAdapter:
        state = context["runtime_state"]
        adapter = state.get("communication_adapter")
        if isinstance(adapter, CommunicationAdapter):
            return adapter
        adapter = DomainAdapterRegistry.create(
            contract.intent.channel,
            run_id=context["run_id"],
            task=contract.task,
            store=self._store(context),
            evidence=self._evidence(context),
            stop_event=stop_event,
            task_intent=contract.intent,
        )
        state["communication_adapter"] = adapter
        return adapter

    @staticmethod
    def _stage_output(stage: TaskStage, started: float, **values: Any) -> dict[str, Any]:
        selection = stage.execution
        return {
            "stage_id": stage.id,
            "stage_type": stage.type.value,
            "status": "done",
            "controller_elapsed_ms": int((time.monotonic() - started) * 1000),
            "required_capabilities": selection.required_capabilities if selection else [],
            "verifier_requirements": selection.verifier_requirements if selection else [],
            "exposed_capabilities": selection.exposed_capabilities if selection else [],
            "missing_capabilities": selection.missing_capabilities if selection else [],
            "satisfiable": selection.satisfiable if selection else False,
            "tools": selection.tools if selection else [],
            "model_tools": selection.model_tools if selection else [],
            "controller_tools": selection.controller_tools if selection else [],
            "adapter": selection.adapter if selection else "",
            "model_role": selection.model_role if selection else "none",
            **values,
        }

    @staticmethod
    def _semantic_stage_metrics(
        started: float,
        runner: Any,
        *,
        boundary_elapsed_ms: int = 0,
    ) -> dict[str, int]:
        """Split semantic-stage wall time into model wait and local work.

        ``runner.model_elapsed_ms`` already contains both main-model turns and
        inline semantic VLM calls.  Only the independent boundary verifier is
        added here; reading its verdict again must never double-charge an
        inline verdict.
        """
        model_elapsed_ms = max(
            0,
            int(getattr(runner, "model_elapsed_ms", 0) or 0),
        ) + max(0, int(boundary_elapsed_ms or 0))
        total_elapsed_ms = max(0, int((time.monotonic() - started) * 1000))
        return {
            "model_elapsed_ms": model_elapsed_ms,
            "controller_elapsed_ms": max(
                0,
                total_elapsed_ms - model_elapsed_ms,
            ),
        }

    def execute(self, node: ArpNode, context: dict[str, Any], stop_event) -> NodeResult:
        started = time.monotonic()
        stage = TaskStage.model_validate(node.config.get("stage") or {})
        contract = self._contract(context)
        self.capabilities.assert_satisfiable(contract)
        context["runtime_state"]["current_stage"] = stage.model_dump(mode="json")
        BUS.publish(
            "task_stage_start",
            run_id=context["run_id"],
            node_id=node.id,
            stage_id=stage.id,
            stage_type=stage.type.value,
            capabilities=stage.execution.model_dump(mode="json") if stage.execution else {},
        )
        before = len(self._evidence(context).records())
        try:
            result = self._execute(stage, contract, context, stop_event, started)
        except Exception as exc:
            failure_type = getattr(exc, "failure_type", "stage_exception")
            recovery_attempt = self._recovery_attempt(
                context, stage, str(failure_type),
            )
            decision = self.recovery.decide(
                stage.type.value, failure_type, attempt=recovery_attempt,
            )
            stage_details = getattr(exc, "stage_details", {})
            if stage.type in {
                StageType.DRAW_ARTIFACT,
                StageType.CREATE_OR_EDIT_ARTIFACT,
            }:
                stage_details = (
                    dict(stage_details)
                    if isinstance(stage_details, dict)
                    else {}
                )
                model_elapsed_ms = max(
                    0,
                    int(stage_details.get("model_elapsed_ms") or 0),
                )
                stage_details.update({
                    "model_elapsed_ms": model_elapsed_ms,
                    "controller_elapsed_ms": max(
                        0,
                        int((time.monotonic() - started) * 1000)
                        - model_elapsed_ms,
                    ),
                })
            if stage.type in INTERACTION_STAGE_TYPES:
                try:
                    recovered = self.interactions.recover_failure(
                        stage,
                        contract,
                        context,
                        started,
                        failure_type=str(failure_type),
                        recovery_action=decision.action,
                        recovery_reason=decision.reason,
                        retryable=decision.retryable,
                        failure_details=(
                            stage_details
                            if isinstance(stage_details, dict)
                            else {}
                        ),
                    )
                except Exception as recovery_exc:
                    # Budget exhaustion while scheduling recovery is terminal;
                    # preserve that precise failure instead of reporting the
                    # earlier, recoverable symptom.
                    exc = recovery_exc
                    failure_type = getattr(
                        recovery_exc, "failure_type", "stage_exception",
                    )
                    stage_details = getattr(recovery_exc, "stage_details", {})
                    decision = self.recovery.decide(
                        stage.type.value,
                        failure_type,
                        attempt=recovery_attempt,
                    )
                    recovered = None
                if recovered is not None:
                    recovered_output = (
                        recovered.output
                        if isinstance(recovered.output, dict)
                        else {}
                    )
                    BUS.publish(
                        "task_stage_recovery",
                        run_id=context["run_id"],
                        node_id=node.id,
                        stage_id=stage.id,
                        stage_type=stage.type.value,
                        failure_type=failure_type,
                        recovery=decision.__dict__,
                        recovery_attempt=recovery_attempt,
                        branch=recovered.branch,
                        error=str(exc),
                    )
                    BUS.publish(
                        "task_stage_done",
                        run_id=context["run_id"],
                        node_id=node.id,
                        stage_id=stage.id,
                        stage_type=stage.type.value,
                        status="recovering",
                        elapsed_ms=int((time.monotonic() - started) * 1000),
                        controller_elapsed_ms=int(
                            recovered_output.get("controller_elapsed_ms") or 0
                        ),
                        model_elapsed_ms=int(
                            recovered_output.get("model_elapsed_ms") or 0
                        ),
                        artifact_ids=[],
                        evidence_ids=[],
                        model_calls=int(
                            recovered_output.get("model_calls") or 0
                        ),
                    )
                    return recovered
                interaction_state = context["runtime_state"].setdefault(
                    "interaction_state", {},
                )
                interaction_state.update({
                    "status": "failed",
                    "failure_stage": stage.id,
                    "failure_type": failure_type,
                    "error": str(exc),
                })
            BUS.publish(
                "task_stage_failed",
                run_id=context["run_id"],
                node_id=node.id,
                stage_id=stage.id,
                stage_type=stage.type.value,
                failure_type=failure_type,
                recovery=decision.__dict__,
                recovery_attempt=recovery_attempt,
                error=str(exc),
            )
            return NodeResult(
                ok=False,
                branch="fail",
                output=self._stage_output(
                    stage, started, status="failed", failure_type=failure_type,
                    recovery=decision.__dict__,
                    recovery_attempt=recovery_attempt,
                    error=str(exc),
                    **(stage_details if isinstance(stage_details, dict) else {}),
                ),
                error=f"{stage.id} ({stage.type.value}) failed: {exc}",
            )
        records = self._evidence(context).records()[before:]
        result.evidence.extend(records)
        if not result.ok:
            output = result.output if isinstance(result.output, dict) else {}
            failure_type = str(output.get("failure_type") or "stage_failed")
            recovery_attempt = self._recovery_attempt(
                context, stage, failure_type,
            )
            decision = self.recovery.decide(
                stage.type.value, failure_type, attempt=recovery_attempt,
            )
            output["recovery"] = decision.__dict__
            output["recovery_attempt"] = recovery_attempt
            result.output = output
            BUS.publish(
                "task_stage_failed",
                run_id=context["run_id"],
                node_id=node.id,
                stage_id=stage.id,
                stage_type=stage.type.value,
                failure_type=failure_type,
                recovery=decision.__dict__,
                error=result.error,
            )
            return result
        self._clear_recovery_attempts(context, stage)
        BUS.publish(
            "task_stage_done",
            run_id=context["run_id"],
            node_id=node.id,
            stage_id=stage.id,
            stage_type=stage.type.value,
            elapsed_ms=int((time.monotonic() - started) * 1000),
            controller_elapsed_ms=int(
                (result.output or {}).get("controller_elapsed_ms") or 0
            ) if isinstance(result.output, dict) else 0,
            model_elapsed_ms=int(
                (result.output or {}).get("model_elapsed_ms") or 0
            ) if isinstance(result.output, dict) else 0,
            artifact_ids=[item.get("artifact_id") for item in records if item.get("artifact_id")],
            evidence_ids=[item.get("id") for item in records],
            model_calls=int((result.output or {}).get("model_calls") or 0) if isinstance(result.output, dict) else 0,
        )
        return result

    def _execute(
        self,
        stage: TaskStage,
        contract: TaskContract,
        context: dict[str, Any],
        stop_event,
        started: float,
    ) -> NodeResult:
        registry = self._evidence(context)
        store = self._store(context)
        params = stage.parameters

        if stage.type in INTERACTION_STAGE_TYPES:
            return self.interactions.execute(
                stage,
                contract,
                context,
                stop_event,
                started,
                store=store,
                registry=registry,
            )

        if stage.type == StageType.OPEN_APPLICATION:
            app = str(params.get("app") or "").strip()
            session = ToolSession(
                stop_event=stop_event,
                task=contract.task,
                runtime_profile=build_runtime_profile(
                    contract.task, task_contract=contract, stage=stage,
                ),
                task_intent=contract.intent,
                communication_context={"active": False},
            )
            session.run_id = context["run_id"]
            try:
                data = _json(session.dispatch("open_app", {"name": app}).text)
            finally:
                session.close()
            if data.get("ok") is not True:
                raise RuntimeError(data.get("error") or f"could not open {app}")
            record = registry.record("application.open", provider="local_controller", verified=True, stage_id=stage.id, value=app, metadata=data)
            return NodeResult(output=self._stage_output(stage, started, evidence=record))

        if stage.type in {StageType.DRAW_ARTIFACT, StageType.CREATE_OR_EDIT_ARTIFACT}:
            app = str(params.get("app") or "").strip()
            bound_hwnd = 0
            if app:
                session = ToolSession(
                    stop_event=stop_event,
                    task=contract.task,
                    runtime_profile=build_runtime_profile(
                        contract.task, task_contract=contract, stage=stage,
                    ),
                    task_intent=contract.intent,
                    communication_context={"active": False},
                )
                session.run_id = context["run_id"]
                try:
                    opened = _json(session.dispatch("open_app", {"name": app}).text)
                finally:
                    session.close()
                if opened.get("ok") is not True:
                    raise RuntimeError(opened.get("error") or f"could not open {app}")
                bound_hwnd = self._resolve_hwnd(app)
                if not bound_hwnd:
                    raise RuntimeError(f"opened application did not expose a bindable target window: {app}")
            if stage.type == StageType.DRAW_ARTIFACT:
                subject = str(params.get("subject") or "the requested subject")
                quality = str(params.get("quality") or "normal")
                goal = (
                    f"In the already opened {app or 'drawing application'}, draw {subject}. "
                    + (
                        "The visible result must be polished and detailed."
                        if quality == "detailed"
                        else "The visible result must be a clearly recognizable subject."
                    )
                )
                stage_guidance = (
                    "Create one base composition in one local Controller transaction. Prefer execute_subgoal with one ordered draws list: "
                    "use simple outline primitives or explicit normalized_strokes, semantic part labels, and canvas-relative 0..1000 geometry. "
                    "Choose one conventional coherent view. Multipart subjects must share a readable silhouette and connected spatial relationships; do not stack detached symbol-like primitives. "
                    "Do not use screenshot pixels or physical coordinates as normalized geometry. Then inspect the automatically attached fresh canvas frame. "
                    "Make only forward-only localized repairs for visible defects; do not redraw the whole composition or add unrequested detail. "
                    "Respect the requested subject scope: a complete object or creature must not be replaced by only a head, icon, or fragment unless the user explicitly asked for that partial view. "
                    "As soon as the subject is clearly recognizable, call done so the independent fresh-frame judge can verify it. "
                    + ("Apply a stricter polished/detail standard." if quality == "detailed" else "Require a clearly recognizable subject; do not demand decorative detail that was not requested.")
                )
                provider = "semantic_visual_judge"
                predicate = "artifact.semantic_visible"
            else:
                goal = str(params.get("goal") or contract.task)
                stage_guidance = ""
                provider = "semantic_stage_done"
                predicate = "stage.verified_outcome"
            profile = build_runtime_profile(
                goal,
                task_contract=contract,
                stage=stage,
                stage_guidance=stage_guidance,
                model_tool_allowlist=(
                    tuple(stage.execution.model_tools)
                    if stage.type == StageType.DRAW_ARTIFACT and stage.execution
                    else ()
                ),
                inline_semantic_verification=stage.type == StageType.DRAW_ARTIFACT,
            )
            stage_intent = self._stage_scoped_intent(contract, stage, goal)
            runner = build_runner(
                goal,
                stop_event=stop_event,
                max_steps_override=(
                    3
                    if stage.type == StageType.DRAW_ARTIFACT
                    else min(24, max(4, int(params.get("max_steps") or 40)))
                ),
                runtime_profile=profile,
                task_intent=stage_intent,
                communication_context=self._stage_communication_context(
                    stage, stage_intent,
                ),
            )
            if bound_hwnd:
                runner.bind_target_window(bound_hwnd, app)
            try:
                result = runner.run()
            except Exception as exc:
                metrics = self._semantic_stage_metrics(started, runner)
                details = getattr(exc, "stage_details", {})
                exc.stage_details = {
                    **(details if isinstance(details, dict) else {}),
                    "child_run_id": runner.run_id,
                    "child_status": getattr(runner, "status", "error"),
                    "usage": dict(runner.usage_total),
                    "model_calls": int(
                        getattr(runner, "model_request_count", 0) or 0
                    ),
                    **metrics,
                }
                raise
            successful_draw_mutation = self._has_successful_draw_mutation(
                list(getattr(runner, "_tool_history", []) or [])
            )
            runner_verified = bool(
                runner.status == "done" and getattr(runner, "_done_verified", False)
                and (stage.type != StageType.DRAW_ARTIFACT or successful_draw_mutation)
                and (
                    stage.type != StageType.DRAW_ARTIFACT
                    or bool(getattr(runner, "_inline_stage_semantic_passed", False))
                )
            )
            boundary_verdict: dict[str, Any] = {}
            boundary_calls = 0
            boundary_elapsed_ms = 0
            if (
                not runner_verified
                and stage.type == StageType.DRAW_ARTIFACT
                and successful_draw_mutation
                and not bool(getattr(runner, "_inline_stage_semantic_attempted", False))
            ):
                boundary_calls = 1
                try:
                    boundary_verdict = self._verify_draw_boundary(
                        goal=goal,
                        subject=subject,
                        quality=quality,
                        app=app,
                        hwnd=bound_hwnd,
                        run_id=context["run_id"],
                        stop_event=stop_event,
                        stage_id=stage.id,
                        region=(
                            getattr(runner.session, "_last_canvas_region", None)
                            if isinstance(getattr(runner.session, "_last_canvas_region", None), dict)
                            else None
                        ),
                    )
                except Exception as exc:
                    boundary_elapsed_ms = max(
                        0,
                        int(getattr(exc, "model_elapsed_ms", 0) or 0),
                    )
                    metrics = self._semantic_stage_metrics(
                        started,
                        runner,
                        boundary_elapsed_ms=boundary_elapsed_ms,
                    )
                    details = getattr(exc, "stage_details", {})
                    exc.failure_type = getattr(
                        exc,
                        "failure_type",
                        "semantic_boundary_verification_failed",
                    )
                    exc.stage_details = {
                        **(details if isinstance(details, dict) else {}),
                        "child_run_id": runner.run_id,
                        "child_status": runner.status,
                        "usage": dict(runner.usage_total),
                        "model_calls": int(
                            getattr(runner, "model_request_count", 0) or 0
                        ) + boundary_calls,
                        "boundary_verification": {
                            "ok": False,
                            "error": str(exc),
                            "elapsed_ms": boundary_elapsed_ms,
                        },
                        **metrics,
                    }
                    raise
                boundary_elapsed_ms = max(
                    0,
                    int(boundary_verdict.get("elapsed_ms") or 0),
                )
                runner_verified = bool(
                    boundary_verdict.get("ok") is True
                    and boundary_verdict.get("verified") is True
                    and boundary_verdict.get("semantic_visual_verified") is True
                )
            if not runner_verified:
                metrics = self._semantic_stage_metrics(
                    started,
                    runner,
                    boundary_elapsed_ms=boundary_elapsed_ms,
                )
                failure = RuntimeError(runner.result_text or f"semantic stage ended with {runner.status}")
                failure.failure_type = "semantic_stage_failed"
                failure.stage_details = {
                    "child_run_id": runner.run_id,
                    "child_status": runner.status,
                    "usage": dict(runner.usage_total),
                    "model_calls": int(getattr(runner, "model_request_count", 0) or 0) + boundary_calls,
                    "done_verified": bool(getattr(runner, "_done_verified", False)),
                    "inline_semantic_verification": dict(
                        getattr(runner, "_inline_stage_semantic_last_verdict", {}) or {}
                    ),
                    "successful_draw_mutation": successful_draw_mutation,
                    "boundary_verification": boundary_verdict,
                    **metrics,
                }
                raise failure
            record = registry.record(
                predicate,
                provider=provider,
                verified=True,
                stage_id=stage.id,
                value=goal,
                metadata={
                    "child_run_id": runner.run_id,
                    "usage": dict(runner.usage_total),
                    "agent_done_verified": bool(getattr(runner, "_done_verified", False)),
                    "inline_semantic_verification": dict(
                        getattr(runner, "_inline_stage_semantic_last_verdict", {}) or {}
                    ),
                    "successful_draw_mutation": successful_draw_mutation,
                    "boundary_verification": boundary_verdict,
                },
            )
            try:
                from ..memory import experience

                recipe_node = next((
                    item
                    for item in experience._store().list_nodes(
                        kinds={"skill", "procedure"},
                        include_inactive=True,
                        limit=200,
                    )
                    if str(item.get("source") or "") == str(runner.run_id)
                ), None) if contract.experience_policy != "disabled" else None
                if recipe_node and recipe_node.get("id"):
                    experience.update(str(recipe_node["id"]), {
                        "stage_type": stage.type.value,
                        "parameters": dict(stage.parameters),
                        "outputs": [item.model_dump(mode="json") for item in stage.outputs],
                        "stage_obligations": [item.id for item in stage.obligations],
                        "app_version": str(params.get("app_version") or ""),
                        "failure_reason": "",
                    }, preserve_trust=True)
            except Exception:
                # Memory enrichment must not change the stage result.
                pass
            model_calls = int(getattr(runner, "model_request_count", 0) or 0) + boundary_calls
            metrics = self._semantic_stage_metrics(
                started,
                runner,
                boundary_elapsed_ms=boundary_elapsed_ms,
            )
            return NodeResult(output=self._stage_output(
                stage, started, child_run_id=runner.run_id, result=result,
                usage=dict(runner.usage_total), model_calls=model_calls,
                boundary_verification=boundary_verdict, evidence=record,
                **metrics,
            ))

        if stage.type in {StageType.CAPTURE_ARTIFACT, StageType.SAVE_ARTIFACT}:
            source = str(params.get("source") or contract.intent.source or "active_window")
            hwnd = self._resolve_hwnd(source)
            if not hwnd:
                raise RuntimeError(f"source window not found: {source}")
            artifact = store.capture_artifact(
                scope=str(params.get("scope") or "window"),
                source_hwnd=hwnd,
                purpose="user_output",
                sendable=True,
                metadata={"stage_id": stage.id, "task_id": contract.task_id},
            )
            ref = str(params.get("artifact_ref") or "primary_artifact")
            context["runtime_state"].setdefault("artifact_refs", {})[ref] = artifact.artifact_id
            provider = "artifact_store_capture" if stage.type == StageType.CAPTURE_ARTIFACT else "artifact_store_save"
            predicate = "artifact.current_run_valid" if stage.type == StageType.CAPTURE_ARTIFACT else "artifact.saved"
            record = registry.record(
                predicate,
                provider=provider,
                verified=True,
                artifact_id=artifact.artifact_id,
                stage_id=stage.id,
                metadata={
                    "sha256": artifact.sha256,
                    "created_at": artifact.created_at,
                    "source_window": artifact.source_window,
                    "size_bytes": artifact.size_bytes,
                    "managed_path": artifact.managed_path,
                },
            )
            return NodeResult(output=self._stage_output(stage, started, artifact=artifact.model_dump(mode="json"), evidence=record))

        if stage.type == StageType.OPEN_CONVERSATION:
            adapter = self._adapter(context, contract, stop_event)
            opened = adapter.open_conversation(str(params.get("recipient") or contract.intent.recipient), stage_id=stage.id)
            if opened.get("ok") is not True:
                _raise_stage_failure(opened, "recipient_not_resolved", "conversation could not be opened")
            composer = adapter.resolve_composer()
            if composer.get("ok") is not True:
                _raise_stage_failure(composer, "composer_not_resolved", "conversation composer could not be resolved")
            return NodeResult(output=self._stage_output(stage, started, conversation=opened, composer=composer))

        if stage.type == StageType.SEND_ARTIFACT:
            adapter = self._adapter(context, contract, stop_event)
            artifact_id = self._artifact_ref(context, str(params.get("artifact_ref") or "primary_artifact"))
            staged = adapter.paste_or_attach_artifact(artifact_id, stage_id=stage.id)
            if staged.get("ok") is not True:
                _raise_stage_failure(staged, "artifact_stage_failed", "artifact was not staged")
            sent = adapter.send_current_composer(artifact_id=artifact_id, stage_id=stage.id)
            if sent.get("ok") is not True:
                _raise_stage_failure(sent, "send_action_missing", "send action failed")
            artifact = store.get(artifact_id, require_sendable=True)
            record = registry.record(
                "delivery.real_send_action",
                provider="communication_adapter_send",
                verified=True,
                artifact_id=artifact.artifact_id,
                recipient=contract.intent.recipient,
                stage_id=stage.id,
                metadata={"artifact_sha256": artifact.sha256, "staged": True, "send": sent},
            )
            return NodeResult(output=self._stage_output(stage, started, artifact_id=artifact_id, staged=staged, sent=sent, evidence=record))

        if stage.type == StageType.SEND_TEXT:
            adapter = self._adapter(context, contract, stop_event)
            text = str(params.get("text") or "")
            sent = adapter.send_text(text, stage_id=stage.id)
            if sent.get("ok") is not True:
                raise RuntimeError(sent.get("error") or "text send failed")
            record = registry.record(
                "delivery.real_send_action",
                provider="communication_adapter_send",
                verified=True,
                recipient=contract.intent.recipient,
                stage_id=stage.id,
                metadata={"text": text, "send": sent},
            )
            context["runtime_state"]["sent_text"] = text
            return NodeResult(output=self._stage_output(stage, started, sent=sent, evidence=record))

        if stage.type == StageType.VERIFY_DELIVERY:
            adapter = self._adapter(context, contract, stop_event)
            ref = str(params.get("artifact_ref") or "")
            artifact_id = self._artifact_ref(context, ref) if ref else ""
            verified = adapter.verify_latest_outbound(
                artifact_id=artifact_id,
                text=str(context["runtime_state"].get("sent_text") or ""),
                stage_id=stage.id,
            )
            if verified.get("ok") is not True:
                return NodeResult(
                    ok=False,
                    branch="fail",
                    output=self._stage_output(
                        stage, started, status="failed",
                        failure_type=verified.get("failure_type") or "delivery_not_visible",
                        verification=verified,
                    ),
                    error=str(verified.get("error") or "delivery verification failed"),
                )
            completion = CompletionContractVerifier(contract, registry, store).verify()
            registry.record(
                "contract.complete",
                provider="completion_contract_verifier",
                verified=bool(completion.get("ok")),
                stage_id=stage.id,
                metadata=completion,
            )
            if completion.get("ok") is not True:
                return NodeResult(ok=False, branch="fail", output=self._stage_output(stage, started, completion=completion), error=str(completion.get("reason")))
            adapter.close()
            return NodeResult(output=self._stage_output(stage, started, verification=verified, completion=completion))

        if stage.type == StageType.VERIFY_ARTIFACT:
            completion = CompletionContractVerifier(contract, registry, store).verify()
            if completion.get("ok") is not True:
                return NodeResult(ok=False, branch="fail", output=completion, error=str(completion.get("reason")))
            return NodeResult(output=self._stage_output(stage, started, completion=completion))

        raise RuntimeError(f"stage runtime does not implement {stage.type.value}")


STAGE_RUNTIME = StageRuntime()


def task_stage_handler(node: ArpNode, context: dict[str, Any], stop_event) -> NodeResult:
    return STAGE_RUNTIME.execute(node, context, stop_event)
