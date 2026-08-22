# GUI Agent 端到端测试规范

Implementation note: the current primary `LiveVisualActor` path keeps one original goal, one
`LiveVisualActor`, one visual execution line and eight generic GUI/control
tools. Historical `observe.image_region` and persistent `wait` descriptions
belong to Legacy or frozen experiments; they are not current client-E2E
requirements and must not be restored to teach the 11 samples. See
[`visual-runtime-principles.md`](visual-runtime-principles.md) for the
invariants and client-entry contract.

最近一次当前源码验证（2026-08-19）从可见 Electron 客户端完成受控桌面 `18/18`、
安全真实应用 `5/5` 和 Studio 离线流程 `2/2`。受控与真实应用共 133 次
`physical_attempt` 对应 133 次 `raw_receipt`，全部 `applied/input_completed`；43 次释放清理
全部完成，trace dropped/write failure 均为 0。静态门为 Python `2508 passed`、Renderer
`327 passed`、TypeScript typecheck、Electron/CLI build 与项目路径扫描通过。以下带更早日期的
段落是历史探针，不覆盖这一当前基线，也不能单独证明当前源码。

最近一次受控矩阵验证（2026-08-01）使用可见 Electron 客户端、空 session、
操作系统鼠标键盘输入和独立浏览器 oracle。UIA 只定位控件和读回目标，不写入
目标或代替点击。10 项中 6 项通过：`answer-all-questions` 取得 6/6，另有
`precise-click`、`ordered-clicks`、`drag-fit`、`slider`、`dynamic-hold`；
四个 corridor 变体均因模型提交的路径离开可见通道而失败。报告：
`output/playwright/client-visual-e2e/20260801-183951-suite/suite-report.json`。
这只能叫受控 GUI 能力矩阵，不能冒充真实消费软件成功率。

浏览器靶场不能单独代表桌面 E2E。当前已得到 Notepad 精确落盘的 grounded pass，
并完成 Calculator、Paint、Krita、网易云、QQ、抖音、Wireshark、OneNote 等
原生探针。目标同样从可见 Electron 用操作系统键盘输入、用真实鼠标发送，Agent
动作由 RawDevice 物理执行，结果由文件系统、应用状态或其他独立 oracle 判定。
测试 setup 可以使用命令行，但用户明确要求鼠标键盘时，命令行、UIA、DOM 或 API
不能代替被测动作。

本文定义什么可以叫“真实客户端 E2E”、什么可以计为通过，以及如何使用 11 个真实任务和组合长任务而不把它们写进生产逻辑。

## 当前基线

本轮开始时最近完成本地发布回归的固定产品基线是 `4f93672`：Python
`3464 passed, 13 skipped, 1 warning`、renderer `243 passed`，TypeScript typecheck
通过。该提交的 GitHub Actions run `30663041917` 因 billing/spending limit 在两个 job
均为零步骤时终止；它只能记为 infrastructure blocked，不能记为 Actions 已绿，也不能
把本地结果冒充远端检查。

2026-08-01 产品 owner/config 从历史 canary 名称迁移为 `live_agent` 后，当前精确源码
本地全量复验为 Python `3468 passed, 13 skipped, 1 warning`、renderer `243 passed`，
TypeScript typecheck 通过；可见 Electron 的 fresh/history 双臂、6 轮纯文本聊天与随后
connector→Notepad 任务也全部通过。test-only CI fixture 收口提交 `65fdce0` 的 Actions
run `30688550071` 又实际执行并通过 frontend、Python 两个 job；这是该精确提交的远端
绿色证据，未来 HEAD 必须重新查询。

2026-08-02 当前工作树的发布门为：renderer `320 passed`、根目录 Python `2508 passed`、
WinPilot `3604 passed, 13 skipped, 1 warning`，TypeScript typecheck、renderer/Electron build、
CLI/AI build 均通过。新提交仍必须查询自己的 Actions，不能继承历史提交的绿色状态；这些
回归合同也不替代下面的外部 oracle。

