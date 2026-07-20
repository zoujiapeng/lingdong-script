# GUI Agent 端到端测试规范

Implementation note: the visual runtime now uses adaptive provider reasoning,
bounded model-call deadlines/retries, model-selected high-resolution
`observe.image_region` crops, and a generic persistent `wait` watcher. These
are runtime primitives only; the 11 sample tasks must not be converted into
prompt or controller rules. See
[`visual-runtime-principles.md`](visual-runtime-principles.md) for the
invariants and the client-entry evaluation contract.

最近一次真实验证（2026-07-19）使用 `npm run dev` 启动的可见 Electron 客户端，
`cold_start=1` 且 session 为空，运行 `game-drag-v2`。Agent 确实发出了可信的鼠标
拖拽事件，但模型误读弯曲通道，trusted-browser oracle 判定失败；这不是“入口成功”
的伪通过。报告：`output/playwright/client-visual-e2e/20260719-213401-suite/`。
后续整改应优先改进通用视觉判断、路径表达和失败后的重新观察，不得为该靶场添加
路线规则。

本文定义什么可以叫“真实客户端 E2E”、什么可以计为通过，以及如何使用 11 个真实
任务和组合长任务而不把它们写进生产逻辑。

## 当前基线

2026-07-19 的当前工作树已经通过可见 Electron 客户端执行冷启动 E2E，但尚未完成
11 个真实任务和混合长任务的全量验收。每个结果按实际 oracle 单独记账：

| 场景 | 当前证据 | 结论 |
| --- | --- | --- |
| 本地抖音搜索 `kimi k3` | 客户端入口、空 session、`experience_policy=disabled`、准确 trace 绑定；终态可见搜索结果 | 人工视觉核对成功；通用 report 未配置独立业务 oracle，因此自动报告不是 grounded pass |
| `game-drag-v2` | 同一客户端入口、冷启动隔离和可信真实鼠标事件成立；模型把弯曲通道误读成直角路径 | E2E 失败；独立 oracle 未通过 |
| 网易云搜索梁静茹、播放第一首并调到最大音量 | 客户端入口与冷启动隔离；新鲜帧可见播放状态和 Windows 音量 100%，用户确认成功 | 用户确认业务结果；harness 未产生 terminal pass，不能记正式终态通过 |

这些证据只覆盖三个样本。它们证明通用主线到达真实软件和游戏式拖拽场景，不证明
11 题、混合长任务、任意电脑或任意应用全部可用。失败、超时、`ungrounded` 和受干扰
run 保留原状态。

同一工作树回归为 WinPilot `1884 passed, 13 skipped, 1 deprecation warning`；
TypeScript 全量检查和 production renderer/Electron build 通过。本轮真实客户端靶场
只运行上表 `game-drag-v2`，没有把历史结果冒充本轮通过。回归证明代码合同未破坏，
不能替代真实 GUI oracle。

## 测试分层

| 层级 | 入口 | 可以证明 | 不能证明 |
| --- | --- | --- | --- |
| 单元测试 | 直接函数/类 | 局部算法、合同、失败语义 | 用户客户端可用 |
| 组件/API 集成 | FastAPI、renderer client | 协议、持久化、模块协作 | Electron 焦点、窗口切换、真实输入 |
| Controller 靶场 | 真实受控窗口 + 物理输入 | 坐标、点击、拖拽、路径、按住、外部事件 | 未知真实软件泛化 |
| 客户端视觉 E2E | 可见 Electron 客户端 + 受控目标 | 与用户同入口的视觉闭环 | 真实消费软件兼容性 |
| 客户端真实任务 E2E | 可见 Electron 客户端 + 真实应用 | 当前机器、当前版本下的完整结果 | 跨机器统计成功率 |

测试名称必须诚实。直接调用 `/api/v2/arp/runs` 是 runtime integration，不是客户端
E2E。

## vNext 旁路 A/B

`codex/vnext-agent-core` 的未来实现仍必须从同一可见 Electron 客户端进入。
`legacy/vnext` 只能由每个 run 的显式产品/实验配置选择，不能根据任务文本、应用名、
Task Profiler 或测试题号暗中切换；同一 run 不能让两个 runtime 交叉操作桌面。

vNext 内部的 `direct/standard/orchestrated` 执行深度可以由只读 Task Profiler 提议，
并由确定性 Execution Policy 校验。这是 vNext 内部认知服务深度，不是第二套产品
runtime。测试必须同时记录 recommended depth、effective depth 和 policy override。

