"""能力演示 · 第二部分：遗留 DSL 引擎（离线、0 token）。

用真正的 DSL 文本 + features.action_map.make_action_handler() 执行，覆盖遗留能力：
FOCUS / SET / CLIPBOARD_SET / CLICK / IMAGE_FIND / IMAGE_CLICK / SCROLL / SWIPE /
COLOR_CHECK / IF / SCREENSHOT / LOOP / POPUP_CLOSE，逐项对照独立 oracle 核验。

用法：
    python scripts/能力演示-第二部分-遗留DSL.py
"""
from __future__ import annotations

import ctypes
import json
import os
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PY_ROOT = REPO / "python"
OUT_DIR = REPO / "output" / "能力演示"
ORACLE = OUT_DIR / "靶场状态.json"
REPORT = OUT_DIR / "第二部分-遗留DSL报告.json"
TEMPLATE = PY_ROOT / "templates" / "演示图像.png"

os.chdir(PY_ROOT)
sys.path.insert(0, str(PY_ROOT))

try:  # DPI 感知：坐标与截图一致
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def _oracle() -> dict:
    return json.loads(ORACLE.read_text(encoding="utf-8"))


def _click(x: int, y: int) -> None:
    ctypes.windll.user32.SetCursorPos(int(x), int(y))
    time.sleep(0.05)
    ctypes.windll.user32.mouse_event(0x0002, 0, 0, 0, 0)
    ctypes.windll.user32.mouse_event(0x0004, 0, 0, 0, 0)


def _clipboard_get():
    try:
        import win32clipboard as cb
        cb.OpenClipboard()
        try:
            return cb.GetClipboardData(cb.CF_UNICODETEXT)
        finally:
            cb.CloseClipboard()
    except Exception:
        return None


def _clipboard_set(text: str) -> None:
    try:
        import win32clipboard as cb
        cb.OpenClipboard()
        cb.EmptyClipboard()
        cb.SetClipboardText(text, cb.CF_UNICODETEXT)
        cb.CloseClipboard()
    except Exception:
        pass


def main() -> int:
    import numpy as np
    import cv2
    from perception.vision import ScreenCapture
    import features._all  # noqa: F401
    from engine.executor import DSLExecutor, ExecutionContext
    from features.action_map import make_action_handler

    o = _oracle()
    win = o["window_title"]
    nonce = f"演示-剪贴板-{int(time.time())}"
    w = o["widgets"]

    screen = ScreenCapture.capture()
    b, g, r = screen[:, :, 0].astype(int), screen[:, :, 1].astype(int), screen[:, :, 2].astype(int)
    ys, xs = np.where((b > 200) & (g < 60) & (r > 200))
    TEMPLATE.parent.mkdir(parents=True, exist_ok=True)
    crop = screen[max(0, ys.min() - 6):ys.max() + 6, max(0, xs.min() - 6):xs.max() + 6]
    ok, buf = cv2.imencode(".png", crop)  # imwrite 不支持中文路径
    buf.tofile(str(TEMPLATE))

    color = o["color_bbox"]
    src, dst = o["drag_src"], o["drag_dst"]
    cb = w["click_btn"]
    cbtn = (int(cb["x"] + cb["w"] / 2), int(cb["y"] + cb["h"] / 2))
    pb = w["popup_btn"]
    _click(int(pb["x"] + pb["w"] / 2), int(pb["y"] + pb["h"] / 2))  # 先开弹窗
    time.sleep(0.6)

    dsl = "\n".join([
        f"FOCUS {win}",
        "SET stage armed",
        f"CLIPBOARD_SET {nonce}",
        f"CLICK {cbtn[0]},{cbtn[1]}",
        "IMAGE_FIND 演示图像 sim=0.85",
        "IMAGE_CLICK 演示图像 sim=0.85",
        "SCROLL -3",
        f"SWIPE {src['x']} {src['y']} {dst['x']} {dst['y']}",
        f"COLOR_CHECK {color['x']} {color['y']} {color['w']} {color['h']} 123456 60",
        "IF ${_image_found}",
        "  SCREENSHOT",
        "END",
        "LOOP 2",
        "  IMAGE_FIND 演示图像 sim=0.85",
        "END",
        "POPUP_CLOSE 演示弹窗",
    ])
    (OUT_DIR / "演示脚本.dsl").write_text(dsl, encoding="utf-8")

    saved_clip = _clipboard_get()
    executor = DSLExecutor(action_handler=make_action_handler())
    executor.set_dialog_auto(False)  # 关掉每步全窗口弹窗扫描，避免拖慢演示
    box: dict = {}

    def _run() -> None:
        try:
            box["result"] = executor.run_dsl_sync(dsl, ctx=ExecutionContext(timeout=8.0))
        except Exception as exc:  # noqa: BLE001
            box["error"] = f"{type(exc).__name__}: {exc}"

    import threading
    worker = threading.Thread(target=_run, daemon=True)
    worker.start()
    worker.join(90)
    result, error = box.get("result"), box.get("error", "")
    if worker.is_alive():
        error = "DSL 超时未结束（已放弃）"

    time.sleep(0.6)
    after = _oracle()
    if saved_clip is not None:
        _clipboard_set(saved_clip)  # 恢复用户原本的剪贴板
    dsl_ok = bool(result and result.get("success"))
    checks = [
        ("DSL 执行返回", isinstance(result, dict), error or (result or {}).get("state")),
        ("DSL 成功", dsl_ok, "是" if dsl_ok else str((result or {}).get("error") or error or "否")[:80]),
        ("坐标点击 CLICK", after["click_count"] >= 1, after["click_count"]),
        ("图文点击 IMAGE_CLICK", after["image_click_count"] >= 1, after["image_click_count"]),
        ("拖拽 SWIPE", after["drag_count"] >= 1, after["drag_count"]),
        ("滚轮 SCROLL", after["scroll_events"] >= 1, after["scroll_events"]),
        ("剪贴板 CLIPBOARD", after["clipboard_seen"] == nonce, after["clipboard_seen"]),
        ("关闭弹窗 POPUP_CLOSE", after["popup_closed_count"] >= 1,
         f"开着={after['popup_open']} 已关={after['popup_closed_count']}"),
    ]
    print("\n=== 第二部分：遗留 DSL 引擎（离线、0 token） ===")
    failed = 0
    for name, ok2, detail in checks:
        print(f"  [{'通过' if ok2 else '未过'}] {name}: {detail}")
        failed += 0 if ok2 else 1
    print(f"\n{len(checks) - failed}/{len(checks)} 通过")
    REPORT.write_text(json.dumps({"检查": [{"项": c[0], "通过": bool(c[1]), "详情": str(c[2])[:120]} for c in checks],
                                  "错误": error, "靶场原始快照": after}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"报告: {REPORT}")
    try:
        TEMPLATE.unlink(missing_ok=True)  # 演示结束时删掉生成的模板，保持仓库干净
    except Exception:
        pass
    sys.stdout.flush()
    os._exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
