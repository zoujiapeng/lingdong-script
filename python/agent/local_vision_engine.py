"""
Local Vision Engine — 本地视觉模型引擎（默认关闭）

直接在本地加载 YOLOv8 icon_detect + Florence-2-base 模型，
对屏幕截图进行 UI 元素检测和文字描述，输出 SoM 兼容的标号格式。

推理加速:
  - YOLO: onnxruntime DirectML (AMD GPU) / CPU 自动选择
  - OCR: EasyOCR (已安装) 或 Florence-2
  - 全部懒加载，首次 parse() 才初始化

用法:
    engine = LocalVisionEngine(enabled=True)
    result = engine.parse(base64_screenshot)
    if result:
        # {image_b64, label_map, count} 与 _apply_som() 完全兼容
"""

import base64
import logging
import os
import threading
import time
from typing import Optional

import numpy as np

logger = logging.getLogger("agent.local_vision")

# ── HF 缓存模型路径 ──────────────────────────────────────────
_HF_CACHE = os.path.expanduser("~/.cache/huggingface/hub")
_FLORENCE_BASE = os.path.join(_HF_CACHE, "models--microsoft--Florence-2-base", "snapshots")
_OMNIPARSER = os.path.join(_HF_CACHE, "models--microsoft--OmniParser-v2.0", "snapshots")
_MOBILENET_PATH = os.path.expanduser("~/.cache/claude/mobilenetv3-small.onnx")


def _resolve_snapshot(base_dir: str) -> Optional[str]:
    """Resolve the snapshot directory for a huggingface model."""
    if not os.path.isdir(base_dir):
        return None
    try:
        snaps = sorted(os.listdir(base_dir))
        if snaps:
            return os.path.join(base_dir, snaps[0])
    except Exception:
        return None
    return None


def _get_providers() -> list[str]:
    """Return ONNX Runtime providers ordered by preference (DML > CPU)."""
    try:
        import onnxruntime as ort
        all_p = ort.get_available_providers()
        ordered = []
        if "DmlExecutionProvider" in all_p:
            ordered.append("DmlExecutionProvider")
        ordered.append("CPUExecutionProvider")
        return ordered
    except Exception:
        return ["CPUExecutionProvider"]


# ====================================================================
#  YOLO 图标检测
# ====================================================================

