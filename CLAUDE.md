# AI / Codex / 开发者接手入口

> 最后核对：2026-08-01。当前唯一产品分支是 `master`，本轮开始基线为
> `80ae593fb26335d5f1ff057a491ddf1a9830fc56`。13.20–13.23 已进入主线，本轮客户端与
> 6-Turn 结果见重置文档 13.24；不要用更早的提交说明覆盖 Thread/Turn、按需桌面租约、通用
> connector、history-depth 证据或 provisional native-provider 边界。

本文件是换 Codex 窗口后的第一入口。它保存当前决策和继续工作的边界，不替代
长期架构与测试规范。依次阅读：

1. `README.md`
2. `docs/architecture.md`
3. `docs/testing.md`
4. `docs/vnext-migration.md`
5. `docs/GUI_AGENT_ARCHITECTURE_RESET_2026-07-29.md`

## 最终目标

做一个面对无限、随机、组合、长时间 Windows 桌面任务的通用 Agent。它应像人一样
观察当前界面，自主选择鼠标、键盘以及未来可见的命令行/provider 工具，执行后检查
真实结果并继续。Krita、QQ、网易云、网页试卷和井字棋只是黑盒探针，不是五种任务
类型；任务空间不能被有限分类、应用 adapter、关键词 router 或 prompt 教学覆盖。

衡量标准是完整用户任务的真实端到端完成率，不是代码量、工具 `ok`、模型总结、
像素变化、单元测试数量或某个内部状态进入 `done`。

## 当前 Git / GitHub 状态

- GitHub 默认分支、当前活动分支和唯一长期产品分支均为 `master`。
- 2026-07-30 将原 `codex/lean-gui-agent-core` 的完整 HEAD 快进到 `master`；没有
  force push、历史改写、merge commit 或冻结分支整体合并。
- 原 Draft PR #45/#46 已由这次直接提升取代，不是合并依据。
- 其余具名分支全部只读冻结；只有当前 `master` 的真实 E2E 先证明能力缺口时，才可
  审查其单个候选提交。禁止继续向这些分支提交或把它们整体 merge 回来。
- 2026-08-01 已完成所有具名冻结分支只读总审计；当前没有可准入项，结论为
  `0 cherry-pick / 0 merge / 0 Runtime 修改`。旧分支剩余主要是重复 target pipeline、
  profiler、第二 verifier 和前端 Runtime selector，不解释当前首分歧。
- 后续正常开发直接提交到 `master`；不要再建立另一条长期产品分支。
- 本地未跟踪 `CON` 属于用户，不能读取后擅自解释、暂存、删除、改名或覆盖。

提升前的分支差异、恢复 PR 和审查依据保存在
`docs/GUI_AGENT_ARCHITECTURE_RESET_2026-07-29.md` 第 13.11/13.12 节。它们是历史
证据，不是新的分支工作流。

## 当前唯一产品主链

```text
Electron「智能代理」
  -> POST /api/v2/goals/runs { original goal, optional thread_id }
  -> Goal Admission（不接收执行模式）
  -> 一个 Turn / 一个 LiveVisualActor
  -> 原始 goal + 最多 12 条、16 KiB 的成功 Turn 原生历史
  -> 同一模型直接 reply，或搜索/调用显式只读 connector，或申请 observe_desktop
  -> 只有 observe_desktop 才建立 run-bound lease，Electron 随后让出前台并冻结 target
  -> 同一 Actor 继续读取权威 JPEG + 有界 task state + 最近一笔原生 tool exchange
  -> 模型选择一笔通用 GUI 工具调用
  -> CapabilityBroker 只投影机械可用工具
  -> AtomicDesktopAdapter 唯一一次坐标映射
  -> RawDevice / SendInput 真实鼠标键盘
  -> 新权威帧与回执
  -> 模型继续、claim 或 blocked
```

