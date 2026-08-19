# 流程图 / DSL / @feature

这套系统和 WinPilot agent 并存，但不是同一个工具体系。

## 前端

路径：`src/renderer/`。当前生产工作台已中文化，主导航显示“智能代理、流程工作台、运行记录、技能与学习、系统设置”；API、MCP、VLM、URL、模型名和坐标字段等技术标识保留原文。

核心内容：

- `types/index.ts`：FlowScript 类型和 `NodeType`。
- `utils/nodeDefinitions.ts`：节点定义。
- `components/platform/PlatformWorkbench.tsx`：唯一生产工作台；智能代理、流程工作台、运行记录、技能与学习、系统设置共用 control plane。
- `components/PropertyPanel/`：参数编辑。
- `store/defaults.ts`：默认设置和预置模板。
- `store/persistence.ts`：localStorage 持久化 helper。
- `store/index.ts`：Zustand store；不再复制 defaults/templates/persistence。

`NodeType` 联合类型包含现役节点和历史兼容类型，不等于生产运行能力。节点库、统一搜索和“最近使用”只展示 `flowToArpGraph.ts` 的本地可编译集合；后端 capability catalog 的 `flow_node_types` 只负责标记当前 Runtime 是否已经声明对应执行能力。Runtime 离线或 catalog 尚未加载时，用户仍可离线编排全部本地可编译节点，但不能把“可编辑”冒充“当前可运行”；前端不能编译或只能走 legacy adapter 的节点不会出现在任何新建入口中。

### 离线编辑边界

流程工作台的新建、打开、撤销、重做、属性编辑、本地结构校验和保存都在 renderer 内完成，不需要网络、模型或 Python Runtime。保存只写一个 canonical `.automation.json`，未保存草稿通过本地持久化恢复；打开文件会先解析并校验，只有真正要替换脏文档时才请求确认。

“完全离线执行”在这里指不访问外部网络、不调用模型：确定性的 `replay_step` 和本地原生节点仍由机器上的 Python Runtime、Replayer 与 Desktop Action Queue 执行。Electron 单进程预览、计时器或日志不能冒充真实桌面执行；Runtime 不在或源码漂移时只能继续编辑，不能运行。

Studio 提供显式的“一句话生成片段”辅助，但不进入 Agent 默认执行线。只有用户在编辑器中主动提交时，后端才创建一次短生命周期的 `FlowFragmentProposer`：它复用现有 provider/model/protocol/budget plumbing，但不是第二个 `LiveVisualActor`，主 Actor 也不承担图编排职责。请求只有一条 user JSON，不含 system/developer prompt，不读取桌面、Thread、连接器或完整文档，只收到用户原话、当前图的节点计数/类型摘要、基线 fingerprint 和小型本地可编译机械节点目录。它没有桌面、连接器或执行权限，不增加 Agent 默认提示词、默认工具面、Planner 或任务分类器。

模型只能返回一个严格 schema 的无环片段草稿。Renderer 校验节点类型、参数、端口和基线 fingerprint，先展示摘要、节点、参数与连线；用户确认后才把片段放到当前图右侧空白区，并形成一次可撤销 checkpoint。它不会自动连接既有节点、覆盖文档、保存或运行。这个辅助调用需要模型和网络；新建、打开、编辑、拼接、撤销、保存以及确定性节点的本地执行仍保持完全离线。不得接回 `src/ai-interface`、旧 DSL 生成器或 Electron HTTP 直接写图路径。

片段尚未拼接时没有可证明的上游窗口目标，所以片段预览只用一个通用 synthetic `focus_window` 检查候选节点自身的参数与结构；该节点不会写入用户流程。片段接入主图后，完整文档校验仍按每条可达路径要求真实 `focus_window`，不会因预览通过而放宽裸坐标动作的运行边界。

2026-08-02 可见 Electron 探针从真实 Studio 提交“点击坐标 420,300，等待 1 秒，然后输入文本 hello”，单次调用返回 `click -> wait -> input_text` 三节点/两连线；确认后片段出现在右侧空白区，没有自动连接、保存或运行，随后 `Ctrl+Z` 把图恢复到原来的 16 节点/15 连线/14 动作。这证明 opt-in 生成、预览、应用和撤销链，不证明任意自然语言都能正确编排，也不改变离线节点的逐项 E2E 要求。

同日还在 Electron 默认约 `900 x 580` 的真实窗口尺寸复测了普通用户路径。流程工作台在
`800--960px` 范围内使用左右抽屉而不是把三列硬挤进约 400px 的画布；片段对话框按画布
可视区域而不是整个窗口居中，`Esc` 只关闭该对话框，页眉与确认区在小窗口中保持可见。
用户口语输入“等半秒，然后按一下回车”得到 `wait(0.5) -> keyboard(enter)`，应用后一次
`Ctrl+Z` 删除整个片段，一次 `Ctrl+Y` 恢复。这是编辑器交互证据，不是节点执行 E2E。

