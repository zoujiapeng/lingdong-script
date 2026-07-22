# GUI Agent 架构与运行原理

本文描述当前代码的长期架构边界。它区分“已经实现的机制”“仍存在的兼容路径”和“目标约束”，不把路线图写成已完成事实。

## 目标

灵动脚本的核心目标是：同一个 GUI Agent 通过真实客户端，在未知 Windows 图形界面上持续执行“看画面 → 理解当前状态 → 做一个动作 → 看新结果 → 调整”的闭环。

仓库中的 11 个样例及其混合长任务只是应用发现、开放世界导航、工件传递、连续指针、视觉理解和持续会话等压力方向。生产 runtime 不应知道“第几个测试”，也不应靠原句关键词选择固定剧本。

## 不变量

### 1. 一个产品入口

用户从 Electron 的“智能代理”页提交目标。正式调用链是：

```text
PlatformWorkbench
  -> useControlPlane.startGoal
  -> POST /api/v2/task-contracts/compile
  -> POST /api/v2/arp/runs
  -> ArpExecution
  -> task_stage handler
  -> StageRuntime
  -> one ExecuteDesktopTask visual stage
  -> AgentRunner
  -> FrameAuthority / FastGuiController / RunStateMachine / FailureRecoveryLayer
```

`/api/chat`、MCP、CLI、scheduler 和 integration runner 可以复用内部能力，但不能形成行为不同的第二套 GUI 产品入口。直接 POST API 也不能代替 Electron 客户端 E2E。

### 2. 一个完整目标和一条执行主线

Task Contract 必须保留用户原始目标，普通未知 GUI 目标进入一个 `ExecuteDesktopTask` 视觉阶段。模型可在运行中调整、替换或放弃工作计划；最新画面与动作结果优先于旧计划的一致性。任务不能在看见界面前被 compiler 拆成一串应用专属微步骤，也不能因任务名称切换到另一套 Agent、工具或提示词。

原生客户端使用一个小而固定的通用操作面：观察/窗口、点击/输入/滚动、拖拽/连续指针、等待和终态。复合目标由模型在“新帧 → 一个决定 → 一笔动作 → 新证据”的循环中完成，不靠公开宏工具承载写死流程。运行时可以拒绝过期坐标、失去所有权的窗口或非法输入事务，但不能因为模型改变了语义判断而把它拉回旧策略。

### 3. 一个可见状态权威

对屏幕上是否存在、位于何处、是否变化等问题，当前权威帧是最终依据。权威帧至少绑定：

- 目标 hwnd 或完整虚拟桌面；
- capture region；
- frame id 和时间；
- screen/window epoch；
- 坐标空间与 DPI 信息。

原生模型的空间工具使用它实际收到的完整图片像素（`image_x/image_y`、`image_points`、`image_strokes`）。runtime 校验坐标仍位于这张图片内，再根据同一帧的尺寸和 capture region 映射到内部归一化坐标与物理桌面。模型不需要猜 Windows 坐标，也不能把另一张截图、缩略图或旧帧的像素继续用于当前动作。

UIA、Win32、OCR、传统 CV 和 VLM fallback 都是有 provenance 的观测信号。它们只有在与当前窗口、当前帧和像素区域一致时才能帮助定位；不能仅因 UIA 树丰富或 OCR 有文字就否决模型从画面得到的语义。

### 4. 一个动作事务

GUI mutation 应归一化为同一种事务：

```text
pre-frame
  -> target/path/effect grounding
  -> focus and freshness preflight
  -> desktop input lease
  -> physical or proven background-safe input
  -> post-frame
  -> local effect evidence
```

点击、滚动、输入、快捷键、拖拽、笔画和按住不是不同应用的能力，而是不同形态的通用输入事务。

### 5. 一个完成语义

动作成功、局部像素变化、阶段成功和用户任务完成是四件事：

- **action success**：输入被执行；
- **local effect**：预期局部区域发生了与动作相关的变化；
- **stage success**：阶段工件或义务满足；
- **task completion**：用户的最终目标由当前证据满足。

只有最后一层可以让外层 run 进入可信完成。模型总结、工具 `ok` 或 runtime `done` 都只是候选状态。对播放、弹窗、动画等瞬态效果，done verifier 可以读取有界的动作后视觉 checkpoint；这些历史帧只能证明先前发生过什么，不能替代用户要求持续保持的当前状态。

## 控制平面

### Task Contract

Task Contract 把用户目标表达为：

