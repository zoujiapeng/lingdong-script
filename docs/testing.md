# GUI Agent 端到端测试规范

Implementation note: the visual runtime now uses adaptive provider reasoning,
bounded model-call deadlines/retries, model-selected high-resolution
`observe.image_region` crops, and a generic persistent `wait` watcher. These
are runtime primitives only; the 11 sample tasks must not be converted into
prompt or controller rules. See
[`visual-runtime-principles.md`](visual-runtime-principles.md) for the
invariants and the client-entry evaluation contract.

2026-07-22 的受控 vNext 证据必须按代码代际分别记账。历史基线使用 `npm run dev` 启动的
可见 Electron 客户端，通过 UIA 操作与用户相同的 vNext 选择、冷启动和提交入口，session 为空。随机化
`precise-click` seed `987654`（run `arp-20260722-154335-a9e64b`，3 turns）、seed
`424242`（run `arp-20260722-154447-9329b4`，2 turns）和 capability-admission 收敛后的 seed
`135791`（run `arp-20260722-165702-086da6`，2 turns）均由独立 trusted-browser
pointer oracle 判定通过。suite report 分别为：

- `output/playwright/client-visual-e2e/20260722-154302-suite/suite-report.json`
- `output/playwright/client-visual-e2e/20260722-154415-suite/suite-report.json`
- `output/playwright/client-visual-e2e/20260722-165627-suite/suite-report.json`

ActionEffect 接线前，最终 direction/notches scroll 合同还通过了同一可见 Electron/UIA、冷启动、
空 session 入口的双向 `scroll-reveal`：

- down seed `20260722`：run `arp-20260722-182113-91882e`，report `output/playwright/client-visual-e2e/20260722-182040-suite/suite-report.json`；
- up seed `20260723`：run `arp-20260722-181904-2b30b1`，report `output/playwright/client-visual-e2e/20260722-181825-suite/suite-report.json`。

独立 oracle 同时核对 trusted wheel、方向、实际 `scrollTop` 变化、目标窗口区域和目标完全可见。
前置失败没有删除：`20260722-174712-suite` 暴露 signed amount 语义含糊并进入 actor_error；
`20260722-175451-suite` 暴露浏览器默认滚动采样过早且 Actor 随后振荡；
`20260722-180448-suite` 的 up seed 在旧 signed 合同下重复错误方向并耗尽 24 turns。修复只改变通用
scroll tool schema、Controller 机械映射和 oracle settle；没有增加任务、seed、布局或坐标生产规则。

ActionEffect 最终代码必须单独报告，不能用上述历史双向通过覆盖。通用 120 ms post-action settle
接入后，同入口复测结果为：

- down seed `20260722`：PASS，run `arp-20260722-215529-8af388`，13 次 wheel、总位移 `+693`，report `output/playwright/client-visual-e2e/20260722-215454-suite/suite-report.json`；
- precise-click seed `135791`：PASS，run `arp-20260722-215751-b82e5c`，3 次 click，report `output/playwright/client-visual-e2e/20260722-215719-suite/suite-report.json`；
- up seed `20260723`：FAIL，run `arp-20260722-220002-9ddc79`，24 turns 后净位移反为 `+142`，最长 `unchanged_frame_streak=12`，report `output/playwright/client-visual-e2e/20260722-215930-suite/suite-report.json`。

Controller 的方向映射与 Actor proposal 一致；当前 up 失败归因于 Actor 没有利用中性反馈改变策略，
不能通过自动反向、方向规则或 prompt 教程掩盖。run `arp-20260722-214805-c9faaf` 因用户切换界面使
target identity 失效，作为外部干扰排除，不计通过或失败。上述最终代码的 2 pass / 1 fail 只是单 seed
首轮证据，不能换算成功率，也不能证明 ActionEffect 带来恢复 uplift。

此前真实尝试依次暴露了 Actor 对冗余 full-frame `observe` 的循环、视觉 Verifier provider
返回字符串 confidence/复制条件文本、harness 的 `SW_RESTORE` 改变目标几何，以及 page-ready
后仍使用旧几何。修复均发生在通用工具投影、Verifier 合同和 harness 状态读取层，没有把 case、
seed、目标位置、布局或成功条件写入生产 Agent。