`/api/chat`、CLI、scheduler、FlowScript、`/task-contracts/compile`、客户端直接提交
`/arp/runs` 和 Legacy AgentRunner 仍是开发、Studio 或兼容面，不得成为产品 vNext 的
fallback，也不得冒充真实客户端 E2E。MCP 只通过当前 `ConnectorGateway` 进入同一
Actor：必须显式配置、延迟发现、只读注解通过、物理调用前重检 descriptor；冻结 Legacy
MCP 直调不是产品 connector owner。

前端执行约束选项及其 request/admission/Broker/evidence 状态链已经删除。用户口头说
“必须使用 GUI”时，只保留在原始 goal 中，由同一个模型理解。未来加入 shell 或
provider 后，它们必须与 GUI 工具同时对该模型可见；不能恢复前端开关、Task
Profiler、关键词预路由或另一个 planner。

## 当前角色与唯一 owner

| Owner | 负责 | 不负责 |
| --- | --- | --- |
| `LiveVisualActor` | 理解完整目标、看当前 JPEG、选择语义目标/精确坐标/下一动作、维护有界状态、决定结束 | 物理输入、应用脚本、隐藏策略 |
| Thread/Turn Store | 机械绑定连续成功 Turn、并发与有界历史 | 摘要、长期 memory、理解任务 |
| Desktop Lease | run-bound handoff、target 验证与窗口恢复状态机 | 判断是否需要桌面、选择目标语义 |
| `ConnectorGateway` | 显式 MCP 目录、只读授权、边界、指纹与 receipt | connector 路由、应用规则、远端写入 |
| `ProviderProtocolAdapter` | 显式 wire 翻译和原生签名/tool exchange 回放 | provider 猜测、重试、模型选择 |
| `MissionRuntime` | 驱动模型→动作→新帧的顺序循环 | 编译任务步骤、纠正模型答案 |
| `FrameAuthority` / workspace | 当前窗口、区域、frame id、epoch、JPEG | 解释画面 |
| `CapabilityBroker` | schema、当前可写性、帧/窗口/指针所有权 | 选择任务路线 |
| `AtomicDesktopAdapter` | 把提交图片坐标唯一映射到物理桌面并执行一次 | snap、offset、目标中心、重瞄 |
| `RawDevice` | 真实 SendInput、完成/不确定回执、按键释放清理 | 判断语义成功 |
| 外部 E2E oracle | 判断测试任务真实结果 | 向生产 Agent 暴露答案或坐标 |

不要新增 Grounder、Verifier、Profiler、Critic 或另一个 Planner 来修当前模型。
一个 Visual Actor 当前已经同时承担视觉理解、精确定位、短任务状态、schema 和停止
判断，压力确实较高；不要因此重新拆角色或增加消息传递，模型切换当前按用户要求
暂停。

## 动作面与上下文

当前 Turn 首轮保持零系统提示，只有三个短通用入口：

- `observe_desktop`
- `search_connector_tools`
- `call_connector_tool`（只执行发现过的 read-only 工具）

申请桌面后，同一 Actor 进入视觉线；视觉动作面保持：

- `click`
- `double_click`
- `drag`（可带折线路径和跨帧 `keep_holding`）
- `scroll`
- `type_text`
- `hotkey`（模型侧名称 `press_key`）
- `claim_obligation_done`
- `blocked`

显式只读 connector 的两个通用入口在视觉回合仍可见；它们没有变成 GUI 动作、应用
adapter 或第二执行线。

不要为启动软件、答题、绘画、保存、发 QQ、播放音乐、调音量、棋局或某个网站新增
宏工具。已有工具结构上不能表达的新通用能力，必须先由至少两个不同任务族的真实
E2E 证明。

Visual Actor 的视觉系统提示词保持 45 个英文单词；文本首轮不增加系统教学。Thread
只保留最多 12 条、16 KiB 的成功用户/模型原生消息；失败 Turn 不进入历史，服务进程
重启后不延续。视觉线只保留最近一笔 provider 原生
`user image -> assistant tool call -> tool result`，下一回合加入新 JPEG 后覆盖旧交换；
不保存无界聊天历史，不把长期 memory 当硬能力，也不在 prompt 中教学。
Actor 上下文目前还保留最多 64 项安全动作摘要；删除 A/B 未获准，因此暂留，但它
仍是模型上下文压力债务，不应继续扩张。

