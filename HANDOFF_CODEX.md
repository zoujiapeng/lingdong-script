# HANDOFF_CODEX

本文件只保留当前接手要点。更完整的长期说明见 `README.md`、`CLAUDE.md` 和 `docs/`。

## 当前代码事实

- 当前分支：`codex/low-latency-controller-next`。
- WinPilot 主体：`python/winpilot_home/winpilot/`。
- Electron IPC adapter：`python/agent/__main__.py`。
- GUI 任务：`AgentRunner`。
- 编程/仓库任务：`CodingRunner`。
- WinPilot 不直接使用 `python/features/` 的 `@feature` 注册表。
- Flow/DSL 体系仍存在：`src/renderer/`、`python/engine/`、`python/features/`。
- `.wps.json` 是 WinPilot 语义重放格式，可由 `wpsToFlowScript.ts` 转成 FlowScript。

## 最近重要变化

- `src/renderer/store/index.ts` 去掉重复 defaults/templates/persistence 逻辑。
- `agent/tool_schemas.py` 从 `agent/tools.py` 拆出 OpenAI function schema。
- `agent/tool_schemas.py` 已压缩为短描述，减少每轮 LLM 工具上下文。
- `agent/tool_result.py` 从 `agent/tools.py` 拆出共享工具返回对象。
- `agent/window_tools.py` 从 `agent/tools.py` 拆出窗口列表/扫描/聚焦/应用发现 handler。
- `agent/perception_tools.py` 从 `agent/tools.py` 拆出 observe/read_text/VLM/template/color handler。
- `agent/interaction_tools.py` 从 `agent/tools.py` 拆出 click/type/hotkey/scroll/drag/draw/wait handler。
- `agent/system_tools.py` 从 `agent/tools.py` 拆出 run_shell/run_detached/launch/tray/search/undo handler。
- `agent/plan_tools.py` 从 `agent/tools.py` 拆出 revise_plan/complete_subgoal/done/fail handler。
- `agent/file_tools.py` 从 `agent/tools.py` 拆出通用文件工具 handler。
- `agent/memory_tools.py` 从 `agent/tools.py` 拆出 remember/forget/reuse_skill handler。
- `agent/desktop_tools.py` 从 `agent/tools.py` 拆出剪贴板、鼠标细控、截图、显示器、已安装应用 handler。
- `agent/task_rules.py` 从 `agent/loop.py` 拆出 GUI 任务分类、GUI-only 限制和 done gate guard 规则。
- `agent/done_verifier.py` 从 `agent/loop.py` 拆出 done 二次核验；保留 `loop.fusion/intent/CONFIG` 兼容 patch 路径。
- `agent/context_guidance.py` 从 `agent/loop.py` 拆出 upfront plan、world advisory、stuck recovery 和 playbook 注入。
- `agent/reflection.py` 从 `agent/loop.py` 拆出反思检查点逻辑。
- `winpilot/utils/image_capture.py` 统一窗口截图转 JPEG base64。
- `python/engine/executor_types.py` 从 `python/engine/executor.py` 拆出 legacy executor 状态、上下文和控制流异常。
- `scripts/gui_eval_run.py` 走 `/api/chat` 生成真实 GUI 评测证据报告，输出到忽略目录 `python/winpilot_home/evals/`。
- `CLAUDE.md`、`docs/architecture.md`、`docs/winpilot-agent.md` 已补运行时状态链路：前端 `/api/chat` -> `server.py` `_RunState` -> runner -> `ToolSession.dispatch()` -> BUS/trace/done verifier。
- 工具级复盘方法已写入 `CLAUDE.md` 和 `docs/winpilot-agent.md`：查 `<run_id>.jsonl` 的 `agent_step/agent_tool_start/agent_tool/done_check`，再打开前后截图和 `NNN_llm_request/response.json` 对照模型输入、工具参数、真实屏幕。`run_action_batch` 内部动作另有 `agent_batch_action_start/agent_batch_action` 和 `batch_before/batch_after` 截图。
- 低延迟视觉动作路径：`run_action_batch` 在 `agent/fast_tools.py` -> `fast/controller.py`；controller 会给 `draw_strokes/drag` 追加 `auto_visual_verify`。顶层 `draw_strokes/drag` 现在也会在 `AgentRunner` 中自动包装成单动作 batch，结果里有 `wrapped_by_fast_controller` 和本地视觉变化证据。验证充分时会返回 `fast_path_advice`，提示模型直接 done 而不是再补 zoom/observe。
- `run_action_batch(subgoal="...")` 会把高层子目标写入结果和 summary；`click_element` 使用 `fast/element_cache.py` 的锚点定位，成功/失败都会返回 `anchor_locator`。缓存复用不再相信旧 `elem_id`，会用当前 snapshot 的文本/角色/相对位置重新验证，防止跨 observe 误点同编号新元素。
- UIA-off 真实验证：`20260702-230125-aed7a2` 走 `/api/chat` 完成 Paint 三角形任务，工具序列为 `launch -> observe -> draw_strokes -> done`，`draw_strokes` 结果含 `wrapped_by_fast_controller=true`、`visual_change_detected=true`、`fast_path_advice`，截图 `after_003.jpg` 与模型思考一致。
- UIA-off canvas 快速感知已实现：绘图/画布任务在 UIA 关闭、无 `query/full` observe 时走 `canvas_fast_observe`，跳过全窗口 OCR，只保留 Win32/CV/`likely_canvas` 摘要；需要文字/菜单时用 `observe(query=...)` 或 `observe(full=true)` 强制完整感知。
- 最新 UIA-off 验证：`20260702-234823-a27fea` 走 `/api/chat` 完成同一 Paint 三角形任务，工具序列 `launch -> observe -> draw_strokes -> done`，`observe=220ms`，仅 1 次 perception 且 OCR 为 `skipped=canvas_fast_observe`，done gate 直接 deterministic pass，无第二次 OCR/LLM judge；eval 预算全过，duration 52.33s、cost 0.0692 CNY。
- 普通可见动作现已自动附加 `screen_delta` 和 `action_target`：`click/type_text/hotkey/scroll/focus_window/tray_click/clipboard_paste/mouse_*` 在 `ToolSession.dispatch()` 前后做廉价帧差采样，trace 的 `result_meta` 可直接看到目标窗口、foreground 和像素变化。`screen_delta.changed` 不是完成证明，必须结合 `action_target.after.foreground_matches_target`、截图和目标窗口感知。
- 修复了一类假成功：旧逻辑会让 `type_text` 优先使用当前 foreground，导致 Codex 窗口在前台时文字打进 Codex 输入框；现在输入动作优先 session target，`launch` 找到目标后会置前，`action/verify.py` 在有目标 hwnd 时忽略无关 foreground（仅目标本身或 owner/祖先弹窗可接管）。最终代码 UIA-off 复测 `20260703-004322-6fd304` 走 `/api/chat` 完成 Notepad 输入任务，截图 `after_002.jpg` 确认文字在记事本编辑区，`type_text.result_meta.action_target.after.foreground_matches_target=true`，duration 88.61s、cost 0.06 CNY。
- `npm run clean` 可删除 `dist/`、`release/`、缓存和 pycache；`npm run clean:runtime` 才额外删 `python-runtime/`。
- `python/core/screenshot.py` 补 `capture_screen()`，兼容 legacy Robot Framework keyword。
- `server.py` 的启动守卫只拦真实 `uvicorn winpilot.server:app`，不拦测试导入。
- `test_vision_priority.py` 隔离本机 config，避免测试被 `config.json` 污染。
- `.gitignore` 增加 `_e2e_shots/`、`.plan.md`、`_draw_*.py`。