本文定义什么可以叫“真实客户端 E2E”、什么可以计为通过，以及如何使用 11 个真实
任务和组合长任务而不把它们写进生产逻辑。

## 当前基线

当前工作树已经通过可见 Electron 客户端执行冷启动 E2E，但尚未完成
11 个真实任务和混合长任务的全量验收。每个结果按实际 oracle 单独记账：

| 场景 | 当前证据 | 结论 |
| --- | --- | --- |
| 本地抖音搜索 `kimi k3` | 客户端入口、空 session、`experience_policy=disabled`、准确 trace 绑定；终态可见搜索结果 | 人工视觉核对成功；通用 report 未配置独立业务 oracle，因此自动报告不是 grounded pass |
| `game-drag-v2` | 同一客户端入口、冷启动隔离和可信真实鼠标事件成立；模型把弯曲通道误读成直角路径 | E2E 失败；独立 oracle 未通过 |
| 网易云搜索梁静茹、播放第一首并调到最大音量 | 客户端入口与冷启动隔离；新鲜帧可见播放状态和 Windows 音量 100%，用户确认成功 | 用户确认业务结果；harness 未产生 terminal pass，不能记正式终态通过 |
| vNext 历史 `precise-click` seeds `987654` / `424242` / `135791` | 可见 Electron/UIA 同入口、显式 vNext、冷启动、空 session、准确 run/trace 和独立 trusted-browser pointer oracle | 三个受控 click-only seed grounded pass；不是 legacy paired、真实应用、11 题或其他原语通过 |
| ActionEffect 接线前的 vNext `scroll-reveal` seeds `20260722` down / `20260723` up | 最终 direction/notches 合同；可见 Electron/UIA 同入口、显式 vNext、冷启动、空 session、准确 run/trace、trusted wheel/滚动几何独立 oracle | 历史一对双向受控 grounded pass；不能代表 ActionEffect 最终代码 |
| ActionEffect 最终代码：down `20260722` / precise-click `135791` / up `20260723` | 同入口、独立 oracle、可信输入与 replayable frame effect；down/click 通过，up 保留真实失败 | 2 pass / 1 fail 的首轮样本；证据链可达，但 Actor 恢复收益未证明，P7 未达 |

这些证据仍只覆盖少量样本。它们证明通用主线到达真实软件、游戏式拖拽和受控 vNext 点击/滚轮场景，不证明
11 题、混合长任务、任意电脑或任意应用全部可用。失败、超时、`ungrounded` 和受干扰
run 保留原状态。

当前提交的准确全量回归计数应保存在 CI/交付报告，不在长期规范中冻结旧数字。下述标准命令通过只能证明
代码合同未破坏，不能替代上表真实 GUI oracle；同样，单个 E2E pass 也不能替代全量回归。

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

`codex/vnext-agent-core` 的实现仍必须从同一可见 Electron 客户端进入。
`legacy/vnext` 只能由每个 run 的显式产品/实验配置选择，不能根据任务文本、应用名、
Task Profiler 或测试题号暗中切换；同一 run 不能让两个 runtime 交叉操作桌面。

当前客户端已有可见的 `Legacy（默认）` / `vNext（实验）` per-run 选择器。选择 `vnext`
后，Server 注入增量 live coordinator，并完全绕过 legacy handlers；构造、模型、动作或验证
失败都不得在同一 run 回退。当前工具面只有通用 `click`、`double_click`、`scroll`、
`claim_obligation_done` 和 `blocked`；Runtime 在每回合自动捕获最新权威帧，不把等价的
full-frame `observe` 暴露给 Actor。ExecuteDesktopTask 与 live Runtime state 同时声明
canonical `visual.interact` 和 `visual.read_text`，因为模型直接读取像素；Catalog 只以
`visual.interact` 授权 GUI mutation，`visual.read_text` 不隐式加载 OCR/UIA。投影不包含 case、应用、
原句或坐标规则。独立视觉 ObligationVerifier 无 mutation 工具，最终完成仍需
deterministic MissionVerifier 与同一 run-scoped EvidenceRegistry。

