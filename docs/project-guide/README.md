# 灵动脚本全项目精确讲解

> 源码快照：`master` / `e54d0a97f6cf332daf012c98bf3b8947f8ed9714`，2026-08-02。
> 本套文档面向第一次接手仓库的开发者，也面向需要从“用户点发送”一路追到 Windows 物理输入和外部结果的人。
> 它不把历史设计、测试名字或模型自述当成当前实现；每个结论都应能回到真实文件、符号、合同或测试。

## 1. 这套文档解决什么问题

仓库约有三十四万行文本，既有当前 Electron + Python vNext 产品路径，也有 Studio、离线回放、旧 Flow/DSL、CLI、MCP、宏、调度器、测试靶场和大量迁移中的 Legacy 代码。只阅读某个大文件，很容易得到三个错误印象：

1. 把仍被保存或测试的旧模块误认成产品默认执行链；
2. 把 Runtime 的 `done`、工具 `ok` 或模型话术误认成真实桌面任务完成；
3. 为当前模型的坐标、算术、几何或停止错误继续堆 prompt、规则、角色和应用配方。

本套文档用两层互补方式消除这些问题：

- 人工审计章节解释“为什么存在、谁调用、数据如何流动、失败在哪里分层”；
- 自动生成的逐文件索引从 Git 的 NUL 分隔路径清单重建，记录每个文件的行数、哈希、顶层符号、直接依赖和 API/IPC 入口，负责证明没有漏文件。

## 2. 推荐阅读顺序

| 顺序 | 文档 | 读完能回答的问题 |
| ---: | --- | --- |
| 1 | [系统架构](01-system-architecture.md) | 有哪些进程、哪一条才是产品主链、每个 owner 的边界是什么？ |
| 2 | [运行时全流程](02-runtime-flows.md) | 一条 goal 如何变成一次模型决定、一笔物理输入、新帧和最终状态？ |
| 3 | [合同、状态与接口](03-contracts-and-interfaces.md) | HTTP、WebSocket、IPC、图、帧、目标、动作、证据字段如何对齐？ |
| 4 | [测试、E2E 与故障定位](04-testing-and-debugging.md) | 什么才算真实通过；怎样沿四层证据找到首个分歧？ |
| 5 | [TypeScript/Electron 逐文件卷](05-file-reference-typescript-electron.md) | 前端、主进程、CLI、AI 接口和构建文件分别做什么？ |
| 6 | [WinPilot Python 核心逐文件卷](06-file-reference-winpilot-python.md) | 当前 vNext、兼容 Agent、感知、动作、记忆、录制各文件如何协作？ |
| 7 | [支撑代码/测试/脚本逐文件卷](07-file-reference-support-tests.md) | 两套测试、E2E runner、旧引擎、feature、模板和靶场分别验证什么？ |
| 8 | [机械事实索引](generated-file-inventory.md) | 某个具体文件当前真实行数、SHA、声明、依赖和入口是什么？ |

逐文件三卷由本次源码审计生成。原仓库已有的专题文档仍然有价值，见[原文档索引](../README.md)，但事实冲突时应按“当前源码与测试 → 本快照文档 → `CLAUDE.md` 最新章节 → 历史记录”的顺序判断。

## 3. 一句话理解项目

灵动脚本是一个 Windows 桌面自动化与视觉 Agent 研发仓库。当前产品目标不是为有限应用编写脚本，而是让同一个模型保留原始用户目标，在每一轮读取当前权威 JPEG，只选择一笔通用键鼠动作，然后根据新画面继续判断。

```mermaid
flowchart LR
  User["用户原始 goal"] --> Electron["可见 Electron 智能代理"]
  Electron --> GoalAPI["POST /api/v2/goals/runs"]
  GoalAPI --> Actor["一个 LiveVisualActor"]
  Actor -->|直接回答| Reply["reply 终态"]
  Actor -->|只读连接器| Connector["MCP search/call 后回到同一 Actor"]
  Actor -->|需要桌面| Lease["run-bound desktop lease"]
  Lease --> Frame["当前目标窗口权威 JPEG"]
  Frame --> Decision["一笔通用动作 / claim / blocked"]
  Decision --> Physical["AtomicDesktopAdapter → RawDevice/SendInput"]
  Physical --> NewFrame["新帧 + receipt + action effect"]
  NewFrame --> Actor
```

## 4. 三条代码线必须分开

| 代码线 | 用户/开发入口 | 当前 owner | 可以做什么 | 不能据此声称什么 |
| --- | --- | --- | --- | --- |
| 产品 vNext | Electron“智能代理” → `/api/v2/goals/runs` | `orchestrator/live_agent.py` 组合的单一 `LiveVisualActor` + `MissionRuntime` | 直接回复、显式只读 connector、按需租约、当前帧上的通用 GUI 动作 | 旧 Agent/Flow 已成为 fallback；受控靶场通过等于任意软件可靠 |
| Studio/确定性自动化 | 流程工作台把当前 FlowScript 导出为 `runtime.kind=arp_graph` 的 Automation IR；资料页另走 `/api/replay` | `arp_graph` 进入 ARP；仅 `ACTION_BATCH` 复用 ToolSession；replay 由 Replayer/RuntimeJob 持有 | 编辑、验证、零模型确定性节点、录制/导出/重放 | Studio 节点矩阵等于自然语言 Agent 能力，或 API v2 都属于产品 vNext |
| 兼容与开发接口 | `/api/chat`、`:7880`、CLI、AI/MCP、旧 engine、scheduler、feature | 各自旧 owner | 保持旧消费者、调试、迁移、脚本或外部集成 | 它们是当前 GUI 产品的第二条规划或兜底链 |

