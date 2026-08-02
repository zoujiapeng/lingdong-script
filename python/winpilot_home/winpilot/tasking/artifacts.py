"""Managed, run-scoped artifacts distinct from transient perception frames."""
from __future__ import annotations

import hashlib
from io import BytesIO
import json
from pathlib import Path
import shutil
import threading
import time
from typing import Any
import uuid

from pydantic import BaseModel, Field

from ..config import TRACES_DIR
from ..perception import win32
from ..utils.screenshot import Region, capture, monitor_regions, window_region


class Artifact(BaseModel):
    artifact_id: str
    run_id: str
    managed_path: str
    sha256: str
    mime_type: str
    width: int = 0
    height: int = 0
    size_bytes: int = 0
    source_window: str = ""
    source_hwnd: int = 0
    source_scope: str = ""
    created_at: float = Field(default_factory=time.time)
    selected_at: float = 0.0
    purpose: str = "user_output"
    sendable: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)


def _mime_for(path: Path) -> str:
    return {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".bmp": "image/bmp",
        ".webp": "image/webp",
        ".pdf": "application/pdf",
        ".txt": "text/plain",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    }.get(path.suffix.casefold(), "application/octet-stream")


def _window_title(hwnd: int) -> str:
    if not hwnd:
        return ""
    for item in win32.list_windows():
        try:
            if int(item.get("hwnd") or 0) == int(hwnd):
                return str(item.get("title") or "")
        except (TypeError, ValueError):
            continue
    return ""


