"""Pure mechanical admission for one already-selected desktop input route.

Callers must run this gate inside the existing desktop-action queue and
``FrameAuthority.run_if_current`` pin.  It owns no Windows API, frame state,
input backend, recovery policy, or semantic target selection.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum
import re
from typing import Any, Generic, TypeVar

from ..process_identity import ProcessIdentityError, canonical_process_identity


COMMIT_ADMISSION_REQUEST_FORMAT = "commit-admission-request/2"
COMMIT_ENVIRONMENT_FORMAT = "commit-environment/1"
COMMIT_ADMISSION_RESULT_FORMAT = "commit-admission-result/0"
_IDENTITY = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}")
_SHA256 = re.compile(r"[0-9a-f]{64}")

Point = tuple[int, int]
Region = tuple[int, int, int, int]
Size = tuple[int, int]
T = TypeVar("T")


class CommitAdmissionError(ValueError):
    pass


class CommitRoute(str, Enum):
    PHYSICAL = "physical"
    BACKGROUND_WINDOW = "background_window"


class CommitInputKind(str, Enum):
    """Mechanical constraints required by the selected input primitive."""

    POINT = "point"
    KEYBOARD = "keyboard"


class CommitAdmissionStatus(str, Enum):
    ADMITTED = "admitted"
    NOT_STARTED = "not_started"


class CommitAdmissionReason(str, Enum):
    ADMITTED = "admitted"
    FRAME_CHANGED = "frame_changed"
    OBSERVATION_FAILED = "observation_failed"
    OBSERVATION_INVALID = "observation_invalid"
    STOP_REQUESTED = "stop_requested"
    CANCELED = "canceled"
    MUTATION_NOT_ALLOWED = "mutation_not_allowed"
    POINTER_NOT_RELEASED = "pointer_not_released"
    TARGET_UNAVAILABLE = "target_unavailable"
    TARGET_CHANGED = "target_changed"
    TARGET_ROOT_CHANGED = "target_root_changed"
    TARGET_GEOMETRY_CHANGED = "target_geometry_changed"
    POINT_OUTSIDE_FRAME = "point_outside_frame"
    POINT_OUTSIDE_TARGET = "point_outside_target"
    FOREGROUND_UNAVAILABLE = "foreground_unavailable"
    FOREGROUND_CHANGED = "foreground_changed"
    HIT_TEST_UNAVAILABLE = "hit_test_unavailable"
    HIT_TEST_CHANGED = "hit_test_changed"


def _integer(value: Any, name: str, *, positive: bool = False) -> int:
    if type(value) is not int or value < (1 if positive else 0):
        raise CommitAdmissionError(
            f"{name} must be a {'positive' if positive else 'non-negative'} integer"
        )
    return value


def _optional_positive(value: Any, name: str) -> int | None:
    return None if value is None else _integer(value, name, positive=True)


def _identity(value: Any, name: str) -> str:
    if type(value) is not str or not _IDENTITY.fullmatch(value.strip()):
        raise CommitAdmissionError(f"{name} is not a valid identity")
    return value.strip()


def _tuple_of_ints(value: Any, length: int, name: str) -> tuple[int, ...]:
    if (
        not isinstance(value, (tuple, list))
        or len(value) != length
        or any(type(item) is not int for item in value)
    ):
        raise CommitAdmissionError(f"{name} must contain {length} integers")
    return tuple(value)


def _region(value: Any, name: str) -> Region:
    region = _tuple_of_ints(value, 4, name)
    if region[2] <= 0 or region[3] <= 0:
        raise CommitAdmissionError(f"{name} must have positive width and height")
    return region  # type: ignore[return-value]


def _size(value: Any) -> Size:
    size = _tuple_of_ints(value, 2, "submitted_size")
    if min(size) <= 0:
        raise CommitAdmissionError("submitted_size values must be positive")
    return size  # type: ignore[return-value]


def _point(value: Any, name: str = "screen_point") -> Point:
    return _tuple_of_ints(value, 2, name)  # type: ignore[return-value]


def _strict(raw: Mapping[str, Any], fields: tuple[str, ...], name: str) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise CommitAdmissionError(f"{name} must be an object")
    missing, extra = set(fields) - set(raw), set(raw) - set(fields)
    if missing or extra:
        raise CommitAdmissionError(
            f"invalid {name} fields; missing={sorted(missing)}, extra={sorted(extra)}"
        )
    return dict(raw)


def _contains(region: Region, point: Point) -> bool:
    left, top, width, height = region
    return left <= point[0] < left + width and top <= point[1] < top + height


@dataclass(frozen=True, slots=True)
class FrameCommitIdentity:
    """Complete structural identity consumed by FrameAuthority's atomic pin."""

    frame_id: int
    epoch: int
    hwnd: int
    region: Region
    scope: str
    submitted_size: Size
    image_sha256: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "frame_id", _integer(self.frame_id, "frame_id", positive=True))
        object.__setattr__(self, "epoch", _integer(self.epoch, "epoch"))
        object.__setattr__(self, "hwnd", _integer(self.hwnd, "frame hwnd", positive=True))
        object.__setattr__(self, "region", _region(self.region, "frame region"))
        object.__setattr__(self, "scope", _identity(self.scope, "frame scope"))
        object.__setattr__(self, "submitted_size", _size(self.submitted_size))
        if type(self.image_sha256) is not str or not _SHA256.fullmatch(self.image_sha256):
            raise CommitAdmissionError("image_sha256 must be a lowercase SHA-256 digest")

    def to_dict(self) -> dict[str, Any]:
        return {
            "frame_id": self.frame_id, "epoch": self.epoch, "hwnd": self.hwnd,
            "region": list(self.region), "scope": self.scope,
            "submitted_size": list(self.submitted_size), "image_sha256": self.image_sha256,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "FrameCommitIdentity":
        return cls(**_strict(raw, (
            "frame_id", "epoch", "hwnd", "region", "scope",
            "submitted_size", "image_sha256",
        ), "FrameCommitIdentity"))


@dataclass(frozen=True, slots=True)
class TargetCommitIdentity:
    """Bound child/root/process identity plus exact physical window geometry."""

    binding_id: str
    hwnd: int
    root_hwnd: int
    process_id: int
    process_identity: str
    epoch: int
    window_region: Region

    def __post_init__(self) -> None:
        object.__setattr__(self, "binding_id", _identity(self.binding_id, "binding_id"))
        for name in ("hwnd", "root_hwnd", "process_id"):
            object.__setattr__(self, name, _integer(getattr(self, name), name, positive=True))
        try:
            process_identity = canonical_process_identity(self.process_identity)
        except ProcessIdentityError as exc:
            raise CommitAdmissionError(str(exc)) from exc
        object.__setattr__(self, "process_identity", process_identity)
        object.__setattr__(self, "epoch", _integer(self.epoch, "target epoch"))
        object.__setattr__(self, "window_region", _region(self.window_region, "window_region"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "binding_id": self.binding_id, "hwnd": self.hwnd,
            "root_hwnd": self.root_hwnd, "process_id": self.process_id,
            "process_identity": self.process_identity,
            "epoch": self.epoch, "window_region": list(self.window_region),
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "TargetCommitIdentity":
        return cls(**_strict(raw, (
            "binding_id", "hwnd", "root_hwnd", "process_id",
            "process_identity", "epoch", "window_region",
        ), "TargetCommitIdentity"))


@dataclass(frozen=True, slots=True)
class CommitAdmissionRequest:
    request_id: str
    frame: FrameCommitIdentity
    target: TargetCommitIdentity
    route: CommitRoute | str
    input_kind: CommitInputKind | str
    screen_point: Point | None
    format: str = COMMIT_ADMISSION_REQUEST_FORMAT

    def __post_init__(self) -> None:
        if self.format != COMMIT_ADMISSION_REQUEST_FORMAT:
            raise CommitAdmissionError("unsupported CommitAdmissionRequest format")
        object.__setattr__(self, "request_id", _identity(self.request_id, "request_id"))
        if not isinstance(self.frame, FrameCommitIdentity):
            raise CommitAdmissionError("frame must be FrameCommitIdentity")
        if not isinstance(self.target, TargetCommitIdentity):
            raise CommitAdmissionError("target must be TargetCommitIdentity")
        try:
            object.__setattr__(self, "route", CommitRoute(self.route))
            object.__setattr__(self, "input_kind", CommitInputKind(self.input_kind))
        except (TypeError, ValueError) as exc:
            raise CommitAdmissionError(str(exc)) from exc
        if self.input_kind is CommitInputKind.POINT:
            object.__setattr__(self, "screen_point", _point(self.screen_point))
        elif self.screen_point is not None:
            raise CommitAdmissionError("keyboard input must not contain screen_point")
        if (
            self.input_kind is CommitInputKind.KEYBOARD
            and self.route is not CommitRoute.PHYSICAL
        ):
            raise CommitAdmissionError("keyboard input requires the physical route")
        if self.frame.hwnd != self.target.hwnd or self.frame.epoch != self.target.epoch:
            raise CommitAdmissionError("frame and target identity disagree")

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": self.format, "request_id": self.request_id,
            "frame": self.frame.to_dict(), "target": self.target.to_dict(),
            "route": self.route.value, "input_kind": self.input_kind.value,
            "screen_point": (
                None if self.screen_point is None else list(self.screen_point)
            ),
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "CommitAdmissionRequest":
        value = _strict(raw, (
            "format", "request_id", "frame", "target", "route", "input_kind",
            "screen_point",
        ), "CommitAdmissionRequest")
        value["frame"] = FrameCommitIdentity.from_dict(value["frame"])
        value["target"] = TargetCommitIdentity.from_dict(value["target"])
        return cls(**value)


@dataclass(frozen=True, slots=True)
class CommitEnvironment:
    """Injected facts read after queue wait and immediately before callback."""

    target: TargetCommitIdentity | None
    pointer_released: bool
    mutation_allowed: bool
    stop_requested: bool
    canceled: bool
    foreground_hwnd: int | None
    foreground_root_hwnd: int | None
    foreground_process_id: int | None
    foreground_process_identity: str | None
    hit_test_point: Point | None
    hit_hwnd: int | None
    hit_root_hwnd: int | None
    hit_process_id: int | None
    hit_process_identity: str | None
    format: str = COMMIT_ENVIRONMENT_FORMAT

    def __post_init__(self) -> None:
        if self.format != COMMIT_ENVIRONMENT_FORMAT:
            raise CommitAdmissionError("unsupported CommitEnvironment format")
        if self.target is not None and not isinstance(self.target, TargetCommitIdentity):
            raise CommitAdmissionError("observed target must be TargetCommitIdentity or null")
        for name in ("pointer_released", "mutation_allowed", "stop_requested", "canceled"):
            if type(getattr(self, name)) is not bool:
                raise CommitAdmissionError(f"{name} must be boolean")
        foreground = (
            self.foreground_hwnd, self.foreground_root_hwnd,
            self.foreground_process_id, self.foreground_process_identity,
        )
        if any(value is not None for value in foreground) != all(
            value is not None for value in foreground
        ):
            raise CommitAdmissionError("foreground identity must be complete or absent")
        for name in (
            "foreground_hwnd", "foreground_root_hwnd", "foreground_process_id",
            "hit_hwnd", "hit_root_hwnd", "hit_process_id",
        ):
            object.__setattr__(self, name, _optional_positive(getattr(self, name), name))
        for name in ("foreground_process_identity", "hit_process_identity"):
            value = getattr(self, name)
            if value is not None:
                try:
                    value = canonical_process_identity(value, name)
                except ProcessIdentityError as exc:
                    raise CommitAdmissionError(str(exc)) from exc
                object.__setattr__(self, name, value)
        hit = (
            self.hit_hwnd, self.hit_root_hwnd, self.hit_process_id,
            self.hit_process_identity,
        )
        if self.hit_test_point is None:
            if any(value is not None for value in hit):
                raise CommitAdmissionError("hit identity requires hit_test_point")
        else:
            object.__setattr__(self, "hit_test_point", _point(self.hit_test_point, "hit_test_point"))
            if not all(value is not None for value in hit):
                raise CommitAdmissionError("hit identity must be complete or absent")


@dataclass(frozen=True, slots=True)
class CommitAdmissionResult:
    """Admission only: it makes no claim about physical input completion."""

    request_id: str
    status: CommitAdmissionStatus | str
    reason: CommitAdmissionReason | str
    callback_invoked: bool
    format: str = COMMIT_ADMISSION_RESULT_FORMAT

    def __post_init__(self) -> None:
        if self.format != COMMIT_ADMISSION_RESULT_FORMAT:
            raise CommitAdmissionError("unsupported CommitAdmissionResult format")
        object.__setattr__(self, "request_id", _identity(self.request_id, "request_id"))
        try:
            status, reason = CommitAdmissionStatus(self.status), CommitAdmissionReason(self.reason)
        except (TypeError, ValueError) as exc:
            raise CommitAdmissionError(str(exc)) from exc
        if type(self.callback_invoked) is not bool:
            raise CommitAdmissionError("callback_invoked must be boolean")
        if status is CommitAdmissionStatus.ADMITTED:
            if reason is not CommitAdmissionReason.ADMITTED or not self.callback_invoked:
                raise CommitAdmissionError("ADMITTED requires a returned callback")
        elif self.callback_invoked or reason is CommitAdmissionReason.ADMITTED:
            raise CommitAdmissionError("NOT_STARTED requires no callback and a rejection reason")
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "reason", reason)

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": self.format, "request_id": self.request_id,
            "status": self.status.value, "reason": self.reason.value,
            "callback_invoked": self.callback_invoked,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "CommitAdmissionResult":
        return cls(**_strict(raw, (
            "format", "request_id", "status", "reason", "callback_invoked",
        ), "CommitAdmissionResult"))


@dataclass(frozen=True, slots=True)
class CommitCallbackResult(Generic[T]):
    admission: CommitAdmissionResult
    value: T | None


class CommitAdmissionGate:
    """Observe and invoke one route-bound callback at most once."""

    def __init__(self, observe: Callable[[CommitAdmissionRequest], CommitEnvironment]) -> None:
        if not callable(observe):
            raise CommitAdmissionError("observe must be callable")
        self._observe = observe

    @staticmethod
    def _reason(
        request: CommitAdmissionRequest,
        observed: CommitEnvironment,
    ) -> CommitAdmissionReason | None:
        if observed.stop_requested:
            return CommitAdmissionReason.STOP_REQUESTED
        if observed.canceled:
            return CommitAdmissionReason.CANCELED
        if not observed.mutation_allowed:
            return CommitAdmissionReason.MUTATION_NOT_ALLOWED
        if not observed.pointer_released:
            return CommitAdmissionReason.POINTER_NOT_RELEASED
        if observed.target is None:
            return CommitAdmissionReason.TARGET_UNAVAILABLE
        expected, actual = request.target, observed.target
        if actual.root_hwnd != expected.root_hwnd:
            return CommitAdmissionReason.TARGET_ROOT_CHANGED
        if actual.window_region != expected.window_region:
            return CommitAdmissionReason.TARGET_GEOMETRY_CHANGED
        if actual != expected:
            return CommitAdmissionReason.TARGET_CHANGED
        if request.input_kind is CommitInputKind.POINT:
            assert request.screen_point is not None
            if not _contains(request.frame.region, request.screen_point):
                return CommitAdmissionReason.POINT_OUTSIDE_FRAME
            if not _contains(expected.window_region, request.screen_point):
                return CommitAdmissionReason.POINT_OUTSIDE_TARGET

        if request.route is CommitRoute.PHYSICAL:
            foreground = (
                observed.foreground_hwnd,
                observed.foreground_root_hwnd,
                observed.foreground_process_id,
            )
            if any(value is None for value in foreground):
                return CommitAdmissionReason.FOREGROUND_UNAVAILABLE
            if (
                observed.foreground_root_hwnd != expected.root_hwnd
                or observed.foreground_process_id != expected.process_id
                or observed.foreground_process_identity != expected.process_identity
            ):
                return CommitAdmissionReason.FOREGROUND_CHANGED

        if request.input_kind is CommitInputKind.POINT:
            hit = (observed.hit_hwnd, observed.hit_root_hwnd, observed.hit_process_id)
            if observed.hit_test_point is None or any(value is None for value in hit):
                return CommitAdmissionReason.HIT_TEST_UNAVAILABLE
            if (
                observed.hit_test_point != request.screen_point
                or observed.hit_root_hwnd != expected.root_hwnd
                or observed.hit_process_id != expected.process_id
                or observed.hit_process_identity != expected.process_identity
            ):
                return CommitAdmissionReason.HIT_TEST_CHANGED
        return None

    def admit_and_commit(
        self,
        request: CommitAdmissionRequest,
        callback: Callable[[], T],
    ) -> CommitCallbackResult[T]:
        """Reject before entry, or call once and let callback exceptions escape."""

        if not isinstance(request, CommitAdmissionRequest):
            raise CommitAdmissionError("request must be CommitAdmissionRequest")
        if not callable(callback):
            raise CommitAdmissionError("callback must be callable")
        try:
            observed = self._observe(request)
        except Exception:
            return self._not_started(request, CommitAdmissionReason.OBSERVATION_FAILED)
        if not isinstance(observed, CommitEnvironment):
            return self._not_started(request, CommitAdmissionReason.OBSERVATION_INVALID)
        reason = self._reason(request, observed)
        if reason is not None:
            return self._not_started(request, reason)

        # Never translate this exception to NOT_STARTED: input may have begun.
        value = callback()
        return CommitCallbackResult(
            CommitAdmissionResult(
                request.request_id, CommitAdmissionStatus.ADMITTED,
                CommitAdmissionReason.ADMITTED, True,
            ),
            value,
        )

    @staticmethod
    def _not_started(
        request: CommitAdmissionRequest,
        reason: CommitAdmissionReason,
    ) -> CommitCallbackResult[Any]:
        return CommitCallbackResult(
            CommitAdmissionResult(
                request.request_id, CommitAdmissionStatus.NOT_STARTED, reason, False,
            ),
            None,
        )


__all__ = [
    "COMMIT_ADMISSION_REQUEST_FORMAT", "COMMIT_ENVIRONMENT_FORMAT",
    "COMMIT_ADMISSION_RESULT_FORMAT", "CommitAdmissionError", "CommitInputKind",
    "CommitRoute",
    "CommitAdmissionStatus", "CommitAdmissionReason", "FrameCommitIdentity",
    "TargetCommitIdentity", "CommitAdmissionRequest", "CommitEnvironment",
    "CommitAdmissionResult", "CommitCallbackResult", "CommitAdmissionGate",
]