当前源码已经删除 `LEGACY_FLOW` 的执行 handler：枚举仍用于识别历史文件，但 validation 会给出迁移错误并 fail closed。旧模块仍大量存在不等于 vNext 会调用它们。

## 5. 仓库地图

| 路径 | 当前职责 | 重要边界 |
| --- | --- | --- |
| `src/main/` | Electron 生命周期、可信 preload、IPC、托管 Python、旧本地 API/调度/feature 桥 | 负责进程和前台交接，不做 Agent 语义规划 |
| `src/renderer/` | 智能代理、Studio、运行记录、资产、设置、Flow 编辑器 | `ControlPlaneClient` 只消费 Python API；浏览器状态不是服务端权威 |
| `src/cli/` | 旧命令行入口 | 是开发/兼容面，不是 GUI E2E 入口 |
| `src/ai-interface/` | HTTP/MCP 形式的旧 AI 工具面 | 与当前同一 Actor 的 connector gateway 不是同一个概念 |
| `python/winpilot_home/winpilot/vnext/` | Mission/Obligation 合同、Actor、Broker、Runtime、桌面 adapter、证据平面 | 当前最小 live 执行内核；不要与 Legacy `tasking/TaskContract` 混淆 |
| `python/winpilot_home/winpilot/orchestrator/` | ARP 图/执行、goal admission、Thread、live 组合根 | `live_agent.py` 是产品主链的 canonical composition owner |
| `python/winpilot_home/winpilot/action/` | RawDevice、队列、安全、执行与验证基础 | 物理动作层不解释应用语义 |
| `python/winpilot_home/winpilot/visual_runtime/` | 帧权威、观察、坐标与视觉状态 | 当前 JPEG 和窗口身份的机械真相 |
| `python/winpilot_home/winpilot/modeling/` | 模型调用、协议 adapter、超时与 attempt | 不新增第二 planner 或 answer fixer |
| `python/winpilot_home/winpilot/integrations/` | 显式 MCP connector gateway | 当前只允许重新核验过的只读、非 destructive 工具 |
| `python/winpilot_home/winpilot/agent/` | 大型 Legacy AgentRunner 与旧工具生态 | 迁移/兼容输入，不是当前 vNext 默认依赖 |
| `python/winpilot_home/winpilot/fast/` | Legacy 快速 Controller 与 GUI 能力 | 仍有测试和复用候选，但不能整体接回主循环 |
| `python/winpilot_home/winpilot/perception/` | Win32/UIA/OCR/CV/应用发现等 | 结构化感知是可审计辅助，不替代当前像素语义 |
| `python/winpilot_home/winpilot/tasking/` | Legacy task contract/stage/runtime | 当前 goal admission 不把目标编译为应用步骤 |
| `python/winpilot_home/winpilot/memory/` | Memory Graph、compiler、store、feedback | 记忆只加速，不能成为冷启动硬实力补丁 |
| `python/winpilot_home/winpilot/recorder/` | Trace、human recorder、replay、export | 记录/重放忠实性与 Agent 原任务成功是两个结论 |
| `python/engine/`, `python/features/`, `python/tools/` | 旧 DSL/Flow/feature 运行设施 | 服务 Studio/兼容消费者，不进入产品视觉主循环 |
| `scripts/` | 客户端 E2E、受控矩阵、campaign、报告、清理 | runner 与 oracle 不得向生产 Agent 泄漏答案/坐标 |
| `python/winpilot_home/tests/` | WinPilot 合同、集成和 harness 测试 | 大量通过仍不是用户任务通过 |
| `tests/` | 旧引擎、feature、DSL、工具回归 | 与 vNext 主链测试口径分开 |
| `templates/`, `examples/`, `vision/`, `java-homework/` | fixture、示例、视觉工具、教学样例 | 文件存在不代表生产 Agent 有专属配方 |

## 6. 当前可信快照

以下是 `CLAUDE.md` 最新章节与当前 HEAD 对齐的状态，不应被同文件更早的历史段落覆盖：