运行边界必须对用户可见：校验或启动期间会锁定会替换文档或历史的命令；运行已经开始后
仍可编辑下一版本，但工作台明确提示当前 run 使用启动时快照。任何画布改动只影响下一次
运行，不能让用户误以为正在运行的 graph 被热修改。异步 Runtime 校验返回后，前端用剔除
`compiled_at` 等瞬时元数据的稳定文档 fingerprint 复核启动快照：未修改的流程可正常启动，
校验期间被修改的流程会丢弃旧结果。

属性面板只展示能准确投影到 canonical ARP 的参数。`evidence_gate` 的结构化条件以 JSON
数组编辑并保留对象字段；鼠标文字点击、坐标点击、滚动和键盘序列只暴露已由编译契约
覆盖的选项。键盘既兼容历史 `keys: ['ctrl', 'c']` 和弦，也支持明确
`modifiers: ['ctrl'], keys: ['c', 'v']` 序列；窗口等待超时在 UI 和编译边界统一限制为
`(0, 120]` 秒，无主键、超限和水平滚动都 fail closed。历史图片点击分支以及截图区域仍未
贯通当前 canonical 执行线，
因此保持迁移提示或 fail closed，不能用旧 UI 控件冒充可运行能力。当前 `screenshot` 节点
只产生受管理的全屏工件；直接框选截图、跨显示器/DPI 坐标与裁剪后工件尺寸必须先通过
独立 oracle E2E，才可作为正式区域/图片参数开放。

## 执行

路径：`python/engine/`。

- `dsl_parser.py`：DSL 解析。
- `dsl_flow_converter.py`：FlowScript 与 DSL 互转。
- `executor.py`：旧 DSLExecutor，仍支持大量节点和系统动作。
- `executor_types.py`：旧 DSLExecutor 的状态、上下文和控制流异常。
- `engine.py` / `unified_entry.py`：执行入口。

`python/engine/executor.py` 仍很大，但共享类型已拆到 `executor_types.py`。它应作为 legacy Flow/DSL 执行器维护，不继续塞 WinPilot 能力。

当前生产运行链：

```text
Flow editor state -> automation-ir/1(runtime.arp_graph) -> validation -> RuntimeJob
```

`toAutomationIR()` 只编译一次当前 Flow state，并把 `arp/1` graph 存入 `runtime`；Studio
直接把该 IR 提交到 `/api/v2/automation-ir/runs`。保存默认写 `.automation.json`；旧
FlowScript/WPS 可导入。`store/executor.ts` 与 `python/engine/` 仍是兼容执行入口，不是新工作台
的主运行时。

当前可放入画布的节点包括 action batch、verified skill replay、replay step、evidence/human gate、
基础 GUI action、condition、delay 和 set variable。历史 `agent_goal` 只可读取，validation 会给出
迁移错误。预置模板与其搜索元数据由同一 registry 生成；未知模板 ID 会报错，不再静默回退。

运行前还有两层兼容检查：

- Automation IR 必须通过当前 steps/variables/graph 的结构和参数校验；不再维护 projection fingerprint。
- renderer 只接受 runtime contract v3，且要求 Python 进程 loaded/current source stamp 与 Git
  revision 一致。源码更新后旧服务会被标记为 drifted，流程工作台禁止继续提交并要求重启。

空间动作还有一条与应用无关的目标合同。Renderer 和 Python graph contract 都沿图路径追踪
“是否已建立真实目标”：`focus_window` 可以在同一 `action_batch` 的空间动作之前建立目标，
也可以在前一节点建立后供后续节点使用；如果空间动作早于目标建立，或分支合流的任一路径
没有目标，校验必须 fail closed。同一 ARP run 的多个 `action_batch` 共享一个
`FastGuiController`，所以目标绑定可以跨节点保留，并在 run 结束时统一释放。这是单条物理执行线的会话
连续性，不是任务分类、窗口配方或第二套 controller。

这两个 fingerprint 解决的是不同问题：前者保护 artifact 投影，后者保护正在运行的解释器；不能只通过其中一个就认为运行环境可信。

可靠性边界：

- `INPUT_TEXT`、`CLICK_TEXT`、`CLICK_IMAGE` / `IMAGE_CLICK` 已在 legacy `DSLExecutor` 中落到现有 `click`、`type`、`image_click` action handler，不再解析成功后静默 no-op。
- 前端 `store/executor.ts` 是 legacy FlowScript 兼容执行器，不等同于 WinPilot Replayer，也不是新流程的发布路径。Electron 模式会明确标记兼容运行；无 Electron bridge 时统一设置 `require_real_features=true`，缺少真实能力必须失败，浏览器计时或日志模拟不能报告流程成功。
- `tool_call`、process/window/dialog/a11y 等历史节点可以留在导入 artifact 中供检查和迁移，但不会自动出现在生产节点库或统一搜索中。新流程若确需这类能力，应先增加后端 capability、ARP 编译映射和契约测试，再开放 UI。
- ARP `action_batch`、legacy bridge 和 Agent 都共享 Desktop Action Queue。真实鼠标/键盘仍是 `physical` 全局单写；`controller.multi_input_cooperation` 默认关闭，开启后只增加人类优先的焦点/光标仲裁，不会虚拟出第二套输入设备。浏览器预览无法证明这条真实设备路径。
- 原生视觉 `agent_goal` 收到的画面可能是经过验证的目标窗口裁剪，也可能是完整虚拟桌面。`controller.target_window_capture_enabled` 是可关闭的性能优化，跨窗口、全局或含糊任务会回退全桌面；Flow/IR 不应把某次截图像素尺寸固化成永久物理坐标。