- 原始目标与 typed intent；
- 阶段和依赖；
- 所需 capability；
- 输入/输出 artifact；
- evidence provider；
- completion obligation；
- `experience_policy`。

编译器适合表达“先得到工件，再发送工件”这类显式数据依赖，不适合替模型预写未知 GUI 的每一次点击。客户端 GUI 请求无论 parser 识别出什么任务词，都只生成一个 `ExecuteDesktopTask`；typed intent 继续用于审计和非 GUI 路由，但该视觉阶段以原始目标作为不透明语义输入。无法从用户语句确定的信息应由 Agent 到达对应界面后观察决定。

### ARP

`arp/1` 是可执行图契约，负责阶段顺序、失败边、等待、人类门和运行审计。它不应成为第二个 GUI planner。Task Contract 进入 ARP 后，未知视觉步骤仍由同一个 bounded Agent/Controller 闭环处理。

### StageRuntime

StageRuntime 是阶段生命周期 owner：能力预检、工件传递、可选能力、执行与验证。对普通 GUI 目标，它保真传递完整目标并启动同一个视觉闭环，不重新解析鼠标动作语言，也不替模型编写恢复步骤。

### 四个运行时 owner

| Owner | 唯一职责 | 明确禁止 |
| --- | --- | --- |
| `FrameAuthority` | 原子发布当前 `FrameState`：frame id、epoch、hwnd、region、scope、图像身份和时间 | 自己截图、理解画面、选择目标或动作 |
| `FastGuiController` | 将一笔外部动作规范化为原子事务，执行输入并产出局部效果证据 | 规划完整任务、持有第二份帧真相、宣布任务完成 |
| `RunStateMachine` | 记录运行 phase、因果债务、未验证动作和终态转换 | 解释用户目标、生成计划或发起恢复动作 |
| `FailureRecoveryLayer` | 对失败作事实分类并附加可追溯元数据 | 自动重试、自动切换 UIA/OCR/MCP 或替模型决定下一步 |

四者服务于同一个 `AgentRunner` 循环，不形成四个 planner。失败后先使旧 frame/anchor/计划依据失效，捕获新权威帧，再让模型结合完整目标与失败事实决定继续观察、换路径、等待、阻塞或失败。对于外部参与者、动画或异步界面，模型还可以显式等待有界的视觉变化；runtime 只返回变化后的新帧，不解释变化的业务含义，也不替模型选下一步。

## 视觉与感知

### 原生视觉主路径

当主模型明确支持图像时，普通 GUI 回合直接附加当前权威 JPEG。模型从画面理解语义并提出一笔 typed transaction；动作后重新捕获权威帧，再进入下一回合。

模型推理预算也属于运行时基础设施，而不是任务规则：首帧、画面未变、动作被阻止、完成验收失败或恢复中的回合允许 provider 使用较深的原生视觉推理；在动作已被新帧和因果证据确认后，例行回合才可使用轻量模式。每次 provider 请求同时受 SDK 单次超时和 AgentRunner 总截止时间约束；SDK 隐式重试关闭，WinPilot product package 的 `ModelCallRuntime` 只能在结果被消费前重发同一冻结 decision/role/purpose/fingerprint。改变 thinking、messages、tools 或预算是新 redecision，不是 request retry。超时响应在进入工具分发前丢弃；模型请求 retry 不授予 GUI action replay 权限，mutation outcome unknown 时必须重新观察、对账或阻塞。

如果完整帧中某个区域确实需要更多细节，模型可以调用 `observe(image_region=...)`。这是当前权威图片像素到物理区域的一次通用映射，裁剪图会成为下一张权威帧，后续动作使用裁剪图自己的坐标；它不是画板、棋盘、浏览器或某个应用的特例，也不调用 OCR/UIA 来决定裁剪区域。

当前默认配置关闭 UIA、OCR 与 predictive OCR，`native_vision_direct=true`。这使默认路径先看图；结构化 provider 仍是可显式选择的兼容/提速能力，不是被删除的能力。

默认原则是：

- 不在每次模型看图前强制运行全屏 UIA/OCR；
- 不把模型未请求的 UIA/OCR/CV/MCP 结果塞进视觉回合；它们只是明确选择的辅助探针；
- 不把缩放后的 JPEG 像素当物理桌面坐标；
- 不在一个模型回合连续执行多笔未经新帧验证的空间动作；
- 看不清目标、规则或结果时继续观察或 `blocked`，不猜。

### 显式结构化辅助路径

结构化信号适合：

