"""Durable frame artifacts for the vNext Runtime evidence plane.

This module intentionally knows nothing about Runtime, controller, or desktop
adapter types.  It accepts their canonical literal identities, writes JPEG
bytes once, and returns only the fields needed to construct a
``FrameArtifactReference``.
"""
from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import threading
from typing import Any, Iterable


ARTIFACT_DIRECTORY_NAME = "artifacts"

_MAX_SIGNED_64 = (1 << 63) - 1
_MIN_SIGNED_64 = -(1 << 63)
_IDENTITY = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_ARTIFACT_FILENAME = re.compile(
    r"(?P<role>before|after)-frame-"
    r"(?P<frame_id>[0-9]{20})-(?P<binding>[0-9a-f]{32})\.jpg"
)
_FRAME_IDENTITY_FIELDS = frozenset(
    {
        "frame_id",
        "epoch",
        "hwnd",
        "region",
        "scope",
        "submitted_size",
        "image_sha256",
        "media_type",
    }
)


class FrameArtifactError(RuntimeError):
    """A frame artifact operation could not satisfy its durable contract."""


class FrameArtifactValidationError(FrameArtifactError):
    """An artifact input is not canonical."""


class FrameArtifactExistsError(FrameArtifactError):
    """An immutable artifact path or directory already exists."""


class FrameArtifactMissingError(FrameArtifactError):
    """A required artifact path does not exist."""


class FrameArtifactCorruptedError(FrameArtifactError):
    """An artifact exists but is unsafe or does not match its reference."""


@dataclass(frozen=True, slots=True)
class ArtifactWriteResult:
    """Fields required to construct a canonical FrameArtifactReference."""

    frame_id: int
    artifact_uri: str
    sha256: str
    capture_timestamp_ns: int
    role: str

    def to_reference_dict(self) -> dict[str, int | str]:
        return {
            "frame_id": self.frame_id,
            "artifact_uri": self.artifact_uri,
            "sha256": self.sha256,
            "capture_timestamp_ns": self.capture_timestamp_ns,
            "role": self.role,
        }


