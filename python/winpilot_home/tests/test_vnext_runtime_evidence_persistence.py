"""Focused synthetic tests for durable vNext Runtime evidence."""
from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path, PurePosixPath
from typing import Any

import pytest

from winpilot.vnext.evidence_plane.artifacts import (
    FrameArtifactCorruptedError,
    FrameArtifactExistsError,
    FrameArtifactMissingError,
    FrameArtifactWriter,
)
from winpilot.vnext.evidence_plane.journal import (
    JOURNAL_EVENT_FORMAT,
    JOURNAL_FILENAME,
    RuntimeEvidenceJournal,
    RuntimeEvidenceJournalValidationError,
)


MISSION_ID = "mission-persistence"
RUN_ID = "run-persistence"
DECISION_ID = "decision-persistence"
ACTION_ID = "action-persistence"
CAPTURE_TIMESTAMP_NS = 1_753_600_000_123_456_789
JPEG = b"\xff\xd8\xff\xe0synthetic-evidence-frame\xff\xd9"

_EVENT_FIELDS = {
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
}


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _journal_path(run_directory: Path) -> Path:
    return run_directory / JOURNAL_FILENAME


def _journal_lines(run_directory: Path) -> list[bytes]:
    payload = _journal_path(run_directory).read_bytes()
    assert payload.endswith(b"\n")
    return payload[:-1].split(b"\n")


def _write_journal_lines(
    run_directory: Path,
    lines: list[bytes],
    *,
    trailing_newline: bool = True,
) -> None:
    payload = b"\n".join(lines)
    if trailing_newline:
        payload += b"\n"
    _journal_path(run_directory).write_bytes(payload)


def _rehash(event: dict[str, Any]) -> dict[str, Any]:
    material = {
        key: value
        for key, value in event.items()
        if key != "event_sha256"
    }
    event["event_sha256"] = hashlib.sha256(
        _canonical_json(material)
    ).hexdigest()
    return event


def _create_journal(tmp_path: Path) -> tuple[Path, tuple[Any, ...]]:
    run_directory = tmp_path / "evidence-run"
    with RuntimeEvidenceJournal.create(
        run_directory,
        MISSION_ID,
        RUN_ID,
    ) as journal:
        events = (
            journal.append(
                "actor_decision",
                {
                    "proposal": {
                        "action": "type_text",
                        "args": {"text": "审计证据 ✓"},
                    }
                },
                decision_id=DECISION_ID,
                action_id=ACTION_ID,
            ),
            journal.append(
                "admission",
                {
                    "frame_id": 41,
                    "controller_route": "atomic_desktop_adapter",
                },
                decision_id=DECISION_ID,
                action_id=ACTION_ID,
            ),
            journal.append(
                "physical_attempt",
                {
                    "attempt_index": 1,
                    "backend": "raw_device",
                },
                decision_id=DECISION_ID,
                action_id=ACTION_ID,
            ),
        )
    return run_directory, events


def _encoded_jpeg(raw: bytes = JPEG) -> str:
    return base64.b64encode(raw).decode("ascii")


def _frame_identity(
    raw: bytes = JPEG,
    *,
    frame_id: int = 41,
) -> dict[str, Any]:
    encoded = base64.b64encode(raw)
    return {
        "frame_id": frame_id,
        "epoch": 7,
        "hwnd": 901,
        "region": [10, 20, 320, 240],
        "scope": "target_window",
        "submitted_size": [320, 240],
        "image_sha256": hashlib.sha256(encoded).hexdigest(),
        "media_type": "image/jpeg",
    }


def _artifact_target(run_directory: Path, artifact_uri: str) -> Path:
    return run_directory.joinpath(*PurePosixPath(artifact_uri).parts)


def _write_frame(
    writer: FrameArtifactWriter,
    *,
    raw: bytes = JPEG,
    frame_id: int = 41,
    role: str = "before",
) -> tuple[Any, dict[str, Any]]:
    identity = _frame_identity(raw, frame_id=frame_id)
    result = writer.write_frame(
        mission_id=MISSION_ID,
        run_id=RUN_ID,
        decision_id=DECISION_ID,
        action_id=ACTION_ID,
        role=role,
        frame_identity=identity,
        image_b64=_encoded_jpeg(raw),
        capture_timestamp_ns=CAPTURE_TIMESTAMP_NS,
    )
    return result, identity


def _read_frame(
    reader: FrameArtifactWriter,
    *,
    result: Any,
    identity: dict[str, Any],
) -> bytes:
    return reader.read_frame(
        mission_id=MISSION_ID,
        run_id=RUN_ID,
        decision_id=DECISION_ID,
        action_id=ACTION_ID,
        role=result.role,
        frame_identity=identity,
        artifact_uri=result.artifact_uri,
        sha256=result.sha256,
    )


