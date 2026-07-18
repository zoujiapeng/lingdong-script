"""Versioned contracts shared by compilation, runtime, tools, and verification."""
from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


TASK_CONTRACT_FORMAT = "task-contract/1"


class StageType(str, Enum):
    OPEN_APPLICATION = "OpenApplication"
    FOCUS_WINDOW = "FocusWindow"
    EXECUTE_DESKTOP_TASK = "ExecuteDesktopTask"
    CREATE_OR_EDIT_ARTIFACT = "CreateOrEditArtifact"
    DRAW_ARTIFACT = "DrawArtifact"
    CAPTURE_ARTIFACT = "CaptureArtifact"
    SAVE_ARTIFACT = "SaveArtifact"
    OPEN_CONVERSATION = "OpenConversation"
    RESOLVE_RECIPIENT = "ResolveRecipient"
    GENERATE_MESSAGE_CONTENT = "GenerateMessageContent"
    REVIEW_OR_AUTHORIZE = "ReviewOrAuthorize"
    SEND_TEXT = "SendText"
    SEND_ARTIFACT = "SendArtifact"
    VERIFY_ARTIFACT = "VerifyArtifact"
    VERIFY_DELIVERY = "VerifyDelivery"
    OBSERVE_SCENE = "ObserveScene"
    INFER_INTERACTION = "InferInteraction"
    PERFORM_TURN = "PerformTurn"
    VERIFY_LOCAL_EFFECT = "VerifyLocalEffect"
    WAIT_FOR_EXTERNAL_CHANGE = "WaitForExternalChange"
    EXECUTE_WORKSPACE_TASK = "ExecuteWorkspaceTask"
    RESEARCH_INFORMATION = "ResearchInformation"
    TRANSFORM_DATA = "TransformData"
    MANAGE_AUTOMATION = "ManageAutomation"
    EXECUTE_SEMANTIC_TASK = "ExecuteSemanticTask"


class InteractionActor(BaseModel):
    """A participant in a live visual interaction.

    ``attributes`` deliberately carries open-ended role constraints (colour,
    tool, side, responsibility, and so on).  They are not flattened into task
    keywords because the same UI can assign entirely different roles at run
    time.
    """

    id: str
    kind: str = "external"
    display_name: str = ""
    role: str = ""
    attributes: dict[str, str] = Field(default_factory=dict)


class InteractionIntent(BaseModel):
    """Structured candidate for a shared, stateful visual interaction."""

    active: bool = False
    mode: str = "none"
    actors: list[InteractionActor] = Field(default_factory=list)
    role_assignments: dict[str, dict[str, str]] = Field(default_factory=dict)
    turn_owner: str = "unknown"
    session_mode: str = "bounded"
    continuation_policy: str = "finish_after_verified_effect"
    target: str = ""
    region_hint: str = ""
    action_modalities: list[str] = Field(default_factory=list)
    # One explicit modality may constrain the semantic plan.  Unknown or
    # multi-modal tasks retain ``infer_from_scene`` so complex workflows are
    # not accidentally reduced to one primitive.
    primary_action_modality: str = "infer_from_scene"
    user_started: bool = False
    expected_external_change: bool = False
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    signals: list[str] = Field(default_factory=list)
    ambiguous_fields: list[str] = Field(default_factory=list)
    provenance: dict[str, str] = Field(default_factory=dict)


class InteractionSessionContract(BaseModel):
    """Execution semantics for one activation of a live UI session.

    A turn is semantic and may contain a controller transaction with multiple
    pointer events.  ``max_agent_turns_per_activation`` therefore does not
    reintroduce the old one-click-per-model-call restriction.
    """

    session_id: str
    mode: str
    lifecycle: str = "persistent"
    actors: list[InteractionActor] = Field(default_factory=list)
    role_assignments: dict[str, dict[str, str]] = Field(default_factory=dict)
    initial_turn_owner: str = "unknown"
    target: str = "active_window"
    authoritative_state_ref: str = "interaction_scene"
    surface_region_ref: str = "surface_region"
    interaction_region_ref: str = "interaction_region"
    primary_action_modality: str = "infer_from_scene"
    continuation_policy: str = "wait_for_external_change"
    # Compatibility name retained in the serialized contract.  Runtime treats
    # this as the maximum number of consecutive agent turns between two proven
    # external changes; reaching it routes to WaitForExternalChange instead of
    # terminating the persistent API run.
    max_agent_turns_per_activation: int = Field(default=1, ge=1, le=8)
    wait_for_external_change: bool = True
    # The frontend/API budget that this contract was compiled from.  Keeping
    # the source budget in the serialized contract makes runtime limits
    # auditable instead of leaving several unrelated magic constants behind.
    requested_max_steps: int = Field(default=40, ge=1, le=400)
    # These budgets are part of the executable contract, not UI hints.  The
    # interaction runtime consumes them before another semantic model call,
    # replan, or mutating turn can be attempted.
    max_semantic_turns: int = Field(default=20, ge=1, le=200)
    max_replans: int = Field(default=8, ge=0, le=100)
    max_model_calls: int = Field(default=40, ge=2, le=400)
    external_change_timeout_s: float = Field(default=300.0, ge=5.0, le=1800.0)


