# AI / Codex / 开发者接手入口

> 最后核对：2026-08-02。当前唯一产品分支是 `master`。本文件不再把某个提交写成
> “当前 HEAD”；接手时必须现场运行 `git rev-parse HEAD`、`git rev-parse origin/master`
> 并查询该精确 SHA 的 Actions。13.20–13.26 已进入或正在进入主线；不要用更早的提交说明覆盖
> Thread/Turn、按需桌面租约、通用 connector、history-depth 证据或 provisional
> native-provider 边界。

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
- 2026-08-01 已完成所有具名冻结分支只读总审计；当前没有可准入项，结论为
  `0 cherry-pick / 0 merge / 0 Runtime 修改`。旧分支剩余主要是重复 target pipeline、
  profiler、第二 verifier 和前端 Runtime selector，不解释当前首分歧。
- 审计当时，本地 head、`origin` 远端 head、GitHub branches API 和默认分支四处都只剩
  `master`，且只有一个 worktree、零开放 PR。2026-08-02 再次 fetch 后出现只改两份二进制
  icon、从旧基线分叉的 `origin/agent/update-app-icon`；它不是产品主线，本轮只作只读审计输入，
  未合并也未删除，必须另做可见图标验收后才能归档收口。PR10 历史由
  `archive/pr10-vnext-agent-core-20260726` 保存，PR15 证据由
  `evidence/pr15-legacy-electron-oracle-20260726` 保存；这些 tag 只作只读恢复输入，
  不得继续开发或整体 merge。
- 两条本地 recovery 独有 tip 已分别归档为
  `archive/c4-canonical-from-d418-local-20260801` 与
  `archive/c4-fa2e9caf-local-20260801`，随后删除所有剩余本地历史分支；tag 不是开发分支。
- `4f93672` 的 run `30663041917` 曾因账户 billing/spending limit 在 Python 与 frontend
  job 均为零步骤时终止；它只能作为历史 infrastructure-blocked 记录。
- `65fdce0` 的 GitHub Actions run `30688550071` 随后实际执行全部步骤，frontend 与 Python
  job 均为 `success`。绿色结论只绑定该精确提交；接手时仍须重新查询实时 HEAD，不能用它
  覆盖未来提交。
- 本轮改动前的 `fd53fb887960ce77ebe4b1e7e104ee8c3af425e1` 由 Actions run
  `30698655302` 实际完成 frontend 与 Python 两项并均为 `success`；本轮新提交仍须等待
  自己的检查，不能继承该结论。
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

## Replay / 离线流程现状

当前已用可见 Electron 和双窗口外部 oracle 完整通过：人类示教鼠标键盘 -> 每步
`target_window` 的 WPS -> canonical Automation IR -> Studio 编辑/验证/运行 -> provisional
memory -> 真实 replay 晋级 -> 0-token 复用。Studio 的新建、打开、保存、撤销、重做和
本地结构检查不需要网络或模型；确定性执行需要本机 Python Runtime、Replayer 与 Desktop
Action Queue，但不需要联网。不得再造 JS 离线执行器或把浏览器模拟当成离线执行。

当前对话可以按 `thread_id` 聚合导出：导出器扫描该 Thread 的全部 durable trace，不受前端
最近 40 条 run 投影限制；前端携带的有界 run 列表只用于完整性核对。导出器从 vNext
`physical_attempt` / `raw_receipt` 和兼容 trace 中提取普通 ARP 动作，保留独立重复动作、
每步目标进程、完整 trace 读取与逐 run 完整性账本；公开 trace 详情仍保持有界。用户先看到
`complete/partial`、动作数和遗漏提示，确认后只把 Automation IR 加载进 Studio，不会自动
运行。2026-08-02 可见 Electron 两轮 Thread 导出得到 14 个动作，载入 Studio 后离线 replay
产生 0 次模型调用；独立剪贴板 oracle 精确得到
`ARP-THREAD-A-20260802-0329ARP-THREAD-A-20260802-0329-B-20260802-0333`，SHA-256 为
`934b79c77eb2d6120be63b8a47141c902f343affd49a14e393cc7629d755230b`。重复的 A 是模型参数/
目标选择叠加测试过程中重新激活 Electron 的干扰；canonical 参数与 `physical_attempt` 一致，
Runtime 没有背离，但原始第二轮口头任务不能据此称为干净完成。覆盖声明固定为
`ordinary_arp_actions`。只读 connector 查询不是普通 ARP 动作，未来若加入有副作用的
shell/provider 工具，必须先形成显式 unsupported trace，不能静默冒充“整场对话全部行为
已重放”。
重启当前源码后又从可见 Electron 完成了一次同路径发布验收：默认约
`902 x 582` 窗口确认 partial 提示、加载 16 节点并点击运行，
`arp-20260802-065621-dd12f0` 全部完成、`trace_integrity=complete`、模型调用为 0。
属性面板与 Runtime preflight 都可见使用匹配的 `target_window.title + process`；
独立剪贴板 oracle 再次读到 68 字符和上述同一 SHA-256。