class ArtifactStore:
    """Own every file passed between task stages.

    Observation frames can be retained for audit but have ``sendable=False``;
    ``require_sendable`` therefore prevents a model-facing screenshot from
    silently becoming the user's outbound attachment.
    """

    def __init__(self, run_id: str, root: str | Path | None = None) -> None:
        self.run_id = str(run_id)
        self.root = (Path(root) if root else TRACES_DIR / self.run_id / "artifacts").resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._artifacts: dict[str, Artifact] = {}
        self._lock = threading.RLock()
        self._manifest = self.root / "manifest.json"
        self._load_manifest()

    def _load_manifest(self) -> None:
        """Restore a persisted run store without trusting arbitrary file paths.

        Artifacts outlive the Python process so the history UI and completion
        audit can still inspect them after a restart.  The manifest is treated
        as untrusted input: records for another run, malformed records, and
        paths escaping this run's managed directory are ignored.  ``get``
        performs the expensive size/hash/dimension verification on access.
        """
        if not self._manifest.is_file():
            return
        try:
            payload = json.loads(self._manifest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if (
            not isinstance(payload, dict)
            or payload.get("format") != "artifact-store/1"
            or str(payload.get("run_id") or "") != self.run_id
        ):
            return
        loaded: dict[str, Artifact] = {}
        for raw in payload.get("artifacts") or []:
            try:
                artifact = Artifact.model_validate(raw)
                if artifact.run_id != self.run_id:
                    continue
                managed_path = Path(artifact.managed_path).resolve()
                managed_path.relative_to(self.root)
                artifact.managed_path = str(managed_path)
                loaded[artifact.artifact_id] = artifact
            except (OSError, TypeError, ValueError):
                continue
        self._artifacts = loaded

    @staticmethod
    def _hash(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()

    @staticmethod
    def _dimensions(path: Path) -> tuple[int, int]:
        if path.suffix.casefold() not in {".png", ".jpg", ".jpeg", ".bmp", ".webp"}:
            return 0, 0
        try:
            from PIL import Image
            with Image.open(path) as image:
                return int(image.width), int(image.height)
        except Exception:
            return 0, 0

    def _persist(self) -> None:
        payload = {
            "format": "artifact-store/1",
            "run_id": self.run_id,
            "artifacts": [item.model_dump(mode="json") for item in self._artifacts.values()],
        }
        self._manifest.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _register_path(
        self,
        path: Path,
        *,
        artifact_id: str = "",
        source_window: str = "",
        source_hwnd: int = 0,
        source_scope: str = "",
        purpose: str = "user_output",
        sendable: bool = True,
        selected_at: float = 0.0,
        metadata: dict[str, Any] | None = None,
    ) -> Artifact:
        path = path.resolve()
        try:
            path.relative_to(self.root)
        except ValueError as exc:
            raise ValueError("artifact path must stay inside the run-scoped store") from exc
        if not path.is_file() or path.stat().st_size <= 0:
            raise ValueError(f"artifact file is missing or empty: {path}")
        aid = artifact_id or f"art-{uuid.uuid4().hex[:16]}"
        width, height = self._dimensions(path)
        artifact = Artifact(
            artifact_id=aid,
            run_id=self.run_id,
            managed_path=str(path),
            sha256=self._hash(path),
            mime_type=_mime_for(path),
            width=width,
            height=height,
            size_bytes=path.stat().st_size,
            source_window=source_window,
            source_hwnd=int(source_hwnd or 0),
            source_scope=source_scope,
            purpose=purpose,
            sendable=bool(sendable),
            selected_at=float(selected_at or 0.0),
            metadata=dict(metadata or {}),
        )
        with self._lock:
            self._artifacts[aid] = artifact
            self._persist()
        return artifact

    def capture_artifact(
        self,
        *,
        scope: str = "window",
        source_hwnd: int = 0,
        region: Region | dict[str, int] | None = None,
        source_window: str = "",
        purpose: str = "user_output",
        sendable: bool = True,
        metadata: dict[str, Any] | None = None,
    ) -> Artifact:
        scope = str(scope or "").strip().casefold()
        if scope not in {"window", "screen", "canvas", "region"}:
            raise ValueError("artifact scope must be one of window, screen, canvas, or region")
        resolved_region: Region | None
        if region is not None:
            if scope != "region":
                raise ValueError("explicit artifact region requires scope='region'")
            if isinstance(region, dict):
                has_x = "x" in region
                has_left = "left" in region
                has_y = "y" in region
                has_top = "top" in region
                if has_x == has_left or has_y == has_top:
                    raise ValueError("artifact region requires exactly one x/left and one y/top")
                values = (
                    region["x"] if has_x else region["left"],
                    region["y"] if has_y else region["top"],
                    region.get("width"),
                    region.get("height"),
                )
                if not all(type(value) is int for value in values):
                    raise ValueError("artifact region coordinates and dimensions must be integers")
                resolved_region = Region(*values)
            elif isinstance(region, Region):
                values = (region.left, region.top, region.width, region.height)
                if not all(type(value) is int for value in values):
                    raise ValueError("artifact region coordinates and dimensions must be integers")
                resolved_region = region
            else:
                raise ValueError("artifact region must be an object or Region")
            if resolved_region.width <= 0 or resolved_region.height <= 0:
                raise ValueError("artifact region width and height must be positive")
            owners = [
                monitor for monitor in monitor_regions()
                if (
                    resolved_region.left >= monitor.left
                    and resolved_region.top >= monitor.top
                    and resolved_region.right <= monitor.right
                    and resolved_region.bottom <= monitor.bottom
                )
            ]
            if len(owners) != 1:
                raise ValueError("artifact region must be fully owned by exactly one connected display")
        elif scope == "region":
            raise ValueError("scope='region' requires an explicit artifact region")
        elif scope in {"window", "canvas"}:
            if not source_hwnd:
                raise ValueError("window artifact capture requires source_hwnd")
            resolved_region = window_region(int(source_hwnd)).clamp_to_screen()
        else:
            resolved_region = None
        frame = capture(resolved_region)
        if getattr(frame, "size", 0) <= 0:
            raise ValueError("captured artifact frame is empty")
        if resolved_region is not None:
            frame_height, frame_width = frame.shape[:2]
            if frame_width != resolved_region.width or frame_height != resolved_region.height:
                raise ValueError("captured artifact frame dimensions do not match the resolved region")
        canvas_bbox: list[int] | None = None
        if scope == "canvas" and region is None:
            from ..perception.vision_cv import likely_canvas_bbox

            canvas_bbox = likely_canvas_bbox(frame)
            if canvas_bbox is None:
                raise ValueError("no credible canvas/work surface was found in the source window")
            x1, y1, x2, y2 = [int(value) for value in canvas_bbox]
            if x2 <= x1 or y2 <= y1:
                raise ValueError("detected canvas/work-surface geometry was empty")
            frame = frame[y1:y2, x1:x2]
            # Persist the absolute screen region as provenance, while retaining
            # the detector-relative box for auditing and reproducibility.
            resolved_region = Region(
                resolved_region.left + x1,
                resolved_region.top + y1,
                x2 - x1,
                y2 - y1,
            ).clamp_to_screen()
        from PIL import Image
        image = Image.fromarray(frame[:, :, ::-1].copy())
        aid = f"art-{uuid.uuid4().hex[:16]}"
        path = self.root / f"{aid}.png"
        payload = BytesIO()
        image.save(payload, format="PNG", optimize=True)
        path.write_bytes(payload.getvalue())
        return self._register_path(
            path,
            artifact_id=aid,
            source_window=source_window or _window_title(source_hwnd),
            source_hwnd=source_hwnd,
            source_scope=scope,
            purpose=purpose,
            sendable=sendable,
            metadata={
                **dict(metadata or {}),
                "region": {
                    "left": resolved_region.left,
                    "top": resolved_region.top,
                    "width": resolved_region.width,
                    "height": resolved_region.height,
                } if resolved_region else None,
                "canvas_bbox_in_window": canvas_bbox,
            },
        )

    def capture_observation(self, **kwargs: Any) -> Artifact:
        return self.capture_artifact(purpose="observation", sendable=False, **kwargs)

    def import_file(
        self,
        path: str | Path,
        *,
        purpose: str = "selected_input",
        sendable: bool = True,
        metadata: dict[str, Any] | None = None,
    ) -> Artifact:
        source = Path(path).expanduser().resolve()
        if not source.is_file() or source.stat().st_size <= 0:
            raise ValueError(f"artifact file is missing or empty: {source}")
        aid = f"art-{uuid.uuid4().hex[:16]}"
        suffix = source.suffix.casefold() or ".bin"
        target = self.root / f"{aid}{suffix}"
        shutil.copy2(source, target)
        return self._register_path(
            target,
            artifact_id=aid,
            source_window="external selection",
            source_scope="selected_file",
            purpose=purpose,
            sendable=sendable,
            selected_at=time.time(),
            metadata={**dict(metadata or {}), "original_path": str(source)},
        )

    def get(self, artifact_id: str, *, require_sendable: bool = False) -> Artifact:
        with self._lock:
            artifact = self._artifacts.get(str(artifact_id))
        if artifact is None:
            raise KeyError(f"unknown artifact_id for current run: {artifact_id}")
        if artifact.run_id != self.run_id:
            raise ValueError("artifact does not belong to the current run")
        path = Path(artifact.managed_path).resolve()
        try:
            path.relative_to(self.root)
        except ValueError as exc:
            raise ValueError("artifact path escaped the run-scoped store") from exc
        if not path.is_file() or path.stat().st_size <= 0:
            raise ValueError("artifact file no longer exists or is empty")
        if path.stat().st_size != int(artifact.size_bytes):
            raise ValueError("artifact size changed after registration")
        if self._hash(path) != artifact.sha256:
            raise ValueError("artifact hash changed after registration")
        current_dimensions = self._dimensions(path)
        if current_dimensions != (int(artifact.width), int(artifact.height)):
            raise ValueError("artifact dimensions changed after registration")
        if require_sendable and not artifact.sendable:
            raise ValueError("perception/observation frames cannot be sent as task artifacts")
        return artifact

    def resolve_sendable(
        self,
        artifact_id: str = "",
        *,
        purpose: str = "user_output",
    ) -> tuple[Artifact, dict[str, Any]]:
        """Resolve a model-facing artifact handle without guessing ambiguously.

        The exact returned ``artifact_id`` remains the primary handle.  A GUI
        model can, however, lose that opaque token when old tool messages are
        compacted.  If this run has exactly one valid sendable user artifact,
        it is deterministic to recover that artifact from a missing or stale
        alias.  Multiple candidates are never silently collapsed to "latest".
        """

        requested = str(artifact_id or "").strip()
        if requested:
            try:
                exact = self.get(requested, require_sendable=True)
            except KeyError:
                exact = None
            if exact is not None:
                return exact, {
                    "strategy": "exact_current_run_artifact_id",
                    "requested_artifact_id": requested,
                    "resolved_artifact_id": exact.artifact_id,
                    "candidate_count": 1,
                }

        with self._lock:
            registered = list(self._artifacts.values())
        preferred = [
            item
            for item in registered
            if item.sendable and (not purpose or item.purpose == purpose)
        ]
        candidates = preferred or [item for item in registered if item.sendable]
        valid: list[Artifact] = []
        for candidate in candidates:
            try:
                valid.append(self.get(candidate.artifact_id, require_sendable=True))
            except (KeyError, ValueError):
                continue

        if len(valid) == 1:
            resolved = valid[0]
            return resolved, {
                "strategy": "sole_current_run_sendable_artifact",
                "requested_artifact_id": requested,
                "resolved_artifact_id": resolved.artifact_id,
                "candidate_count": 1,
                "alias_recovered": bool(requested and requested != resolved.artifact_id),
            }
        if not valid:
            raise KeyError("no valid sendable artifact exists in the current run")
        available = [item.artifact_id for item in valid[:8]]
        raise KeyError(
            "artifact_id is ambiguous for the current run; use one exact returned id from "
            f"{available}"
        )

    def latest(self, *, sendable: bool | None = None, purpose: str = "") -> Artifact | None:
        with self._lock:
            items = list(self._artifacts.values())
        filtered = [
            item for item in items
            if (sendable is None or item.sendable is sendable)
            and (not purpose or item.purpose == purpose)
        ]
        return max(filtered, key=lambda item: item.created_at, default=None)

    def artifacts(self) -> list[dict[str, Any]]:
        with self._lock:
            return [item.model_dump(mode="json") for item in self._artifacts.values()]

    def visual_match(
        self,
        source_id: str,
        frame_id: str,
        *,
        target_region: dict[str, int] | None = None,
        baseline_frame_id: str = "",
        require_changed: bool = False,
        min_scale: float = 0.04,
    ) -> dict[str, Any]:
        """Match an artifact in a stage-owned ROI of a later evidence frame.

        Chat clients routinely resize raster attachments, so both geometric
        feature matching and multi-scale edge templates are used.  When a
        pre-send baseline is supplied, a match must also occupy pixels that
        changed after the send action; this prevents a composer preview or an
        older identical bubble from satisfying delivery.
        """
        source = self.get(source_id, require_sendable=True)
        frame = self.get(frame_id)
        try:
            import cv2
            import numpy as np
            min_scale = max(0.02, min(1.0, float(min_scale or 0.04)))
            src = cv2.imread(source.managed_path, cv2.IMREAD_GRAYSCALE)
            dst = cv2.imread(frame.managed_path, cv2.IMREAD_GRAYSCALE)
            if src is None or dst is None:
                raise ValueError("OpenCV could not decode artifact images")
            offset_x = offset_y = 0
            if isinstance(target_region, dict):
                left = max(0, int(target_region.get("left", target_region.get("x", 0))))
                top = max(0, int(target_region.get("top", target_region.get("y", 0))))
                right = min(dst.shape[1], left + max(0, int(target_region.get("width", 0))))
                bottom = min(dst.shape[0], top + max(0, int(target_region.get("height", 0))))
                if right <= left or bottom <= top:
                    raise ValueError("target visual-match region is empty")
                dst = dst[top:bottom, left:right]
                offset_x, offset_y = left, top

            baseline = None
            if baseline_frame_id:
                baseline_artifact = self.get(baseline_frame_id)
                baseline = cv2.imread(baseline_artifact.managed_path, cv2.IMREAD_GRAYSCALE)
                if baseline is not None and isinstance(target_region, dict):
                    baseline = baseline[
                        offset_y:offset_y + dst.shape[0],
                        offset_x:offset_x + dst.shape[1],
                    ]
                if baseline is not None and baseline.shape != dst.shape:
                    baseline = None
            if require_changed and baseline is None:
                raise ValueError("changed-after-send matching requires an aligned baseline frame")

            def change_at(box: tuple[int, int, int, int]) -> tuple[bool, float, float]:
                if baseline is None:
                    return True, 1.0, 255.0
                x, y, width, height = box
                x = max(0, min(dst.shape[1], x))
                y = max(0, min(dst.shape[0], y))
                width = max(0, min(dst.shape[1] - x, width))
                height = max(0, min(dst.shape[0] - y, height))
                if width <= 0 or height <= 0:
                    return False, 0.0, 0.0
                diff = cv2.absdiff(dst[y:y + height, x:x + width], baseline[y:y + height, x:x + width])
                ratio = float(np.mean(diff >= 18))
                mean_delta = float(np.mean(diff))
                return ratio >= 0.025 and mean_delta >= 2.0, ratio, mean_delta

            detector = cv2.ORB_create(nfeatures=1800, scaleFactor=1.2, nlevels=8)
            kp1, des1 = detector.detectAndCompute(src, None)
            kp2, des2 = detector.detectAndCompute(dst, None)
            good = []
            if des1 is not None and des2 is not None:
                matcher = cv2.BFMatcher(cv2.NORM_HAMMING)
                pairs = matcher.knnMatch(des1, des2, k=2)
                good = [pair[0] for pair in pairs if len(pair) == 2 and pair[0].distance < 0.76 * pair[1].distance]

            geometric_match = False
            geometric_inliers = 0
            geometric_box: tuple[int, int, int, int] | None = None
            geometric_change = (False, 0.0, 0.0)
            if len(good) >= 6:
                src_points = np.float32([kp1[item.queryIdx].pt for item in good]).reshape(-1, 1, 2)
                dst_points = np.float32([kp2[item.trainIdx].pt for item in good]).reshape(-1, 1, 2)
                homography, mask = cv2.findHomography(src_points, dst_points, cv2.RANSAC, 4.0)
                if homography is not None and mask is not None:
                    geometric_inliers = int(mask.ravel().sum())
                    corners = np.float32([
                        [0, 0], [src.shape[1], 0],
                        [src.shape[1], src.shape[0]], [0, src.shape[0]],
                    ]).reshape(-1, 1, 2)
                    projected = cv2.perspectiveTransform(corners, homography).reshape(-1, 2)
                    x1 = int(np.floor(projected[:, 0].min()))
                    y1 = int(np.floor(projected[:, 1].min()))
                    x2 = int(np.ceil(projected[:, 0].max()))
                    y2 = int(np.ceil(projected[:, 1].max()))
                    if x2 > x1 and y2 > y1:
                        geometric_box = (x1, y1, x2 - x1, y2 - y1)
                        geometric_change = change_at(geometric_box)
                        width_ratio = (x2 - x1) / max(1, src.shape[1])
                        height_ratio = (y2 - y1) / max(1, src.shape[0])
                        aspect_ratio = (
                            ((x2 - x1) / max(1, y2 - y1))
                            / (src.shape[1] / max(1, src.shape[0]))
                        )
                        mostly_inside = (
                            x2 > 0 and y2 > 0
                            and x1 < dst.shape[1] and y1 < dst.shape[0]
                            and x1 >= -0.10 * (x2 - x1)
                            and y1 >= -0.10 * (y2 - y1)
                            and x2 <= dst.shape[1] + 0.10 * (x2 - x1)
                            and y2 <= dst.shape[0] + 0.10 * (y2 - y1)
                        )
                        geometric_match = (
                            geometric_inliers >= 8
                            and geometric_inliers / max(1, len(good)) >= 0.25
                            and width_ratio >= min_scale
                            and height_ratio >= min_scale * 0.65
                            and 0.45 <= aspect_ratio <= 2.20
                            and mostly_inside
                            and (not require_changed or geometric_change[0])
                        )

            # A geometrically consistent homography already proves identity,
            # scale, placement, and (when required) post-send change.  Do not
            # pay for fourteen multi-scale template searches after all of the
            # stronger conditions have passed; sparse images still fall
            # through to the edge-template path below.
            if geometric_match and geometric_box is not None:
                return {
                    "matched": True,
                    "good_matches": len(good),
                    "geometric_inliers": geometric_inliers,
                    "geometric_match": True,
                    "geometric_bbox": [
                        geometric_box[0] + offset_x,
                        geometric_box[1] + offset_y,
                        geometric_box[2],
                        geometric_box[3],
                    ],
                    "geometric_change_ratio": round(float(geometric_change[1]), 4),
                    "geometric_mean_pixel_delta": round(float(geometric_change[2]), 3),
                    "source_keypoints": len(kp1 or []),
                    "frame_keypoints": len(kp2 or []),
                    "template_score": 0.0,
                    "template_scale": 0.0,
                    "match_bbox": [
                        geometric_box[0] + offset_x,
                        geometric_box[1] + offset_y,
                        geometric_box[2],
                        geometric_box[3],
                    ],
                    "target_region": dict(target_region or {}),
                    "min_scale": round(min_scale, 4),
                    "baseline_frame_id": baseline_frame_id,
                    "changed_after_send": bool(geometric_change[0]) if baseline is not None else None,
                    "change_ratio": round(float(geometric_change[1]), 4),
                    "mean_pixel_delta": round(float(geometric_change[2]), 3),
                    "method": "orb_homography",
                }

            # Chat clients resize attachments. ORB is strong on textured
            # images but sparse drawings may have too few keypoints, so add a
            # subject-agnostic multi-scale edge comparison.
            src_edge = cv2.Canny(src, 55, 155)
            dst_edge = cv2.Canny(dst, 55, 155)
            max_scale = min(
                1.0,
                dst.shape[1] / max(1, src.shape[1]),
                dst.shape[0] / max(1, src.shape[0]),
            )
            template_score = 0.0
            template_scale = 0.0
            template_box: tuple[int, int, int, int] | None = None
            template_change = (False, 0.0, 0.0)
            if max_scale >= min_scale and float(src_edge.std()) > 1.0:
                for raw_scale in (0.04, 0.055, 0.07, 0.10, 0.14, 0.18, 0.20, 0.24, 0.32, 0.42, 0.55, 0.70, 0.85, 1.0):
                    scale = min(max_scale, raw_scale)
                    if scale < min_scale:
                        continue
                    width = max(10, int(src_edge.shape[1] * scale))
                    height = max(10, int(src_edge.shape[0] * scale))
                    if width > dst_edge.shape[1] or height > dst_edge.shape[0]:
                        continue
                    templ = cv2.resize(src_edge, (width, height), interpolation=cv2.INTER_AREA)
                    score_map = cv2.matchTemplate(dst_edge, templ, cv2.TM_CCOEFF_NORMED)
                    # Inspect several separated peaks.  The strongest match may
                    # be an older identical outbound bubble; a newly changed
                    # candidate is the one that proves this send action.
                    for _candidate in range(5):
                        _minimum, score, _min_loc, location = cv2.minMaxLoc(score_map)
                        score = float(score)
                        box = (int(location[0]), int(location[1]), width, height)
                        changed = change_at(box)
                        eligible = not require_changed or changed[0]
                        if eligible and score > template_score:
                            template_score = score
                            template_scale = scale
                            template_box = box
                            template_change = changed
                        x1 = max(0, int(location[0]) - width // 2)
                        y1 = max(0, int(location[1]) - height // 2)
                        x2 = min(score_map.shape[1], int(location[0]) + width // 2 + 1)
                        y2 = min(score_map.shape[0], int(location[1]) + height // 2 + 1)
                        score_map[y1:y2, x1:x2] = -1.0
                    if raw_scale >= max_scale:
                        break
            template_match = template_score >= 0.45
            matched = geometric_match or template_match
            selected_box = geometric_box if geometric_match else template_box
            selected_change = geometric_change if geometric_match else template_change
            return {
                "matched": matched,
                "good_matches": len(good),
                "geometric_inliers": geometric_inliers,
                "geometric_match": geometric_match,
                "geometric_bbox": (
                    [
                        geometric_box[0] + offset_x,
                        geometric_box[1] + offset_y,
                        geometric_box[2],
                        geometric_box[3],
                    ]
                    if geometric_box is not None else []
                ),
                "geometric_change_ratio": round(float(geometric_change[1]), 4),
                "geometric_mean_pixel_delta": round(float(geometric_change[2]), 3),
                "source_keypoints": len(kp1 or []),
                "frame_keypoints": len(kp2 or []),
                "template_score": round(template_score, 4),
                "template_scale": round(template_scale, 4),
                "match_bbox": (
                    [
                        selected_box[0] + offset_x,
                        selected_box[1] + offset_y,
                        selected_box[2],
                        selected_box[3],
                    ]
                    if selected_box is not None else []
                ),
                "target_region": dict(target_region or {}),
                "min_scale": round(min_scale, 4),
                "baseline_frame_id": baseline_frame_id,
                "changed_after_send": bool(selected_change[0]) if baseline is not None else None,
                "change_ratio": round(float(selected_change[1]), 4),
                "mean_pixel_delta": round(float(selected_change[2]), 3),
                "method": "orb_homography+multiscale_edge_template",
            }
        except Exception as exc:
            return {"matched": False, "good_matches": 0, "reason": str(exc), "method": "unavailable"}

    def frame_change(
        self,
        baseline_frame_id: str,
        current_frame_id: str,
        *,
        target_region: dict[str, int] | None = None,
        pixel_threshold: int = 18,
        min_change_ratio: float = 0.025,
        min_mean_delta: float = 2.0,
        allow_coherent_region: bool = False,
    ) -> dict[str, Any]:
        """Prove that a stage-owned screen region changed between two frames.

        This is intentionally separate from artifact identity matching.  It is
        useful for causal UI transitions such as a newly opened attachment
        confirmation, while ``visual_match`` remains responsible for proving
        that a sent message bubble corresponds to a particular artifact.
        """
        baseline_artifact = self.get(baseline_frame_id)
        current_artifact = self.get(current_frame_id)
        try:
            import cv2
            import numpy as np

            baseline = cv2.imread(baseline_artifact.managed_path, cv2.IMREAD_GRAYSCALE)
            current = cv2.imread(current_artifact.managed_path, cv2.IMREAD_GRAYSCALE)
            if baseline is None or current is None:
                raise ValueError("OpenCV could not decode comparison frames")
            if baseline.shape != current.shape:
                raise ValueError("comparison frames are not geometrically aligned")

            region = dict(target_region or {})
            left = max(0, int(region.get("left", region.get("x", 0))))
            top = max(0, int(region.get("top", region.get("y", 0))))
            width = int(region.get("width", current.shape[1] - left))
            height = int(region.get("height", current.shape[0] - top))
            right = min(current.shape[1], left + max(0, width))
            bottom = min(current.shape[0], top + max(0, height))
            if right <= left or bottom <= top:
                raise ValueError("frame-change region is empty")

            delta = cv2.absdiff(
                current[top:bottom, left:right],
                baseline[top:bottom, left:right],
            )
            threshold = max(1, min(255, int(pixel_threshold)))
            ratio = float(np.mean(delta >= threshold))
            mean_delta = float(np.mean(delta))
            ratio_floor = max(0.0, min(1.0, float(min_change_ratio)))
            mean_floor = max(0.0, float(min_mean_delta))
            coherent_change = self._coherent_frame_change(
                delta,
                threshold=threshold,
                ratio_floor=ratio_floor,
            )
            globally_changed = ratio >= ratio_floor and mean_delta >= mean_floor
            return {
                "changed": bool(
                    globally_changed
                    or (
                        allow_coherent_region
                        and ratio >= ratio_floor
                        and coherent_change.get("detected") is True
                    )
                ),
                "change_ratio": round(ratio, 4),
                "mean_pixel_delta": round(mean_delta, 3),
                "pixel_threshold": threshold,
                "min_change_ratio": round(ratio_floor, 4),
                "min_mean_delta": round(mean_floor, 3),
                "target_region": {
                    "left": left,
                    "top": top,
                    "width": right - left,
                    "height": bottom - top,
                },
                "baseline_frame_id": baseline_frame_id,
                "current_frame_id": current_frame_id,
                "coherent_change": coherent_change,
                "coherent_region_allowed": bool(allow_coherent_region),
                "method": "aligned_frame_delta",
            }
        except Exception as exc:
            return {
                "changed": False,
                "change_ratio": 0.0,
                "mean_pixel_delta": 0.0,
                "reason": str(exc),
                "method": "unavailable",
            }

    @staticmethod
    def _coherent_frame_change(
        delta: Any,
        *,
        threshold: int,
        ratio_floor: float,
    ) -> dict[str, Any]:
        """Find a spatially coherent changed patch inside a larger ROI.

        Remote UI acknowledgement often changes a compact banner, dialog, or
        status label while the rest of a full-window search region remains
        identical.  A whole-ROI mean dilutes that evidence.  Conversely,
        scattered capture noise must not become causal proof, so this detector
        requires a connected component with both absolute pixel support and
        meaningful density inside its own bounding box.
        """
        import cv2
        import numpy as np

        mask = (delta >= threshold).astype(np.uint8)
        region_pixels = max(1, int(mask.size))
        minimum_pixels = max(
            24,
            int(np.ceil(region_pixels * max(0.00035, ratio_floor * 0.25))),
        )
        component_count, _labels, stats, _centroids = (
            cv2.connectedComponentsWithStats(mask, connectivity=8)
        )
        candidates: list[dict[str, Any]] = []
        for index in range(1, component_count):
            left, top, width, height, pixels = [
                int(value) for value in stats[index]
            ]
            if pixels < minimum_pixels or width <= 0 or height <= 0:
                continue
            component_region = delta[top:top + height, left:left + width]
            component_mask = mask[top:top + height, left:left + width]
            density = float(np.mean(component_mask))
            local_mean = float(np.mean(component_region))
            if density < 0.025 or local_mean < 2.0:
                continue
            candidates.append({
                "bounds": {
                    "left": left,
                    "top": top,
                    "width": width,
                    "height": height,
                },
                "changed_pixels": pixels,
                "density": round(density, 4),
                "mean_pixel_delta": round(local_mean, 3),
            })
        best = max(
            candidates,
            key=lambda item: (
                int(item["changed_pixels"]),
                float(item["density"]),
                float(item["mean_pixel_delta"]),
            ),
            default=None,
        )
        return {
            "detected": best is not None,
            "minimum_changed_pixels": minimum_pixels,
            "candidate_count": len(candidates),
            "best": best,
        }

    def snapshot(self) -> dict[str, Any]:
        items = self.artifacts()
        return {"run_id": self.run_id, "root": str(self.root), "count": len(items), "artifacts": items}
