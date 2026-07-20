# GUI Agent 端到端测试规范

Implementation note: the visual runtime now uses adaptive provider reasoning,
bounded model-call deadlines/retries, model-selected high-resolution
`observe.image_region` crops, and a generic persistent `wait` watcher.  These
are runtime primitives only; the 11 sample tasks must not be converted into
prompt or controller rules.  See [`visual-runtime-principles.md`](visual-runtime-principles.md)
for the invariants and the client-entry evaluation contract.

最近一次真实验证（2026-07-19）使用 `npm run dev` 启动的可见 Electron 客户端，
`cold_start=1` 且 session 为空，运行 `game-drag-v2`。Agent 确实发出了可信的
鼠标拖拽事件，但模型误读了弯曲通道，trusted-browser oracle 判定失败；这不是
“入口成功”的伪通过。报告：
`output/playwright/client-visual-e2e/20260719-213401-suite/`。后续整改应优先改进
通用视觉判断、路径表达和失败后的重新观察，不得为该靶场添加路线规则。

本文定义什么可以叫“真实客户端 E2E”、什么可以计为通过，以及如何使用 11 个真实任务和组合长任务而不把它们写进生产逻辑。

## 当前基线

2026-07-19 的当前工作树已经通过可见 Electron 客户端执行了冷启动 E2E，但尚未完成 11 个真实任务和混合长任务的全量验收。每个结果按它实际拥有的 oracle 单独记账：

| 场景 | 当前证据 | 结论 |
| --- | --- | --- |
| 本地抖音搜索 `kimi k3` | 客户端入口、空 session、`experience_policy=disabled`、准确 trace 绑定；桌面终态可见本地抖音窗口和搜索结果 | 真实任务结果经人工视觉核对成功；该通用 report 未配置独立业务 oracle，因此自动报告本身不是 grounded pass |
| `game-drag-v2` | 同一客户端入口、冷启动隔离和可信真实鼠标事件均成立；模型将弯曲通道误读成直角路径 | E2E 失败；独立 oracle 未通过，不能把入口成功记作任务成功 |
| 网易云搜索梁静茹、播放第一首并调到最大音量 | 客户端入口与冷启动隔离；新鲜桌面帧可见歌曲处于播放状态、Windows 音量为 100%，用户明确确认任务成功 | 用户确认的业务结果；随后按用户要求停止声音和测试，harness 未产生 terminal pass，不能记为正式终态通过 |

这些证据只覆盖三个具体样本。它们证明通用主线已经到达真实软件和游戏式拖拽场景，不证明 11 题、混合长任务、任意电脑或任意应用已经全部可用；失败、超时、`ungrounded` 与被干扰的其他 run 仍保留原状态。

同一工作树的回归为：WinPilot `1884 passed, 13 skipped, 1 deprecation warning`；TypeScript 全量检查、production renderer/Electron build 均通过。本轮真实客户端靶场只运行了上表的 `game-drag-v2`，没有把其他任务的历史结果冒充本轮通过。这些回归证明代码合同没有破坏，不能替代上表的真实 GUI oracle。

## 测试分层

| 层级 | 入口 | 可以证明 | 不能证明 |
| --- | --- | --- | --- |
| 单元测试 | 直接函数/类 | 局部算法、合同、失败语义 | 用户客户端可用 |
| 组件/API 集成 | FastAPI、renderer client | 协议、持久化、模块协作 | Electron 焦点、窗口切换、真实输入 |
| Controller 靶场 | 真实受控窗口 + 物理输入 | 坐标、点击、拖拽、路径、按住、外部事件 | 未知真实软件泛化 |
| 客户端视觉 E2E | 可见 Electron 客户端 + 受控目标 | 与用户同入口的视觉闭环 | 真实消费软件兼容性 |
| 客户端真实任务 E2E | 可见 Electron 客户端 + 真实应用 | 当前机器、当前版本下的完整结果 | 跨机器统计成功率 |

测试名称必须诚实。直接调用 `/api/v2/arp/runs` 的测试是 runtime integration，不是客户端 E2E。

