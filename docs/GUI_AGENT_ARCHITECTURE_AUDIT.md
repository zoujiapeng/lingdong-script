# GUI Agent 架构审计

审计日期：2026-07-18
审计范围：`python/winpilot_home/winpilot`、`src`、`scripts`、产品入口、测试入口和长期文档。

## 结论

项目已经具备真实像素、视觉模型、Windows 输入、Task Contract、ARP、Controller、证据和真实客户端 harness，但整体复杂度仍明显高于这些能力所需的最小架构。

主要风险不是缺少更多规则，而是同一件事被多个层重复解释：

- AgentRunner 解释工具、坐标、状态、恢复和完成；
- TaskContractCompiler 解释任务族和阶段；
- StageRuntime/InteractionStageEngine 再解释动作与验证；
- SubgoalExecutor/Controller 再把语义转成动作；
- adapter、scope guard、ledger 和 done gate 各自保留部分状态。

如果继续围绕 11 个样例追加关键词、提示词、应用布局和完成分支，代码会变多，未知任务能力反而下降。

## 已确认的正确方向

本轮代码和测试已经建立以下可继续复用的通用基础：

- 可见 Electron 客户端 E2E runner，而不是只调用 API；
- run 级冷启动和 session reset 证明；
- outer run、trace 和 external oracle 绑定；
- 权威截图与 frame/window identity；
- 统一的点击、滚动、输入、拖拽、路径和跨帧 held-pointer 原语；
- browser target 的可信 pointer event oracle；
- 可选 MCP capability source；
- graphical affordance/scene coverage 只从当前像素生成候选，不携带应用名或对象模板。

这些机制可以保留，但必须由所有视觉任务共享的固定工具和 owner 直接提供，不能只从某个 stage、应用或 test fixture 可达。

## 仍然存在的结构性问题

### 1. AgentRunner 过重

`agent/loop.py` 同时承担：

- 模型消息与工具 schema；
- frame 注入和坐标映射；
- action canonicalization；
- 多种进度/失败账本；
- context folding；
- scope/recovery；
- done 编排。

这使得任何 GUI 改动都容易扩散到巨型类。当前代码已经引入四个应继续收敛的 owner：

1. `FrameAuthority`：只原子发布帧、窗口、区域、epoch、坐标和新鲜度身份，不截图、不解释画面；
2. `FastGuiController`：只规范化并执行一笔 action transaction，返回局部效果证据，不规划任务；
3. `RunStateMachine`：只记录 phase、因果债务、未验证动作和终态，不生成 todo 或恢复步骤；
4. `FailureRecoveryLayer`：只分类失败并附加事实元数据，不自行选择重试、UIA/OCR 或 MCP。

`AtomicAction` 与事务证据是这些 owner 之间的 typed data，不应再发展成第五个 planner。旧账本、scope guard、拖拽和 held state 必须逐步迁到唯一 owner，而不是继续复制一套。

### 2. 多套 planner/executor 重叠

当前可见的执行层包括 `AgentRunner`、`StageRuntime`、`InteractionStageEngine`、`SubgoalExecutor`、`FastGuiController`、`ToolSession` 和 raw action executor。

目标依赖方向应是：

```text
user goal
  -> task/stage contract
  -> semantic decision
  -> one canonical action transaction
  -> raw device executor
  -> evidence reducer
  -> fresh authoritative frame
  -> next semantic decision
```

每层只允许向下一层传 typed data。下层不得反向 import 上层 planner 或 domain adapter。

### 3. Compiler 容易变成脆弱规则库

Task Contract 对跨应用工件、显式阶段和完成义务有价值，但 compiler 内的关键词/任务族分支会让模型在看到界面前就失去探索空间。

普通未知 GUI 目标应保留原文并进入一个 `ExecuteDesktopTask` 视觉阶段。模型始终持有完整目标；本地编译器不应把它拆成需要逐项执行的脚本。原生客户端工具面保持小、固定、通用，复合能力来自视觉闭环而不是不断增加宏工具。

保留标准：

- 从用户语句可以确定的 artifact dependency；
- 明确的外部系统/MCP capability；
- 需要独立证据的业务义务；
- cold-start/context policy。

迁移或删除：

