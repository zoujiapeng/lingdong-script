# 录制、Trace、Replay

> 本文同时覆盖 vNext trace 与 Legacy ARP/AgentRunner replay。当前 Electron
> “智能代理”入口是 `/api/v2/goals/runs`；Task Contract、客户端 `/arp/runs`、
> StageRuntime 和 AgentRunner 只属于 Studio/兼容说明，不能按本文接回产品主链。

WinPilot 的复用链路是：

```text
Agent/人类操作 -> trace -> automation-ir/1 -> Replayer / ARP / Flow editor
                                  \-> .wps.json 兼容导出
```

## Trace

运行时 trace 在 `python/winpilot_home/traces/`，属于本机运行数据，不提交。

当 `agent.llm_trace=true` 时，系统会记录 LLM 请求/响应、工具调用、截图引用和事件，用于复现模型每一步看到的信息。调试假成功时优先看 trace 和真实屏幕，不只看最终 `done`。

原生视觉帧不保证永远是全屏或永远是目标窗口。启用 `controller.target_window_capture_enabled` 后，只有经过前台、可见性、几何、任务语义和收益检查的单窗口目标才会裁剪；其他场景回退完整虚拟桌面。LLM trace 中应同时核对该帧声明的 capture scope、reason、物理 screen region、提交图片尺寸与模型提交的 `image_*` 像素；0..1000 `frame_*` 是映射后的内部/兼容坐标，不能只打开 JPEG 猜来源或混用两套坐标。

关键文件/事件：

- `python/winpilot_home/traces/<run_id>.jsonl`：BUS 事件流。vNext 主线的可重放事实来自已认证的 `physical_attempt` 与配对 `raw_receipt`；旧 trace 还可能包含 `agent_tool.replay` 和 legacy batch 子动作事件。
- `python/winpilot_home/traces/<run_id>/NNN_llm_request.json` / `NNN_llm_response.json`：模型每步真实输入输出。
- `before_NNN.jpg` / `after_NNN.jpg`：顶层动作前后截图。
- `batch_before_NNN.jpg` / `batch_after_NNN.jpg`：旧版公开 batch wrapper 留下的兼容子动作截图；当前 native 模型工具面不再产生这类宏调用。
- trace 结束时写入 `trace_integrity`，记录 accepted/written/dropped/error；停止录制会等待已进入截图阶段的 callback 落盘，不能在最后一个动作仍在生产时先宣布完整。

`GET /api/status` 的 `runtime` 还会记录 contract v2、进程加载/当前源码指纹和 loaded/current Git commit。复盘前先确认这些字段一致；源码已经变化但 Python 没重启的 run 不代表当前代码。原生工作台发现这种漂移会阻止提交新任务并要求重启。

`GET /api/trace/{run_id}/summary` 会汇总工具耗时、done 证据、`low_latency` 和 `fast_gui` 指标。低延迟复盘优先看 summary，再打开对应截图和 LLM request/response 对照。

公开 `GET /api/trace/{run_id}` 最多返回 2000 条并附带 `trace_read_integrity`，避免客户端无界加载；单 run 和 Thread 导出器使用内部全量读取，不能把公开详情上限误当成导出截断。

API 集成诊断可用脚本生成证据报告；它不等于真实客户端 E2E：

```bash
python scripts/gui_eval_run.py "用计算器计算6和7的和" --base-url http://127.0.0.1:8765
```

脚本走公开 `/api/chat`，不会直接调用内部 runner。当前原生前端目标把原始 goal
提交到 `/api/v2/goals/runs`，由一条 vNext mission/Visual Actor 链执行。显式
Studio/兼容消费者仍可经 `/api/v2/task-contracts/compile` 和 `/api/v2/arp/runs`
运行 typed stage、Skill 或确定性 Adapter；这条 Legacy 链共享部分 RuntimeJob、
桌面资源、trace 和证据基础设施，但不是 vNext fallback，也不能替代客户端 E2E。

