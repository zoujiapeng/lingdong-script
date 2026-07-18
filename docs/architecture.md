# GUI Agent 架构与运行原理

本文描述当前代码的长期架构边界。它区分“已经实现的机制”“仍存在的兼容路径”和“目标约束”，不把路线图写成已完成事实。

## 目标

灵动脚本的核心目标是：同一个 GUI Agent 通过真实客户端，在未知 Windows 图形界面上持续执行“看画面 → 理解当前状态 → 做一个动作 → 看新结果 → 调整”的闭环。

测试中的 QQ、画板、音乐、短视频、笔记、抓包、聊天和游戏只是不同压力方向。生产 runtime 不应知道“第几个测试”，也不应靠这些句子的关键词选择固定剧本。

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
  -> AgentRunner / Controller / deterministic capability
```

`/api/chat`、MCP、CLI、scheduler 和 integration runner 可以复用内部能力，但不能形成行为不同的第二套 GUI 产品入口。直接 POST API 也不能代替 Electron 客户端 E2E。

### 2. 一个可见状态权威

对屏幕上是否存在、位于何处、是否变化等问题，当前权威帧是最终依据。权威帧至少绑定：

- 目标 hwnd 或完整虚拟桌面；
- capture region；
- frame id 和时间；
- screen/window epoch；
- 坐标空间与 DPI 信息。

UIA、Win32、OCR、传统 CV 和 VLM fallback 都是有 provenance 的观测信号。它们只有在与当前窗口、当前帧和像素区域一致时才能帮助定位；不能仅因 UIA 树丰富或 OCR 有文字就否决模型从画面得到的语义。

### 3. 一个动作事务

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

### 4. 一个完成语义

动作成功、局部像素变化、阶段成功和用户任务完成是四件事：

- **action success**：输入被执行；
- **local effect**：预期局部区域发生了与动作相关的变化；
- **stage success**：阶段工件或义务满足；
- **task completion**：用户的最终目标由当前证据满足。

只有最后一层可以让外层 run 进入可信完成。模型总结、工具 `ok` 或 runtime `done` 都只是候选状态。

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

编译器适合表达“先得到工件，再发送工件”这类显式数据依赖，不适合替模型预写未知 GUI 的每一次点击。无法从用户语句确定的信息应由 Agent 到达对应界面后观察决定。

### ARP

`arp/1` 是可执行图契约，负责阶段顺序、失败边、等待、人类门和运行审计。它不应成为第二个 GUI planner。Task Contract 进入 ARP 后，未知视觉步骤仍由同一个 bounded Agent/Controller 闭环处理。

### StageRuntime

StageRuntime 是阶段生命周期 owner：能力预检、工件传递、可选 Skill、执行、验证、失败恢复。它可以调用通用 GUI runtime、确定性工具或 MCP，但不应重新解析一套鼠标动作语言。

## 视觉与感知

### 原生视觉主路径

当主模型明确支持图像时，普通 GUI 回合直接附加当前权威 JPEG。模型从画面理解语义并提出一笔 typed transaction；动作后重新捕获权威帧，再进入下一回合。

默认原则是：

- 不在每次模型看图前强制运行全屏 UIA/OCR；
- 不把缩放后的 JPEG 像素当物理桌面坐标；
- 不在一个模型回合连续执行多笔未经新帧验证的空间动作；
- 看不清目标、规则或结果时继续观察或 `blocked`，不猜。

### 结构化辅助路径

结构化信号适合：

- 已确认的标准输入框、按钮和菜单；
- 文本查询与短标签定位；
- 后台安全且可验证的 UIA/Win32 操作；
- 非视觉模型的 fallback；
- scoped OCR 和结果交叉核对。

结构化信号不适合单独理解游戏、自绘画布、动画、图片按钮、视觉布局规则或复杂跨区域关系。旧 `fusion.observe` 仍保留 UIA/OCR 优先和 rich-skip 分支，这是兼容债务，不是目标架构。

### 窗口与应用发现

应用解析应按以下顺序收集事实后决定：

1. 已运行且可见/可恢复的窗口；
2. 托盘或隐藏窗口；
3. 已安装应用和开始菜单入口；
4. 用户指定路径或可执行文件；
5. 最后才启动新实例。

“打开 QQ”不意味着无条件再启动一个 QQ。窗口标题、进程、可见状态、登录态和当前画面需要一起判断。应用别名只能帮助发现候选，不能决定操作流程。

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

`draw_strokes`、`drawing_gui` 和 DrawArtifact 是历史命名或上层语义，不应限制底层能力的可用范围。拖动验证控件、摆放游戏对象、移动时间轴、手写和画图共用相同的指针、区域、路径与验证基础设施。

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

计划是可变工作状态。模型可以添加、删除、移动、重开或替换 todo；初始计划不能限制后续视觉探索。

上下文有三层：

- 当前 run 的目标、计划、窗口、画面和工具证据；
- session context，用于“刚刚画的画”“继续”等跨 turn 引用；
- long-term Memory/Skill，用于经过验证的经验复用。

冷启动只禁用第三层经验能力，并清空测试要求的 session；它不应破坏同一客户端会话中明确需要的多 turn 依赖。测试 harness 必须显式区分“独立 case”与“同一 scenario 的后续 turn”。

Memory Pack 只能作为提示。当前权威帧、确定性文件/进程证据和用户最新指令优先于记忆。

## MCP 边界

MCP 是可选 capability source。办公套件、云服务或结构化系统存在可靠 MCP 时，StageRuntime 可以选择它；没有 MCP 或 MCP 不适合时必须保留 GUI 路径。

选择 MCP 的条件应是能力、授权、输入输出和验证契约匹配，而不是应用名硬编码。MCP 成功也需要进入同一 artifact/evidence/completion 体系。

## Trace、Artifact 与 Replay

- Trace 保存模型请求/响应、工具事务、帧、证据和状态变化。
- ArtifactStore 区分用户工件与模型观察帧，并用 run-scoped id/hash 传递。
- `automation-ir/1` 是新保存流程和 Skill 的规范中间层。
- `.wps.json`、FlowScript、DSL 和旧浏览器 executor 是兼容路径，不是 GUI Agent 的事实源。
- Replay 只有在适用条件和 postflight evidence 重新通过时才可算 Skill 成功。

## 当前实现债务

全面审查确认以下问题仍在代码中，文档不能掩盖：

1. `agent/loop.py` 体积过大，混合规划、动作归一化、状态账本、上下文和恢复。
2. `TaskContractCompiler`、`StageRuntime`、`InteractionStageEngine`、`SubgoalExecutor` 和 Controller 存在重复解释。
3. 某些 communication/application adapter 仍含布局、别名或任务族特化。
4. `fusion.observe` 的旧默认顺序仍可能让 UIA/OCR 影响 VLM 调度；原生视觉主路径已经绕开部分问题，但未完成全局统一。
5. `/api/chat`、legacy IPC、FlowScript 和旧 feature 系统仍增加维护面。
6. 受控视觉目标的通过范围大于真实软件 E2E 覆盖，不能据此宣称通用成功率。

整改优先级见 [GUI Agent 架构审计](GUI_AGENT_ARCHITECTURE_AUDIT.md)。

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
