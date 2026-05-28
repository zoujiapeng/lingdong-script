"""
MIMO Agent (backward-compatible shim) — delegates to LingdongAgent text_protocol.

Kept for compatibility with external scripts (run_mimo_demo.py, etc.).
New code should use LingdongAgent directly.
"""
import logging
import os

logger = logging.getLogger("agent.mimo")

DEFAULT_API_BASE = "https://api.xiaomimimo.com/v1"
DEFAULT_MODEL = "mimo-v2-omni"
MAX_STEPS = 30


class MimoAgent:
    """Backward-compatible shim — delegates to LingdongAgent run_text_protocol."""

    def __init__(self, api_key="", api_base="", model="",
                 event_callback=None, stop_event=None):
        from agent.agent import LingdongAgent
        self._agent = LingdongAgent(
            api_key=api_key,
            api_base=api_base,
            model=model or os.environ.get("VLM_MODEL", DEFAULT_MODEL),
            event_callback=event_callback,
            stop_event=stop_event,
        )
        self._event_cb = event_callback

    def load_tools(self):
        self._agent.load_tools()

    def run(self, goal):
        return self._agent.run_text_protocol(goal)

    def _emit(self, event, data):
        if self._event_cb:
            self._event_cb({"type": event, **data})
