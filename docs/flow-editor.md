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

`NodeType` 联合类型包含现役节点和历史兼容类型，不等于生产运行能力。节点库、统一搜索和“最近使用”都只展示 `flowToArpGraph.ts` 的可编译集合与后端 capability catalog 的 `flow_node_types` 交集；后端未声明、前端不能编译或只能走 legacy adapter 的节点不会出现在任何新建入口中。catalog 尚未加载时交集为空，前端不会用硬编码最近列表绕过运行前可达性。

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
Flow editor state -> automation-ir/1(runtime.arp_graph + fingerprint) -> validation -> RuntimeJob
```

`toAutomationIR()` 只编译一次当前 Flow state，并把 `arp/1` graph 与 projection fingerprint 存入 `runtime`；Studio 直接把该 IR 提交到 `/api/v2/automation-ir/runs`。保存默认写 `.automation.json`；旧 FlowScript/WPS 可导入。`store/executor.ts` 与 `python/engine/` 仍是兼容执行入口，不是新工作台的主运行时。

当前可放入生产画布的节点包括 agent goal、action batch、verified skill replay、replay step、evidence/human gate、基础 GUI action、condition、delay 和 set variable。预置模板与其搜索元数据由同一 registry 生成；未知模板 ID 会报错，不再静默回退到第一个模板。默认模板都能完整编译成非 legacy ARP graph，并包含与目标一致的证据 gate。

运行前还有两层独立兼容检查：

- Automation IR 的 projection fingerprint 必须与当前 steps/variables/graph 一致，避免陈旧投影或被篡改的 graph 运行。
- renderer 只接受 runtime contract v2，且要求 Python 进程启动时源码指纹、当前源码指纹和 Git revision 一致。源码更新后旧服务会被标记为 drifted，流程工作台禁止继续提交并要求重启。

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

WinPilot 成功 trace 会生成规范 `.automation.json`，并保留 `.wps.json` 兼容导出。`src/renderer/utils/automationIR.ts` 负责 IR/Flow/WPS adapter、运行投影和 fingerprint；后端会重新计算 fingerprint，拒绝 steps/variables/graph 不一致的陈旧投影。Agent 不需要直接理解 FlowScript 内部结构。

这个桥只做导入、检查和迁移，不改变 WinPilot 的重放来源：离线 0-token 重放仍由 `python/winpilot_home/winpilot/recorder/trace.py` 的 Replayer 执行。`draw_strokes`、`run_detached`、`tray_click`、未知步骤以及 `while` / `until` 控制流导入 FlowScript 时会保留为带 `_requires_wps_replay` 的占位；占位不等于 `@feature` executor 已经能运行，也不会进入新流程节点库。WPS 导入和浏览器兼容执行都要求真实 feature；不能桥接或桥接失败时应让流程失败。

## 计划与证据边界

- Flow/ARP graph 是用户编辑并保存的编排 artifact，不等同于 Agent 的 live todo。运行到 `agent_goal` 时，同一 `AgentRunner` 可用 `revise_plan` 随时替换、清空、增删、移动或重新打开自己的任务项；这些变化不会静默改写已保存的 Flow 节点。
- 客户端收到新的 `agent_plan` 快照后应整体更新显示，不能继续使用旧索引。计划项被标记完成只表示局部工作状态，不是 ARP `evidence_gate` 或最终任务成功证据。
- `action_batch` 返回成功、画面发生变化、preview 走完、replay 结束或 graph 到达 `end` 都不能替代目标 evidence。需要可信完成时，图中必须保留与用户目标匹配的 evidence gate，或由 `agent_goal` 的新鲜画面/确定性 done gate 核验。
