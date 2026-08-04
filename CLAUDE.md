# AI / Codex / 开发者接手入口

> 最后核对：2026-08-04。当前唯一产品分支是 `master`。本文件不再把某个提交写成
> “当前 HEAD”；接手时必须现场运行 `git rev-parse HEAD`、`git rev-parse origin/master`
> 并查询该精确 SHA 的 Actions。13.20–13.28 已进入或正在进入主线；不要用更早的提交说明覆盖
> Thread/Turn、按需桌面租约、通用 connector、history-depth 证据或 provisional
> native-provider 边界。

本文件是换 Codex 窗口后的第一入口。它保存当前决策和继续工作的边界，不替代
长期架构与测试规范。依次阅读：

1. `README.md`
2. `docs/project-guide/README.md`（全项目架构、流程、合同和零遗漏逐文件索引）
3. `docs/architecture.md`
4. `docs/testing.md`
5. `docs/vnext-migration.md`
6. `docs/GUI_AGENT_ARCHITECTURE_RESET_2026-07-29.md`

## 最终目标

做一个面对无限、随机、组合、长时间 Windows 桌面任务的通用 Agent。它应像人一样
观察当前界面，自主选择鼠标、键盘以及未来可见的命令行/provider 工具，执行后检查
真实结果并继续。Krita、QQ、网易云、网页试卷和井字棋只是黑盒探针，不是五种任务
类型；任务空间不能被有限分类、应用 adapter、关键词 router 或 prompt 教学覆盖。

衡量标准是完整用户任务的真实端到端完成率，不是代码量、工具 `ok`、模型总结、
像素变化、单元测试数量或某个内部状态进入 `done`。

## 2026-08-04 模型优先（Model-first）最高原则

模型就是系统本身：它理解目标、看真实屏幕、选目标和坐标、验证自己的结果、
决定何时完成、用自然语言汇报。运行时只负责给它真实桌面并执行它选的物理动作。
任何"听起来很完善"的层——安全契约、隐私门、证据链、验证通道、能力检查、
权限握手——如果唯一作用是拒绝或延迟模型已经正确做出的决定，就删除。

- **绝大部分靠模型，不靠硬编码规则。** 模型弱就换模型或接受结果；给当前模型
  加配方、分类器、提示教学、坐标偏移、吸附、第二验证器或多角色只会把错误
  固化到更强模型身上。
- **本仓库不建设、不要求独立结果证据（external oracle）机制。** 那套 E2E
  oracle 属另一项目；本仓库以真实任务完成和单元/集成测试守住机械层即可，
  不再为"可证明"堆证据链。
- **任务完成由模型在它看到的真实屏幕上自行判断。** 不再叠加独立完成验证器；
  模型 claim 的可靠性就是产品可靠性的一部分。
- **物理输入去往模型在屏幕上指的位置，不管那是哪个窗口。** 不得强制绑定窗口
  到前台，不得因"另一个窗口才应该拥有点击"而拒绝点击。
- 只保留模型自身无法守住的机械边界：物理输入单写入者、按键/指针释放清理、
  凭据不泄漏、不可逆外部动作的同意。

## 2026-08-04 安全设计清理计划（重点方向）

完整、更完善的安全系统由另一项目提供；本仓库任何自带的安全设计——选项、
守卫、审批门、权限握手、证据链——都会干扰那个项目对 Agent 的判断。全部
删除是本项目重点方向之一，按以下清单执行（删除时不保留"恒关闭"的死开关，
选项与机制一起消失）：

P0（会拦截 Agent 动作，优先删除）：

- `ARP_RUNS.decide_gate` 运行审批门（`server.py` 路由 +
  `orchestrator/runtime.py` 的 `decide_gate`、`approved/comment` 入参、
  `integrations/chatgpt_mcp.py` 的 gate hook）。
- `approve_mode` 审批选项（`config` + 前端控件）。
- legacy `agent/scope_guard.py`（`ensure_scope`/`take_scope_recovery`，
  含 `_native_point_scope_guard`）：随 legacy 包按消费者先行流程退役，
  vNext 主链不可达，不作为单独修复对象。

P1（默认关闭不拦截，从配置面删除选项，随后删除机制分支）：