- 根据应用名决定具体点击步骤；
- 根据样例措辞删除或添加固定 stage；
- 通过题号或原句选择上下文；
- 让 stage 名称决定底层能否拖拽、点击或滚动。

### 4. UIA/OCR 已退出默认路径，兼容入口仍需约束

原生视觉 Agent 主路径直接接收权威帧；当前默认配置已经关闭 UIA、OCR 与 predictive OCR。`perception/fusion.py` 的旧结构化路径仍包含 UIA/OCR rich/skip 行为，部分兼容 helper 也会把 OCR/UIA 候选直接提升为目标，因此“默认关闭”不能替代 provenance、frame consistency 和显式选择约束。

整改原则：

- pixel-rendered 任务默认由当前画面和视觉模型理解；
- UIA/OCR 只有同 hwnd、同 epoch、可见边界和像素一致时才能增强目标；
- 自绘/游戏/画布界面不能因 UIA 树为空或错误而降级能力；
- structured UI 的确定性收益单独保留，不能污染通用视觉默认路径；
- 所有 provider 都必须携带 provenance 和 confidence，最终选择写入 trace。
- 动作失败后首先废弃旧 frame/anchor，重新观察并让模型判断；不得因失败自动启动 UIA/OCR/MCP fallback cascade。
- 辅助通道只有在当前任务、来源和可验证收益明确匹配时才显式启用。

### 5. 多源应用发现已落地，窗口恢复 owner 仍分散

当前实现已经合并 Start Menu 快捷方式、`Get-StartApps`、App Paths、可执行/进程 identity 和启动前后窗口证据，并默认先复用身份匹配的已运行实例。窗口列表、托盘、登录态、alias、focus fallback 和 launch 仍分布在多个模块；剩余风险包括隐藏实例恢复、登录页选择和焦点来回切换。

继续收敛到一个 `AppResolver` owner，并保持“基础工具发现事实，模型根据画面决策”的边界。以抽象 `app.open` 合同为例，它统一查询运行进程/窗口、Start Menu、StartApps、App Paths、安装信息和快捷方式元数据，输出候选、provenance、启动方式及启动前快照。执行后再用 pid/path/hwnd、时间和窗口变化证明新状态由本次动作产生。

Agent 根据当前任务和画面选择复用、恢复或启动；resolver 不写应用工作流，也不靠应用专属 prompt 弥补索引缺失。本地抖音真实客户端任务已经证明现有多源索引可以找到桌面客户端，但一次通过不等于 owner 已完全统一。

### 6. 特化 adapter 仍是技术债务

通信和部分 artifact stage 仍包含应用布局、控件别名、任务族 shortcut 或固定 fallback。已有真实成功记录不能证明这些规则可泛化。

处理顺序：

1. 用 trace 证明通用原语已经覆盖的部分；
2. 把 artifact identity、conversation identity、send/delivery evidence 保留为领域合同；
3. 把查找窗口、点击、输入、拖拽、滚动全部迁回通用 runtime；
4. 删除固定物理坐标和应用版本布局；
5. 无法迁移且只有单样例价值的代码直接删除。

领域合同可以存在，但不等于领域 GUI 脚本。

### 7. Flow/Browser 兼容面增加维护成本

FlowScript、DSL、WPS、旧 feature bridge 和浏览器 executor 有历史兼容用途，但不是 GUI Agent 硬实力的主线。新功能不得同时向这些路径复制实现。

短期规则：

- 新 artifact 只写 `automation-ir/1`；
- 旧格式通过 adapter 导入/导出；
- 浏览器 executor 不得模拟真实桌面成功；
- Studio 只显示前后端都声明可执行的节点；
- 没有真实用户/兼容需求和调用 trace 的旧入口列入删除候选。

独立 `docs/visual-runtime-guide` 分支中的浏览器交互图解不合并；长期架构以 Markdown 和真实代码为准。

## 测试审计

### 有效的客户端入口

- `scripts/client_e2e_run.py`：操作可见 Electron 客户端，运行真实任务与组合长任务；
- `scripts/client_visual_e2e_run.py`：复用客户端入口，受控目标提供外部 pointer oracle。

E2E harness 使用 `uiautomation + Win32` 操作客户端自身控件。这不意味着 Agent 用 UIA 理解目标应用；harness 驱动与被测 Agent 感知必须分开描述。