当前低延迟 GUI 靶场位于 `python/winpilot_home/eval_targets/gui_agent_target.html`。它用于测试通用原子动作、自动 `screen_watch`、recent/action ROI、changed-only 观察和 fresh-screen done gate，不是生产功能的专属规则，也不是当前 native 客户端 E2E 入口。

画布、绘图、签名、拖拽、手写都只是同一套连续指针能力的压力样例。某次画出可识别对象只能证明该次动作链；如果没有固定任务集、多次运行、同模型/显示/DPI 配置和外部 ground truth，不能从单条 trace 宣称真实成功率。

## .wps.json

当前边界：

- 新导出的 agent 脚本使用 `format: "wps/2"`；`wps/1` 只作为旧脚本兼容输入保留。
- `export_script()` 优先从 vNext evidence journal 中配对 durable `physical_attempt` 与 `raw_receipt`，并兼容真正的 legacy run 中的顶层 `agent_tool.replay` 和 legacy batch replay。显式 vNext run 缺 journal、seal 或认证记录时只会标为 partial/拒绝动作，绝不回退读取混入 trace 的 legacy replay 字段。只有已有真实物理尝试、且 Replayer 已知的普通 ARP 动作会进入脚本；观察、模型思考、只读 connector 查询、复合编排和校验事件不会变成重放动作。
- vNext journal 按 strict UTF-8 和 strict JSON 重读：拒绝非法编码、重复 key、非有限数、非对象及非 canonical bytes；每行只能包含 `format`、`schema_version`、`sequence`、`previous_event_sha256`、`event_kind`、`mission_id`、`run_id`、`decision_id`、`action_id`、`payload`、`event_sha256` 这些精确字段。导出会校验连续 sequence、从 genesis 开始的 SHA-256 链、事件内容 hash、请求 run/mission scope，并把 journal head/count 与 `runtime_evidence_sealed` 的 seal 和完成 report 对齐；任一处不一致都不能被 trace 文本补成成功。
- trace 侧 raw receipt 缺失时，只有 journal 内同时存在相互配对的认证 `physical_attempt` 和 `raw_receipt` 才能做 journal-only recovery，并仍显式留下完整性原因。trace receipt 的 `run_id` / `mission_id` 与 seal 的 mission 必须和 journal 精确相等，缺字段也会被排除并标为 partial；seal 计数必须是真正的整数，布尔值不能冒充 `0/1`。目标进程只来自 physical attempt 的 `target_identity.executable_identity`；未认证的 trace 标题不会进入 replay，trace 进程只作一致性核对。当前 journal 没有窗口标题，因此同一进程的多窗口定位仍需用户在 Studio 补充，不能宣称窗口级精确重放。
- 独立发生的重复动作按原顺序保留，不按内容去重；每一步可以保留自己的稳定目标窗口/进程。无法可靠恢复的动作、目标或 trace 完整性必须写进 `export_manifest.reasons`，导出标成 `partial`，不能静默丢失后仍称 complete。
- `agent_plan` 只是当前 run 的工作状态投影，不会变成重放动作。旧 trace 中已经删除的公开计划/子目标事件也只按兼容元数据读取；导出的脚本只保存实际发生且可验证的动作语义。
- 保存前会做结构校验：未知工具、缺少 click 目标、缺少 type_text 文本、无效 `draw_strokes` 笔画、无效 loop 类型等会被拒绝，避免“能保存但不能重放”。`ctrl.if` 必须带 `cond` 对象，确实无条件时显式写 `{"always": true}`；`ctrl.loop` 的 `count` / `max_iters` 必须是数字，`while` / `until` 必须带 `cond` 对象，避免空条件循环跑到上限才停。
- Replayer 当前支持 `click`、`type_text`、`hotkey`、`launch`、`focus_window`、`scroll`、`drag`、`draw_strokes`、`wait`、`run_shell`、`run_detached`、`tray_click`，以及 `ctrl.if` / `ctrl.loop` 和 `ext.*` 便利步骤。
- 脚本如果声明 `target_window`，Replayer 会在任何动作前绑定稳定 title/process；Automation IR 声明桌面 mutation 却没有目标窗口时 fail-fast。`conditions.checks` 还可强制 `file_exists`、`process_running`、可见文本等 preflight。
- 结构化 `evidence.required` 会在步骤完成后执行 postflight；`visible.text`、`window.visible`、`file.exists`、`process.running` 或直接 verify DSL 不通过时，replay 整体失败。旧字符串 evidence 仅作为 advisory，不冒充已验证证据。
- 文本 selector 只会通过已聚焦/已锚定窗口 `fusion.observe()` 重新定位；Replayer 不会猜 `list_windows()[0]` 作为锚点。没有目标窗口时，文本重定位会失败，只有坐标型步骤能继续按录制坐标兜底。
- `elapsed_ms` 等 trace 计时字段不是重放语义，导出时会剥离；selector 文本会去掉未读数、消息数等易变噪声。
- Replay 重新使用当前目标窗口和当前物理坐标映射，不把录制时 JPEG 的宽高或 crop/full 选择当成永久事实。窗口、显示器、DPI 或 capture scope 改变后，语义 selector/conditions 不成立就应失败，而不是按旧截图像素盲点。
- `controller.multi_input_cooperation` 默认关闭；开启时 Replayer 也必须遵守人类优先的前台/光标所有权检查。它不能靠录制脚本绕过 Desktop Action Queue 的 `physical` single-writer，也不能把一套 Windows 光标伪装成第二鼠标。

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
- `POST /api/export/thread/{thread_id}/ir`
- `GET /api/script/{file}/ir`
- `GET /api/automation-ir/{file}`
- `POST /api/v2/automation-ir/validate`
- `POST /api/v2/automation-ir/runs`
- `POST /api/replay`
- `GET /api/replay/{replay_id}`
- `POST /api/replay/{replay_id}/cancel`