2026-08-02 对示教/离线流程链做了可见 Electron 双窗口探针：用户从前端开始录制，依次在
两个独立 Tk 窗口完成 11 个鼠标键盘叶子，停止后自动生成 canonical Automation IR 并在
Studio 显示为 13 节点/12 连线。随后从 Studio 前端运行，ARP run
`arp-20260802-000043-6439b0` 的 11/11 步证据通过，模型调用为 0；独立窗口标题 oracle
确认 A=`afinal001`、B=`btwo001`。重置两个窗口后，又从“技能与记忆”前端完成
memory verification `verify-7bf3d1b4ed57`，候选由 provisional 晋级为 `replay_verified`；
再次重置后点击“复用”，RuntimeJob `replay-1c54a9f89d9e` 完成且同一外部 oracle 再次通过。
这组证据只证明 human Apprentice 链；Agent 整场对话导出与离线节点证据分别按下面的独立
探针记账，三者不能互相替代。

2026-08-02 随后完成了一次真实两轮 Agent Thread 导出与离线 replay。用户从可见 Electron
前端分别要求在 Notepad 输入 `ARP-THREAD-A-20260802-0329`，再追加
`-B-20260802-0333`；两个来源 run 为 `arp-20260802-033126-e2c5f9` 与
`arp-20260802-033440-85c0f4`。从对话顶栏导出得到 14 个普通 ARP 动作，确认后载入 Studio
为 16 节点/15 连线；清空 Notepad 并从 Studio 运行后，
`arp-20260802-033821-0bb256` 的 16 个节点完成且 `vnext_model_call=0`。独立剪贴板 oracle
精确读到
`ARP-THREAD-A-20260802-0329ARP-THREAD-A-20260802-0329-B-20260802-0333`，长度 68，
SHA-256=`934b79c77eb2d6120be63b8a47141c902f343affd49a14e393cc7629d755230b`。
重复 A 不是 Runtime 修正目标：第一轮未结束时测试操作重新激活了 Electron，叠加模型的参数/
目标选择形成额外动作；raw canonical 与 `physical_attempt` 一致，replay 忠实复现。因此重放链
grounded pass，但原始第二轮“只追加一次”的任务结果不算干净成功。

重启当前源码后，普通用户再次从对话顶栏导出：前端显示 `partial`、14 个动作和
“只加载、不会自动运行”，用户确认并放弃旧未保存草稿后，Studio 载入 16 节点。
在默认约 `902 x 582` 窗口点击“运行工作流”，run
`arp-20260802-065621-dd12f0` 的 16 个节点全部完成，`trace_integrity=complete`，
`vnext_model_call=0`。导出节点的参数面板可见保留了经认证进程一致的
`target_window.title + process`，Runtime preflight 也按这两个通用窗口标识定位。
独立剪贴板 oracle 再次得到同一 68 字符串与同一 SHA-256。这是当前源码的
可见重放证据，但仍不改写上述原始 Agent 第二轮受测结果。

这不证明整场对话的所有普通 ARP 动作都可忠实导出。跨 decision 的 held gesture 含
`started_held` / `keep_holding`，当前会按各段落点投影成普通 click，不再得到 0 步，但不能
冒充连续拖拽；缺少 durable run -> Thread owner index 也使完整归属只能靠约 14 秒的全量
trace 扫描核对。这两项属于基础设施/语义保真缺口。
轻量 owner scan 一度把本机耗时降到约 1.36 秒，但可能漏掉归属/完整性异常而 false-complete，
实验已完整回退。当前选择较慢但可证明的全量扫描。

同一可见 Studio 的一句话辅助探针提交“点击坐标 420,300，等待 1 秒，然后输入文本 hello”。
隔离的 `FlowFragmentProposer` 用一次 opt-in、单条 user JSON、零 system/developer message
返回 `click -> wait -> input_text` 三节点/两连线；确认后片段出现在当前图右侧空白处，未自动
连接、保存或运行，`Ctrl+Z` 恢复原来的 16 节点/15 连线/14 动作。这证明生成/预览/应用/撤销
合同，不是自然语言编排成功率，也不计入离线节点通过率。

严格的离线节点独立矩阵当前为 `2/19`，只计 `click` 与 `screenshot` DUT。2026-08-02 从可见 Electron Studio
打开 `python/winpilot_home/eval_targets/offline_flow_click_e2e.json`，校验后运行
`start -> focus_window -> click -> managed screenshot -> end`。run
`arp-20260802-052309-82cf06` 的 5 个节点全部 `ok`、`trace_integrity=complete`，模型调用为 0；
Runtime 外部 JSON oracle 显示 `click_count: 0 -> 1`，其余输入计数全部保持 0。截图工件
SHA-256=`7a04783ba7c6d80ba81d0da86fd991b9e158a5315dc7608dd51b0c690e775e04`。
业务 click 通过只依据外部 JSON；managed screenshot 只提供真实 artifact evidence，不是业务
oracle，因此 `focus_window` 与 `screenshot` 都不计为独立通过。

