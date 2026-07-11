# 录制、Trace、Replay

WinPilot 的复用链路是：

```text
Agent/人类操作 -> trace -> automation-ir/1 -> Replayer / ARP / Flow editor
                                  \-> .wps.json 兼容导出
```

## Trace

运行时 trace 在 `python/winpilot_home/traces/`，属于本机运行数据，不提交。

当 `agent.llm_trace=true` 时，系统会记录 LLM 请求/响应、工具调用、截图引用和事件，用于复现模型每一步看到的信息。调试假成功时优先看 trace 和真实屏幕，不只看最终 `done`。

关键文件/事件：

- `python/winpilot_home/traces/<run_id>.jsonl`：BUS 事件流，含 `agent_step`、`agent_tool_start`、`agent_tool`、`agent_batch_action_start`、`agent_batch_action`、`done_check`、`agent_done`。
- `python/winpilot_home/traces/<run_id>/NNN_llm_request.json` / `NNN_llm_response.json`：模型每步真实输入输出。
- `before_NNN.jpg` / `after_NNN.jpg`：顶层动作前后截图。
- `batch_before_NNN.jpg` / `batch_after_NNN.jpg`：`run_action_batch` 内部子动作截图。

`GET /api/trace/{run_id}/summary` 会汇总工具耗时、done 证据、`low_latency` 和 `fast_gui` 指标。低延迟复盘优先看 summary，再打开对应截图和 LLM request/response 对照。

真实 GUI 评测可用脚本生成证据报告：

```bash
python scripts/gui_eval_run.py "用计算器计算6和7的和" --base-url http://127.0.0.1:8765
```

脚本走公开 `/api/chat`，不会直接调用内部 runner。当前原生前端目标入口走 `/api/v2/arp/runs`，会先尝试 Skill，再在 miss/fail 时进入同一个 AgentRunner；两条入口共享 RuntimeJob、ToolSession、Controller、trace 和 done gate，但路由前缀不同。

当前低延迟 GUI 靶场位于 `python/winpilot_home/eval_targets/gui_agent_target.html`。它用于测试 `run_action_batch(subgoal=...)`、自动 `screen_watch`、recent/action ROI、`observe(changed_only=true)` 和 fresh-screen done gate，不是生产功能的专属规则。

## .wps.json

当前边界：

- 新导出的 agent 脚本使用 `format: "wps/2"`；`wps/1` 只作为旧脚本兼容输入保留。
- `export_script()` 会收集顶层 `agent_tool.replay`，也会收集 `run_action_batch` 内部的 `agent_batch_action.replay`；只有 Replayer 已知的可执行工具会进入脚本，`auto_visual_verify`、`observe`、`execute_subgoal` 等观测/复合/校验事件不会变成重放动作。
- 保存前会做结构校验：未知工具、缺少 click 目标、缺少 type_text 文本、无效 `draw_strokes` 笔画、无效 loop 类型等会被拒绝，避免“能保存但不能重放”。`ctrl.if` 必须带 `cond` 对象，确实无条件时显式写 `{"always": true}`；`ctrl.loop` 的 `count` / `max_iters` 必须是数字，`while` / `until` 必须带 `cond` 对象，避免空条件循环跑到上限才停。
- Replayer 当前支持 `click`、`type_text`、`hotkey`、`launch`、`focus_window`、`scroll`、`drag`、`draw_strokes`、`wait`、`run_shell`、`run_detached`、`tray_click`，以及 `ctrl.if` / `ctrl.loop` 和 `ext.*` 便利步骤。
- 脚本如果声明 `target_window`，Replayer 会在任何动作前绑定稳定 title/process；Automation IR 声明桌面 mutation 却没有目标窗口时 fail-fast。`conditions.checks` 还可强制 `file_exists`、`process_running`、可见文本等 preflight。
- 结构化 `evidence.required` 会在步骤完成后执行 postflight；`visible.text`、`window.visible`、`file.exists`、`process.running` 或直接 verify DSL 不通过时，replay 整体失败。旧字符串 evidence 仅作为 advisory，不冒充已验证证据。
- 文本 selector 只会通过已聚焦/已锚定窗口 `fusion.observe()` 重新定位；Replayer 不会猜 `list_windows()[0]` 作为锚点。没有目标窗口时，文本重定位会失败，只有坐标型步骤能继续按录制坐标兜底。
- `elapsed_ms` 等 trace 计时字段不是重放语义，导出时会剥离；selector 文本会去掉未读数、消息数等易变噪声。