`POST /api/replay` 会提交统一 RuntimeJob 并返回 `replay_id`。Agent、ARP、Replay 和计划任务共享 `interactive_runtime` 排队，不再互相直接返回 busy 409；查询接口返回步骤、preflight、postflight、error 和时间戳。

Thread 导出以 `thread_id` 扫描全部 durable trace 文件；前端最近 40 条 run 的有界投影只作为完整性断言，不是导出范围，因此更早的同 Thread 动作不会仅因 UI 历史窗口而丢失。后端逐项核对 Thread 归属并拒绝同一 trace 混有多个 Thread identity；每个 append 周期产生的全部 `trace_integrity` segment 都要逐段核对 accepted count、dropped/write failures 和尾部事件，不只看最后一段。`source.export_manifest.coverage` 固定为 `ordinary_arp_actions`，`source_runs` 只列实际纳入的 run。前端先展示完整/部分导出和动作数，用户确认后只加载进 Studio，绝不自动运行。

当前无界扫描是 correctness-first：本机一次完整 Thread 扫描约 14 秒。曾实验只做轻量 owner scan，耗时约 1.36 秒，但在没有 durable run -> Thread owner index 时可能漏掉归属/完整性异常而 false-complete，因此该实验已完整回退。现在宁可保留约 14 秒的全量核对，也不把快但不可证明的结果标成 complete；durable owner index 尚未实现。

原生中文工作台的“技能与记忆”页包含“基础流程、技能复用、宏录制”，页标题为“流程、技能与宏录制”；“运行记录”页用于查看 run、trace 和工件。后端 API/类名仍沿用 `apprentice` 作为兼容技术名，但客户端不再把学徒模式包装成独立产品功能。页面文案已中文化，脚本格式、API、MCP、VLM、坐标和模型标识仍保留技术原名。

## Automation IR v1

