"""Deterministic state machine for one typed task stage."""
from __future__ import annotations

import ctypes
import json
import time
from typing import Any

from ..agent.router import build_runner
from ..agent.runtime_profile import build_runtime_profile
from ..agent.tools import ToolSession
from ..orchestrator.contracts import ArpNode
from ..orchestrator.runtime import NodeResult
from ..perception import apps, win32
from ..perception.app_aliases import expand as expand_app_aliases
from ..utils.log import BUS
from .adapters import CommunicationAdapter, DomainAdapterRegistry
from .artifacts import ArtifactStore
from .capability import CapabilityPlanner
from .completion import CompletionContractVerifier
from .conversation import generate_conversation_response
from .contracts import StageType, TaskContract, TaskIntent, TaskStage
from .evidence import EvidenceRegistry
from .execution_context import normalize_execution_context, validated_handoff_target
from .interaction_stage_runtime import (
    INTERACTION_STAGE_TYPES,
    InteractionStageEngine,
)
from .message_content import (
    MessageDraft,
    authorize_message_content,
    generate_message_content,
)
from .recovery import RecoveryPolicy


SEMANTIC_EXECUTION_STAGE_TYPES = frozenset({
    StageType.EXECUTE_DESKTOP_TASK,
    StageType.DRAW_ARTIFACT,
    StageType.CREATE_OR_EDIT_ARTIFACT,
    StageType.EXECUTE_WORKSPACE_TASK,
    StageType.RESEARCH_INFORMATION,
    StageType.TRANSFORM_DATA,
    StageType.MANAGE_AUTOMATION,
    StageType.EXECUTE_SEMANTIC_TASK,
})


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
        contract.  ExecuteDesktopTask is intentionally opaque: the original
        goal plus fresh pixels belong to the visual model, while compiler
        guesses about communication, drawing, games, or pointer tactics must
        not become runtime policy.
        """
        operation_by_stage = {
            StageType.OPEN_APPLICATION: "open",
            StageType.FOCUS_WINDOW: "focus",
            StageType.EXECUTE_DESKTOP_TASK: "operate",
            StageType.CREATE_OR_EDIT_ARTIFACT: "edit",
            StageType.DRAW_ARTIFACT: "operate",
            StageType.CAPTURE_ARTIFACT: "capture",
            StageType.SAVE_ARTIFACT: "save",
            StageType.OPEN_CONVERSATION: "send",
            StageType.RESOLVE_RECIPIENT: "send",
            StageType.GENERATE_MESSAGE_CONTENT: "draft_message",
            StageType.REVIEW_OR_AUTHORIZE: "authorize_message",
            StageType.SEND_TEXT: "send",
            StageType.SEND_ARTIFACT: "send",
            StageType.VERIFY_ARTIFACT: "verify",
            StageType.VERIFY_DELIVERY: "send",
            StageType.OBSERVE_SCENE: "interact",
            StageType.INFER_INTERACTION: "interact",
            StageType.PERFORM_TURN: "interact",
            StageType.VERIFY_LOCAL_EFFECT: "interact",
            StageType.WAIT_FOR_EXTERNAL_CHANGE: "interact",
            StageType.EXECUTE_WORKSPACE_TASK: "workspace",
            StageType.RESEARCH_INFORMATION: "research",
            StageType.TRANSFORM_DATA: "transform_data",
            StageType.MANAGE_AUTOMATION: "automation",
            StageType.EXECUTE_SEMANTIC_TASK: "execute",
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

        parameters = stage.parameters or {}
        if stage.type == StageType.EXECUTE_DESKTOP_TASK:
            return TaskIntent(
                original_text=str(
                    goal
                    or parameters.get("original_task")
                    or parameters.get("goal")
                    or contract.task
                ),
                operation=["operate"],
                domain="desktop",
                provenance={
                    "stage_scope": f"{contract.task_id}:{stage.id}",
                    "semantic_owner": "visual_model",
                    "intent_policy": "opaque_original_goal",
                },
            )

        payload = contract.intent.model_dump(mode="json")
        provenance = dict(payload.get("provenance") or {})
        provenance.update({
            "stage_scope": f"{contract.task_id}:{stage.id}",
            "stage_operation": "task_contract_stage",
        })
        preserve_original_intent = stage.type in {
            StageType.EXECUTE_DESKTOP_TASK,
            StageType.DRAW_ARTIFACT,
        }
        payload.update({
            "original_text": str(goal or ""),
            "operation": (
                list(payload.get("operation") or [])
                if preserve_original_intent
                else [operation_by_stage.get(stage.type, "execute")]
            ),
            "source": str(
                parameters.get("source")
                or parameters.get("app")
                or payload.get("source")
                or ""
            ),
            "subject": str(
                parameters.get("subject")
                or payload.get("subject")
                or ""
            ),
            "quality": str(
                parameters.get("quality")
                or payload.get("quality")
                or "normal"
            ),
            "ambiguous_fields": (
                list(payload.get("ambiguous_fields") or [])
                if preserve_original_intent
                else []
            ),
            "provenance": provenance,
        })
        preserves_communication = bool(
            stage.type in communication_stages
            or stage.type in {
                StageType.EXECUTE_DESKTOP_TASK,
                StageType.DRAW_ARTIFACT,
            }
        )
        if not preserves_communication:
            payload.update({
                "channel": "",
                "recipient": "",
                "destination": "",
                "send_mode": "send",
            })
        # ExecuteDesktopTask is the general visual-agent stage.  It may carry a
        # drag/hold/path interaction discovered by the compiler even though it
        # is not one of the legacy typed interaction stages.  Clearing that
        # state here made named-app games, sliders and other direct manipulation
        # tasks lose capabilities before AgentRunner saw them.
        if (
            stage.type not in interaction_stages
            and stage.type not in {
                StageType.EXECUTE_DESKTOP_TASK,
                StageType.DRAW_ARTIFACT,
            }
        ):
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
        active = bool(
            stage.type in communication_stages
            or (
                stage.type in {
                    StageType.EXECUTE_DESKTOP_TASK,
                    StageType.DRAW_ARTIFACT,
                }
                and "send" in intent.operation
            )
        )
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
            queries = list(dict.fromkeys(expand_app_aliases(source) or [source]))
            try:
                for candidate in apps.resolve(source, max_results=3):
                    queries.append(str(candidate.get("name") or ""))
                    queries.extend(
                        str(item) for item in (candidate.get("aliases") or [])
                    )
            except Exception:
                pass
            for query in dict.fromkeys(item.strip() for item in queries if item.strip()):
                match = win32.find_window(query)
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
    def _desktop_effect_summary(history: list | tuple) -> dict[str, Any]:
        """Summarize successful, non-observational work from a child runner.

        This is intentionally capability-shaped rather than application-
        shaped.  It prevents an open-only run (and a capability list typed into
        Notepad) from satisfying a larger desktop workflow while still
        allowing arbitrary future applications to use the same GUI controls.
        """

        category_by_tool = {
            "click": "gui_control",
            "hotkey": "gui_control",
            "scroll": "gui_control",
            "drag": "gui_control",
            "type_text": "gui_control",
            "run_action_batch": "gui_control",
            "execute_subgoal": "gui_control",
            "draw_strokes": "drawing",
            "capture_artifact": "artifact",
            "screenshot": "artifact",
            "send_text": "communication",
            "send_artifact": "communication",
            "paste_image_file": "communication",
            "web_search": "research",
            "run_shell": "workspace",
            "write_file": "workspace",
            "apply_patch": "workspace",
            "schedule_task": "automation",
            "call_mcp_tool": "external_provider",
        }
        ignored = {
            "open_app", "launch", "focus_window", "find_app",
            "find_program", "list_windows", "scan_windows", "observe",
            "read_text", "vlm_describe", "vlm_verify", "done", "blocked",
            "fail", "revise_plan", "complete_subgoal",
            "discover_mcp_tools",
        }
        successful_tools: list[str] = []
        categories: set[str] = set()
        evidence_packets: list[dict[str, Any]] = []
        for item in history or []:
            if not isinstance(item, (list, tuple)) or len(item) < 3:
                continue
            name = str(item[0] or "").strip().casefold()
            raw = item[2]
            payload = raw if isinstance(raw, dict) else _json(str(raw or ""))
            if name in ignored or not isinstance(payload, dict):
                continue
            if payload.get("blocked") is True or payload.get("ok") is not True:
                continue
            successful_tools.append(name)
            categories.add(category_by_tool.get(name, "gui_control"))
            packet = payload.get("evidence_packet")
            if isinstance(packet, dict):
                evidence_packets.append(packet)
        return {
            "successful_effect_count": len(successful_tools),
            "successful_effect_tools": successful_tools,
            "effect_categories": sorted(categories),
            "effect_category_count": len(categories),
            "evidence_packets": evidence_packets[-8:],
            "open_only": not successful_tools,
        }

    @staticmethod
    def _validated_open_result(data: dict[str, Any], app: str) -> dict[str, Any]:
        """Fail closed unless the controller proved a concrete target window."""

        hwnd = int(data.get("target_hwnd") or 0)
        focus = data.get("focus") if isinstance(data.get("focus"), dict) else {}
        launch = data.get("launch") if isinstance(data.get("launch"), dict) else {}
        launch_focus = (
            launch.get("focus_after_launch")
            if isinstance(launch.get("focus_after_launch"), dict)
            else {}
        )
        focused = bool(
            focus.get("ok") is True
            or launch_focus.get("ok") is True
            or launch.get("foreground_matches_target") is True
            or data.get("foreground_matches_target") is True
        )
        verified = bool(
            data.get("ok") is True
            and data.get("verified") is True
            and hwnd > 0
            and focused
        )
        return {
            "ok": verified,
            "verified": verified,
            "app": app,
            "target_hwnd": hwnd,
            "title": str(data.get("title") or ""),
            "focused": focused,
            "controller_result": data,
            **({
                "failure_type": "application_target_unverified",
                "error": (
                    "controller did not prove that the requested application "
                    "owns a focused target window"
                ),
            } if not verified else {}),
        }

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
            "selected_evidence_providers": (
                selection.selected_evidence_providers if selection else {}
            ),
            **values,
        }

    @staticmethod
    def _semantic_stage_metrics(
        started: float,
        runner: Any,
    ) -> dict[str, int]:
        """Split semantic-stage wall time into model wait and local work."""
        model_elapsed_ms = max(
            0,
            int(getattr(runner, "model_elapsed_ms", 0) or 0),
        )
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
            recovery_attempt = None
            decision = None
            if stage.type in INTERACTION_STAGE_TYPES:
                recovery_attempt = self._recovery_attempt(
                    context, stage, str(failure_type),
                )
                decision = self.recovery.decide(
                    stage.type.value, failure_type, attempt=recovery_attempt,
                )
            stage_details = getattr(exc, "stage_details", {})
            stage_details = (
                dict(stage_details)
                if isinstance(stage_details, dict)
                else {}
            )
            stage_details.setdefault("error", str(exc))
            if stage.type in SEMANTIC_EXECUTION_STAGE_TYPES:
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
                assert decision is not None and recovery_attempt is not None
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
            failure_event = {
                "run_id": context["run_id"],
                "node_id": node.id,
                "stage_id": stage.id,
                "stage_type": stage.type.value,
                "failure_type": failure_type,
                "error": str(exc),
            }
            recovery_output = {}
            if decision is not None and recovery_attempt is not None:
                recovery_output = {
                    "recovery": decision.__dict__,
                    "recovery_attempt": recovery_attempt,
                }
                failure_event.update(recovery_output)
            BUS.publish("task_stage_failed", **failure_event)
            output_details = dict(stage_details)
            for reserved_key in (
                "status", "failure_type", "recovery", "recovery_attempt", "error",
            ):
                output_details.pop(reserved_key, None)
            return NodeResult(
                ok=False,
                branch="fail",
                output=self._stage_output(
                    stage, started, status="failed", failure_type=failure_type,
                    error=str(exc),
                    **recovery_output,
                    **output_details,
                ),
                error=f"{stage.id} ({stage.type.value}) failed: {exc}",
            )
        records = self._evidence(context).records()[before:]
        result.evidence.extend(records)
        if not result.ok:
            output = result.output if isinstance(result.output, dict) else {}
            failure_type = str(output.get("failure_type") or "stage_failed")
            failure_event = {
                "run_id": context["run_id"],
                "node_id": node.id,
                "stage_id": stage.id,
                "stage_type": stage.type.value,
                "failure_type": failure_type,
                "error": result.error,
            }
            if stage.type in INTERACTION_STAGE_TYPES:
                recovery_attempt = self._recovery_attempt(
                    context, stage, failure_type,
                )
                decision = self.recovery.decide(
                    stage.type.value, failure_type, attempt=recovery_attempt,
                )
                output["recovery"] = decision.__dict__
                output["recovery_attempt"] = recovery_attempt
                failure_event["recovery"] = decision.__dict__
            result.output = output
            BUS.publish("task_stage_failed", **failure_event)
            return result
        if stage.type in INTERACTION_STAGE_TYPES:
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
                data = _json(session.dispatch(
                    "open_app", {"name": app, "strict_resolution": True},
                ).text)
            finally:
                session.close()
            verdict = self._validated_open_result(data, app)
            if verdict.get("ok") is not True:
                failure = RuntimeError(
                    verdict.get("error") or data.get("error") or f"could not open {app}"
                )
                failure.failure_type = str(
                    verdict.get("failure_type") or "application_target_unverified"
                )
                failure.stage_details = {"open_verification": verdict}
                raise failure
            record = registry.record(
                "application.open",
                provider="local_controller",
                verified=True,
                stage_id=stage.id,
                value=app,
                metadata=verdict,
            )
            return NodeResult(output=self._stage_output(stage, started, evidence=record))

        if stage.type in SEMANTIC_EXECUTION_STAGE_TYPES:
            # New GUI contracts use ExecuteDesktopTask.  DrawArtifact remains
            # readable only as a task-contract/1 alias and is normalized before
            # any runtime policy, prompt, tool, or verification decision.
            general_desktop_stage = stage.type in {
                StageType.EXECUTE_DESKTOP_TASK,
                StageType.DRAW_ARTIFACT,
            }
            if (
                stage.type == StageType.EXECUTE_SEMANTIC_TASK
                and str(params.get("response_mode") or "") == "conversation"
            ):
                response = generate_conversation_response(
                    contract.task,
                    list(contract.session_context or []),
                    stop_event=stop_event,
                )
                record = registry.record(
                    "stage.verified_outcome",
                    provider="semantic_stage_done",
                    verified=True,
                    stage_id=stage.id,
                    value=response.text,
                    metadata={
                        "response_mode": "conversation",
                        "text_only": True,
                        "model_calls": response.model_calls,
                    },
                )
                return NodeResult(output=self._stage_output(
                    stage,
                    started,
                    result=response.text,
                    response_text=response.text,
                    model_calls=response.model_calls,
                    model_elapsed_ms=response.model_elapsed_ms,
                    controller_elapsed_ms=0,
                    evidence=record,
                ))
            app = str(params.get("app") or "").strip()
            if app.casefold() in {"active_window", "foreground", "current"}:
                app = ""
            bound_hwnd = 0
            bound_target_hint = app
            if not app:
                execution_context = normalize_execution_context(context.get("inputs"))
                handoff_hwnd, handoff_error = validated_handoff_target(execution_context)
                if handoff_error is not None:
                    failure = RuntimeError(str(
                        handoff_error.get("reason") or "desktop target handoff failed"
                    ))
                    failure.failure_type = str(
                        handoff_error.get("failure_type") or "target_binding_failed"
                    )
                    failure.stage_details = {"desktop_handoff": handoff_error}
                    raise failure
                bound_hwnd = int(handoff_hwnd or 0)
                target_window = execution_context.get("target_window")
                if isinstance(target_window, dict):
                    bound_target_hint = str(target_window.get("title") or "")
            # A semantic desktop runner owns application discovery, launch,
            # focus, observation, and every later GUI action as one closed
            # loop.  Pre-opening here created a second action owner: a launch
            # failure could terminate the stage before the runner made even
            # one model call, and the runner could not recover from a delayed
            # or initially ambiguous window.  Deterministic OpenApplication
            # stages still use the stage-level adapter elsewhere; only the
            # complete desktop task enters through the semantic action loop.
            if app and not general_desktop_stage:
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
                    opened = _json(session.dispatch(
                        "open_app", {"name": app, "strict_resolution": True},
                    ).text)
                finally:
                    session.close()
                open_verdict = self._validated_open_result(opened, app)
                if open_verdict.get("ok") is not True:
                    failure = RuntimeError(
                        open_verdict.get("error")
                        or opened.get("error")
                        or f"could not open {app}"
                    )
                    failure.failure_type = "application_target_unverified"
                    failure.stage_details = {"open_verification": open_verdict}
                    raise failure
                bound_hwnd = int(open_verdict.get("target_hwnd") or 0)
            if general_desktop_stage:
                goal = str(
                    params.get("original_task")
                    or params.get("goal")
                    or contract.task
                )
                # Evidence names belong to the serialized contract, while the
                # behavior that proves them is always the same general desktop
                # done judge.  Reading an older contract therefore does not
                # require a second drawing verifier or a misleading duplicate
                # model call.
                obligation = next(iter(stage.obligations), None)
                predicate = (
                    obligation.evidence_predicate
                    if obligation is not None
                    else "desktop.full_goal_verified"
                )
                provider = (
                    str(obligation.evidence_providers[0].id)
                    if obligation is not None and obligation.evidence_providers
                    else "desktop_closed_loop_verifier"
                )
            else:
                goal = str(params.get("goal") or contract.task)
                provider = "semantic_stage_done"
                predicate = "stage.verified_outcome"
            runtime_stage = stage
            if stage.type == StageType.DRAW_ARTIFACT:
                runtime_stage = stage.model_copy(
                    update={"type": StageType.EXECUTE_DESKTOP_TASK},
                    deep=True,
                )
                runtime_stage = runtime_stage.model_copy(
                    update={
                        "execution": self.capabilities.plan_stage(
                            runtime_stage,
                            channel=contract.intent.channel,
                        ),
                    },
                    deep=True,
                )
            profile = build_runtime_profile(
                goal,
                task_contract=contract,
                stage=runtime_stage,
                model_tool_allowlist=(
                    tuple(runtime_stage.execution.model_tools)
                    if general_desktop_stage and runtime_stage.execution
                    else ()
                ),
            )
            stage_intent = self._stage_scoped_intent(contract, runtime_stage, goal)
            runner = build_runner(
                goal,
                stop_event=stop_event,
                pause_controller=context.get("pause_controller"),
                max_steps_override=max(1, int(params.get("max_steps") or 40)),
                runtime_profile=profile,
                task_intent=stage_intent,
                communication_context=self._stage_communication_context(
                    runtime_stage, stage_intent,
                ),
                session_context=list(contract.session_context or []),
            )
            on_runner = context.get("on_runner")
            if callable(on_runner):
                on_runner(runner)
            BUS.publish(
                "arp_child_run",
                run_id=context["run_id"],
                node_id=stage.id,
                child_run_id=runner.run_id,
                child_kind="agent",
                task=goal,
            )
            if bound_hwnd:
                runner.bind_target_window(bound_hwnd, bound_target_hint)
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
            runner_verified = bool(
                runner.status == "done" and getattr(runner, "_done_verified", False)
            )
            desktop_effects: dict[str, Any] = {}
            if general_desktop_stage:
                desktop_effects = self._desktop_effect_summary(
                    list(getattr(runner, "_tool_history", []) or [])
                )
                if params.get("forbid_open_only") is True and desktop_effects.get("open_only"):
                    runner_verified = False
            if not runner_verified:
                metrics = self._semantic_stage_metrics(started, runner)
                failure = RuntimeError(runner.result_text or f"semantic stage ended with {runner.status}")
                failure.failure_type = "semantic_stage_failed"
                failure.stage_details = {
                    "child_run_id": runner.run_id,
                    "child_status": runner.status,
                    "usage": dict(runner.usage_total),
                    "model_calls": int(getattr(runner, "model_request_count", 0) or 0),
                    "done_verified": bool(getattr(runner, "_done_verified", False)),
                    "desktop_effects": desktop_effects,
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
                    "desktop_effects": desktop_effects,
                    "runtime_stage_type": profile.stage_type,
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
            model_calls = int(getattr(runner, "model_request_count", 0) or 0)
            metrics = self._semantic_stage_metrics(started, runner)
            return NodeResult(output=self._stage_output(
                stage, started, child_run_id=runner.run_id, result=result,
                usage=dict(runner.usage_total), model_calls=model_calls,
                runtime_stage_type=profile.stage_type,
                desktop_effects=desktop_effects,
                evidence=record,
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

        if stage.type == StageType.GENERATE_MESSAGE_CONTENT:
            draft = generate_message_content(
                instruction=str(params.get("instruction") or contract.task),
                recipient=str(params.get("recipient") or contract.intent.recipient),
                channel=str(params.get("app") or contract.intent.channel),
                stop_event=stop_event,
            )
            ref = str(params.get("message_ref") or "outbound_message_draft")
            context["runtime_state"].setdefault("message_drafts", {})[ref] = (
                draft.model_dump(mode="json")
            )
            if (
                draft.requires_user_input
                or not draft.grounded
                or not draft.text.strip()
            ):
                failure = RuntimeError(
                    "message draft requires missing context"
                    if draft.requires_user_input
                    else "message generator did not produce grounded non-empty content"
                )
                failure.failure_type = (
                    "message_context_missing"
                    if draft.requires_user_input
                    else "message_content_ungrounded"
                )
                failure.stage_details = {
                    "draft": draft.model_dump(mode="json"),
                    "model_calls": draft.model_calls,
                    "model_elapsed_ms": draft.model_elapsed_ms,
                }
                raise failure
            record = registry.record(
                "message.content_generated",
                provider="text_message_generator",
                verified=True,
                recipient=contract.intent.recipient,
                stage_id=stage.id,
                metadata={
                    "message_ref": ref,
                    "content_source": draft.source,
                    "confidence": draft.confidence,
                    "grounded": draft.grounded,
                    "length": len(draft.text),
                },
            )
            return NodeResult(output=self._stage_output(
                stage,
                started,
                message_ref=ref,
                draft={
                    "text": draft.text,
                    "confidence": draft.confidence,
                    "grounded": draft.grounded,
                    "requires_user_input": draft.requires_user_input,
                },
                evidence=record,
                model_calls=draft.model_calls,
                model_elapsed_ms=draft.model_elapsed_ms,
                controller_elapsed_ms=max(
                    0,
                    int((time.monotonic() - started) * 1000)
                    - draft.model_elapsed_ms,
                ),
            ))

        if stage.type == StageType.REVIEW_OR_AUTHORIZE:
            source = str(params.get("content_source") or "none")
            draft = None
            if source == "generate":
                ref = str(params.get("draft_ref") or "outbound_message_draft")
                payload = context["runtime_state"].get("message_drafts", {}).get(ref)
                if isinstance(payload, dict):
                    draft = MessageDraft.model_validate(payload)
            authorization = authorize_message_content(
                text=str(params.get("text") or ""),
                content_source=source,
                generated_draft=draft,
            )
            if authorization.get("ok") is not True:
                _raise_stage_failure(
                    authorization,
                    "message_not_authorized",
                    "outbound message was not authorized",
                )
            ref = str(params.get("message_ref") or "authorized_outbound_message")
            context["runtime_state"].setdefault("authorized_messages", {})[ref] = {
                "text": str(authorization["text"]),
                "content_source": str(authorization["content_source"]),
                "stage_id": stage.id,
                "task_id": contract.task_id,
            }
            record = registry.record(
                "message.content_authorized",
                provider="message_contract_review",
                verified=True,
                recipient=contract.intent.recipient,
                stage_id=stage.id,
                metadata={
                    "message_ref": ref,
                    "content_source": authorization["content_source"],
                    "length": len(str(authorization["text"])),
                    "task_id": contract.task_id,
                },
            )
            return NodeResult(output=self._stage_output(
                stage,
                started,
                message_ref=ref,
                authorization={
                    "ok": True,
                    "content_source": authorization["content_source"],
                    "length": len(str(authorization["text"])),
                },
                evidence=record,
            ))

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
            message_ref = str(params.get("message_ref") or "")
            if message_ref:
                authorized = context["runtime_state"].get(
                    "authorized_messages", {}
                ).get(message_ref)
                if not isinstance(authorized, dict):
                    failure = RuntimeError(
                        "authorized outbound message is missing from current run state"
                    )
                    failure.failure_type = "message_authorization_missing"
                    raise failure
                if str(authorized.get("task_id") or "") != contract.task_id:
                    failure = RuntimeError(
                        "authorized outbound message does not belong to this task"
                    )
                    failure.failure_type = "message_authorization_scope_mismatch"
                    raise failure
                text = str(authorized.get("text") or "").strip()
            else:
                # Compatibility for serialized task-contract/1 graphs created
                # before outbound content became a first-class state binding.
                text = str(params.get("text") or "").strip()
            if not text:
                failure = RuntimeError("outbound message text is empty")
                failure.failure_type = "empty_message_content"
                raise failure
            adapter = self._adapter(context, contract, stop_event)
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