这还不能称为“整场对话的所有普通 ARP 动作都已可导出”。跨 decision 的连续 held gesture
带有 `started_held` / `keep_holding`，当前会被导出器拒绝并得到 0 步；缺少 durable
run -> Thread owner index 时，归属和完整性也只能靠全量 trace 扫描证明。这两项是基础设施
缺口。一次轻量 owner scan 曾把本机扫描从约 14 秒降到 1.36 秒，但存在漏掉归属/完整性异常、
把 partial 误报为 complete 的风险，已完整回退；当前保留 correctness-first 的约 14 秒全量
扫描。不能为提速恢复第二 planner、摘要模型或放宽完整性门。

Studio 另有显式“一句话生成片段”辅助：短生命周期的 `FlowFragmentProposer` 只复用现有
provider/model/protocol/budget plumbing，不创建第二个 `LiveVisualActor`，也不把图编排职责
塞进主 Actor。它只在用户主动提交时进行一次 opt-in 调用；provider 消息为一个 user JSON、
零 system/developer message，不观察桌面、不读当前 Thread、不取得 connector 或执行权限。
结果必须经过结构/fingerprint 校验和参数预览，用户确认后才作为一个 undo checkpoint 插入
右侧空白区；不自动连入主图，也不自动运行。可见 Electron 已把“点击坐标 420,300，等待
1 秒，然后输入文本 hello”生成 `click -> wait -> input_text` 三节点片段，应用后 `Ctrl+Z`
恢复原来的 16 节点/15 连线/14 动作。它是在线编辑器辅助调用，不属于完全离线节点执行，
也不改变产品任务链或默认提示词。旧 `src/ai-interface`、DSL 生成器和 Electron HTTP 直接
写图链包含第二模型、长提示教学、关键词路由或无确认突变，不得接回。权威边界与逐节点证据
见 `docs/replay-recording.md`、`docs/flow-editor.md`、`docs/offline-flow-node-e2e.md`。

默认约 `900 x 580` 的 Electron 小窗口现已用可见用户路径复测：流程工作台使用左右抽屉，
片段弹窗按画布居中且 `Esc` 不再关闭整个工作台，页脚操作在最小高度可达。口语“等半秒，
然后按一下回车”生成 `wait(0.5) -> keyboard(enter)`，应用后可一次 undo/redo 整体回退和
恢复。属性面板同时删去或封住不能精确编译的假参数，结构化 evidence 条件不再被改成
`[object Object]`。正式 `screenshot` 现在同时支持受管理的全屏与物理屏幕区域工件；图片模板
节点仍未因此开放，必须单独取得 canonical 编译、物理执行和外部业务 oracle。

严格的离线节点独立矩阵当前为 `2/19`，只计 `click` 与 `screenshot` 两个 DUT。`click` 的
既有 run `arp-20260802-052309-82cf06` 仍由 Runtime 外部 JSON 证明 `click_count: 0 -> 1`。
2026-08-02 又从可见 Electron Studio 完成 `screenshot.region`：用户先用 `Esc` 取消一次框选，
原参数未改变且没有新 run；随后在 125% DPI 屏幕直接框选，属性面板保存物理区域
`x=16,y=130,w=934,h=182`，并从前端运行
`start -> focus_window -> click -> screenshot -> end`。run
`arp-20260802-130804-ecc3bd` 的节点全部 `ok`、`trace_integrity=complete`、模型调用为 0。
独立 `mss` 按同一区域重抓后的 RGB 像素 SHA-256 与受管工件完全相同：
`57d2bf67fcb8f14488b882a32864b09136a069ae96857ac98bbcaa7908ade743`；工件文件 SHA 也与
manifest/evidence 一致。另一次框选真实覆盖任务栏所在的 work-area 外区域，证明 overlay 没有
被 Windows 工作区裁短。当前机器只有一块 1920x1080、125% 屏幕，第二显示器和负坐标只能
记为 `infrastructure_unavailable/not_run`，不能用合成测试冒充 E 级证据。以上没有增加应用
配方、提示教学或模型职责：产品仍是一个原始 goal、一个 `LiveVisualActor` 和一条视觉执行线；
`FlowFragmentProposer` 仍只是隔离的编辑器辅助。

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
只保留最多 12 条、16 KiB 的成功用户/模型原生消息；失败 Turn 不进入历史。现有
`AgentThreadStore` 还把最多 100 个 Thread 的这个有界成功投影原子写入私有 checkpoint，
服务重启后恢复同一 `thread_id`、可见成功结果与原生消息；它不保存 trace 正文、失败 Turn、
视觉帧或长期摘要。视觉线只保留最近一笔 provider 原生
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