class FrameArtifactWriter:
    """Exclusive artifact writer or strict read-only audit view for one run."""

    def __init__(
        self,
        run_directory: os.PathLike[str] | str,
        *,
        mode: str = "new",
    ) -> None:
        if type(mode) is not str or mode not in {"new", "audit"}:
            raise FrameArtifactValidationError(
                "frame artifact mode must be new or audit"
            )
        run_path = _existing_run_directory(run_directory)
        artifact_path = run_path / ARTIFACT_DIRECTORY_NAME
        if mode == "new":
            try:
                os.mkdir(artifact_path, 0o700)
            except FileExistsError:
                raise FrameArtifactExistsError(
                    "frame artifact directory already exists"
                ) from None
            except OSError:
                raise FrameArtifactError(
                    "frame artifact directory could not be exclusively created"
                ) from None
            try:
                _fsync_directory(run_path)
            except OSError:
                raise FrameArtifactError(
                    "frame artifact directory could not be made durable"
                ) from None
        else:
            _validate_artifact_directory(artifact_path)
        self._run_directory = run_path
        self._artifact_directory = artifact_path
        self._writable = mode == "new"
        self._lock = threading.RLock()

    @classmethod
    def create(
        cls,
        run_directory: os.PathLike[str] | str,
    ) -> FrameArtifactWriter:
        """Exclusively create a writable ``artifacts`` directory for a run."""

        return cls(run_directory, mode="new")

    @classmethod
    def open_read_only(
        cls,
        run_directory: os.PathLike[str] | str,
    ) -> FrameArtifactWriter:
        """Safely reopen an existing artifact directory for audit."""

        return cls(run_directory, mode="audit")

    @classmethod
    def replay(
        cls,
        run_directory: os.PathLike[str] | str,
    ) -> FrameArtifactWriter:
        """Alias for the explicit read-only audit operation."""

        return cls.open_read_only(run_directory)

    @property
    def run_directory(self) -> Path:
        return self._run_directory

    @property
    def artifact_directory(self) -> Path:
        return self._artifact_directory

    @property
    def writable(self) -> bool:
        return self._writable

    def write_frame(
        self,
        *,
        mission_id: str,
        run_id: str,
        decision_id: str,
        action_id: str,
        role: str,
        frame_identity: dict[str, Any],
        image_b64: str,
        capture_timestamp_ns: int,
    ) -> ArtifactWriteResult:
        """Validate and durably write one immutable JPEG artifact."""

        checked_mission_id = _identity(mission_id, "mission_id")
        checked_run_id = _identity(run_id, "run_id")
        checked_decision_id = _identity(decision_id, "decision_id")
        checked_action_id = _identity(action_id, "action_id")
        checked_role = _role(role)
        checked_frame = _frame_identity(frame_identity)
        checked_timestamp = _timestamp_ns(
            capture_timestamp_ns,
            "capture_timestamp_ns",
        )
        raw, raw_sha256 = _decode_frame(image_b64, checked_frame)
        artifact_uri = _artifact_uri(
            mission_id=checked_mission_id,
            run_id=checked_run_id,
            decision_id=checked_decision_id,
            action_id=checked_action_id,
            role=checked_role,
            frame_identity=checked_frame,
        )

        with self._lock:
            if not self._writable:
                raise FrameArtifactError("frame artifact store is read-only")
            _validate_artifact_directory(self._artifact_directory)
            target, _ = _artifact_target(
                self._artifact_directory,
                artifact_uri,
            )
            try:
                with target.open("xb") as stream:
                    _write_all(stream, raw)
                    stream.flush()
                    os.fsync(stream.fileno())
                _fsync_directory(self._artifact_directory)
            except FileExistsError:
                raise FrameArtifactExistsError(
                    "frame artifact already exists"
                ) from None
            except OSError:
                raise FrameArtifactError(
                    "frame artifact could not be durably written"
                ) from None

        return ArtifactWriteResult(
            frame_id=checked_frame["frame_id"],
            artifact_uri=artifact_uri,
            sha256=raw_sha256,
            capture_timestamp_ns=checked_timestamp,
            role=checked_role,
        )

    def read_frame(
        self,
        *,
        mission_id: str,
        run_id: str,
        decision_id: str,
        action_id: str,
        role: str,
        frame_identity: dict[str, Any],
        artifact_uri: str,
        sha256: str,
    ) -> bytes:
        """Read and authenticate one artifact, distinguishing loss from damage."""

        checked_mission_id = _identity(mission_id, "mission_id")
        checked_run_id = _identity(run_id, "run_id")
        checked_decision_id = _identity(decision_id, "decision_id")
        checked_action_id = _identity(action_id, "action_id")
        checked_role = _role(role)
        checked_frame = _frame_identity(frame_identity)
        checked_sha256 = _sha256(sha256, "sha256")
        expected_uri = _artifact_uri(
            mission_id=checked_mission_id,
            run_id=checked_run_id,
            decision_id=checked_decision_id,
            action_id=checked_action_id,
            role=checked_role,
            frame_identity=checked_frame,
        )
        if artifact_uri != expected_uri:
            raise FrameArtifactCorruptedError(
                "frame artifact path does not match its binding"
            )

        with self._lock:
            _validate_artifact_directory(self._artifact_directory)
            target, match = _artifact_target(
                self._artifact_directory,
                artifact_uri,
            )
            if (
                match.group("role") != checked_role
                or int(match.group("frame_id")) != checked_frame["frame_id"]
            ):
                raise FrameArtifactCorruptedError(
                    "frame artifact path does not match its frame"
                )
            raw = _read_regular_file(target)

        if not _is_jpeg(raw):
            raise FrameArtifactCorruptedError(
                "frame artifact is not a complete JPEG"
            )
        if hashlib.sha256(raw).hexdigest() != checked_sha256:
            raise FrameArtifactCorruptedError(
                "frame artifact content hash does not match"
            )
        encoded = base64.b64encode(raw)
        if (
            hashlib.sha256(encoded).hexdigest()
            != checked_frame["image_sha256"]
        ):
            raise FrameArtifactCorruptedError(
                "frame artifact does not match the frame identity"
            )
        return raw

    def list_orphans(
        self,
        referenced_artifact_uris: Iterable[str],
    ) -> tuple[str, ...]:
        """Return deterministic artifact paths not named by any reference."""

        if isinstance(referenced_artifact_uris, (str, bytes)):
            raise FrameArtifactValidationError(
                "artifact references must be an iterable of paths"
            )
        referenced: set[str] = set()
        try:
            for artifact_uri in referenced_artifact_uris:
                _artifact_target(self._artifact_directory, artifact_uri)
                referenced.add(artifact_uri)
        except TypeError:
            raise FrameArtifactValidationError(
                "artifact references must be an iterable of paths"
            ) from None

        with self._lock:
            _validate_artifact_directory(self._artifact_directory)
            try:
                entries = tuple(self._artifact_directory.iterdir())
            except FileNotFoundError:
                raise FrameArtifactMissingError(
                    "frame artifact directory is missing"
                ) from None
            except OSError:
                raise FrameArtifactCorruptedError(
                    "frame artifact directory could not be inspected"
                ) from None

            present: set[str] = set()
            for entry in entries:
                try:
                    metadata = entry.lstat()
                except FileNotFoundError:
                    raise FrameArtifactMissingError(
                        "frame artifact disappeared during audit"
                    ) from None
                except OSError:
                    raise FrameArtifactCorruptedError(
                        "frame artifact could not be inspected"
                    ) from None
                if (
                    stat.S_ISLNK(metadata.st_mode)
                    or not stat.S_ISREG(metadata.st_mode)
                ):
                    raise FrameArtifactCorruptedError(
                        "frame artifact directory contains an unsafe entry"
                    )
                artifact_uri = PurePosixPath(
                    ARTIFACT_DIRECTORY_NAME,
                    entry.name,
                ).as_posix()
                _artifact_target(self._artifact_directory, artifact_uri)
                present.add(artifact_uri)

        if referenced - present:
            raise FrameArtifactMissingError(
                "a referenced frame artifact is missing"
            )
        return tuple(sorted(present - referenced))

    def __repr__(self) -> str:
        return f"<FrameArtifactWriter writable={self.writable}>"


