"""
Observation subsystem — collects environment state after tool execution.

Gathers screen state (active window, OCR text, screenshots) to provide
the agent with rich context for decision-making.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from core.interfaces import Observation, ToolResult

logger = logging.getLogger("agent.observation")


class ObservationCollector:
    """Collects snapshots of the environment.

    Uses the @feature registry to query system state:
    - Active window title
    - OCR text on screen
    - Screenshot
    - Detected images

    Usage:
        collector = ObservationCollector()
        obs = collector.collect()
        print(obs.windowTitle, obs.ocrText[:3])
    """

    def __init__(self) -> None:
        self._registry = None  # lazy import

    def _get_registry(self) -> Any:
        """Lazy import the feature registry."""
        if self._registry is None:
            import features._all  # noqa: F401 — trigger lazy loading
            from features.registry import registry
            self._registry = registry
        return self._registry

    # ── Main API ───────────────────────────────────────────────

    def collect(
        self,
        last_result: ToolResult | None = None,
        screenshot: bool = False,
    ) -> Observation:
        """Collect a full environment observation.

        Args:
            last_result: The most recent tool execution result to attach.
            screenshot: Whether to capture a screenshot (slower).

        Returns:
            An Observation dataclass with current state.
        """
        reg = self._get_registry()

        obs = Observation(
            timestamp=time.time(),
            lastActionResult=last_result,
        )

        # Get active window info
        try:
            win = reg.execute("get_active_window")
            if isinstance(win, dict) and win.get("success"):
                result = win.get("result", {})
                if isinstance(result, dict):
                    obs.currentApp = result.get("process", result.get("name", ""))
                    obs.windowTitle = result.get("title", "")
        except Exception as e:
            logger.debug("get_active_window failed: %s", e)

        # OCR text extraction (best effort)
        try:
            ocr = reg.execute("ocr_extract_all")
            if isinstance(ocr, dict) and ocr.get("success"):
                texts = ocr.get("result", [])
                if isinstance(texts, list):
                    obs.ocrText = [self._clean_text(t) for t in texts[:50]]
        except Exception as e:
            logger.debug("ocr_extract_all failed: %s", e)

        # Screenshot (optional)
        if screenshot:
            try:
                ss = reg.execute("screenshot", {"format": "jpeg", "quality": 60})
                if isinstance(ss, dict) and ss.get("success"):
                    result = ss.get("result", {})
                    if isinstance(result, dict):
                        obs.screenshotPath = result.get("path", result.get("data", ""))
            except Exception as e:
                logger.debug("screenshot failed: %s", e)

        return obs

    def collect_after_action(self, result: ToolResult) -> Observation:
        """Quick observation after a tool execution.

        Shorthand for collect() that always attaches the action result
        but skips the expensive screenshot.
        """
        return self.collect(last_result=result, screenshot=False)

    def collect_with_screenshot(self, result: ToolResult | None = None) -> Observation:
        """Full observation including screenshot (slower)."""
        return self.collect(last_result=result, screenshot=True)

    # ─── Helpers ──────────────────────────────────────────────

    @staticmethod
    def _clean_text(t: Any) -> str:
        """Normalize an OCR text entry to a clean string."""
        if isinstance(t, dict):
            return t.get("text", str(t))
        return str(t).strip()[:200]

    def detect_changes(self, before: Observation, after: Observation) -> dict:
        """Compare two observations and report what changed.

        Returns:
            Dict with keys: window_changed, new_text, lost_text, screenshot_changed
        """
        changes: dict = {}

        if before.windowTitle != after.windowTitle:
            changes["window_changed"] = {
                "from": before.windowTitle,
                "to": after.windowTitle,
            }

        before_texts = set(before.ocrText)
        after_texts = set(after.ocrText)
        new_texts = after_texts - before_texts
        if new_texts:
            changes["new_text"] = list(new_texts)[:20]

        if before.screenshotPath != after.screenshotPath:
            changes["screenshot_changed"] = True

        return changes

    def summarize(self, obs: Observation) -> str:
        """Generate a human-readable summary of an observation.

        Useful for LLM context — condenses the observation into
        a compact text representation.
        """
        parts = []
        if obs.windowTitle:
            parts.append(f"Window: {obs.windowTitle}")
        if obs.currentApp:
            parts.append(f"App: {obs.currentApp}")
        if obs.ocrText:
            # Show first 5 text items
            texts = obs.ocrText[:5]
            parts.append(f"Screen text: {' | '.join(texts)}")
        if obs.screenshotPath:
            parts.append(f"Screenshot: {obs.screenshotPath}")
        return " | ".join(parts) if parts else "(no observation data)"