def test_journal_round_trip_is_canonical_utf8_jsonl(tmp_path: Path) -> None:
    run_directory, written = _create_journal(tmp_path)

    raw_lines = _journal_lines(run_directory)
    assert len(raw_lines) == 3
    assert b"\r" not in _journal_path(run_directory).read_bytes()
    assert "审计证据 ✓".encode("utf-8") in raw_lines[0]
    for line in raw_lines:
        decoded = json.loads(line)
        assert set(decoded) == _EVENT_FIELDS
        assert decoded["format"] == JOURNAL_EVENT_FORMAT
        assert line == _canonical_json(decoded)

    replayed = RuntimeEvidenceJournal.open_read_only(
        run_directory,
        MISSION_ID,
        RUN_ID,
    )
    assert replayed.events == written
    assert replayed.event_count == 3
    assert replayed.head == written[-1].event_sha256
    assert replayed.run_directory == run_directory.resolve()
    assert replayed.mission_id == MISSION_ID
    assert replayed.run_id == RUN_ID
    assert replayed.writable is False
    assert replayed.events[0].to_dict()["payload"]["proposal"]["args"] == {
        "text": "审计证据 ✓"
    }


def test_journal_rejects_duplicate_event_line(tmp_path: Path) -> None:
    run_directory, _ = _create_journal(tmp_path)
    lines = _journal_lines(run_directory)
    _write_journal_lines(run_directory, [*lines, lines[-1]])

    with pytest.raises(
        RuntimeEvidenceJournalValidationError,
        match="sequence",
    ):
        RuntimeEvidenceJournal.open_read_only(
            run_directory,
            MISSION_ID,
            RUN_ID,
        )


def test_journal_rejects_duplicate_json_key(tmp_path: Path) -> None:
    run_directory, _ = _create_journal(tmp_path)
    lines = _journal_lines(run_directory)
    needle = (
        b'"format":"runtime-evidence-journal-event/1"'
    )
    assert needle in lines[0]
    lines[0] = lines[0].replace(
        needle,
        needle + b"," + needle,
        1,
    )
    _write_journal_lines(run_directory, lines)

    with pytest.raises(
        RuntimeEvidenceJournalValidationError,
        match="duplicate object keys",
    ):
        RuntimeEvidenceJournal.open_read_only(
            run_directory,
            MISSION_ID,
            RUN_ID,
        )


def test_journal_rejects_non_contiguous_sequence(tmp_path: Path) -> None:
    run_directory, _ = _create_journal(tmp_path)
    lines = _journal_lines(run_directory)
    event = json.loads(lines[1])
    event["sequence"] = 9
    lines[1] = _canonical_json(_rehash(event))
    _write_journal_lines(run_directory, lines)

    with pytest.raises(
        RuntimeEvidenceJournalValidationError,
        match="sequence",
    ):
        RuntimeEvidenceJournal.open_read_only(
            run_directory,
            MISSION_ID,
            RUN_ID,
        )


def test_journal_rejects_out_of_order_events(tmp_path: Path) -> None:
    run_directory, _ = _create_journal(tmp_path)
    lines = _journal_lines(run_directory)
    _write_journal_lines(
        run_directory,
        [lines[0], lines[2], lines[1]],
    )

    with pytest.raises(
        RuntimeEvidenceJournalValidationError,
        match="sequence|hash chain",
    ):
        RuntimeEvidenceJournal.open_read_only(
            run_directory,
            MISSION_ID,
            RUN_ID,
        )


def test_journal_rejects_broken_previous_hash(tmp_path: Path) -> None:
    run_directory, _ = _create_journal(tmp_path)
    lines = _journal_lines(run_directory)
    event = json.loads(lines[1])
    event["previous_event_sha256"] = "f" * 64
    lines[1] = _canonical_json(_rehash(event))
    _write_journal_lines(run_directory, lines)

    with pytest.raises(
        RuntimeEvidenceJournalValidationError,
        match="hash chain",
    ):
        RuntimeEvidenceJournal.open_read_only(
            run_directory,
            MISSION_ID,
            RUN_ID,
        )


def test_journal_rejects_event_hash_mismatch(tmp_path: Path) -> None:
    run_directory, _ = _create_journal(tmp_path)
    lines = _journal_lines(run_directory)
    event = json.loads(lines[0])
    event["payload"]["proposal"]["args"]["text"] = "tampered"
    lines[0] = _canonical_json(event)
    _write_journal_lines(run_directory, lines)

    with pytest.raises(
        RuntimeEvidenceJournalValidationError,
        match="event hash",
    ):
        RuntimeEvidenceJournal.open_read_only(
            run_directory,
            MISSION_ID,
            RUN_ID,
        )


def test_journal_rejects_truncated_final_line(tmp_path: Path) -> None:
    run_directory, _ = _create_journal(tmp_path)
    lines = _journal_lines(run_directory)
    _write_journal_lines(
        run_directory,
        lines,
        trailing_newline=False,
    )

    with pytest.raises(
        RuntimeEvidenceJournalValidationError,
        match="trailing newline",
    ):
        RuntimeEvidenceJournal.open_read_only(
            run_directory,
            MISSION_ID,
            RUN_ID,
        )


