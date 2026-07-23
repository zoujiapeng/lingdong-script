"""Identity-free, read-only inputs projected for the Visual Actor."""
from __future__ import annotations

import json
import re
from types import MappingProxyType
from typing import Annotated, Any, Literal, Mapping

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
    field_validator,
    model_validator,
)

from ..capabilities.contracts import MutationKind, ToolCatalog, ToolRoute
from ..capability_names import InvalidCapability, canonical_capabilities
from ..capabilities.state import AuthoritativeFrameState, BrokerProjection, ExecutionRoute
from ..mission.contracts import MissionCard
from ..targeting.contracts import (
    ACTIVATE_TARGET_TOOL_NAME,
    MAX_TARGET_CANDIDATES,
    activate_target_tool_schema,
)
from ..targeting.lease import (
    TargetSelectionLease,
    TargetSelectionLeaseError,
    TargetSelectionScope,
)
from .proposals import _freeze, _json_value, _reject_overreach, _thaw, _unique_text


ACTOR_MISSION_VIEW_FORMAT = "actor-mission-view/2"
ACTOR_FRAME_VIEW_FORMAT = "actor-frame-view/0"
ACTOR_TOOL_VIEW_FORMAT = "actor-tool-view/1"
ACTOR_TURN_INPUT_FORMAT = "actor-turn-input/4"
ACTOR_PREVIOUS_ACTION_FORMAT = "actor-previous-action/0"
MAX_ACTOR_PREVIOUS_INTENT_CHARS = 240
MAX_ACTOR_PREVIOUS_ARGUMENT_CHARS = 4_096
_TOOL = re.compile(r"[a-z][a-z0-9_]{0,63}")
_NO_MUTATION_OUTCOME_TOOLS = frozenset({
    "done", "fail", "complete", "complete_task", "complete_subgoal",
    "run_action_batch", "execute_action_batch", "revise_plan",
})


class InvalidActorView(ValueError):
    pass