def _existing_run_directory(value: os.PathLike[str] | str) -> Path:
    if type(value) is str:
        raw = value
    elif isinstance(value, os.PathLike):
        raw = os.fspath(value)
    else:
        raise FrameArtifactValidationError(
            "run_directory must be a filesystem path"
        )
    if type(raw) is not str or not raw:
        raise FrameArtifactValidationError(
            "run_directory must be a non-empty filesystem path"
        )
    try:
        path = Path(raw)
        if not path.is_absolute():
            raise FrameArtifactValidationError(
                "run_directory must be an absolute filesystem path"
            )
        resolved = path.resolve(strict=True)
        metadata = resolved.stat()
    except FrameArtifactValidationError:
        raise
    except (OSError, RuntimeError):
        raise FrameArtifactValidationError(
            "run_directory is not an existing durable directory"
        ) from None
    if not stat.S_ISDIR(metadata.st_mode):
        raise FrameArtifactValidationError(
            "run_directory is not an existing durable directory"
        )
    return resolved


def _validate_artifact_directory(path: Path) -> None:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        raise FrameArtifactMissingError(
            "frame artifact directory is missing"
        ) from None
    except OSError:
        raise FrameArtifactCorruptedError(
            "frame artifact directory could not be inspected"
        ) from None
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise FrameArtifactCorruptedError(
            "frame artifact directory is unsafe"
        )
    try:
        if path.resolve(strict=True).parent != path.parent.resolve(strict=True):
            raise FrameArtifactCorruptedError(
                "frame artifact directory escaped its run directory"
            )
    except FrameArtifactCorruptedError:
        raise
    except (OSError, RuntimeError):
        raise FrameArtifactCorruptedError(
            "frame artifact directory could not be resolved"
        ) from None