- `no_mouse_steal` / `multi_input_cooperation` /
  `cooperative_cursor_tolerance_px`（`config` + `action/executor.py` 的
  `_no_mouse_steal` / `multi_input_cooperation_enabled` /
  `cooperative_input_guard` / `_cooperative_action_still_owned` 及
  `raw_device.py` / `desktop_queue.py` 的 cooperative 分支）。
- 对应测试 `test_multi_input_cooperation.py` 与相关用例随之删除。

P2（只记录不拦截，删除候选）：

- `MutationPermitAuthority` 每动作许可握手（`vnext/runtime/mutation_permits.py`
  及 `call_once_with_permit`、adapter 的 permit 校验链）。
- evidence 记录链（每动作 `actor_decision -> admission -> physical_attempt ->
  raw_receipt -> controller_transition` 五类事件全记录 + 帧工件 + 哈希链）；
  独立结果证据装置属另一项目，本仓库只保留机械层测试。
- 文档中的安全/隐私描述与 `SECURITY.md` 同步清理。

验收标准：上述关键词（`approve_mode`、`no_mouse_steal`、
`multi_input_cooperation`、`decide_gate`、`scope_guard`、`permit`、
`cooperative_`）在生产代码中搜索为零；全量测试通过后按
`docs/visual-runtime-principles.md` 的机械边界清单复核，只保留物理输入
单写入者、按键/指针释放清理、凭据不泄漏。

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

`/api/chat`、CLI、scheduler、FlowScript、`/task-contracts/compile`、服务端直接提交
`/arp/runs` 和 Legacy AgentRunner 仍是开发、Studio 或兼容面，不得成为产品 vNext 的
fallback，也不得冒充真实客户端 E2E。Renderer 中默认提交 legacy graph/task contract 的
客户端方法已经删除。MCP 只通过当前 `ConnectorGateway` 进入同一
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

2026-08-03 整改后，初始文本轮带一个 68 词的通用角色说明
`INITIAL_ACTOR_SYSTEM_CONTRACT`（回答聊天、混合目标先做任务、不编造结果），不包含
应用配方、关键词 router 或坐标教学；模型仍自由选择直接回复、connector 或桌面。入口：

- `observe_desktop`
- `search_connector_tools`
- `call_connector_tool`（只执行发现过的 read-only 工具）
- `get_agent_status` / `get_memory_status` / `get_capabilities`（Actor 自持只读状态工具，不申请租约）

申请桌面后，同一 Actor 进入视觉线；视觉动作面保持：

- `click`
- `double_click`
- `drag`（可带折线路径和跨帧 `keep_holding`）
- `scroll`
- `type_text`
- `hotkey`（模型侧名称 `press_key`）
- `launch_application`（按显示名/别名/PATH 裸命令/路径通用启动，成功后经 workspace
  因果后继机制重绑定到新窗口）
- `claim_obligation_done`
- `blocked`

显式只读 connector 的两个通用入口和三个状态工具在视觉回合仍可见；它们没有变成 GUI
动作、应用 adapter 或第二执行线。

不要为启动软件、答题、绘画、保存、发 QQ、播放音乐、调音量、棋局或某个网站新增
宏工具。已有工具结构上不能表达的新通用能力，必须先由至少两个不同任务族的真实
E2E 证明。

视觉系统提示词保持 77 个英文单词（含聊天/任务混合目标与 `blocked` 停止判断）；
初始文本轮只带上述短角色合同（68 词），不增加应用或界面教学。Thread
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
- Visual Actor `temperature=0.3`；
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

屏幕优先帧（full_desktop）落地后的最新可见 Electron 受控 smoke 为 `3/4`：
drag-fit、slider、dynamic-hold 通过，game-drag-v2（corridor）失败属模型轨迹
几何（输入执行正常、外部事件 trusted）。此前的 `6/10` 矩阵（含 4 个 corridor
全失败）是 target-window 裁剪帧时代的旧基线，不再代表当前源码。

通过条件包括用操作系统键盘向可见 Electron 输入原始目标、用鼠标点击发送、空
session、准确 run/trace 绑定与 RawDevice 物理回执；UIA 只负责定位与读回。
同一工作树的完整回归为 WinPilot `3627 passed, 13 skipped`；typecheck 与
renderer/Electron build 通过。它们证明回归合同；任务完成由模型在真实屏幕上
自行判断，本仓库不再建设独立结果证据装置。

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
`thread_id`；同一个 `LiveVisualActor` 在短角色合同首轮直接 `reply` 或调用唯一
`observe_desktop`（2026-08-03 前为零系统提示，见"动作面与上下文"）。只有后者才发布
run-bound lease，Electron 此时才隐藏并交付精确 target，matching terminal 自行恢复。
没有 router、第二角色、旧 `/api/chat`
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