## 坐标、窗口和输入规则

- 模型坐标使用它实际收到的 `submitted-image` 精确像素，不是 `0..1000`。
- 当前提交图片最大边为 1120；物理区域可以是目标窗口或完整虚拟桌面。
- Runtime 只映射一次。验收只比较模型提交坐标和记录的物理参数是否满足同一个唯一
  变换；不得读取随机目标 bbox、按钮中心或答案来“纠正”模型。
- 若提交与物理执行一致但没命中，先归因模型视觉定位或观察质量，不能加全局 offset、
  snap、按钮吸附或软件坐标表。
- 当前 workspace 已支持同进程 modal、临时 popup、对话框消失后回 owner 窗口以及
  捕获期间窗口变化后的重捕获；不得为 Krita/Qt 写名称或坐标特判。
- 物理鼠标键盘保持全局单 writer；键盘 chord 必须释放，held pointer 在超时、失焦、
  取消和异常时必须 `mouse_up`。
- `type_text` 已用数字、ASCII、中文、emoji 和 `Ctrl+A` 覆盖做过同路径可见 GUI
  探针，逐码点一致。模型提交错文本时不要修改键盘基础工具。

## 推理、图片与模型边界

当前可复现基线：

- MiMo `mimo-v2.5` 原生接收 JPEG；
- `api.thinking=false`；
- Visual Actor `temperature=0.0`；
- 最大提交边 1120；
- 每个 vNext Visual Actor 决策最多一次 provider attempt。

默认 wire 仍是 OpenAI-compatible Chat Completions。只有显式
`api.protocol`/`api.api_style` 才启用 Responses、Anthropic Messages 或 Gemini
`generateContent`；`api.provider` 只是能力/usage/价格元数据，不能切 wire。三条 native
adapter 尚无真实 provider credential 联机与跨任务族 Electron E2E，只能 provisional；
未通过者应整条回退，不能继续堆兼容代码。

已经做过并回退的负优化：

- `thinking=true`：corridor 仍失败且更慢；
- 最大提交边 `1120 -> 1600`：更差；
- 移除 Visual Actor `temperature=0.0`：随机重复错误更多；
- `mimo-v2.5-pro`：当前官方接口不支持图片，不能作为截图主模型；
- 为 corridor、试卷、重复点击或边缘坐标加提示/规则：禁止，错误属于模型时会对更强
  模型形成负优化。
- 删除累计 `completed_action_summaries`：有效原生前端删除臂为 `5/10`，没有跨任务
  收益且低于当前保留态 `6/10`，生产删除已完整回退。`ordered-clicks` 仍通过，故
  这不证明摘要必需，只说明本次删除未达到保留门。
- 删除与原始 goal 重复的 `desired_state`、`acceptance_criteria` 和固定
  `required_capabilities`：严格交叉配对中 A 为 Explorer `0/2`、Notepad `2/2`，
  B 为 Explorer `0/2`、Notepad 原始外部结果 `1/2`。B 的 Notepad 缺字样本实际是
  物理输入回执与应用状态分歧，属于输入交付/应用边界的基础设施样本，具体根因待
  跨族复现，不能归因于字段删除；但 B 仍没有跨族完整成功率收益，`+0/-3` 候选已
  完整回退。