## 当前风险

- `agent/loop.py` 仍偏大，但任务规则、done 核验、上下文提示和反思检查点已拆出；主 LLM/tool loop 还在里面。
- `agent/tools.py` 已大幅拆分；剩余主要是 `ToolSession` 状态、dispatch 和目标窗口解析。
- `python/engine/executor.py` 仍是 legacy 巨型执行器，已先拆出 `executor_types.py`。
- GUI 任务必须看真实屏幕/trace/文件/进程证据，不能只看 done 日志。
- 复盘时必须确认 LLM request、tool args、tool result、前后截图四者是否对得上。
- 不要为了 QQ、计算器、网易云、抖音等软件写专属规则。
- UIA-off canvas 路径已明显提速，但这只是画布/绘图类 fast path；QQ/网易云/抖音等非画布 custom UI 仍需要继续做 ROI/变化触发 OCR、输入框识别和轻量视觉控制器。

## 推荐验证

```bash
python -m pytest python/winpilot_home/tests -q
npx vitest run
npm run build
```

## 2026-07-03 本轮新增

- 分支仍是 `codex/low-latency-controller-next`。
- 新增网页靶场 `python/winpilot_home/eval_targets/gui_agent_target.html`，用于测试 GUI 局部变化、输入、弹窗、拖拽、画布和 trace 截图一致性。启动方式：在该目录运行 `python -m http.server 8877 --bind 127.0.0.1`。
- `observe(changed_only=true)` 已接入 `screen_watch` 最近 ROI：当前帧有变化用 `changed_regions`，稳定后 30 秒内用 `recent_changed_regions`，并裁剪到目标窗口后再跑 Win32/OCR/CV。无可用 ROI 时会明确返回 fallback 文案。
- 普通可见动作的 `screen_delta` 已改为动作前/动作后独立截图 diff，并把新鲜动作 ROI 写入 `screen_watch.action_changed_regions`。`observe(changed_only=true)` 优先 action ROI，再退回普通 recent ROI，避免后台 watcher 抢帧或被弹层/动画噪声污染。
- `fusion.screen_watch_autostart=true`：ToolSession 记住目标窗口或准备执行可见动作时会自动启动 watcher，AgentRunner 结束时调用 `ToolSession.close()` 停止 watcher。不要再假设模型必须手动 `screen_watch start` 才有公告板。
- `winpilot/eval/summary.py` 新增 `low_latency` 指标：screen capture、perception、action decision、action execution、post-action verify、replan frequency、VLM frequency、skill reuse rate、watch auto-start/running coverage。`fast_gui` 还会统计 batch subgoal、controller phase、anchor click/cache/failure。
- `launch`/窗口匹配修复：URL 直接走 Windows protocol handler；`window_title` 和 `window_appears` 都会用于 launch 后找窗口；标题匹配会忽略空格、下划线和连字符差异。实测把 `gui_agent_target` 正确聚焦到 Chrome 靶场窗口，避免 Codex 遮挡污染感知。
- `done_verifier.py` 新增显式可见状态目标的确定性 done：例如“确认状态卡出现 LOCAL_COUNTER”，会从 fresh screen OCR 验证，且 `LOCAL_COUNTER` 可匹配 OCR 的 `LOCAL COUNTER`。这减少了完成后等待 LLM done judge 的延迟，但不会用 agent 自己总结当证据。
- 最新成功 E2E：`20260703-025138-dcf2c6`，UIA-off，任务通过 `/api/chat` 跑。真实截图 `python/winpilot_home/traces/20260703-025138-dcf2c6/batch_after_002.jpg` 显示 `LOCAL_COUNTER_1`；trace 中 `observe(changed_only=true)` 直接在左侧状态卡 ROI 内识别到 `LOCAL COUNTER`；`done_check` 为 true。对照失败苗头 run `20260703-024228-c3525b`：第一次 changed_only 被右侧弹层/画布噪声带偏，之后修复 action/recent ROI 优先级。

## 15 项改造状态

- 已有基础：子目标/batch 雏形、自动 watcher、局部/action ROI observe、动作独立 screen_delta、目标窗口保护、canvas fast path、部分 world model、锚点缓存复验、部分 skill replay、可见证据 done gate、低延迟指标。
- 半成品：多层视觉调度、视觉锚点点击、状态机、探索慢复用快、预测式感知、延迟指标。它们有文件和局部逻辑，但没有形成稳定闭环。
- 未完成：真正轻量 UI 模型/YOLO 常驻内环、视觉伺服、LLM 只处理异常、跨软件泛化 skill、完整性能 dashboard。后续不要为了单个软件写专属规则。
