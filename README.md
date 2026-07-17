# 灵动脚本 / Lingdong Script

Windows 低延迟 GUI Agent + Agent Runtime Platform（ARP）。当前产品由一个统一控制平面组成：

- **Main LLM Agent**：理解目标、下达子目标、处理异常和调整策略。
- **Fast GUI Controller**：变化触发感知、视觉锚点、批量动作、视觉伺服和动作后验证。
- **ARP Runtime + Studio**：用版本化 `arp/1` 图编排 Agent、Controller、Skill、证据门和人工门。
- **Task Contract + Stage Runtime**：先把自然语言编译成类型化阶段、工件、能力和完成义务，再选择 Controller、Adapter、工具与证据路径。
- **World Model + Run Projection**：统一屏幕状态、元素、页面阶段、timeline、evidence 和延迟指标。

`automation-ir/1` 是新保存流程、Skill、WPS 导出和 Flow 编辑的规范中间层；`arp/1` 是可执行图契约。Legacy WPS、FlowScript 和 DSL 仍作为导入/导出或兼容格式存在，不支持的旧节点会显式标记为 `legacy_flow` 并在运行前失败，不会伪装成等价迁移。

## 当前事实

- GitHub 默认且唯一长期主分支是 `master`；临时开发分支验证后必须合并或删除，不保留第二条产品主线。
- 用户始终面对一个 WinPilot Agent；Agent、ARP 图和 Controller 的事件统一投影成同一种 Run。
- “智能代理”页先调用 `/api/v2/task-contracts/compile`，再从同一个 `/api/v2/arp/runs` 入口运行 `task_stage` 图。跨应用工件任务不再退化为整句 `agent_goal`；阶段 Skill 未命中或失败只恢复当前阶段。
- 需要根据当前画面理解规则、轮次或可操作区域的任务会编译为通用实时交互合同：`ObserveScene → InferInteraction → PerformTurn → VerifyLocalEffect`；持续会话可在尚未终止时进入 `WaitForExternalChange`，但等待只是下一轮的控制/遥测，不是最终完成义务。最终仍必须由最新权威帧上的 `interaction.task_completed` 证据闭环。这条路径不按应用名、对象名或测试坐标分流。
- 前端提供 run 级“冷启动（禁用 Skill/Memory）”开关。开启后合同编译、RuntimeProfile、StageRuntime 与 trace 共享 `experience_policy=disabled`，用于真实验证首次探索，不靠删除本地文件或仅隐藏一个 replay 节点。
- 编程/仓库能力由 `agent/runtime_profile.py` 启用 Coding Runtime；`agent/coding.py` 只是旧 import 的兼容 shim，不再有独立 CodingRunner 主循环。
- Electron 主进程 HTTP API 默认监听 `127.0.0.1:7880`。WinPilot FastAPI 由 `python/winpilot_home/serve.py` 启动，端口由 Electron 动态选择；两者除公开 ping 外都使用进程随机 bearer token。不要直接运行 `uvicorn winpilot.server:app`。
- 默认模型配置在 `python/winpilot_home/winpilot/config.py` 和本机 `python/winpilot_home/config.json`。provider、cache、usage、cost 归一化在 `winpilot/model_adapter.py`。
- 当前 MiMo 路由以真实能力而不是名称猜测为准：`mimo-v2.5` 接收截图并作为原生视觉主模型；`mimo-v2.5-pro` 当前只走 text-only 路径，不向它发送 `image_url`。MiMo/Reasonix 的 provider thinking 默认关闭，避免工具调用被 reasoning 截断或显著拖慢；实验性连续空间推理需显式开启。
- 主模型明确支持图片时默认走真实画面闭环：初始权威帧 → 每个模型回合至多一笔视觉事务 → 动作后权威帧 → 下一步决策；最终 GUI 完成还要用新鲜帧做目标条件视觉核验。
- 模型收到的帧不再固定为全屏。`controller.target_window_capture_enabled=true` 时，运行时只在目标 hwnd 有效、可见、位于前台、几何可信且任务不需要跨窗口/桌面上下文时裁剪目标窗口；任一条件不满足就保守回退完整虚拟桌面。“系统设置”页可关闭此优化以做 A/B 对照。
- 原生视觉模型的普通无查询 `observe` 默认直接接收当前 capture scope 的 JPEG，不先支付 UIA/OCR/VLM 的同帧拆解。UIA、Win32、OCR、CV 与独立 VLM fallback 只用于文本查询、结构化定位、changed-only、非视觉模型和失败恢复；原生视觉主模型不会再看到重复的二次 VLM 工具。
- `observe` 是只读操作；即使识别出聊天输入区也只返回 Controller 提案，不会暗中发送。原生视觉的点击、滚动、拖拽和笔画统一使用当前整帧的 `frame_*` 归一化坐标（0..1000），运行时映射到同一次截图的物理窗口区域；模型不能把缩放后的 JPEG 像素冒充物理 `x/y`。
- 没有匹配当前 hwnd/screen epoch/窗口区域的新鲜画面时动作会被拦截。动作落下前会再次截图：能从当前帧可靠定位动作目标时，只检查目标外扩 `controller.visual_preflight_target_margin`（默认 60/1000）后的 ROI，变化比例超过 `controller.visual_preflight_target_change_ratio`（默认 `0.01`）才取消；无法可靠定位目标时退回整窗检查，阈值为 `controller.visual_preflight_change_ratio`（默认 `0.0025`）。取消后会返回新帧。目标含糊或结果看不清时应 `blocked`/安全暂停，不会猜坐标继续执行。
- 低延迟 GUI 已落地 batch/subgoal、默认 3 FPS 的变化触发 watcher、变化 ROI 增量 OCR/Win32 预取、视觉元素缓存、anchor 复验、点击前局部视觉伺服、瞬态 press 采样、Skill replay、证据 done gate 和实时指标。确定性 Adapter 使用 `structured_ui` 先取 UIA/Win32 结构，只在收件人、确认框或短控件等义务区域做 scoped OCR；前台义务 OCR 会排在尚未开始的后台预测 OCR 前，释放 Perception lease 会取消排队分析并丢弃已经过期的后台结果。
- 通用实时交互的 typed action 覆盖点击/双击、悬停、指针移动、拖拽、连续笔画、滚动、颜色选择、文本输入、快捷键、限时按键和稳定等待；`mouse_down → move/wait → mouse_up` 只允许作为同一批次内经过平衡校验的连续事务，不作为三个不受约束的顶层模型工具暴露。长拖拽和长路径的新鲜度检查沿路径最多均匀抽样 12 个局部 ROI，避免用一个大包围框把端点或局部变化稀释掉。
- 系统提示词已收敛为稳定的紧凑闭环契约：真实画面优先，结构化感知只作提示；domain playbook 默认关闭。绘图、签名、拖拽、手写等只属于同一套“连续指针几何”通用路径，画猫/画狗不是生产专属能力。
- 模型可随时用 `revise_plan` 替换或清空任务列表，也可原子执行 add/update/remove/move/reopen；任务列表是可变工作状态，不是完成证据。
- `controller.multi_input_cooperation` 是默认关闭的实验开关。开启后优先后台安全的窗口消息/UIA 动作，不抢占其他前台窗口的键盘焦点；长物理动作中若用户移动鼠标或切换前台，用户取得控制权，Agent 安全停止该动作。Windows 仍只有一个系统鼠标和一个键盘焦点，这不是虚拟出第二套硬件。
- 前端主工作台已中文化；API、MCP、VLM、URL、坐标和模型名等技术标识保留原文。Electron 与 Python 使用 runtime contract v2、进程启动时源码指纹和 Git revision 校验；源码漂移或旧进程不兼容时禁止启动新前端任务并要求重启。
- “运行记录”先读取轻量 run 摘要，选择后再加载单次 typed contract/stage/capability/artifact/evidence/completion 详情；ARP audit snapshot、stage trace 和 ArtifactStore manifest 会持久化。较老 trace 缺少合同原文时只恢复事件可以证明的阶段与证据，不用当前 parser 重猜历史意图。图片工件通过 run-scoped、哈希复验的 artifact API 展示。
- 前端以 WebSocket 事件驱动刷新当前 run（80 ms 合并），terminal 才刷新完整平台/列表，5 秒轮询只作恢复兜底；`ping` 不再触发 REST。系统设置页同时显示 RuntimeJob、Desktop Action Queue、异步动作与 Perception daemon 健康摘要。
- 像素变化、颜色存在和 Controller 的 `auto_visual_verify` 只说明发生了视觉效果，不是任务完成证据；当前尚未内置带正式权重的常驻学习型 UI detector。
- 长期记忆以 `python/winpilot_home/memory.db` 的 SQLite Memory Graph 为主；`python/winpilot_home/experience_memory.json` 只保留迁移兼容。Memory Pack 作为有界 user message 注入，不能覆盖当前截图或确定性证据。
- Studio 只展示同时被前端编译器和后端 capability catalog 声明可执行的节点。历史 FlowScript 节点定义仍可用于读取旧文件，但不会再从节点库或动态 `@feature` 搜索中创建；兼容浏览器执行器强制禁止模拟成功。
- 脚本文件默认是 draft artifact，不等于 Skill。Skill 复用要求 active Memory Graph 节点、结构化适用条件和匹配的脚本 SHA-256；Apprentice 候选必须经过占用 `interactive_runtime` 资源锁的真实 replay，并让结构化 postflight evidence 全部通过后才能晋升。
- `python/winpilot_home/config.json`、`python/winpilot_home/traces/`、`python/winpilot_home/evals/`、`python/winpilot_home/scripts/`、`python/winpilot_home/experience_memory.json` 是本机运行数据，不提交；根目录 `scripts/` 是受版本控制的开发与评测工具。