def test_journal_rejects_invalid_utf8(tmp_path: Path) -> None:
    run_directory, _ = _create_journal(tmp_path)
    _journal_path(run_directory).write_bytes(b"\xff\n")

    with pytest.raises(
        RuntimeEvidenceJournalValidationError,
        match="strict UTF-8",
    ):
        RuntimeEvidenceJournal.open_read_only(
            run_directory,
            MISSION_ID,
            RUN_ID,
        )


def test_journal_rejects_noncanonical_json_bytes(tmp_path: Path) -> None:
    run_directory, _ = _create_journal(tmp_path)
    lines = _journal_lines(run_directory)
    event = json.loads(lines[0])
    lines[0] = json.dumps(
        event,
        ensure_ascii=False,
        sort_keys=False,
    ).encode("utf-8")
    _write_journal_lines(run_directory, lines)

    with pytest.raises(
        RuntimeEvidenceJournalValidationError,
        match="bytes are not canonical",
    ):
        RuntimeEvidenceJournal.open_read_only(
            run_directory,
            MISSION_ID,
            RUN_ID,
        )


def test_frame_artifact_write_is_exclusive(tmp_path: Path) -> None:
    run_directory = tmp_path / "artifact-run"
    run_directory.mkdir()
    writer = FrameArtifactWriter.create(run_directory)
    result, identity = _write_frame(writer)

    target = _artifact_target(run_directory, result.artifact_uri)
    assert target.read_bytes() == JPEG
    assert _read_frame(
        FrameArtifactWriter.open_read_only(run_directory),
        result=result,
        identity=identity,
    ) == JPEG

    with pytest.raises(FrameArtifactExistsError):
        _write_frame(writer)
    with pytest.raises(FrameArtifactExistsError):
        FrameArtifactWriter.create(run_directory)


def test_frame_artifact_preserves_raw_and_base64_hashes(
    tmp_path: Path,
) -> None:
    run_directory = tmp_path / "artifact-run"
    run_directory.mkdir()
    writer = FrameArtifactWriter.create(run_directory)
    result, identity = _write_frame(writer)
    encoded = base64.b64encode(JPEG)

    assert result.sha256 == hashlib.sha256(JPEG).hexdigest()
    assert identity["image_sha256"] == hashlib.sha256(encoded).hexdigest()
    assert result.sha256 != identity["image_sha256"]
    assert result.frame_id == identity["frame_id"]
    assert result.capture_timestamp_ns == CAPTURE_TIMESTAMP_NS
    assert result.role == "before"
    assert _read_frame(
        FrameArtifactWriter.open_read_only(run_directory),
        result=result,
        identity=identity,
    ) == JPEG


def test_frame_artifact_rejects_missing_file(tmp_path: Path) -> None:
    run_directory = tmp_path / "artifact-run"
    run_directory.mkdir()
    writer = FrameArtifactWriter.create(run_directory)
    result, identity = _write_frame(writer)
    _artifact_target(run_directory, result.artifact_uri).unlink()

    reader = FrameArtifactWriter.open_read_only(run_directory)
    with pytest.raises(FrameArtifactMissingError, match="missing"):
        _read_frame(reader, result=result, identity=identity)


@pytest.mark.parametrize(
    ("replacement", "expected_message"),
    (
        (
            b"\xff\xd8\xff\xe0replacement-frame\xff\xd9",
            "content hash",
        ),
        (JPEG[:-2], "not a complete JPEG"),
        (b"not-a-jpeg", "not a complete JPEG"),
    ),
    ids=("replaced", "truncated", "non-jpeg"),
)
def test_frame_artifact_rejects_corrupted_file(
    tmp_path: Path,
    replacement: bytes,
    expected_message: str,
) -> None:
    run_directory = tmp_path / "artifact-run"
    run_directory.mkdir()
    writer = FrameArtifactWriter.create(run_directory)
    result, identity = _write_frame(writer)
    _artifact_target(run_directory, result.artifact_uri).write_bytes(
        replacement
    )

    reader = FrameArtifactWriter.open_read_only(run_directory)
    with pytest.raises(
        FrameArtifactCorruptedError,
        match=expected_message,
    ):
        _read_frame(reader, result=result, identity=identity)


def test_frame_artifact_lists_orphans(tmp_path: Path) -> None:
    run_directory = tmp_path / "artifact-run"
    run_directory.mkdir()
    writer = FrameArtifactWriter.create(run_directory)
    before, _ = _write_frame(writer)
    after_raw = b"\xff\xd8\xff\xe0synthetic-after-frame\xff\xd9"
    after, _ = _write_frame(
        writer,
        raw=after_raw,
        frame_id=42,
        role="after",
    )

    reader = FrameArtifactWriter.open_read_only(run_directory)
    assert reader.list_orphans((before.artifact_uri,)) == (
        after.artifact_uri,
    )
    assert reader.list_orphans(
        (before.artifact_uri, after.artifact_uri)
    ) == ()