class YOLODetector:
    """YOLOv8 icon_detect 模型包装器（ONNX + DirectML 加速）。

    优先加载 ONNX 格式（需手动用 ultralytics export 转换），
    回退到 .pt PyTorch 格式（CPU only）。
    """

    def __init__(self, model_path: str, providers: list[str] | None = None):
        self._model_path = model_path
        self._providers = providers or ["CPUExecutionProvider"]
        self._session = None
        self._pt_model = None
        self._lock = threading.Lock()
        self._input_name = "images"
        self._input_shape = (1, 3, 640, 640)

    def _load(self):
        if self._session is not None or self._pt_model is not None:
            return
        with self._lock:
            if self._session is not None:
                return
            # Try ONNX first
            onnx_path = self._model_path.replace(".pt", ".onnx")
            if os.path.isfile(onnx_path):
                try:
                    import onnxruntime as ort
                    self._session = ort.InferenceSession(onnx_path, providers=self._providers)
                    self._input_name = self._session.get_inputs()[0].name
                    self._input_shape = self._session.get_inputs()[0].shape
                    provider = self._providers[0] if self._providers else "CPU"
                    logger.info("YOLO ONNX loaded (%s, %s)", provider, os.path.getsize(onnx_path) // 1024 // 1024)
                    return
                except Exception as exc:
                    logger.warning("YOLO ONNX load failed, trying PyTorch: %s", exc)
            # Fallback to PyTorch
            try:
                from ultralytics import YOLO
                self._pt_model = YOLO(self._model_path)
                logger.info("YOLO PyTorch loaded (%d params)", sum(p.numel() for p in self._pt_model.model.parameters()))
            except Exception as exc:
                logger.warning("YOLO load failed: %s", exc)
                self._pt_model = False

    def detect(self, image_cv2: np.ndarray) -> list[dict]:
        """Run YOLO detection on an OpenCV image (BGR numpy array).

        Returns list of {x, y, w, h, confidence, class_id}.
        Coordinates are in original image space.
        """
        if self._session is None and self._pt_model is None:
            self._load()
        if self._session is None and (self._pt_model is None or self._pt_model is False):
            return []

        h_img, w_img = image_cv2.shape[:2]

        try:
            if self._session:
                return self._detect_onnx(image_cv2, h_img, w_img)
            else:
                return self._detect_pytorch(image_cv2, h_img, w_img)
        except Exception as exc:
            logger.warning("YOLO detection error: %s", exc)
            return []

    def _detect_onnx(self, image_cv2: np.ndarray, h_img: int, w_img: int) -> list[dict]:
        """DirectML/CPU inference via ONNX Runtime."""
        import cv2

        _, _, onnx_h, onnx_w = self._input_shape
        # Resize + normalize
        resized = cv2.resize(image_cv2, (onnx_w, onnx_h))
        blob = resized.astype(np.float32) / 255.0
        blob = np.transpose(blob, (2, 0, 1))[np.newaxis, ...]

        outputs = self._session.run(None, {self._input_name: blob})
        # YOLO11 output: (1, 5, 8400) → [center_x, center_y, w, h, max_conf]
        pred = outputs[0]  # shape (1, 5, 8400)
        pred = pred[0]  # (5, 8400)

        boxes = []
        for i in range(pred.shape[1]):
            vals = pred[:, i]
            conf = float(vals[4])
            if conf < 0.05:
                continue
            cx, cy, bw, bh = vals[:4]
            # Map from 640x640 to original image coordinates
            x1 = int((cx - bw / 2) / onnx_w * w_img)
            y1 = int((cy - bh / 2) / onnx_h * h_img)
            x2 = int((cx + bw / 2) / onnx_w * w_img)
            y2 = int((cy + bh / 2) / onnx_h * h_img)
            boxes.append({
                "x": max(0, x1),
                "y": max(0, y1),
                "w": max(1, x2 - x1),
                "h": max(1, y2 - y1),
                "confidence": conf,
                "class_id": 0,
            })

        boxes.sort(key=lambda b: -b["confidence"])
        return boxes[:100]  # cap at 100 detections

    def _detect_pytorch(self, image_cv2: np.ndarray, h_img: int, w_img: int) -> list[dict]:
        """CPU inference via PyTorch."""
        results = self._pt_model(image_cv2, verbose=False)
        boxes = []
        for r in results:
            if r.boxes is None:
                continue
            for box in r.boxes:
                x1, y1, x2, y2 = map(int, box.xyxy[0].tolist())
                conf = float(box.conf[0])
                cls_id = int(box.cls[0]) if box.cls is not None else 0
                boxes.append({
                    "x": x1, "y": y1,
                    "w": max(1, x2 - x1), "h": max(1, y2 - y1),
                    "confidence": conf, "class_id": cls_id,
                })
        boxes.sort(key=lambda b: -b["confidence"])
        return boxes[:100]


# ====================================================================
#  MobileNet V3 图标分类（ONNX + DirectML）
# ====================================================================

class MobileNetClassifier:
    """MobileNet V3 Small ONNX 图标分类器。

    对 YOLO 检出的图标区域做 ImageNet 分类。
    推理速度 ~10ms (DirectML GPU)。
    """

    def __init__(self, model_path: str, providers: list[str] | None = None):
        self._model_path = model_path
        self._providers = providers or ["CPUExecutionProvider"]
        self._session = None
        self._labels = None
        self._lock = threading.Lock()

    def _load(self):
        if self._session is not None:
            return
        with self._lock:
            if self._session is not None:
                return
            try:
                import onnxruntime as ort
                self._session = ort.InferenceSession(self._model_path, providers=self._providers)
                # Load ImageNet labels
                try:
                    from torchvision.models import MobileNet_V3_Small_Weights
                    self._labels = MobileNet_V3_Small_Weights.IMAGENET1K_V1.meta["categories"]
                except Exception:
                    self._labels = []
                logger.info("MobileNet ONNX loaded (%d labels, %s)",
                            len(self._labels), self._providers[0])
            except Exception as exc:
                logger.warning("MobileNet load failed: %s", exc)
                self._session = False

    def classify(self, image_cv2) -> list[tuple[str, float]]:
        """Classify an image region.

        Args:
            image_cv2: BGR numpy array (any size)

        Returns:
            list of (label, confidence) tuples, sorted by confidence descending.
        """
        if self._session is None or self._session is False:
            self._load()
        if self._session is None or self._session is False:
            return []

        import cv2
        try:
            blob = cv2.resize(image_cv2, (224, 224)).astype(np.float32) / 255.0
            blob = blob.transpose(2, 0, 1)[np.newaxis, ...]
            input_name = self._session.get_inputs()[0].name
            result = self._session.run(None, {input_name: blob})
            probs = result[0][0]
            top3 = probs.argsort()[-3:][::-1]
            return [(self._labels[i] if i < len(self._labels) else f"class_{i}", float(probs[i]))
                    for i in top3]
        except Exception as exc:
            logger.debug("MobileNet classify error: %s", exc)
            return []


# ====================================================================
#  Florence-2 屏幕理解（已废弃，保留 CPU fallback）
# ====================================================================

class Florence2Engine:
    """Florence-2-base 本地推理引擎。

    提供:
      - ocr(image_cv2) → list[str]
      - caption_region(image_cv2, bbox) → str
    """

    # Florence-2 任务提示词
    TASK_OCR = "<OCR>"
    TASK_OCR_WITH_REGION = "<OCR_WITH_REGION>"
    TASK_CAPTION = "<CAPTION>"
    TASK_DETAILED_CAPTION = "<DETAILED_CAPTION>"
    TASK_REGION_TO_TEXT = "<REGION_TO_TEXT>"

    def __init__(self, model_dir: str, device: str = "cpu"):
        self._model_dir = model_dir
        self._device = device
        self._model = None
        self._processor = None
        self._lock = threading.Lock()

    def _load(self):
        if self._model is not None:
            return
        with self._lock:
            if self._model is not None:
                return
            try:
                import torch
                from transformers import AutoProcessor, AutoModelForCausalLM

                logger.info("Loading Florence-2-base from %s ...", self._model_dir)

                # Device setup
                if self._device == "directml":
                    import torch_directml
                    torch_dev = torch_directml.device()
                elif self._device == "cuda":
                    torch_dev = torch.device("cuda")
                else:
                    torch_dev = torch.device("cpu")

                self._processor = AutoProcessor.from_pretrained(
                    self._model_dir,
                    trust_remote_code=True,
                )
                self._model = AutoModelForCausalLM.from_pretrained(
                    self._model_dir,
                    trust_remote_code=True,
                    torch_dtype=torch.float32,  # 避免 bfloat16 在不支持的设备上崩溃
                ).to(torch_dev)
                self._torch_device = torch_dev

                # Warm up with a small inference
                dummy = torch.zeros((1, 3, 224, 224))
                dummy = dummy.to(torch_dev)
                with torch.no_grad():
                    _ = self._model.generate(
                        pixel_values=dummy,
                        input_ids=torch.tensor([[0]]).to(torch_dev),
                        max_new_tokens=1,
                    )
                logger.info("Florence-2-base loaded on %s", self._device)
            except Exception as exc:
                logger.warning("Florence-2 load failed: %s", exc)
                self._model = False  # marker: permanently failed

    @property
    def available(self) -> bool:
        return self._model is not None and self._model is not False

    def run_task(self, task: str, image_cv2) -> str:
        """Run a Florence-2 task on the given image.

        Args:
            task: Florence-2 task prompt (<OCR>, <CAPTION>, etc.)
            image_cv2: OpenCV BGR numpy array

        Returns:
            Decoded text output from the model.
        """
        if self._model is False:
            return ""
        if self._model is None:
            self._load()
        if self._model is None or self._model is False:
            return ""

        import cv2
        import numpy as np
        import torch

        try:
            rgb = cv2.cvtColor(image_cv2, cv2.COLOR_BGR2RGB)
            inputs = self._processor(
                text=task,
                images=rgb,
                return_tensors="pt",
            )
            pixel_values = inputs["pixel_values"].to(self._torch_device)
            input_ids = inputs["input_ids"].to(self._torch_device)

            with torch.no_grad():
                generated_ids = self._model.generate(
                    pixel_values=pixel_values,
                    input_ids=input_ids,
                    max_new_tokens=512,
                    num_beams=3,
                )
            result = self._processor.batch_decode(generated_ids, skip_special_tokens=True)
            text = (result[0] if result else "").strip()
            # Remove task prefix if model echoes it
            if text.startswith(task):
                text = text[len(task):].strip()
            return text
        except Exception as exc:
            logger.warning("Florence-2 inference error: %s", exc)
            return ""

    def run_task_on_region(self, task: str, image_cv2, bbox: tuple) -> str:
        """Run a Florence-2 task on a cropped region.

        Args:
            task: Florence-2 task prompt
            image_cv2: full image
            bbox: (x, y, w, h)

        Returns:
            Text output.
        """
        import cv2
        x, y, w, h = bbox
        h_img, w_img = image_cv2.shape[:2]
        x1 = max(0, min(x, w_img - 1))
        y1 = max(0, min(y, h_img - 1))
        x2 = min(w_img, x1 + max(1, w))
        y2 = min(h_img, y1 + max(1, h))
        region = image_cv2[y1:y2, x1:x2]
        if region.size == 0:
            return ""
        return self.run_task(task, region)


# ====================================================================
#  LocalVisionEngine — 统一入口
# ====================================================================

class LocalVisionEngine:
    """本地视觉引擎，替代/补充 OmniParser 和 SoM。

    流水线:
      screenshot → [YOLO icon_detect → OCR 文字提取 → 合并 UIA → 画框编号]
                → 返回 {image_b64, label_map, count}

    YOLO 通过 ONNX Runtime (DirectML GPU / CPU) 推理，检测 UI 图标控件。
    RapidOCR 扫描全屏可见文字（ONNX Runtime，支持 DirectML）。
    UIA 控件树提供标准控件文本（外部传入，瞬时返回）。
    Florence-2-base 可选（CPU only，较慢）。

    所有模型懒加载（首次 parse() 时初始化）。
    加载失败 → 静默降级，不抛异常，不影响现有工作路径。
    """

    def __init__(self, enabled: bool = False, providers: list[str] | None = None):
        self._enabled = enabled
        self._providers = providers or _get_providers()
        self._disabled: bool = False

        # Lazy inited
        self._yolo: Optional[YOLODetector] = None
        self._mobilenet: Optional[MobileNetClassifier] = None
        self._florence: Optional[Florence2Engine] = None
        self._rapid_ocr = None  # RapidOCR engine (lazy)

        # ── Resolve model paths ──
        self._model_paths = self._resolve_model_paths()

    # ── Path resolution ──────────────────────────────────────────

    @staticmethod
    def _resolve_model_paths() -> dict:
        """Locate model files in HF cache.

        Returns {yolo_pt, florence_dir} or empty dict on failure.
        """
        paths = {}

        # YOLO icon_detect: OmniParser v2.0 → snapshots/.../icon_detect/model.pt
        omni_snap = _resolve_snapshot(_OMNIPARSER)
        if omni_snap:
            yolo_pt = os.path.join(omni_snap, "icon_detect", "model.pt")
            if os.path.isfile(yolo_pt):
                paths["yolo_pt"] = yolo_pt
                logger.info("YOLO model: %s (%d MB)", yolo_pt, os.path.getsize(yolo_pt) // 1024 // 1024)
            else:
                logger.warning("YOLO model not found at %s", yolo_pt)

        # Florence-2-base
        fl_snap = _resolve_snapshot(_FLORENCE_BASE)
        if fl_snap:
            paths["florence_dir"] = fl_snap
            logger.info("Florence-2 model: %s", fl_snap)
        else:
            logger.warning("Florence-2 model not found")

        return paths

    @property
    def available(self) -> bool:
        """Check if engine is enabled and at least YOLO model is available."""
        if not self._enabled or self._disabled:
            return False
        return bool(self._model_paths.get("yolo_pt"))

    # ── Lazy init ────────────────────────────────────────────────

    def _ensure_models(self):
        """Lazy load models on first use."""
        if self._disabled or self._yolo is not None:
            return
        try:
            y_pt = self._model_paths.get("yolo_pt")
            if y_pt:
                self._yolo = YOLODetector(y_pt, providers=self._providers)

            # MobileNet ONNX classifier (lazy, only when needed for icon labeling)
            if os.path.isfile(_MOBILENET_PATH):
                self._mobilenet = MobileNetClassifier(_MOBILENET_PATH, providers=self._providers)

            # Florence-2 is intentionally not loaded during first-pass perception.
            # It is CPU-heavy and is only useful for explicit caption/OCR fallback, while
            # generic input/control targeting needs YOLO/OCR/CV candidates to return fast.

            if not self._yolo:
                logger.warning("LocalVision: no YOLO model, disabling")
                self._disabled = True
        except Exception as exc:
            logger.warning("LocalVision init error: %s", exc)
            self._disabled = True

    def _ensure_ocr(self):
        """Lazy init RapidOCR."""
        if self._rapid_ocr is not None:
            return
        try:
            from rapidocr_onnxruntime import RapidOCR
            self._rapid_ocr = RapidOCR()
            logger.info("LocalVision: RapidOCR ready")
        except Exception as exc:
            logger.debug("RapidOCR unavailable (optional): %s", exc)
            self._rapid_ocr = False

    # ── Main parse API ───────────────────────────────────────────

    def parse(self, image_base64: str, uia_elements: list[dict] | None = None) -> Optional[dict]:
        """Process screenshot and return SoM-compatible result.

        Args:
            image_base64: base64 JPEG screenshot
            uia_elements: optional list of {x, y, w, h, text} from UIA tree.
                          If provided, YOLO detections are merged with UIA text labels.

        Returns:
            {image_b64, label_map, count} matching _apply_som() format.
            Returns None if disabled or all models failed (caller falls back to UIA+OCR).
        """
        if not self._enabled or self._disabled:
            return None

        self._ensure_models()
        if self._disabled:
            return None

        import cv2

        try:
            nparr = np.frombuffer(base64.b64decode(image_base64), np.uint8)
            img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            if img is None:
                return None
        except Exception:
            return None

        img_full = img
        h_full, w_full = img_full.shape[:2]
        scale = 1.0
        max_edge = max(h_full, w_full)
        if max_edge > 1280:
            scale = 1280.0 / float(max_edge)
            img = cv2.resize(
                img_full,
                (max(1, int(w_full * scale)), max(1, int(h_full * scale))),
                interpolation=cv2.INTER_AREA,
            )

        h_img, w_img = img.shape[:2]
        t_start = time.time()

        # ── Step 1: YOLO icon detection (DirectML/CPU) ──
        yolo_elements: list[dict] = []
        if self._yolo:
            try:
                boxes = self._yolo.detect(img)
                for box in boxes:
                    x, y, w, h = box["x"], box["y"], box["w"], box["h"]
                    if w < 12 or h < 10 or w > w_img // 2 or h > h_img // 2:
                        continue
                    yolo_elements.append({
                        "x": x, "y": y, "w": w, "h": h,
                        "confidence": box.get("confidence", 0),
                    })
            except Exception as exc:
                logger.warning("YOLO detection failed: %s", exc)
        t_yolo = time.time()

        # ── Step 2: RapidOCR full-screen text detection ──
        ocr_elements: list[dict] = []
        self._ensure_ocr()
        if self._rapid_ocr and self._rapid_ocr is not False:
            try:
                result, _ = self._rapid_ocr(img)
                if result:
                    for box, text, conf in result:
                        conf_val = float(conf) if not isinstance(conf, (int, float)) else conf
                        clean_text = text.strip() if text else ""
                        if not clean_text:
                            continue
                        # Text-only agents need single-character controls too: keypad digits,
                        # operators, hotkey letters, toolbar glyph OCR, etc. Keep them when OCR
                        # is reasonably confident instead of dropping all len==1 text.
                        if len(clean_text) == 1 and conf_val < 0.45:
                            continue
                        if len(clean_text) > 1 and conf_val <= 0.3:
                            continue
                        if text:
                            xs = [int(p[0]) for p in box]
                            ys = [int(p[1]) for p in box]
                            x1, x2 = min(xs), max(xs)
                            y1, y2 = min(ys), max(ys)
                            ocr_elements.append({
                                "text": clean_text[:60],
                                "x": x1, "y": y1,
                                "w": max(1, x2 - x1), "h": max(1, y2 - y1),
                            })
            except Exception as exc:
                logger.warning("RapidOCR failed: %s", exc)
        t_ocr = time.time()

        # ── Step 3: Merge OCR > UIA > YOLO, dedup by position ──
        seen: set = set()
        merged: list[dict] = []

        def _add(text: str, x: int, y: int, w: int, h: int):
            key = (x // 15, y // 15)
            if key in seen:
                return
            seen.add(key)
            merged.append({"text": text[:60], "x": x, "y": y, "w": w, "h": h})

        # 1. OCR text first (precise coordinates, visible on screen)
        for o_el in ocr_elements:
            _add(o_el["text"], o_el["x"], o_el["y"], o_el["w"], o_el["h"])

        # 2. UIA controls (supplement OCR)
        if uia_elements:
            for u_el in uia_elements:
                _add(u_el.get("text", ""), u_el["x"], u_el["y"], u_el["w"], u_el["h"])

        # 3. YOLO icons not covered by OCR/UIA
        for y_el in yolo_elements:
            key = (y_el["x"] // 15, y_el["y"] // 15)
            if key not in seen:
                seen.add(key)
                label = "[icon]"
                if self._mobilenet:
                    try:
                        region = img[max(0,y_el["y"]):min(h_img,y_el["y"]+y_el["h"]),
                                     max(0,y_el["x"]):min(w_img,y_el["x"]+y_el["w"])]
                        if region.size > 0:
                            top = self._mobilenet.classify(region)
                            if top and top[0][1] > 3.0:
                                label = top[0][0].replace("_", " ").replace("-", " ")
                    except Exception:
                        pass
                _add(label, y_el["x"], y_el["y"], y_el["w"], y_el["h"])

        if not merged:
            logger.debug("LocalVision: no elements detected (YOLO=%d, UIA=%s)",
                         len(yolo_elements), len(uia_elements) if uia_elements else "N/A")
            return None

        # ── Sort top-to-bottom, left-to-right ──
        merged.sort(key=lambda e: (e["y"] // 25, e["x"]))

        # ── Draw SoM boxes + numbers ──
        label_map: dict = {}
        overlaid = img_full.copy()
        max_elements = 40
        inv_scale = 1.0 / scale if scale > 0 else 1.0

        for i, elem in enumerate(merged[:max_elements]):
            idx = i + 1
            x = int(elem["x"] * inv_scale)
            y = int(elem["y"] * inv_scale)
            w = max(1, int(elem["w"] * inv_scale))
            h = max(1, int(elem["h"] * inv_scale))
            x = min(w_full - 1, max(0, x))
            y = min(h_full - 1, max(0, y))
            cx = min(w_full - 1, max(0, x + w // 2))
            cy = min(h_full - 1, max(0, y + h // 2))
            ow = min(w_full - x, w)
            oh = min(h_full - y, h)

            label_map[idx] = {"text": elem["text"][:60], "x": cx, "y": cy, "w": ow, "h": oh}

            cv2.rectangle(overlaid, (x, y), (x + ow, y + oh), (66, 133, 244), 2)
            lbl = str(idx)
            (lbl_w, lbl_h), _ = cv2.getTextSize(lbl, cv2.FONT_HERSHEY_SIMPLEX, 0.55, 2)
            bg_x1, bg_y1 = max(0, x - 1), max(0, y - lbl_h - 8)
            bg_x2, bg_y2 = min(w_full, x + lbl_w + 5), max(0, y - 1)
            cv2.rectangle(overlaid, (bg_x1, bg_y1), (bg_x2, bg_y2), (66, 133, 244), -1)
            cv2.putText(overlaid, lbl, (x, max(lbl_h, y - 4)), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 2)

        _, buf = cv2.imencode(".jpg", overlaid, [cv2.IMWRITE_JPEG_QUALITY, 85])
        result_b64 = base64.b64encode(buf).decode()

        elapsed = time.time() - t_start
        logger.info("LocalVision: %d elements (scale %.2f, YOLO %.2fs, OCR %.2fs, total %.2fs)",
                     len(label_map), scale, t_yolo - t_start, t_ocr - t_yolo, elapsed)

        return {
            "image_b64": result_b64,
            "label_map": label_map,
            "count": len(label_map),
        }


