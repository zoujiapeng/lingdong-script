"""
Wheel Selector: 全屏遮罩，滚轮/方向键切换选项，回车确认

IPC 模式（被 Electron 调用）：
  接收 stdin JSON: {"options": ["a","b","c"], "values": ["x","y","z"], "title": "选择"}
  输出 stdout JSON: {"selected": "x"} 或 {"cancelled": true}
"""
from __future__ import annotations
import tkinter as tk
import json
import sys
from typing import List, Optional, Callable


class WheelSelector:
    """可复用的滚轮选择器（全屏遮罩版）"""

    @staticmethod
    def select(
        options: List[str],
        values: Optional[List[str]] = None,
        title: str = "请选择",
        default: int = 0,
        message: str = "",
        sound_feedback: Optional[Callable[[str], None]] = None,
    ) -> Optional[str]:
        result: list[Optional[str]] = [None]
        vals = values or options[:]
        selected = [max(0, min(default, len(options) - 1))]

        root = tk.Tk()
        root.title(title)
        root.attributes("-fullscreen", True)
        root.attributes("-topmost", True)
        root.attributes("-alpha", 0.4)
        root.configure(bg="black")
        root.focus_force()

        canvas = tk.Canvas(root, highlightthickness=0, bg="black")
        canvas.pack(fill=tk.BOTH, expand=True)

        sw = root.winfo_screenwidth()
        sh = root.winfo_screenheight()

        def _draw(highlight_idx: int):
            canvas.delete("all")
            if message:
                canvas.create_text(sw // 2, sh // 2 - 80 - len(options) * 20,
                                   text=message, fill="white",
                                   font=("Microsoft YaHei", 22, "bold"),
                                   anchor="center")
            start_y = sh // 2 - len(options) * 25
            for i, opt in enumerate(options):
                y = start_y + i * 50
                color = "#ffcc00" if i == highlight_idx else "#aaaaaa"
                font_spec = ("Microsoft YaHei", 18, "bold") if i == highlight_idx else ("Microsoft YaHei", 16)
                text = f"▶ {opt}" if i == highlight_idx else f"  {opt}"
                canvas.create_text(sw // 2, y, text=text, fill=color,
                                   font=font_spec, anchor="center")
            canvas.create_text(sw // 2, sh - 60,
                               text="滚轮/方向键 切换  ·  回车 确认  ·  ESC取消",
                               fill="#888888",
                               font=("Microsoft YaHei", 12), anchor="center")

        def _move(delta: int):
            selected[0] = (selected[0] + delta) % len(options)
            _draw(selected[0])
            if sound_feedback:
                try:
                    sound_feedback(options[selected[0]])
                except Exception:
                    pass

        def _confirm():
            result[0] = vals[selected[0]]
            root.quit()

        def on_wheel(event):
            _move(-1 if event.delta > 0 else 1)
            return "break"

        def on_up(event):
            _move(-1)

        def on_down(event):
            _move(1)

        def on_enter(event):
            _confirm()

        def on_escape(event):
            root.quit()

        root.bind("<MouseWheel>", on_wheel)
        root.bind("<Up>", on_up)
        root.bind("<Down>", on_down)
        root.bind("<Return>", on_enter)
        root.bind("<Escape>", on_escape)

        _draw(selected[0])

        try:
            root.mainloop()
        except Exception:
            pass
        _safe_destroy(root)
        return result[0]


def _safe_destroy(root):
    try:
        root.quit()
        root.destroy()
    except Exception:
        pass


def _ipc_main():
    """IPC 入口：被 Electron 子进程调用，stdin/stdout JSON 协议"""
    raw = sys.stdin.read().strip()
    if not raw:
        print(json.dumps({"error": "empty input"}))
        sys.exit(1)

    try:
        params = json.loads(raw)
    except json.JSONDecodeError as e:
        print(json.dumps({"error": f"invalid JSON: {e}"}))
        sys.exit(1)

    options = params.get("options", [])
    values = params.get("values")
    title = params.get("title", "请选择")
    message = params.get("message", "")
    default = params.get("default", 0)

    if not options:
        print(json.dumps({"error": "no options provided"}))
        sys.exit(1)

    result = WheelSelector.select(
        options=options,
        values=values,
        title=title,
        default=default,
        message=message,
    )

    if result is not None:
        print(json.dumps({"selected": result}))
    else:
        print(json.dumps({"cancelled": True}))


if __name__ == "__main__":
    if "--ipc" in sys.argv:
        _ipc_main()
    else:
        sel = WheelSelector.select(
            options=["截取图片 — 框选屏幕区域截图", "从资源管理器选择", "口头说明"],
            values=["capture", "file", "description"],
            title="选择图片获取方式",
            message="你要怎样添加图片？",
        )
        print(f"选中: {sel}")
