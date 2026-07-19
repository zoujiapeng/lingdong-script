# WinPilot Agent 实现说明

本文补充 [architecture.md](architecture.md) 的实现细节。总体原则和当前债务以前者与 [GUI_AGENT_ARCHITECTURE_AUDIT.md](GUI_AGENT_ARCHITECTURE_AUDIT.md) 为准。

## 构建与入口

`python/winpilot_home/winpilot/agent/router.py` 根据 `RuntimeProfile` 构建同一个 `AgentRunner`。GUI、Coding、MCP、Replay 和 Memory 是同一 runner 的能力开关，不是多个用户可见 Agent。

正式客户端路径：

```text
PlatformWorkbench
  -> task-contracts/compile
  -> arp/runs
  -> task_stage
  -> StageRuntime
  -> one ExecuteDesktopTask visual stage
  -> AgentRunner
  -> FrameAuthority / FastGuiController / RunStateMachine / FailureRecoveryLayer
```

`/api/chat` 会直接创建 Agent runtime，用于 integration、MCP 和兼容调用。它共享工具、Controller、trace 和 done verifier，但绕过客户端的 Task Contract/ARP 提交流程，因此不能替代客户端 E2E。

## RuntimeProfile

`agent/runtime_profile.py` 控制每个 run 可用的能力，例如 GUI、coding、memory、replay 和 MCP。profile 只裁剪能力，不根据任务名选择不同 Agent。

`cold_start=true` 时，Task Contract、ARP 和 child runtime 必须共享 `experience_policy=disabled`，关闭：

- Memory Pack；
- Skill/replay 建议和执行；
- session 外长期经验提示；
- 经验写入和反馈。

历史 schema 中的绘图别名只用于兼容读取，不能产生绘图专属 runtime，也不属于当前 native 模型工具面。

## 主循环

AgentRunner 的语义循环可以简化为：

1. 保留完整用户目标，建立可变工作状态和 run 状态；
2. 对 GUI 任务捕获并由 `FrameAuthority` 原子发布当前权威帧；
3. 构建紧凑 system contract、当前 context 和工具 schema；
4. 模型从完整目标、当前画面和失败事实提出一个工具调用或状态；
5. `FastGuiController` canonicalize/authorize 一笔事务；
6. ToolSession/Controller 执行并记录 local evidence；
7. `RunStateMachine` 记录因果债务，捕获并发布新帧；
8. 成功时继续闭环；失败时由 `FailureRecoveryLayer` 分类，重新观察后仍由模型决定下一步；
9. 最终证据不足时继续/blocked/fail，不生成虚假完成。

初始 todo 不是编译后的不可变脚本，也不是对模型公开的第二套工作流语言。模型可在推理中调整计划，但完整用户目标始终是根约束。

## ToolSession

`agent/tools.py` 的 `ToolSession` 持有当前 run 的工具状态和统一 dispatch。工具按 mixin 拆分在 `*_tools.py` 中，包括窗口、感知、交互、系统、文件、memory、coding 等。

任何 GUI mutation 都必须经过：

- capability/allow 检查；
- target window 和 scope；
- frame/coordinate grounding；
- Desktop Action Queue；
- trace；
- post-action evidence。

Fast Controller 内层的常见动作可通过 `action/raw_device.py` 避免递归支付完整 dispatch 生命周期，但外层授权和证据只能执行一次，不能被绕过。

## 工具 schema

`agent/tool_schemas.py` 提供 function-calling schema。真实客户端的 native GUI profile 保持一个小而固定的通用操作面：

- 观察、列出/聚焦窗口和发现/启动应用；
- 点击、移动、滚动、文本输入和快捷键；
- 拖拽、连续路径以及跨帧 `mouse_down` / `move` / `mouse_up`；
- 有界等待；
- 工件交接、通信和显式 MCP 调用；
- `done` / `blocked` / `fail` 终态。

工具只表达通用操作，不表达“完成某类任务”的宏。复合目标由模型逐帧完成，所有空间动作最终降低到同一种 canonical transaction。较大的 standard/full schema 属于兼容或开发调用面，不是客户端 GUI Agent 的架构合同。

## 权威帧

截图由 capture/window 层产生，`agent/frame_authority.py` 的 `FrameAuthority` 只负责把已经产生的帧作为不可混淆的 `FrameState` 原子发布。它可以绑定：

- 经验证的前台目标窗口裁剪；
- 完整虚拟桌面。

