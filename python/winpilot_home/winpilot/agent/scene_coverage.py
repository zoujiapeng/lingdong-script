"""Bounded visual scene coverage and local-loop detection.

The native GUI agent reasons from one authoritative frame at a time.  That is
the right execution contract, but a newest-frame-only model can still forget
that it has already visited the same viewport and oscillate between two or
three old scenes.  This module keeps a small, task- and application-agnostic
ledger of real frames and the spatial actions taken from them.

It deliberately does not infer domain objects (posts, files, game pieces,
settings, ...).  A scene is only a stable visual viewport on one captured
surface; a transition is only an executed action followed by another real
frame.  That makes the same coverage state useful for feeds, long forms,
menus, canvases, maps, games, and arbitrary third-party applications.
"""
from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass, field
from typing import Any

import cv2
import numpy as np


_SCENE_CONTENT_ROI = (20.0, 20.0, 980.0, 930.0)


@dataclass
class SceneRecord:
    scene_id: str
    surface_key: tuple[int, tuple[int, int, int, int]]
    first_image: np.ndarray
    latest_image: np.ndarray
    first_frame_id: int
    last_frame_id: int
    visits: int = 1
    digests: set[str] = field(default_factory=set)
    outgoing_actions: dict[str, dict[str, Any]] = field(default_factory=dict)


