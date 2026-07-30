# AI / Codex / 开发者接手入口

> 最后核对：2026-07-30。最后一笔运行时代码提交是
> `bf29783 fix: preserve one visual tool exchange`；其后的交接提交只更新文档。

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
- 后续正常开发直接提交到 `master`；不要再建立另一条长期产品分支。
- 本地未跟踪 `CON` 属于用户，不能读取后擅自解释、暂存、删除、改名或覆盖。

提升前的分支差异、恢复 PR 和审查依据保存在
`docs/GUI_AGENT_ARCHITECTURE_RESET_2026-07-29.md` 第 13.11/13.12 节。它们是历史
证据，不是新的分支工作流。

## 当前唯一产品主链

```text
Electron「智能代理」
  -> POST /api/v2/goals/runs { original goal, execution_context }
  -> Goal Admission（不接收执行模式）
  -> 一个 full-goal mission / 一个 LiveVisualActor
  -> 原始 goal + 当前权威 JPEG + 有界 task state
     + 最多最近一笔 provider 原生 tool exchange
  -> 模型选择一笔通用工具调用
  -> CapabilityBroker 只投影机械可用工具
  -> AtomicDesktopAdapter 唯一一次坐标映射
  -> RawDevice / SendInput 真实鼠标键盘
  -> 新权威帧与回执
  -> 模型继续、claim 或 blocked
```

`/api/chat`、MCP、CLI、scheduler、FlowScript、`/task-contracts/compile`、客户端提交
`/arp/runs` 和 Legacy AgentRunner 是开发、Studio、集成或兼容面，不得成为产品
vNext 的 fallback，也不得冒充真实客户端 E2E。

前端执行约束选项及其 request/admission/Broker/evidence 状态链已经删除。用户口头说
“必须使用 GUI”时，只保留在原始 goal 中，由同一个模型理解。未来加入 shell 或
provider 后，它们必须与 GUI 工具同时对该模型可见；不能恢复前端开关、Task
Profiler、关键词预路由或另一个 planner。

## 当前角色与唯一 owner

| Owner | 负责 | 不负责 |
| --- | --- | --- |
| `LiveVisualActor` | 理解完整目标、看当前 JPEG、选择语义目标/精确坐标/下一动作、维护有界状态、决定结束 | 物理输入、应用脚本、隐藏策略 |
| `MissionRuntime` | 驱动模型→动作→新帧的顺序循环 | 编译任务步骤、纠正模型答案 |
| `FrameAuthority` / workspace | 当前窗口、区域、frame id、epoch、JPEG | 解释画面 |
| `CapabilityBroker` | schema、当前可写性、帧/窗口/指针所有权 | 选择任务路线 |
| `AtomicDesktopAdapter` | 把提交图片坐标唯一映射到物理桌面并执行一次 | snap、offset、目标中心、重瞄 |
| `RawDevice` | 真实 SendInput、完成/不确定回执、按键释放清理 | 判断语义成功 |
| 外部 E2E oracle | 判断测试任务真实结果 | 向生产 Agent 暴露答案或坐标 |

不要新增 Grounder、Verifier、Profiler、Critic 或另一个 Planner 来修当前模型。
一个 Visual Actor 当前已经同时承担视觉理解、精确定位、短任务状态、schema 和停止
判断，压力确实较高；优先换更强原生视觉模型，而不是重新拆角色和增加消息传递。

## 动作面与上下文

当前 live vNext 的模型工具面保持：

- `click`
- `double_click`
- `drag`（可带折线路径和跨帧 `keep_holding`）
- `scroll`
- `type_text`
- `hotkey`（模型侧名称 `press_key`）
- `claim_obligation_done`
- `blocked`

不要为启动软件、答题、绘画、保存、发 QQ、播放音乐、调音量、棋局或某个网站新增
宏工具。已有工具结构上不能表达的新通用能力，必须先由至少两个不同任务族的真实
E2E 证明。

Visual Actor 的系统提示词保持 45 个英文单词。当前只保留最近一笔 provider 原生
`user image -> assistant tool call -> tool result`，下一回合加入新 JPEG 后覆盖旧
交换；不保存无界聊天历史，不把长期 memory 当硬能力，也不在 prompt 中教学。

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

已经做过并回退的负优化：

- `thinking=true`：corridor 仍失败且更慢；
- 最大提交边 `1120 -> 1600`：更差；
- 移除 Visual Actor `temperature=0.0`：随机重复错误更多；
- `mimo-v2.5-pro`：当前官方接口不支持图片，不能作为截图主模型；
- 为 corridor、试卷、重复点击或边缘坐标加提示/规则：禁止，错误属于模型时会对更强
  模型形成负优化。

下一项高价值实验是把更强的原生视觉模型放到完全相同的 10 项矩阵做 A/B。若试卷和
corridor 直接改善，Runtime 不应随模型升级再变化。没有可用的更强视觉模型时，
宁可诚实保留失败，也不要继续扩张执行层。

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
| precise-click | answer-all-questions（5/6） |
| ordered-clicks | corridor-v0 |
| drag-fit | corridor-v1 |
| slider | game-drag-v2 |
| dynamic-hold | corridor-v3 |

通过条件包括真实 Electron 输入、空 session、真实鼠标键盘、准确 run/trace 绑定和
独立浏览器 oracle。报告在被 Git 忽略的
`output/playwright/native-exchange-all-20260730/20260730-215611-suite/`。

关键归因：

- `ordered-clicks` 在加入最近一笔原生交换前会重复第一个目标；现在三次真实点击按
  `[1,2,3]` 通过。这是结构性上下文修复，应保留。
- 试卷 `8×9` 失败时，模型第一次和“纠错”时原始参数都明确是 `81`；物理层两次都
  原样执行 `81`。这是模型智力问题。
- 四个 corridor 中，模型提交折线与 `physical_attempt` 逐点一致；真实浏览器记录
  完整 pointermove，但模型路径切角或穿出通道。这是视觉几何问题。
- WinPilot 全量回归：`3373 passed, 13 skipped`。回归不能替代 E2E。

五项原始长任务在当前精确源码上没有 5/5：

1. Krita 的 modal 生命周期与返回主窗口已打通，但模型重复点击颜色控件边缘，未完成
   绘画、保存和 QQ 发送。
2. 试卷能答完六题但最新样本为 5/6。
3. 井字棋没有当前精确源码的正式外部-oracle pass。
4. 网易云历史样本曾播放成功但音量为 100%，不是用户要求的 5%，也不是当前复验。
5. QQ 群摘要历史运行到达真实窗口并执行 27 笔输入，但没有跨群覆盖状态与可信完成。

不得把历史人工确认、部分动作成功或受控靶场通过写成这五项已完成。

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

确认只看见用户未跟踪的 `CON`，然后阅读本文件和上述五份文档。不要立刻改代码。

优先级：

1. 找到可用的更强原生视觉模型，使用同一 10 项矩阵做干净 A/B。
2. 只有当前 E2E 证明缺口时才审查冻结分支候选；不扩张 Runtime 补模型。
3. 只在 submitted/canonical/physical/终态首次分叉能复现时修基础设施。
4. 重跑五项真实任务及随机组合长任务，保留失败证据。
5. 审计完成的冻结分支直接删除，保持 `master` 为唯一产品主线。

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
