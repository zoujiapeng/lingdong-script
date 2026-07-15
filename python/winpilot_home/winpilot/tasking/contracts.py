"""Versioned contracts shared by compilation, runtime, tools, and verification."""
from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


TASK_CONTRACT_FORMAT = "task-contract/1"


class StageType(str, Enum):
    OPEN_APPLICATION = "OpenApplication"
    FOCUS_WINDOW = "FocusWindow"
    CREATE_OR_EDIT_ARTIFACT = "CreateOrEditArtifact"
    DRAW_ARTIFACT = "DrawArtifact"
    CAPTURE_ARTIFACT = "CaptureArtifact"
    SAVE_ARTIFACT = "SaveArtifact"
    OPEN_CONVERSATION = "OpenConversation"
    RESOLVE_RECIPIENT = "ResolveRecipient"
    SEND_TEXT = "SendText"
    SEND_ARTIFACT = "SendArtifact"
    VERIFY_ARTIFACT = "VerifyArtifact"
    VERIFY_DELIVERY = "VerifyDelivery"


class TaskIntent(BaseModel):
    original_text: str
    operation: list[str] = Field(default_factory=list)
    media_type: str = "none"
    channel: str = ""
    recipient: str = ""
    source: str = ""
    capture_scope: str = "window"
    destination: str = ""
    send_mode: str = "send"
    subject: str = ""
    quality: str = "normal"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    ambiguous_fields: list[str] = Field(default_factory=list)
    provenance: dict[str, str] = Field(default_factory=dict)


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
    artifact_ref: str = ""
    recipient: str = ""

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
    satisfiable: bool = False
    missing_capabilities: list[str] = Field(default_factory=list)
    experience_policy: str = "allow"
    compiler_version: str = "typed-stage-1"


class ContractCompileRequest(BaseModel):
    task: str
    max_steps: int = Field(default=40, ge=1, le=400)
    cold_start: bool = False


class ContractCompileResponse(BaseModel):
    contract: TaskContract
    graph: dict[str, Any]