class SceneCoverageLedger:
    """Track bounded visual viewport coverage without application semantics."""

    def __init__(
        self,
        *,
        max_scenes: int = 24,
        max_observations: int = 64,
        revisit_change_ratio: float = 0.0012,
    ) -> None:
        self.max_scenes = max(4, int(max_scenes))
        self.max_observations = max(8, int(max_observations))
        self.revisit_change_ratio = max(
            0.0, min(0.01, float(revisit_change_ratio)),
        )
        self._next_scene_number = 1
        self._scenes: dict[str, SceneRecord] = {}
        self._digest_index: dict[
            tuple[tuple[int, tuple[int, int, int, int]], str], str
        ] = {}
        self._observations: list[dict[str, Any]] = []
        self._current_scene_id = ""
        self._current_surface_key: tuple[
            int, tuple[int, int, int, int]
        ] | None = None
        self._since_novel = 0
        self._revisit_count = 0
        self._last_state: dict[str, Any] = {}
        self._scroll_frontiers: dict[
            tuple[int, tuple[int, int, int, int]], dict[str, Any]
        ] = {}

    @staticmethod
    def _surface_key(
        hwnd: int,
        frame_region: tuple[int, int, int, int] | list[int],
    ) -> tuple[int, tuple[int, int, int, int]]:
        region = tuple(int(value) for value in frame_region)
        if len(region) != 4:
            region = (0, 0, 0, 0)
        return int(hwnd), region

    @staticmethod
    def _digest(image_b64: str) -> str:
        return hashlib.sha256(
            image_b64.encode("ascii", errors="ignore"),
        ).hexdigest()[:24]

    @staticmethod
    def _decode_scene_image(image_b64: str) -> np.ndarray | None:
        """Decode one frame once and retain a bounded comparison raster."""
        try:
            raw = np.frombuffer(base64.b64decode(image_b64), dtype=np.uint8)
            image = cv2.imdecode(raw, cv2.IMREAD_COLOR)
            if image is None or image.size == 0:
                return None
            height, width = image.shape[:2]
            scale = min(1.0, 320.0 / max(1, width, height))
            if scale < 1.0:
                image = cv2.resize(
                    image,
                    (max(1, int(width * scale)), max(1, int(height * scale))),
                    interpolation=cv2.INTER_AREA,
                )
            return image
        except Exception:
            return None

    @staticmethod
    def _scroll_direction(args: dict[str, Any] | None) -> str:
        try:
            amount = float((args or {}).get("amount") or 0.0)
        except (TypeError, ValueError):
            return ""
        if amount < 0:
            return "down"
        if amount > 0:
            return "up"
        return ""

    @classmethod
    def action_key(
        cls,
        action: str,
        args: dict[str, Any] | None,
        action_signature: str = "",
    ) -> str:
        kind = str(action or "").partition(":")[0].strip().casefold()
        if kind == "scroll":
            direction = cls._scroll_direction(args)
            if direction:
                # Magnitude and small coordinate jitter must not turn the same
                # explored edge into a supposedly new coverage strategy.  A
                # coarse region remains part of the key so another pane or
                # scrollable surface is still a genuinely available route.
                payload = dict(args or {})
                source = payload.get("source_frame_coordinates")
                if isinstance(source, dict):
                    payload.update(source)
                try:
                    x = float(payload.get("frame_x"))
                    y = float(payload.get("frame_y"))
                    bucket = (
                        max(0, min(3, int(x // 250))),
                        max(0, min(3, int(y // 250))),
                    )
                    return f"scroll:{direction}:r{bucket[0]}{bucket[1]}"
                except (TypeError, ValueError):
                    region = str(payload.get("expected_region") or "").strip().casefold()
                    return f"scroll:{direction}:{region[:40] or 'surface'}"
        return str(action_signature or "").strip() or kind

    def _scene_difference(
        self,
        record: SceneRecord,
        image: np.ndarray,
    ) -> float | None:
        candidates = (record.latest_image, record.first_image)
        best: float | None = None
        for previous in candidates:
            if previous is None or previous.shape != image.shape:
                continue
            delta = cv2.absdiff(previous, image)
            changed = delta.max(axis=2) > 12
            ratio = float(changed.mean()) if changed.size else 1.0
            if ratio > self.revisit_change_ratio:
                height, width = image.shape[:2]
                left, top, right, bottom = _SCENE_CONTENT_ROI
                x1 = max(0, min(width - 1, int(left / 1000.0 * width)))
                y1 = max(0, min(height - 1, int(top / 1000.0 * height)))
                x2 = max(x1 + 1, min(width, int(right / 1000.0 * width)))
                y2 = max(y1 + 1, min(height, int(bottom / 1000.0 * height)))
                central = changed[y1:y2, x1:x2]
                central_ratio = (
                    float(central.mean()) if central.size else None
                )
                # Ignore a blinking clock/caret or tiny system-border update,
                # but require the application content itself to remain almost
                # identical.  The global ceiling prevents a changed window
                # around a blank centre from collapsing into the old scene.
                if (
                    central_ratio is not None
                    and float(central_ratio) <= self.revisit_change_ratio
                    and ratio <= 0.012
                ):
                    ratio = float(central_ratio)
            best = ratio if best is None else min(best, ratio)
        return best

    def _find_scene(
        self,
        *,
        surface_key: tuple[int, tuple[int, int, int, int]],
        image: np.ndarray,
        digest: str,
    ) -> tuple[SceneRecord | None, float | None]:
        exact_id = self._digest_index.get((surface_key, digest), "")
        if exact_id and exact_id in self._scenes:
            return self._scenes[exact_id], 0.0

        candidates = [
            record for record in self._scenes.values()
            if record.surface_key == surface_key
        ]
        candidates.sort(key=lambda item: item.last_frame_id, reverse=True)
        best_record: SceneRecord | None = None
        best_ratio: float | None = None
        # Local loops are recent.  Capping approximate comparisons keeps the
        # bookkeeping cheaper than one perception/model turn even on a long
        # traversal; exact digest matches still cover every retained scene.
        for record in candidates[:10]:
            ratio = self._scene_difference(record, image)
            if ratio is None or ratio > self.revisit_change_ratio:
                continue
            if best_ratio is None or ratio < best_ratio:
                best_record = record
                best_ratio = ratio
        return best_record, best_ratio

    @staticmethod
    def _measure_vertical_translation(
        previous: np.ndarray | None,
        current: np.ndarray | None,
    ) -> dict[str, Any]:
        """Estimate real content motion after a wheel action from pixels.

        ORB matches tolerate a large page jump better than whole-frame phase
        correlation, which wraps at half a frame and is easily dominated by a
        fixed toolbar.  The result is diagnostic/advisory and is emitted only
        when several independent features agree on one vertical displacement.
        """
        if (
            previous is None or current is None
            or previous.shape != current.shape
            or previous.ndim != 3
        ):
            return {}
        height, width = previous.shape[:2]
        top = max(0, int(height * 0.08))
        bottom = min(height, int(height * 0.93))
        left = max(0, int(width * 0.08))
        right = min(width, int(width * 0.94))
        first = cv2.cvtColor(
            previous[top:bottom, left:right], cv2.COLOR_BGR2GRAY,
        )
        second = cv2.cvtColor(
            current[top:bottom, left:right], cv2.COLOR_BGR2GRAY,
        )
        if min(first.shape[:2]) < 40:
            return {}
        try:
            detector = cv2.ORB_create(nfeatures=600, fastThreshold=10)
            first_points, first_desc = detector.detectAndCompute(first, None)
            second_points, second_desc = detector.detectAndCompute(second, None)
            if first_desc is None or second_desc is None:
                return {}
            matcher = cv2.BFMatcher(cv2.NORM_HAMMING)
            pairs = matcher.knnMatch(first_desc, second_desc, k=2)
        except cv2.error:
            return {}
        displacements: list[tuple[float, float]] = []
        for pair in pairs:
            if len(pair) != 2:
                continue
            best, alternate = pair
            if best.distance >= 0.78 * max(1.0, alternate.distance):
                continue
            source = first_points[best.queryIdx].pt
            target = second_points[best.trainIdx].pt
            dx = float(target[0] - source[0])
            dy = float(target[1] - source[1])
            if abs(dx) > width * 0.12 or abs(dy) > height * 0.82:
                continue
            displacements.append((dx, dy))
        if len(displacements) < 8:
            return {}
        dy_values = np.asarray([item[1] for item in displacements], dtype=np.float32)
        median_y = float(np.median(dy_values))
        deviations = np.abs(dy_values - median_y)
        tolerance = max(3.0, float(np.median(deviations)) * 2.5)
        inliers = deviations <= tolerance
        inlier_count = int(np.count_nonzero(inliers))
        if inlier_count < 6:
            return {}
        inlier_ratio = inlier_count / max(1, len(displacements))
        if inlier_ratio < 0.45:
            return {}
        verified_y = float(np.median(dy_values[inliers]))
        return {
            "content_shift_y_px": round(verified_y, 2),
            "content_motion": (
                "up" if verified_y < -2.5
                else "down" if verified_y > 2.5
                else "stationary"
            ),
            "match_count": len(displacements),
            "inlier_count": inlier_count,
            "confidence": round(float(inlier_ratio), 3),
        }

    @staticmethod
    def _scroll_region(action_key: str) -> str:
        parts = str(action_key or "").split(":", 2)
        return parts[2] if len(parts) == 3 and parts[2] else "surface"

    def _update_scroll_frontier(
        self,
        *,
        surface_key: tuple[int, tuple[int, int, int, int]],
        direction: str,
        action_key: str,
        source_scene_id: str,
        target_scene_id: str,
        new_scene: bool,
        frame_id: int,
        motion: dict[str, Any],
    ) -> dict[str, Any]:
        region = self._scroll_region(action_key)
        state = self._scroll_frontiers.setdefault(surface_key, {
            "last_direction": "",
            "last_region": "surface",
            "successful_novel_scrolls": 0,
            "same_direction_novel_streak": 0,
            "no_progress_streak": 0,
            "boundaries": set(),
            "last_scroll_novel": False,
            "last_frame_id": 0,
            "actions_since_scroll": 0,
        })
        edge_id = f"{direction}:{region}"
        moved_to_new_scene = bool(
            new_scene and target_scene_id and target_scene_id != source_scene_id
        )
        if moved_to_new_scene:
            same_frontier = bool(
                state.get("last_direction") == direction
                and state.get("last_region") == region
                and state.get("last_scroll_novel") is True
            )
            state["same_direction_novel_streak"] = (
                int(state.get("same_direction_novel_streak") or 0) + 1
                if same_frontier else 1
            )
            state["successful_novel_scrolls"] = int(
                state.get("successful_novel_scrolls") or 0
            ) + 1
            state["no_progress_streak"] = 0
            state["boundaries"].discard(edge_id)
        else:
            same_frontier = bool(
                state.get("last_direction") == direction
                and state.get("last_region") == region
            )
            state["same_direction_novel_streak"] = 0
            state["no_progress_streak"] = (
                int(state.get("no_progress_streak") or 0) + 1
                if same_frontier else 1
            )
            if int(state["no_progress_streak"]) >= 2:
                state["boundaries"].add(edge_id)
        state.update({
            "last_direction": direction,
            "last_region": region,
            "last_scroll_novel": moved_to_new_scene,
            "last_frame_id": int(frame_id),
            "actions_since_scroll": 0,
            "last_source_scene_id": source_scene_id,
            "last_target_scene_id": target_scene_id,
            "last_motion": dict(motion),
        })
        return self.frontier_recommendation(surface_key)

    def frontier_recommendation(
        self,
        surface_key: tuple[int, tuple[int, int, int, int]] | None = None,
    ) -> dict[str, Any]:
        key = surface_key or self._current_surface_key
        if key is None:
            return {}
        state = self._scroll_frontiers.get(key)
        if not state or not state.get("last_direction"):
            return {}
        direction = str(state.get("last_direction") or "")
        region = str(state.get("last_region") or "surface")
        edge_id = f"{direction}:{region}"
        boundaries = sorted(str(value) for value in state.get("boundaries") or set())
        at_boundary = edge_id in boundaries
        recommended_direction = (
            "up" if direction == "down" else "down"
        ) if at_boundary else direction
        return {
            "active": True,
            "last_direction": direction,
            "last_region": region,
            "last_scroll_novel": bool(state.get("last_scroll_novel")),
            "successful_novel_scrolls": int(
                state.get("successful_novel_scrolls") or 0
            ),
            "same_direction_novel_streak": int(
                state.get("same_direction_novel_streak") or 0
            ),
            "no_progress_streak": int(state.get("no_progress_streak") or 0),
            "actions_since_scroll": int(state.get("actions_since_scroll") or 0),
            "boundary_reached": bool(at_boundary),
            "measured_boundaries": boundaries,
            "recommended_direction": recommended_direction,
            "recommended_amount": -3 if recommended_direction == "down" else 3,
            "reason": (
                "measured_same_direction_boundary"
                if at_boundary
                else "last_wheel_reached_new_viewport"
                if state.get("last_scroll_novel")
                else "retry_frontier_once_then_reverse_if_stationary"
            ),
            "last_motion": dict(state.get("last_motion") or {}),
        }

    def _evict_if_needed(self) -> None:
        while len(self._scenes) > self.max_scenes:
            candidates = [
                record for record in self._scenes.values()
                if record.scene_id != self._current_scene_id
            ]
            if not candidates:
                return
            victim = min(candidates, key=lambda item: item.last_frame_id)
            self._scenes.pop(victim.scene_id, None)
            for digest in tuple(victim.digests):
                self._digest_index.pop((victim.surface_key, digest), None)

    def _cycle_state(
        self,
        surface_key: tuple[int, tuple[int, int, int, int]],
    ) -> dict[str, Any]:
        scene_ids = [
            str(item.get("scene_id") or "")
            for item in self._observations
            if item.get("surface_key") == surface_key
        ]
        compressed: list[str] = []
        for scene_id in scene_ids:
            if scene_id and (not compressed or compressed[-1] != scene_id):
                compressed.append(scene_id)
        compressed = compressed[-10:]
        current = compressed[-1] if compressed else ""
        previous_index = -1
        for index in range(len(compressed) - 2, -1, -1):
            if compressed[index] == current:
                previous_index = index
                break
        cycle = (
            compressed[previous_index:]
            if previous_index >= 0
            else []
        )
        cycle_length = max(0, len(cycle) - 1)
        return {
            "recent_scene_path": compressed,
            "cycle_detected": bool(1 <= cycle_length <= 4),
            "cycle_scene_path": cycle,
            "cycle_length": cycle_length,
        }

    def record_frame(
        self,
        *,
        image_b64: str,
        hwnd: int,
        frame_region: tuple[int, int, int, int] | list[int],
        frame_id: int,
        phase: str,
        action: str = "",
        action_args: dict[str, Any] | None = None,
        action_signature: str = "",
        action_executed: bool = True,
    ) -> dict[str, Any]:
        """Record one initial/post-action authoritative frame."""
        if (
            str(phase or "") not in {"initial", "post_action"}
            or len(str(image_b64 or "")) < 128
        ):
            return {}
        surface_key = self._surface_key(hwnd, frame_region)
        digest = self._digest(image_b64)
        decoded = self._decode_scene_image(image_b64)
        if decoded is None:
            return {}
        previous_scene_id = (
            self._current_scene_id
            if self._current_surface_key == surface_key
            else ""
        )
        previous_image = None
        if previous_scene_id and previous_scene_id in self._scenes:
            previous_image = self._scenes[previous_scene_id].latest_image.copy()
        record, match_ratio = self._find_scene(
            surface_key=surface_key,
            image=decoded,
            digest=digest,
        )
        new_scene = record is None
        if record is None:
            scene_id = f"S{self._next_scene_number}"
            self._next_scene_number += 1
            record = SceneRecord(
                scene_id=scene_id,
                surface_key=surface_key,
                first_image=decoded.copy(),
                latest_image=decoded.copy(),
                first_frame_id=int(frame_id),
                last_frame_id=int(frame_id),
                digests={digest},
            )
            self._scenes[scene_id] = record
            match_ratio = None
            self._since_novel = 0
        else:
            record.visits += 1
            record.latest_image = decoded.copy()
            record.last_frame_id = int(frame_id)
            record.digests.add(digest)
            self._revisit_count += 1
            self._since_novel += 1
        self._digest_index[(surface_key, digest)] = record.scene_id

        action_kind = str(action or "").partition(":")[0].casefold()
        executed_action_key = ""
        if previous_scene_id and action and action_executed:
            executed_action_key = self.action_key(
                action,
                action_args,
                action_signature,
            )
            source = self._scenes.get(previous_scene_id)
            if source is not None and executed_action_key:
                edge = source.outgoing_actions.setdefault(executed_action_key, {
                    "count": 0,
                    "targets": [],
                    "last_frame_id": 0,
                    "action": action_kind,
                })
                edge["count"] = int(edge.get("count") or 0) + 1
                targets = list(edge.get("targets") or [])
                if record.scene_id not in targets:
                    targets.append(record.scene_id)
                edge["targets"] = targets[-4:]
                edge["last_frame_id"] = int(frame_id)

        scroll_direction = (
            self._scroll_direction(action_args)
            if action_kind == "scroll" and action_executed else ""
        )
        scroll_motion: dict[str, Any] = {}
        scroll_frontier: dict[str, Any] = {}
        if scroll_direction:
            scroll_motion = self._measure_vertical_translation(
                previous_image, decoded,
            )
            scroll_frontier = self._update_scroll_frontier(
                surface_key=surface_key,
                direction=scroll_direction,
                action_key=executed_action_key,
                source_scene_id=previous_scene_id,
                target_scene_id=record.scene_id,
                new_scene=bool(new_scene),
                frame_id=int(frame_id),
                motion=scroll_motion,
            )
        elif action and action_executed:
            frontier_state = self._scroll_frontiers.get(surface_key)
            if frontier_state is not None:
                frontier_state["actions_since_scroll"] = int(
                    frontier_state.get("actions_since_scroll") or 0
                ) + 1
                scroll_frontier = self.frontier_recommendation(surface_key)

        observation = {
            "frame_id": int(frame_id),
            "scene_id": record.scene_id,
            "surface_key": surface_key,
            "new_scene": bool(new_scene),
            "revisit": not new_scene,
            "visit_count": int(record.visits),
            "match_change_ratio": (
                round(float(match_ratio), 8)
                if match_ratio is not None else None
            ),
            "action": action_kind,
            "action_key": executed_action_key,
            "action_executed": bool(action_executed),
            "scroll_direction": scroll_direction,
        }
        self._observations.append(observation)
        self._observations = self._observations[-self.max_observations:]
        self._current_scene_id = record.scene_id
        self._current_surface_key = surface_key
        self._evict_if_needed()

        cycle = self._cycle_state(surface_key)
        unique_on_surface = sum(
            1 for item in self._scenes.values()
            if item.surface_key == surface_key
        )
        state = {
            "scene_id": record.scene_id,
            "frame_id": int(frame_id),
            "new_scene": bool(new_scene),
            "revisit": not new_scene,
            "visit_count": int(record.visits),
            "match_change_ratio": observation["match_change_ratio"],
            "unique_scene_count": int(unique_on_surface),
            "revisit_count": int(self._revisit_count),
            "actions_since_new_scene": int(self._since_novel),
            "surface_hwnd": int(surface_key[0]),
            "frame_region": list(surface_key[1]),
            "action": action_kind,
            "action_key": executed_action_key,
            "action_executed": bool(action_executed),
            "scroll_direction": scroll_direction,
            "scroll_motion": scroll_motion,
            "scroll_frontier": scroll_frontier,
            **cycle,
        }
        self._last_state = state
        return dict(state)

    def guard_repeated_edge(
        self,
        *,
        action: str,
        action_args: dict[str, Any] | None,
        action_signature: str = "",
        explicit_repeat: bool = False,
    ) -> dict[str, Any] | None:
        """Block only a proven repeated edge in a measured visual cycle.

        Scroll edges can also be rejected after repeated non-novel frames.
        Click edges require the stricter cross-scene cycle proof: repeated
        clicking within one otherwise stable game/application frame remains
        available.  Drags, drawing and held-pointer motion are never blocked
        here because their evolving geometry is itself the interaction.
        """
        if explicit_repeat or not self._last_state:
            return None
        kind = str(action or "").partition(":")[0].strip().casefold()
        if (
            kind not in {"click", "scroll"}
            or not self._last_state.get("revisit")
        ):
            return None
        current = self._scenes.get(self._current_scene_id)
        if current is None:
            return None
        key = self.action_key(action, action_args, action_signature)
        prior = current.outgoing_actions.get(key)
        if not key or not prior:
            return None
        strong_loop_evidence = bool(self._last_state.get("cycle_detected"))
        if kind == "scroll":
            strong_loop_evidence = bool(
                strong_loop_evidence
                or int(self._last_state.get("actions_since_new_scene") or 0) >= 2
            )
        if not strong_loop_evidence:
            return None
        direction = self._scroll_direction(action_args) if kind == "scroll" else ""
        return {
            "ok": False,
            "verified": False,
            "blocked": True,
            "action_executed": False,
            "scene_coverage_guard": True,
            "strategy_change_required": True,
            "current_scene_id": current.scene_id,
            "current_scene_visit_count": int(current.visits),
            "proposed_edge": key,
            "proposed_scroll_direction": direction,
            "prior_edge_count": int(prior.get("count") or 0),
            "prior_target_scene_ids": list(prior.get("targets") or []),
            "actions_since_new_scene": int(
                self._last_state.get("actions_since_new_scene") or 0
            ),
            "cycle_scene_path": list(
                self._last_state.get("cycle_scene_path") or []
            ),
            "error": (
                "The authoritative pixels match a previously visited scene, and this "
                "same spatial action from that scene already traversed a known edge. "
                "Repeating it would re-enter the measured local coverage loop, so no input "
                "was sent. Continue toward a different visual frontier, use another "
                "scrollable region/navigation route, act on a different visible target, or "
                "finish only when fresh evidence proves the requested coverage is complete."
            ),
            "needs_replan": True,
        }

    def context_summary(self) -> str:
        state = self._last_state
        if not state or int(state.get("frame_id") or 0) <= 1:
            return ""
        line = (
            "Scene coverage ledger (real pixels, application-agnostic): "
            f"current={state.get('scene_id')}, visits={state.get('visit_count')}, "
            f"unique_viewports={state.get('unique_scene_count')}, "
            f"revisits={state.get('revisit_count')}, "
            f"actions_since_new_viewport={state.get('actions_since_new_scene')}."
        )
        if state.get("revisit"):
            line += (
                " The current authoritative frame matches an already visited viewport; "
                "do not count this return as coverage progress."
            )
        if state.get("cycle_detected"):
            path = " -> ".join(state.get("cycle_scene_path") or [])
            line += (
                f" A recent visual cycle is measured ({path}). Do not replay an explored "
                "scene/action edge; choose an unseen frontier, a different visible target, "
                "or a different scrollable/navigation surface."
            )
        frontier = self.frontier_recommendation()
        if frontier:
            if frontier.get("boundary_reached"):
                line += (
                    " Scroll dynamics measured the current "
                    f"{frontier.get('last_direction')} edge as stationary twice. "
                    f"Use amount={frontier.get('recommended_amount')} to move "
                    f"{frontier.get('recommended_direction')} in the same region, or "
                    "choose another visible scrollable surface; do not repeat the old sign."
                )
            elif int(frontier.get("successful_novel_scrolls") or 0) > 0:
                line += (
                    " Measured scroll frontier: the last amount sign moved to a new "
                    f"viewport ({frontier.get('last_direction')}, region="
                    f"{frontier.get('last_region')}). After current visible targets, "
                    f"continue amount={frontier.get('recommended_amount')} in that same "
                    "region. Do not reopen or rediscover the application while this "
                    "pixel-verified frontier remains available."
                )
        return line

    @property
    def last_state(self) -> dict[str, Any]:
        return dict(self._last_state)
