# 流程图 / DSL / @feature

这套系统和 WinPilot agent 并存，但不是同一个工具体系。

## 前端

路径：`src/renderer/`。

核心内容：

- `types/index.ts`：FlowScript 类型和 `NodeType`。
- `utils/nodeDefinitions.ts`：节点定义。
- `components/PropertyPanel/`：参数编辑。
- `store/defaults.ts`：默认设置和预置模板。
- `store/persistence.ts`：localStorage 持久化 helper。
- `store/index.ts`：Zustand store；不再复制 defaults/templates/persistence。

当前 `NodeType` 数量：54。

## 执行

路径：`python/engine/`。

- `dsl_parser.py`：DSL 解析。
- `dsl_flow_converter.py`：FlowScript 与 DSL 互转。
- `executor.py`：旧 DSLExecutor，仍支持大量节点和系统动作。
- `executor_types.py`：旧 DSLExecutor 的状态、上下文和控制流异常。
- `engine.py` / `unified_entry.py`：执行入口。

`python/engine/executor.py` 仍很大，但共享类型已拆到 `executor_types.py`。它应作为 legacy Flow/DSL 执行器维护，不继续塞 WinPilot 能力。

当前可靠性边界：

- `INPUT_TEXT`、`CLICK_TEXT`、`CLICK_IMAGE` / `IMAGE_CLICK` 已在 legacy `DSLExecutor` 中落到现有 `click`、`type`、`image_click` action handler，不再解析成功后静默 no-op。
- 前端 `store/executor.ts` 是浏览器侧 FlowScript 模拟/桥接执行器，不等同于 WinPilot Replayer。`wait_process` 会通过 `wait_process_start` / `wait_process_exit` feature 桥接；feature 返回 `success: false` 时应视为节点失败。

## @feature

路径：`python/features/`。

`@feature` 注册表服务：

- 流程图节点能力。
- Python MCP。
- Electron HTTP API 的 `/api/features/*`。
- DSL `tool_call` / 旧集成。

不要把这套注册表直接暴露给 WinPilot 主 GUI agent。

真实边界：

- `FlowScript` 是前端画布 JSON；`DSL` 是旧文本编排入口；`python/engine/` 负责 legacy DSL/Flow 执行，不是 WinPilot 主 agent 的低延迟 controller。
- `@feature` 是流程图/MCP/HTTP/CLI 的工具注册表。`FeatureSpec.flow_type` 只声明“这个 DSL 关键字应导入成哪个 FlowScript 节点”，必须和 `types/index.ts`、`nodeDefinitions.ts`、`dsl_flow_converter.py` 里的参数 shape 对齐。
- 旧注册表里已知例子：`CLICK -> click`，`DRAG -> swipe`。不要把拖拽类动作声明成 `click` 节点，否则编辑器属性面板和 executor 会拿到错误参数。
- `tool_call` 只表示“交给 feature 桥”。如果 payload 带 `_requires_wps_replay`，它是 WinPilot-only `.wps.json` 占位，前端 executor 会报错而不是假装成功。

## 修改节点时必须同步

如果新增/修改节点类型，至少检查：

- `src/renderer/types/index.ts`
- `src/renderer/utils/nodeDefinitions.ts`
- `src/renderer/components/PropertyPanel/`
- `src/renderer/store/executor.ts`
- `python/engine/dsl_flow_converter.py`
- `python/engine/executor.py`
- `python/engine/executor_types.py`
- 相关 vitest / Python 测试

## 与 WinPilot 的桥

WinPilot 成功 trace 可以导出 `.wps.json`，再由 `src/renderer/utils/wpsToFlowScript.ts` 转成 FlowScript。Agent 不需要直接理解 FlowScript 内部结构。

这个桥只做导入/编辑，不改变 WinPilot 的重放来源：离线 0-token 重放仍由 `python/winpilot_home/winpilot/recorder/trace.py` 的 Replayer 执行。`draw_strokes`、`run_detached`、`tray_click` 等 WinPilot-only 步骤导入 FlowScript 时会保留为带 `_requires_wps_replay` 的 `tool_call` payload，占位不等于旧 `@feature` executor 已经能直接运行。
