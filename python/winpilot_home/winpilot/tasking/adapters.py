"""Pluggable communication domain adapters.

Adapters own application-specific navigation fallbacks.  They expose semantic
operations to the stage runtime and never encode a recipient or screen
coordinate in production code.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
import json
import re
import time
from types import SimpleNamespace
from typing import Any

from ..agent.runtime_profile import RuntimeProfile
from ..agent.tools import ToolSession
from ..perception import ocr
from ..utils.screenshot import Region, window_region
from .artifacts import Artifact, ArtifactStore
from .evidence import EvidenceRegistry


def _payload(text: str) -> dict[str, Any]:
    try:
        data = json.loads(str(text or "{}"))
        return data if isinstance(data, dict) else {}
    except (TypeError, json.JSONDecodeError):
        match = re.search(r"\{.*\}", str(text or ""), re.DOTALL)
        if match:
            try:
                data = json.loads(match.group(0))
                return data if isinstance(data, dict) else {}
            except (TypeError, json.JSONDecodeError):
                pass
    return {}


def _ok(text: str) -> bool:
    data = _payload(text)
    return bool(data.get("ok") is True and not data.get("blocked") and not data.get("error"))


@dataclass(frozen=True)
class ChatLayoutProfile:
    """Versioned, scale-independent zones for split-pane chat clients."""

    version: str
    search: tuple[float, float, float, float]
    contacts: tuple[float, float, float, float]
    header: tuple[float, float, float, float]
    recipient_header: tuple[float, float, float, float]
    messages: tuple[float, float, float, float]
    composer: tuple[float, float, float, float]
    attachment_confirmation: tuple[float, float, float, float]
    attachment_preview: tuple[float, float, float, float]
    attachment_dismiss: tuple[float, float, float, float] = ()

    @staticmethod
    def region(window: Region, zone: tuple[float, float, float, float]) -> Region:
        left, top, right, bottom = zone
        x1 = window.left + int(round(window.width * left))
        y1 = window.top + int(round(window.height * top))
        x2 = window.left + int(round(window.width * right))
        y2 = window.top + int(round(window.height * bottom))
        return Region(x1, y1, max(1, x2 - x1), max(1, y2 - y1)).clamp_to_screen()


DEFAULT_CHAT_LAYOUT = ChatLayoutProfile(
    version="split-chat-v1",
    search=(0.04, 0.04, 0.34, 0.18),
    contacts=(0.04, 0.10, 0.34, 0.96),
    header=(0.34, 0.04, 0.99, 0.20),
    recipient_header=(0.34, 0.04, 0.72, 0.20),
    messages=(0.34, 0.16, 0.99, 0.72),
    composer=(0.34, 0.70, 0.99, 0.98),
    attachment_confirmation=(0.30, 0.20, 0.72, 0.80),
    attachment_preview=(0.32, 0.25, 0.62, 0.55),
)

QQ_DESKTOP_LAYOUT = ChatLayoutProfile(
    version="qq-desktop-v1",
    search=(0.035, 0.045, 0.155, 0.18),
    contacts=(0.035, 0.10, 0.155, 0.96),
    header=(0.155, 0.04, 0.99, 0.18),
    recipient_header=(0.155, 0.04, 0.47, 0.18),
    messages=(0.155, 0.14, 0.99, 0.81),
    composer=(0.155, 0.81, 0.99, 0.98),
    attachment_confirmation=(0.30, 0.20, 0.70, 0.80),
    attachment_preview=(0.35, 0.25, 0.58, 0.52),
    # Versioned, scale-independent fallback for QQ's unlabeled modal close
    # affordance.  It is used only after recipient + send button prove that an
    # attachment confirmation is already open, and it is verified afterwards.
    attachment_dismiss=(0.585, 0.265, 0.625, 0.320),
)


class CommunicationAdapter(ABC):
    id = "communication"
    layout_profile = DEFAULT_CHAT_LAYOUT
    send_labels: tuple[str, ...] = ("发送", "Send")

    def __init__(
        self,
        *,
        run_id: str,
        task: str,
        store: ArtifactStore,
        evidence: EvidenceRegistry,
        stop_event=None,
        task_intent: Any | None = None,
    ) -> None:
        self.run_id = run_id
        self.task = task
        self.store = store
        self.evidence = evidence
        self.session = ToolSession(
            stop_event=stop_event,
            task=task,
            runtime_profile=RuntimeProfile(desktop=True, coding=False),
            task_intent=task_intent,
        )
        self.session.run_id = run_id
        self.session.managed_artifact_store = store
        self.session.evidence_registry = evidence
        self.recipient = ""
        self.stage_id = ""
        self._preview_frame_id = ""
        self._pre_paste_frame_id = ""
        self._pre_send_frame_id = ""
        self._post_send_frame_id = ""
        self._send_action_at = 0.0
        self._pending_text = ""
        self._sent_text = ""
        self._conversation_header: dict[str, Any] = {}
        self._composer_binding: dict[str, Any] = {}
        self._pending_send_confirmation: dict[str, Any] = {}
        # Instance clock seams keep delivery polling deterministic in tests and
        # prevent a patched module-global clock from corrupting unrelated
        # perception/background threads in the same process.
        self._monotonic = time.monotonic
        self._sleep = time.sleep

    @abstractmethod
    def open_conversation(self, recipient: str, *, stage_id: str = "") -> dict[str, Any]: ...

    @abstractmethod
    def resolve_composer(self) -> dict[str, Any]: ...

    def _window(self) -> Region | None:
        snapshot = getattr(self.session, "snapshot", None)
        region = getattr(snapshot, "region", None)
        if isinstance(region, Region):
            return region
        if not self.session.hwnd:
            return None
        try:
            return window_region(int(self.session.hwnd)).clamp_to_screen()
        except Exception:
            return None

    def _observe_snapshot(self):
        # Communication adapters need UI structure plus OCR only in the exact
        # recipient/confirmation zones they verify below.  A task-wide visual
        # profile would otherwise run full-window OCR (and sometimes VLM) for
        # every local transaction, even though no semantic scene decision is
        # being delegated to a model here.
        self.session.dispatch("observe", {
            "full": True,
            "include_icons": False,
            "_controller_perception_mode": "structured_ui",
        })
        return getattr(self.session, "snapshot", None)

    @staticmethod
    def _normalized_text(value: Any) -> str:
        return re.sub(r"\s+", "", str(value or "")).strip().casefold()

    @staticmethod
    def _element_in_region(element: Any, region: Region) -> bool:
        try:
            cx = int(getattr(element, "cx", 0) or 0)
            cy = int(getattr(element, "cy", 0) or 0)
            if not cx and int(getattr(element, "width", 0) or 0) > 0:
                cx = int(getattr(element, "left", 0) or 0) + int(getattr(element, "width", 0)) // 2
            if not cy and int(getattr(element, "height", 0) or 0) > 0:
                cy = int(getattr(element, "top", 0) or 0) + int(getattr(element, "height", 0)) // 2
            return region.left <= cx <= region.right and region.top <= cy <= region.bottom
        except (TypeError, ValueError):
            return False

    def _matching_elements(
        self,
        snapshot: Any,
        text: str,
        zone: tuple[float, float, float, float],
        *,
        roles: tuple[str, ...] = (),
        allow_contains: bool = False,
        allowed_prefixes: tuple[str, ...] = (),
    ) -> list[Any]:
        window = getattr(snapshot, "region", None)
        if not isinstance(window, Region):
            return []
        region = self.layout_profile.region(window, zone)
        wanted = self._normalized_text(text)
        if not wanted:
            return []
        prefixed_identities = {
            f"{self._normalized_text(prefix)}{wanted}"
            for prefix in allowed_prefixes
            if self._normalized_text(prefix)
        }
        matches: list[Any] = []
        for element in getattr(snapshot, "elements", []) or []:
            actual = self._normalized_text(getattr(element, "text", ""))
            if not actual or (
                actual != wanted
                and actual not in prefixed_identities
                and not (allow_contains and wanted in actual)
            ):
                continue
            role = str(getattr(element, "role", "") or "").casefold()
            if roles and not any(token in role for token in roles):
                continue
            if self._element_in_region(element, region):
                matches.append(element)
        return sorted(
            matches,
            key=lambda item: (
                self._normalized_text(getattr(item, "text", "")) != wanted,
                str(getattr(item, "source", "")) != "uia",
                -float(getattr(item, "confidence", 0.0) or 0.0),
            ),
        )

    @staticmethod
    def _element_metadata(element: Any) -> dict[str, Any]:
        raw_element_id = getattr(element, "elem_id", -1)
        return {
            "element_id": int(raw_element_id if raw_element_id is not None else -1),
            "source": str(getattr(element, "source", "") or ""),
            "role": str(getattr(element, "role", "") or ""),
            "text": str(getattr(element, "text", "") or ""),
            "bbox": [
                int(getattr(element, "left", 0) or 0),
                int(getattr(element, "top", 0) or 0),
                int(getattr(element, "width", 0) or 0),
                int(getattr(element, "height", 0) or 0),
            ],
        }

    def _recipient_header_context(self, snapshot: Any) -> dict[str, Any]:
        matches = self._matching_elements(
            snapshot,
            self.recipient,
            self.layout_profile.recipient_header,
        )
        # UIA-heavy chat snapshots often omit OCR entirely even though the
        # conversation title is visibly rendered. Do one cheap, header-only
        # OCR probe instead of waiting for a whole-stage retry/full observe.
        window = getattr(snapshot, "region", None)
        if not matches and isinstance(window, Region):
            zone = self.layout_profile.region(window, self.layout_profile.recipient_header)
            try:
                supplemental = list(ocr.collect(zone, min_conf=0.45) or [])
            except Exception:
                supplemental = []
            if supplemental:
                probe = SimpleNamespace(
                    region=window,
                    elements=[*(getattr(snapshot, "elements", []) or []), *supplemental],
                )
                matches = self._matching_elements(
                    probe,
                    self.recipient,
                    self.layout_profile.recipient_header,
                )
        if not matches:
            return {
                "verified": False,
                "recipient": self.recipient,
                "layout_profile": self.layout_profile.version,
            }
        return {
            "verified": True,
            "recipient": self.recipient,
            "layout_profile": self.layout_profile.version,
            "element": self._element_metadata(matches[0]),
        }

    def _attachment_confirmation_context(self, snapshot: Any) -> dict[str, Any]:
        window = getattr(snapshot, "region", None)
        if not isinstance(window, Region):
            return {"verified": False, "reason": "window geometry unavailable"}
        zone = self.layout_profile.region(window, self.layout_profile.attachment_confirmation)
        normalized_labels = [self._normalized_text(label) for label in self.send_labels]

        def scan(elements: list[Any]) -> tuple[list[Any], list[Any]]:
            probe = SimpleNamespace(region=window, elements=elements)
            recipients = self._matching_elements(
                probe,
                self.recipient,
                self.layout_profile.attachment_confirmation,
                allowed_prefixes=("发送给", "发送给：", "发送给:", "Send to", "Send to:", "Send to："),
            )
            send_buttons: list[Any] = []
            for element in elements:
                if not self._element_in_region(element, zone):
                    continue
                text = self._normalized_text(getattr(element, "text", ""))
                if not any(
                    text == label or text.startswith(label + "(") or text.startswith(label + "（")
                    for label in normalized_labels
                ):
                    continue
                send_buttons.append(element)
            return recipients, send_buttons

        elements = list(getattr(snapshot, "elements", []) or [])
        recipient_matches, buttons = scan(elements)
        if not recipient_matches or not buttons:
            try:
                supplemental = list(ocr.collect(zone) or [])
            except Exception:
                supplemental = []
            if supplemental:
                recipient_matches, buttons = scan([*elements, *supplemental])
        buttons.sort(key=lambda item: (
            "button" not in str(getattr(item, "role", "") or "").casefold(),
            str(getattr(item, "source", "") or "") != "uia",
            -int(getattr(item, "cy", 0) or 0),
        ))
        button_metadata = self._element_metadata(buttons[0]) if buttons else {}
        button_text = self._normalized_text(button_metadata.get("text"))
        count_match = re.search(r"[\(（](\d+)[\)）]", button_text)
        return {
            "verified": bool(recipient_matches and buttons),
            "recipient": self.recipient,
            "layout_profile": self.layout_profile.version,
            "recipient_element": self._element_metadata(recipient_matches[0]) if recipient_matches else {},
            "send_button": button_metadata,
            "attachment_count": int(count_match.group(1)) if count_match else 0,
        }

    @staticmethod
    def _paste_receipt_matches(artifact: Artifact, payload: dict[str, Any]) -> bool:
        """Bind the controller's clipboard mutation to one managed artifact."""
        return bool(
            payload.get("ok") is True
            and str(payload.get("artifact_id") or "") == artifact.artifact_id
            and str(payload.get("sha256") or "") == artifact.sha256
            and int(payload.get("width") or 0) == artifact.width
            and int(payload.get("height") or 0) == artifact.height
            and payload.get("delivery") is False
            and payload.get("preview_only") is True
            and str(payload.get("via") or "") == "clipboard_dib"
        )

    def _bind_pending_confirmation(
        self,
        artifact: Artifact,
        context: dict[str, Any],
        paste_receipt: dict[str, Any],
    ) -> None:
        """Persist a run-scoped, artifact-bound confirmation transaction."""

        self._pending_send_confirmation = {
            **dict(context),
            "artifact_id": artifact.artifact_id,
            "artifact_sha256": artifact.sha256,
            "recipient": self.recipient,
            "stage_id": self.stage_id,
            "preview_frame_id": self._preview_frame_id,
            "paste_receipt": dict(paste_receipt),
            "bound_at": time.time(),
        }

    def _dismiss_stale_attachment_confirmation(self, snapshot: Any) -> dict[str, Any]:
        """Cancel a proven stale confirmation without ever activating Send."""
        context = self._attachment_confirmation_context(snapshot)
        if context.get("verified") is not True:
            return {"ok": True, "dismissed": False, "reason": "no stale confirmation"}

        attempts: list[dict[str, Any]] = []
        escaped = self.session.dispatch("hotkey", {"keys": ["esc"]})
        attempts.append({"method": "escape", "ok": _ok(escaped.text)})
        self._sleep(0.15)
        refreshed = self._observe_snapshot()
        remaining = self._attachment_confirmation_context(refreshed)
        if remaining.get("verified") is not True:
            return {"ok": True, "dismissed": True, "attempts": attempts}

        dismiss_zone = self.layout_profile.attachment_dismiss
        window = getattr(refreshed, "region", None)
        if not dismiss_zone or not isinstance(window, Region):
            return {
                "ok": False,
                "failure_type": "stale_confirmation_visible",
                "error": "a stale attachment confirmation remains visible and has no safe cancel fallback",
                "confirmation_context": remaining,
                "attempts": attempts,
            }
        zone = self.layout_profile.region(window, dismiss_zone)
        clicked = self.session.dispatch("click", {
            "x": zone.left + zone.width // 2,
            "y": zone.top + zone.height // 2,
            "physical": True,
        })
        attempts.append({
            "method": "versioned_relative_dismiss",
            "ok": _ok(clicked.text),
            "layout_profile": self.layout_profile.version,
            "region": {
                "left": zone.left,
                "top": zone.top,
                "width": zone.width,
                "height": zone.height,
            },
        })
        self._sleep(0.2)
        after = self._observe_snapshot()
        remaining = self._attachment_confirmation_context(after)
        if remaining.get("verified") is True:
            return {
                "ok": False,
                "failure_type": "stale_confirmation_visible",
                "error": "stale attachment confirmation could not be cancelled safely",
                "confirmation_context": remaining,
                "attempts": attempts,
            }
        return {"ok": True, "dismissed": True, "attempts": attempts}

    def _frame_local_zone(
        self,
        frame: Artifact,
        zone: tuple[float, float, float, float],
    ) -> dict[str, int]:
        capture_region = frame.metadata.get("region") if isinstance(frame.metadata, dict) else None
        if not isinstance(capture_region, dict):
            return {}
        screen_region = Region(
            int(capture_region.get("left", 0)),
            int(capture_region.get("top", 0)),
            int(capture_region.get("width", frame.width)),
            int(capture_region.get("height", frame.height)),
        )
        zoned = self.layout_profile.region(screen_region, zone)
        left = max(0, zoned.left - screen_region.left)
        top = max(0, zoned.top - screen_region.top)
        return {
            "left": left,
            "top": top,
            "width": max(0, min(frame.width - left, zoned.width)),
            "height": max(0, min(frame.height - top, zoned.height)),
        }

    @staticmethod
    def _frame_screen_region(frame: Artifact) -> Region | None:
        capture_region = frame.metadata.get("region") if isinstance(frame.metadata, dict) else None
        if not isinstance(capture_region, dict):
            return None
        try:
            return Region(
                int(capture_region.get("left", 0)),
                int(capture_region.get("top", 0)),
                int(capture_region.get("width", frame.width)),
                int(capture_region.get("height", frame.height)),
            )
        except (TypeError, ValueError):
            return None

    def _frame_local_element_region(
        self,
        frame: Artifact,
        element: Any,
        *,
        padding: int = 8,
    ) -> dict[str, int]:
        screen_region = self._frame_screen_region(frame)
        if screen_region is None:
            return {}
        try:
            left = int(getattr(element, "left", 0) or 0)
            top = int(getattr(element, "top", 0) or 0)
            width = int(getattr(element, "width", 0) or 0)
            height = int(getattr(element, "height", 0) or 0)
        except (TypeError, ValueError):
            return {}
        if width <= 0 or height <= 0:
            return {}
        pad = max(0, int(padding))
        local_left = max(0, left - screen_region.left - pad)
        local_top = max(0, top - screen_region.top - pad)
        right = min(frame.width, left - screen_region.left + width + pad)
        bottom = min(frame.height, top - screen_region.top + height + pad)
        if right <= local_left or bottom <= local_top:
            return {}
        return {
            "left": local_left,
            "top": local_top,
            "width": right - local_left,
            "height": bottom - local_top,
        }

    def _element_is_outbound(self, snapshot: Any, element: Any) -> bool:
        window = getattr(snapshot, "region", None)
        if not isinstance(window, Region):
            return False
        messages = self.layout_profile.region(window, self.layout_profile.messages)
        try:
            center_x = int(getattr(element, "cx", 0) or 0)
            if not center_x:
                center_x = (
                    int(getattr(element, "left", 0) or 0)
                    + int(getattr(element, "width", 0) or 0) // 2
                )
        except (TypeError, ValueError):
            return False
        return center_x >= messages.left + messages.width // 2

    def _match_bbox_is_outbound(self, frame: Artifact, match_bbox: Any) -> bool:
        if not isinstance(match_bbox, (list, tuple)) or len(match_bbox) != 4:
            return False
        messages = self._frame_local_zone(frame, self.layout_profile.messages)
        if not messages:
            return False
        try:
            center_x = int(match_bbox[0]) + int(match_bbox[2]) // 2
        except (TypeError, ValueError):
            return False
        return center_x >= int(messages["left"]) + int(messages["width"]) // 2

    def _message_text_matches(self, snapshot: Any, text: str) -> list[Any]:
        """Read exact text only inside the message obligation ROI."""
        window = getattr(snapshot, "region", None)
        if not isinstance(window, Region):
            return []
        elements = list(getattr(snapshot, "elements", []) or [])
        zone = self.layout_profile.region(window, self.layout_profile.messages)
        try:
            elements.extend(ocr.collect(zone, min_conf=0.45) or [])
        except Exception:
            pass
        probe = SimpleNamespace(region=window, elements=elements)
        matches = self._matching_elements(
            probe,
            text,
            self.layout_profile.messages,
        )
        return sorted(
            matches,
            key=lambda item: int(getattr(item, "cy", 0) or 0),
            reverse=True,
        )

    def paste_or_attach_artifact(self, artifact_id: str, *, stage_id: str = "") -> dict[str, Any]:
        self.stage_id = stage_id or self.stage_id
        artifact = self.store.get(artifact_id, require_sendable=True)
        snapshot = getattr(self.session, "snapshot", None) or self._observe_snapshot()
        context = self._recipient_header_context(snapshot)
        if context.get("verified") is not True:
            return {
                "ok": False,
                "failure_type": "recipient_not_resolved",
                "error": "conversation header no longer proves the requested recipient",
                "recipient_context": context,
            }
        before_confirmation = self._attachment_confirmation_context(snapshot)
        if before_confirmation.get("verified") is True:
            return {
                "ok": False,
                "failure_type": "stale_confirmation_visible",
                "error": "refused to paste while an earlier attachment confirmation is still visible",
                "confirmation_context": before_confirmation,
            }
        self._pending_send_confirmation = {}
        self._sent_text = ""
        self._pending_text = ""
        before_frame = self._capture_observation("pre_paste_conversation")
        self._pre_paste_frame_id = before_frame.artifact_id if before_frame else ""
        if not self._pre_paste_frame_id:
            return {
                "ok": False,
                "failure_type": "delivery_baseline_missing",
                "error": "artifact staging requires a clean pre-paste conversation baseline",
            }
        result = self.session.dispatch("paste_image_file", {"path": artifact.managed_path})
        if not _ok(result.text):
            return {"ok": False, "failure_type": "artifact_stage_failed", "error": result.text}
        paste_receipt = _payload(result.text)
        if not self._paste_receipt_matches(artifact, paste_receipt):
            return {
                "ok": False,
                "failure_type": "artifact_identity_unproven",
                "error": "clipboard paste receipt does not bind to the requested current-run artifact",
                "paste_receipt": paste_receipt,
            }
        self._sleep(0.5)
        frame = self._capture_observation("composer_preview")
        self._preview_frame_id = frame.artifact_id if frame else ""
        composer_region = self._frame_local_zone(frame, self.layout_profile.composer) if frame else {}
        confirmation_region = (
            self._frame_local_zone(frame, self.layout_profile.attachment_preview)
            if frame else {}
        )
        after_snapshot = self._observe_snapshot()
        confirmation_context = self._attachment_confirmation_context(after_snapshot)
        confirmation_change_region = (
            self._frame_local_zone(frame, self.layout_profile.attachment_confirmation)
            if frame else {}
        )
        confirmation_change = (
            self.store.frame_change(
                before_frame.artifact_id,
                frame.artifact_id,
                target_region=confirmation_change_region,
            )
            if before_frame and frame and confirmation_change_region else {
                "changed": False,
                "reason": "aligned pre/post confirmation frames unavailable",
            }
        )
        causal_confirmation = bool(
            confirmation_context.get("verified") is True
            and int(confirmation_context.get("attachment_count") or 0) == 1
            and before_confirmation.get("verified") is not True
            and confirmation_change.get("changed") is True
        )

        preview_surface = ""
        preview_region: dict[str, Any] = {}
        preview_match: dict[str, Any] = {"matched": False}
        composer_match: dict[str, Any] = {
            "matched": False,
            "reason": "not evaluated because a stronger causal attachment proof was available",
        }
        confirmation_match: dict[str, Any] = dict(composer_match)
        if causal_confirmation:
            # Structural confirmation evidence outranks a weak match in the
            # underlying composer.  A modal can overlap the composer and tiny
            # edge templates near the send button are not artifact identity.
            preview_surface = "attachment_confirmation"
            preview_region = confirmation_region
            preview_match = {
                "matched": True,
                "method": "exact_paste_receipt+new_single_attachment_confirmation",
                "visual_match": {
                    "matched": False,
                    "skipped": "causal_attachment_confirmation",
                },
                "frame_change": confirmation_change,
                "paste_receipt": paste_receipt,
            }
            self._bind_pending_confirmation(artifact, confirmation_context, paste_receipt)
        else:
            confirmation_match = (
                self.store.visual_match(
                    artifact.artifact_id,
                    frame.artifact_id,
                    target_region=confirmation_region,
                )
                if frame and confirmation_region else {
                    "matched": False,
                    "reason": "attachment confirmation frame/region missing",
                }
            )
            composer_match = (
                self.store.visual_match(
                    artifact.artifact_id,
                    frame.artifact_id,
                    target_region=composer_region,
                )
                if frame and composer_region else {
                    "matched": False,
                    "reason": "composer preview frame/region missing",
                }
            )
            if confirmation_match.get("matched") is True:
                preview_surface = "attachment_confirmation"
                preview_region = confirmation_region
                preview_match = confirmation_match
                if confirmation_context.get("verified") is True:
                    self._bind_pending_confirmation(artifact, confirmation_context, paste_receipt)
            elif composer_match.get("matched") is True:
                preview_surface = "composer"
                preview_region = composer_region
                preview_match = composer_match
        if preview_match.get("matched") is not True:
            return {
                "ok": False,
                "failure_type": "artifact_stage_failed",
                "error": "paste action did not create a visible artifact preview in the composer or attachment confirmation dialog",
                "preview_frame_id": self._preview_frame_id,
                "composer_match": composer_match,
                "confirmation_match": confirmation_match,
                "recipient_context": context,
                "paste_receipt": paste_receipt,
                "confirmation_context": confirmation_context,
                "confirmation_change": confirmation_change,
            }
        if preview_surface == "attachment_confirmation" and confirmation_context.get("verified") is not True:
            return {
                "ok": False,
                "failure_type": "recipient_not_resolved",
                "error": "attachment confirmation is visible but its recipient/send button could not be proven",
                "preview_frame_id": self._preview_frame_id,
                "preview_match": preview_match,
                "confirmation_context": confirmation_context,
                "confirmation_change": confirmation_change,
            }
        preview = self.evidence.record(
            "artifact.composer_preview",
            provider=f"{self.id}_composer_preview",
            verified=True,
            artifact_id=artifact.artifact_id,
            recipient=self.recipient,
            stage_id=self.stage_id,
            metadata={
                "artifact_sha256": artifact.sha256,
                "preview_frame_id": self._preview_frame_id,
                "preview_region": preview_region,
                "preview_match": preview_match,
                "preview_surface": preview_surface,
                "confirmation_context": confirmation_context,
                "pre_paste_frame_id": self._pre_paste_frame_id,
                "paste_receipt": paste_receipt,
                "delivery": False,
            },
            text="Artifact is staged in a composer/attachment confirmation surface; this is explicitly not delivery evidence.",
        )
        return {
            "ok": True,
            "artifact": artifact.model_dump(mode="json"),
            "preview_evidence": preview,
            "preview_match": preview_match,
            "preview_surface": preview_surface,
            "confirmation_context": confirmation_context,
            "pre_paste_frame_id": self._pre_paste_frame_id,
            "paste_receipt": paste_receipt,
        }

    def send_current_composer(self, artifact_id: str = "", *, stage_id: str = "") -> dict[str, Any]:
        self.stage_id = stage_id or self.stage_id
        action = "enter"
        confirmation_binding: dict[str, Any] = {}
        post_send_context: dict[str, Any] = {}
        if self._pending_send_confirmation:
            confirmation_binding = dict(self._pending_send_confirmation)
            pending_artifact_id = str(confirmation_binding.get("artifact_id") or "")
            if artifact_id and pending_artifact_id != str(artifact_id):
                return {
                    "ok": False,
                    "failure_type": "artifact_identity_unproven",
                    "error": "pending attachment confirmation belongs to a different artifact",
                    "pending_artifact_id": pending_artifact_id,
                    "requested_artifact_id": str(artifact_id),
                }
            try:
                pending_artifact = self.store.get(pending_artifact_id, require_sendable=True)
            except Exception as exc:
                return {
                    "ok": False,
                    "failure_type": "artifact_identity_unproven",
                    "error": str(exc),
                }
            if pending_artifact.sha256 != str(confirmation_binding.get("artifact_sha256") or ""):
                return {
                    "ok": False,
                    "failure_type": "artifact_identity_unproven",
                    "error": "pending confirmation artifact hash no longer matches the managed artifact",
                }
            if not self._pre_paste_frame_id:
                return {
                    "ok": False,
                    "failure_type": "delivery_baseline_missing",
                    "error": "refused to send because the clean pre-paste conversation baseline is unavailable",
                }

            # Re-observe immediately before the irreversible send.  Element ids
            # and OCR boxes from the paste frame are not reused across frames.
            snapshot = self._observe_snapshot()
            recipient_context = self._attachment_confirmation_context(snapshot)
            same_recipient = (
                self._normalized_text(recipient_context.get("recipient"))
                == self._normalized_text(self.recipient)
                == self._normalized_text(confirmation_binding.get("recipient"))
            )
            if (
                recipient_context.get("verified") is not True
                or not same_recipient
                or int(recipient_context.get("attachment_count") or 0) != 1
            ):
                return {
                    "ok": False,
                    "failure_type": "recipient_not_resolved",
                    "error": "refused to confirm attachment because the fresh dialog recipient, count, or send button is not proven",
                    "recipient_context": recipient_context,
                }
            button = recipient_context.get("send_button") or {}
            element_id = int(button.get("element_id", -1))
            bbox = button.get("bbox") or []
            source = str(button.get("source") or "").casefold()
            if element_id >= 0 and source == "uia":
                result = self.session.dispatch("click", {"element_id": element_id})
            elif len(bbox) == 4:
                result = self.session.dispatch("click", {
                    "x": int(bbox[0]) + int(bbox[2]) // 2,
                    "y": int(bbox[1]) + int(bbox[3]) // 2,
                    "physical": True,
                })
            else:
                return {
                    "ok": False,
                    "failure_type": "send_action_missing",
                    "error": "attachment confirmation send button has no actionable geometry",
                }
            if not _ok(result.text) and len(bbox) == 4:
                result = self.session.dispatch("click", {
                    "x": int(bbox[0]) + int(bbox[2]) // 2,
                    "y": int(bbox[1]) + int(bbox[3]) // 2,
                    "physical": True,
                })
            action = "attachment_confirmation_button"
        else:
            snapshot = self._observe_snapshot()
            recipient_context = self._recipient_header_context(snapshot)
            if recipient_context.get("verified") is not True:
                return {
                    "ok": False,
                    "failure_type": "recipient_not_resolved",
                    "error": "refused to send because the requested recipient is no longer proven in the conversation header",
                    "recipient_context": recipient_context,
                }
            if not self._pending_text:
                baseline = self._capture_observation("pre_send_conversation")
                self._pre_send_frame_id = baseline.artifact_id if baseline else ""
            if not self._pre_send_frame_id:
                return {
                    "ok": False,
                    "failure_type": "delivery_baseline_missing",
                    "error": "refused to send because a causal pre-send conversation baseline could not be captured",
                }
            result = self.session.dispatch("hotkey", {"keys": ["enter"]})
        if not _ok(result.text):
            return {"ok": False, "failure_type": "send_action_missing", "error": result.text}
        self._send_action_at = time.time()
        if confirmation_binding:
            self._sent_text = ""
        elif self._pending_text:
            self._sent_text = self._pending_text
            self._pending_text = ""
        if confirmation_binding:
            self._sleep(0.3)
            after_snapshot = self._observe_snapshot()
            remaining = self._attachment_confirmation_context(after_snapshot)
            if remaining.get("verified") is True:
                return {
                    "ok": False,
                    "failure_type": "send_action_missing",
                    "error": "attachment confirmation remained visible after activating Send",
                    "confirmation_context": remaining,
                }
            post_send_context = self._recipient_header_context(after_snapshot)
            if post_send_context.get("verified") is not True:
                return {
                    "ok": False,
                    "failure_type": "recipient_not_resolved",
                    "error": "confirmation closed but the requested conversation header is no longer proven",
                    "recipient_context": post_send_context,
                }
            self._pending_send_confirmation = {}
        evidence = self.evidence.record(
            "delivery.send_action",
            provider="communication_adapter_send",
            verified=True,
            recipient=self.recipient,
            stage_id=self.stage_id,
            metadata={
                "adapter": self.id,
                "action": action,
                "executed_at": self._send_action_at,
                "recipient_context": recipient_context,
                "post_send_recipient_context": post_send_context,
                "composer_binding": self._composer_binding,
                "attachment_confirmation": confirmation_binding,
                "pre_send_frame_id": self._pre_send_frame_id,
                "pre_paste_frame_id": self._pre_paste_frame_id,
                "sent_text": self._sent_text,
            },
        )
        self._sleep(0.8)
        return {"ok": True, "send_action": evidence}

    def send_text(self, text: str, *, stage_id: str = "") -> dict[str, Any]:
        if not text:
            return {"ok": False, "failure_type": "text_missing", "error": "send_text requires text"}
        self._sent_text = ""
        self._pending_text = ""
        self._pre_send_frame_id = ""
        baseline = self._capture_observation("pre_type_conversation")
        self._pre_send_frame_id = baseline.artifact_id if baseline else ""
        if not self._pre_send_frame_id:
            return {
                "ok": False,
                "failure_type": "delivery_baseline_missing",
                "error": "text send requires a causal conversation baseline before typing",
            }
        typed = self.session.dispatch("type_text", {"text": text})
        if not _ok(typed.text):
            return {"ok": False, "failure_type": "text_stage_failed", "error": typed.text}
        self._pending_text = text
        return self.send_current_composer(stage_id=stage_id)

    def verify_latest_outbound(
        self,
        artifact_id: str = "",
        text: str = "",
        *,
        stage_id: str = "",
    ) -> dict[str, Any]:
        self.stage_id = stage_id or self.stage_id
        if not self._send_action_at:
            return {"ok": False, "failure_type": "send_action_missing", "error": "no real send action was recorded"}
        artifact = self.store.get(artifact_id, require_sendable=True) if artifact_id else None
        if artifact is not None and not self._pre_paste_frame_id:
            return {
                "ok": False,
                "failure_type": "delivery_baseline_missing",
                "error": "artifact delivery has no clean pre-paste conversation baseline",
            }
        if artifact is None:
            if not text or self._normalized_text(text) != self._normalized_text(self._sent_text):
                return {
                    "ok": False,
                    "failure_type": "text_identity_unproven",
                    "error": "requested delivery text is not bound to the recorded send action",
                }
            if not self._pre_send_frame_id:
                return {
                    "ok": False,
                    "failure_type": "delivery_baseline_missing",
                    "error": "text delivery has no causal pre-send conversation baseline",
                }
        deadline = self._monotonic() + 5.0
        last: dict[str, Any] = {
            "ok": False,
            "failure_type": "delivery_not_visible",
            "error": "post-send evidence is not visible yet",
        }
        while self._monotonic() < deadline:
            snapshot = self._observe_snapshot()
            recipient_context = self._recipient_header_context(snapshot)
            post_frame = self._capture_observation("post_send_conversation")
            self._post_send_frame_id = post_frame.artifact_id if post_frame else ""
            if not post_frame or post_frame.created_at < self._send_action_at:
                last = {
                    "ok": False,
                    "failure_type": "delivery_not_visible",
                    "error": "no post-send frame newer than the send action",
                    "recipient_context": recipient_context,
                }
                self._sleep(0.35)
                continue

            if artifact is not None:
                message_region = self._frame_local_zone(post_frame, self.layout_profile.messages)
                match = self.store.visual_match(
                    artifact.artifact_id,
                    post_frame.artifact_id,
                    target_region=message_region,
                    baseline_frame_id=self._pre_paste_frame_id,
                    require_changed=True,
                    min_scale=0.08,
                )
                outbound_geometry = self._match_bbox_is_outbound(post_frame, match.get("match_bbox"))
                if (
                    recipient_context.get("verified") is True
                    and match.get("matched") is True
                    and outbound_geometry
                ):
                    record = self.evidence.record(
                        "delivery.visible_outbound",
                        provider="communication_adapter_visible_outbound",
                        verified=True,
                        artifact_id=artifact.artifact_id,
                        recipient=self.recipient,
                        stage_id=self.stage_id,
                        metadata={
                            "artifact_sha256": artifact.sha256,
                            "preview_frame_id": self._preview_frame_id,
                            "baseline_frame_id": self._pre_paste_frame_id,
                            "post_send_frame_id": post_frame.artifact_id,
                            "message_region": message_region,
                            "artifact_match": match,
                            "outbound_geometry": outbound_geometry,
                            "recipient_context": recipient_context,
                            "send_action_at": self._send_action_at,
                        },
                    )
                    return {
                        "ok": True,
                        "evidence": record,
                        "post_send_frame": post_frame.model_dump(mode="json"),
                    }
                last = {
                    "ok": False,
                    "failure_type": "delivery_not_visible",
                    "error": "post-send evidence does not prove a newly changed artifact bubble in the requested conversation",
                    "recipient_context": recipient_context,
                    "artifact_match": match,
                    "outbound_geometry": outbound_geometry,
                    "post_send_frame_id": post_frame.artifact_id,
                }
            else:
                text_matches = self._message_text_matches(snapshot, text)
                candidate_evidence: list[dict[str, Any]] = []
                changed_match: Any | None = None
                for candidate in text_matches:
                    outbound = self._element_is_outbound(snapshot, candidate)
                    candidate_region = self._frame_local_element_region(post_frame, candidate)
                    change = (
                        self.store.frame_change(
                            self._pre_send_frame_id,
                            post_frame.artifact_id,
                            target_region=candidate_region,
                        )
                        if candidate_region else {
                            "changed": False,
                            "reason": "text candidate has no frame-local geometry",
                        }
                    )
                    candidate_evidence.append({
                        "element": self._element_metadata(candidate),
                        "outbound": outbound,
                        "region": candidate_region,
                        "frame_change": change,
                    })
                    if outbound and change.get("changed") is True:
                        changed_match = candidate
                        break
                if recipient_context.get("verified") is True and changed_match is not None:
                    record = self.evidence.record(
                        "delivery.visible_outbound",
                        provider="communication_adapter_visible_outbound",
                        verified=True,
                        recipient=self.recipient,
                        stage_id=self.stage_id,
                        metadata={
                            "text": text,
                            "text_element": self._element_metadata(changed_match),
                            "baseline_frame_id": self._pre_send_frame_id,
                            "candidate_evidence": candidate_evidence,
                            "recipient_context": recipient_context,
                            "post_send_frame_id": post_frame.artifact_id,
                            "send_action_at": self._send_action_at,
                        },
                    )
                    return {
                        "ok": True,
                        "evidence": record,
                        "post_send_frame": post_frame.model_dump(mode="json"),
                    }
                last = {
                    "ok": False,
                    "failure_type": "delivery_not_visible",
                    "error": "outbound text is not visible in the message region of the requested conversation",
                    "recipient_context": recipient_context,
                    "text_visible": bool(text_matches),
                    "candidate_evidence": candidate_evidence,
                    "post_send_frame_id": post_frame.artifact_id,
                }
            self._sleep(0.35)
        return last

    def _capture_observation(self, purpose: str) -> Artifact | None:
        hwnd = int(self.session.hwnd or 0)
        if not hwnd:
            return None
        try:
            return self.store.capture_observation(
                scope="window",
                source_hwnd=hwnd,
                metadata={"adapter": self.id, "purpose": purpose, "recipient": self.recipient},
            )
        except Exception:
            return None

    def _semantic_delivery_check(self, artifact: Artifact, recipient_visible: bool) -> dict[str, Any]:
        if not recipient_visible:
            return {"verified": False, "reason": "recipient context is not visible"}
        result = self.session.dispatch(
            "vlm_verify",
            {"criteria": (
                f"The current conversation visibly shows a newly sent outbound image or attachment bubble "
                f"after the send action, in the conversation with {self.recipient}. "
                "A composer paste preview is not sufficient."
            )},
        )
        data = _payload(result.text)
        return {
            "verified": bool(data.get("verified") is True or data.get("semantic_visual_verified") is True),
            "reason": str(data.get("reason") or data.get("error") or ""),
            "method": str(data.get("verify_method") or "semantic-vlm"),
        }

    def close(self) -> None:
        self.session.close()


