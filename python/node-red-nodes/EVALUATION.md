# Node-RED 集成评估报告

## 基本信息
- **评估日期**: 2026-05-16
- **Node-RED 版本**: 4.1.10
- **目标**: 评估用 Node-RED 替换 React Flow 前端画布的可行性

## 测试结果

| 项目 | 状态 | 备注 |
|------|------|------|
| npm install node-red | ✅ | 233 个依赖包, 无冲突 |
| 启动/停止 | ✅ | 内嵌 Express HTTP 服务器 |
| Admin UI 加载 | ✅ | http://localhost:1880/admin |
| HTTP API 通信 | ✅ | 可通过 :5877 调用 lobster 后端 |
| 自定义节点创建 | ✅ | lobster-click / lobster-type / lobster-run-dsl 3 个节点 |
| Electron 嵌入 | ⚠️ | 技术上可行（webview/iframe），但需要处理端口冲突和 CSP |
| 完整节点迁移 | ❌ | 127 个自定义节点需逐一重写为 Node-RED 格式 |

## 评估细节

### 1. Node-RED 启动测试
- 内嵌模式通过 `RED.init()` + `RED.start()` 正常启停
- 提供 Admin UI（httpAdminRoot）和 HTTP API（httpNodeRoot）两个挂载点
- 日志级别可配置（warn/info/debug/trace）

### 2. 自定义节点测试
创建了 3 个 PoC 节点：
- `lobster-click` — 屏幕坐标点击，调用 `POST /api/features/call {name: "click_target", params: {x, y}}`
- `lobster-type` — 键盘输入，调用 `POST /api/features/call {name: "type_text", params: {text}}`
- `lobster-run-dsl` — DSL 脚本执行，调用 `POST /api/features/call {name: "run_dsl", params: {dsl}}`

所有节点通过 Node-RED 的 npm 模块发现机制加载（`node-red-contrib-lobster`）。

### 3. 与现有后端通信测试
- Node-RED 自定义节点通过 `http.request` 调用 `127.0.0.1:5877/api/features/call`
- 响应正常解析为 `msg.payload` 传递给下游节点
- 支持异步非阻塞执行

### 4. Electron 集成可行性
**可行方案**：
- 在 Electron 中启动内嵌 Node-RED 服务器（同进程或子进程）
- 通过 `<webview>` 或 `<iframe>` 嵌入 Admin UI
- 通过 Electron IPC 或直接 HTTP 调用与 Node-RED 通信

**风险**：
- 端口冲突（现有 :5877 + Node-RED :1880）
- CSP 限制（生产模式从 file:// 加载，跨域访问 localhost:1880 可能被阻止）
- 双 HTTP 服务器增加内存消耗
- Node-RED 的 flow 格式与现有 FlowScript 格式不同
- 需要维护两套节点定义（React + Node-RED）或实现完整迁移

## 结论与建议

### 不建议将 React Flow 替换为 Node-RED

1. **成本 vs 收益不成比例**：
   - 需重写 127 个自定义节点为 Node-RED 格式
   - 需重写属性面板、节点库、参数编辑器等所有前端组件
   - 现有代码（nodeDefinitions.ts, PropertyPanel/*.tsx, FlowNode.tsx）完全废弃

2. **架构不匹配**：
   - Node-RED 是为 IoT/设备自动化设计的（MQTT, HTTP, GPIO），而非桌面 GUI 自动化
   - 当前系统关键特性（OCR 区域拾取、滚轮选择器、宏录制、AI Agent IPC）没有 Node-RED 对应物
   - 这些特性需要大量自定义 Node-RED 插件开发

3. **运行模式冲突**：
   - Node-RED 是持久化服务器，当前系统是桌面应用按需启动
   - 生产模式（file://）下嵌入外部 HTTP 服务有 CSP 问题
   - Node-RED 的流程部署机制（deploy button）与现有直接执行模式不同

### Node-RED 的潜在用途

Node-RED 更适合作为**外部编排工具**，而非替换前端画布：
- 用户可以通过 Node-RED 编排复杂的跨应用自动化流程
- 将 lobster 暴露为 Node-RED 的子流程/函数节点
- 与 Home Assistant、智能家居等 IoT 系统集成

### 最终决定
Phase 4 结论：**保留 React Flow，不做 Node-RED 迁移**。
将本项目维持在 `claude/` 目录中，作为评估参考。