## 2026-08-02 旧架构物理解耦第一批

新产品执行线现在有两道不可回退合同：从 `winpilot.orchestrator.live_agent` 遍历当前全部
内部静态依赖，任何直接、间接或可解析的动态 `winpilot.agent` 边都会使测试失败；另一个
独立进程在 import hook 明确禁止整个 `winpilot.agent` 包时，仍必须启动托管 server 并完成
一次模拟的 `/api/v2/goals/runs` vNext admission。`FrameAuthority` 已从旧 `agent` 包搬到
`visual_runtime`；旧 Runner 可以反向复用这个机械 owner，新线不再向旧命名空间取依赖。

第一批实际删除包括 Electron 的 `python -m agent` 子进程、`AGENT_GOAL` 自动入口、五个
agent IPC、local `/api/agent-start`、preload/types/state/event listener，以及 Renderer 中无
生产调用者且默认提交 legacy 的 `startGraph` / `compileTask`。服务启动对旧
Runner/router/session/adapters 改为 legacy 调用时才加载；ChatGPT MCP 也只在外部桌面会话
真正创建 `ToolSession` 时才加载旧包。按 Git rename detection 统计，生产 diff 为
`+55/-678`、净删除 623 行；`FrameAuthority` 是文件迁移，只改了 1 行归属说明。系统提示、
模型角色和工具面均为 `+0/-0`。

可见 Electron 的物理缺席测试把打包测试目录中的整个 `winpilot/agent` 可恢复改名后重新
启动。客户端仍显示运行时在线，从真实前端再次提交目标并到达 vNext 配置校验；结果与旧包
存在时一致，均因当前 `deepseek-v4-flash` 没有配置可用视觉端点而在模型调用前失败。该结果
证明产品启动/admission 不借用旧 Agent，但不是聊天或 GUI 任务通过。失败归因是 provider
配置基础设施；本轮不换模型、不增加 text/GUI router，也不为它修改 Runtime。测试包随后已
恢复，源工作树的旧包尚未整体删除。

本地发布门为 WinPilot `3607 passed, 13 skipped, 1 warning`、根测试 `2508 passed`、
renderer `317 passed`，TypeScript typecheck 与完整 build 通过；warning 是既有 `mss.mss`
弃用提示。GitHub 状态仍必须绑定本轮提交后的精确 SHA 查询。

仍不能宣称仓库已无旧架构：server 的 `/api/chat`、scheduler、Legacy ARP/Studio provider、
外部 ChatGPT MCP 的旧 `ToolSession`，以及 Electron 的本地 7880 API、feature、F9 engine 与
自动写入 `.claude.json` 仍存在。它们现在不能被新产品执行线依赖，但必须继续按消费者先行、
旧数据可读、执行 fail closed 的顺序逐批删除，不能一次性砍掉离线流程和用户计划。

## 2026-08-02 MiMo 真实分层与 `LEGACY_FLOW` 执行删除

当前 MiMo `mimo-v2.5` 的有效受控矩阵为 `6/10`：试卷、精确/有序点击、普通拖放、滑块和
dynamic hold 通过，四个 corridor 变体全部失败。四次 raw model points、canonical points 与
`physical_attempt` 逐点一致，外部 pointer oracle 的最小余量为 `-164/-94/-44/-9`；模型走错
方向或切过内角后仍声称完成。原生 Notepad 精确替换为 `NATIVE-E2E-8A31C5` 通过；Calculator
`43×17` 两次分别停在 `4 +` 和 `43 ×`、显示 0，模型在清晰 486x667 窗口上反复误认 `3`、
乘号和 `7`。这些有效失败的首分歧都在模型参数层，不在 canonical、坐标映射、SendInput 或
应用终态读取层。MiMo 不是完全不会 GUI，但当前精细定位、受约束几何、自纠错和停止可靠性
不足以担当强通用桌面 Agent。不要为这些样本加按键配方、转角规则、snap、offset、答案修正、
第二 verifier 或提示教学。