窗口隐藏、目标含糊、任务跨窗口、几何异常或裁剪收益不足时使用完整桌面。每帧记录 image identity、frame id、epoch、hwnd、capture scope、物理 region 和时间。`FrameAuthority` 不截图、不解释画面、不选择动作。

原生模型工具使用它实际收到的完整图片像素：点使用 `image_x/image_y`，拖拽和连续路径使用对应的 `image_*` 字段。runtime 先校验这些坐标位于该图片内，再根据同一 `FrameState` 的图片尺寸和 capture region 映射到内部 0..1000 空间与物理桌面。`frame_*` 只保留给内部/兼容调用；模型不能把旧帧、缩略图或另一张截图的像素当作当前 Windows 坐标。

## 感知策略

### Native visual

主模型明确支持图像时，普通 observe 直接附加当前权威 JPEG，不先强制拆成 UIA/OCR/VLM 摘要。同一视觉回合通常只接受一笔 mutation，随后重新捕获。

当前默认配置是 UIA、OCR、predictive OCR 关闭，`native_vision_direct=true`。这是默认调度策略，不是删除 structured assist。

### Structured assist

UIA、Win32、OCR、CV 和独立 VLM fallback 用于：

- 明确文本查询；
- 标准控件定位；
- scoped OCR；
- changed-only/perception cache；
- 显式选择的非视觉兼容运行；
- 有明确收益的交叉验证。

`perception/fusion.py` 仍包含旧的 provider priority 和 rich-skip 路径。它们是 structured/fallback 兼容实现，不能被解释为 UIA/OCR 对所有 GUI 的语义优先级。

辅助 provider 只有在模型或合同显式选择、当前任务确实受益时才运行。动作失败不是自动切换 UIA/OCR 的理由。任何结构化元素要参与点击，至少要核对当前 hwnd、可见边界、frame epoch 和必要的像素一致性。OCR 大框中的文字不应把模型当前帧内更精确的 subtarget 吸附到错误中心。

## Fast GUI Controller

`fast/controller.py` 的 `FastGuiController` 管理一笔通用动作的规范化、低延迟执行和局部验证。相关底层模块包括：

- `fast/action_contract.py`：typed action；
- `fast/perception_daemon.py`：watcher 与 ElementCache 热状态；
- `fast/input_locator.py`：UIA-off 输入候选；
- `action/desktop_queue.py`：桌面资源租约；
- `action/executor.py`：Windows 原子动作；
- `action/verify.py`：局部动作验证。

Controller 对每个外部动作只 canonicalize 一次，并负责“动作是否在正确窗口、正确帧、正确区域执行并产生局部效果”。它不持有第二份帧真相，不负责拆分完整目标，也不判断开放世界用户目标已经整体完成。

## 运行状态与失败恢复

`agent/run_state.py` 的 `RunStateMachine` 是单一运行进度账本：记录 phase、动作前后转换、未验证因果债务和终态。它只跟踪状态，不替模型制定计划。

`agent/failure_recovery.py` 的 `FailureRecoveryLayer` 只给失败附加稳定分类和事实元数据，返回值不会执行恢复动作。恢复顺序固定为：

1. 清理未配对输入并使旧 frame/anchor 失效；
2. 捕获并发布新权威帧；
3. 把失败事实与完整目标交还模型；
4. 由模型决定继续观察、调整动作、等待、`blocked` 或 `fail`。

运行时不得根据失败类型自动切换 UIA、OCR、MCP、领域 adapter 或另一套 planner。辅助能力只有在当前证据显示它明确有益、并被本回合显式选择时才能使用。

## 连续指针

拖拽、滑块、画布、签名、地图、时间轴和游戏式对象移动共享：

- source/target/route/effect region；
- source footprint；
- interaction region；
- path clearance；
- preflight freshness；
- post-action causal evidence。

长路径的新鲜度可以沿路线抽样局部 ROI，不能只用一个巨大包围框稀释局部变化。

目标在按下后才出现时，`pointer_hold_before/after` 可建立跨帧 held session：

- session 由 runtime 创建，模型不能伪造；
- 新帧出现后旧空间坐标失效；
- held 状态只允许继续移动和匹配释放；
- 失焦、超时、取消、错误和终态都强制清理。

## 窗口和焦点

GUI mutation 前必须确认目标窗口。应用发现应先搜索已运行/托盘/隐藏实例，再决定是否启动。focus recovery 只能做有界、应用无关的尝试；恢复后必须废弃旧帧和旧元素。