## 成熟度快照

这份快照按当前代码核对，不按目标路线图美化：

| 方向 | 当前状态 |
| --- | --- |
| 原生视觉闭环 | `AgentRunner` 在首轮和每笔视觉事务后附加权威真实帧；帧会明确标记为已验证目标窗口裁剪或完整虚拟桌面。同一模型回合的后续工具调用被延后。低层动作使用 0..1000 帧坐标，并必须匹配当前 hwnd、screen epoch、截图区域及动作前像素新鲜度；`done` 再由新鲜真实帧和任务目标独立核验。 |
| Raw Device Executor | `action/raw_device.py` 让 Controller batch 的常见 click/type/key/drag/focus/launch 子动作直接调用已授权 handler，不再递归支付完整 `ToolSession.dispatch()`；少数复杂动作仍走外层工具生命周期。 |
| Action Queue | `action/desktop_queue.py` 是公平资源队列：真实鼠标/键盘仍按 `physical` 全局单写；明确使用 PostMessage、不会移动鼠标的后台窗口动作可按 `window:<hwnd>` 在不同窗口并发，同一 hwnd 串行；剪贴板另有独占资源。 |
| Perception daemon | `fast/perception_daemon.py` 按 hwnd 复用 watcher 与 ElementCache，并在有意义的 ROI 变化后合并调度轻量 OCR/Win32；changed-only observe 可消费热缓存，UIA/VLM 不进入 daemon 内环。lease 释放会设置 generation-scoped cancellation，旧的排队/完成中结果不能进入重获 lease 后的 history；ONNX 已开始的一次推理本身不能安全抢占。它还不是常驻学习型 YOLO/UI detector。 |
| Runtime jobs | Agent、ARP、Replay、Memory verification 和计划任务统一进入 `runtime_jobs.py`；API 可查询/取消，交互任务按 `interactive_runtime` 排队，真实鼠标键盘仍由 Desktop Action Queue 单写保护。 |
| ARP / Automation IR | `automation-ir/1` 是新 artifact 的规范中间层。Flow editor 在 IR 的 `runtime.arp_graph` 中保存一次编译后的 `arp/1` 投影及 fingerprint，后端校验后直接运行；WPS/FlowScript/DSL 是兼容 adapter，不是新的并行事实源。 |
| Memory / Skill | Agent run 只经 `memory/compiler.py` 写入一次；同名候选保留谱系而不删除旧版本。实际复用同时检查任务意图、app/window、stage/parameters/anchors（存在时）和脚本摘要。前端分开展示已验证技能、草稿/无效脚本、长期记忆和进程内 session memory。 |
| Task Contract / Artifact | 前端自然语言入口先生成 `task-contract/1`：结构化 intent、typed stages、每阶段 capability selection、artifact binding、evidence provider 和 completion obligation。`ArtifactStore` 将用户工件与模型观察帧分离，并以 run-scoped `artifact_id`、哈希、来源、时间贯穿投递和验收。 |
| 实时视觉交互 | `SurfaceBinding` 将交互面绑定到一个新鲜目标 hwnd、capture scope、窗口 client authority 和 frame identity；`InteractionRegion` 是该帧 0..1000 坐标空间中的显式安全区域。语义模型只提出一个 typed turn，Controller 在本地连续执行并返回动作后权威帧；区域外动作、陈旧帧、目标失焦或含糊表面均 fail-closed。 |
| 输入机制合同 | 连续路径执行前先满足 `interaction.prepare_input_mode`。Controller 从当前可访问界面证明自由绘制模式，发现选择、形状、文字等冲突模式时按语义控件切换并复核 Selected/Checked 或新鲜界面状态；只点到控件、只有工具返回 `ok` 或缺少后置状态都不能放行。注册表描述输入机制和通用控件语义，不绑定应用名、测试图形或屏幕坐标。 |
| 提示词与计划 | 默认发送紧凑、通用、视觉优先的系统契约，不注入应用专属 recipe；模型可实时重排、增删或重新打开 todo。 |
| 多输入协同 | 实验开关默认关闭；开启时实行人类优先的焦点/光标仲裁并 fail-closed，不能把它描述成两套真实鼠标键盘并发。 |
| 前端与运行时 | “智能代理、流程工作台、运行记录、技能与学习、系统设置”共用 `/api/v2` 和 WebSocket；contract v2 与源码指纹会拒绝陈旧 Python 服务，不再依赖 iframe Agent 页面。运行列表与 job 列表是轻量摘要，详情按 run id 加载；重启后从 trace/manifest 恢复历史阶段、能力、工件、证据和完成合同。节点主列表、搜索与最近入口共享 compiler/catalog 可达性交集。 |
| 真实能力 | 计算器、记事本、网页靶场、连续指针/拖拽和真实 Paint→QQ 工件链已有回归。2026-07-17 的最终自主视觉矩阵覆盖完整拖入、精确点击、顺序点击、滑块和四个通道方向，`8 passed, 1 deselected in 155.79s`，全部由浏览器 trusted pointer ground truth 判定；需要真人继续操作的共享回合只验收 Agent 自己已完成且进入 `waiting_external`，不冒充终态成功。关闭 Skill/Memory 的 QQ run `arp-20260716-215314-7f98cd` 是前一日从同一 Task Contract/ARP 路径创建并发送当前 run 工件的历史真实证据，本轮没有再次对外发送。测试对象和收件人只是样本参数，生产代码没有对象模板、收件人别名或物理坐标分支。这仍只是单机固定样本，不是统计成功率，更不是“达到人类通用视觉能力”的证明。微信、Steam、抖音、网易云等复杂软件尚未完成同等级真实 E2E。 |

