# GUI Agent 端到端测试规范

Implementation note: current live vNext keeps one original goal, one
`LiveVisualActor`, one visual execution line and eight generic GUI/control
tools. Historical `observe.image_region` and persistent `wait` descriptions
belong to Legacy or frozen experiments; they are not current client-E2E
requirements and must not be restored to teach the 11 samples. See
[`visual-runtime-principles.md`](visual-runtime-principles.md) for the
invariants and client-entry contract.

最近一次受控矩阵验证（2026-07-31）使用可见 Electron 客户端、空 session、
操作系统鼠标键盘输入和独立浏览器 oracle。UIA 只定位控件和读回目标，不写入
目标或代替点击。10 项中 5 项通过：`precise-click`、`ordered-clicks`、
`drag-fit`、`slider`、`dynamic-hold`；`answer-all-questions` 只有 4/6，
四个 corridor 变体均因模型提交的路径离开可见通道而失败。报告：
`output/playwright/github-open-e2e-20260731/controlled-suite-r2/20260731-081832-suite/`。
这只能叫受控 GUI 能力矩阵，不能冒充真实消费软件成功率。

浏览器靶场不能单独代表桌面 E2E。当前已得到 Notepad 精确落盘的 grounded pass，
并完成 Calculator、Paint、Krita、网易云、QQ、抖音、Wireshark、OneNote 等
原生探针。目标同样从可见 Electron 用操作系统键盘输入、用真实鼠标发送，Agent
动作由 RawDevice 物理执行，结果由文件系统、应用状态或其他独立 oracle 判定。
测试 setup 可以使用命令行，但用户明确要求鼠标键盘时，命令行、UIA、DOM 或 API
不能代替被测动作。

本文定义什么可以叫“真实客户端 E2E”、什么可以计为通过，以及如何使用 11 个真实任务和组合长任务而不把它们写进生产逻辑。

## 当前基线

2026-07-31 当前精确源码已复验一组真实消费软件。当前源码仍未完成 11 个真实
任务和混合长任务的全量验收；每个结果按它实际拥有的 oracle 单独记账：

| 场景 | 当前证据 | 结论 |
| --- | --- | --- |
| Notepad 精确替换并保存 | Runtime verified；屏幕和磁盘均为 `E2E-ALPHA-729`，文件 hash 独立核对 | grounded pass；通用 runner 未接 verifier 是测试假阴性 |
| 网易云搜索周杰伦并播放第一首 | 搜索第一行与底部播放器为同一歌曲，处于播放态 | 人工外部核对通过；通用 runner 报告仍为 unverified |
| 抖音搜索 `kimi k3` | 稳定主窗口中搜索框和结果页均显示查询 | 人工外部核对通过；启动器 HWND 不能当稳定目标 |
| Wireshark 最近流量 | 最近捕获有 109 个包，客户端交付的协议摘要与可见列表一致 | 人工外部核对通过；仍欠固定 pcap 自动事实 oracle |
| QQ 空间点赞 | QQ 主窗口到浏览窗口接管成功，30 次物理动作全部 applied；至少三处点赞成功，但终态当前视口仍有至少两条未赞 | 外部 oracle failed；模型覆盖、自纠错和停止失败，不是输入链失败 |
| Calculator、Paint、Krita、QQ 群摘要、OneNote | 物理回执全部进入正确目标，但外部任务分别错误、未完成或 blocked | 模型参数/规划/停止失败；不能用 Runtime 状态覆盖外部 oracle |

这些证据证明通用主线已经到达多种真实软件，不证明 11 题、mixed、任意电脑或
任意应用已经全部可用。完整 run 与分层归因见
[`GUI_AGENT_ARCHITECTURE_RESET_2026-07-29.md`](GUI_AGENT_ARCHITECTURE_RESET_2026-07-29.md)
13.16。回归只证明代码合同没有破坏，不能替代真实 GUI oracle。

## 测试分层

