"""Identity-free, read-only inputs projected for the Visual Actor."""
from __future__ import annotations

import json
import re
from types import MappingProxyType
from typing import Annotated, Any, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator

from ..capabilities.contracts import ToolCatalog, ToolRoute
from ..capability_names import InvalidCapability, canonical_capabilities
from ..capabilities.state import AuthoritativeFrameState, BrokerProjection, ExecutionRoute
from ..mission.contracts import MissionCard
from .proposals import _freeze, _json_value, _reject_overreach, _thaw, _unique_text


ACTOR_MISSION_VIEW_FORMAT = "actor-mission-view/0"
ACTOR_FRAME_VIEW_FORMAT = "actor-frame-view/0"
ACTOR_TOOL_VIEW_FORMAT = "actor-tool-view/0"
ACTOR_TURN_INPUT_FORMAT = "actor-turn-input/0"
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


class ActorFailureView(_View):
    kind: str
    message: str
    count: Annotated[int, Field(strict=True, ge=1)]


class ActorMissionView(_View):
    format: Literal["actor-mission-view/0"]
    original_goal: str
    active_obligation: ActorObligationView
    verified_facts: tuple[str, ...]
    artifacts: tuple[ActorArtifactView, ...]
    target_title: str | None
    last_transition: ActorTransitionView | None
    failure_summary: ActorFailureView | None

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
    format: Literal["actor-tool-view/0"]
    name: str
    input_schema: Mapping[str, Any] = Field(alias="schema")

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


class ActorTurnInput(_View):
    """Complete model-visible turn input; the image bytes travel separately."""

    format: Literal["actor-turn-input/0"]
    mission: ActorMissionView
    frame: ActorFrameView
    tools: tuple[ActorToolView, ...]

    @field_validator("tools")
    @classmethod
    def _tools(cls, value: tuple[ActorToolView, ...]) -> tuple[ActorToolView, ...]:
        if not value:
            raise ValueError("ActorTurnInput requires at least one Broker-projected tool")
        if len({tool.name for tool in value}) != len(value):
            raise ValueError("Actor tools must be unique")
        return value

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
            ) if transition else None
        ),
        failure_summary=(
            ActorFailureView(
                kind=failure.kind, message=failure.message, count=failure.count
            ) if failure else None
        ),
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
        tools.append(ActorToolView(format=ACTOR_TOOL_VIEW_FORMAT, name=name, schema=spec.schema))
    return tuple(tools)


def project_actor_turn_input(
    card: MissionCard,
    frame: AuthoritativeFrameState,
    catalog: ToolCatalog,
    projection: BrokerProjection,
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
    return ActorTurnInput(
        format=ACTOR_TURN_INPUT_FORMAT,
        mission=project_actor_mission_view(card),
        frame=project_actor_frame_view(frame),
        tools=project_actor_tool_views(catalog, projection),
    )


__all__ = [
    "ACTOR_MISSION_VIEW_FORMAT", "ACTOR_FRAME_VIEW_FORMAT", "ACTOR_TOOL_VIEW_FORMAT",
    "ACTOR_TURN_INPUT_FORMAT", "InvalidActorView", "ActorObligationView",
    "ActorArtifactView", "ActorTransitionView", "ActorFailureView", "ActorMissionView",
    "ActorFrameView", "ActorToolView", "ActorTurnInput", "project_actor_mission_view",
    "project_actor_frame_view", "project_actor_tool_views", "project_actor_turn_input",
]