目标基础工具遵循“多源发现并返回证据，模型结合画面决策”。抽象 `app.open` 不是应用工作流宏：当前实现已读取运行进程/窗口、Start Menu、StartApps、App Paths、快捷方式目标和可执行身份，返回带 provenance 的候选并优先复用身份匹配的实例；启动后再比较前后进程/窗口快照，以 pid、路径、hwnd 和时间关系验证因果身份。托盘、登录态、focus 与 launch 仍分散，属于待收敛债务。

如果用户或无关窗口在物理动作期间接管焦点/光标，运行时应停止或重新观察。Agent 自己触发的弹窗属于产品状态，不能归为外部干扰。

## Desktop Action Queue

`physical` 资源保护真实鼠标键盘，全局单写。已经证明不移动光标/焦点的 PostMessage/UIA 窗口动作可以使用 `window:<hwnd>` 资源，不同 hwnd 并行，同一 hwnd 串行。剪贴板另有独占资源。

`controller.multi_input_cooperation` 默认关闭。开启也只是人类优先仲裁，不是两套硬件输入。

## Done Gate

`agent/done_verifier.py` 将任务完成与动作完成分开。常见拒绝原因：

- 当前画面没有用户要求的结果；
- 只有旧截图或旧 OCR；
- 只有工具 `ok` 或 pixel-effect；
- 发送任务只有草稿/附件预览，没有目标会话中的发送后证据；
- 文件任务没有 file/hash 等确定性证据；
- artifact identity 不匹配；
- completion obligation 未全部满足。

对可见 GUI 条件，fresh native frame 是最终依据；OCR/UIA 可以交叉核对但不能覆盖相反画面。文件、进程等不可见条件使用确定性 provider。

## Context 和 Memory

上下文折叠需要保留目标、当前计划、窗口/frame 状态、失败原因和最近证据。session context 只解析当前 scenario 的指代；不能把上一条失败 run 或全局最近文件当作“刚刚的工件”。

长期记忆位于 Memory Graph。复用需要 typed applicability、当前脚本 hash 和验证谱系；replay 后仍执行当前 postflight。Memory 不得覆盖当前截图和确定性证据。

## MCP

`integrations/mcp_client.py` 和相应工具将 MCP 作为显式可选能力来源。调用需要 capability、授权、输入输出和 evidence contract 匹配。MCP 适合结构化办公和外部系统，但不是 GUI 失败后的自动 fallback；原生视觉主线必须能独立运行，MCP 成功也进入同一 completion gate。

## Trace 与复盘

Trace 位于忽略目录 `python/winpilot_home/traces/`。复盘重点：

- outer/child run 关系；
- `agent_step`；
- LLM request/response；
- tool start/result；
- before/after/terminal frame；
- frame/window identity；
- action transaction 与 input cleanup；
- done check、artifact 和 evidence。

LLM history 中的工具结果可能经过 compact，完整结果以 trace artifact 为准。

## 测试

单元/API/Controller/客户端 E2E 的边界和命令统一见 [testing.md](testing.md)。

需要特别记住：

- `scripts/gui_eval_run.py` 是 integration runner；
- `scripts/client_e2e_run.py` 才操作真实客户端运行 11 个任务和 mixed scenario；
- `scripts/client_visual_e2e_run.py` 从客户端提交任务，并用受控窗口外部 oracle 验证物理输入；
- harness 使用 UI Automation 操作被测客户端控件，不代表 Agent 依赖 UIA 理解目标应用；
- 单次 run 只证明当前环境下的一个样本，不是成功率。

2026-07-18 的当前样本包括：本地抖音搜索结果经人工视觉核对；`game-drag-v2` 由独立浏览器 pointer oracle 正式判定通过；网易云播放与系统音量 100% 由用户确认，但 harness 随后按用户要求停止，未形成 terminal pass。更精确的状态边界见 [testing.md](testing.md)。

## 当前限制

- AgentRunner 和 InteractionStageEngine 仍过重；
- 多层仍重复解释 action、progress 和 completion；
- 四个核心 owner 已建立，但旧状态与恢复逻辑尚未全部迁移到唯一职责；
- structured UI 已退出默认配置，但兼容入口仍需防止 UIA/OCR 反向覆盖视觉判断；
- 多源应用索引与已运行实例复用已经落地，托盘、登录态和窗口恢复尚未统一为一个 owner；
- 某些领域 adapter 和 compiler 仍有特化；
- 连续移动目标、多显示器、高 DPI、动画、复杂游戏和真实消费软件覆盖不足。

不要通过增加专属 prompt 或写死流程隐藏这些限制。
