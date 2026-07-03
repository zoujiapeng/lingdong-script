# AI 接手速览

这份文件只写最短事实，避免 AI 读太多旧文档。

## 项目主线

1. `python/winpilot_home/winpilot/`
   - 当前 WinPilot desktop agent；用户始终面对一个 Agent。
   - GUI / Computer Use capability：`agent/loop.py` 的 `AgentRunner`。
   - Coding capability：`agent/coding.py` 的 `CodingRunner`。
   - 路由入口：`agent/router.py`，只做内部 capability/runner 选择。

2. `src/renderer/` + `python/engine/` + `python/features/`
   - 旧流程图/DSL/`@feature` 自动化系统。
   - 仍在用，但不是 WinPilot 主 agent 的工具来源。

## 入口文件

| 路径 | 作用 |
| --- | --- |
| `src/main/winpilotServer.ts` | Electron 启动 WinPilot FastAPI |
| `python/winpilot_home/serve.py` | WinPilot 唯一支持的服务启动入口 |
| `python/winpilot_home/winpilot/server.py` | WinPilot REST/WebSocket |
| `python/winpilot_home/winpilot/config.py` | 真实默认配置 |
| `python/winpilot_home/winpilot/agent/tool_schemas.py` | GUI agent OpenAI function schema |
| `python/winpilot_home/winpilot/agent/task_rules.py` | GUI 任务分类和 done gate 规则辅助函数 |
| `python/winpilot_home/winpilot/agent/done_verifier.py` | GUI agent done 二次核验 mixin |
| `python/winpilot_home/winpilot/agent/context_guidance.py` | GUI agent planning/world/playbook 上下文提示 mixin |
| `python/winpilot_home/winpilot/agent/reflection.py` | GUI agent 反思检查点 mixin |
| `python/winpilot_home/winpilot/agent/tools.py` | GUI agent `ToolSession` 状态、dispatch、目标窗口解析 |
| `python/winpilot_home/winpilot/agent/window_tools.py` | GUI agent 窗口列表、扫描、聚焦、应用发现工具 mixin |
| `python/winpilot_home/winpilot/agent/perception_tools.py` | GUI agent observe/read_text/VLM/template/color 感知工具 mixin |
| `python/winpilot_home/winpilot/agent/interaction_tools.py` | GUI agent click/type/hotkey/scroll/drag/draw/wait 输入动作工具 mixin |
| `python/winpilot_home/winpilot/agent/system_tools.py` | GUI agent run_shell/run_detached/launch/tray/search/undo 系统工具 mixin |
| `python/winpilot_home/winpilot/agent/plan_tools.py` | GUI agent revise_plan/complete_subgoal/done/fail 计划和终止工具 mixin |
| `python/winpilot_home/winpilot/agent/file_tools.py` | GUI agent 通用文件工具 mixin |
| `python/winpilot_home/winpilot/agent/memory_tools.py` | GUI agent 经验记忆/0-token replay 工具 mixin |
| `python/winpilot_home/winpilot/agent/desktop_tools.py` | GUI agent 剪贴板、鼠标细控、截图、显示器、应用列表工具 mixin |
| `python/winpilot_home/winpilot/agent/tool_result.py` | GUI agent 工具返回对象 |
| `python/winpilot_home/winpilot/action/executor.py` | 鼠标、键盘、窗口、剪贴板、截图等底层动作 |
| `python/winpilot_home/winpilot/perception/fusion.py` | UIA/Win32/OCR/CV/VLM 感知融合 |
| `python/engine/executor_types.py` | legacy Flow/DSL executor 状态、上下文和控制流异常 |
| `src/main/api.ts` | Electron HTTP API，默认 `127.0.0.1:7880` |

## 不要踩坑

- 不要直接运行 `uvicorn winpilot.server:app`；用 `python/winpilot_home/serve.py`。
- 不要把 `python/features/` 当成 WinPilot 工具池。
- 不要写计算器、QQ、网易云等单软件专属规则。
- 不要相信“done 日志”等于真实成功；必须看屏幕/文件/进程/窗口/trace。
- GUI 真实评测优先走 `scripts/gui_eval_run.py` 或前端 Agent 页，它们都提交 `/api/chat`，不要用离线 CLI 冒充前端路径。
- 不要把 `config.json`、`traces/`、`scripts/`、`experience_memory.json` 当源码。
- 不要继续往 `agent/loop.py`、`agent/tools.py`、`python/engine/executor.py` 塞大块新逻辑。