class TaskIntent(BaseModel):
    original_text: str
    operation: list[str] = Field(default_factory=list)
    domain: Literal[
        "desktop", "communication", "workspace", "web_research",
        "data", "automation", "general",
    ] = "general"
    resource_types: list[str] = Field(default_factory=list)
    media_type: str = "none"
    channel: str = ""
    recipient: str = ""
    source: str = ""
    applications: list[str] = Field(default_factory=list)
    capture_scope: str = "window"
    destination: str = ""
    send_mode: Literal[
        "send", "draft", "share", "bounded_chat", "continuous_chat",
    ] = "send"
    subject: str = ""
    quality: str = "normal"
    message_text: str = ""
    message_content_source: Literal["none", "explicit", "generate"] = "none"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    ambiguous_fields: list[str] = Field(default_factory=list)
    provenance: dict[str, str] = Field(default_factory=dict)
    interaction: InteractionIntent = Field(default_factory=InteractionIntent)


class TaskRequirement(BaseModel):
    """One user-visible requirement retained from the original request.

    Requirements are deliberately separate from stages.  A planner may choose
    one semantic desktop stage or several typed stages, but it may not silently
    discard a requirement simply because the structural parser only
    understood the first verb in the utterance.
    """

    id: str
    text: str
    kind: str = "semantic_goal"
    stage_ids: list[str] = Field(default_factory=list)
    completion_predicates: list[str] = Field(default_factory=list)
    required: bool = True
    source_span: list[int] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class RequirementCoverage(BaseModel):
    """Compile-time proof that every required clause has an execution path."""

    complete: bool = False
    strategy: str = "unverified"
    covered_requirement_ids: list[str] = Field(default_factory=list)
    uncovered_requirement_ids: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class StateBinding(BaseModel):
    """Typed ephemeral state passed between stages, never a user artifact."""

    name: str
    state_type: str
    source: str = ""
    scope: str = ""
    required: bool = True
    ephemeral: bool = True


class ArtifactBinding(BaseModel):
    name: str
    media_type: str = "application/octet-stream"
    source: str = ""
    artifact_id: str = ""
    required: bool = True


class EvidenceProviderSpec(BaseModel):
    id: str
    capabilities: list[str] = Field(default_factory=list)
    description: str = ""


class CompletionObligation(BaseModel):
    id: str
    description: str
    stage_id: str
    evidence_providers: list[EvidenceProviderSpec]
    # ``id`` is the stable contract condition identifier.  ``predicate`` is
    # the exact run-evidence claim that may discharge it.  They currently
    # match for compiler-produced contracts, but keeping them separate avoids
    # accidentally treating an arbitrary record from an allowed provider as
    # proof.  The empty default preserves older serialized task-contract/1
    # payloads; verifiers interpret it as ``id``.
    predicate: str = ""
    artifact_ref: str = ""
    recipient: str = ""

    @property
    def evidence_predicate(self) -> str:
        return str(self.predicate or self.id)

    @property
    def verifier_capabilities(self) -> set[str]:
        return {
            capability
            for provider in self.evidence_providers
            for capability in provider.capabilities
        }


class CapabilitySelection(BaseModel):
    required_capabilities: list[str] = Field(default_factory=list)
    verifier_requirements: list[str] = Field(default_factory=list)
    exposed_capabilities: list[str] = Field(default_factory=list)
    missing_capabilities: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    model_tools: list[str] = Field(default_factory=list)
    controller_tools: list[str] = Field(default_factory=list)
    controller: str = "stage_runtime"
    adapter: str = ""
    model_role: str = "none"
    satisfiable: bool = False
    expansions: list[str] = Field(default_factory=list)
    selected_evidence_providers: dict[str, str] = Field(default_factory=dict)


class TaskStage(BaseModel):
    id: str
    type: StageType
    name: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    depends_on: list[str] = Field(default_factory=list)
    inputs: list[ArtifactBinding] = Field(default_factory=list)
    outputs: list[ArtifactBinding] = Field(default_factory=list)
    state_inputs: list[StateBinding] = Field(default_factory=list)
    state_outputs: list[StateBinding] = Field(default_factory=list)
    required_capabilities: list[str] = Field(default_factory=list)
    obligations: list[CompletionObligation] = Field(default_factory=list)
    execution: CapabilitySelection | None = None
    allow_skill_replay: bool = False


class TaskContract(BaseModel):
    format: str = TASK_CONTRACT_FORMAT
    task_id: str
    task: str
    intent: TaskIntent
    stages: list[TaskStage]
    obligations: list[CompletionObligation]
    final_conditions: list[str]
    requirements: list[TaskRequirement] = Field(default_factory=list)
    requirement_coverage: RequirementCoverage = Field(
        default_factory=RequirementCoverage,
    )
    clarification_questions: list[str] = Field(default_factory=list)
    session_context: list[dict[str, Any]] = Field(default_factory=list)
    session: InteractionSessionContract | None = None
    satisfiable: bool = False
    missing_capabilities: list[str] = Field(default_factory=list)
    experience_policy: str = "allow"
    compiler_version: str = "typed-stage-1"


class ContractCompileRequest(BaseModel):
    task: str
    max_steps: int = Field(default=40, ge=1, le=400)
    cold_start: bool = False
    # The visible Electron cockpit is explicitly a GUI-agent surface.  This
    # product fact is more reliable than guessing execution media from verbs.
    require_gui: bool = False


class ContractCompileResponse(BaseModel):
    ok: bool = True
    needs_input: bool = False
    clarification_questions: list[str] = Field(default_factory=list)
    contract: TaskContract
    graph: dict[str, Any]
