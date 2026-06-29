# Node-RED 集成评估

`python/node-red-nodes/` 只是实验性 PoC，不是当前主线架构。主流程图编辑器仍是 Electron + React 画布，运行能力由 `src/main/api.ts` 暴露在 `127.0.0.1:7880`。

当前本地节点包 `python/node-red-nodes/lobster/` 只包含三个示例节点：`lobster-click`、`lobster-type`、`lobster-run-dsl`。它们可以作为以后外部编排接入的参考，但不要把 Node-RED 当成 WinPilot Agent、FlowScript 或当前画布的替代方案。

保留理由很简单：React 画布已经绑定模板、属性面板、快捷键、执行状态、录制导入和 FlowScript；Node-RED 的数据结构不同，迁移会引入一套新的复杂度。除非后续明确要做 IoT/HTTP/MQTT 生态集成，否则这里保持 PoC 状态。