click/double-click/scroll 进入同一个 `AtomicDesktopController` transaction。scroll 只走 unqueued
admitted physical SendInput backend，在 wheel 紧前执行 final target guard；结果不确定时不得切换
第二种输入机制或自动重放。其参数语义、窗口身份、post-action frame/evidence 与点击一样必须由
Runtime/Controller 机械绑定，不能由 prompt 或 case 规则补齐。

Actor-visible scroll schema 使用 `direction: "up" | "down"` 和整数 `notches: 1..12`；方向表示
显露 viewport 上方或下方内容。Controller 机械转换到 RawDevice 的 signed amount。旧 `amount`、零/负
notches、未知方向、额外字段和 bool 必须在零 RawDevice 调用下拒绝；ToolCatalog 与
RuntimeCapabilitySnapshot fingerprint 必须随 schema 稳定变化。

当前 coordinator context 是 `/1`：harness/集成测试必须覆盖模型 in-flight 时触发 pause，确认
非阻塞 `is_paused` 使旧 proposal 在零 Controller 调用下失效；resume 后必须看到新 frame/new
decision。frame freshness max-age 显式记录（当前默认 30 秒），过龄 frame 在 Broker/admission
阶段拒绝。turn count 必须来自 canonical ExecuteDesktopTask `max_steps`，与 session 请求冲突时在
模型前失败；effective budget 还要记录本地 24-turn cap。RawDevice 必须由产品 composition root
注入，缺失时禁止构造 coordinator 或 fallback 到另一 backend。

Server 在 queue prepare、模型调用和桌面 mutation 前只经过一次 runtime-aware
`DesktopRuntimeAdmission`。legacy 必须保持原顺序的 static/runtime preflight；vNext 必须从
coordinator 的同一不可变 `RuntimeCapabilitySnapshot` 校验 canonical capability/service、Catalog 与
Controller parity，并将该 snapshot 继续供 `ExecutionPolicy`、live `RuntimeState` 与 Broker 使用。
回归测试必须覆盖 legacy/vNext missing-capability、缺 contract、runtime/snapshot identity、未知 service、
Catalog/Controller drift、snapshot fingerprint replay，以及拒绝后 prepare/model/Controller/legacy fallback
调用数为零。scroll 虽有 ActionEffect 接线前最终 direction/notches 合同的一对真实客户端双向证据，
最终代码仍保留一个 up 失败；上述合同也不代表其余 GUI 原语可用。

`precise-click` 是这条窄链的第一个随机化受控 oracle，不是产品策略。上述三个历史 seed 已从
真实可见 Electron/UIA 入口获得 vNext grounded pass；三次均为冷启动、空 session，且外部
trusted-browser oracle 独立确认目标鼠标事件。它们尚未包含 legacy paired 对照，也不能满足
完整 P7。`scroll-reveal` 的 direction/notches 合同在 ActionEffect 接线前曾获得 down/up 双向通过；
最终 ActionEffect 代码的当前证据必须按 down/click 通过、up 失败单独记账，仍缺 paired、多布局、
动态画面和真实任务覆盖。
`type_text`、`hotkey`、拖拽、绘制、held pointer、ROI observe、应用发现、provider/MCP 和 live
Profiler 仍不得从这些 case 推断为可用。

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
9. 模型调用期间 pause 会使该 proposal 在 admission 前失效，resume 后不执行旧动作；
10. frame 超过显式 freshness max-age 时，Controller/backend 调用数为零；
11. canonical `max_steps` 用尽后稳定失败，不再额外调用模型或设备。

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

vNext click canary（harness 必须通过客户端可见控件明确选择实验 runtime；当前已有三个 seed 通过记录，但仍不代表完整 P7）：

```bash
python scripts/client_visual_e2e_run.py --case precise-click --seed 424242 --fail-fast
```

vNext scroll canary（随机化方向和初始位置；以下两个 seed 分别覆盖最终合同的 down/up）：

```bash
python scripts/client_visual_e2e_run.py --case scroll-reveal --seed 20260722 --fail-fast
python scripts/client_visual_e2e_run.py --case scroll-reveal --seed 20260723 --fail-fast
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