最新可见 Electron 受控矩阵是 `6/10`：

| 通过 | 失败 |
| --- | --- |
| answer-all-questions（6/6） | corridor-v0 |
| precise-click | corridor-v1 |
| ordered-clicks | game-drag-v2 |
| drag-fit | corridor-v3 |
| slider | — |
| dynamic-hold | — |

通过条件包括用操作系统键盘向可见 Electron 输入原始目标、用鼠标点击发送、空
session、准确 run/trace 绑定、RawDevice 物理回执和独立浏览器 oracle。UIA 只负责
定位与读回。报告在被 Git 忽略的
`output/playwright/client-visual-e2e/20260801-183951-suite/suite-report.json`。
同一工作树的完整回归为 WinPilot `3478 passed, 13 skipped, 1 warning`、根测试
`2508 passed`、renderer `244 passed`，typecheck、renderer/Electron build 与 CLI/AI build
均通过；它们证明回归合同，不替代外部任务 oracle。

新增的原生 Explorer 长任务也使用同一真实前端输入口径。生产对照
`arp-20260731-072140-0f4b81` 和三字段去重臂
`arp-20260731-073504-a24efb` 均绑定正确 Explorer HWND，分别有 29 次真实输入
全部 applied；外部文件系统 oracle 均失败。去重臂完成了前两个正确重命名，但
最终仍为 `center-7Q4M.txt.txt / north-7Q4M.txt / south-7Q4M.txt`，没有目录。
因此生产候选已回退，当前精确源码仍是矩阵全量回归时的 Runtime。
该历史 run 重启后曾显示“已中断”，是当时独立的持久化缺口，不解释任务外部失败。
13.26 已用有界私有 Thread checkpoint 修复并通过正常关闭/全新服务进程的两族复验；
公共 trace 仍保持脱敏，不能用恢复投影冒充任务成功。

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
交换；失败 Turn 不进入历史。13.20 当时仍是进程内存状态，13.26 已增加同一 owner 的
有界私有 continuation checkpoint。

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

## 历史引用恢复与候选准入门

1. 所有新工作只在 `master`；归档 tag 与历史提交不再演化。
2. 先在当前 `master` 构造随机失败样本，再审查对应归档提交。
3. 只提取最小能力，不 cherry-pick 旧 planner、prompt、state 或重复 owner。
4. 至少两个任务族 E2E 有实质收益且代码规模合理才保留；无收益立即丢弃。
5. 同一提交删除被替代实现，禁止长期双写。
6. 不为“恢复历史”整体 merge 归档树；只提取通过准入门的最小候选。

## 新 Codex 窗口的第一组操作

```powershell
git switch master
git pull --ff-only
git status -sb
git log -8 --oneline --decorate
```

接手时必须重新核对 `master`、`origin/master`、GitHub 默认分支、branches API、worktree 与
精确 SHA 的 Actions，不要 reset、整体暂存或触碰用户未跟踪的 `CON`。然后阅读本文件和上述
五份文档，不要因为看到“继续”就立刻追加代码。

下一轮唯一最高价值实验：不改生产代码，先从可见 Studio 独立验收 canonical `input_text`。
用户在属性面板输入当轮随机的 ASCII、中文、emoji 与换行组合，运行
`start -> focus_window -> input_text -> end`，由 Runtime 外部 JSON/文件 oracle 精确核对完整
Unicode 字符串、长度与 SHA；目标/焦点缺失必须 fail closed，空串行为必须明确且不能静默
变成另一种动作。只有首分歧落在产品基础设施时才修改对应 owner；不能为字符内容增加答案
修正、软件 prompt 或键盘重试规则。
图片参数虽然可以复用本轮唯一选区 owner，但历史 `image_click` / `mouse_operation.image` 仍不
开放；跨 decision held gesture 也继续作为已知重放缺口，不与该实验并行修改。

期间继续记录 raw model args、实际 canonical parse、物理 MCP/GUI receipt、严格外部终态、
产品自主 terminal/恢复与 usage；不加提示教学、router、摘要模型、工具筛选器、自动重试或
Runtime 修正，不接回旧 `/api/chat`。native provider 只有拿到真实 credential 后才做相同
canonical 多轮工具序列与至少两个任务族 Electron E2E；未通过的协议整条回退。模型切换
当前暂停；归档 tag 只作只读恢复输入，保持 `master` 为唯一产品主线。

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