## vNext 旁路 A/B

`codex/vnext-agent-core` 的未来实现仍必须从同一可见 Electron 客户端进入。legacy/vNext 只能由每个 run 的显式实验配置选择，不能根据任务文本、应用名或测试题号路由；同一 run 不能让两个 runtime 交叉操作桌面。

测试不得向 vNext 预置内部 obligation、工具面、路径或 Inspector 结论。Mission Compiler、Mission Reducer、Capability Broker 和独立 Verifier 都是被测对象。只读 shadow 可以离线比较状态、工具投影和 verdict，但不能发出第二份鼠标键盘输入。完整指标、held-out 与切换门槛见 [vnext/evaluation.md](vnext/evaluation.md)。

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

被测 run 必须保留用户完整自然语言目标，并进入和正常用户相同的单一视觉执行主线。测试不得改用更大的工具 schema、额外提示词、内部宏、直接 Controller 调用或绕过客户端的预置计划。

`status=done` 只表示候选完成。没有第 8 条时，报告必须是 `ungrounded`，不能是 `passed`。

## 冷启动与上下文

冷启动用于证明硬实力不依赖历史经验。`cold_start=true` 必须让同一个 run 的：

- Task Contract；
- ARP；
- RuntimeProfile；
- StageRuntime；
- AgentRunner；
- trace

共享 `experience_policy=disabled`。测试需验证没有 experience prompt、Skill replay、Memory Pack、经验写入和反馈。

冷启动不等于删除用户当前对话。上下文规则如下：

- 独立 case 开始前清空 session；
- 同一 scenario 的后续 turn 保留前序 turn 的 typed artifact/reference；
- “刚刚画的画”必须解析到本 scenario 已验证工件，而不是全局最近文件；
- 没有前序工件时必须询问、探索或失败，不能从长期记忆伪造；
- 迟到的旧 run 写回不得污染新 scenario。

## 11 个真实任务的定位

原始 11 题覆盖的不是 11 套产品功能，而是若干通用压力方向：

| 压力方向 | 例子所覆盖的能力 |
| --- | --- |
| 应用发现与复用 | 先找已运行/已登录窗口，再决定是否启动 |
| 开放世界导航 | 观察界面、搜索、滚动、进入子页面 |
| 连续指针 | 画、拖拽、限区路径、游戏式操控 |
| 工件引用 | 截图、刚刚的工件、跨应用传递 |
| 多阶段任务 | 打开、搜索、操作、验证的长链 |
| 视觉理解 | 自绘控件、图表、抓包列表、非标准界面 |
| 持续会话 | 等待外部消息、解析新命令、继续执行 |
| 失败恢复 | 弹窗、焦点丢失、错误窗口、动作无效果 |

允许测试代码保存原始文本、fixture 和精确 oracle。禁止生产 prompt、compiler、router、adapter、state key 或动作坐标根据题号、原句或专属对象分支。

这 11 条必须按原始用户表达提交；它们也可以被重新排列为多 turn、带依赖和等待的长任务。测试可以知道期望结果，Agent 的提示词和运行时不能知道“正在跑第几题”或获得测试规则解释。

应用发现还要单独做基础工具合同测试：随机选择来自进程/窗口、Start Menu、StartApps、App Paths 或快捷方式的候选来源，检查 provenance、去重、已运行实例复用和启动后 pid/path/hwnd 因果身份。测试不得给模型补充某个应用的安装位置或专属启动提示词。

## 组合长任务

`mixed` 套件不是简单拼字符串。每个 scenario 应描述：

- turns；
- turn 之间的 artifact/session dependency；
- 允许的等待点；
- 每个阶段 oracle；
- 最终 oracle；
- 清理方式。

示例结构：先创建一个视觉工件，下一 turn 引用“刚刚的工件”，再进入另一个应用处理，最后核对目标应用中出现的是同一 hash/内容。中间任一阶段失败都不能靠最后一段模型回答掩盖。

## 视觉靶场