上一轮冻结候选已通过并保留。`ArpNodeKind.LEGACY_FLOW` 枚举仍用于读取历史流程，但每个该 kind
现在 validation fail closed 并给出明确迁移错误；`legacy.flow` 能力广告、默认 handler、旧
`_legacy_handler` 与六种节点的隐式 action 翻译已删除。生产 `+4/-87`、净删 83 行，测试
`+35/-2`；没有改系统提示、模型角色、工具面、坐标、键盘、scheduler、宏或用户数据。当前
20 个可运行目录节点的编译矩阵不生成 `legacy_flow`。

删除后重启真实 Electron，从可见 Studio 运行 canonical
`start -> focus_window -> input_text -> screenshot -> end`。run
`arp-20260802-183654-20ec43` 的 5 节点全部 `ok`、模型调用 0、trace
`38 accepted / 0 dropped / 0 write failures`。流程、canonical/physical 输入与外部 Tk oracle
均为 `TXT-8A31C5-中文-🙂-END`：19 个码点，UTF-8 SHA-256
`ebcb3151c0be5d553a0606e9fab8f496ce8ba1d64f75a750ed2a750eb22a866c`。截图中的 12 色静态区域
与独立 `mss` 重抓逐像素相同，RGB SHA-256
`e82463ec23dc36748988a2970377fa3448360e51ce65e2e0281a9b20b85e1321`。严格离线节点独立矩阵
升至 `3/19`。完整回归为 WinPilot `3613 passed, 13 skipped, 1 warning`、renderer
`317 passed`、TypeScript typecheck 通过；warning 仍是既有 `mss.mss` deprecation。

## 代码与架构准入规则

1. 优先修已有基础设施、复用成熟实现或净删除；不要先加层。
2. 一轮变更若增加数百行生产代码，应先证明现有 owner 结构上无法修复；数千/数万行
   的架构扩张在真实 E2E 通过前不接受。
3. 不为当前模型的精度、算术、几何或停止错误添加“通用外观”的提示和约束。
4. 不为有限软件集合做适配；适配再多应用也不能覆盖无限任务。
5. 隐私、安全和证明层只保留真实机械边界：当前帧/窗口身份、物理输入所有权、凭据
   不泄漏、不可逆外部动作边界。不要让重复 policy/ledger/guard 改写用户目标或阻断
   合法动作；只记录不保护真实边界的证据/审计链属于删除候选。
6. 新能力必须说明唯一 owner、通用输入/输出、失败语义和被删除的旧 owner，并在至少
   两个不同任务族上有真实端到端完成；不依赖独立 oracle 装置（属另一项目）。
7. 失败先比较：模型原始参数 → canonical 参数 → physical_attempt → OS/应用终态。
   证据在哪一层第一次分叉，就只修那一层。
8. 不把旧结果当本轮结果；每次改运行时代码后重载 Electron 并重跑当前源码。
9. 不把全量 pytest 或内部 `done` 当用户任务完成；任务是否完成由模型在它看到的
   真实屏幕上自行判断，不叠加独立完成验证器。
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

下一轮最高价值方向（按优先级）：

1. 执行"安全设计清理计划"（见上文 2026-08-04 计划章节）：P0 审批门/审批选项/
   legacy scope_guard -> P1 输入仲裁选项与分支 -> P2 permit 与 evidence 记录链，
   验收标准为安全关键词在生产代码中搜索为零。
2. 模型能力评估与切换始终以真实任务完成为准；当前模型为 `mimo-v2.5`（视觉
   可用、temperature 0.3、thinking 关闭），eoeo 中转（gpt-5.6-luna）已切回备份。
   如需换模型，用相同 goal/截图/schema/turn budget 做对照，保留真实完成率更高者，
   不给当前模型加提示教学、router、摘要模型或 Runtime 修正。归档 tag 只作只读
   恢复输入，保持 `master` 为唯一产品主线。

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

## 2026-08-03 主架构三缺口整改（未提交工作树）

本轮按“状态查询工具 → 无目标窗口启动能力 → 工具格式修复 → 记忆语义 → 错误阶段 →
初始轮角色合同”的顺序整改主链，没有新增模型角色、关键词 router 或第二 Planner。