## @feature

路径：`python/features/`。

`@feature` 注册表仍服务：

- Python MCP。
- Electron HTTP API 的 `/api/features/*`。
- DSL `tool_call` / 旧集成。

它不再自动生成生产流程图节点。动态 feature 只有在显式进入 capability catalog、获得稳定参数 schema、ARP 映射和失败语义后，才可加入节点库。

renderer 的统一搜索不再维护动态 feature 索引或“功能”结果分支；它只搜索可执行模板与 catalog 允许的新节点。Electron `features:list` 仍为已保存的 legacy `tool_call` 属性面板提供参数 schema，这是一条读取/编辑兼容路径，不是生产节点发现机制。

不要把这套注册表直接暴露给 WinPilot 主 GUI agent。

真实边界：

- `FlowScript` 是前端画布 JSON；`DSL` 是旧文本编排入口；`python/engine/` 负责 legacy DSL/Flow 执行，不是 WinPilot 主 agent 的低延迟 controller。
- `@feature` 是 MCP/HTTP/CLI 和 legacy DSL 的工具注册表。`FeatureSpec.flow_type` 只声明“这个 DSL 关键字应导入成哪个兼容 FlowScript 节点”，不是生产可执行能力证明；参数 shape 仍必须和 `types/index.ts`、`nodeDefinitions.ts`、`dsl_flow_converter.py` 对齐。
- 旧注册表里已知例子：`CLICK -> click`，`DRAG -> swipe`。不要把拖拽类动作声明成 `click` 节点，否则编辑器属性面板和 executor 会拿到错误参数。
- `tool_call` 只表示“交给 feature 桥”。如果 payload 带 `_requires_wps_replay`，它是 WinPilot-only `.wps.json` 占位，前端 executor 会报错而不是假装成功；未知 WPS step 和 `while` / `until` replay 控制流也走这个保守路径。

## 修改节点时必须同步

如果新增/修改节点类型，至少检查：

- `src/renderer/types/index.ts`
- `src/renderer/utils/nodeDefinitions.ts`
- `src/renderer/platform/flowToArpGraph.ts`
- `src/renderer/components/PropertyPanel/`
- `python/winpilot_home/winpilot/orchestrator/catalog.py`
- `src/renderer/platform/contracts.ts`
- `src/renderer/store/executor.ts`
- `python/engine/dsl_flow_converter.py`
- `python/engine/executor.py`
- `python/engine/executor_types.py`
- 相关 vitest / Python 测试

## 与 WinPilot 的桥

WinPilot trace 可以生成规范 `.automation.json`，并保留 `.wps.json` 兼容导出。
`src/renderer/utils/automationIR.ts` 负责 IR/Flow/WPS adapter 与运行图结构校验；当前不再计算或
核对 projection fingerprint。Agent 不需要直接理解 FlowScript 内部结构。

这个桥只做导入、检查和迁移，不改变 WinPilot 的重放来源：离线 0-token 重放仍由 `python/winpilot_home/winpilot/recorder/trace.py` 的 Replayer 执行。`draw_strokes`、`run_detached`、`tray_click`、未知步骤以及 `while` / `until` 控制流导入 FlowScript 时会保留为带 `_requires_wps_replay` 的占位；占位不等于 `@feature` executor 已经能运行，也不会进入新流程节点库。WPS 导入和浏览器兼容执行都要求真实 feature；不能桥接或桥接失败时应让流程失败。

## 计划与证据边界

- Flow/ARP graph 是用户编辑并保存的编排 artifact，不等同于在线 Agent 的内部工作计划。
  历史 `agent_goal` 节点没有执行 handler，只能显示迁移错误。
- `action_batch` 返回成功、画面发生变化、preview 走完、replay 结束或 graph 到达 `end` 都不能
  替代外部业务结果。确定性图可以用显式 evidence gate 管理自己的结构化条件；测试仍必须用
  Runtime 之外的 oracle 判定用户目标。
- managed `screenshot` 在真实工件具有 `artifact_id` 和 SHA-256 时可以产生
  `artifact.screenshot` 完成证据，让确定性 graph 到达可审计终态；它只证明截图工件被捕获，
  不能单独证明同一图中的点击、输入或外部业务结果。那些语义仍需 Runtime 之外的 oracle。
