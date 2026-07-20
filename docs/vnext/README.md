# vNext Agent 核心旁路

状态：**设计阶段，仅文档，尚未实现运行时代码**

迁移分支：`codex/vnext-agent-core`

目标：在不复制产品入口、不编写应用剧本的前提下，旁路建设新的 Agent 核心，达到验收门槛后逐步切换并删除旧职责。

## 为什么建设 vNext

当前系统的问题不是“模型还少学了几个界面规则”，而是一个模型和多层本地代码同时承担太多职责：模型既要理解像素，又要记住长任务主线、选择工具、维护鼠标状态、解释失败并给自己验收；Compiler、StageRuntime、AgentRunner、Controller 和 adapter 又会重复解释同一个目标与动作。

vNext 将这些职责压缩为一条主线：

> **一个唯一可修改 GUI 的 Visual Actor，一个唯一可写 MissionState 的确定性 Reducer，多个无桌面写权限的按需认知角色。**

Visual Actor 只做最难本地化的事情：根据当前真实像素和局部义务，选择下一笔语义动作。任务主线、依赖、工具面、输入状态、证据和最终完成都由外部确定性组件维护。

## 不可妥协的边界

1. 只有 Visual Actor 可以提出桌面 mutation；Planner、Inspector、Verifier 永远没有 GUI mutation 工具。
2. 只有 Mission Reducer 可以修改 Mission Ledger；所有模型只能提交 typed proposal、claim 或 report。
3. `original_goal` 完整、不可丢失；Obligation 只描述期望状态、验收条件、依赖与禁止效果，不描述点击步骤。
4. Capability Broker 根据真实运行状态投影小工具面，不根据应用名、测试题号、prompt 关键词或模型自报阶段切换工具。
5. Controller 只执行模型选择的动作，不做 OCR 吸附、不换按钮、不启动 UIA/OCR/MCP fallback、不判断任务完成。
6. `claim_obligation_done` 只是候选声明；确定性证据、应用 oracle、provider receipt、独立视觉 Verifier 按顺序验收。
7. Planner 和 Visual Inspector 只按事件调用；没有跨应用依赖、重复失败或复杂视觉证据时不启动。
8. 基础工具与调度骨架优先于 prompt。没有证明工具和数据流正确前，不允许用更多提示词“教会”模型某个任务。
9. vNext 与 legacy 共用可见 Electron 用户入口、冷启动语义、Artifact/Evidence、FrameAuthority、Controller 基础和 E2E oracle；不得为 vNext 建第二套测试入口。
10. 11 个任务及其组合只能作为黑盒探针和报告数据，不能进入生产 prompt、Broker、Compiler、坐标或完成条件。

## 文档导航

| 文档 | 作用 |
| --- | --- |
| [architecture.md](architecture.md) | 目标架构、唯一写入者、角色边界、完整运行流程 |
| [contracts.md](contracts.md) | MissionState、Obligation、MissionCard、动作、证据和角色 I/O 合同 |
| [migration-plan.md](migration-plan.md) | 旁路目录、阶段切换、旧职责删除门槛和分支纪律 |
| [evaluation.md](evaluation.md) | P0 指标、A/B 方法、真实客户端 E2E、held-out 与发布门槛 |
| [failure-triage.md](failure-triage.md) | “工具与骨架优先”诊断顺序，以及何时才应调整模型或增加认知角色 |
| [decisions.md](decisions.md) | 已接受架构决策、延后问题与防漂移规则 |

## 当前阶段

当前分支只建立架构事实和迁移合同：

- 不新增 Python、TypeScript、配置或产品开关；
- 不改变当前 `master` 的产品行为；
- 不声称 Mission Ledger、Broker 或多角色系统已经实现；
- 不把历史单次 E2E 结果改写成 vNext 成功证据；
- 下一步代码工作必须从 [migration-plan.md](migration-plan.md) 的 P0 指标和 P1 纯状态内核开始。

## 旁路不是第二条产品主线

`codex/vnext-agent-core` 是迁移集成分支，不是永久 fork。未来代码可以在独立命名空间中旁路实现，但每个 run 只能选择一个 runtime，legacy 与 vNext 不能同时操作桌面。只读 shadow 可以比较编译结果、Broker 工具投影或 Verifier 判定，不能产生第二份鼠标键盘输入。

达到默认切换门槛后，vNext 合并回 `master`，旧 owner 按证据逐项删除，迁移分支最终删除。长期仍只有一个产品入口和一个主分支。

## 每轮工作开始前

1. 先说明本轮解决的通用失败模式和唯一 owner。
2. 先检查基础工具、帧、窗口、动作事务和 evidence 是否正确。
3. 指明本轮只实现一个迁移切片，不横跨多层重写。
4. 列出准备替换的旧职责及其删除条件。
5. 预先声明测试入口、对照组、外部 oracle 和 held-out 场景。
6. 若需要增加 Planner/Inspector/提示词，先给出没有它时跨任务失败的 trace 证据。
