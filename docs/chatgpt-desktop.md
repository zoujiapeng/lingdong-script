# ChatGPT / Codex 桌面桥接（已移除）

WinPilot 曾挂载本地 Streamable HTTP `/mcp`，让 ChatGPT/Codex 作为另一层桌面规划器。
该产品线及 `winpilot/integrations/chatgpt_mcp.py` 已删除；
`GET /api/v2/integrations/chatgpt` 只返回 `removed` 状态，不再提供连接端点或桌面工具。

当前 connector 方向相反：同一个 `LiveVisualActor` 在首次完整桌面 JPEG 调用中，通过
`ConnectorGateway` 使用显式配置的外部 MCP。connector、shell、回复和 GUI 都留在同一
Actor 内，不创建第二个规划器或第二条桌面执行线。

Electron `:7880/mcp` 仍是流程编辑兼容面，不是自然语言 Agent 或 ChatGPT 桌面桥接。
