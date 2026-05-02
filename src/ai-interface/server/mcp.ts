/**
 * MCP stdio Server — Model Context Protocol 实现
 *
 * 此服务器通过标准输入/输出与 Claude Code 通信，实现工具调用。
 * 协议：JSON-RPC 2.0 over stdio
 *
 * Claude Code 会自动启动此服务器，并通过 stdio 发送工具调用请求。
 *
 * 支持的 MCP 方法：
 *   - initialize          — 初始化连接
 *   - tools/list          — 获取工具列表
 *   - tools/call          — 调用工具
 *   - notifications/initialized — 初始化完成通知（忽略）
 *
 * 启动：node dist/ai-interface/server/mcp.js
 */

import * as readline from 'readline';
import { toolRegistry } from '../tools/registry';
import { MCPRequest, MCPResponse } from '../tools/types';

export async function startMCPServer(): Promise<void> {
  const rl = readline.createInterface({
    input: process.stdin,
    output: process.stdout,
    terminal: false,
  });

  // 标准 MCP 协议初始化响应
  const serverInfo = {
    name: 'lingdong-script',
    version: '1.0.0',
  };

  const capabilities = {
    tools: {},  // 声明支持工具能力
  };

  rl.on('line', async (line: string) => {
    let request: MCPRequest;
    try {
      request = JSON.parse(line);
    } catch {
      // 非 JSON 忽略
      return;
    }

    if (request.jsonrpc !== '2.0') return;

    const id = request.id;

    try {
      switch (request.method) {
        case 'initialize':
          sendResponse({
            jsonrpc: '2.0',
            id,
            result: {
              protocolVersion: '2024-11-05',
              serverInfo,
              capabilities,
            },
          });
          break;

        case 'notifications/initialized':
          // 忽略，无响应
          break;

        case 'tools/list':
          sendResponse({
            jsonrpc: '2.0',
            id,
            result: {
              tools: toolRegistry.toMCPTools(),
            },
          });
          break;

        case 'tools/call': {
          const { name, arguments: args } = request.params || {};
          if (!name) {
            sendError(id, -32602, '缺少工具名称 (name)');
            break;
          }
          const result = await toolRegistry.call(name, args || {});
          sendResponse({
            jsonrpc: '2.0',
            id,
            result: {
              content: [
                {
                  type: 'text',
                  text: JSON.stringify(result, null, 2),
                },
              ],
              isError: !result.success,
            },
          });
          break;
        }

        default:
          sendError(id, -32601, `不支持的方法: ${request.method}`);
      }
    } catch (err: any) {
      sendError(id, -32603, `内部错误: ${err.message}`);
    }
  });

  rl.on('close', () => {
    process.exit(0);
  });

  // 进程退出处理
  process.on('SIGINT', () => process.exit(0));
  process.on('SIGTERM', () => process.exit(0));
}

function sendResponse(response: MCPResponse): void {
  process.stdout.write(JSON.stringify(response) + '\n');
}

function sendError(id: number | string, code: number, message: string): void {
  sendResponse({
    jsonrpc: '2.0',
    id,
    error: { code, message },
  });
}
