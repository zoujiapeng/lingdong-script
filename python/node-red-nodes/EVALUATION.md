# Node-RED 集成评估

## 结论

当前不使用 Node-RED 替换主流程图画布。`python/node-red-nodes/` 保留为实验/PoC，`package.json` 仍通过 `file:python/node-red-nodes/lobster` 引用本地节点包。

## 当前 PoC 内容

目录 `python/node-red-nodes/lobster/` 包含 3 个自定义节点：

- `lobster-click`
- `lobster-type`
- `lobster-run-dsl`

这些节点的设计意图是通过 HTTP 调用本项目后端能力。当前项目主 HTTP API 是 Electron 的 `127.0.0.1:7880`，实际可用端点以 `src/main/api.ts` 为准，旧评估中提到的 `:5877` 不再作为当前事实使用。

## 为什么不替换 React 画布

- React 画布已经和 FlowScript、属性面板、模板、快捷键、执行状态、导入录制深度绑定。
- Node-RED 的 flow 格式和当前 FlowScript 不同，完整迁移成本高。
- 桌面 GUI 自动化需要区域拾取、OCR、窗口/进程、Agent IPC、录制导入等专用交互，Node-RED 需要大量定制。
- 当前目标是提升 WinPilot 通用桌面操控能力，而不是更换画布框架。

## 可保留用途

- 作为外部编排实验。
- 作为 Node-RED 用户接入本项目能力的参考。
- 未来若需要 IoT/HTTP/MQTT 生态集成，可单独完善，不影响主流程图编辑器。