| 层级 | 入口 | 可以证明 | 不能证明 |
| --- | --- | --- | --- |
| 单元测试 | 直接函数/类 | 局部算法、合同、失败语义 | 用户客户端可用 |
| 组件/API 集成 | FastAPI、renderer client | 协议、持久化、模块协作 | Electron 焦点、窗口切换、真实输入 |
| Controller 靶场 | 真实受控窗口 + 物理输入 | 坐标、点击、拖拽、路径、按住、外部事件 | 未知真实软件泛化 |
| 客户端视觉 E2E | 可见 Electron 客户端 + 受控目标 | 与用户同入口的视觉闭环 | 真实消费软件兼容性 |
| 客户端真实任务 E2E | 可见 Electron 客户端 + 真实应用 | 当前机器、当前版本下的完整结果 | 跨机器统计成功率 |

测试名称必须诚实。直接调用 `/api/v2/arp/runs` 的测试是 runtime integration，不是客户端 E2E。

## 真实客户端 E2E 的必要条件

一次通过必须同时满足：

1. 找到并核验当前工作区启动的 Electron 进程；
2. 通过前台可见客户端控件和操作系统键盘填写自然语言目标；UIA 只能定位和
   读回，禁止用 `ValuePattern.SetValue`、Invoke、DOM、IPC 或 HTTP 代替用户输入；
   若长目标换行后 `ValuePattern` 只暴露首个视觉行，可用只读 `TextPattern`
   核对可见文档中的完整目标，但不得借此写入；两者都不能证明完整目标时必须在
   发送前失败；
3. 若客户端暴露用户配置，只能在客户端切换；未暴露的配置不得由测试从后端
   偷偷注入，并须记录实际准入默认值；
4. 通过操作系统鼠标点击可见“发送消息”控件；
5. 绑定点击后产生的准确 outer run id、child run 和 trace；
6. 保存提交前、handoff 后和终态截图；
7. runtime 没有超时、漂移、未配对输入或 unverified done；
8. 独立 oracle 证明用户目标实际成立；
9. 测试结束后只清理本次启动且已核验属于工作区的进程。

被测 run 必须保留用户完整自然语言目标，并进入和正常用户相同的单一视觉执行主线。测试不得改用更大的工具 schema、额外提示词、内部宏、直接 Controller 调用或绕过客户端的预置计划。

明确要求 GUI、鼠标或键盘的 fixture，其 oracle 必须显式要求对应执行介质，
不能解析目标关键词来路由 Runtime。通过还必须有对应的 `physical_attempt` /
RawDevice 证据和目标应用收到的可信操作系统事件；命令行辅助可以存在，但
不能替代被明确要求的界面动作。

`status=done` 只表示候选完成。没有第 8 条时，报告必须是 `ungrounded`，不能是 `passed`。

## 聊天与桌面租约生命周期

前端聊天不是 GUI task 的宽松变体。真实聊天 E2E 必须用同样的操作系统键盘和真实
鼠标发送，但通过条件是：

- 从点击发送到 direct reply 可见，Electron 始终 `visible && !iconic`；
- 没有 desktop lease、外部 GUI observation、`physical_attempt` 或伪造的 mission
  completion evidence；
- 前端显示模型原始 reply，而不是把 `claim_obligation_done` 包装成“已回复”。

需要桌面操作的同一入口则必须记录 run-bound lease：只有同一个 Actor 调用通用
`observe_desktop` 后才允许隐藏；capture 必须晚于 Electron yield ACK，首帧和任何
物理动作必须晚于 lease bind。matching success/fail/cancel 后，产品必须自行
`restore/show`；旧 run、旧 lease 和重复 terminal 都不得改变当前窗口。

`scripts/client_e2e_run.py` 必须先把 terminal 时的 `IsWindowVisible`、`IsIconic`、
foreground HWND 和窗口矩形写入 `client_terminal_before_harness_restore`，之后才可
为了截图调用 `restore_and_focus()`；正式生命周期 oracle 必须读取前者，不能让
harness 恢复掩盖产品缺陷。若产品未恢复，case 当场失败，不能先恢复再继续
`chat → GUI → chat`。`status=reply` 作为独立成功终态处理，不能映射为普通 failed
或视觉 verified done。
最后一轮还必须只凭该 thread 的有界上一轮交换回答 GUI 轮产生的随机值，防止把三个
互不知情的 run 误报为 Codex 式连续对话。

## Connector、模型协议与缓存

