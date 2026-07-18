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

这些机制可以保留，但必须继续下沉到所有视觉任务共享的 owner，不能只从 DrawArtifact、Paint 或某个 test stage 可达。

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

这使得任何 GUI 改动都容易扩散到巨型类。目标不是再拆几个 mixin，而是先定义三个稳定核心对象：

1. `FrameAuthority`：窗口、区域、帧、epoch、坐标、新鲜度；
2. `ActionTransaction`：意图、grounding、preflight、输入、post-frame、local evidence；
3. `TaskProgress`：阶段义务、artifact、失败原因和 task completion evidence。

上层只能组合这三个对象，不应再各自解析 batch/drag/held state。

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
```

每层只允许向下一层传 typed data。下层不得反向 import 上层 planner 或 domain adapter。

### 3. Compiler 容易变成脆弱规则库

Task Contract 对跨应用工件、显式阶段和完成义务有价值，但 compiler 内的关键词/任务族分支会让模型在看到界面前就失去探索空间。

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

### 4. UIA/OCR 仍可能误导视觉任务

原生视觉 Agent 主路径已经可以直接接收权威帧，但 `perception/fusion.py` 的旧结构化路径仍包含 UIA/OCR rich/skip 行为，部分 action helper 也会把 OCR/UIA 候选直接提升为目标。

整改原则：

- pixel-rendered 任务默认由当前画面和视觉模型理解；
- UIA/OCR 只有同 hwnd、同 epoch、可见边界和像素一致时才能增强目标；
- 自绘/游戏/画布界面不能因 UIA 树为空或错误而降级能力；
- structured UI 的确定性收益单独保留，不能污染通用视觉默认路径；
- 所有 provider 都必须携带 provenance 和 confidence，最终选择写入 trace。

### 5. 应用发现与窗口恢复仍分散

窗口列表、进程搜索、开始菜单、应用 alias、托盘、focus fallback 和 launch 分布在多个模块。失败表现包括重复启动、选择登录页、忽略已登录实例和焦点来回切换。

需要一个 `AppResolver` owner，统一输出候选及证据：运行态、窗口态、可见性、登录态线索、安装态和启动方式。Agent 根据当前任务和画面选择候选；resolver 不写应用工作流。

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

## 当前验证事实

在本次文档收口前，当前提交已完成：

- WinPilot 全量回归：`1985 passed, 13 skipped`；
- 客户端 E2E harness 聚焦测试：`18 passed`；
- 真实 Electron 冷启动 `dynamic-hold`：外部 trusted pointer oracle 通过；
- GitHub Actions：frontend 与 python 通过。

这些数字只记录本次发布检查，不代表产品成功率。`dynamic-hold` 证明按下、经过 checkpoint、观察新目标并释放的机制可达；它不证明 11 个真实任务已经解决。

## 下一轮整改顺序

### A. 收敛动作 owner

- 定义唯一 canonical action transaction；
- 让 partial-output guard、scope guard、run state、done gate 读取同一对象；
- 删除重复 batch/drag/held 解析；
- 保持 raw executor 只负责输入，不解释任务语义。

### B. 收敛感知权威

- 明确 native visual 与 structured UI 两条策略的入口条件；
- 移除 UIA/OCR rich 对视觉语义的隐式否决；
- 给结构化 anchor 加 frame/pixel consistency gate；
- 用自绘界面、错误 UIA 树和 OCR 干扰做负向回归。

### C. 收敛应用发现

- 统一运行态/窗口态/托盘/安装态/启动态；
- 默认先复用已运行实例；
- 候选含糊时让模型看当前画面决定；
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