def _identity(value: Any, name: str) -> str:
    if type(value) is not str or _IDENTITY.fullmatch(value) is None:
        raise FrameArtifactValidationError(
            f"{name} is not a canonical identity"
        )
    return value


def _sha256(value: Any, name: str) -> str:
    if type(value) is not str or _SHA256.fullmatch(value) is None:
        raise FrameArtifactValidationError(
            f"{name} must be a lowercase SHA-256 digest"
        )
    return value


def _timestamp_ns(value: Any, name: str) -> int:
    if type(value) is not int or not 0 <= value <= _MAX_SIGNED_64:
        raise FrameArtifactValidationError(
            f"{name} must be a non-negative signed-64 integer"
        )
    return value


def _positive_integer(value: Any, name: str) -> int:
    if type(value) is not int or not 0 < value <= _MAX_SIGNED_64:
        raise FrameArtifactValidationError(
            f"{name} must be a positive signed-64 integer"
        )
    return value


def _role(value: Any) -> str:
    if type(value) is not str or value not in {"before", "after"}:
        raise FrameArtifactValidationError("role must be before or after")
    return value


def _frame_identity(value: Any) -> dict[str, Any]:
    if type(value) is not dict or set(value) != _FRAME_IDENTITY_FIELDS:
        raise FrameArtifactValidationError(
            "frame_identity fields do not match the canonical schema"
        )
    frame_id = _positive_integer(value["frame_id"], "frame_identity.frame_id")
    epoch = _timestamp_ns(value["epoch"], "frame_identity.epoch")
    hwnd = _positive_integer(value["hwnd"], "frame_identity.hwnd")
    region = value["region"]
    if (
        type(region) is not list
        or len(region) != 4
        or any(type(part) is not int for part in region)
        or any(
            not _MIN_SIGNED_64 <= part <= _MAX_SIGNED_64
            for part in region
        )
        or region[2] <= 0
        or region[3] <= 0
    ):
        raise FrameArtifactValidationError(
            "frame_identity.region must be four canonical integers"
        )
    submitted_size = value["submitted_size"]
    if (
        type(submitted_size) is not list
        or len(submitted_size) != 2
        or any(type(part) is not int or part <= 0 for part in submitted_size)
        or any(part > _MAX_SIGNED_64 for part in submitted_size)
    ):
        raise FrameArtifactValidationError(
            "frame_identity.submitted_size must be two positive integers"
        )
    if (
        type(value["scope"]) is not str
        or value["scope"] not in {"target_window", "full_desktop"}
    ):
        raise FrameArtifactValidationError(
            "frame_identity.scope must be target_window or full_desktop"
        )
    image_sha256 = _sha256(
        value["image_sha256"],
        "frame_identity.image_sha256",
    )
    if (
        type(value["media_type"]) is not str
        or value["media_type"] != "image/jpeg"
    ):
        raise FrameArtifactValidationError(
            "frame_identity.media_type must be image/jpeg"
        )
    return {
        "frame_id": frame_id,
        "epoch": epoch,
        "hwnd": hwnd,
        "region": list(region),
        "scope": value["scope"],
        "submitted_size": list(submitted_size),
        "image_sha256": image_sha256,
        "media_type": "image/jpeg",
    }


def _decode_frame(
    image_b64: Any,
    frame_identity: dict[str, Any],
) -> tuple[bytes, str]:
    if type(image_b64) is not str or not image_b64:
        raise FrameArtifactValidationError(
            "frame payload must be non-empty canonical base64"
        )
    try:
        encoded = image_b64.encode("ascii", errors="strict")
        raw = base64.b64decode(encoded, validate=True)
    except (UnicodeEncodeError, binascii.Error, ValueError):
        raise FrameArtifactValidationError(
            "frame payload must be canonical base64"
        ) from None
    if base64.b64encode(raw) != encoded:
        raise FrameArtifactValidationError(
            "frame payload must be canonical base64"
        )
    if not _is_jpeg(raw):
        raise FrameArtifactValidationError(
            "frame payload must be a complete JPEG"
        )
    if (
        hashlib.sha256(encoded).hexdigest()
        != frame_identity["image_sha256"]
    ):
        raise FrameArtifactValidationError(
            "frame payload does not match frame_identity"
        )
    return raw, hashlib.sha256(raw).hexdigest()