## 运行时状态

- 前端/Electron 调 `python/winpilot_home/serve.py` 启动 FastAPI；任务从 `/api/chat` 进入，`server.py` 的 `_RunState` 保证一次只跑一个 agent/replay/人工录制。
- `agent/router.py` 只做内部 capability 选择：GUI 任务进 `AgentRunner`，编程/仓库任务在非 GUI-only 时走 `CodingRunner`。不要把这些能力描述成用户需要面对的多个独立 agent。
- `AgentRunner.status` 状态集合：`pending`、`running`、`done`、`fail`、`reply`、`stopped`、`error`。`done` 还要经过 `done_verifier.py`，否则会被打回继续工作。
- 每一步是：构造/折叠 messages -> 调 LLM function calling -> `ToolSession.dispatch()` -> 工具 mixin 执行 -> 写 BUS 事件和 trace -> 可能注入 world/playbook/reflect。
- LLM 每步真实收到/返回的信息在 `python/winpilot_home/traces/<run_id>/NNN_*.json`；UI 事件和截图索引在 `python/winpilot_home/traces/<run_id>.jsonl`。

## 记忆和上下文

- 长期经验：`python/winpilot_home/winpilot/memory/experience.py` 读写本机 `experience_memory.json`。recipe/note 带 `conditions` 和 `evidence`；自动动作 recipe 不进 coding 任务。
- `reuse_skill` 在 `agent/memory_tools.py`，只对强任务匹配的已验证 replay 脚本做 0-token 重放。replay 成功只说明步骤执行完，仍要看当前屏幕/文本/VLM/文件等证据才能 `done`；只有 replay 成功且最终 run 完成的 recipe 才会获得正反馈，失败 replay 后兜底完成不算成功复用。
- session memory 在 `agent/session.py`，只对“再来/继续/刚才那个/previous/same/it”等指代任务注入；普通新任务不要带历史噪声。
- world advisory 在 `context_guidance.py` / `perception/world.py` / `agent/run_state.py`，只提示卡住、重复、screen watcher 变化和 need_observe，不做额外感知。
- 上下文折叠在 `agent/loop.py`：超过阈值后折叠旧大结果，checkpoint 必须保留任务目标、关键屏幕状态、失败/恢复原因和最近工具证据。

## 如何复盘工具细节