1. **同一个 LiveVisualActor 增加只读状态工具**：`get_agent_status`、
   `get_memory_status`、`get_capabilities` 在初始文本轮和视觉轮都可见，由
   `_LiveAgentStatusProvider` 直接读 Runtime/配置事实，不申请桌面租约。记忆状态
   如实报告 vNext 主链 `experience_policy=disabled`（不读取/写入长期经验记忆），
   同时给出配置里的 `agent.experience_memory` 与 store 存在性，不再让模型猜。
2. **无目标窗口启动能力**：`/api/v2/desktop-context` 在没有可用前台应用窗口时
   回退到 Windows Shell 桌面（Progman）作为 `binding_mode="desktop_shell"` 目标；
   `_freeze_prebound_target`、`_BoundTargetProbe`、`DesktopWorkspace`、
   `AtomicDesktopAdapter` 增加 `desktop_shell` 标志放宽前台校验；新增通用
   `launch_application(app, arguments)` 工具，经 `perception.apps` 既有
   resolve+launch_target/startfile 执行（无记事本/计算器适配器），失败按
   `launch_application_failed` 归类为可恢复 NOT_STARTED，成功后由既有 workspace
   因果后继机制重绑定到新窗口。
3. **一次工具格式修复**：视觉轮模型工具调用不符合 schema 时，把结构化
   `tool_format_error` 回喂同一模型修复一次；第二次失败即终止。修复发生在任何
   物理输入之前；物理动作后仍绝不自动重放（call-once + permit 不变）。
4. **记忆语义统一**：`get_memory_status` 与运行面板/记忆页明确显示
   “长期经验记忆未启用（冷启动）”；Studio 长期记忆页加注 vNext 主链不读写。
5. **错误阶段分类**：`live_agent_done` 与运行输出新增 `failure_stage`：
   `model_routing` / `desktop_handoff` / `target_binding` / `screenshot` /
   `tool_format` / `physical_input` / `result_verification` / `canceled`。
6. **初始轮短角色合同**：初始文本轮新增 68 词的 `INITIAL_ACTOR_SYSTEM_CONTRACT`
   （回答聊天、混合目标先做任务并把短答案放进最终 claim、不编造结果），视觉提示词
   相应从 45 词扩展到 77 词（增加“混合聊天/任务目标”与“无法推进时调用 blocked”）。
   这是通用角色说明，不包含应用配方、关键词路由或坐标教学；此前零系统提示，历史
   记录中的“零系统提示/不增加系统教学”描述仅适用于更早版本，以本节为准。

回归：WinPilot `3628 passed, 13 skipped, 1 warning`、根测试 `2508 passed`、
renderer `318 passed`、TypeScript typecheck 与完整 build 通过。新增/更新的关键测试：
状态工具不申请租约、最后调用不提供状态工具、一次格式修复、通用启动成功/失败、
desktop_shell 绑定与启动后窗口采用、错误阶段分类、初始轮角色合同与视觉提示词长度。

## 2026-08-03 真实可见 Electron E2E（当前源码，未提交工作树）

已在 `npm run dev` 的可见 Electron 上用操作系统键盘/真实鼠标提交并完成两个基础任务：

1. **查询记忆状态** `arp-20260803-133836-78a096`（thread
   `thread-e87eb42afc3b4e67b6fe48a596952c2f`）：2 次模型调用后 `reply` 终态，
   trace 零 `desktop_lease_requested`、零 `vnext_gui_observation`、零
   `vnext_raw_receipt`、零 connector。Thread 保存的回复如实描述
   “长期经验记忆未启用；存储组件存在但未激活；experience_policy=disabled”。
   状态查询不申请桌面租约。
2. **打开记事本** `arp-20260803-134115-fe73db`（无任何记事本窗口的干净桌面）：
   一次 `launch_application` applied 后出现
   `vnext_workspace_target_adopted { causal_action=launch_application,
   target_hwnd=854236 }`——workspace 因果重绑定到新窗口；模型下一次观察即
   `claim_obligation_done`，`mission_verified`，2 turns / 3 模型调用。
   独立 Win32 oracle 看到 1 个 `notepad.exe`（pid 14112）与可见 Notepad 窗口。
   `gui_execution_trace.passed=true`（applied=[launch_application]，forbidden=[]）。