测试不得向 vNext 预置内部 obligation、TaskProfile、ExecutionProfile、工具面、路径
或 Inspector 结论。Goal Compiler、Task Profiler、Execution Policy、Mission Reducer、
Capability Broker、Runtime envelope 和独立 Verifier 都是被测对象。只读 shadow 可以
离线比较 profile、状态、工具投影和 verdict，但不能发出第二份鼠标键盘输入。完整
指标、held-out 与切换门槛见 [vnext/evaluation.md](vnext/evaluation.md)。

## 真实客户端 E2E 的必要条件

一次通过必须同时满足：

1. 找到并核验当前工作区启动的 Electron 进程；
2. 通过可见客户端控件填写自然语言目标；
3. 在客户端真实切换冷启动、步数等用户可见配置，不用后端参数伪装客户端设置；
4. 点击真实“运行任务”控件；
5. 绑定点击后产生的准确 outer run id、child run 和 trace；
6. 保存提交前、handoff 后和终态截图；
7. runtime 没有超时、漂移、未配对输入或 unverified done；
8. 独立 oracle 证明用户目标实际成立；
9. 测试结束后只清理本次启动且已核验属于工作区的进程。

被测 run 必须保留用户完整自然语言目标，并进入与正常用户相同的执行主线。测试不得
改用更大工具 schema、额外提示词、内部宏、直接 Controller 调用或绕过客户端的预置
计划/profile。

`status=done` 只表示候选完成。没有第 8 条时，报告必须是 `ungrounded`，不能是
`passed`。

## 冷启动与上下文

冷启动用于证明硬实力不依赖历史经验。`cold_start=true` 必须让同一 run 的 Task
Contract、ARP、RuntimeProfile、StageRuntime/对应 vNext Mission Runtime、Visual
Actor、Task Profiler、Verifier 和 trace 共享 `experience_policy=disabled`。测试需
验证没有 experience prompt、Skill replay、Memory Pack、经验写入和反馈。

冷启动不等于删除用户当前对话：

- 独立 case 开始前清空 session；
- 同一 scenario 后续 turn 保留前序 typed artifact/reference；
- “刚刚画的画”必须解析到本 scenario 已验证工件，不是全局最近文件；
- 无前序工件时必须询问、探索或失败，不能从长期记忆伪造；
- 迟到旧 run 写回不得污染新 scenario。

## 11 个真实任务的定位

原始 11 题覆盖通用压力方向：

| 压力方向 | 例子覆盖能力 |
| --- | --- |
| 应用发现与复用 | 先找已运行/已登录窗口，再决定是否启动 |
| 开放世界导航 | 观察、搜索、滚动、进入子页面 |
| 连续指针 | 画、拖拽、限区路径、游戏式操控 |
| 工件引用 | 截图、刚刚的工件、跨应用传递 |
| 多阶段任务 | 打开、搜索、操作、验证的长链 |
| 视觉理解 | 自绘控件、图表、抓包列表、非标准界面 |
| 持续会话 | 等待外部消息、解析新命令、继续执行 |
| 失败恢复 | 弹窗、焦点丢失、错误窗口、动作无效果 |

允许测试代码保存原始文本、fixture 和精确 oracle。禁止生产 prompt、compiler、
profiler、policy、broker、adapter、state key 或动作坐标根据题号、原句或专属对象
分支。

11 条必须按原始用户表达提交，也可重排为多 turn、依赖和等待的长任务。测试可以知道
期望结果，Agent 的提示词和 runtime 不能知道“第几题”或获得路线解释。

应用发现还要做基础合同测试：随机选择进程/窗口、Start Menu、StartApps、App Paths、
快捷方式等来源，检查 provenance、去重、已有实例复用和启动后 pid/path/HWND 因果
身份。测试不得补充应用安装位置或专属启动提示。

## 组合长任务

`mixed` 套件不是简单拼字符串。每个 scenario 描述：

- turns；
- turn 间 artifact/session dependency；
- 允许等待点；
- 每个阶段 oracle；
- 最终 oracle；
- 清理方式。

例如先创建视觉工件，下一 turn 引用“刚刚的工件”，再进入另一应用处理，最后核对目标
应用中出现同一 hash/内容。中间失败不能靠最后模型回答掩盖。

## 视觉靶场

`scripts/client_visual_e2e_run.py` 从客户端提交不包含规则的目标，例如“完成当前界面上的
测试。”。Agent 必须自己从画面理解规则。

受控目标应随机化：