用户当前暂不考虑模型切换。`type_text` 跨 Notepad/Explorer 的 8 个冻结槽位均有
完整 proposal/canonical/submitted/physical 参数、完整 RawDevice 回执和稳定后帧，
没有字符丢失复现；真实完整任务为 `7/8`。唯一失败是小窗口只显示长文本后缀后，模型
重复重输且从未保存。探索性最大化对照一次完成并精确落盘，随后 provider 超时/断连
使 Runtime 误报失败。该结果不支持自动最大化、提示教学或输入 Runtime 修改。
`gpt-5.6-sol` 的最小 JPEG + tool transport smoke 使用现有本机 credential，在
模型响应前返回 HTTP 401 `AuthenticationError`；这是 credential/infrastructure
阻塞，不是模型 A/B；该路线按用户要求暂缓，不修改 Runtime。

## Pi Agent 结论

临时、被 Git 忽略的 Pi `0.83.0` spike 证明 provider 原生 tool transcript 能修复
有序多步状态，但不能修复模型算术。主线因此只实现最近一笔原生交换：

- 没有安装 `@earendil-works/pi-agent-core` 到主依赖；
- 没有 Node↔Python RPC；
- 没有第二个 runtime；
- 没有引入 Pi 的 steering、并行工具或更多策略。

只有当未来 Pi 能整体替换并删除 Python 模型循环 owner，且跨任务 E2E 明显提高时才
考虑引入。不能把 Pi 再包一层后与现有 loop 长期双写。

## 当前精确源码证据

最新可见 Electron 受控矩阵是 `5/10`：

| 通过 | 失败 |
| --- | --- |
| precise-click | answer-all-questions（4/6） |
| ordered-clicks | corridor-v0 |
| drag-fit | corridor-v1 |
| slider | game-drag-v2 |
| dynamic-hold | corridor-v3 |

通过条件包括用操作系统键盘向可见 Electron 输入原始目标、用鼠标点击发送、空
session、准确 run/trace 绑定、RawDevice 物理回执和独立浏览器 oracle。UIA 只负责
定位与读回。报告在被 Git 忽略的
`output/playwright/github-open-e2e-20260731/controlled-suite-r2/20260731-081832-suite/`。
同一源码的 WinPilot 全量回归为 `3377 passed, 13 skipped`。

新增的原生 Explorer 长任务也使用同一真实前端输入口径。生产对照
`arp-20260731-072140-0f4b81` 和三字段去重臂
`arp-20260731-073504-a24efb` 均绑定正确 Explorer HWND，分别有 29 次真实输入
全部 applied；外部文件系统 oracle 均失败。去重臂完成了前两个正确重命名，但
最终仍为 `center-7Q4M.txt.txt / north-7Q4M.txt / south-7Q4M.txt`，没有目录。
因此生产候选已回退，当前精确源码仍是矩阵全量回归时的 Runtime。
该 run 重启后显示“已中断”是独立持久化缺口：trace recorder 关闭后才发布外层
`arp_run_done`，恢复投影看不到 run 终态；它不解释任务外部失败，本轮未修。

随后完成的严格交叉配对共有 8 个有效 run：A 的 Explorer 两次均失败、Notepad
两次精确落盘；B 的 Explorer 两次均失败，Notepad 一次精确落盘，另一次 Runtime
提交和回执均为 14 字符 `E2E-ALPHA-N1X7`，但可见窗口与磁盘只有
`E2-ALPHA-N1X7`。因此该缺字首先属于输入交付/应用边界，具体根因待跨族复现；
删除臂仍未达到保留门。
长 goal 的 UIA `ValuePattern` 只返回首个视觉行时，runner 现在用只读
`TextPattern` 核对完整文档；无效的提交前样本被排除，8 个有效 run 均从前台
Electron 用操作系统键盘输入并以真实鼠标发送。

13.18 的新交付复验进一步得到 `type_text` delivery `8/8`、外部任务 `7/8`；
13.17 的缺字没有跨族复现，当前输入实现不变。P4 Notepad 的小窗口失败发生在首次
精确写入内存后：水平视口只露出后缀，模型连续重输而没有保存。相同 token 的最大化
探索对照四个动作即精确落盘，但保存后的 provider `APITimeoutError` 和
`APIConnectionError` 阻止了 Runtime 正常结束。另有 P3 Notepad 首次 `Ctrl+A`
和 P4 Explorer 首次 `Enter` 回执完整但稳定外部状态未生效，可进入下一轮通用按键
消费边界的测试型 A/B；不得添加重复按键规则。