- 真实 GUI 评测用前端 Agent 页，或用 `scripts/gui_eval_run.py "任务"`；脚本同样 POST `/api/chat`，轮询 `/api/status`，读取 `/api/trace/<run_id>`，不会绕开前端路径。
- 先拿 `run_id`，看 `python/winpilot_home/traces/<run_id>.jsonl`：`agent_step` 是第几轮 LLM，`agent_tool_start` 是工具调用前，`agent_tool` 是工具结果，`agent_done` 是最终状态。
- 每个 `agent_tool` 里重点看 `tool`、`args`、`result_preview`、`terminal`、`elapsed_ms`、`replay`；动作工具还会带 `screenshot`，指向 `python/winpilot_home/traces/<run_id>/<before|after>_NNN.jpg`。
- `run_action_batch` 的内部子动作会额外写 `agent_batch_action_start/agent_batch_action` 事件；可变更屏幕的子动作会保存 `batch_before_NNN.jpg` / `batch_after_NNN.jpg`，用来核对快速内环到底有没有真的点、拖、输入、画。
- `run_action_batch(subgoal="...")` 会把子目标写入工具结果和 trace summary。`click_element` 走视觉锚点/文本锚点时会返回 `anchor_locator`、`resolved_element`、`element_cache`，用于判断是缓存命中、重新定位，还是锚点没找到；如果当前 snapshot 没找到文本锚点，controller 会自己做一次 `observe(query=label)` 并重试，结果里看 `controller_auto_observe` 和 `metrics.auto_observe_count`。
- `type_into_element` 是 `run_action_batch` 内部组合动作：定位锚点、点击聚焦、可选 `Ctrl+A` 清空、再 `type_text`。结果里看 `subresults`、`controller_internal_action_count`、`metrics.compound_round_trips_saved`，用于判断卡在定位、聚焦、清空还是输入验证。
- `observe(changed_only=true, query="...")` 先查 recent/action ROI；如果 ROI 里没有 query 命中，会明确标记 `Changed-only observe query fallback` 并退回全窗口 observe，避免局部变化不含目标时误导模型。
- `run_action_batch` 可能追加内部 `auto_visual_verify` 结果；这是 controller 对 `draw_strokes/drag` 动作区域做的本地截图摘要，不是 LLM 额外发起的一轮工具调用。
- 顶层 `draw_strokes/drag` 调用也会在 `AgentRunner` 内自动包装成单动作 `run_action_batch(sync=false)`，所以即使模型没有主动写 batch，也应在工具结果里看到 `wrapped_by_fast_controller`、`auto_visual_verify`、`visual_change_detected` 这类证据；验证充分时还会返回 `fast_path_advice`，提示模型不要再额外 zoom/observe。
- UIA 关闭的绘图/画布任务会用 `canvas_fast_observe`：普通 `observe` 跳过全窗口 OCR，只给 Win32/CV/`likely_canvas` 摘要；如果要读菜单/工具栏文字，用 `observe(query="...")` 或 `observe(full=true)`。
- 普通可见动作 `click/type_text/hotkey/scroll/focus_window/tray_click/clipboard_paste/mouse_*` 会自动附带廉价 `screen_delta`；`result_meta.action_target` 会记录动作前后 `target_hwnd`、foreground 和 `foreground_matches_target`。`screen_delta.changed=true` 只说明像素变化，不能单独证明成功；如果 foreground 不匹配 target，要先重新 focus/observe，不能据此 done。
- `launch` 找到目标窗口后会主动置前并记录 `foreground_matches_target`；`action/verify.py` 在有目标 hwnd 时只接受目标本身或 owner/祖先弹窗，不再让 Codex、日志窗口、同进程其他窗口的 OCR 文本冒充成功。
- `click/type_text/hotkey/launch/drag/scroll` 会自动保存动作前后截图；必须人工打开截图确认真实画面和 agent 的推理是否一致。
- 看 `python/winpilot_home/traces/<run_id>/NNN_llm_request.json` 和 `NNN_llm_response.json`，确认模型每一步实际收到了什么感知文本、工具结果、用户插话、视觉图片，以及它返回了哪些 tool calls。
- 如果“成功”可疑，查 `done_check` 事件和 `done_verifier.py`；没有截图/文件/进程/窗口/read_text 证据的 done 都不能算真成功。
- 如果工具看起来没生效，先对比截图，再看该工具 mixin：感知在 `perception_tools.py`，输入动作在 `interaction_tools.py`，启动/shell 在 `system_tools.py`，计划/done/fail 在 `plan_tools.py`。

## 当前清理方向

- `src/renderer/store/index.ts` 已去掉重复 defaults/templates/persistence 定义，真实来源是 `store/defaults.ts` 和 `store/persistence.ts`。
- WinPilot 截图转 base64 的公共逻辑在 `winpilot/utils/image_capture.py`。
- WinPilot GUI 工具 schema 已从 `agent/tools.py` 拆到 `agent/tool_schemas.py`，并压缩为短描述，减少 LLM 工具上下文噪音。
- 文件读写/搜索 handler 已拆到 `agent/file_tools.py`。
- 经验记忆/复用 handler 已拆到 `agent/memory_tools.py`；桌面杂项 handler 已拆到 `agent/desktop_tools.py`。
- 窗口列表/扫描/聚焦/应用发现 handler 已拆到 `agent/window_tools.py`。
- observe/read_text/VLM/template/color 感知 handler 已拆到 `agent/perception_tools.py`。
- click/type/hotkey/scroll/drag/draw/wait 输入动作 handler 已拆到 `agent/interaction_tools.py`。
- run_shell/run_detached/launch/tray/search/undo 系统工具 handler 已拆到 `agent/system_tools.py`。
- revise_plan/complete_subgoal/done/fail 计划和终止 handler 已拆到 `agent/plan_tools.py`。
- 真实 GUI 评测脚本在 `scripts/gui_eval_run.py`，它走 `/api/chat`，与前端 Agent 页面同路径，并把报告写到忽略的 `python/winpilot_home/evals/`。
- WinPilot GUI 任务分类/guard 规则已拆到 `agent/task_rules.py`；done 二次核验已拆到 `agent/done_verifier.py`；planning/world/playbook 提示已拆到 `agent/context_guidance.py`；反思检查点已拆到 `agent/reflection.py`。
- legacy Flow/DSL executor 的共享状态/异常类型已拆到 `python/engine/executor_types.py`，`executor.py` 仍只按 legacy 执行器维护。
- 旧 `python/core/screenshot.py` 保留 `capture_screen()` 兼容 legacy Robot Framework keyword。
- `npm run clean` 清 `dist/`、`release/`、测试/浏览器缓存和 pycache；`npm run clean:runtime` 才额外删 `python-runtime/`。
- `agent/tools.py` 现在主要保留 `ToolSession` 状态、dispatch 和目标窗口解析。

