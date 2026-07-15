"""Shared types for the WinPilot memory graph."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

NODE_KINDS = {
    "raw_artifact",
    "chunk",
    "summary",
    "fact",
    "preference",
    "rule",
    "episode",
    "procedure",
    "skill",
    "warning",
    "observation",
    "session_ref",
}

EDGE_KINDS = {
    "derived_from",
    "supported_by",
    "verified_by",
    "contradicted_by",
    "supersedes",
    "requires",
    "used_in",
    "expanded_in",
    "warning_for",
}

ACTIVE_STATUSES = {"active", "provisional"}
ACTION_KINDS = {"procedure", "skill"}
FACTUAL_KINDS = {"fact", "preference", "rule"}
PACKABLE_KINDS = ACTION_KINDS | FACTUAL_KINDS | {"warning", "summary"}


@dataclass
class TaskFrame:
    task: str
    app: str = ""
    app_version: str = ""
    stage_type: str = ""
    parameters: dict[str, Any] = field(default_factory=dict)
    anchors: list[str] = field(default_factory=list)
    coding: bool = False
    volatile: bool = False
    words: list[str] = field(default_factory=list)
    bigrams: set[str] = field(default_factory=set)


@dataclass
class MemoryCandidate:
    node: dict[str, Any]
    score: float
    reasons: list[str] = field(default_factory=list)
    related: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class MemoryPack:
    text: str
    selected_ids: list[str]
    scores: dict[str, Any]
    candidates: list[MemoryCandidate] = field(default_factory=list)
