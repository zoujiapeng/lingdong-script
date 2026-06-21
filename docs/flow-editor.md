# 流程图编辑器与执行引擎

流程图体系是项目的零-token执行主线。它与 WinPilot Agent 解耦，但可以导入 WinPilot 产生的 `.wps.json`。

## 前端

主要代码在 `src/renderer/`。

| 模块 | 说明 |
|---|---|
| `App.tsx` | 顶层布局、视图切换、快捷键、运行入口 |
| `store/` | Zustand + immer 状态、FlowScript 操作、执行状态 |
| `components/Canvas/` | 画布、节点、连线 |
| `components/PropertyPanel/` | 节点参数面板 |
| `components/NodeLibrary/` | 节点库 |
| `components/ReplayEditorView.tsx` | trace/脚本导入和录制入口 |
| `components/AgentView.tsx` | 内嵌 WinPilot webui |
| `utils/nodeDefinitions.ts` | 节点定义 |
| `utils/wpsToFlowScript.ts` | `.wps.json` 转 FlowScript |
| `utils/pseudoParser.ts` | 伪代码/DSL 辅助 |

## 节点类型

当前 `src/renderer/types/index.ts` 的 `NodeType` 包括：

- 基础：`start`、`end`、`click`、`wait`、`input_text`、`keyboard`、`swipe`
- 视觉/感知：`image_click`、`click_text`、`screenshot`、`color_check`、`progress_wait`、`region_wait`、`scene_condition`
- 控制流：`condition`、`loop`、`sub_script`
- 数据：`variable_set`、`variable_get`
- 系统：`shutdown`、`reboot`、`logoff`、`hibernate`、`lock_workstation`、`launch_program`、`kill_process`、`wait_process`、`focus_window`、`set_wallpaper`、`display_off`、`system_sleep`、`show_desktop`、`battery_status`、`empty_recycle_bin`、`display_brightness`
- 剪贴板/窗口/浏览器：`clipboard_set`、`clipboard_get`、`window_control`、`web_browser`
- 聚合节点：`system_power`、`audio_action`、`desktop_action`、`clipboard_action`、`process_action`、`mouse_operation`
- 对话框/无障碍/WiFi：`dialog_action`、`a11y_action`、`wifi_action`
- 摄像头：`camera_capture`、`camera_record`
- 扩展：`popup_close`、`tool_call`

当前 `NodeType` 为 54 个；`src/renderer/types/index.ts` 和 `utils/nodeDefinitions.ts` 是实际来源。

## FlowScript 与 DSL

前端保存的是 FlowScript，Python engine 执行前会转换为 DSL。

关键代码：

- `python/engine/dsl_parser.py`
- `python/engine/dsl_flow_converter.py`
- `python/engine/executor.py`
- `python/engine/engine.py`
- `tools/dsl_to_flow.py`

控制流通过边端口表达：

- `condition` 使用 `yes` / `no`
- `loop` 使用 `loop_body` / `loop_end`
- 普通节点使用默认输出端口

## 执行引擎

`python/engine/engine.py` 作为 stdin/stdout 子进程由 Electron 主进程管理。实际执行在 `executor.py`：

- 内置关键字走专门 `_exec_*` 实现。
- 其它带 `dsl_keyword` 的能力可通过 @feature 注册表兜底。
- Dialog/menu helper 仍在 `python/agent/dialog_handler.py` 和 `python/agent/menu_handler.py`，这些文件虽然在 `python/agent/` 下，但不是旧 LingdongAgent 主循环。

## @feature 注册表

`python/features/registry.py` 提供 `@feature` 装饰器和统一注册表。`python/features/_all.py` 控制懒加载模块列表。

@feature 能被导出为：

- Python MCP 工具
- Electron HTTP `/api/features/call`
- DSL 关键字
- CLI/内部调用
- 流程图 `tool_call`

WinPilot 主 agent 不使用 @feature。这个边界是项目性能和可维护性的关键。

## Node-RED

`python/node-red-nodes/` 是 Node-RED 集成实验。`package.json` 仍通过 `file:python/node-red-nodes/lobster` 引用它，但当前结论是保留 React 流程图，不用 Node-RED 替换主画布。详见 `python/node-red-nodes/EVALUATION.md`。