- 已确认的标准输入框、按钮和菜单；
- 文本查询与短标签定位；
- 后台安全且可验证的 UIA/Win32 操作；
- 明确选择的非视觉兼容运行；
- scoped OCR 和结果交叉核对。

结构化信号只有在模型或合同明确请求、来源与当前帧可核对、且预期收益清楚时才进入本回合。失败本身不是自动启用 UIA/OCR 的理由。结构化信号不适合单独理解游戏、自绘画布、动画、图片按钮、视觉布局规则或复杂跨区域关系。旧 `fusion.observe` 仍保留 UIA/OCR 优先和 rich-skip 分支，这是兼容债务，不是目标架构。

### 窗口与应用发现

这里采用与 Codex 类似的职责划分：基础工具负责尽可能完整地发现事实并返回证据，模型负责结合完整目标、当前画面和工具结果作语义决定。不要用 prompt 教模型某个应用藏在哪里；应修好所有任务共用的发现工具。

以抽象合同 `app.open` 为例（不是要求新增同名宏工具），基础层应查询并归一化：

- 运行进程、可见/隐藏/托盘窗口及其 pid/hwnd；
- Start Menu 和 StartApps 注册项；
- App Paths、安装元数据与可执行路径；
- `.lnk` 快捷方式的目标、参数和工作目录；
- 目标选择与启动前后的进程/窗口快照。

工具返回候选、provenance、置信依据和可执行启动方式；模型根据画面选择复用、恢复或启动。启动后必须用新进程/窗口与启动动作的时间、pid、路径和窗口变化验证因果身份，不能因为命令退出码为零就声称打开了正确应用。

应用解析应按以下顺序收集事实后决定：

1. 已运行且可见/可恢复的窗口；
2. 托盘或隐藏窗口；
3. 已安装应用和开始菜单入口；
4. 用户指定路径或可执行文件；
5. 最后才启动新实例。

“打开应用”不意味着无条件启动新实例。窗口标题、进程、可见状态、登录态线索和当前画面需要一起判断。应用别名只能帮助发现候选，不能决定操作流程。

当前实现已经在 `perception/apps.py` 与通用启动工具中接入 Start Menu、`Get-StartApps`、App Paths、快捷方式目标、可执行/进程身份、已运行实例复用和启动前后窗口证据；本地抖音冷启动 E2E 已证明这条多源路径能找到桌面客户端，而不是退回网页。窗口、托盘、登录态、alias、focus 与 launch 仍分散在多个 owner，因此这是一项已经落地但尚未完全收敛的基础能力。

## 动作执行

### 通用动作集合

Controller 当前覆盖或编排：

- pointer move、click、double click、hover；
- scroll；
- type text、hotkey、timed key；
- drag；
- continuous strokes/path；
- wait for stable/change；
- mouse down / move / mouse up 的跨帧 held-pointer session。

`wait(until_change=true, persistent=true)` 是外部参与者、动画或异步界面的通用等待原语：它只负责在绑定窗口或模型指定 ROI 上保持 watcher，并返回“是否观察到已稳定的变化”；变化代表什么、下一步做什么仍由模型结合新帧决定。应用聊天、棋局或某个测试的等待逻辑不得下沉到该工具。

连续路径不是绘画专属通道。移动滑块、摆放对象、调整时间轴、手写和画图共用相同的指针、区域、路径、新鲜度与验证基础设施；任何 stage 都不能决定这些通用能力是否可用。

### 跨帧按住

有些任务在按下后才显示目标。运行时允许：

1. 在当前权威帧定位 source；
2. `mouse_down` 并建立 held session；
3. 捕获新权威帧；
4. 模型理解新出现的 checkpoint/target；
5. 只允许继续移动或匹配的 release；
6. 超时、失焦、取消或异常时强制 `mouse_up` 清理。

旧帧中未执行的空间坐标不能跨帧继续使用，必须基于新画面重规划。

### 桌面资源

物理鼠标和键盘通过 Desktop Action Queue 全局单写。只有被证明不会移动真实鼠标、不会抢键盘焦点的窗口消息/UIA 动作才可按 hwnd 隔离。`multi_input_cooperation` 只是实验性的人类优先仲裁，不代表 Windows 存在第二套物理输入设备。

## 计划、上下文与记忆

完整用户目标是不可丢失的根约束，计划只是模型内部的可变工作状态。初始计划不能限制后续视觉探索，也不能演化为本地编译器必须逐项执行的脚本。

上下文有三层：

- 当前 run 的目标、计划、窗口、画面和工具证据；
- session context，用于“刚刚画的画”“继续”等跨 turn 引用；
- long-term Memory/Skill，用于经过验证的经验复用。