- 当前配置是 MiMo `mimo-v2.5`；屏幕优先帧（full_desktop）落地后的受控 smoke 为 `3/4`（drag-fit、slider、dynamic-hold 通过，corridor 属模型几何族），更早的 `6/10` 矩阵是 target-window 裁剪帧时代的旧基线；
- corridor 的 raw model points、canonical points 和 `physical_attempt` 一致，首个分歧在模型参数层；
- 原生 Notepad 精确替换通过；Calculator `43×17` 两次均未形成正确结果，首个分歧同样在模型识别/选择；
- `LEGACY_FLOW` 执行路径删除后，Studio 的 `input_text` + `screenshot` 外部 oracle 通过；严格离线节点独立矩阵升至 `3/19`；
- `CLAUDE.md` 最新章节记录的回归为 WinPilot `3613 passed, 13 skipped, 1 warning`、renderer `317 passed`、TypeScript typecheck 通过；它没有在同一快照重报根测试套件或完整 build，不能与较早发布门数字拼成一次“完整回归”；
- 这些数字是该快照的工程证据，不是跨机器、跨应用的成功率。

较早文档中出现的 `2/19` 是加入 `input_text` 独立 oracle 之前的历史状态。保留它有助于追踪证据演进，但不能再称“当前”。

## 7. 最小开发与验证入口

开发环境使用 Node.js 20、Python 3.11。支持的 Python 服务入口是 Electron 托管或 `serve.py`；直接运行 `uvicorn winpilot.server:app` 会被启动守卫拒绝。

```powershell
npm ci
python -m pip install -r requirements.txt
npm run dev
```

常规静态、单元与集成检查：

```powershell
npm test
npm run typecheck
npm run build
npm run build:all
python -m pytest python/winpilot_home/tests -q
python -m pytest tests -q
```

真实客户端检查需要先保持 `npm run dev` 的 Electron 可见，再在另一终端运行：

```powershell
npm run e2e:client:visual
npm run e2e:client:original
npm run e2e:client:mixed
```

## 8. 零遗漏与防漂移验证

机械索引不读取普通换行列表，而是读取 `git ls-files -z`，因此中文、空格和特殊字符路径不会被 Git quoting 或 shell 分词吞掉。生成脚本本身也在代码口径中；生成结果排除自身，避免自引用 hash。

```powershell
python scripts/generate_project_guide.py
python scripts/generate_project_guide.py --check
```

`--check` 同时验证：

1. 当前生成文本与仓库文件重新计算的结果逐字一致；
2. 每个代码、测试或配置路径恰有一个 `PROJECT-FILE` 标记；
3. 每个代码、测试或配置路径也至少出现在三卷人工语义索引之一；
4. 没有缺失、重复或额外路径；
5. 每个文件的 SHA-256、行数、Python AST 或 TS/JS 静态声明来自当前内容。

新增、删除、重命名或修改任何文件后，都应重跑生成器。人工章节负责语义，机械索引负责完整性；两者缺一不可。

## 9. 高频术语

| 术语 | 本项目中的精确定义 |
| --- | --- |
| goal | 用户提交的完整原始目标；产品主链不把它改写成关键词步骤 |
| Thread / Turn | 服务端有界对话容器 / 一次用户请求；只有成功且有用户结果的 Turn 进入后续历史 |
| ARP | 仓库中的图与执行容器；产品 vNext 固定生成 `start → full-goal AGENT_GOAL → end` 三节点壳，其中只有一个语义节点；Studio 可提交更一般的图 |
| Visual Actor | 唯一语义模型角色；先决定 reply/connector/desktop，进入桌面后每轮看权威 JPEG 选一个工具 |
| desktop lease | 某个 run 请求 Electron 让出前台并交付精确目标窗口的单次绑定 |
| target binding | `binding_id`、HWND、PID、可执行身份、标题快照、建立它的 evidence ref 与 epoch；目标 region 属于冻结目标/RuntimeFrame，不是该 wire contract 的字段 |
| authoritative frame | 与目标、epoch、区域、捕获时间和 payload hash 原子绑定的当前 JPEG |
| canonical arguments | 模型参数经过严格 schema 解析后的运行时参数；其中坐标型动作的坐标仍处于模型实际收到的 submitted-image 像素空间 |
| physical attempt | 映射到物理桌面并准备交给 RawDevice/SendInput 的实际参数记录 |
| receipt | 物理输入是否开始/完成、失败种类和机制等回执 |
| action effect | 前后可比帧上的局部视觉变化摘要；不是用户语义完成证明 |
| external oracle | 独立于被测 Runtime 的文件、应用状态、可信事件、像素或 UI 读回；用户任务是否通过的最终测试依据 |
| fail closed | 身份、帧、权限、证据或结果无法证明时停止并明确失败，而不是猜测或静默降级 |

## 10. 阅读代码时最重要的判断法

遇到任何失败或“架构很高级但任务很差”的现象，先把一次真实 run 排成四列：

```text
模型原始参数
  -> canonical 参数
  -> physical_attempt / RawDevice receipt
  -> 外部应用最终状态
```

应找的是第一个“不满足本层合同，或不符合约定的确定性变换”的层，而不是机械比较数值相等：submitted 坐标映射到物理桌面后本来就可能数值不同。如果 raw 参数按合同已错，继续加入坐标补偿、按钮吸附、应用规则、答案修正或第二模型，只会让系统在样例上更复杂、在开放任务上更脆弱；如果映射与物理回执正确但外部状态错，再检查焦点、输入机制和应用状态。
