/**
 * 灵动脚本 AI Interface — 标准工具接口入口
 *
 * 统一对外暴露"工具调用协议"，两种模式：
 *   mcp   — MCP stdio 协议（供 Claude Code 直接调用）
 *   http  — HTTP REST API（供任何 AI / HTTP 客户端调用）
 *
 * 启动方式：
 *   node dist/ai-interface/index.js --mode mcp      # MCP stdio 模式
 *   node dist/ai-interface/index.js --mode http     # HTTP API 模式（默认）
 *   node dist/ai-interface/index.js --mode both     # 同时启动（HTTP 在后台）
 *   node dist/ai-interface/index.js --mode http --port 3456
 */

import { toolRegistry } from './tools/registry';
import { runFlowTool } from './tools/run-flow';
import { listFlowsTool } from './tools/list-flows';
import { createFlowTool } from './tools/create-flow';
import { askAITool } from './tools/ask-ai';
import { screenCaptureTool } from './tools/screen-capture';
import { startMCPServer } from './server/mcp';
import { startHTTPServer } from './server/http';
import { ServerMode } from './tools/types';

// 注册所有工具
toolRegistry.register(runFlowTool);
toolRegistry.register(listFlowsTool);
toolRegistry.register(createFlowTool);
toolRegistry.register(askAITool);
toolRegistry.register(screenCaptureTool);

function parseArgs(): { mode: ServerMode; port: number } {
  const args = process.argv.slice(2);
  const modeArg = args.find(a => a.startsWith('--mode='));
  const modeIndex = args.indexOf('--mode');
  let mode: ServerMode = 'http'; // 默认 HTTP

  if (modeArg) {
    const m = modeArg.split('=')[1] as ServerMode;
    if (['mcp', 'http', 'both'].includes(m)) mode = m;
  } else if (modeIndex >= 0 && modeIndex + 1 < args.length) {
    const m = args[modeIndex + 1] as ServerMode;
    if (['mcp', 'http', 'both'].includes(m)) mode = m;
  }

  const portIndex = args.indexOf('--port');
  let port = 3456;
  if (portIndex >= 0 && portIndex + 1 < args.length) {
    port = parseInt(args[portIndex + 1]) || 3456;
  }

  return { mode, port };
}

async function main() {
  const { mode, port } = parseArgs();

  // 注册所有工具
  console.error(`[ai-interface] 已注册 ${toolRegistry.listNames().length} 个工具: ${toolRegistry.listNames().join(', ')}`);
  console.error(`[ai-interface] 启动模式: ${mode}`);

  switch (mode) {
    case 'mcp':
      console.error('[ai-interface] 启动 MCP stdio 服务器 (for Claude Code)...');
      await startMCPServer();
      break;

    case 'http':
      console.error(`[ai-interface] 启动 HTTP API 服务器 (端口: ${port})...`);
      await startHTTPServer(port);
      break;

    case 'both':
      console.error(`[ai-interface] 同时启动 MCP + HTTP:${port}...`);
      // HTTP 在后台
      startHTTPServer(port).catch(err => {
        console.error(`[ai-interface] HTTP 服务器错误: ${err.message}`);
      });
      // MCP 在前台（stdio）
      await startMCPServer();
      break;
  }
}

main().catch(err => {
  console.error(`[ai-interface] 启动失败: ${err.message}`);
  process.exit(1);
});