`python/winpilot_home/winpilot/automation_ir.py` 和 `src/renderer/utils/automationIR.ts` 提供规范中间层：

- `format: "automation-ir/1"`
- `id` / `name` / `source`
- `conditions` / `evidence`
- `steps` / `variables`
- `safety` / `fallback`

新保存流程、成功 trace、Apprentice draft 和 Memory Skill 默认写 `.automation.json`。`runtime.kind` 明确选择 `wps_replay` 或 `arp_graph`；Flow editor 将编译后的 graph 和 projection fingerprint 一并放入 IR，后端验证后直接提交 RuntimeJob，不再执行 IR -> Flow -> graph 的往返转换。WPS、FlowScript 和 DSL 继续作为兼容 adapter；只有 `source.kind == "wps"` 的 IR 可回转为 WPS，避免不可等价执行的 Flow 被假装成离线 replay。

Task Contract 的阶段 Skill 不是整句历史任务的模糊 replay。它以 `stage_type + parameters + app_version` 为组合键，保存前置条件、参数、输出 artifact、验证条件和失效原因；命中后仍用当前应用状态做 post-verify。某一阶段 miss 或失效时，只从该 `task_stage` 继续探索，已经验证的前置阶段和 run-scoped artifact 不会被整项丢弃。

## 人类演示录制

底层录制接口：

- `POST /api/human/start`
- `POST /api/human/stop`
- `GET /api/human/status`

内置“宏录制”工作流使用版本化入口：

- `POST /api/v2/replay/apprentice/start`
- `POST /api/v2/replay/apprentice/stop`
- `GET /api/v2/replay/apprentice/status`

人类录制用于生成可重放动作，不代表可以直接训练模型；如果要用于学习，必须经过语义化、验证和失败样本分析。

当前示教录制的确定性边界：

- 宏名称是独立可选字段，不再用任务描述截断代替名称；落盘必须使用统一 Windows-safe 名称并拒绝静默覆盖。

- 录制开始时记住客户端控制窗口，控制窗口自身的点击和按键不会进入脚本；空录制或始终没有稳定目标窗口会失败，不会生成空技能。
- 每个点击、文本、快捷键和拖拽步骤都保存自己的 `target_window`。焦点切换会切分文本缓冲；跨窗口拖拽会取消，而不是把两个窗口之间的路径伪装成一个动作。
- Ctrl+V 在事件入队时快照 Unicode 剪贴板并保存为 `type_text`，避免以后回放读取到变化后的剪贴板。Ctrl+A..Z 的控制字符会还原为组合键名。
- 人类演示的点击允许没有独立像素变化，因为它可能只建立焦点；这项标记只写入示教脚本，不改变自主 Agent 点击的验证规则。整个重放仍必须由最后的结构化 postflight 外部证据判定成功。
- Replayer 在每个叶子动作前重新聚焦或解析该步目标。相同目标签名绑定的 HWND 仍存活且进程一致时允许文档标题变化；HWND 消失、进程不匹配或目标签名改变时必须重新解析并 fail closed。
- WPS 导入流程图后，一个 Flow run 共享同一 Replayer session；首节点执行一次 preflight，末节点执行一次 postflight，中间 `replay_step` 逐叶验证。控制节点本身不伪造物理动作证据。

### “整场对话沉淀为可编辑重放”的当前边界

Human Apprentice 已经闭环“人类鼠标键盘演示 -> WPS -> canonical Automation IR -> 可编辑 Flow -> provisional memory -> 真实 replay 验证晋级 -> 0-token 复用”。它属于宏录制内部的兼容 recorder，不是单独产品模式。

