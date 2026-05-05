"""
Region Picker: 全屏透明覆盖层，鼠标拖拽选框

提供:
  RegionPicker.pick(message)       → (x, y, w, h) | None
  RegionPicker.multi_pick(message) → [dict{type, x, y, w, h, purpose}, ...] | None

参考 wheel_selector.py 的 tkinter 全屏模式实现。
"""
from __future__ import annotations
import tkinter as tk
from typing import Optional, Tuple, List


class RegionPicker:
    """全屏区域选择器（鼠标拖拽选框）"""

    @staticmethod
    def pick(message: str = "请拖拽选择屏幕区域") -> Optional[Tuple[int, int, int, int]]:
        """单次区域选择。返回 (x, y, w, h) 或 None（取消）。"""
        root, canvas, info = _create_overlay(message)
        info["mode"] = "single"

        def on_press(e):
            info["start"] = (e.x_root, e.y_root)
            _delete_rect(canvas, info)
            info["rect"] = canvas.create_rectangle(
                e.x_root, e.y_root, e.x_root, e.y_root,
                outline="#ff4444", width=3, fill="",
            )

        def on_drag(e):
            if info.get("rect") is None:
                return
            sx, sy = info["start"]
            canvas.coords(info["rect"], sx, sy, e.x_root, e.y_root)

        def on_release(e):
            if info.get("rect") is None:
                return
            sx, sy = info["start"]
            x, y = min(sx, e.x_root), min(sy, e.y_root)
            w, h = abs(e.x_root - sx), abs(e.y_root - sy)
            _safe_destroy(root)
            if w >= 5 and h >= 5:
                info["result"][0] = (x, y, w, h)

        canvas.bind("<ButtonPress-1>", on_press)
        canvas.bind("<B1-Motion>", on_drag)
        canvas.bind("<ButtonRelease-1>", on_release)

        _run(root)
        return info["result"][0]

    @staticmethod
    def multi_pick(message: str = "请框选目标：左键=点击  右键=监控  空格完成") -> Optional[List[dict]]:
        """连续区域选择。
        左键框选 → type="click"
        右键框选 → type="monitor"
        空格/ESC → 结束

        左键/右键点击后在同一个 canvas 上拖拽选框，
        释放后恢复区域列表界面，可继续框选。
        返回列表或 None（未选任何区域）。
        """
        root, canvas, info = _create_overlay(message)
        info["mode"] = "multi"

        regions: List[dict] = []
        info["regions"] = regions

        _show_multi_ui(canvas, info, regions, message)

        # ── 拖拽状态 ──
        drag = {"active": False, "start": None, "rect": None, "capture_type": None}

        def _end_drag(e):
            if not drag["active"]:
                return
            drag["active"] = False
            sx, sy = drag["start"]
            x, y = min(sx, e.x_root), min(sy, e.y_root)
            w, h = abs(e.x_root - sx), abs(e.y_root - sy)
            _delete_rect(canvas, drag)
            if w >= 5 and h >= 5:
                regions.append({
                    "type": drag["capture_type"],
                    "x": x, "y": y, "w": w, "h": h,
                    "purpose": "点击目标" if drag["capture_type"] == "click" else "监控区域",
                })
            # 恢复多选 UI
            _show_multi_ui(canvas, info, regions, message)

        def on_press(e):
            # 根据按钮决定类型 (1=左键, 3=右键)
            region_type = "click" if e.num == 1 else "monitor"
            drag["active"] = True
            drag["start"] = (e.x_root, e.y_root)
            drag["capture_type"] = region_type
            _delete_rect(canvas, drag)
            canvas.delete("all")
            drag["rect"] = canvas.create_rectangle(
                e.x_root, e.y_root, e.x_root, e.y_root,
                outline="#ff4444", width=3, fill="",
            )
            # 重绑 release 到当前拖拽
            canvas.bind("<B1-Motion>", lambda e: _on_drag(e))
            canvas.bind("<B3-Motion>", lambda e: _on_drag(e))
            canvas.bind("<ButtonRelease-1>", _end_drag)
            canvas.bind("<ButtonRelease-3>", _end_drag)

        def _on_drag(e):
            if not drag["active"] or drag["rect"] is None:
                return
            sx, sy = drag["start"]
            canvas.coords(drag["rect"], sx, sy, e.x_root, e.y_root)

        def on_space(e):
            _safe_destroy(root)
            info["result"][0] = regions if regions else None

        canvas.bind("<ButtonPress-1>", on_press)
        canvas.bind("<ButtonPress-3>", on_press)
        root.bind("<space>", on_space)

        _run(root)
        return info["result"][0]


def _create_overlay(message: str):
    root = tk.Tk()
    root.title("选择区域")
    root.attributes("-fullscreen", True)
    root.attributes("-topmost", True)
    root.attributes("-alpha", 0.3)
    root.configure(bg="black")
    root.focus_force()

    canvas = tk.Canvas(root, highlightthickness=0, bg="black", cursor="crosshair")
    canvas.pack(fill=tk.BOTH, expand=True)

    info = {
        "start": None,
        "rect": None,
        "result": [None],
    }

    def on_escape(e):
        _safe_destroy(root)

    root.bind("<Escape>", on_escape)

    return root, canvas, info


def _show_multi_ui(canvas, info, regions, message):
    """绘制 multi_pick 界面：提示文字 + 已选区域矩形 + 区域列表。"""
    canvas.delete("all")
    sw = canvas.winfo_screenwidth()
    sh = canvas.winfo_screenheight()

    canvas.create_text(sw // 2, sh // 2,
                       text=message,
                       fill="white", font=("Microsoft YaHei", 24, "bold"),
                       anchor="center")
    canvas.create_text(sw // 2, sh - 60,
                       text="左键框选=点击目标  右键框选=监控  空格=完成  ESC取消",
                       fill="#888888", font=("Microsoft YaHei", 14),
                       anchor="center")

    # 绘制已选区域
    for r in regions:
        color = "#44ff44" if r["type"] == "click" else "#ffaa00"
        canvas.create_rectangle(
            r["x"], r["y"], r["x"] + r["w"], r["y"] + r["h"],
            outline=color, width=2, dash=(4, 2),
        )
        canvas.create_text(
            r["x"] + 4, r["y"] + 4,
            text=f"{'CLICK' if r['type'] == 'click' else 'MON'}",
            fill=color, font=("Consolas", 10), anchor="nw",
        )

    # 已选区域文字列表（左上角）
    for i, r in enumerate(regions):
        tag = "CLICK" if r["type"] == "click" else "MONITOR"
        y = 30 + i * 25
        canvas.create_text(
            20, y,
            text=f"{i+1}. [{tag}] ({r['x']},{r['y']}) {r['w']}x{r['h']}",
            fill="#ffcc00", font=("Consolas", 12), anchor="w",
        )


def _delete_rect(canvas, info):
    if info.get("rect"):
        try:
            canvas.delete(info["rect"])
        except Exception:
            pass
        info["rect"] = None


def _run(root):
    try:
        root.mainloop()
    except Exception:
        pass
    _safe_destroy(root)


def _safe_destroy(root):
    try:
        root.quit()
        root.destroy()
    except Exception:
        pass


if __name__ == "__main__":
    import sys
    if "--multi" in sys.argv:
        r = RegionPicker.multi_pick()
        print(r)
    else:
        r = RegionPicker.pick()
        print(f"选中区域: x={r[0]} y={r[1]} w={r[2]} h={r[3]}" if r else "已取消")