2026-07-17 的客户端同入口冷启动首回合 `arp-20260717-035253-f61869` 特意从冲突的 Selection 模式开始：系统用 1 次模型调用识别当前共享状态，Controller 自动切换到 Brushes、选择红色并在当前像素解析出的空区域画出 X，typed local verifier 证明输入模式、区域、两条路径和红色像素；用户预先画出的黑色圆圈不计为 Agent 输出。Observe / Infer / Perform / Verify 分别为 0.84 / 13.55 / 7.07 / 0.65 秒，约 22.1 秒的首回合仍不适合实时陪玩，主要瓶颈是首次语义规划。该持续会话随后因测试观察导致目标失焦而失败，因此这里只把它记录为“真实客户端首回合证据”，不冒充终态 E2E 通过。

## 快速启动

CI 使用 Node.js 20 和 Python 3.11。首次启动先准备依赖：

```bash
npm ci
python -m pip install -r requirements.txt
npm run dev
```

日常开发也可使用：

```bash
npm install
npm run dev
```

启动后第一屏就是原生“智能代理”页；“流程工作台”可直接验证并运行默认安全图。API key 可在“系统设置”页或 `python/winpilot_home/config.json` 中配置。

当前已登录的 Codex 桌面客户端可通过带 bearer token 的本地 MCP 作为高层规划器，不需要另配模型 API key：WinPilot 在 `/mcp` 暴露观察、controller 子目标、批量动作、Skill 与 ARP 工具。视觉任务先用 `begin_gui_task` 绑定完整目标，再由 `capture_visual_fallback` 返回带 frame identity 的权威画面；`execute_visual_transaction` 在同一次 Controller 往返中执行 typed transaction 并返回动作后权威帧。消费级 ChatGPT 还需要受控的 HTTPS/OAuth 适配层；Electron 托管随机 token 目前也没有面向任意外部客户端的公开配对流程。准确连接方式和安全边界见 [docs/chatgpt-desktop.md](docs/chatgpt-desktop.md)。