13.20 已把 13.19 的失败候选落成当前产品合同。主入口现在只提交 `goal` 与可选
`thread_id`；同一个 `LiveVisualActor` 在零系统提示首轮直接 `reply` 或调用唯一
`observe_desktop`。只有后者才发布 run-bound lease，Electron 此时才隐藏并交付
精确 target，matching terminal 自行恢复。没有 router、第二角色、旧 `/api/chat`
或应用 prompt。服务端 Thread/Turn 只保存最近 12 条、16 KiB 的成功用户可见原生
交换；失败 Turn 不进入历史，当前仍是进程内存状态。

当前源码的真实可见 Electron 验收在同一 Thread 连续完成 7 个 direct reply 和一个
Notepad 桌面 Turn：能跨轮回答 `95-B`，桌面轮在租约后用
`Ctrl+A → type_text → Ctrl+S` 把外部文件精确保存为 `DESKTOP-c419bb42`，产品自行
恢复；恢复后的下一轮又只凭历史精确回复该 token，lease idle、零物理动作。两次
模型 transport `Connection error` 经普通用户原样重发恢复，失败 Turn 未污染历史，
归为 provider/基础设施干扰，不增加 Runtime 自动重试。桌面结果通过，但其完成文本
比“只回复已完成”更长，是模型格式偏差。生产切片实际为 `+1372/-155`、净增 1217
行；成本显著高于 13.19 估计，主要来自 Thread/Turn、租约竞态与跨进程生命周期。
最终 Python 全量 `3388 passed, 13 skipped`，renderer `234 passed` 且 typecheck
通过。完整合同、证据、保留/回退和下一实验见架构重置文档 13.20。

13.21 在同一 Actor 上加入两个短 generic connector 工具和显式 native-provider wire
边界，没有增加 router、第二角色或服务专属 prompt。只读 `ConnectorGateway` 的通用
fixture exact-read 与 connector→Notepad 外部结果为 `2/2`；20 个同 Thread 聊天 Turn
为 `20/20`，第 20 轮精确回忆第 18 轮随机 marker。官方 GitHub Gateway 物理
`get_commit(master)` 返回独立 `gh api` 核对的同一 SHA；中文可见 run 因 goal 在提交前
变成大量 `?` 只算测试干扰。完整 ASCII run 则在 5 次模型响应后返回未批准工具并
`state_rejected`，是模型工具选择/停止失败，不增加 formatter 或 Runtime 修正。

冻结 Legacy 确有 32 KiB 前 append-only 的 DeepSeek stable-prefix 布局，当前 vNext 不走
它，也不发送 `prompt_cache_key`、`cache_control` 或 `cachedContent`。真实 20-turn usage
为 `21626` prompt、`13248` cache hit、`8378` miss，证明上游自动命中与 telemetry 生效，
不证明项目主动创建缓存。Responses、Anthropic、Gemini wire 仅显式启用且仍 provisional；
审计发现的 provider 误切 wire 与 Anthropic `strict:true` 丢失已修复，但三家没有真实
credential E2E。Operit 只做 LGPL-3.0 源码审计和 clean-room 设计参考，没有逐字搬运或
引入 Android runtime；Pi 计划不变。

13.21 生产切片为 `+2207/-114`；连同 13.20，当前完整生产工作树相对 HEAD 为
`+3579/-269`。最终验证为 Python `3458 passed, 13 skipped, 1 warning`、renderer
`234 passed`、TypeScript typecheck 通过。完整外部证据、失败分层和唯一下一实验见
重置文档 13.21。

