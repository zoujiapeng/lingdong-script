"""Canonical identity for one concrete Windows process instance.

This neutral owner performs no process enumeration, window selection,
mutation, or runtime state management. Callers supply OS-observed facts.
"""
from __future__ import annotations

import hashlib
import json
import ntpath
import re


MAX_PROCESS_EXECUTABLE_PATH_CHARS = 2_048
_PROCESS_IDENTITY = re.compile(r"process-[0-9a-f]{32}")


class ProcessIdentityError(ValueError):
    """A process-instance identity input is incomplete or non-canonical."""


def canonical_process_identity(
    value: object, name: str = "process_identity",
) -> str:
    """Validate an already-derived ``process-<32hex>`` identity."""

    if type(value) is not str or not _PROCESS_IDENTITY.fullmatch(value):
        raise ProcessIdentityError(f"{name} must be canonical process-<32hex>")
    return value


def canonical_executable_path(
    value: object,
    name: str = "executable_path",
) -> str:
    """Validate one already-canonical absolute Windows ``.exe`` path."""

    if type(value) is not str:
        raise ProcessIdentityError(
            f"{name} must be a canonical absolute .exe path"
        )
    drive, _tail = ntpath.splitdrive(value)
    if (
        not value
        or value != value.strip()
        or len(value) > MAX_PROCESS_EXECUTABLE_PATH_CHARS
        or any(character in value for character in ('"', "\x00", "\r", "\n"))
        or not drive
        or not ntpath.isabs(value)
        or ntpath.normpath(value) != value
        or ntpath.splitext(ntpath.basename(value))[1].casefold() != ".exe"
    ):
        raise ProcessIdentityError(
            f"{name} must be a canonical absolute .exe path"
        )
    return value


def process_identity_for(
    *,
    process_id: int,
    executable_path: object,
    creation_time_ns: int,
) -> str:
    """Derive a stable identity for one observed process instance."""

    if type(process_id) is not int or process_id <= 0:
        raise ProcessIdentityError("process_id must be a positive integer")
    if type(creation_time_ns) is not int or creation_time_ns <= 0:
        raise ProcessIdentityError("creation_time_ns must be a positive integer")
    if type(executable_path) is not str:
        raise ProcessIdentityError("executable_path must be a string")
    path = executable_path.strip()
    if (
        not path
        or "\x00" in path
        or len(path) > MAX_PROCESS_EXECUTABLE_PATH_CHARS
    ):
        raise ProcessIdentityError("executable_path is invalid")
    drive, _tail = ntpath.splitdrive(path)
    if not drive or not ntpath.isabs(path):
        raise ProcessIdentityError("executable_path must be absolute")
    path = ntpath.normcase(ntpath.normpath(path)).casefold()
    material = json.dumps(
        {
            "creation_time_ns": creation_time_ns,
            "executable_path": path,
            "process_id": process_id,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return "process-" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


__all__ = [
    "MAX_PROCESS_EXECUTABLE_PATH_CHARS",
    "ProcessIdentityError",
    "canonical_executable_path",
    "canonical_process_identity",
    "process_identity_for",
]