## 常用验证

```bash
python -m pytest python/winpilot_home/tests -q
python -m pytest tests -q
npx vitest run
npm run build
npm run build:all
```

2026-07-17 当前工作树的最终全量验证为 WinPilot `1827 passed, 13 skipped, 1 warning`、根 Python `2508 passed`、Vitest `209/209 passed`，TypeScript typecheck、Vite/Electron、CLI 与 AI build 全部通过。13 个常规 skip 是必须显式授权真实桌面或真实通信的 opt-in Windows E2E；本轮另行显式运行自主视觉矩阵 `8/8`，共享回合因需要真人继续而被排除在终态矩阵之外。Paint→QQ 的 `1/1` 是 2026-07-16 的历史真实发送证据，本轮没有再次发送。这里不使用 mock 代替 E2E，也不把单机运行记录描述成统计泛化评测；GitHub 是否可发布还要看推送后当前提交的 Actions 结果。

清理生成产物和缓存：

```bash
npm run clean
```

`npm run clean:runtime` 会额外删除忽略的 `python-runtime/`。只有确认可重新准备运行时环境时再使用。

直接 Agent GUI 评测走 `/api/chat`；当前原生前端目标先调用 `/api/v2/task-contracts/compile`，再走 `/api/v2/arp/runs`。普通阶段由本地 `StageRuntime` 执行，只有语义内容和异常阶段进入有界 `AgentRunner`：