13.22 已执行 strict history-depth pilot，生产代码 `+0/-0`。相同 fixture/token/task/path
的 fresh 第 1 轮与前端 20 Turn 后第 21 轮都失败，但首个分歧不同：fresh 样本在 Save
动作前出现未复现的 `target_identity_changed`；history 样本 connector 链正确执行两次
后零 GUI 动作，以两次非法 visual proposal 结束。因此单对不能归因 history。新鲜诊断
`arp-20260731-232655-3f7c7a` 又证明 connector/raw/canonical/physical 一致、13 笔 GUI
动作 applied；Windows 文件名编辑框最终显示 `*.txtC:\Users\...`，模型两次看到“文件名
无效”仍重复策略，故不改 Runtime、不加 Windows/Save As 规则。

paired runner 已在测试侧继续收紧：随机 AB/BA 顺序、实测 task/config/source/path hash、
实际 provider history message count/chars/roles/hash、20 个 history chat 的零 connector/
零 lease/零 GUI oracle、严格文件与产品恢复判定。旧完整 report 的 `separate_threads=false`
是失败 target Thread ID 投影错误，实际两 Thread 不同；它和更早的 Vite/Runtime-ready/
sidecar import 失败均属测试干扰。一次性 target-identity probe 已删除；通用参数链与实际
history probe 保留。聚焦测试 `34 passed`，单轮可见 Electron 预检
`arp-20260731-235249-105256` 观察到首轮 history=0 且三类副作用全为 0。完整证据与下一
实验见重置文档 13.22。两轮负向预检的第二轮 `arp-20260731-235909-f4d259` 虽精确回忆
marker，实际 history=2 条/48 字符，却不必要地申请 desktop lease 并观察一次 GUI；新
text-only gate 正确拒绝它。反查旧正式 B 的 20 个聊天 run，则全部为零 connector、lease、
observation、tool attempt 和 GUI action，因此旧 prefill 未实际污染桌面。
发布前完整回归为 Python `3462 passed, 13 skipped, 1 warning`、renderer `234 passed`，
TypeScript typecheck 通过。

13.23 已把 history-depth target 改成 runner 临时目录内预先打开的已有随机文件，任务
不再包含路径或经过 Save As。每臂重新写同一初值，并以 fresh HWND、标题文件名和
提交前磁盘 SHA 证明目标；
cleanup 作为独立测试有效性，不能覆盖用户外部终态或被误报成模型退化。稳定复用现有
Vite 后，fresh target 连续 `3/3` 完整通过：每次 connector raw→canonical→fixture 为
`1/1`，文件精确等于随机值，GUI 均为 `hotkey → type_text → hotkey`，自主 done、窗口
恢复和清理均通过。

四个 20-Turn ABBA 尝试没有形成一个完整 pair：history 路径分别在第 12 轮误用桌面
（模型）、第 11/第 1 轮模型 provider/API `Connection error`（基础设施）、第 6 轮 UIA
`COMError`（测试干扰）中止；另一次 fresh 被 Vite 探活干扰挡住。controls 未齐时报告
现在只写 `pair_incomplete_before_paired_target`，不能归因 history。生产代码仍为 `+0/-0`，
不加重试、摘要、router、提示或 Windows 快捷键规则。完整报告、旧 comparison 字段债务
和冻结分支审计见 13.23。最终回归为 Python `3464 passed, 13 skipped, 1 warning`、
renderer `234 passed`、TypeScript typecheck 通过；renderer 与 typecheck 并行时的一次
5 秒超时经串行原样重跑消失，属于测试调度干扰。

13.24 首次得到两个有效的 6-Turn history pair。`92002`（F→H）与 `92003`（H→F）中，
每臂的六个聊天 Turn 均为真实前端提交、非空成功回复、零 connector、零 lease、零观察、
零 GUI；target 前实际 history 均精确为 12 条 `user/assistant` 交替消息。两个 pair 的
fresh/history target 共 `4/4` 外部通过，因此当前没有 history-depth 负信号。`92001`
和 `92004` 无效：两次 fresh 首轮都提交多个 desktop observation；一个普通寒暄和一个
“陪我聊两句”样本不必要地申请 lease/观察桌面，均是模型模态选择，不准许用关键词
router、提示教学或客户端意图分类修正。