E2E 暴露并修复的问题：
- `perception.apps.launch_application` 现在优先用 `shutil.which` 解析 PATH 裸命令
  （如 `notepad`→`C:\WINDOWS\system32\notepad.EXE`），避免模糊索引把 `notepad`
  解析成已安装的 Notepad++（别名含 notepad）；这是通用 PATH 兜底，不是应用适配。
- `scripts/client_e2e_run.py` 的 `gui_actions` 集合加入 `launch_application`，
  harness 不再把该动作误报为 `non_gui_action`。
- E2E 前必须重启 Electron（kill-electron.ps1 后 `npm run dev`）：Python 源码漂移
  保护会让修改后仍运行旧源码的托管服务拒绝新任务并使 Run 按钮 disabled；重启后
  `loaded_stamp==current_stamp`，Run 按钮恢复。

受控视觉矩阵 smoke（drag-fit/slider/dynamic-hold/game-drag-v2）在重启后的当前源码
上为 `3/4`（game-drag-v2 corridor 失败是既有模型几何族，不属本轮基础设施）。

2026-08-03 晚再次全量复核当前源码：

- 重启 Electron 后受控视觉全矩阵为 `6/10`，与既有基线逐 case 一致：通过
  answer-all-questions / precise-click / ordered-clicks / drag-fit / slider /
  dynamic-hold，失败 corridor-v0/v1/v3 与 game-drag-v2（模型几何族，非本轮
  基础设施）。报告在
  `output/playwright/client-visual-e2e/20260803-210144-suite/suite-report.json`。
- original 长任务 `11/11` 未通过，全部 `failure_stage` 落在模型层
  （`model_routing` / `tool_format` / `state_rejected` / `blocked` / turn
  budget 耗尽），如 QQ 空间点赞在初始轮即 `invalid_proposal`、画板画花时模型给
  `observe_desktop` 传参、聊天样本 `reply` 终态无外部 oracle 可验证；
  `client_e2e_run.py` 的 original/mixed 目录本身不作为正式验收统计（见
  `docs/GUI_AGENT_ARCHITECTURE_RESET_2026-07-29.md` 13.16 节），本次结果只作
  失败分层记录，不改 Runtime、不加提示。
- mixed 套件两次尝试均被 `SetForegroundWindow` 无法置前台的运行环境干扰中止
  （runner 在首个 case 提交前失败），不属于模型或 Runtime 失败；已完成的
  mix-media-analysis / mix-drawing-handoff 均为模型层失败。
- 全量回归 WinPilot `3628 passed, 13 skipped, 1 warning`、根测试 `2508 passed`、
  renderer `318 passed`、typecheck 与完整 build 通过。
- 修复 `scripts/client_thread_connector_e2e_run.py` 既有 harness flaky：Windows
  文件系统 mtime 相对 `time.time_ns()` 存在约 8 ms 量化偏差，`file_evidence` 的
  `modified_after_start` 严格 `>=` 比较在全量负载下偶发误判，新增 100 ms 容差
  `FILE_MTIME_TOLERANCE_NS`；单测复跑 `16 passed`，全量下原失败用例通过。这是
  测试基础设施修复，未改变 Runtime、提示词或动作面。

本段结论只绑定上述精确 run SHA 与 trace 文件，不能覆盖未来提交。

## 2026-08-03 深夜：反馈黑洞与目标漂移三修复（未提交工作树）

依据 original 长任务 trace 分层（模型参数 → canonical → physical → 外部状态）
定位到三个真实软件任务失败的**基础设施**根因，按"只修第一层分歧"实现三个修复：

1. **被拒动作反馈黑洞**：此前 `not_started`（如 `hit_test_changed`、
   `target_identity_changed`）只写入审计证据，**不进 mission 事件流**——模型下一轮
   收到"干净"新帧，完全不知道自己上次点击没执行、为什么被拒，于是对同一过期目标
   无限重复（Krita 56 次、抖音 10 次连续 `not_started` 直到 budget 耗尽）。
   修复：`mission_runtime` 的 STALE_DISCARDED 分支新增 `_record_rejected_transition`，
   把 proven-not-executed 的拒绝作为 `transition_recorded`（`input_completed=False` +
   `error_kind`）写入投影；`TransitionSummary` / `ActorPreviousActionView` 增加
   `error_kind` 字段（canonical payload 同步放行）；`visual_actor.propose()` 不再
   要求 `input_completed=True`，只要 action 匹配就把 `previous_action`（含
   `error_kind`）回喂同一模型。仍然绝无物理重放。
