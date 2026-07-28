"""Durable, canonical journal for one vNext Runtime evidence scope.

The journal is deliberately mechanical.  It authenticates the bytes, order,
and run scope of evidence events, while higher-level evidence-plane code owns
semantic ordering and cross-record validation.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import re
import threading
from types import MappingProxyType
from typing import Any, BinaryIO, Iterable, Mapping

from ..json_contract import (
    BoundedJSONError,
    MAX_SIGNED_64,
    bounded_json_copy,
    freeze_bounded_json,
)


JOURNAL_FILENAME = "evidence.jsonl"
JOURNAL_EVENT_FORMAT = "runtime-evidence-journal-event/1"
JOURNAL_SCHEMA_VERSION = 1
GENESIS_EVENT_SHA256 = "0" * 64

# The tuple documents the evidence chain's vocabulary.  Ordering constraints
# between these events belong to the evidence plane, not this persistence
# primitive.
ALLOWED_EVENT_KINDS = (
    "actor_decision",
    "admission",
    "physical_attempt",
    "raw_receipt",
    "controller_transition",
    "frame_artifact",
)

_EVENT_FIELDS = (
    "format",
    "schema_version",
    "sequence",
    "previous_event_sha256",
    "event_kind",
    "mission_id",
    "run_id",
    "decision_id",
    "action_id",
    "payload",
    "event_sha256",
)
_HASHED_EVENT_FIELDS = _EVENT_FIELDS[:-1]
_IDENTITY = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}")
_SHA256 = re.compile(r"[0-9a-f]{64}")


class RuntimeEvidenceJournalError(RuntimeError):
    """The evidence journal could not satisfy its durable contract."""


class RuntimeEvidenceJournalValidationError(RuntimeEvidenceJournalError):
    """A persisted journal is not a canonical, valid evidence stream."""


class RuntimeEvidenceJournalPoisoned(RuntimeEvidenceJournalError):
    """A write failed, so the journal can never accept another append."""


def _identity(value: Any, name: str) -> str:
    if type(value) is not str or _IDENTITY.fullmatch(value) is None:
        raise RuntimeEvidenceJournalValidationError(
            f"{name} is not a canonical identity"
        )
    return value


def _optional_identity(value: Any, name: str) -> str | None:
    if value is None:
        return None
    return _identity(value, name)


def _sha256(value: Any, name: str) -> str:
    if type(value) is not str or _SHA256.fullmatch(value) is None:
        raise RuntimeEvidenceJournalValidationError(
            f"{name} is not a lowercase SHA-256 digest"
        )
    return value


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if type(value) is tuple:
        return [_thaw(item) for item in value]
    return value


def _bounded_object(value: Any, name: str) -> Mapping[str, Any]:
    if type(value) is not dict:
        raise RuntimeEvidenceJournalValidationError(
            f"{name} must be a plain JSON object"
        )
    try:
        frozen = freeze_bounded_json(value)
    except (BoundedJSONError, RecursionError):
        raise RuntimeEvidenceJournalValidationError(
            f"{name} violates the bounded JSON contract"
        ) from None
    if type(frozen) is not MappingProxyType:
        raise RuntimeEvidenceJournalValidationError(
            f"{name} must be a plain JSON object"
        )
    return frozen


def _canonical_bytes(value: Any) -> bytes:
    try:
        copied = bounded_json_copy(_thaw(value))
        text = json.dumps(
            copied,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        return text.encode("utf-8")
    except (
        BoundedJSONError,
        RecursionError,
        UnicodeEncodeError,
        ValueError,
    ):
        raise RuntimeEvidenceJournalValidationError(
            "journal event violates the canonical JSON contract"
        ) from None


def _event_sha256(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _reject_constant(_: str) -> None:
    raise RuntimeEvidenceJournalValidationError(
        "journal JSON contains a non-finite number"
    )


def _strict_object(pairs: Iterable[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise RuntimeEvidenceJournalValidationError(
                "journal JSON contains duplicate object keys"
            )
        result[key] = value
    return result


def _decode_json_line(line: bytes) -> dict[str, Any]:
    try:
        text = line.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise RuntimeEvidenceJournalValidationError(
            "journal JSON is not strict UTF-8"
        ) from None
    try:
        decoded = json.loads(
            text,
            object_pairs_hook=_strict_object,
            parse_constant=_reject_constant,
        )
    except RuntimeEvidenceJournalValidationError:
        raise
    except (json.JSONDecodeError, RecursionError, ValueError):
        raise RuntimeEvidenceJournalValidationError(
            "journal JSON is malformed"
        ) from None
    if type(decoded) is not dict:
        raise RuntimeEvidenceJournalValidationError(
            "journal event must be a JSON object"
        )
    return decoded


@dataclass(frozen=True, slots=True, repr=False)
class RuntimeEvidenceJournalEvent:
    """One immutable, content-addressed journal event."""

    format: str
    schema_version: int
    sequence: int
    previous_event_sha256: str
    event_kind: str
    mission_id: str
    run_id: str
    decision_id: str | None
    action_id: str | None
    payload: Mapping[str, Any] = field(repr=False)
    event_sha256: str

    def __post_init__(self) -> None:
        if self.format != JOURNAL_EVENT_FORMAT:
            raise RuntimeEvidenceJournalValidationError(
                "journal event format is unsupported"
            )
        if (
            type(self.schema_version) is not int
            or self.schema_version != JOURNAL_SCHEMA_VERSION
        ):
            raise RuntimeEvidenceJournalValidationError(
                "journal schema version is unsupported"
            )
        if (
            type(self.sequence) is not int
            or not 0 <= self.sequence <= MAX_SIGNED_64
        ):
            raise RuntimeEvidenceJournalValidationError(
                "journal sequence must be a non-negative signed-64 integer"
            )
        object.__setattr__(
            self,
            "previous_event_sha256",
            _sha256(self.previous_event_sha256, "previous_event_sha256"),
        )
        if (
            type(self.event_kind) is not str
            or self.event_kind not in ALLOWED_EVENT_KINDS
        ):
            raise RuntimeEvidenceJournalValidationError(
                "journal event kind is unsupported"
            )
        object.__setattr__(self, "mission_id", _identity(self.mission_id, "mission_id"))
        object.__setattr__(self, "run_id", _identity(self.run_id, "run_id"))
        object.__setattr__(
            self,
            "decision_id",
            _optional_identity(self.decision_id, "decision_id"),
        )
        object.__setattr__(
            self,
            "action_id",
            _optional_identity(self.action_id, "action_id"),
        )
        object.__setattr__(self, "payload", _bounded_object(self.payload, "payload"))
        object.__setattr__(
            self,
            "event_sha256",
            _sha256(self.event_sha256, "event_sha256"),
        )
        if self.event_sha256 != _event_sha256(self.hash_material()):
            raise RuntimeEvidenceJournalValidationError(
                "journal event hash does not match its canonical content"
            )

    @classmethod
    def build(
        cls,
        *,
        sequence: int,
        previous_event_sha256: str,
        event_kind: str,
        mission_id: str,
        run_id: str,
        decision_id: str | None,
        action_id: str | None,
        payload: dict[str, Any],
    ) -> RuntimeEvidenceJournalEvent:
        material = {
            "format": JOURNAL_EVENT_FORMAT,
            "schema_version": JOURNAL_SCHEMA_VERSION,
            "sequence": sequence,
            "previous_event_sha256": previous_event_sha256,
            "event_kind": event_kind,
            "mission_id": mission_id,
            "run_id": run_id,
            "decision_id": decision_id,
            "action_id": action_id,
            "payload": payload,
        }
        return cls(event_sha256=_event_sha256(material), **material)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> RuntimeEvidenceJournalEvent:
        try:
            copied = bounded_json_copy(value)
        except (BoundedJSONError, RecursionError):
            raise RuntimeEvidenceJournalValidationError(
                "journal event violates the bounded JSON contract"
            ) from None
        expected = set(_EVENT_FIELDS)
        actual = set(copied)
        if actual != expected:
            raise RuntimeEvidenceJournalValidationError(
                "journal event fields do not match the schema"
            )
        return cls(**{name: copied[name] for name in _EVENT_FIELDS})

    def hash_material(self) -> dict[str, Any]:
        value = self.to_dict()
        return {name: value[name] for name in _HASHED_EVENT_FIELDS}

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": self.format,
            "schema_version": self.schema_version,
            "sequence": self.sequence,
            "previous_event_sha256": self.previous_event_sha256,
            "event_kind": self.event_kind,
            "mission_id": self.mission_id,
            "run_id": self.run_id,
            "decision_id": self.decision_id,
            "action_id": self.action_id,
            "payload": _thaw(self.payload),
            "event_sha256": self.event_sha256,
        }

    def __repr__(self) -> str:
        return (
            "<RuntimeEvidenceJournalEvent "
            f"sequence={self.sequence} event_kind={self.event_kind!r}>"
        )


class RuntimeEvidenceJournal:
    """Exclusive writer or strict read-only replay of one evidence journal."""

    def __init__(
        self,
        *,
        run_directory: Path,
        mission_id: str,
        run_id: str,
        events: tuple[RuntimeEvidenceJournalEvent, ...],
        stream: BinaryIO | None,
    ) -> None:
        self._run_directory = run_directory
        self._mission_id = _identity(mission_id, "mission_id")
        self._run_id = _identity(run_id, "run_id")
        self._events = list(events)
        self._stream = stream
        self._poisoned = False
        self._lock = threading.RLock()

    @classmethod
    def create(
        cls,
        run_directory: os.PathLike[str] | str,
        mission_id: str,
        run_id: str,
    ) -> RuntimeEvidenceJournal:
        """Exclusively create an empty writable journal for one run."""

        checked_mission_id = _identity(mission_id, "mission_id")
        checked_run_id = _identity(run_id, "run_id")
        path = _normalized_path(run_directory)
        try:
            os.mkdir(path)
        except OSError:
            raise RuntimeEvidenceJournalError(
                "evidence run directory could not be exclusively created"
            ) from None

        journal_path = path / JOURNAL_FILENAME
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_APPEND
        if hasattr(os, "O_BINARY"):
            flags |= os.O_BINARY
        try:
            descriptor = os.open(journal_path, flags, 0o600)
        except OSError:
            raise RuntimeEvidenceJournalError(
                "evidence journal could not be exclusively created"
            ) from None

        try:
            stream = os.fdopen(descriptor, "wb", buffering=0)
        except BaseException:
            try:
                os.close(descriptor)
            except OSError:
                pass
            raise RuntimeEvidenceJournalError(
                "evidence journal writer could not be initialized"
            ) from None
        return cls(
            run_directory=path,
            mission_id=checked_mission_id,
            run_id=checked_run_id,
            events=(),
            stream=stream,
        )

    @classmethod
    def open_read_only(
        cls,
        run_directory: os.PathLike[str] | str,
        mission_id: str,
        run_id: str,
    ) -> RuntimeEvidenceJournal:
        """Open and authenticate a complete journal without write authority."""

        checked_mission_id = _identity(mission_id, "mission_id")
        checked_run_id = _identity(run_id, "run_id")
        path = _normalized_path(run_directory)
        journal_path = path / JOURNAL_FILENAME
        try:
            data = journal_path.read_bytes()
        except OSError:
            raise RuntimeEvidenceJournalValidationError(
                "evidence journal could not be read"
            ) from None

        events = _replay(
            data,
            mission_id=checked_mission_id,
            run_id=checked_run_id,
        )
        return cls(
            run_directory=path,
            mission_id=checked_mission_id,
            run_id=checked_run_id,
            events=events,
            stream=None,
        )

    @classmethod
    def replay(
        cls,
        run_directory: os.PathLike[str] | str,
        mission_id: str,
        run_id: str,
    ) -> RuntimeEvidenceJournal:
        """Alias for the explicit read-only open operation."""

        return cls.open_read_only(run_directory, mission_id, run_id)

    @property
    def events(self) -> tuple[RuntimeEvidenceJournalEvent, ...]:
        with self._lock:
            return tuple(self._events)

    @property
    def head(self) -> str:
        with self._lock:
            if not self._events:
                return GENESIS_EVENT_SHA256
            return self._events[-1].event_sha256

    @property
    def event_count(self) -> int:
        with self._lock:
            return len(self._events)

    @property
    def run_directory(self) -> Path:
        return self._run_directory

    @property
    def mission_id(self) -> str:
        return self._mission_id

    @property
    def run_id(self) -> str:
        return self._run_id

    @property
    def writable(self) -> bool:
        with self._lock:
            return self._stream is not None and not self._poisoned

    def append(
        self,
        event_kind: str,
        payload: dict[str, Any],
        *,
        decision_id: str | None = None,
        action_id: str | None = None,
    ) -> RuntimeEvidenceJournalEvent:
        """Durably append one canonical event, or poison this writer forever."""

        with self._lock:
            if self._poisoned:
                raise RuntimeEvidenceJournalPoisoned(
                    "evidence journal is permanently poisoned"
                )
            if self._stream is None:
                raise RuntimeEvidenceJournalError(
                    "evidence journal is not writable"
                )

            event = RuntimeEvidenceJournalEvent.build(
                sequence=len(self._events),
                previous_event_sha256=(
                    self._events[-1].event_sha256
                    if self._events
                    else GENESIS_EVENT_SHA256
                ),
                event_kind=event_kind,
                mission_id=self._mission_id,
                run_id=self._run_id,
                decision_id=decision_id,
                action_id=action_id,
                payload=payload,
            )
            line = _canonical_bytes(event.to_dict()) + b"\n"

            try:
                _write_all(self._stream, line)
                self._stream.flush()
                os.fsync(self._stream.fileno())
                self._events.append(event)
            except BaseException:
                self._poison_locked()
                raise RuntimeEvidenceJournalPoisoned(
                    "evidence journal append failed and permanently poisoned it"
                ) from None

            return event

    def close(self) -> None:
        with self._lock:
            stream = self._stream
            self._stream = None
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass

    def _poison_locked(self) -> None:
        self._poisoned = True
        stream = self._stream
        self._stream = None
        if stream is not None:
            try:
                stream.close()
            except OSError:
                pass

    def __enter__(self) -> RuntimeEvidenceJournal:
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    def __repr__(self) -> str:
        return (
            "<RuntimeEvidenceJournal "
            f"writable={self.writable} event_count={self.event_count}>"
        )


def _normalized_path(value: os.PathLike[str] | str) -> Path:
    if type(value) is str:
        raw = value
    elif isinstance(value, os.PathLike):
        raw = os.fspath(value)
    else:
        raise RuntimeEvidenceJournalError(
            "run_directory must be a filesystem path"
        )
    if type(raw) is not str or not raw:
        raise RuntimeEvidenceJournalError(
            "run_directory must be a non-empty filesystem path"
        )
    try:
        return Path(raw).resolve(strict=False)
    except (OSError, RuntimeError):
        raise RuntimeEvidenceJournalError(
            "run_directory could not be normalized"
        ) from None


def _write_all(stream: BinaryIO, value: bytes) -> None:
    view = memoryview(value)
    offset = 0
    while offset < len(view):
        written = stream.write(view[offset:])
        if type(written) is not int or written <= 0:
            raise OSError("journal write made no progress")
        offset += written


def _replay(
    data: bytes,
    *,
    mission_id: str,
    run_id: str,
) -> tuple[RuntimeEvidenceJournalEvent, ...]:
    if not data:
        return ()
    if not data.endswith(b"\n"):
        raise RuntimeEvidenceJournalValidationError(
            "evidence journal is missing its trailing newline"
        )

    lines = data[:-1].split(b"\n")
    if any(not line for line in lines):
        raise RuntimeEvidenceJournalValidationError(
            "evidence journal contains an empty event line"
        )

    events: list[RuntimeEvidenceJournalEvent] = []
    previous = GENESIS_EVENT_SHA256
    for expected_sequence, line in enumerate(lines):
        decoded = _decode_json_line(line)
        event = RuntimeEvidenceJournalEvent.from_dict(decoded)
        if _canonical_bytes(event.to_dict()) != line:
            raise RuntimeEvidenceJournalValidationError(
                "journal event bytes are not canonical"
            )
        if event.sequence != expected_sequence:
            raise RuntimeEvidenceJournalValidationError(
                "journal event sequence is not contiguous"
            )
        if event.previous_event_sha256 != previous:
            raise RuntimeEvidenceJournalValidationError(
                "journal event hash chain is broken"
            )
        if event.mission_id != mission_id or event.run_id != run_id:
            raise RuntimeEvidenceJournalValidationError(
                "journal event scope does not match the requested run"
            )
        events.append(event)
        previous = event.event_sha256
    return tuple(events)


__all__ = [
    "ALLOWED_EVENT_KINDS",
    "GENESIS_EVENT_SHA256",
    "JOURNAL_EVENT_FORMAT",
    "JOURNAL_FILENAME",
    "JOURNAL_SCHEMA_VERSION",
    "RuntimeEvidenceJournal",
    "RuntimeEvidenceJournalError",
    "RuntimeEvidenceJournalEvent",
    "RuntimeEvidenceJournalPoisoned",
    "RuntimeEvidenceJournalValidationError",
]