class SearchBasedChatAdapter(CommunicationAdapter):
    app_name = ""
    search_labels: tuple[str, ...] = ("搜索", "Search")

    def open_conversation(self, recipient: str, *, stage_id: str = "") -> dict[str, Any]:
        recipient = str(recipient or "").strip()
        if not recipient:
            return {"ok": False, "failure_type": "recipient_not_resolved", "error": "recipient is empty"}
        self.recipient = recipient
        self.stage_id = stage_id
        opened = self.session.dispatch("open_app", {"name": self.app_name})
        if not _ok(opened.text):
            return {"ok": False, "failure_type": "window_not_found", "error": opened.text}
        # Normalize stale search menus or an attachment confirmation left by a
        # previously interrupted run.  Escape is a reversible, app-scoped
        # cancellation; it never confirms or sends pending content.
        self.session.dispatch("hotkey", {"keys": ["esc"]})
        self._sleep(0.15)
        snapshot = self._observe_snapshot()
        stale = self._dismiss_stale_attachment_confirmation(snapshot)
        if stale.get("ok") is not True:
            return stale
        if stale.get("dismissed") is True:
            snapshot = self._observe_snapshot()
        trace: list[dict[str, Any]] = []
        if stale.get("dismissed") is True:
            trace.append({"action": "dismiss_stale_attachment_confirmation", **stale})

        # If the requested recipient is already proven in the authoritative
        # conversation-header zone, the navigation obligation is complete.
        # Re-clicking the same contact only creates a transient UI state and
        # forces another full perception pass.
        context = self._recipient_header_context(snapshot)
        if context.get("verified") is True:
            strategy = "already_open"
            self._conversation_header = context
            record = self.evidence.record(
                "conversation.correct_recipient",
                provider="communication_adapter_context",
                verified=True,
                recipient=recipient,
                stage_id=stage_id,
                metadata={
                    "adapter": self.id,
                    "strategy": strategy,
                    "hwnd": int(self.session.hwnd or 0),
                    "layout_profile": self.layout_profile.version,
                    "selected_contact": {},
                    "recipient_context": context,
                    "trace": trace,
                },
                text=f"Recipient {recipient!r} is already visible in the main conversation header region.",
            )
            return {
                "ok": True,
                "recipient": recipient,
                "strategy": strategy,
                "recipient_context": context,
                "evidence": record,
            }

        def click_element(element: Any) -> bool:
            metadata = self._element_metadata(element)
            element_id = int(getattr(element, "elem_id", -1))
            result = self.session.dispatch("click", {"element_id": element_id})
            ok = _ok(result.text)
            if not ok:
                result = self.session.dispatch("click", {
                    "x": int(getattr(element, "cx", 0) or 0),
                    "y": int(getattr(element, "cy", 0) or 0),
                    "physical": True,
                })
                ok = _ok(result.text)
            trace.append({"action": "click_recipient", "element": metadata, "ok": ok})
            return ok

        strategy = "visible_contact"
        candidates = self._matching_elements(
            snapshot,
            recipient,
            self.layout_profile.contacts,
        )
        selected_contact: dict[str, Any] = {}
        if candidates:
            selected_contact = self._element_metadata(candidates[0])
            if not click_element(candidates[0]):
                candidates = []

        if not candidates:
            strategy = "scoped_search_field"
            search_fields: list[Any] = []
            for label in self.search_labels:
                search_fields.extend(self._matching_elements(
                    snapshot,
                    label,
                    self.layout_profile.search,
                    roles=("edit", "input", "search", "combobox"),
                    allow_contains=True,
                ))
            search_field = search_fields[0] if search_fields else None
            if search_field is not None:
                clicked = self.session.dispatch("click", {"element_id": int(getattr(search_field, "elem_id", -1))})
                if not _ok(clicked.text):
                    return {"ok": False, "failure_type": "recipient_not_resolved", "error": clicked.text}
            else:
                strategy = "keyboard_search_v1"
                focused = self.session.dispatch("hotkey", {"keys": ["ctrl", "f"]})
                if not _ok(focused.text):
                    return {"ok": False, "failure_type": "recipient_not_resolved", "error": focused.text}
            self.session.dispatch("hotkey", {"keys": ["ctrl", "a"]})
            typed = self.session.dispatch("type_text", {"text": recipient})
            if not _ok(typed.text):
                return {"ok": False, "failure_type": "recipient_not_resolved", "error": typed.text}
            self._sleep(0.45)
            snapshot = self._observe_snapshot()
            candidates = self._matching_elements(
                snapshot,
                recipient,
                self.layout_profile.contacts,
            )
            if candidates:
                selected_contact = self._element_metadata(candidates[0])
                if not click_element(candidates[0]):
                    return {
                        "ok": False,
                        "failure_type": "recipient_not_resolved",
                        "error": "recipient result was found but could not be activated",
                        "trace": trace,
                    }
            else:
                selected = self.session.dispatch("hotkey", {"keys": ["enter"]})
                if not _ok(selected.text):
                    return {"ok": False, "failure_type": "recipient_not_resolved", "error": selected.text}

        deadline = self._monotonic() + 4.0
        context: dict[str, Any] = {}
        while self._monotonic() < deadline:
            self._sleep(0.3)
            snapshot = self._observe_snapshot()
            context = self._recipient_header_context(snapshot)
            if context.get("verified") is True:
                break
        if context.get("verified") is not True:
            return {
                "ok": False,
                "failure_type": "recipient_not_resolved",
                "error": "the requested recipient was not proven in the main conversation header region",
                "strategy": strategy,
                "layout_profile": self.layout_profile.version,
                "selected_contact": selected_contact,
                "recipient_context": context,
                "trace": trace,
            }
        self._conversation_header = context
        record = self.evidence.record(
            "conversation.correct_recipient",
            provider="communication_adapter_context",
            verified=True,
            recipient=recipient,
            stage_id=stage_id,
            metadata={
                "adapter": self.id,
                "strategy": strategy,
                "hwnd": int(self.session.hwnd or 0),
                "layout_profile": self.layout_profile.version,
                "selected_contact": selected_contact,
                "recipient_context": context,
                "trace": trace,
            },
            text=f"Recipient {recipient!r} is visible in the main conversation header region.",
        )
        return {
            "ok": True,
            "recipient": recipient,
            "strategy": strategy,
            "recipient_context": context,
            "evidence": record,
        }

    def resolve_composer(self) -> dict[str, Any]:
        if not self.session.hwnd or not self.recipient:
            return {"ok": False, "failure_type": "composer_not_resolved", "error": "conversation context is not bound"}
        # open_conversation leaves a freshly verified authoritative snapshot.
        # Reuse it for this reversible focus click; irreversible paste/send
        # paths independently revalidate recipient and confirmation state.
        snapshot = getattr(self.session, "snapshot", None)
        if snapshot is None:
            snapshot = self._observe_snapshot()
        context = self._recipient_header_context(snapshot)
        if context.get("verified") is not True:
            return {
                "ok": False,
                "failure_type": "composer_not_resolved",
                "error": "cannot bind a composer without a verified conversation header",
                "recipient_context": context,
            }
        window = getattr(snapshot, "region", None)
        if not isinstance(window, Region):
            return {"ok": False, "failure_type": "composer_not_resolved", "error": "chat window geometry is unavailable"}
        composer_region = self.layout_profile.region(window, self.layout_profile.composer)
        edit_candidates = []
        for element in getattr(snapshot, "elements", []) or []:
            role = str(getattr(element, "role", "") or "").casefold()
            if any(token in role for token in ("edit", "textbox", "input", "document")) and self._element_in_region(element, composer_region):
                edit_candidates.append(element)
        method = "uia_composer"
        element_metadata: dict[str, Any] = {}
        if edit_candidates:
            candidate = max(edit_candidates, key=lambda item: int(getattr(item, "cy", 0) or 0))
            clicked = self.session.dispatch("click", {"element_id": int(getattr(candidate, "elem_id", -1))})
            element_metadata = self._element_metadata(candidate)
        else:
            # Versioned scale-independent fallback inside the already verified
            # composer zone.  It is never used to discover the conversation.
            method = "layout_composer_fallback"
            clicked = self.session.dispatch("click", {
                "x": composer_region.left + composer_region.width // 2,
                "y": composer_region.top + composer_region.height // 2,
                "physical": True,
            })
        if not _ok(clicked.text):
            return {"ok": False, "failure_type": "composer_not_resolved", "error": clicked.text}
        self._composer_binding = {
            "method": method,
            "region": {
                "left": composer_region.left,
                "top": composer_region.top,
                "width": composer_region.width,
                "height": composer_region.height,
            },
            "element": element_metadata,
            "layout_profile": self.layout_profile.version,
        }
        return {
            "ok": True,
            "hwnd": int(self.session.hwnd),
            "recipient": self.recipient,
            **self._composer_binding,
        }


