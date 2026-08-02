"""Generic, fully local desktop target for per-node Flow/ARP E2E.

The target intentionally knows nothing about WinPilot.  It exposes ordinary
Tk widgets and writes an independent JSON state file after every observable
event so a harness can judge real OS input without trusting Runtime ``done``.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import time
import tkinter as tk
from tkinter import ttk
from typing import Any


class OfflineFlowNodeTarget:
    def __init__(self, state_file: Path, case_id: str, marker: str = "", geometry: str = "900x650") -> None:
        self.state_file = state_file.resolve()
        self.root = tk.Tk()
        self.root.title(f"Offline Flow Node Target | {case_id}")
        self.root.geometry(geometry)
        self.root.minsize(720, 520)
        self.state: dict[str, Any] = {
            "format": "offline-flow-node-oracle/1",
            "case_id": case_id,
            "pid": os.getpid(),
            "window_title": self.root.title(),
            "started_at": time.time(),
            "updated_at": time.time(),
            "focus_events": 0,
            "click_count": 0,
            "branch_yes_count": 0,
            "branch_no_count": 0,
            "entry_text": "",
            "submit_count": 0,
            "submitted_text": "",
            "key_events": [],
            "scroll_events": 0,
            "scroll_delta": 0,
            "drag_count": 0,
            "drag_start": None,
            "drag_end": None,
            "pointer_events": 0,
            "last_pointer": None,
            "hover_enter_count": 0,
            "hover_duration_ms": 0,
            "closed": False,
            "screenshot_marker": marker,
            "screenshot_marker_bbox_tk_logical": None,
            "screenshot_marker_text_sha256": hashlib.sha256(marker.encode("utf-8")).hexdigest() if marker else "",
        }
        self.marker = marker
        self.marker_canvas: tk.Canvas | None = None
        self._drag_start: tuple[int, int] | None = None
        self._last_pointer_write = 0.0
        self._hover_started = 0.0
        self._build()
        self.root.bind("<FocusIn>", self._focus, add=True)
        self.root.bind_all("<KeyPress>", self._key, add=True)
        self.root.bind_all("<Motion>", self._pointer, add=True)
        self.root.protocol("WM_DELETE_WINDOW", self._close)
        if self.marker_canvas is not None:
            self.root.after(120, self._record_marker_geometry)
        self._write_state()

    def _build(self) -> None:
        outer = ttk.Frame(self.root, padding=16)
        outer.pack(fill="both", expand=True)
        ttk.Label(
            outer,
            text="Offline Flow Node E2E Target",
            font=("Segoe UI", 18, "bold"),
        ).pack(anchor="w")
        self.status = tk.StringVar(value="READY")
        ttk.Label(outer, textvariable=self.status).pack(anchor="w", pady=(2, 12))

        if self.marker:
            digest = hashlib.sha256(self.marker.encode("utf-8")).digest()
            marker = tk.Canvas(outer, height=150, background="#0f172a", highlightthickness=0)
            marker.pack(fill="x", pady=(0, 14))
            for index, value in enumerate(digest[:12]):
                color = f"#{value:02x}{digest[(index + 7) % len(digest)]:02x}{digest[(index + 13) % len(digest)]:02x}"
                marker.create_rectangle(12 + index * 54, 14, 58 + index * 54, 60, fill=color, outline="#ffffff")
            marker.create_text(
                20,
                102,
                anchor="w",
                text=self.marker,
                fill="#ffffff",
                font=("Consolas", 24, "bold"),
            )
            self.marker_canvas = marker

        body = ttk.Panedwindow(outer, orient="horizontal")
        body.pack(fill="both", expand=True)
        left = ttk.Frame(body, padding=(0, 0, 12, 0))
        right = ttk.Frame(body)
        body.add(left, weight=1)
        body.add(right, weight=1)

        ttk.Button(left, text="CLICK TARGET", command=self._click).pack(fill="x", pady=(0, 8), ipady=10)
        branch_row = ttk.Frame(left)
        branch_row.pack(fill="x", pady=(0, 8))
        ttk.Button(branch_row, text="BRANCH YES", command=lambda: self._branch("yes")).pack(side="left", fill="x", expand=True)
        ttk.Button(branch_row, text="BRANCH NO", command=lambda: self._branch("no")).pack(side="left", fill="x", expand=True, padx=(8, 0))
        hover = tk.Label(left, text="HOVER TARGET", background="#f59e0b", foreground="white", pady=10)
        hover.pack(fill="x", pady=(0, 12))
        hover.bind("<Enter>", self._hover_enter)
        hover.bind("<Leave>", self._hover_leave)
        ttk.Label(left, text="TEXT INPUT").pack(anchor="w")
        self.entry_value = tk.StringVar()
        self.entry_value.trace_add("write", self._entry_changed)
        self.entry = ttk.Entry(left, textvariable=self.entry_value, font=("Consolas", 12))
        self.entry.pack(fill="x", pady=(4, 8), ipady=6)
        ttk.Button(left, text="SUBMIT TEXT", command=self._submit).pack(fill="x", pady=(0, 16), ipady=6)

        ttk.Label(left, text="DRAG FROM THE BLUE BOX INTO THE GREEN BOX").pack(anchor="w")
        self.drag_canvas = tk.Canvas(left, height=220, background="#f8fafc", highlightthickness=1)
        self.drag_canvas.pack(fill="both", expand=True, pady=(4, 0))
        self.drag_canvas.create_rectangle(35, 70, 145, 165, fill="#3b82f6", outline="", tags="source")
        self.drag_canvas.create_text(90, 118, text="DRAG", fill="white", font=("Segoe UI", 12, "bold"))
        self.drag_canvas.create_rectangle(285, 70, 395, 165, fill="#22c55e", outline="", tags="target")
        self.drag_canvas.create_text(340, 118, text="DROP", fill="white", font=("Segoe UI", 12, "bold"))
        self.drag_canvas.bind("<ButtonPress-1>", self._drag_press)
        self.drag_canvas.bind("<ButtonRelease-1>", self._drag_release)

        ttk.Label(right, text="SCROLL AREA").pack(anchor="w")
        scroll_frame = ttk.Frame(right)
        scroll_frame.pack(fill="both", expand=True, pady=(4, 0))
        self.scroll_canvas = tk.Canvas(scroll_frame, background="#ffffff", highlightthickness=1)
        scrollbar = ttk.Scrollbar(scroll_frame, orient="vertical", command=self.scroll_canvas.yview)
        self.scroll_canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self.scroll_canvas.pack(side="left", fill="both", expand=True)
        content = ttk.Frame(self.scroll_canvas)
        self.scroll_canvas.create_window((0, 0), window=content, anchor="nw")
        for index in range(1, 61):
            ttk.Label(content, text=f"Scrollable row {index:02d}", padding=(10, 8)).pack(anchor="w", fill="x")
        content.bind("<Configure>", lambda _event: self.scroll_canvas.configure(scrollregion=self.scroll_canvas.bbox("all")))
        for widget in (self.scroll_canvas, content):
            widget.bind("<MouseWheel>", self._scroll, add=True)
        self.root.after(250, self.entry.focus_set)

    def _record_marker_geometry(self) -> None:
        if self.marker_canvas is None or not self.root.winfo_exists():
            return
        self.root.update_idletasks()
        self.state["screenshot_marker_bbox_tk_logical"] = {
            "left": int(self.marker_canvas.winfo_rootx()),
            "top": int(self.marker_canvas.winfo_rooty()),
            "width": int(self.marker_canvas.winfo_width()),
            "height": int(self.marker_canvas.winfo_height()),
        }
        self._write_state()
        self.root.after(250, self._record_marker_geometry)

    def _write_state(self) -> None:
        self.state["updated_at"] = time.time()
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.state_file.with_name(f"{self.state_file.name}.tmp")
        temporary.write_text(json.dumps(self.state, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.state_file)

    def _focus(self, _event: tk.Event) -> None:
        self.state["focus_events"] += 1
        self._write_state()

    def _key(self, event: tk.Event) -> None:
        events = self.state["key_events"]
        events.append({"keysym": str(event.keysym), "char": str(event.char), "at": time.time()})
        del events[:-100]
        self._write_state()

    def _entry_changed(self, *_args: object) -> None:
        self.state["entry_text"] = self.entry_value.get()
        self.status.set(f"TEXT: {self.state['entry_text']}")
        self._write_state()

    def _click(self) -> None:
        self.state["click_count"] += 1
        self.status.set(f"CLICKED {self.state['click_count']}")
        self._write_state()

    def _branch(self, branch: str) -> None:
        key = f"branch_{branch}_count"
        self.state[key] += 1
        self.status.set(f"BRANCH {branch.upper()}")
        self._write_state()

    def _pointer(self, event: tk.Event) -> None:
        now = time.monotonic()
        if now - self._last_pointer_write < 0.1:
            return
        self._last_pointer_write = now
        self.state["pointer_events"] += 1
        self.state["last_pointer"] = [int(event.x_root), int(event.y_root)]
        self._write_state()

    def _hover_enter(self, _event: tk.Event) -> None:
        self._hover_started = time.monotonic()
        self.state["hover_enter_count"] += 1
        self._write_state()

    def _hover_leave(self, _event: tk.Event) -> None:
        if self._hover_started:
            self.state["hover_duration_ms"] += int((time.monotonic() - self._hover_started) * 1000)
            self._hover_started = 0.0
            self._write_state()

    def _submit(self) -> None:
        self.state["submit_count"] += 1
        self.state["submitted_text"] = self.entry_value.get()
        self.status.set(f"SUBMITTED: {self.state['submitted_text']}")
        self._write_state()

    def _scroll(self, event: tk.Event) -> str:
        delta = int(event.delta or 0)
        units = -1 if delta > 0 else 1
        self.scroll_canvas.yview_scroll(units * 3, "units")
        self.state["scroll_events"] += 1
        self.state["scroll_delta"] += delta
        self.state["scroll_position"] = list(self.scroll_canvas.yview())
        self.status.set(f"SCROLLED {self.state['scroll_events']}")
        self._write_state()
        return "break"

    def _drag_press(self, event: tk.Event) -> None:
        self._drag_start = (int(event.x), int(event.y))
        self.state["drag_start"] = list(self._drag_start)
        self._write_state()

    def _drag_release(self, event: tk.Event) -> None:
        if self._drag_start is None:
            return
        self.state["drag_count"] += 1
        self.state["drag_end"] = [int(event.x), int(event.y)]
        self.status.set(f"DRAGGED {self.state['drag_start']} -> {self.state['drag_end']}")
        self._drag_start = None
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
    parser.add_argument("--case-id", default="manual")
    parser.add_argument("--marker", default="")
    parser.add_argument("--geometry", default="900x650")
    args = parser.parse_args()
    OfflineFlowNodeTarget(
        args.state_file,
        str(args.case_id),
        marker=str(args.marker),
        geometry=str(args.geometry),
    ).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