`.wps.json` 是 WinPilot 侧的语义重放格式，不是 FlowScript。

它应尽量记录：

- 工具名和参数。
- 目标窗口/元素/坐标。
- 语义 selector。
- expect 验证。
- 必要的变量和控制结构。

保存/读取接口在 WinPilot REST：

- `GET /api/scripts`
- `GET /api/script/{file}`
- `PUT /api/script/{file}`
- `POST /api/export/{run_id}`
- `POST /api/export/{run_id}/ir`
- `GET /api/script/{file}/ir`
- `GET /api/automation-ir/{file}`
- `POST /api/replay`
- `GET /api/replay/{replay_id}`
- `POST /api/replay/{replay_id}/cancel`

`POST /api/replay` 会提交统一 RuntimeJob 并返回 `replay_id`。Agent、ARP、Replay 和计划任务共享 `interactive_runtime` 排队，不再互相直接返回 busy 409；查询接口返回步骤、preflight、postflight、error 和时间戳。

## Automation IR v1

`python/winpilot_home/winpilot/automation_ir.py` 和 `src/renderer/utils/automationIR.ts` 提供规范中间层：

- `format: "automation-ir/1"`
- `id` / `name` / `source`
- `conditions` / `evidence`
- `steps` / `variables`
- `safety` / `fallback`

新保存流程、成功 trace、Apprentice draft 和 Memory Skill 默认写 `.automation.json`。Flow editor 的运行链也是 Flow state -> IR -> `arp/1`。WPS、FlowScript 和 DSL 继续作为兼容 adapter；只有 `source.kind == "wps"` 的 IR 可回转为 WPS，避免不可等价执行的 Flow 被假装成离线 replay。

## 人类演示录制

接口：

- `POST /api/human/start`
- `POST /api/human/stop`
- `GET /api/human/status`

人类录制用于生成可重放动作，不代表可以直接训练模型；如果要用于学习，必须经过语义化、验证和失败样本分析。

## 导入流程图

前端转换器：`src/renderer/utils/wpsToFlowScript.ts`。

转换边界：

- 能被现有 FlowScript 节点准确表达的步骤会落到原生节点，例如 `click` / `click_text`、`input_text`、`keyboard`、`swipe`、`wait`、`clipboard_set/get`、`screenshot`。
- `run_shell` 会导入为旧 `@feature` 注册表里的 `run_command`；shell 类型保留在 `_wps_shell` 中，供人工调整。
- `ext.clipboard_get` 的 `into` 会保留到 `clipboard_get` 节点；前端 executor 通过真实 feature bridge 读取后会写入 FlowScript runtime variables，后续 `variable_*` 条件可以继续使用。
- `draw_strokes`、`run_detached`、`tray_click`、未知 WPS step，以及 `while` / `until` 控制流会导入为带 `_requires_wps_replay` 的占位，并保留原始 payload。前端 FlowScript executor 会 fail-fast，提示必须走原始 `.wps.json` Replayer；不要把无法等价执行的节点模拟成成功。
- 普通 `tool_call` 也必须通过 `window.electronAPI.callFeature` 真实桥接执行；没有桥接时要 fail-fast，不能在浏览器预览里模拟系统/桌面动作成功。
- WPS 导入器会给转换后的节点加 `_source_wps_replay`，并给脚本设置 `settings.require_real_features=true`。前端 executor 对这些节点采取保守策略：基础点击、文字点击、输入、快捷键、滚动、拖拽、截图、窗口等待、启动/聚焦/剪贴板等会优先走真实 feature bridge；没有 bridge 或 bridge 返回失败时节点失败。手工新建的普通 FlowScript 节点仍可用于浏览器预览模拟，但这不代表它能等价重放真实桌面动作。

测试重点：

- `.wps.json` step 类型能否转换成正确 FlowScript 节点。
- 参数名是否和 `nodeDefinitions` / `dsl_flow_converter.py` 对齐。
- 转换后的流程图能否被旧 engine 执行。

## 维护原则

- Replayer 与 FlowScript converter 都要跟 `.wps.json` schema 一起演进。
- 不要让 Agent 直接编辑流程图内部格式。
- 不要把运行 trace 当成源码文档提交。
- 不要把 batch 顶层结果当成全部证据；`run_action_batch` 内部动作要看 `agent_batch_action` 和 `batch_before/batch_after`。