### 仍需避免的假通过

- runtime `done` 没有独立 oracle；
- 只证明打开应用，没有证明后续目标；
- 只看到草稿或附件预览，就声称已发送；
- 只看到像素变化，就声称业务完成；
- trace 通过目录时间差绑定而非准确 run id；
- 冷启动只改前端开关，child runtime 仍使用经验；
- 后续 turn 没有 typed dependency，却从全局最近工件猜“刚刚”；
- 外部窗口抢焦点后仍把 run 计入稳定基线。

完整规则见 [testing.md](testing.md)。

## 当前验证状态

当前整改工作树已经从可见 Electron 客户端执行冷启动、空 session 的真实任务与视觉靶场，不再使用“只做了文档审计、没有跑 E2E”的旧基线：

- 本地抖音客户端显示 `kimi k3` 搜索结果，终态经人工视觉核对；通用 report 没有独立业务 oracle，因此自动报告仍不能记为 grounded pass；
- `game-drag-v2` 的客户端 run 与独立 trusted-browser pointer oracle 均通过，正式计为 E2E pass；
- 网易云任务达到请求歌曲播放和 Windows 音量 100%，用户明确确认成功；随后按用户要求停止声音与测试，harness 没有 terminal pass，因此只记“用户确认业务目标完成”，不改写成 formal terminal pass；
- 11 个真实任务尚未全部通过，混合长任务也没有当前版本的全量通过证据；
- 其他失败、`ungrounded`、超时和外部干扰 run 保留原分类，不能由文档改写为成功。

## 下一轮整改顺序

### A. 收敛动作 owner

- 定义唯一 canonical action transaction；
- 让 `FrameAuthority`、`FastGuiController`、`RunStateMachine`、partial-output guard、scope guard 和 done gate 读取同一身份与事务；
- 删除重复的复合动作、拖拽和 held-state 解析；
- 保持 raw executor 只负责输入，不解释任务语义。

### B. 收敛感知权威

- 明确 native visual 与 structured UI 两条策略的入口条件；
- 移除 UIA/OCR rich 对视觉语义的隐式否决；
- 给结构化 anchor 加 frame/pixel consistency gate；
- 失败统一回到“重新观察 → 模型判断”，由 `FailureRecoveryLayer` 只提供分类事实；
- 用自绘界面、错误 UIA 树和 OCR 干扰做负向回归。

### C. 收敛应用发现

- 在已有 Start Menu/StartApps、App Paths、快捷方式、进程身份和启动因果证据上，继续统一运行态/窗口态/托盘、登录态、安装态和启动态；
- 默认先复用已运行实例；
- 候选含糊时让模型看当前画面决定；
- 启动后用进程/窗口的前后快照验证因果身份，不接受仅命令返回成功；
- 删除散落 alias/focus/launch 重复实现。

### D. 削减 compiler 与 adapter

- 按生产关键词和应用名建立清单；
- 区分合法领域合同与 GUI 剧本；
- 每轮迁移一个调用面并净删代码；
- 11 题只能留在 test fixture 和 oracle。

### E. 扩大真实客户端矩阵

- 增加滚轮探索、遮挡、托盘、已有实例、动态弹窗和多 turn 引用；
- 每类至少一个不同应用/不同布局；
- 保留失败，不以新增规则换取单例绿色；
- 增加失败后仍保持 native visual 主线、没有隐式切换辅助通道的断言；
- 逐步增加重复次数和 seed 后才报告统计数据。

## 变更准入门

每个 GUI runtime PR 必须回答：

1. 解决的是通用失败模式还是一个样例？
2. 唯一 owner 是谁？是否新增了重复状态？
3. 为什么不能删除或复用现有路径？
4. UIA/OCR/MCP 的证据边界是什么？
5. 冷启动下是否仍成立？
6. 从真实 Electron 客户端如何验证？
7. external oracle 是什么？
8. 在第二个不同任务族上是否成立？
9. 生产代码净增多少，删除了什么？
10. 哪些能力仍未验证，文档是否明确说明？

缺少这些答案时，不应通过增加 prompt、route、ledger、adapter 或 compiler 分支来“修复”。