2. **初始轮零修复机会**：初始轮模型给 `observe_desktop` 传参等
   `LiveModelRoleError` 此前直接 `state_rejected` 终止（0 turns）。修复：
   `begin_turn` 与视觉轮一致，首次 admission 失败回喂结构化
   `tool_format_error` 一次（`_tool_format_repair` 复用），第二次失败才终止。
3. **过期目标无限重试**：被拒动作不 arm workspace pending successor，binding
   永不更新，后续点击永远撞同一失效目标。修复：`AtomicDesktopAdapter` 跟踪滑动
   窗口内最近 3 次可恢复拒绝，若全部属于目标几何族（hit_test/target/region/
   point 类），调用新增的 `workspace.request_target_refresh()` 重新探测当前前台
   窗口；`DesktopWorkspace` 新增该 uncaused successor 机制（复用既有两采样
   adopt，仍不直接改写输入）。非几何拒绝（如 permit）会打断该序列，避免误刷新。

配套测试：被拒动作进入事件流并含 error_kind、rejected previous_action 替换旧的
completed intent、初始轮一次修复/二次终止、workspace refresh 成功/无前台变化不
adopt/覆盖旧 pending、adapter 连续几何拒绝触发 refresh、混合拒绝不提前触发。

全量回归：WinPilot `3635 passed, 13 skipped, 1 warning`（+7）、根测试
`2508 passed`、renderer `318 passed`、typecheck 与完整 build 通过。

可见 Electron E2E（重启后当前源码）：

- 受控视觉全矩阵 `6/10` 与基线逐 case 一致，无回退
  （`output/playwright/client-visual-e2e/20260803-225924-suite/`）。
- 画板画花 `original-2` run `arp-20260803-231058-b2dff9`：60 次回执中
  `launch_application applied ×1`、`click applied ×58`、仅 1 次
  `hotkey not_started:target_identity_changed`、1 次 launch 后窗口采用——
  对照修复前 Krita 案例 `click not_started ×56`。模型 60 轮全部真实点击画板但
  未识别完成（停止判断，模型层），基础设施不再吞动作。
- 网易云 `original-5` run `arp-20260803-231730-12a6b8`：`launch_application
  applied ×24`、`hotkey applied ×1`、`click not_started:hit_test_changed ×35`；
  模型收到 error_kind 后仍重复点击（策略未变，模型层）。

结论：三修复消除了"点击被拒且模型不知情、目标漂移后无限重试"两条系统性问题，
真实软件任务的剩余失败归因转为模型层（目标选择、停止判断）。本段结论只绑定上述
精确 run SHA 与 trace 文件，不能覆盖未来提交。

## 2026-08-04：run_shell 接入 vNext + 死板规则清理（未提交工作树）

**产品方向确认**：完整整体 Agent（B 形态）——GUI 工具与 shell 工具同时可见，模型
按语义自主选择；评测纯度由任务语义保证（受控矩阵本身 GUI-only），不用工具投影
裁剪。

**run_shell 接入**（connector 同款路径，文本轮+视觉轮可见，不申请桌面 lease）：
- 工具 `run_shell(command, shell, timeout_s)`，复用既有 `action/shell.py`
  （safety.guard 守卫、超时、UTF-8），`config.agent.shell_enabled` 控制是否提供
  （已改为 true）
- 注入链：`_shell_runner_factory` → `_CompositionDependencies.shell_factory` →
  `LiveVisualActor(shell_runner=...)`；`_shell_exchange`/`_call_shell`/`_shell_tool_definitions`
- 每工具族调用上限改为**共享本地工具预算**（status+shell+connector 合用一个
  budget，全局 `model_call_limit` 是唯一硬顶）

**死板规则清理**（原应用语义却用硬编码的位置）：
1. **H1 窗口交接动作名门控**（workspace.py）：删除 `_WINDOW_CHANGING_ACTIONS`
   动作名列表——所有完成输入都武装后继探测，是否真切前台由两采样观察语义决定