- 位置、尺寸、颜色和方向；
- source/target 数量；
- 路径和障碍；
- 按下后才出现的 checkpoint/target；
- 无关动画和焦点干扰；
- 滚动后才出现的内容。

oracle 从目标窗口外部记录可信 pointer/keyboard events 和最终状态，不读取 Agent 内部
计划决定成功。

当前视觉套件包含拖入、滑块、dynamic hold、限区 corridor 和 game-like drag。它验证
通用输入机制，不得描述为验证码、植物大战僵尸或任意游戏已经通过。

## 真实应用 oracle

优先级从强到弱：

1. 文件 hash、进程状态、应用导出结果或服务端回执；
2. 目标应用的新鲜可见状态与 artifact identity；
3. 受控窗口独立事件记录；
4. 新鲜帧视觉 judge；
5. UIA/OCR 文本交叉核对。

模型自述、工具 `ok`、旧截图、仅像素变化、输入框草稿，都不能自动证明发送、发布、
保存或业务完成。

## 焦点与外部干扰

测试机器出现无关命令行、系统弹窗或用户操作时：

- 若抢焦点或改变输入目标，本次 run 标记 invalid/interfered；
- 不计产品失败或成功；
- 恢复干净环境后用相同 seed、提示、冷启动和步数重跑；
- 报告保留干扰原因，最终基线只采用有效 run。

Agent 自己触发的窗口、Shell UI 或弹窗不是外部干扰，必须评估。

## 失败恢复断言

每个会触发动作失败、无效果、焦点变化、弹窗或 stale frame 的场景必须验证：

1. 旧 frame、anchor 和未执行空间计划已失效；
2. 未配对按键或鼠标按住已清理；
3. 下一次语义决定使用新权威帧；
4. failure layer 只记录分类，没有偷偷执行恢复动作；
5. runtime 没因失败自动切 UIA、OCR、MCP、领域 adapter 或另一 planner；
6. 模型显式选择辅助能力时，trace 说明来源、当前帧一致性和预期收益；
7. vNext 模型 proposal 的 runtime envelope 仍绑定当前 revision、obligation 和 frame；
8. 局部失败没有无理由触发整项任务重新 profiling。

强容错来自“重新观察 -> 模型重新判断 -> 通用动作再执行”，不来自越来越长的 fallback
cascade。

## 命令

先启动真实客户端：

```bash
npm run dev
```

另开终端：

```bash
npm run e2e:client:visual
npm run e2e:client:original
npm run e2e:client:mixed
npm run e2e:client:all
```

单个 deterministic 视觉 case：

```bash
python scripts/client_visual_e2e_run.py --case dynamic-hold --seed 424242 --fail-fast
```

常规回归：

```bash
python -m pytest python/winpilot_home/tests -q
python -m pytest tests -q
npm test
npm run typecheck
npm run build
npm run build:all
```

## 报告要求

每个 E2E 报告至少记录：

- git revision、branch、runtime version；
- Electron PID/HWND/command line；
- task、scenario、seed、turn dependency；
- cold-start/session reset 证明；
- outer/child run id 和 trace；
- model/provider/config hash；
- vNext TaskProfileProposal、ExecutionProfile 和 policy override（若适用）；
- display/DPI/capture scope；
- BrokerProjection、tool attempts、successful tools和输入清理；
- model proposal、Runtime envelope和TransitionRecord（若适用）；
- external oracle、errors、是否 grounded；
- before/after/terminal screenshots；
- duration 与各阶段耗时。

报告和 trace 位于忽略目录，不把大量一次性 run ID 写进 README 或架构文档。稳定结论
可以写 changelog，但必须说明适用和未覆盖范围。

## 发布门槛

涉及 GUI 主循环、感知、动作、窗口、Profiler/Broker、完成判断或客户端 harness 的提交，
至少需要：

- 相关聚焦测试；
- WinPilot 全量测试；
- TypeScript 测试和 typecheck（若触及前端/协议）；
- 一个真实客户端视觉 E2E；
- 改变真实应用逻辑时，对应真实任务 E2E 或明确记录未验证；
- 失败恢复改动时，至少一个“新帧后重新判断且未隐式切辅助通道”的负向场景；
- Profiler/Policy改动时，至少一个简单任务over-profile和一个复杂任务under-profile负向场景；
- GitHub Actions 通过。

单次成功不能换算成功率。只有固定环境、固定协议、多 seed、多次重复和外部 ground truth
才可报告统计指标。