该历史探针证明了 spatial action 前 target 建立与 managed screenshot 工件路径。当前 Studio
ACTION_BATCH 已改为直连 `fast.controller`，不再创建 `ToolSession`；2026-08-19 又从可见 Studio
复测 click 与 clipboard/screenshot 两条 fixture，外部 click 计数与剪贴板结果均通过，两个 run
trace 完整且零模型调用。managed screenshot 是真实工件，不是独立业务 oracle。

同日又从真实属性面板直接框选 screenshot 区域。先按 `Esc` 取消，节点参数未改变且没有新
run；在 125% DPI 屏幕再次框选后，属性面板与 canonical action 都保存物理区域
`x=16,y=130,width=934,height=182`。从前端运行得到
`arp-20260802-130804-ecc3bd`，节点全部 `ok`、`trace_integrity=complete`、模型调用为 0。
受管工件为 934x182；独立 `mss` 在同一物理区域重抓，解码 RGB 像素逐点相同，双方像素
SHA-256 均为 `57d2bf67fcb8f14488b882a32864b09136a069ae96857ac98bbcaa7908ade743`。
manifest/evidence 的 artifact id、文件 SHA 和 region 也全部一致，12 个当轮 marker 色块均存在并
按顺序排列。另一次真实拖选覆盖到 work-area 下方的任务栏区域，返回
`x=50,y=1030,width=350,height=35`，排除了 overlay 被工作区裁短。当前机器只有一块屏幕，
第二显示器/负坐标严格记为 `infrastructure_unavailable/not_run`。剩余 17 个节点只是尚无严格
独立证据，不能记作已失败。

另有一个组合 E 级批次：从可见 Electron 打开
`python/winpilot_home/eval_targets/offline_flow_clipboard_screenshot.json` 并运行，YES 路径覆盖
`start/end`、`clipboard_get`、`condition`、`clipboard_set` 和基础 `screenshot`。独立 OS
剪贴板精确得到 `OFFLINE-NODE-E2E-PASS-20260802`；截图工件为 1920x1080、182009 bytes，
manifest/hash 一致并可视为真实 Studio 桌面。NO 分支未运行，截图也没有验证 target nonce；
因此只能记为这几个节点/分支的组合证据，不能称为所有离线节点逐项通过。

旧架构删除现在还必须通过三层 absence gate：

- `live_agent` 全部内部 import 闭包不得触达 `winpilot.agent`；
- 独立进程阻断整个旧 Agent 包时，托管 server 与 vNext goal admission 仍须成立；
- 打包 Electron 中物理移走旧包后，真实前端必须仍能上线并提交到同一个 vNext admission。

2026-08-02 前两层通过；第三层也到达 admission，但当前模型配置在调用前因缺少视觉端点
fail closed，因此不能记为聊天或 GUI 完成。下一轮只尝试删除 `LEGACY_FLOW` 执行兼容，并用
可见 Studio 的 canonical `input_text` 与 `screenshot` 两族外部 oracle 决定保留或整批回退；
历史文件继续可读/可编辑，未知旧节点必须明确报迁移错误。

同一候选的真实前端原生探针没有被内部 `done` 覆盖：Calculator `43×17` 的外部显示为
`44` 而非 `731`；Paint 没有形成合格红色 X；QQ 群摘要在零 GUI 动作前被未批准工具拒绝；
网易云错误选择 connector 元工具且参数不合法，目标歌曲/播放态/5% 音量均未成立。这四项
首分歧都在模型原始参数或工具选择，不能据此修改 Runtime。正式报告分别位于
`output/playwright/client-e2e/20260801-185226-calculator-random-43x17/report.json`、
`output/playwright/client-paint-game-e2e/20260801-190517-paint-tic-tac-toe/paint-report.json`、
`output/playwright/client-qq-groups-e2e/20260801-190819-qq-groups/qq-groups-report.json` 与
`output/playwright/client-netease-music-e2e/20260801-191108-netease-music/netease-music-report.json`。

2026-07-31 受控矩阵与原生探针记录已复验一组真实消费软件。当前源码仍未完成 11 个真实
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
6. 保存提交前、首次视觉调用所见桌面和终态截图；
7. runtime 没有超时、漂移、未配对输入或 unverified done；
8. 独立 oracle 证明用户目标实际成立；
9. 测试结束后只清理本次启动且已核验属于工作区的进程。