class _View(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ActorObligationView(_View):
    desired_state: str
    acceptance_criteria: tuple[str, ...]
    forbidden_effects: tuple[str, ...]
    required_capabilities: tuple[str, ...]

    @field_validator("desired_state")
    @classmethod
    def _desired_state(cls, value: Any) -> str:
        if type(value) is not str or not value.strip():
            raise ValueError("desired_state must be non-empty")
        return value

    @field_validator("acceptance_criteria", "forbidden_effects", mode="before")
    @classmethod
    def _semantic_lists(cls, value: Any, info: Any) -> tuple[str, ...]:
        return _unique_text(value, info.field_name)

    @field_validator("required_capabilities", mode="before")
    @classmethod
    def _capabilities(cls, value: Any) -> tuple[str, ...]:
        try:
            return canonical_capabilities(value)
        except InvalidCapability as exc:
            raise ValueError(str(exc)) from exc


class ActorArtifactView(_View):
    media_type: str

    @field_validator("media_type")
    @classmethod
    def _media_type(cls, value: Any) -> str:
        if type(value) is not str or not value.strip():
            raise ValueError("media_type must be non-empty")
        return value.strip().lower()


class ActorTransitionView(_View):
    action: str
    input_completed: Annotated[bool, Field(strict=True)]
    visible_effect: str
    local_visual_change: Literal["changed", "unchanged", "unknown"] | None = None
    frame_comparable: Annotated[bool, Field(strict=True)] | None = None
    target_binding_valid: Annotated[bool, Field(strict=True)] | None = None

    @field_validator("action")
    @classmethod
    def _action(cls, value: Any) -> str:
        if type(value) is not str or not _TOOL.fullmatch(value):
            raise ValueError("transition action must be a canonical tool name")
        return value


class ActorPreviousActionView(ActorTransitionView):
    """One identity-free, non-authoritative action view for the next turn."""

    format: Literal["actor-previous-action/0"]
    local_intent: str
    arguments: Mapping[str, Any]
    post_action_frame_reconciled: Annotated[bool, Field(strict=True)]

    @field_validator("local_intent")
    @classmethod
    def _local_intent(cls, value: Any) -> str:
        if type(value) is not str or not value.strip():
            raise ValueError("previous action local intent must be non-empty")
        normalized = value.strip()
        if len(normalized) > MAX_ACTOR_PREVIOUS_INTENT_CHARS:
            raise ValueError("previous action local intent is too long")
        return normalized

    @field_validator("arguments", mode="before")
    @classmethod
    def _arguments(cls, value: Any) -> Mapping[str, Any]:
        normalized = _json_value(value, "previous_action.arguments")
        if not isinstance(normalized, dict):
            raise ValueError("previous action arguments must be an object")
        _reject_overreach(normalized, "previous_action.arguments")
        encoded = json.dumps(
            normalized,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        if len(encoded) > MAX_ACTOR_PREVIOUS_ARGUMENT_CHARS:
            raise ValueError("previous action arguments exceed the bounded context")
        return normalized

    @field_validator("arguments", mode="after")
    @classmethod
    def _freeze_arguments(cls, value: Mapping[str, Any]) -> Mapping[str, Any]:
        return _freeze(dict(value))

    @field_serializer("arguments")
    def _serialize_arguments(self, value: Mapping[str, Any]) -> dict[str, Any]:
        return _thaw(value)


class ActorFailureView(_View):
    kind: str
    message: str
    count: Annotated[int, Field(strict=True, ge=1)]


class ActorMissionView(_View):
    format: Literal["actor-mission-view/2"]
    original_goal: str
    active_obligation: ActorObligationView
    verified_facts: tuple[str, ...]
    artifacts: tuple[ActorArtifactView, ...]
    target_title: str | None
    last_transition: ActorTransitionView | None
    failure_summary: ActorFailureView | None
    unchanged_frame_streak: Annotated[int, Field(strict=True, ge=0)] = 0

    @field_validator("original_goal")
    @classmethod
    def _original_goal(cls, value: Any) -> str:
        if type(value) is not str or not value.strip():
            raise ValueError("original_goal must be non-empty")
        return value

    @field_validator("verified_facts", mode="before")
    @classmethod
    def _facts(cls, value: Any) -> tuple[str, ...]:
        return _unique_text(value, "verified_facts")

    def to_json(self) -> str:
        return _canonical_json(self)


class ActorFrameView(_View):
    format: Literal["actor-frame-view/0"]
    image_available: Annotated[bool, Field(strict=True)]
    authoritative: Annotated[bool, Field(strict=True)]
    fresh: Annotated[bool, Field(strict=True)]
    coordinate_space: Literal["submitted_image_pixels"]

    def to_json(self) -> str:
        return _canonical_json(self)


class ActorToolView(_View):
    format: Literal["actor-tool-view/1"]
    name: str
    input_schema: Mapping[str, Any] = Field(alias="schema")
    requires_local_intent: Annotated[bool, Field(strict=True)]

    @field_validator("name")
    @classmethod
    def _name(cls, value: Any) -> str:
        if type(value) is not str or not _TOOL.fullmatch(value):
            raise ValueError("tool name is invalid")
        if value in _NO_MUTATION_OUTCOME_TOOLS or "batch" in value:
            raise ValueError("Actor tool cannot complete/fail a mission or batch actions")
        return value

    @field_validator("input_schema", mode="before")
    @classmethod
    def _schema(cls, value: Any) -> Mapping[str, Any]:
        normalized = _json_value(value, "tool.schema")
        if not isinstance(normalized, dict) or normalized.get("type") != "object":
            raise ValueError("tool schema must be an object JSON schema")
        _reject_overreach(normalized, "tool.schema")
        return normalized

    @field_validator("input_schema", mode="after")
    @classmethod
    def _freeze_schema(cls, value: Mapping[str, Any]) -> Mapping[str, Any]:
        return _freeze(dict(value))

    @field_serializer("input_schema")
    def _serialize_schema(self, value: Mapping[str, Any]) -> dict[str, Any]:
        return _thaw(value)

    def to_json(self) -> str:
        return _canonical_json(self)


class ActorTargetChoiceView(_View):
    """Semantic candidate facts; ordinal is meaningful only for this turn."""

    ordinal: Annotated[int, Field(strict=True, ge=1, le=MAX_TARGET_CANDIDATES)]
    label: str
    state: Literal["visible", "recoverable", "installed"]
    window_title: str | None

    @field_validator("label")
    @classmethod
    def _label(cls, value: Any) -> str:
        if type(value) is not str or not value.strip() or len(value.strip()) > 512:
            raise ValueError("target choice label must be bounded non-empty text")
        return value.strip()

    @field_validator("window_title")
    @classmethod
    def _window_title(cls, value: Any) -> str | None:
        if value is None:
            return None
        if type(value) is not str or len(value.strip()) > 512:
            raise ValueError("target choice window_title must be bounded text or null")
        return value.strip() or None


class ActorTurnInput(_View):
    """Complete model-visible turn input; the image bytes travel separately."""

    format: Literal["actor-turn-input/4"]
    mission: ActorMissionView
    frame: ActorFrameView
    tools: tuple[ActorToolView, ...]
    target_choices: tuple[ActorTargetChoiceView, ...] = ()
    previous_action: ActorPreviousActionView | None = None

    @field_validator("tools")
    @classmethod
    def _tools(cls, value: tuple[ActorToolView, ...]) -> tuple[ActorToolView, ...]:
        if not value:
            raise ValueError("ActorTurnInput requires at least one Broker-projected tool")
        if len({tool.name for tool in value}) != len(value):
            raise ValueError("Actor tools must be unique")
        return value

    @model_validator(mode="after")
    def _target_choices_match_surface(self) -> "ActorTurnInput":
        ordinals = tuple(item.ordinal for item in self.target_choices)
        if ordinals != tuple(range(1, len(ordinals) + 1)):
            raise ValueError("target choice ordinals must be contiguous and one-based")
        activation_tools = tuple(
            tool for tool in self.tools
            if tool.name == ACTIVATE_TARGET_TOOL_NAME
        )
        has_activation_tool = bool(activation_tools)
        if has_activation_tool != bool(self.target_choices):
            raise ValueError(
                "activate_target and target_choices must be projected together"
            )
        if activation_tools:
            tool = activation_tools[0]
            if (
                tool.requires_local_intent is not True
                or _thaw(tool.input_schema) != activate_target_tool_schema()
            ):
                raise ValueError("activate_target must use the canonical ordinal-only schema")
        return self

    def to_json(self) -> str:
        return _canonical_json(self)

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "ActorTurnInput":
        try:
            return cls.model_validate(raw)
        except Exception as exc:
            raise InvalidActorView(str(exc)) from exc


def _canonical_json(value: BaseModel) -> str:
    return json.dumps(
        value.model_dump(mode="json", by_alias=True),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def project_actor_mission_view(card: MissionCard) -> ActorMissionView:
    if not isinstance(card, MissionCard) or card.active_obligation is None:
        raise InvalidActorView("Visual Actor requires one active obligation")
    active = card.active_obligation
    transition = card.last_transition
    failure = card.failure_summary
    return ActorMissionView(
        format=ACTOR_MISSION_VIEW_FORMAT,
        original_goal=card.original_goal,
        active_obligation=ActorObligationView(
            desired_state=active.desired_state,
            acceptance_criteria=active.acceptance_criteria,
            forbidden_effects=active.forbidden_effects,
            required_capabilities=active.required_capabilities,
        ),
        verified_facts=tuple(fact.statement for fact in card.verified_facts),
        artifacts=tuple(ActorArtifactView(media_type=item.media_type) for item in card.artifact_bindings),
        target_title=(card.target_binding.title_snapshot or None) if card.target_binding else None,
        last_transition=(
            ActorTransitionView(
                action=transition.action,
                input_completed=transition.input_completed,
                visible_effect=transition.visible_effect,
                local_visual_change=(
                    transition.action_effect.local_visual_change.value
                    if transition.action_effect is not None
                    else None
                ),
                frame_comparable=(
                    transition.action_effect.frame_comparable
                    if transition.action_effect is not None
                    else None
                ),
                target_binding_valid=(
                    transition.action_effect.target_binding_valid
                    if transition.action_effect is not None
                    else None
                ),
            ) if transition else None
        ),
        failure_summary=(
            ActorFailureView(
                kind=failure.kind, message=failure.message, count=failure.count
            ) if failure else None
        ),
        unchanged_frame_streak=card.no_progress_streak,
    )


def project_actor_frame_view(frame: AuthoritativeFrameState) -> ActorFrameView:
    if not isinstance(frame, AuthoritativeFrameState):
        raise InvalidActorView("frame must be AuthoritativeFrameState")
    return ActorFrameView(
        format=ACTOR_FRAME_VIEW_FORMAT,
        image_available=frame.frame_id is not None,
        authoritative=frame.authoritative,
        fresh=frame.fresh,
        coordinate_space="submitted_image_pixels",
    )


def project_actor_tool_views(
    catalog: ToolCatalog, projection: BrokerProjection,
) -> tuple[ActorToolView, ...]:
    if not isinstance(catalog, ToolCatalog) or not isinstance(projection, BrokerProjection):
        raise InvalidActorView("tool projection requires canonical ToolCatalog/BrokerProjection")
    if projection.execution_route is not ExecutionRoute.VISUAL_ACTOR:
        raise InvalidActorView("non-Actor Broker route cannot produce Actor tools")
    tools = []
    for name in projection.tool_names:
        try:
            spec = catalog.get(name)
        except KeyError as exc:
            raise InvalidActorView(f"Broker projected unknown tool {name!r}") from exc
        if spec.route is not ToolRoute.VISUAL_ACTOR:
            raise InvalidActorView(f"tool {name!r} is not owned by Visual Actor route")
        tools.append(ActorToolView(
            format=ACTOR_TOOL_VIEW_FORMAT,
            name=name,
            schema=spec.schema,
            requires_local_intent=(spec.mutation_kind is MutationKind.GUI),
        ))
    return tuple(tools)


def project_actor_target_choices(
    lease: TargetSelectionLease | None,
) -> tuple[ActorTargetChoiceView, ...]:
    if lease is None:
        return ()
    if not isinstance(lease, TargetSelectionLease):
        raise InvalidActorView("target choices require one TargetSelectionLease")
    try:
        facts = lease.choice_facts()
        return tuple(ActorTargetChoiceView(**{
            "ordinal": item.ordinal,
            "label": item.label,
            "state": item.state,
            "window_title": item.window_title,
        }) for item in facts)
    except TargetSelectionLeaseError as exc:
        raise InvalidActorView(str(exc)) from exc


def project_actor_turn_input(
    card: MissionCard,
    frame: AuthoritativeFrameState,
    catalog: ToolCatalog,
    projection: BrokerProjection,
    *,
    previous_action: ActorPreviousActionView | None = None,
    target_selection_lease: TargetSelectionLease | None = None,
    current_target_selection_scope: TargetSelectionScope | None = None,
) -> ActorTurnInput:
    if card.active_obligation_id != projection.obligation_id:
        raise InvalidActorView("MissionCard and BrokerProjection obligation mismatch")
    if card.mission_revision != projection.mission_revision:
        raise InvalidActorView("MissionCard and BrokerProjection revision mismatch")
    binding = card.target_binding
    if (binding.binding_id if binding else None) != projection.target_binding_id:
        raise InvalidActorView("MissionCard and BrokerProjection target mismatch")
    if not frame.authoritative or not frame.fresh or frame.frame_id is None:
        raise InvalidActorView("Visual Actor requires a fresh authoritative frame")
    if binding and (frame.hwnd != binding.hwnd or frame.epoch != binding.epoch):
        raise InvalidActorView("authoritative frame does not belong to the bound target")
    if (target_selection_lease is None) != (current_target_selection_scope is None):
        raise InvalidActorView(
            "target selection lease and current scope must be projected together"
        )
    if binding is not None and target_selection_lease is not None:
        raise InvalidActorView("bound target cannot project acquisition candidates")
    if target_selection_lease is not None:
        scope = current_target_selection_scope
        if not isinstance(scope, TargetSelectionScope):
            raise InvalidActorView("current target selection scope is invalid")
        if target_selection_lease.scope != scope:
            raise InvalidActorView("target selection lease scope is stale")
        if (
            scope.mission_id != card.mission_id
            or scope.mission_revision != card.mission_revision
            or scope.obligation_id != card.active_obligation_id
            or scope.broker_projection_id != projection.projection_id
        ):
            raise InvalidActorView("target selection lease does not match the current turn")
        if (
            scope.frame.scope != "full_desktop"
            or scope.frame.frame_id != frame.frame_id
            or scope.frame.hwnd != frame.hwnd
            or scope.frame.epoch != frame.epoch
        ):
            raise InvalidActorView(
                "target selection lease requires the exact full-desktop frame"
            )
    mission_view = project_actor_mission_view(card)
    frame_view = project_actor_frame_view(frame)
    tool_views = project_actor_tool_views(catalog, projection)
    choice_views = project_actor_target_choices(target_selection_lease)
    try:
        return ActorTurnInput(
            format=ACTOR_TURN_INPUT_FORMAT,
            mission=mission_view,
            frame=frame_view,
            tools=tool_views,
            target_choices=choice_views,
            previous_action=previous_action,
        )
    except InvalidActorView:
        raise
    except Exception as exc:
        raise InvalidActorView(str(exc)) from exc


__all__ = [
    "ACTOR_MISSION_VIEW_FORMAT", "ACTOR_FRAME_VIEW_FORMAT", "ACTOR_TOOL_VIEW_FORMAT",
    "ACTOR_TURN_INPUT_FORMAT", "ACTOR_PREVIOUS_ACTION_FORMAT",
    "MAX_ACTOR_PREVIOUS_INTENT_CHARS", "MAX_ACTOR_PREVIOUS_ARGUMENT_CHARS",
    "InvalidActorView", "ActorObligationView",
    "ActorArtifactView", "ActorTransitionView", "ActorFailureView", "ActorMissionView",
    "ActorPreviousActionView", "ActorFrameView", "ActorToolView",
    "ActorTargetChoiceView", "ActorTurnInput",
    "project_actor_mission_view",
    "project_actor_frame_view", "project_actor_tool_views",
    "project_actor_target_choices", "project_actor_turn_input",
]