`scripts/client_visual_e2e_run.py` 从客户端提交一句不包含规则的目标，例如“完成当前界面上的测试。”。Agent 必须自己从画面理解规则。

受控目标应随机化：

- 位置、尺寸、颜色和方向；
- source/target 数量；
- 路径和障碍；
- 按下后才出现的 checkpoint/target；
- 无关动画和焦点干扰；
- 需要滚动后才出现的内容。

oracle 必须从目标窗口外部记录可信 pointer/keyboard events 和最终状态，不读取 Agent 内部计划来决定成功。

当前视觉套件包含拖入、滑块、dynamic hold、限区 corridor 和 game-like drag。它验证通用输入机制，不得被描述为验证码、植物大战僵尸或任意游戏已经通过。

## 真实应用 oracle

优先级从强到弱：

1. 文件 hash、进程状态、应用导出结果或服务端回执；
2. 目标应用中的新鲜可见状态与 artifact identity；
3. 受控窗口的独立事件记录；
4. 新鲜帧上的视觉 judge；
5. UIA/OCR 文本交叉核对。

模型自述、工具 `ok`、旧截图、仅有像素变化、输入框中存在草稿，都不能自动证明发送、发布、保存或业务完成。

## 焦点与外部干扰

测试机器出现无关命令行、系统弹窗或用户操作时：

- 若抢占焦点或改变输入目标，本次 run 标记为 invalid/interfered；
- 不把它计为产品失败或成功；
- 恢复到干净环境后用相同 seed、提示、冷启动和步数重跑；
- 报告保留干扰原因，但最终基线只采用有效 run。

Agent 自己触发的窗口、Shell UI 或弹窗不是外部干扰，必须作为产品行为评估。

## 失败恢复断言

每个会触发动作失败、无效果、焦点变化、弹窗或 stale frame 的场景还必须验证：

1. 旧 frame、anchor 和未执行空间计划已失效；
2. 未配对的按键或鼠标按住状态已安全清理；
3. 下一次语义决定使用新发布的权威帧；
4. failure layer 只记录分类，没有偷偷执行恢复动作；
5. runtime 没有仅因失败自动切换 UIA、OCR、MCP、领域 adapter 或另一套 planner；
6. 如果模型显式选择辅助能力，trace 能说明来源、当前帧一致性和预期收益。

强容错来自“重新观察 → 模型重新判断 → 通用动作再执行”的闭环，不来自越来越长的 fallback cascade。

## 命令

先启动真实客户端：

```bash
npm run dev
```

另开终端：

```bash
# 全部受控视觉场景
npm run e2e:client:visual

# 原始 11 个真实任务
npm run e2e:client:original

# 多 turn / 依赖 / 组合长任务
npm run e2e:client:mixed

# 两组真实任务
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

- git revision 和工作区；
- Electron PID/HWND/command line；
- task、scenario、seed、turn dependency；
- cold-start/session reset 证明；
- outer/child run id 和 trace 文件；
- model/provider/config hash；
- display/DPI/capture scope；
- tool attempts、successful tools 和输入清理状态；
- external oracle、errors、是否 grounded；
- before/after/terminal screenshots；
- duration 与各阶段耗时。

报告和 trace 位于忽略目录，不把大量一次性 run ID 写进 README 或架构文档。稳定结论可以写入 changelog，但必须同时写清适用范围和未覆盖范围。

## 发布门槛

涉及 GUI 主循环、感知、动作、窗口、完成判断或客户端 harness 的提交，至少需要：

- 相关聚焦测试；
- WinPilot 全量测试；
- TypeScript 测试和 typecheck（若触及前端/协议）；
- 一个真实客户端视觉 E2E；
- 改变真实应用逻辑时，对应真实任务 E2E 或明确记录未验证；
- 失败恢复相关改动时，至少一个“新帧后重新判断且未隐式切辅助通道”的负向场景；
- GitHub Actions 通过。

单次成功不能换算成成功率。只有固定环境、固定协议、多 seed、多次重复和外部 ground truth 才能报告统计指标。