```bash
python scripts/gui_eval_run.py "用计算器计算6和7的和" --base-url http://127.0.0.1:8765
```

报告写到忽略目录 `python/winpilot_home/evals/`，trace 和截图在 `python/winpilot_home/traces/`。汇总中的 `verified_success` 只统计同时满足未超时、`status=done`、有外部 ground truth 且通过、policy 通过、性能预算通过并且没有 unverified done 标记的 run；没有检查项的报告记为 `ungrounded`，不再把 agent 自报 done 算成功。

单次手工成功不能换算成“成功率”。2026-07-17 的通用实时交互 opt-in E2E 从 `/api/v2/task-contracts/compile` 与 `/api/v2/arp/runs` 进入，而不是直接调用内部 Controller。最终自主矩阵 `drag_fit`、`precise_click`、`ordered_clicks`、`slider`、`corridor_drag_v0..v3` 为 `8 passed, 1 deselected in 155.79s`；对应 run 为 `arp-20260717-172312-b1ad02`、`arp-20260717-172329-9fdf84`、`arp-20260717-172351-1cb6a4`、`arp-20260717-172418-b96691`、`arp-20260717-172434-dbed8e`、`arp-20260717-172453-7d12af`、`arp-20260717-172511-2a9a15`、`arp-20260717-172529-78a6aa`。八项均为 `done`，外部 ground truth 全部通过，0 次 replan/recovery；合计 10 次模型调用（平均 1.25、最多 2），Controller 平均 2,377 ms。浏览器分别验证完整对象落入、唯一目标点击、严格的 `1→2→3` 顺序、滑块终点和四种路径的合法性/minimum clearance；frame lineage、当前像素 grounding、source footprint、路径与 effect evidence 同时参与。模型调用次数是 trace 中的性能观测项，不是固定正确性阈值；复杂歧义或恢复场景允许增加调用，运行时只拒绝无界循环和不可满足路径。`shared_turn` 必须等真人继续，因此本轮只保留“Agent 回合已验证后进入 waiting_external”的非终态检查点，不将用户缺席记作 Agent 动作失败，也不计入自主终态矩阵。