class QQCommunicationAdapter(SearchBasedChatAdapter):
    id = "qq"
    app_name = "QQ"
    search_labels = ("搜索", "搜索联系人", "查找", "Search")
    layout_profile = QQ_DESKTOP_LAYOUT


class WeChatCommunicationAdapter(SearchBasedChatAdapter):
    id = "wechat"
    app_name = "微信"
    search_labels = ("搜索", "搜索联系人", "Search")


class DomainAdapterRegistry:
    _types = {
        "qq": QQCommunicationAdapter,
        "wechat": WeChatCommunicationAdapter,
        "微信": WeChatCommunicationAdapter,
    }

    @staticmethod
    def _component_id(adapter_type: type[CommunicationAdapter]) -> str:
        return f"{str(adapter_type.id).strip().casefold()}_adapter"

    @classmethod
    def component_for_channel(cls, channel: str) -> str:
        """Resolve a registered adapter component without instantiating it."""

        adapter_type = cls._types.get(str(channel or "").strip().casefold())
        return cls._component_id(adapter_type) if adapter_type is not None else ""

    @classmethod
    def probe_registration(
        cls,
        *,
        channel: str = "",
        component: str = "",
    ) -> dict[str, Any]:
        """Perform the only safe fail-before-run adapter probe.

        Registry membership and channel/component agreement are static facts.
        Constructing an adapter may touch the desktop, while installation,
        login state, and recipient availability are mutable external state; all
        three remain explicit stage-time limitations instead of being reported
        as preflight success.
        """

        channel_key = str(channel or "").strip().casefold()
        component_key = str(component or "").strip().casefold()
        channel_type = cls._types.get(channel_key) if channel_key else None
        registered_types = set(cls._types.values())
        component_type = next(
            (
                adapter_type
                for adapter_type in registered_types
                if cls._component_id(adapter_type) == component_key
            ),
            None,
        ) if component_key else None
        selected_type = channel_type or component_type
        registered = bool(
            selected_type is not None
            and (not channel_key or channel_type is selected_type)
            and (not component_key or component_type is selected_type)
        )
        return {
            "registered": registered,
            "channel": str(channel or "").strip(),
            "component": (
                cls._component_id(selected_type) if selected_type is not None else component_key
            ),
            "adapter_id": str(selected_type.id) if selected_type is not None else "",
            "probe_scope": "static_registry_only",
            "runtime_limitations": [
                "Static registration does not prove that the application is installed or running.",
                "Static registration does not prove login/session readiness or recipient availability.",
            ],
        }

    @classmethod
    def create(cls, channel: str, **kwargs: Any) -> CommunicationAdapter:
        adapter_type = cls._types.get(str(channel or "").strip().casefold())
        if adapter_type is None:
            raise KeyError(f"no communication adapter registered for channel: {channel}")
        return adapter_type(**kwargs)

    @classmethod
    def registered_channels(cls) -> list[str]:
        return sorted(set(cls._types))
