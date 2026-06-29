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

## @feature

路径：`python/features/`。

`@feature` 注册表服务：

- 流程图节点能力。
- Python MCP。
- Electron HTTP API 的 `/api/features/*`。
- DSL `tool_call` / 旧集成。

不要把这套注册表直接暴露给 WinPilot 主 GUI agent。

## 修改节点时必须同步

如果新增/修改节点类型，至少检查：

- `src/renderer/types/index.ts`
- `src/renderer/utils/nodeDefinitions.ts`
- `src/renderer/components/PropertyPanel/`
- `python/engine/dsl_flow_converter.py`
- `python/engine/executor.py`
- `python/engine/executor_types.py`
- 相关 vitest / Python 测试

## 与 WinPilot 的桥

WinPilot 成功 trace 可以导出 `.wps.json`，再由 `src/renderer/utils/wpsToFlowScript.ts` 转成 FlowScript。Agent 不需要直接理解 FlowScript 内部结构。