同日用户授权的最终真实 QQ E2E run `arp-20260716-215314-7f98cd` 对“用画板画一头猪然后截图打开QQ发送给我的手机”设置 `cold_start=true`，关闭 Skill/Memory，并从同一前端路径在 69.896 秒内完成 2 次模型调用；模型耗时 17,318 ms、Controller 51,309 ms。`DrawArtifact`、`CaptureArtifact`、`OpenConversation`、`SendArtifact`、`VerifyDelivery` 均为 `done`，0 个 Skill replay，每一阶段的 verifier requirements 都属于 exposed capabilities，trace 还保留实际选择的 evidence provider。它创建当前 run 工件 `art-7cb4d57e14af4211`（SHA-256 `b3e725f49ca3247506dbd7517fd405c9b892b2c7f329060a562316db1c38d01a`，866×413，10,370 bytes），打开“我的手机”，执行真实发送，并在发送后可见图片气泡中得到 209 个 good matches / 163 个 homography inliers；匹配区域相对发送前基线变化率为 0.0597，Completion Contract 全部满足。绘图语义验收认为主体可辨认，但 `detail_level=low`；这不是高审美质量声明。

另外三种通信改写分别由 run `arp-20260716-130503-a61df0`、`arp-20260716-130643-0dbba1`、`arp-20260716-130708-36a212` 完成，三项真实 E2E 合计 `3 passed in 180.34s`。它们编译到等价的 artifact/communication obligations；“发送刚画的工件”通过同一 typed 路径建立真实前置工件后，主 run 为 0 次模型调用，其余两个绘制并发送的 run 各为 2 次。这些 QQ 样本与九个受控视觉样本仍不足以报告统计成功率或跨版本稳定性。

上述真实 run 都记录阶段、当前能力、模型/Controller/capture 耗时、预算、artifact、evidence 和失败/恢复字段，但仍不证明未知软件、移动或遮挡目标、多窗口、高 DPI、长会话及复杂审美任务已经达到人类水平。`structured_ui` 依赖目标应用可提供的 UIA/Win32 结构，scoped OCR 仍可能受自绘控件、动画和遮挡影响；前台 OCR 优先级不能中断已经进入 ONNX 的推理；强 ORB/homography 命中可以提前返回，但稀疏图、重裁切或大形变仍会落入较慢且可能含糊的多尺度 edge-template fallback。后续性能和成功率结论必须来自更大的固定任务集、多次运行、同模型/显示/DPI 配置和外部 ground truth。

网页靶场用于复现输入、按钮、局部变化、弹窗、拖拽和画布任务：

```bash
cd python/winpilot_home/eval_targets
python -m http.server 8877 --bind 127.0.0.1
```

然后让前端 Agent 或 `/api/chat` 打开 `http://127.0.0.1:8877/gui_agent_target.html`，用截图和 trace 核对真实行为。

## 目录边界

| 路径 | 职责 |
| --- | --- |
| `src/main/` | Electron 主进程、HTTP API、Python 子进程、WinPilot 服务生命周期 |
| `src/renderer/` | 原生中文“智能代理、流程工作台、运行记录、技能与学习、系统设置”和 FlowCanvas |
| `python/winpilot_home/winpilot/` | 当前 WinPilot agent、感知、动作、录制、记忆、REST |
| `python/winpilot_home/winpilot/orchestrator/` | `arp/1` contract、runtime、adapter、capability catalog、Run Projection |
| `python/winpilot_home/winpilot/tasking/` | TaskIntent、TaskContract、CapabilityPlanner、StageRuntime、ArtifactStore、Communication Adapter、Evidence 与完成验收 |
| `python/agent/__main__.py` | Electron IPC 到 WinPilot 的适配器 |
| `python/engine/` | legacy FlowScript/DSL 执行引擎 |
| `python/features/` | legacy `@feature` 注册表，供流程图/MCP/HTTP/CLI 使用 |
| `python/core/` | legacy 公共接口层，仍被部分测试/工具引用 |
| `scripts/` | 开发、评测、清理脚本；运行输出写入忽略目录 |

