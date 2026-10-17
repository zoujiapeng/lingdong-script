"""WinPilot 能力演示 · 综合靶场（完全本地，独立校验）。

它完全不知道 WinPilot 的存在，只是一个普通 Tk 窗口；每发生一次可观察的操作，
就把事实写进一个外部 JSON 状态文件。这样“运行时之外”的裁判就能判断真实的系统
输入，而不是听信运行时报的 ``done``。

覆盖的可观察事实：
  单击、文本输入/提交、键盘事件、悬停、拖拽、滚轮、剪贴板、固定颜色块、
  动画进度条、可模板匹配的图像、可 OCR 的大号文字、可关闭的弹窗。
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import time
import ctypes
import tkinter as tk
from tkinter import ttk

try:  # 让 Tk 报告真实物理像素，使靶场坐标与系统输入坐标一致
    ctypes.windll.shcore.SetProcessDpiAwareness(2)  # 逐显示器 DPI 感知
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

IMAGE_W, IMAGE_H = 300, 300
COLOR_HEX = "#123456"
OCR_TEXT = "演示文字-7391"


class ShowcaseTarget:
    def __init__(self, state_file: Path, case_id: str) -> None:
        self.state_file = state_file.resolve()
        self.root = tk.Tk()
        self.root.title(f"能力演示靶场 | {case_id}")
        self.root.geometry("1040x780+60+40")
        self.root.attributes("-topmost", True)
        self.state: dict = {
            "format": "capability-showcase-oracle/1",
            "case_id": case_id,
            "pid": os.getpid(),
            "window_title": self.root.title(),
            "started_at": time.time(),
            "updated_at": time.time(),
            "click_count": 0,
            "entry_text": "",
            "submit_count": 0,
            "submitted_text": "",
            "key_events": [],
            "hover_enter_count": 0,
            "drag_count": 0,
            "drag_start": None,
            "drag_end": None,
            "scroll_events": 0,
            "scroll_delta": 0,
            "clipboard_seen": "",
            "color_bbox": None,
            "color_rgb": None,
            "progress_value": 0,
            "progress_started": False,
            "progress_done": False,
            "image_bbox": None,
            "image_click_count": 0,
            "ocr_text": OCR_TEXT,
            "ocr_bbox": None,
            "popup_open": False,
            "popup_closed_count": 0,
            "closed": False,
        }
        self._drag_start = None
        self._clipboard_armed_at = 0.0
        self._build()
        self.root.bind_all("<KeyPress>", self._key, add=True)
        self.root.bind("<ButtonPress-1>", self._any_press, add=True)
        self.root.protocol("WM_DELETE_WINDOW", self._close)
        self.root.after(200, self._tick)
        self._write_state()

    # ── 界面布局 ──────────────────────────────────────────────────────
    def _build(self) -> None:
        outer = ttk.Frame(self.root, padding=12)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="WinPilot 能力演示靶场",
                  font=("Segoe UI", 16, "bold")).pack(anchor="w")

        cols = ttk.Frame(outer)
        cols.pack(fill="both", expand=True, pady=(8, 0))
        left = ttk.Frame(cols)
        right = ttk.Frame(cols)
        left.pack(side="left", fill="both", expand=True, padx=(0, 10))
        right.pack(side="left", fill="both", expand=True)

        # 单击
        self.click_btn = ttk.Button(left, text="点击我", command=self._click)
        self.click_btn.pack(fill="x", ipady=8)
        # 文本
        self.entry_value = tk.StringVar()
        self.entry_value.trace_add("write", lambda *_: self._entry_changed())
        ttk.Label(left, text="文本输入").pack(anchor="w", pady=(8, 0))
        self.entry = ttk.Entry(left, textvariable=self.entry_value, font=("Consolas", 11))
        self.entry.pack(fill="x")
        self.submit_btn = ttk.Button(left, text="提交", command=self._submit)
        self.submit_btn.pack(fill="x", pady=(4, 8))
        # 悬停
        self.hover_label = tk.Label(left, text="悬停区", background="#f59e0b", foreground="white", pady=8)
        self.hover_label.pack(fill="x")
        self.hover_label.bind("<Enter>", lambda _e: self._hover_enter())
        # 颜色块
        ttk.Label(left, text="颜色块").pack(anchor="w", pady=(8, 0))
        self.color_canvas = tk.Canvas(left, height=48, background=COLOR_HEX, highlightthickness=0)
        self.color_canvas.pack(fill="x")
        # 进度条
        ttk.Label(left, text="进度条").pack(anchor="w", pady=(8, 0))
        self.progress = ttk.Progressbar(left, maximum=100, value=0)
        self.progress.pack(fill="x")
        self.progress_btn = ttk.Button(left, text="开始进度", command=self._start_progress)
        self.progress_btn.pack(fill="x", pady=(4, 8))
        # 弹窗
        self.popup_btn = ttk.Button(left, text="打开弹窗", command=self._open_popup)
        self.popup_btn.pack(fill="x")

        # 图像（可模板匹配）
        ttk.Label(right, text="图像（模板匹配）").pack(anchor="w")
        self.image_canvas = tk.Canvas(right, width=IMAGE_W, height=IMAGE_H,
                                      background="#ff00ff", highlightthickness=4,
                                      highlightbackground="#00ff00")
        self.image_canvas.pack(pady=(2, 8))
        self.image_canvas.create_oval(24, 24, IMAGE_W - 24, IMAGE_H - 24, outline="#ffffff", width=9)
        self.image_canvas.create_oval(78, 78, IMAGE_W - 78, IMAGE_H - 78, outline="#ffff00", width=7)
        self.image_canvas.create_text(IMAGE_W / 2, IMAGE_H / 2 - 14, text="\u2605",
                                      fill="#ffffff", font=("Segoe UI Symbol", 64, "bold"))
        self.image_canvas.bind("<Button-1>", self._image_click)
        # OCR 大号文字
        ttk.Label(right, text="OCR 文字").pack(anchor="w")
        self.ocr_label = tk.Label(right, text=OCR_TEXT, font=("Consolas", 22, "bold"),
                                  background="#ffffff", foreground="#000000", padx=8, pady=6)
        self.ocr_label.pack(fill="x")
        # 拖拽
        ttk.Label(right, text="拖拽（蓝→绿）").pack(anchor="w", pady=(8, 0))
        self.drag_canvas = tk.Canvas(right, height=150, background="#f8fafc", highlightthickness=1)
        self.drag_canvas.pack(fill="x")
        self.drag_canvas.create_rectangle(30, 40, 130, 120, fill="#3b82f6", outline="", tags="src")
        self.drag_canvas.create_text(80, 80, text="拖", fill="white", font=("Segoe UI", 11, "bold"))
        self.drag_canvas.create_rectangle(230, 40, 330, 120, fill="#22c55e", outline="", tags="dst")
        self.drag_canvas.create_text(280, 80, text="放", fill="white", font=("Segoe UI", 11, "bold"))
        self.drag_canvas.bind("<ButtonPress-1>", self._drag_press)
        self.drag_canvas.bind("<ButtonRelease-1>", self._drag_release)
        # 滚动区
        ttk.Label(right, text="滚动区").pack(anchor="w", pady=(8, 0))
        sf = ttk.Frame(right)
        sf.pack(fill="both", expand=True)
        self.scroll_canvas = tk.Canvas(sf, background="#ffffff", highlightthickness=1)
        sb = ttk.Scrollbar(sf, orient="vertical", command=self.scroll_canvas.yview)
        self.scroll_canvas.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.scroll_canvas.pack(side="left", fill="both", expand=True)
        content = ttk.Frame(self.scroll_canvas)
        self.scroll_canvas.create_window((0, 0), window=content, anchor="nw")
        for i in range(1, 41):
            ttk.Label(content, text=f"第 {i:02d} 行", padding=(8, 5)).pack(anchor="w", fill="x")
        content.bind("<Configure>", lambda _e: self.scroll_canvas.configure(scrollregion=self.scroll_canvas.bbox("all")))
        for w in (self.scroll_canvas, content):
            w.bind("<MouseWheel>", self._scroll, add=True)
        self.root.bind_all("<MouseWheel>", self._scroll, add=True)
        self._popup: tk.Toplevel | None = None

    # ── 独立 oracle 写盘 ─────────────────────────────────────────────
    def _write_state(self) -> None:
        self.state["updated_at"] = time.time()
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_file.with_name(self.state_file.name + ".tmp")
        tmp.write_text(json.dumps(self.state, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.state_file)

    @staticmethod
    def _bbox(widget) -> dict:
        return {
            "x": int(widget.winfo_rootx()), "y": int(widget.winfo_rooty()),
            "w": int(widget.winfo_width()), "h": int(widget.winfo_height()),
        }

    def _tick(self) -> None:
        try:
            self.root.update_idletasks()
            self.state["color_bbox"] = self._bbox(self.color_canvas)
            self.state["color_rgb"] = [0x12, 0x34, 0x56]
            self.state["image_bbox"] = self._bbox(self.image_canvas)
            self.state["ocr_bbox"] = self._bbox(self.ocr_label)
            self.state["widgets"] = {name: self._bbox(widget) for name, widget in {
                "click_btn": self.click_btn,
                "entry": self.entry,
                "submit_btn": self.submit_btn,
                "hover_label": self.hover_label,
                "progress_btn": self.progress_btn,
                "popup_btn": self.popup_btn,
                "scroll_area": self.scroll_canvas,
                "image": self.image_canvas,
                "drag_canvas": self.drag_canvas,
            }.items()}
            dcx, dcy = self.drag_canvas.winfo_rootx(), self.drag_canvas.winfo_rooty()
            self.state["drag_src"] = {"x": dcx + 80, "y": dcy + 80}
            self.state["drag_dst"] = {"x": dcx + 280, "y": dcy + 80}
            if time.time() - self._clipboard_armed_at >= 0.4:
                self._clipboard_armed_at = time.time()
                try:
                    value = self.root.clipboard_get()
                    if value and value != self.state["clipboard_seen"]:
                        self.state["clipboard_seen"] = value
                except tk.TclError:
                    pass
            self._write_state()
        except Exception:
            pass
        finally:
            if self.root.winfo_exists():
                self.root.after(400, self._tick)

    # ── 事件记录 ─────────────────────────────────────────────────────
    def _any_press(self, event: tk.Event) -> None:
        self.state["all_press_count"] = self.state.get("all_press_count", 0) + 1
        self.state["last_press_root"] = [int(event.x_root), int(event.y_root)]
        self._write_state()

    def _click(self) -> None:
        self.state["click_count"] += 1
        self._write_state()

    def _entry_changed(self) -> None:
        self.state["entry_text"] = self.entry_value.get()
        self._write_state()

    def _submit(self) -> None:
        self.state["submit_count"] += 1
        self.state["submitted_text"] = self.entry_value.get()
        self._write_state()

    def _key(self, event: tk.Event) -> None:
        events = self.state["key_events"]
        events.append({"keysym": str(event.keysym), "char": str(event.char)})
        del events[:-200]
        self._write_state()

    def _hover_enter(self) -> None:
        self.state["hover_enter_count"] += 1
        self._write_state()

    def _image_click(self, _event: tk.Event) -> None:
        self.state["image_click_count"] += 1
        self._write_state()

    def _drag_press(self, event: tk.Event) -> None:
        self._drag_start = (int(event.x_root), int(event.y_root))
        self.state["drag_start"] = list(self._drag_start)
        self._write_state()

    def _drag_release(self, event: tk.Event) -> None:
        if self._drag_start is None:
            return
        self.state["drag_count"] += 1
        self.state["drag_end"] = [int(event.x_root), int(event.y_root)]
        self._drag_start = None
        self._write_state()

    def _scroll(self, event: tk.Event) -> str:
        delta = int(event.delta or 0)
        self.scroll_canvas.yview_scroll(-1 if delta > 0 else 1, "units")
        self.state["scroll_events"] += 1
        self.state["scroll_delta"] += delta
        self._write_state()
        return "break"

    def _start_progress(self) -> None:
        self.state["progress_started"] = True
        self.state["progress_value"] = 0
        self._write_state()
        self._animate_progress(0)

    def _animate_progress(self, value: int) -> None:
        if not self.root.winfo_exists():
            return
        self.progress["value"] = value
        self.state["progress_value"] = value
        if value >= 100:
            self.state["progress_done"] = True
            self._write_state()
            return
        self._write_state()
        self.root.after(50, lambda: self._animate_progress(value + 4))

    def _open_popup(self) -> None:
        if self._popup is not None and self._popup.winfo_exists():
            return
        self._popup = tk.Toplevel(self.root)
        self._popup.title("演示弹窗")
        self._popup.geometry("360x160+%d+%d" % (self.root.winfo_rootx() + 320, self.root.winfo_rooty() + 260))
        tk.Label(self._popup, text="弹窗", font=("Segoe UI", 20, "bold")).pack(expand=True)
        self._popup.protocol("WM_DELETE_WINDOW", self._popup_closed)
        self.state["popup_open"] = True
        self._write_state()

    def _popup_closed(self) -> None:
        self.state["popup_open"] = False
        self.state["popup_closed_count"] += 1
        if self._popup is not None:
            try:
                self._popup.destroy()
            except Exception:
                pass
            self._popup = None
        self._write_state()

    def _close(self) -> None:
        self.state["closed"] = True
        self._write_state()
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-file", type=Path, required=True)
    parser.add_argument("--case-id", default="演示")
    args = parser.parse_args()
    ShowcaseTarget(args.state_file, str(args.case_id)).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