被测 run 必须保留用户完整自然语言目标，并进入和正常用户相同的单一视觉执行主线。测试不得改用更大的工具 schema、额外提示词、内部宏、直接 Controller 调用或绕过客户端的预置计划。

明确要求 GUI、鼠标或键盘的 fixture，其 oracle 必须显式要求对应执行介质，
不能解析目标关键词来路由 Runtime。通过还必须有对应的 `physical_attempt` /
RawDevice 证据和目标应用收到的可信操作系统事件；命令行辅助可以存在，但
不能替代被明确要求的界面动作。

`status=done` 只表示候选完成。没有第 8 条时，报告必须是 `ungrounded`，不能是 `passed`。

## 聊天与统一视觉生命周期

前端聊天不是 GUI task 的宽松变体。真实聊天 E2E 必须用同样的操作系统键盘和真实
鼠标发送，但通过条件是：

- 从点击发送到回复可见，Electron 始终 `visible && !iconic`；
- 第一次 provider 请求包含完整真实桌面 JPEG、原始 goal 和预期的有界 Thread history；
- 没有 `physical_attempt`，也不伪造任何外部应用状态；
- 前端显示同一 Actor 通过 `reply_to_user` 返回的用户结果。

会话重启还必须使用正常标题栏关闭，并证明新 Electron、托管服务 PID/HWND 与旧进程不同，
旧消息重新可见、下一条消息仍绑定同一 `thread_id`，且模型实际收到的恢复历史与有界
checkpoint 精确一致。失败基线
`output/playwright/client-thread-restart-e2e/20260801-153038-thread-restart/report.json`
在服务重启后得到 404；最小 checkpoint 候选随后在聊天 marker 与 connector→Notepad
两个不同任务族通过，报告为
`output/playwright/client-thread-restart-e2e/20260801-194535-thread-restart/report.json`。
这只证明最近成功 Turn 的短会话连续性，不证明长期 memory。

未发送草稿是独立客户端状态：正常关闭/重启后应逐字恢复，但不能创建 run；成功发送和
“新建对话”必须清除。可见 Electron 证据为
`output/playwright/client-draft-restart-e2e/20260801-170711-draft-restart/report.json`。

需要桌面操作的同一入口不再经过客户端交接。服务端必须在第一次模型调用前建立桌面
workspace 并捕获权威 `full_desktop` JPEG；任何空间动作都必须引用该帧，动作回执后再
捕获新帧。Electron 不得为 run 隐藏、最小化或在终态抢回前台。

`scripts/client_e2e_run.py` 必须记录提交后与 terminal 时的 `IsWindowVisible`、`IsIconic`、
foreground HWND 和窗口矩形；harness 的聚焦或截图动作不能掩盖产品自行隐藏、最小化或
抢前台。纯聊天与 GUI 任务都走同一视觉 run 状态机，差别只是前者没有物理输入。
最后一轮还必须只凭该 thread 的有界上一轮交换回答 GUI 轮产生的随机值，防止把三个
互不知情的 run 误报为 Codex 式连续对话。

## Connector、模型协议与缓存

Connector 的组件测试至少覆盖：仅加载显式 MCP 配置、remote HTTP 与 stdio 两种
transport、环境变量替换、目录/参数/结果大小上限、取消和总 deadline，以及 stale
ref、descriptor 变化和调用前完整 descriptor 重检。不能只 mock Gateway 后
声称远端调用成立。正式报告还应持久化不含 credential/正文的 run-scoped physical
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
  `Actor returned an unapproved tool`（当时的错误文案），回复为空、零 GUI 动作。这个干净
  task failed 的 usage 为 `8126` prompt、`539` completion、`4160` hit、`3966` miss；
  首个分歧是模型工具选择/停止，不是 connector transport。报告：
  `output/playwright/github-connector-e2e-ascii/20260731-220236/report.json`。

History-depth 对照必须额外满足：

- 两臂的 task、fixture config/source、输出路径与 Runtime/model identity 均由实测 hash
  比较，不能硬编码控制变量成立；
- fresh target 实际 provider history 为 0；history target 的 message count/chars/roles/hash
  必须从 Actor 随后真正放进 provider request 的 message list 旁路观测，不能用
  “20 个前端 Turn 约等于 6 轮模型历史”推算；