## 文档

- [CLAUDE.md](CLAUDE.md)：给 AI/开发者的最短接手说明。
- [docs/README.md](docs/README.md)：文档索引。
- [docs/architecture.md](docs/architecture.md)：当前架构、状态机和边界。
- [docs/frontend.md](docs/frontend.md)：原生五入口、刷新/历史审计、节点可达性、兼容与死代码边界。
- [docs/winpilot-agent.md](docs/winpilot-agent.md)：WinPilot agent 运行机制和 trace 复盘方法。
- [docs/model-providers.md](docs/model-providers.md)：模型 provider、cache、usage/cost。
- [docs/apis.md](docs/apis.md)：Electron HTTP、WinPilot REST、IPC 与 MCP 契约。
- [docs/chatgpt-desktop.md](docs/chatgpt-desktop.md)：ChatGPT/Codex 桌面 MCP 桥接与安全边界。
- [docs/memory-graph.md](docs/memory-graph.md)：Memory Graph、召回、反馈与迁移。
- [docs/replay-recording.md](docs/replay-recording.md)：trace、Automation IR、兼容 WPS、replay、导入流程图。
- [docs/flow-editor.md](docs/flow-editor.md)：流程图/DSL/`@feature` 体系。
- [docs/项目导览-面试版.md](docs/项目导览-面试版.md)：基于当前代码的完整项目讲解。
- [CONTRIBUTING.md](CONTRIBUTING.md)：贡献、测试和文档同步要求。
- [SECURITY.md](SECURITY.md)：本地控制面、凭据与桌面动作安全策略。

## 维护原则

- 不为单个软件硬编码专属规则；优先增强通用感知、窗口限定、验证和恢复。
- 用户始终面对一个 Agent。新能力优先作为 Runtime/Capability/Skill 接入同一 runner；只有明确隔离、安全或生命周期边界时才引入独立 runner。
- GUI 成功不能只看 agent 总结，也不能只看像素发生变化；必须用与任务目标匹配的新鲜截图，或文件、进程、窗口、trace 等确定性工具证据验证。
- 验证可做有限并发：文件/进程只读检查会与 UIA/OCR 重叠；`shell_true` 不并发。真实物理输入保持全局 single-writer，只有明确证明为后台安全的窗口消息动作才允许跨 hwnd 并发。
- 不继续把大块新逻辑塞进 `agent/loop.py`、`agent/tools.py`、`python/engine/executor.py`；新增能力优先拆模块。
- 文档写当前事实，不堆历史 run 日志。性能数字必须注明日期、入口、模型和验证口径；历史 trace 留在本机忽略目录或 eval 报告里。
- WinPilot Agent 自带计划任务：“系统设置”页和 `schedule_task` / `list_scheduled_tasks` / `cancel_scheduled_task` 共用 `python/winpilot_home/agent_schedules.json`；到期后提交统一 RuntimeJob，再走同一个 `AgentRunner`。这不是 Windows Task Scheduler。
- `search_files` 优先使用随包提供的 Everything ES 客户端连接本机 Everything 索引，支持文件/目录、扩展名和排序；索引不可用时只做有时间上限的本地扫描。
- Electron 会在任务完成、失败或停止时设置 Windows 任务栏状态、闪烁后台窗口并发送系统通知；同一 `run_id` 只提醒一次。
- `winpilot/__init__.py` 会在任何动作/感知模块之前通过 `utils/dpi.py` 初始化 Per-Monitor V2；坐标偏移排查用 `coordinate_diagnostics` / `coordinate_click_test`，它们报告物理屏幕、截图、窗口、DPI 和真实鼠标点击路径，不等同于浏览器 CSS/client 坐标。