本轮只改现有 Electron 聊天 owner：中文 IME 组合输入 Enter 不再误发送；成功提交和
“新建对话”清除旧 `arp-last-goal`，避免重启后把已发送任务伪装成草稿；失败卡与任务栏
复用同一通用 outcome classifier，并显示原始失败结果；“再次编辑”只把原目标放回输入框，
不自动重试或产生外部副作用。只有明确的 vnext/no-legacy 直接聊天正文不按 JSON 结果协议
猜测，原有 legacy 安全停止分类保持不变；过渡状态也不再误画成失败。失败草稿可跨旧 run
水合恢复，提交期间不能再切换 Thread 或覆盖草稿。可见失败截图证明当前源码显示精确模型
合同错误、再次编辑与详情入口。生产切片为 `+70/-26`，没有 Runtime、系统提示、模型角色、
工具面或窗口 lease 变化；
history A/B 默认缩到 6 Turn，并要求精确 12 条实际 provider history。最终回归为 Python
`3464 passed, 13 skipped, 1 warning`、renderer `243 passed`、TypeScript typecheck 通过。

关键归因：

- `ordered-clicks` 在加入最近一笔原生交换前会重复第一个目标；现在三次真实点击按
  `[1,2,3]` 通过。这是结构性上下文修复，应保留。
- 本次试卷只有 4/6；前次同源码曾取得 6/6，也曾两次提交 `81`。Runtime 未变化，
  因此当前证据是模型样本波动，不是算术修复。
- 四个 corridor 中，模型提交折线与 `physical_attempt` 逐点一致；真实浏览器记录
  可信 pointermove，但模型路径切角或穿出通道，最小余量为
  `-24/-69/-12/-9`。这是视觉几何问题。
- Explorer 对照与去重臂的模型参数、canonical 参数和物理回执也一致；失败分别是
  重命名时扩展名保留状态理解、自我纠错耗散和最终参数类型错误，属于模型层。对照首轮
  的连接错误已恢复，只少一个可用动作，不足以解释两次完整任务失败。

五项原始长任务在当前精确源码上没有 5/5：

1. Krita 手机探针只完成 1080×1920 画布和粉红矩形轮廓；完整绘画、保存和 QQ
   发送未完成，外部发送还需动作时确认。
2. 当前受控试卷样本只有 4/6。
3. 井字棋 30 次真实输入后没有红 X；模型停留在颜色控件。
4. 网易云梁静茹歌曲已播放，但应用可见音量约 2%，CoreAudio 主音量为 0% 且静音，
   均不是 5%。
5. QQ 群摘要已进入至少一个群，但没有跨群覆盖或可信汇总，30 turn 耗尽；oracle
   同时存在把已进入群误报为 0 的测试债务。

不得把历史人工确认、部分动作成功或受控靶场通过写成这五项已完成。

本轮另外得到四项当前源码人工外部核对通过：Notepad 文件精确落盘、网易云搜索
周杰伦并播放第一首、抖音搜索 `kimi k3`、Wireshark 打开最近捕获并交付协议摘要。
Paint 画花、能力演示和 OneNote→Word 失败；“那你干嘛”虽然被识别为非任务，
模型仍做了一次无意义画布点击。完整明细见架构重置文档 13.16。
QQ 空间点赞从 QQ 主窗口成功接管浏览窗口，30 次物理动作全部 applied；至少三处
灰色赞变为蓝色，但终态仍至少有两条当前可见说说未赞，属于模型覆盖、自纠错和
停止失败，不是窗口或输入基础设施失败。

## 代码与架构准入规则

1. 优先修已有基础设施、复用成熟实现或净删除；不要先加层。
2. 一轮变更若增加数百行生产代码，应先证明现有 owner 结构上无法修复；数千/数万行
   的架构扩张在真实 E2E 通过前不接受。