Agent 对话现在可以按显式 `thread_id` 聚合 durable trace；可选 `run_ids` 只作前端已知集合的完整性断言，并不限制扫描范围。导出器把 vNext evidence journal 中回执为 `input_completed=true` 的普通 ARP 物理动作按原顺序投影为一个 WPS / Automation IR。相同动作不会按内容去重，每一步保留自己的目标窗口；内部导出读取完整 trace，队列丢失、写入失败、坏行、缺失 run、线程不匹配、缺少动作目标或当前不能表达的物理动作都会进入 `export_manifest.reasons`。任何完整性标志不成立时只能明确标为 partial，前端仍允许用户把草稿载入 Studio 人工删改，但不能称为“完整重放”。

较早的 2026-08-02 可见 Electron 两轮 Thread 探针导出过 14 个普通 ARP 动作，来源 run 为
`arp-20260802-033126-e2c5f9` 和 `arp-20260802-033440-85c0f4`；当时载入 Studio 后显示
16 节点/15 连线，离线 replay `arp-20260802-033821-0bb256` 产生 0 次
`vnext_model_call`，独立剪贴板 oracle 得到长度 68、SHA-256 为
`934b79c77eb2d6120be63b8a47141c902f343affd49a14e393cc7629d755230b` 的预期文本。
这是较早版本链路的 0-model replay 证据，不能冒充当前严格 exporter 已重新 replay 成功。

本轮加入上述 journal、seal、trace segment 和 Thread 归属重校验后，当前可见导出结果是
`partial`、14 actions、1 条 integrity hint；前端只确认了用户同意后把草稿加载进 Studio，
没有自动运行，也尚未对该严格导出物做新的离线 replay。因此当前证据只支持“严格导出能诚实
交接一个可人工编辑的 partial 草稿”，不支持“当前严格 exporter 已完成端到端重放”。

较早证据中的重复 A 也被原样保留：第一轮仍运行时测试操作重新激活了 Electron，模型随后做了
额外修正和目标切换。首分歧在模型原始参数/目标选择与测试干扰；canonical 参数、
`physical_attempt` 和回放终态一致，Runtime 没有背离。因而这次 replay 链通过，但原始第二轮
“只追加一次”的口头任务不能称为干净完成；这仍只是严格重校验前的历史结论。保留多余动作
供用户在流程图中删除正是当前设计。

已知 P1 缺口是跨 decision 的 held gesture：带 `started_held` / `keep_holding` 的连续拖拽目前会被导出器拒绝。真实 run `arp-20260801-184511-c3f357`、`arp-20260731-082438-60fbe6`、`arp-20260731-063144-b3b2b4` 都因此导出 0 步。这里不能直接把多段轨迹猜成一次 drag；必须先在受控 canvas 与 slider 两个不同任务族上做外部 oracle E2E，再决定是否以及怎样合并手势语义。

覆盖范围刻意限定为 `ordinary_arp_actions`：连接器只读查询、provider 请求、模型思考、观察帧、聊天文本和未来可能出现的副作用工具不属于普通 ARP 动作，不能伪造成鼠标键盘脚本，也不能静默承诺已重放。当前不会把 Agent 对话导出自动写入 provisional memory；用户先在 Studio 检查、编辑和真实回放，之后是否晋级为技能仍沿用既有验证链。当前结论是“人类示教链已闭环；Agent Thread 的独立普通 ARP 动作聚合与可编辑交接已增强，较早版本有一次外部 oracle 的 0-model replay”，不是“整场对话的所有普通 ARP 已完备”，更不是“Agent 做过的一切副作用都已可表达”。held gesture P1 与缺少 durable owner index 仍共同阻止“所有动作”的结论；不能为扩大覆盖恢复旧 MemoryCompiler、第二 Planner 或新的模型角色。

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
- 复盘旧版 batch trace 时，不要把 legacy 顶层结果当成全部证据；应同时看兼容子动作事件和 `batch_before/batch_after`。当前 native 模型入口不再暴露该宏。
- 不要把“检索到 Skill”“启动 replay”“执行了空脚本”“计划项已完成”或单纯像素变化当成 ARP evidence。只有非空执行叶子逐步通过 Replayer 核验，并满足最终目标 evidence，才能进入可信完成链。