Connector 的组件测试至少覆盖：仅加载显式 MCP 配置、remote HTTP 与 stdio 两种
transport、环境变量替换、目录/参数/结果大小上限、取消和总 deadline，以及 stale
ref、descriptor 变化、未知 annotations、destructive annotations 全部 fail closed。
read-only descriptor 必须在同一物理 `call_tool` 前再次核对；不能只 mock Gateway 后
声称远端调用安全。正式报告还应持久化不含 credential/正文的 run-scoped physical
receipt（connector、tool fingerprint、arguments hash、result hash、status）；当前官方
GitHub E2E 报告尚缺这一项，因此 direct Gateway smoke 是补强证据，不是替代品。

真实客户端 connector E2E 仍从可见 Electron 输入目标并点击发送。同一个 Actor 的
trace 应显示 connector 与 reply/GUI 同轮可选，没有关键词 router；read 场景需用独立
远端/fixture 日志和随机 token 核对调用与结果。当前有效证据为：

- 20 个聊天 Turn `20/20`，报告
  `output/playwright/client-thread-connector-e2e/20260731-211729-thread-connector/report.json`；
  末尾额外 connector probe 不在该 `20/20` 内；
- 新鲜 Thread 的 GitHub-like read 与 connector→Notepad 外部 oracle `2/2`，报告
  `output/playwright/client-thread-connector-e2e/20260731-214024-thread-connector/report.json`；
  后者精确落盘时 run 尚在 `running`，不能把外部成功扩写为停止判断通过；
- GitHub 官方 remote 的 Gateway 物理
  `get_commit(owner=zoujiapeng, repo=lingdong-script, sha=master, detail=none)` receipt 为
  `succeeded`，返回值与独立 `gh api` 的
  `fba5d7bb6dcb13c46e93dbc29bc3cef44e560622` 一致；
- 首个中文 Electron run `arp-20260731-214538-485580` 的 before-submit 已把目标转为大量
  `?`，必须记为测试输入干扰，不能用其输出归因模型；
- ASCII 可见复验 `arp-20260731-220327-1ed4e4` 的提交文本完整，官方 remote 有真实
  会话/POST；五次模型响应后终态为 `state_rejected`，detail 是
  `Actor initial turn returned an unapproved tool`，回复为空、零 GUI 动作。这个干净
  task failed 的 usage 为 `8126` prompt、`539` completion、`4160` hit、`3966` miss；
  首个分歧是模型工具选择/停止，不是 connector transport。报告：
  `output/playwright/github-connector-e2e-ascii/20260731-220236/report.json`。

History-depth 对照必须额外满足：

- 两臂的 task、fixture config/source、输出路径与 Runtime/model identity 均由实测 hash
  比较，不能硬编码控制变量成立；
- fresh target 实际 provider history 为 0；history target 的 message count/chars/roles/hash
  必须从 Actor 随后真正放进 provider request 的 message list 旁路观测，不能用
  “20 个前端 Turn 约等于 6 轮模型历史”推算；
- 每个 history chat 都必须证明 raw connector call、Gateway call、desktop lease、GUI
  observation/action 和 Runtime tool attempt 全为 0；否则它预热了被测通路，整臂无效；
- arm 顺序按 seed 随机，并在多个 seed 中做 AB/BA 对消；单对只叫 pilot，不能报告
  history 因果效果；
- target 必须等 Agent 自主 terminal，文件内容严格相等，产品窗口须在 harness 恢复前
  已可见且非最小化。

首个完整 pilot 位于
`output/playwright/history-depth-ab/20260731-230951-history-depth-ab/report.json`。fresh
与 20-turn 两臂都失败且失败层不同；旧报告另有 failed-target Thread ID 投影错误，实际
Thread 不同，因此不得写成 history regression。fresh 诊断
`arp-20260731-232655-3f7c7a` 显示 connector 与 13 笔 GUI 物理动作均正确，但 Windows
文件名编辑框成为 `*.txtC:\Users\...`，模型在两次“文件名无效”后仍重复策略；这不是
Runtime 参数或输入交付分歧。测试侧实际 history/零副作用 probe 的可见 Electron 预检
`arp-20260731-235249-105256` 已通过。两轮负向预检的第二轮
`arp-20260731-235909-f4d259` 实际观测到 2 条/48 字符的 `user,assistant` history，但模型
为纯文本回忆申请 desktop lease 和一次 GUI observation，因此 text-only gate 必须失败；
这证明 gate 不能用最终回复 passed 替代。反查旧正式 B 的 20 个 prefill run，其 raw/
Gateway connector、lease、observation、tool attempt 和 GUI action 均为 0。聚焦回归
`34 passed`。下一次正式矩阵应改用预先存在且已打开的文档，避免 Save As 噪声，并至少
运行四个随机 seed 的 counterbalanced pairs。
当前发布前全量回归为 Python `3462 passed, 13 skipped, 1 warning`、renderer
`234 passed`，TypeScript typecheck 通过。