3. 不为当前模型的精度、算术、几何或停止错误添加“通用外观”的提示和约束。
4. 不为有限软件集合做适配；适配再多应用也不能覆盖无限任务。
5. 隐私、安全和证明层只保留真实机械边界：当前帧/窗口身份、物理输入所有权、凭据
   不泄漏、不可逆外部动作边界。不要让重复 policy/ledger/guard 改写用户目标或阻断
   合法动作。
6. 新能力必须说明唯一 owner、通用输入/输出、失败语义、被删除的旧 owner，以及至少
   两个不同任务族的外部-oracle E2E。
7. 失败先比较：模型原始参数 → canonical 参数 → physical_attempt → OS/应用终态。
   证据在哪一层第一次分叉，就只修那一层。
8. 不把旧结果当本轮结果；每次改运行时代码后重载 Electron 并重跑当前源码。
9. 不把全量 pytest、直接 API、模型 claim 或 runtime done 当用户任务完成。
10. 不与真实 GUI E2E 并发跑重型全量测试；曾经因此让 `/api/status` 超时，属于测试
    调度干扰而非模型失败。

## 剩余冻结分支审计门

1. 所有新工作只在 `master`；冻结分支不再演化。
2. 先在当前 `master` 构造随机失败样本，再审查对应旧提交。
3. 只提取最小能力，不 cherry-pick 旧 planner、prompt、state 或重复 owner。
4. 至少两个任务族 E2E 有实质收益且代码规模合理才保留；无收益立即丢弃。
5. 同一提交删除被替代实现，禁止长期双写。
6. 不为“清理分支”整体 merge 冻结历史；审计完的分支直接删除。

## 新 Codex 窗口的第一组操作

```powershell
git switch master
git pull --ff-only
git status -sb
git log -8 --oneline --decorate
```

本轮开始时 `master` 与 `origin/master` 同为 `80ae593`，除用户未跟踪的 `CON` 外干净；
13.24 改现有 Electron 聊天 owner、测试 runner、测试与文档。接手时必须重新核对状态，不要 reset、整体暂存或
触碰 `CON`。然后阅读本文件和上述五份文档，不要立刻追加代码。

优先级：

1. 下一轮唯一实验先不改生产代码：通过真实前端建立带随机 marker 的成功 Thread，正常
   关闭并重启 Electron 及其托管服务，再检查旧消息是否可见、继续发送是否仍绑定原 Thread、
   模型能否只凭恢复历史精确回答 marker。当前内存 Thread Store 与本地 stale thread id
   预计会在这一边界分叉，但必须先取得可见证据，不能直接增加持久化 owner。
2. 若正常重启首先在客户端/Thread persistence 层分叉，再设计一个私有、有限、可删除旧
   owner 的最小持久化候选，并用聊天连续性与重启后的简单桌面任务两个家族验证；不得通过
   自动创建新 Thread 假装上下文仍存在，也不得解除公共 trace 正文脱敏。
3. 继续记录 raw model args、实际 canonical parse、物理 MCP/GUI receipt、严格外部终态、
   产品自主 terminal/恢复与 usage；不加提示教学、router、摘要模型、工具筛选器、自动
   重试或 Runtime 修正，不接回旧 `/api/chat`。
4. native provider 只有拿到真实 credential 后才做相同 canonical 多轮工具序列与至少
   两个任务族 Electron E2E；未通过的协议整条回退，不继续扩代码。
5. 模型切换当前暂停；冻结分支只作只读审计，保持 `master` 为唯一产品主线。

常用验证：

```powershell
python -m pytest python/winpilot_home/tests -q
python scripts/client_visual_e2e_run.py --suite all --timeout 600
python scripts/client_e2e_run.py --suite original
python scripts/client_e2e_run.py --suite mixed
```

真实客户端 E2E 前先运行 `npm run dev`。不要直接运行
`uvicorn winpilot.server:app`；支持的入口是 Electron 托管或
`python/winpilot_home/serve.py`。