def _is_jpeg(value: bytes) -> bool:
    return (
        len(value) >= 5
        and value.startswith(b"\xff\xd8\xff")
        and value.endswith(b"\xff\xd9")
    )


def _artifact_uri(
    *,
    mission_id: str,
    run_id: str,
    decision_id: str,
    action_id: str,
    role: str,
    frame_identity: dict[str, Any],
) -> str:
    material = {
        "mission_id": mission_id,
        "run_id": run_id,
        "decision_id": decision_id,
        "action_id": action_id,
        "role": role,
        "frame_identity": frame_identity,
    }
    try:
        canonical = json.dumps(
            material,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError):
        raise FrameArtifactValidationError(
            "frame artifact binding is not canonical"
        ) from None
    binding = hashlib.sha256(canonical).hexdigest()[:32]
    filename = (
        f"{role}-frame-{frame_identity['frame_id']:020d}-{binding}.jpg"
    )
    return PurePosixPath(ARTIFACT_DIRECTORY_NAME, filename).as_posix()


def _artifact_target(
    artifact_directory: Path,
    artifact_uri: Any,
) -> tuple[Path, re.Match[str]]:
    if (
        type(artifact_uri) is not str
        or not artifact_uri
        or artifact_uri.strip() != artifact_uri
        or len(artifact_uri) > 4096
        or "\\" in artifact_uri
    ):
        raise FrameArtifactCorruptedError(
            "frame artifact path is not canonical"
        )
    path = PurePosixPath(artifact_uri)
    if (
        path.is_absolute()
        or path.as_posix() != artifact_uri
        or path.parts[:1] != (ARTIFACT_DIRECTORY_NAME,)
        or len(path.parts) != 2
        or path.parts[1] in {".", ".."}
    ):
        raise FrameArtifactCorruptedError(
            "frame artifact path is not a safe relative path"
        )
    match = _ARTIFACT_FILENAME.fullmatch(path.name)
    if match is None:
        raise FrameArtifactCorruptedError(
            "frame artifact filename is not canonical"
        )
    return artifact_directory / path.name, match


def _read_regular_file(path: Path) -> bytes:
    try:
        if path.is_symlink():
            raise FrameArtifactCorruptedError(
                "frame artifact path is unsafe"
            )
        flags = os.O_RDONLY
        if hasattr(os, "O_BINARY"):
            flags |= os.O_BINARY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(path, flags)
    except FrameArtifactCorruptedError:
        raise
    except FileNotFoundError:
        raise FrameArtifactMissingError(
            "frame artifact is missing"
        ) from None
    except OSError:
        raise FrameArtifactCorruptedError(
            "frame artifact could not be opened safely"
        ) from None

    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise FrameArtifactCorruptedError(
                "frame artifact is not a regular file"
            )
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = -1
            return stream.read()
    except FrameArtifactCorruptedError:
        raise
    except OSError:
        raise FrameArtifactCorruptedError(
            "frame artifact could not be read"
        ) from None
    finally:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass


def _write_all(stream: Any, value: bytes) -> None:
    view = memoryview(value)
    offset = 0
    while offset < len(view):
        written = stream.write(view[offset:])
        if type(written) is not int or written <= 0:
            raise OSError("frame artifact write made no progress")
        offset += written


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


__all__ = [
    "ARTIFACT_DIRECTORY_NAME",
    "ArtifactWriteResult",
    "FrameArtifactCorruptedError",
    "FrameArtifactError",
    "FrameArtifactExistsError",
    "FrameArtifactMissingError",
    "FrameArtifactValidationError",
    "FrameArtifactWriter",
]