冷启动只禁用第三层经验能力，并清空测试要求的 session；它不应破坏同一客户端会话中明确需要的多 turn 依赖。测试 harness 必须显式区分“独立 case”与“同一 scenario 的后续 turn”。

Memory Pack 只能作为提示。当前权威帧、确定性文件/进程证据和用户最新指令优先于记忆。

## MCP 边界

MCP 是显式可选的 capability source。只有能力、授权、输入输出、当前上下文和可验证结果明确匹配时才使用；它不是 GUI 失败后的自动 fallback。没有 MCP 或 MCP 不适合时，原生视觉主线仍可独立运行。

选择 MCP 的条件应是能力、授权、输入输出和验证契约匹配，而不是应用名硬编码。MCP 成功也需要进入同一 artifact/evidence/completion 体系。

## Trace、Artifact 与 Replay

- Trace 保存模型请求/响应、工具事务、帧、证据和状态变化。
- ArtifactStore 区分用户工件与模型观察帧，并用 run-scoped id/hash 传递。
- `automation-ir/1` 是新保存流程和 Skill 的规范中间层。
- `.wps.json`、FlowScript、DSL 和旧浏览器 executor 是兼容路径，不是 GUI Agent 的事实源。
- Replay 只有在适用条件和 postflight evidence 重新通过时才可算 Skill 成功。

## 当前实现债务

全面审查确认以下问题仍在代码中，文档不能掩盖：

1. `agent/loop.py` 体积过大；`FrameAuthority`、`FastGuiController`、`RunStateMachine` 与 `FailureRecoveryLayer` 虽已建立边界，旧逻辑尚未全部迁出和去重。
2. `TaskContractCompiler`、`StageRuntime`、`InteractionStageEngine`、`SubgoalExecutor` 和 Controller 存在重复解释。
3. 某些 communication/application adapter 仍含布局、别名或任务族特化。
4. 原生配置已默认关闭 UIA/OCR，但 `fusion.observe` 等兼容入口仍保留旧 provider 顺序；必须继续防止非默认调用面把结构化信号升级成视觉语义或失败后的自动 fallback cascade。
5. `/api/chat`、legacy IPC、FlowScript 和旧 feature 系统仍增加维护面。
6. 当前整改版本尚未用真实 Electron 客户端完成 11 个任务和混合长任务的全量验收，不能继承历史 run 的通过结论或据此宣称通用成功率。

整改优先级见 [GUI Agent 架构审计](GUI_AGENT_ARCHITECTURE_AUDIT.md)。

`codex/vnext-agent-core` 分支正在旁路迁移下一代核心：可重放 Mission、逐回合 Capability Broker、单一 Visual Actor、独立只读 Verifier，以及按证据保持禁用的 Planner/Inspector。当前已有默认关闭、按 run 显式选择的增量 live coordinator，工具面为 `click/double_click/scroll/claim/blocked`，复用唯一 Controller/RawDevice。三个 `precise-click` seed 和最终 direction/notches 合同下的一对双向 `scroll-reveal` seed 的冷启动、空 session、可见 Electron canary 已通过独立 trusted-browser oracle。legacy 仍默认，完整 P7、其余 GUI 原语、默认切换和 legacy 净删尚未完成。详见 [vNext 文档入口](vnext/README.md)。本文前述仍是 legacy 默认运行时事实，不能把少量受控原语成功写成通用 GUI 成功。

## 验收原则

GUI 架构改动至少需要：

1. 小范围单元测试证明数据和失败语义；
2. Python/TypeScript 常规回归；
3. 可控视觉目标验证真实物理输入和外部 ground truth；
4. 从可见 Electron 客户端提交的冷启动 E2E；
5. 至少一个与实现时样例不同的任务族；
6. trace 中准确绑定 outer run、child run、frame lineage 和 terminal evidence。

完整规则见 [端到端测试规范](testing.md)。

## 文档事实源

- 本文：架构、不变量和当前债务；
- [winpilot-agent.md](winpilot-agent.md)：工具、Controller、done gate 等实现细节；
- [testing.md](testing.md)：测试分类和通过定义；
- [frontend.md](frontend.md)：客户端入口和刷新/历史呈现；
- [apis.md](apis.md)：HTTP、IPC、MCP 契约；
- [replay-recording.md](replay-recording.md)：trace、IR、replay 与兼容层。

具体 run ID、截图路径和一次性性能数字属于测试报告或 trace，不应继续堆入架构文档。