- 每个 history chat 都必须证明 raw connector call、Gateway call、GUI action 和 Runtime
  physical attempt 全为 0；首个全桌面 JPEG 属统一调用合同，不算预热其他执行通路；
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
为纯文本回忆进入了当时独立的 GUI 路径，因此旧 text-only gate 必须失败；这项历史
判据已被“首调固定全桌面 JPEG、聊天零物理输入”的统一合同取代。反查旧正式 B 的 20 个
prefill run，其 raw/Gateway connector、tool attempt 和 GUI action 均为 0。聚焦回归
`34 passed`。下一次正式矩阵应改用预先存在且已打开的文档，避免 Save As 噪声，并至少
运行四个随机 seed 的 counterbalanced pairs。
当前发布前全量回归为 Python `3462 passed, 13 skipped, 1 warning`、renderer
`234 passed`，TypeScript typecheck 通过。

显式 MCP 配置最高优先级与按完整 server 配置 fingerprint 隔离目录缓存已有
`21 passed` 聚焦测试；后续回归仍需覆盖同名 server 多配置，防止再次读到错误目录。

OpenAI Responses、Anthropic Messages 与 Gemini `generateContent` 首先做 canonical
message/tool/image 的双向 fixture 测试，再分别用真实 credential 做 provider smoke；
最后仍需由可见 Electron 触发至少一个零物理输入的 `reply_to_user`，并核对第一次请求已
携带 JPEG；声称支持 GUI 的模型还需一个实际产生物理输入的简单任务。配置必须显式选择 wire，测试也要证明只改变 `api.provider`、
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

冷启动用于证明硬实力不依赖历史经验。当前产品 vNext 每个 run 都固定
`experience_policy=disabled`，不会经过已删除的 Task Contract、StageRuntime、RuntimeProfile 或
AgentRunner。测试需验证同一条 `LiveVisualActor → MissionRuntime` 链没有 experience prompt、
Skill replay、Memory Pack、经验写入和反馈；Studio/Replay 的兼容记忆不能泄漏进产品 goal。

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

流程工作台的 renderer 聚焦回归还应覆盖真实默认窗口边界，而不只用宽浏览器视口：

- `900 x 580` 下节点库和属性面板必须是可开关抽屉，主画布不被三列挤压；
- 片段对话框的 `Esc` 不得关闭整个工作台，确认按钮在最小高度内仍可达；
- 片段落点按 canvas viewport 计算，一次 undo/redo 必须整体回退/恢复；
- 每个属性控件都要断言精确 canonical 参数；不支持的图片、方向、坐标组合，以及非法、越界
  或跨屏截图区域必须 fail closed，不能静默降级成另一种动作；
- active run 使用启动时快照；若允许继续编辑，UI 必须明确标为“下一次运行”。

2026-08-02 的小窗口可见探针用真实 Electron 默认尺寸提交两条口语请求：一条生成
`focus_window -> click -> wait -> input_text -> screenshot`，另一条生成
`wait(0.5) -> keyboard(enter)`；完成参数修改、应用、整体 undo/redo，并验证抽屉、主题、
对话框 Esc 与 sticky footer。直接相关的 6 个 renderer 聚焦文件共 48 项测试通过，
完整 renderer 回归为 30 个文件、316 项通过。这个结果只证明
编辑体验与编译边界，不替代下方逐节点真实执行矩阵；图片模板输入当前仍是未实现能力。

2026-08-02 删除 `LEGACY_FLOW` 执行 owner 后，必须从重启后的可见 Studio 重新跑，而不能复用
删除前的节点结果。当前回归图为
`start -> focus_window -> input_text -> screenshot -> end`：`input_text` 由 Runtime 外部 Tk JSON
核对完整 Unicode 文本、码点长度和 UTF-8 SHA；`screenshot` 由独立 `mss` 在同一物理区域重抓，
比较解码后 RGB 像素 hash。run `arp-20260802-183654-20ec43` 两项均通过且 trace 无丢事件，
因此兼容删除保留。控制台乱码不能直接判产品失败，必须回到原始 JSON 字节/码点；同理，
Runtime `done` 不能替代 Tk 或像素 oracle。删除后的完整回归为 renderer 30 个文件、317 项，
WinPilot `3613 passed, 13 skipped`，TypeScript typecheck 通过。

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
- GitHub Actions 必须实际启动且通过；billing、配额或 runner 导致的零步骤终止只能
  记为 infrastructure blocked，本地回归不能冒充 Actions 通过。

单次成功不能换算成成功率。只有固定环境、固定协议、多 seed、多次重复和外部 ground truth 才能报告统计指标。
