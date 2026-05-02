/**
 * 灵动脚本 AI Interface — 核心类型定义
 *
 * Tool: 对外暴露的"能力单元"，每个 tool 有名称、描述、输入输出 schema。
 * 所有 AI（Claude Code / ChatGPT / 等）通过统一的 Tool 协议调用。
 */

/** Tool 输入参数的 JSON Schema 定义 */
export interface ToolParameterProperty {
  type: 'string' | 'number' | 'boolean' | 'array' | 'object';
  description: string;
  enum?: string[];
  default?: any;
  required?: boolean;
}

export interface ToolInputSchema {
  type: 'object';
  properties: Record<string, ToolParameterProperty>;
  required: string[];
}

/** Tool 定义 — 告诉 AI 有什么能力、怎么用 */
export interface ToolDefinition {
  /** 工具名称（AI 调用时用） */
  name: string;
  /** 工具描述（告诉 AI 这个工具做什么、何时用） */
  description: string;
  /** 输入参数的 JSON Schema */
  inputSchema: ToolInputSchema;
  /** 优先级提示：告诉 AI 优先使用这个工具的程度 (high/medium/low) */
  priority: 'high' | 'medium' | 'low';
  /** 使用示例，帮助 AI 理解调用模式 */
  examples: string[];
  /** 所属分类 */
  category: string;
}

/** Tool 执行结果 */
export interface ToolResult {
  success: boolean;
  data?: any;
  error?: string;
  /** 人类可读的执行摘要 */
  summary?: string;
}

/** Tool 执行函数签名 */
export type ToolHandler = (args: Record<string, any>) => Promise<ToolResult>;

/** 完整的 Tool 单元（定义 + 实现） */
export interface Tool {
  definition: ToolDefinition;
  handler: ToolHandler;
}

/** MCP JSON-RPC 消息类型 */
export interface MCPRequest {
  jsonrpc: '2.0';
  id: number | string;
  method: string;
  params?: any;
}

export interface MCPResponse {
  jsonrpc: '2.0';
  id: number | string;
  result?: any;
  error?: {
    code: number;
    message: string;
    data?: any;
  };
}

/** HTTP API 标准响应 */
export interface APIResponse {
  success: boolean;
  data?: any;
  error?: string;
  summary?: string;
  tool?: string;
  timestamp: string;
}

/** 服务模式 */
export type ServerMode = 'mcp' | 'http' | 'both';