2. **H4 失败原因被吞+分类器错归**（live_agent.py）：保留真实 `result.detail`
   （transport 错误截断到类型前缀防敏感泄漏）；`blocked` 不再归
   `physical_input`（是模型语义停止判定→model_routing）；无关键词的
   state_rejected 归 `physical_input`（系统前置失败）而非默认 model_routing
3. **M3 run_shell 描述**：删除 "Prefer this over clicking..." 偏好规则（提示教学），
   改中性事实描述
4. **M4 初始提示关键词路由**：删除 "Call observe_desktop for any request to
   open, search, click..." 动词枚举，改为中性"需要看/操作桌面时使用"
5. **H3 可恢复错误白名单**（mission_runtime.py）：删除
   `_RECOVERABLE_NOT_STARTED_ERROR_KINDS`——所有 proven-not-executed 拒绝都带
   error_kind 反馈模型，是否继续由模型语义决定，系统只留机械预算
6. **M7 动作历史不对称投影**：`_safe_action_summary`/`is_safe_action_summary`
   统一为裸动作名（scroll_down/scroll_up/hotkey_chord 特判删除，细节在
   previous_action 结构化视图与截图），清理 `_HOTKEY_SUMMARY_*` 死代码

全量回归：WinPilot `3637 passed, 13 skipped, 1 warning`、根测试 `2508 passed`、
renderer `318 passed`、typecheck 与完整 build 通过。

结论只绑定当前工作树；未提交，`CON` 未触碰。

**M2/M6 补充**（同一轮）：
- M2 连续 INVALID_PROPOSAL/ACTOR_ERROR 硬停：判定为预算护栏（配合
  max_turns/max_model_calls 有界），核心错误吞没已在 H4 解决（真实 detail 保留、
  transport 截断防泄漏），不再改 session 注入接口
- M6 指针 held 时只允许 drag：改为**排除指针动作**（click/double_click/drag 冲突）
  但允许非指针动作（hotkey/type_text/launch/claim/blocked）——held 且 owned 时
  模型可以继续拖或语义结束；held 但租约丢失（非 owned）时仍全排除（机械安全）


## 2026-08-04 屏幕优先帧与守卫层解除（已提交 master）

按 Model-first 原则整改视觉执行线，目标：模型看到真实屏幕、点击落在它指的位置，
解除层层守卫对合法动作的拦截。三次真实运行的分层审计表明：网易云任务失败的
首分歧不在模型参数，而在执行线——裁剪帧让模型看不到已启动的应用（10:06 run），
raw_device 的强制前台检查用绑定窗口（终端）判定点击合法性，导致 49/59 次点击
被拒、模型只能无限重试（14:52 run）；解除后同一任务一次完成。

本轮改动：

1. **屏幕优先帧（默认）**：`controller.target_window_capture_enabled` 默认改为
   `false`；`live_agent._capture_current` 默认按完整虚拟桌面捕获
   （`frame_scope=full_desktop`），窗口裁剪仅显式开启或 desktop_shell 时使用。
2. **点击任意可见窗口**：`atomic.py` 在 full_desktop 帧下放行点击（hit test 落在
   哪个窗口就点哪个），跳过对绑定窗口的前台强制；点击后 OS 自然激活目标窗口，
   下一轮 workspace 因果后继自动重绑定。
3. **解除 raw_device 强制前台检查**：`enforce_foreground` 参数全链透传
   （`RawDeviceExecutor.click -> execute_admitted_physical_click -> _raw_click`），
   vNext 在 full_desktop 帧下传 `False`；legacy 路径默认不变。
4. **scope 校验统一放宽**：envelope / Broker / MissionRuntime / 证据平面里所有
   硬编码 `frame_scope == "target_window"` 改为接受
   `{target_window, full_desktop}`。
5. **legacy done_verifier**：仅当验证过的窗口裁剪被选中时才附加原生图，全屏帧
   走 vNext 原生路径，避免全屏帧误触发 LLM 完成判断。

回归：Python `3627 passed, 13 skipped`（新增 screen-first 点击/重绑定用例）。
可见 Electron E2E smoke：slider、dynamic-hold 外部判定通过，game-drag-v2 走廊
失败属模型轨迹几何（输入执行正常）。本仓库不再为任务完成建设独立结果证据装置
（该装置属另一项目）；任务完成由模型在真实屏幕上自行判断。