显式 MCP 配置最高优先级与按完整 server 配置 fingerprint 隔离目录缓存已有
`21 passed` 聚焦测试；后续回归仍需覆盖同名 server 多配置，防止再次读到错误目录。

OpenAI Responses、Anthropic Messages 与 Gemini `generateContent` 首先做 canonical
message/tool/image 的双向 fixture 测试，再分别用真实 credential 做 provider smoke；
最后仍需由可见 Electron 触发至少一个 direct reply，声称支持视觉的模型还需一个
需要截图的简单任务。配置必须显式选择 wire，测试也要证明只改变 `api.provider`、
模型名或 base URL 不会触发协议猜测，并验证 canonical `strict=true` 在 Anthropic
tool schema 中不丢失。当前三条原生线路没有真实-provider 证据，只能报告“provisional 协议
实现存在，外部兼容未验证”。审计后的相关 5 个测试文件为 `214 passed`，已覆盖
provider 元数据不切 wire 与 Anthropic `strict:true`；这个数字仍不能替代 credential
smoke 或可见 Electron E2E。

缓存测试不得用本地计数器伪造收益。仓库冻结 Legacy 确有 32 KiB 前 append-only
history 与 cache-friendly session prefix，但当前 vNext 不走该 owner。固定
provider/config 下运行重复稳定前缀的长 Thread A/B，只按 provider 返回的 usage 核对
`cache_hit_tokens` 与 `cache_creation_tokens`，并确认每步 `agent_usage` 和 run
`model_usage` 累计一致。上述 20-turn 报告累计 `21626` prompt、`13248` hit、`8378`
miss、`0` creation，证明真实 telemetry，不证明主动缓存控制。项目没有发送
`prompt_cache_key`、`cache_control` 或 `cachedContent`；未来若实验这些机制，必须逐项
A/B 成本、延迟和任务完成率，不能用扩大历史或重复截图换命中。

当前文档切片的常规回归为 Python `3458 passed, 13 skipped, 1 warning`、renderer `234 passed`
且 typecheck 通过；这些仍不能替代上面的外部 oracle。

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

当前 `client_e2e_run.py` 的 original/mixed 目录还不满足本节合同：通用 case
没有 `independent_verifier`；`max_steps` 只写入报告，不改变实际
`30 turns / 300 seconds / 60 model calls`；vNext persistent 判定仍依赖
Legacy 事件；四个 mixed 仍是拼接字符串。它们目前只能作为入口压力运行，不能
形成正式通过率，也不能把批量 `passed=false` 解释为产品任务失败。

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

`/api/status.running` 是聚合投影，不能单独作为终态 oracle。只要匹配 run 的
`current_run` 或 `runtime_jobs` 仍处于
`queued/waiting_resources/running/canceling`，即使顶层 `running=false` 且前端
重新出现“发送消息”，harness 也必须继续等待；否则该 run 记为测试干扰并排除，
不能计作产品成功或失败。

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
- connector 改动需一个显式配置 fixture E2E；真实服务支持声明还需对应 credential
  与服务端 oracle，不能由 fixture 代替；
- 模型 wire 改动需对应真实 provider smoke，缺少 credential 时必须明确记为未验证；
- 改变真实应用逻辑时，对应真实任务 E2E 或明确记录未验证；
- 失败恢复相关改动时，至少一个“新帧后重新判断且未隐式切辅助通道”的负向场景；
- GitHub Actions 通过。

单次成功不能换算成成功率。只有固定环境、固定协议、多 seed、多次重复和外部 ground truth 才能报告统计指标。
