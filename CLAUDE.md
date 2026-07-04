# AI 接手速览

这份文件只写最短事实，避免 AI 读太多旧文档。

## 项目主线

1. `python/winpilot_home/winpilot/`
   - 当前 WinPilot desktop agent；用户始终面对一个 Agent。
   - 唯一 runner：`agent/loop.py` 的 `AgentRunner`。
   - GUI / Computer Use 是默认主 Runtime；编程/仓库任务由 `agent/runtime_profile.py` 在同一 runner 内启用 Coding Runtime。
   - `agent/coding.py` 只保留兼容别名，不再实现独立编程子 agent。

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
| `python/winpilot_home/winpilot/agent/tool_schemas.py` | 统一 Agent OpenAI function schema；Coding Runtime 激活时动态加入 repo 工具 |
| `python/winpilot_home/winpilot/agent/task_rules.py` | 任务分类、GUI-only guard、coding runtime 规则辅助函数 |
| `python/winpilot_home/winpilot/agent/runtime_profile.py` | 每次 run 的 RuntimeProfile，决定是否启用 Coding Runtime |
| `python/winpilot_home/winpilot/agent/done_verifier.py` | GUI agent done 二次核验 mixin |
| `python/winpilot_home/winpilot/agent/context_guidance.py` | GUI agent planning/world/playbook 上下文提示 mixin |
| `python/winpilot_home/winpilot/agent/reflection.py` | GUI agent 反思检查点 mixin |
| `python/winpilot_home/winpilot/agent/tools.py` | 统一 Agent `ToolSession` 状态、dispatch、目标窗口解析和 runtime 挂载 |
| `python/winpilot_home/winpilot/agent/window_tools.py` | GUI agent 窗口列表、扫描、聚焦、应用发现工具 mixin |
| `python/winpilot_home/winpilot/agent/perception_tools.py` | GUI agent observe/read_text/VLM/template/color 感知工具 mixin |
| `python/winpilot_home/winpilot/agent/interaction_tools.py` | GUI agent click/type/hotkey/scroll/drag/draw/wait 输入动作工具 mixin |
| `python/winpilot_home/winpilot/agent/system_tools.py` | GUI agent run_shell/run_detached/launch/tray/search/undo 系统工具 mixin |
| `python/winpilot_home/winpilot/agent/plan_tools.py` | GUI agent revise_plan/complete_subgoal/done/fail 计划和终止工具 mixin |
| `python/winpilot_home/winpilot/agent/file_tools.py` | 通用文件工具；Coding Runtime 激活时委托到 workspace-confined `CodingSession` |
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
- `agent/router.py` 只构建一个 `AgentRunner`：GUI、编程、shell、文件等都在同一主循环中作为 Runtime/工具被调度。不要把这些能力描述成用户需要面对的多个独立 agent。
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
- `execute_subgoal` 是同一 `AgentRunner/ToolSession` 里的 controller-first routine，不是第二个 agent。它把输入、单个或多个有序点击、等待、验证收进本地 controller；也可用于“可见按钮 + 可验证状态”的 click-only routine。click-only 单状态验证会优先用动作后的 `observe(changed_only=true, query=目标状态)`，避免无条件全窗口 `read_text`。结果里看 `subgoal_route=controller_first`、`actions_executed`、`controller_state`、`evidence_packet.matched_texts/missing_texts/candidate_click_labels`。如果 `needs_llm_reason=true` 且有 `candidate_click_labels`，说明锚点文字没命中但粗区域里有可见候选，LLM 应改用最相关的本地化/同义可见标签重试 `execute_subgoal`，不要转去 shell/源码查 GUI 标签。
- `execute_subgoal` 的 `subgoal` 字段只是 routine 标签，不是可执行动作。调用时必须有 `input`、`click/clicks`、`draw/draws` 或 `verify_texts`；如果只有自然语言 subgoal，`FastToolMixin` 会在进入 controller/perception 前快速返回 blocked，并提示先 `launch/find_app/focus_window/list_windows` 或补齐具体 routine。
- `execute_subgoal.draw` 支持简单画布任务：controller 用 `likely_canvas_bbox` 找可绘制区域，生成 triangle/rectangle/circle/line/check 等 strokes，必要时先执行内部 `select_color`。`select_color` 不是 Paint 专用规则：它会用 OCR 找 Colors/颜色类工具栏标签并点击图标区域打开面板，再用 HSV 小色块聚簇寻找目标色；同一 hwnd/window region 下会缓存“打开颜色面板”的按钮坐标，但不会缓存色块坐标，每次选色仍重新扫描目标色块，缓存 opener 失效时会回退 OCR opener；`evidence_packet.matched_visual_colors/missing_visual_colors/visible_colors` 是颜色是否真的画到动作区域里的证据，done gate 会拒绝最近仍缺色的绘图结果。
- `execute_subgoal.draws=[...]` 支持把相邻的简单 shape/color/region 绘图目标交给同一个 controller routine；`AgentRunner` 会从未完成 plan 中保守解析相邻绘图项并自动扩展单个 `draw`，但会跳过自定义 `strokes` 和“确认/截图/保存”等非绘图项。无额外 check 的简单绘图 plan 项可由已验证的 `draws[]` 证据自动标记完成；带 check 的绘图项仍走 `complete_subgoal`，由当前 controller 颜色证据核验。后续若 LLM 又请求已由最近 `execute_subgoal` 真实画过且颜色证据匹配的 shape/color/region，loop 会返回 `subgoal_route=controller_evidence_reuse`，让模型完成对应计划项而不是重复移动鼠标。
- 绘图任务的纯颜色核验（例如 `execute_subgoal(verify_texts=["blue","red"])` 且没有新输入/点击/绘制）可复用最近 controller 视觉颜色证据，返回 `subgoal_route=controller_visual_evidence_reuse`，避免模型为了“确认颜色”再次 OCR 颜色词或重复绘制。`draws[]` 证据还可自动完成“确认画布/图形/颜色可见”这类纯视觉 plan 项；若 plan 全部完成，该轮 `execute_subgoal` 可带 `terminal="done"`，但 loop 仍会走 `done_verifier.py`。若用户没有明确要求保存图片路径，`complete_subgoal` 会忽略 plan 里凭空出现的 `file_exists`/`file_contains` 图片检查；这只防假失败，最终 done 仍必须由 controller 颜色证据、截图或其它真实证据通过。
- `run_action_batch(subgoal="...")` 会把子目标写入工具结果和 trace summary。`click_element` 走视觉锚点/文本锚点时会返回 `anchor_locator`、`resolved_element`、`element_cache`，用于判断是缓存命中、重新定位，还是锚点没找到；如果当前 snapshot 没找到文本锚点，controller 会自己做一次 `observe(query=label)` 并重试，结果里看 `controller_auto_observe` 和 `metrics.auto_observe_count`。
- `type_into_element` 是 `run_action_batch` 内部组合动作：定位锚点、点击聚焦、可选 `Ctrl+A` 清空、再 `type_text`。结果里看 `subresults`、`controller_internal_action_count`、`metrics.compound_round_trips_saved`，用于判断卡在定位、聚焦、清空还是输入验证。
- `type_into_element` 和 `execute_subgoal` 会使用 UIA/OCR/CV 组合的通用 `input_locator`；UIA 关闭时会用 label、placeholder/search-like OCR、附近矩形、focused non-button 和 synthetic candidate，不写 QQ/计算器/靶场专属规则。
- `execute_subgoal` 在没有 snapshot 且 click 带 `expected_region` 时，会先对该粗区域做 targeted initial OCR/CV；只有裁剪区真的识别到目标 label 才使用该 snapshot，否则回退全窗口 observe。snapshot 的 region 仍保持完整目标窗口，避免 watcher 被裁剪区带偏。
- `observe(changed_only=true, query="...")` 先查 recent/action ROI；如果 ROI 里没有 query 命中，会明确标记 `Changed-only observe query fallback` 并退回全窗口 observe，避免局部变化不含目标时误导模型。
- `run_action_batch` 可能追加内部 `auto_visual_verify` 结果；这是 controller 对 `draw_strokes/drag` 动作区域做的本地截图摘要，不是 LLM 额外发起的一轮工具调用。
- 顶层 `click/type_text/hotkey/scroll/focus_window/draw_strokes/drag` 调用也会在 `AgentRunner` 内自动包装成单动作 `run_action_batch`，所以即使模型没有主动写 batch，也应在工具结果里看到 `wrapped_by_fast_controller`、`metrics`、`needs_observe` 等证据；`draw_strokes/drag` 仍用 `sync=false` 并可带 `auto_visual_verify`、`visual_change_detected`，验证充分时还会返回 `fast_path_advice`，提示模型不要再额外 zoom/observe。
- 严格 GUI-only 且用户明确禁 shell、写 `GUI only` 或写 `只用GUI` 时，`tool_schemas.py` 不会向模型暴露 `run_shell/run_detached`、`search_files` 和通用文件读写/搜索工具；执行层的 GUI-only guard 仍会兜底拦截 shell/文件/脚本工具。排查“它为什么还想走命令行或读文件”时先看该 run 的 `NNN_llm_request.json` tools 列表。
- `/api/status.runtime` 和前端顶部 `runtime` badge 会显示 repo root、branch、commit、dirty、PID、端口。怀疑测错前端/旧服务时先查这里。
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
- `planner.py` 会清理危险的按钮标签回声 check：例如 “点击 Apply/Input 按钮” 不应生成 `text_appears=Apply/Input`，因为该文字点击前就存在；只有点击后的新状态、文件、进程、窗口或显著状态 token 才适合作为 `complete_subgoal` 核验。相邻的“点击/输入控件”+“确认状态文本出现”会合并为一个带 `text_appears` 的计划项，推动 LLM 使用 `execute_subgoal(..., verify_texts=[...])`。
- `complete_subgoal` 会接受当前 `execute_subgoal:evidence_packet` 中刚匹配到的可见文本，避免 controller 已验证后再跑一轮 full verify；普通 `observe/read_text` 的历史证据仍只在任务或子目标明确“曾出现/出现过”时接受。
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
- trace summary 新增 `low_latency` 和更细的 `fast_gui` 指标：screen capture、perception、LLM decision、action execution、post-action verify、replan/VLM/skill reuse/watch coverage、batch/subgoal routine、controller phase、anchor click/cache/failure。`execute_subgoal` 会计入 `subgoal_calls/subgoal_actions/verified_subgoals` 和 `round_trips_saved`；`subgoal_elapsed_ms/subgoal_controller_elapsed_ms/subgoal_overhead_ms` 用于判断慢在 controller 内环还是前置 observe/包装开销。VLM 只有 `count>0` 或 `ms>0` 才算强视觉调用，未配置时的 0ms 占位不会污染 `vlm_call_count`。
- `done_verifier.py` 增加“显式可见状态目标”确定性收束：任务写了“确认出现 LOCAL_COUNTER”这类目标时，必须在 fresh screen OCR 中看到对应文本才会跳过 LLM done judge。OCR 容错会把 `LOCAL_COUNTER` 和 `LOCAL COUNTER` 视为同一标签。
- 最新严格 GUI-only 网页靶场验证：`20260703-231829-586255`，工具序列包含 `launch -> click -> type_text -> click(expect FAST_WRAP_OK_073) -> click(expect LOCAL_COUNTER) -> read_text -> done`；`run_shell` 未暴露；普通 `click` 结果带 `wrapped_by_fast_controller`；截图 `after_006.jpg` 显示输入框 `FAST_WRAP_OK_073` 和状态卡 `LOCAL_COUNTER_2`。对照失败 run `20260703-225332-4fb92a`：当时 agent 没输入 `FAST_WRAP_OK_071` 却把“功能等价”当完成；现在 done gate 会要求显著字面量 token 出现在最终证据中。
- 最新 controller-first GUI-only 网页靶场验证：`20260704-005515-7f70a7`，工具序列 `launch -> observe -> execute_subgoal(input+apply) -> execute_subgoal(counter) -> execute_subgoal(verify) -> done`；11 个工具、1 次 observe、done 一次通过，截图 `batch_after_003.jpg` 显示 `SUBGOAL_OK_078` 和 `LOCAL_COUNTER_2`。对照失败 run `20260704-002852-1e75a4`：`execute_subgoal` 误把全局 `LOCAL_COUNTER` 继承进输入子目标，导致输入已成功仍返回 `ok=false`；已修为只继承当前 subgoal/args 中提到的全局 literal。
- 最新 click-only controller-first 验证：`20260704-014433-99e853`，工具序列 `launch -> complete_subgoal -> execute_subgoal(click+verify LOCAL_COUNTER) -> complete_subgoal -> done`，5 个工具完成；截图 `batch_after_002.jpg` 显示 `LOCAL_COUNTER_1`，`fast_gui.subgoal_calls=1`，`vlm_call_count=0`。
- 最新 changed-only click-only 验证：`20260704-020133-18f863`，`execute_subgoal` 内部为 `click_element -> wait_stable -> observe(changed_only=true, query=LOCAL_COUNTER)`，工具耗时约 5.9s；截图 `batch_after_002.jpg` 显示 `LOCAL_COUNTER_1`。`20260704-020623-8f8903` 进一步验证 `complete_subgoal` 可直接使用 controller evidence，计划核验降到 1ms，但模型仍可能额外 `observe(query=LOCAL_COUNTER)`，后续要继续减少这类 LLM 习惯性复验。
- targeted initial ROI 验证：`20260704-022217-2eeead`。模型传入 `expected_region=left`，`execute_subgoal` 内部没有全窗口 prelude，`subgoal_overhead_ms=327`，截图 `batch_after_002.jpg` 显示 `LOCAL_COUNTER_1`；该 run 仍有一次模型主动 `observe(full=true)`，说明下一步应继续减少 LLM 执行前的习惯性全量观察。
- 本地化标签恢复验证：`20260704-031451-4d2852`。同一个 `/api/chat` GUI-only 任务要求点击英文 `Local Counter`，实际页面只有中文“只改局部状态”。首次 `execute_subgoal` 在无 session hwnd 时使用 foreground hwnd 做 targeted ROI，锚点失败后返回 `candidate_click_labels` 和 controller recovery hint，模型第二次用可见中文标签重试并成功；工具序列 `execute_subgoal(fail) -> observe -> execute_subgoal(success) -> complete_subgoal -> done`，schema 中没有 `run_shell`，截图 `batch_after_002.jpg` 显示 `LOCAL_COUNTER_1`，done gate 理由为 fresh screen evidence。
- GUI-only Paint 绘图验证：`20260704-041425-bbbe11`。任务通过 `/api/chat` 要求“打开 Windows 画图并画明显蓝色三角形”，最终状态 `done`；关键截图 `python/winpilot_home/traces/20260704-041425-bbbe11/batch_after_002.jpg` 显示 Paint 画布里的蓝色三角形。该 run 只用了 1 次 `execute_subgoal.draw`，动作序列为 `select_color -> draw_strokes -> wait_stable`，证据包报告 `matched_visual_colors=["blue"]`、`visible_colors.blue.pixels=2937`。`select_color` 会优先使用当前目标 hwnd 的窗口区域，避免沿用过期 fullscreen snapshot 导致误点工具栏图标。
- GUI-only Paint 多图形验证：`20260704-045155-a9dc21`，使用便宜模型 `mimo-v2.5`。任务要求从空白画布开始画左侧蓝色三角形和右侧红色矩形，最终 `done`；真实截图 `python/winpilot_home/traces/20260704-045155-a9dc21/batch_after_003.jpg`/手工截图显示两个图形都存在。两次 `execute_subgoal.draw` 均 `verified=true`：蓝色三角形 `visible_colors.blue.pixels=3835`，红色矩形 `visible_colors.red.pixels=2856`。该轮验证了 Paint 启动黑壳阶段的 canvas-ready wait、`draw.expected_region` 左右分区、视觉颜色 evidence ledger、以及 planner 不再把“蓝色/红色”误当 OCR 文字核验。
- 成本/步数回归验证：`20260704-053455-7b33d3`，仍使用便宜模型 `mimo-v2.5`，同一 Paint 多图形任务在 `max_steps=9` 下 8 个工具完成：`launch -> complete_subgoal -> execute_subgoal(draw blue) -> complete_subgoal -> execute_subgoal(draw red) -> complete_subgoal -> screenshot -> done`。真实截图 `python/winpilot_home/traces/manual_screen_20260704_053535.jpg` 显示蓝色三角形和红色矩形；`prompt_tokens=92863`，低于前一轮 10 步成功 run `20260704-052452-04bc28` 的 `119532`。修复点：verify-only `execute_subgoal` 没有 target hwnd 时会快速返回 `needs_target_window=true`，planner 会丢弃绘图/视觉确认项里的弱文本 app-title check，避免把“可见图形颜色存在”错误核验成“找到 Paint/画图文字”。
- schema/计划成本回归：`20260704-055208-e16c54`，同一任务在 `max_steps=8` 下完成，仍为便宜模型 `mimo-v2.5`。`execute_subgoal` tool schema 从约 6017 字符压到约 3972，GUI-only tools 从 23516 降到 21471；planning 提示改为“已有 todo 就不要 revise_plan 重述”。该 run 无多余 `revise_plan`，序列为 `launch -> complete_subgoal -> execute_subgoal(draw blue) -> complete_subgoal -> execute_subgoal(draw red) -> complete_subgoal -> screenshot -> done`，`cache_miss_tokens=5516`。`complete_subgoal` 在所有子目标完成时也会返回 `terminal="done"`，但 loop 仍会走原 `done` gate，不绕过验证。
- 绘图 guidance 压缩回归：`20260704-055941-d4c164`，同一任务、`mimo-v2.5`、`max_steps=8`。绘图任务额外 guidance 从 trace 里的约 2814 字符压到约 873 字符，真实截图 `python/winpilot_home/traces/manual_screen_20260704_060024.jpg` 仍显示蓝色三角形和红色矩形。该 run 通过 `complete_subgoal(4)` 的 `terminal="done"` 收尾，少一轮显式 `done`，`prompt_tokens=87619`、`cache_miss_tokens=5059`。
- 严格 GUI-only schema/终止核验回归：`20260704-062419-8f48dc`，同一任务、`mimo-v2.5`、`max_steps=8`。8 次 LLM request 都只有 39 个工具、tools JSON 约 19709 字符，且没有 `run_shell/run_detached/search_files/list_dir/read_file/write_file/search_text`；真实截图 `python/winpilot_home/traces/manual_screen_20260704_062508.jpg` 显示蓝色三角形和红色矩形。`done_checks[0].reason` 为 `Drawing task accepted from controller visual color evidence: red, blue.`，说明 `complete_subgoal` terminal done 没有跳过 done gate；`cache_miss_tokens=4401`。
- plan tool history 压缩回归：`20260704-063415-fcd39d`，同一任务、`mimo-v2.5`、`max_steps=8`。`complete_subgoal` 原始 trace 仍保留完整 plan，但写回 LLM history 时变成 361-487 字符 JSON 摘要（完成项、remaining_steps、next_unfinished、简短 guidance），不再重复整段 todo/Plan hint；真实截图 `python/winpilot_home/traces/manual_screen_20260704_063504.jpg` 显示两个图形，`done_checks` 仍为 controller 颜色证据 `red, blue`，`prompt_tokens=81545`、`cache_miss_tokens=4233`。
- 相邻绘图子目标合并/压缩证据回归：`20260704-073655-e7b108`，同一 Paint GUI-only 任务、便宜模型 `mimo-v2.5`、`max_steps=8`。第一次 `execute_subgoal(draw blue)` 被自动扩展为 `draws=[blue triangle left, red rectangle right]`，整轮只有 1 次 `execute_subgoal`，压缩后的 tool evidence 仍保留 `draw_strokes`，done gate 通过 controller 颜色证据 `red, blue` 接受。真实截图 `python/winpilot_home/traces/manual_screen_20260704_073736.jpg` 显示左侧蓝色三角形和右侧红色矩形，`prompt_tokens=70113`、`cache_miss_tokens=3937`。
- 绘图计划假文件检查回归：`20260704-075519-703ae4`，同一 Paint GUI-only 任务、便宜模型 `mimo-v2.5`、`max_steps=8`，最终 `done`。整轮仍只有 1 次 `execute_subgoal`，参数被扩展为 `draws=[blue triangle left, red rectangle right]`；没有 `run_shell`、没有 File/Save As 路径，也没有去验证凭空生成的图片文件。真实截图 `python/winpilot_home/traces/manual_screen_20260704_075555.jpg` 显示左侧蓝色三角形和右侧红色矩形，`done_checks[0].reason=Drawing task accepted from controller visual color evidence: red, blue.`，`prompt_tokens=59172`、`cache_miss_tokens=3428`。
- 绘图视觉确认自动完成回归：`20260704-081006-61ccb2`，同一 Paint GUI-only 任务、便宜模型 `mimo-v2.5`、`max_steps=8`，最终 `done`。工具数从上一轮 7 降到 3：`launch -> complete_subgoal(1) -> execute_subgoal(draws blue/red, terminal done)`；没有额外截图/保存/文件检查。真实截图 `python/winpilot_home/traces/manual_screen_20260704_081043.jpg` 显示左侧蓝色三角形和右侧红色矩形，done gate 仍以 controller 颜色证据 `red, blue` 通过；`prompt_tokens=27253`、`cache_miss_tokens=1269`。
- 颜色面板 opener 缓存回归：`20260704-082538-6ac6bd`，同一 Paint GUI-only 任务、便宜模型 `mimo-v2.5`，最终 `done`。这轮模型先误用一次 verify-only `execute_subgoal` 并被 scope/target guard 阻止，随后仍通过 `launch -> complete_subgoal -> execute_subgoal(draws blue/red, terminal done)` 完成；真实截图 `python/winpilot_home/traces/manual_screen_20260704_082615.jpg` 显示两个图形。第二次 `select_color` 使用缓存的面板 opener，内部耗时约 1604ms；色块仍重新扫描，避免盲点 stale swatch。
- 空 `execute_subgoal` 快速拒绝已补测试：只有 `subgoal`/`constraints`、没有具体 routine 时不会初始化 fast controller，也不会观察当前前台窗口；这用于压低模型误把 `execute_subgoal` 当 launch/discovery 工具时的成本。

## 15 项低延迟改造进度

- 已落地或有骨架：`execute_subgoal` controller-first routine、`execute_subgoal.draws[]` 相邻绘图合并、最近 controller 视觉证据复用、`run_action_batch`、batch `subgoal`、自动 `screen_watch`、动作独立 `screen_delta/action_target`、changed/action ROI observe、perception reducer、UIA-off input locator、scope guard、部分 world model、element cache 锚点复验、部分 skill replay、canvas fast observe、可见证据 done gate、低延迟 trace 指标。
- 部分落地但仍不成熟：视觉锚点点击、状态机、预测式感知、指标体系。现在已经能让清晰输入/按钮 routine 由 controller 内环处理，但还没有真正做到“所有常规动作都不经 LLM，LLM 只管异常”；`click_element` 仍主要依赖 OCR/UIA/CV 元素和锚点文本/区域，不是真正轻量视觉模型。
- 尚未真正完成：轻量 UI 视觉模型/YOLO 主循环、视觉伺服控制、跨任务稳定 skill 复用率、全量延迟 dashboard、强 VLM 只作兜底的成熟调度策略。
