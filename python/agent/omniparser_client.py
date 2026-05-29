"""
OmniParser client — connects to OmniParser FastAPI server for screen element detection.

OmniParser (Microsoft) uses YOLOv8 icon detection + Florence-2 icon captioning + OCR
to convert a screenshot into a structured list of all interactive UI elements with
bounding boxes, types, and descriptions.

Architecture:
  LingdongAgent._apply_som()
    → OmniParserClient.parse(image_b64)    (if server available)
    → fallback: UIA + OCR                  (if server unavailable)

The OmniParser server runs as a subprocess (uvicorn) and listens on localhost:8000.
It is started lazily on first use and kept alive for the agent's lifetime.
"""

import json
import logging
import os
import subprocess
import sys
import threading
import time
from typing import Optional

import requests as _req

logger = logging.getLogger("agent.omniparser")

OMNIPARSER_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "..", "omni_parser_repo")
)
SERVER_SCRIPT = os.path.join(OMNIPARSER_DIR, "omnitool", "omniparserserver", "omniparserserver.py")
DEFAULT_PORT = 8000
SERVER_START_TIMEOUT = 60  # seconds to wait for model loading
HEALTH_CHECK_INTERVAL = 1.0


class OmniParserClient:
    """Manages the OmniParser subprocess server and provides a parse() API.

    Usage:
        client = OmniParserClient()
        result = client.parse(base64_screenshot)
        if result:
            label_map = result["label_map"]  # {1: {text, x, y, w, h}, ...}
            som_image = result["image_b64"]   # base64 of labeled screenshot
    """

    def __init__(self, port: int = DEFAULT_PORT):
        self.port = port
        self._process: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()
        self._available = False
        self._permanently_failed = False  # 防止重复启动
        self._start_attempts = 0
        self._max_start_attempts = 2  # 重试上限
        self._start_cooldown_until = 0.0  # 冷却期，避免频繁重试

    # ── Server lifecycle ──────────────────────────────────────────

    def _check_server(self) -> bool:
        """Quick health check — is the server responding?"""
        try:
            resp = _req.get(f"http://127.0.0.1:{self.port}/probe/", timeout=3)
            return resp.status_code == 200
        except Exception:
            return False

    def start(self) -> bool:
        """Start the OmniParser FastAPI server as a subprocess.

        Returns True if the server is running after startup (either
        newly started or already running).

        Transient failures (timeout, premature exit) do NOT permanently
        disable the client — up to _max_start_attempts retries with cooldown.
        Only dependency-level failures (transformers>=5.0, missing weights)
        set _permanently_failed.
        """
        if self._permanently_failed:
            return False

        with self._lock:
            if self._available:
                return True

            # Cooldown + retry limit inside lock to avoid races
            now = time.time()
            if now < self._start_cooldown_until:
                return False
            if self._start_attempts >= self._max_start_attempts:
                return False
            self._start_attempts += 1

            # Already running but not yet marked available?
            if self._process and self._process.poll() is None:
                if self._check_server():
                    self._available = True
                    return True

            # Fast dependency check — skip OmniParser if transformers >= 5.0 (incompatible)
            try:
                import transformers
                tv = tuple(int(x) for x in transformers.__version__.split(".")[:2])
                if tv >= (5, 0):
                    logger.warning(
                        "OmniParser requires transformers<5.0, got %s. Permanently disabled.",
                        transformers.__version__,
                    )
                    self._permanently_failed = True
                    return False
            except Exception:
                pass

            # Validate paths
            if not os.path.isfile(SERVER_SCRIPT):
                logger.warning("OmniParser server script not found at %s", SERVER_SCRIPT)
                return False

            weights_dir = os.path.join(OMNIPARSER_DIR, "weights")
            if not os.path.isdir(weights_dir):
                logger.warning(
                    "OmniParser weights not found at %s. "
                    "Download from huggingface.co/microsoft/OmniParser-v2.0",
                    weights_dir,
                )
                return False

            logger.info("Starting OmniParser server on port %d (attempt %d/%d)...",
                        self.port, self._start_attempts, self._max_start_attempts)
            try:
                server_script = os.path.join(os.path.dirname(SERVER_SCRIPT), "omniparserserver.py")
                self._process = subprocess.Popen(
                    [
                        sys.executable,
                        server_script,
                        "--som_model_path", os.path.join(weights_dir, "icon_detect", "model.pt"),
                        "--caption_model_name", "florence2",
                        "--caption_model_path", os.path.join(weights_dir, "icon_caption_florence"),
                        "--device", "cpu",
                        "--BOX_TRESHOLD", "0.05",
                        "--host", "127.0.0.1",
                        "--port", str(self.port),
                    ],
                    cwd=os.path.dirname(SERVER_SCRIPT),
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except Exception as exc:
                logger.warning("Failed to start OmniParser server: %s", exc)
                self._start_cooldown_until = time.time() + 30  # backoff 30s
                return False

            # Wait for server to be ready
            deadline = time.time() + SERVER_START_TIMEOUT
            while time.time() < deadline:
                if self._process.poll() is not None:
                    logger.warning("OmniParser server exited prematurely (code %d)", self._process.returncode)
                    self._process = None
                    self._start_cooldown_until = time.time() + 30
                    return False
                if self._check_server():
                    self._available = True
                    logger.info("OmniParser server ready on port %d", self.port)
                    return True
                time.sleep(HEALTH_CHECK_INTERVAL)

            logger.warning("OmniParser server failed to start within %ds", SERVER_START_TIMEOUT)
            try:
                self._process.kill()
                self._process.wait(timeout=3)
            except Exception:
                pass
            self._process = None
            self._available = False
            self._start_cooldown_until = time.time() + 60  # 1min cooldown before retry
            return False

    def stop(self):
        """Stop the OmniParser server."""
        with self._lock:
            self._available = False
            if self._process and self._process.poll() is None:
                self._process.terminate()
                try:
                    self._process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self._process.kill()
                logger.info("OmniParser server stopped")
            self._process = None

    def __del__(self):
        self.stop()

    # ── Parse API ─────────────────────────────────────────────────

    def parse(self, image_base64: str) -> Optional[dict]:
        """Send screenshot to OmniParser and return structured element list.

        Returns:
            {
                "image_b64": str,       # base64 JPEG of the labeled screenshot
                "label_map": {int: {    # element_id → {text, x, y, w, h, type, content}}
                    "text": str,
                    "x": int, "y": int, "w": int, "h": int,
                    "type": str,        # "text" | "icon"
                    "content": str,     # element description / OCR text
                },
                "count": int,
                "latency": float,       # server-side processing time in seconds
            }
            Returns None on failure (server unavailable / timeout).
        """
        if not self._available:
            if not self.start():
                return None

        try:
            resp = _req.post(
                f"http://127.0.0.1:{self.port}/parse/",
                json={"base64_image": image_base64},
                timeout=120,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            logger.warning("OmniParser parse error: %s", exc)
            self._available = False  # Mark unavailable, will retry on next call
            return None

        # Convert OmniParser format → our SoM label_map format
        parsed_list = data.get("parsed_content_list", [])
        som_image = data.get("som_image_base64", "")

        label_map = {}
        for idx, elem in enumerate(parsed_list, start=1):
            bbox = elem.get("bbox", [0, 0, 0, 0])  # [x1, y1, x2, y2]
            content = str(elem.get("content", "") or "")
            elem_type = elem.get("type", "unknown")
            # Extract readable text for display
            display = content
            if content.startswith("Text Box ID"):
                display = content.split(":", 1)[-1].strip() if ":" in content else content
            elif content.startswith("Icon Box ID"):
                display = content.split(":", 1)[-1].strip() if ":" in content else content

            x1, y1, x2, y2 = map(int, bbox[:4])
            w = max(1, x2 - x1)
            h = max(1, y2 - y1)
            label_map[idx] = {
                "text": f"[{elem_type}] {display[:40]}",
                "x": (x1 + x2) // 2,
                "y": (y1 + y2) // 2,
                "w": w,
                "h": h,
                "type": elem_type,
                "content": content,
            }

        return {
            "image_b64": som_image,
            "label_map": label_map,
            "count": len(label_map),
            "latency": data.get("latency", 0),
        }