## 验证

```bash
python -m pytest python/winpilot_home/tests -q
npx vitest run
npm run build
```

## 2026-07-03 低延迟 GUI 更新

- 新增网页靶场：`python/winpilot_home/eval_targets/gui_agent_target.html`。用 `python -m http.server 8877 --bind 127.0.0.1` 在该目录启动后访问 `http://127.0.0.1:8877/gui_agent_target.html`。它用于制造输入、局部状态变化、弹窗、拖拽、画布等可截图验证的 GUI 变化，不是生产逻辑的专属规则。
- `launch` 现在支持直接 URL 或 `start msedge http://...` 这类包装命令，并且 `window_title` / `window_appears` 会做标题归一化匹配：`gui_agent_target` 可以匹配 `WinPilot GUI Agent Target - Google Chrome`。找到目标后会 `focus_window`，避免 Codex 或日志窗口遮挡目标。
- `screen_watch` 会保留最近变化 ROI，`observe(changed_only=true)` 会优先只观察最近 ROI。默认最近窗口是 30 秒，目的是覆盖真实 LLM 推理延迟；超过窗口会明确 fallback 到普通 observe。
- 普通可见动作的 `screen_delta` 现在用动作前/动作后独立截图计算，不再直接复用后台 watcher 的 `_prev`；watcher 另有 `action_changed_regions`，`observe(changed_only=true)` 会优先使用新鲜 action ROI，再退回普通 recent ROI。这是为了防止后台 8FPS watcher 抢帧或被弹层/动画噪声带偏。
- `fusion.screen_watch_autostart=true`：ToolSession 在记住目标窗口或执行可见动作时会自动启动 watcher，run 结束时 `ToolSession.close()` 会停止 watcher。模型仍可手动调用 `screen_watch`，但不再必须先想起 start。
- trace summary 新增 `low_latency` 和更细的 `fast_gui` 指标：screen capture、perception、LLM decision、action execution、post-action verify、replan/VLM/skill reuse/watch coverage、batch subgoal、controller phase、anchor click/cache/failure。用于判断一次任务慢在哪里，而不是凭感觉优化。
- `done_verifier.py` 增加“显式可见状态目标”确定性收束：任务写了“确认出现 LOCAL_COUNTER”这类目标时，必须在 fresh screen OCR 中看到对应文本才会跳过 LLM done judge。OCR 容错会把 `LOCAL_COUNTER` 和 `LOCAL COUNTER` 视为同一标签。
- 最新 UIA-off 网页靶场验证：`20260703-025138-dcf2c6`，工具序列 `launch -> observe -> run_action_batch(subgoal, click+wait) -> observe(changed_only=true) -> done`。截图 `batch_after_002.jpg` 显示 `LOCAL_COUNTER_1`；`changed_only` ROI 覆盖左侧状态卡并直接 OCR 到 `LOCAL COUNTER`；`done_check` 理由为 `Visible state accepted from fresh screen evidence: LOCAL_COUNTER`。

## 15 项低延迟改造进度

- 已落地或有骨架：`run_action_batch`、batch `subgoal`、自动 `screen_watch`、动作独立 `screen_delta/action_target`、changed/action ROI observe、部分 world model、element cache 锚点复验、部分 skill replay、canvas fast observe、可见证据 done gate、低延迟 trace 指标。
- 部分落地但仍不成熟：LLM 下子目标而非逐步点击、视觉锚点点击、状态机、预测式感知、指标体系。现在还没有真正做到“常规动作完全由 controller 内环处理，LLM 只管异常”；`click_element` 仍主要依赖 OCR/UIA/CV 元素和锚点文本/区域，不是真正轻量视觉模型。
- 尚未真正完成：轻量 UI 视觉模型/YOLO 主循环、视觉伺服控制、跨任务稳定 skill 复用率、全量延迟 dashboard、强 VLM 只作兜底的成熟调度策略。
