/**
 * HTTP API Server — 通用 REST API（任何 AI / HTTP 客户端都可以调用）
 *
 * 端点：
 *   GET  /health              — 健康检查
 *   GET  /v1/tools            — 列出所有工具定义
 *   POST /v1/tools/:name      — 调用指定工具
 *   GET  /v1/prompt           — 获取系统提示词（AI 用）
 *   POST /v1/prompt           — 获取指定强度的提示词
 *   GET  /v1/prompt/switch    — 查看当前提示词开关状态
 *   POST /v1/prompt/switch    — 切换提示词强度
 *
 * 启动：node dist/ai-interface/server/http.js [--port 3456]
 */

import * as http from 'http';
import { toolRegistry } from '../tools/registry';
import { getSystemPrompt, PromptLevel } from '../prompt';
import { APIResponse } from '../tools/types';

let currentPromptLevel: PromptLevel = 'strong';

function jsonResponse(res: http.ServerResponse, status: number, data: any): void {
  res.writeHead(status, {
    'Content-Type': 'application/json',
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type, Authorization',
  });
  res.end(JSON.stringify(data));
}

function parseBody(req: http.IncomingMessage): Promise<any> {
  return new Promise((resolve, reject) => {
    const chunks: Buffer[] = [];
    req.on('data', (c: Buffer) => chunks.push(c));
    req.on('end', () => {
      const raw = Buffer.concat(chunks).toString();
      if (!raw) return resolve({});
      try {
        resolve(JSON.parse(raw));
      } catch {
        resolve({});
      }
    });
    req.on('error', reject);
  });
}

export async function startHTTPServer(port: number = 3456): Promise<void> {
  const server = http.createServer(async (req, res) => {
    // CORS preflight
    if (req.method === 'OPTIONS') {
      res.writeHead(204, {
        'Access-Control-Allow-Origin': '*',
        'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
        'Access-Control-Allow-Headers': 'Content-Type, Authorization',
      });
      res.end();
      return;
    }

    const reqUrl = new URL(req.url || '/', `http://${req.headers.host || 'localhost'}`);
    const pathname = reqUrl.pathname;
    const method = req.method || 'GET';

    try {
      // --- GET /health ---
      if (method === 'GET' && pathname === '/health') {
        const response: APIResponse = {
          success: true,
          data: {
            status: 'ok',
            version: '1.0.0',
            server: 'lingdong-script-ai-interface',
            promptLevel: currentPromptLevel,
            toolCount: toolRegistry.listNames().length,
            availableTools: toolRegistry.listNames(),
          },
          tool: 'health',
          timestamp: new Date().toISOString(),
        };
        jsonResponse(res, 200, response);
        return;
      }

      // --- GET /v1/tools ---
      if (method === 'GET' && pathname === '/v1/tools') {
        const response: APIResponse = {
          success: true,
          data: {
            tools: toolRegistry.getDefinitions(),
            count: toolRegistry.listNames().length,
          },
          timestamp: new Date().toISOString(),
        };
        jsonResponse(res, 200, response);
        return;
      }

      // --- POST /v1/tools/:name ---
      const toolMatch = pathname.match(/^\/v1\/tools\/([a-zA-Z_][a-zA-Z0-9_-]*)$/);
      if (method === 'POST' && toolMatch) {
        const toolName = toolMatch[1];
        const body = await parseBody(req);
        const args = body.arguments || body.args || body;
        const result = await toolRegistry.call(toolName, args);
        const response: APIResponse = {
          success: result.success,
          data: result.data,
          error: result.error,
          summary: result.summary,
          tool: toolName,
          timestamp: new Date().toISOString(),
        };
        jsonResponse(res, result.success ? 200 : 400, response);
        return;
      }

      // --- GET /v1/prompt ---
      if (method === 'GET' && pathname === '/v1/prompt') {
        const prompt = getSystemPrompt(currentPromptLevel);
        const response: APIResponse = {
          success: true,
          data: {
            level: currentPromptLevel,
            prompt,
          },
          timestamp: new Date().toISOString(),
        };
        jsonResponse(res, 200, response);
        return;
      }

      // --- POST /v1/prompt ---
      if (method === 'POST' && pathname === '/v1/prompt') {
        const body = await parseBody(req);
        const level: PromptLevel = body.level || currentPromptLevel;
        const extraContext = body.context;
        const prompt = getSystemPrompt(level, extraContext);
        const response: APIResponse = {
          success: true,
          data: {
            level,
            prompt,
          },
          timestamp: new Date().toISOString(),
        };
        jsonResponse(res, 200, response);
        return;
      }

      // --- GET /v1/prompt/switch --- 查看当前开关状态
      if (method === 'GET' && pathname === '/v1/prompt/switch') {
        const response: APIResponse = {
          success: true,
          data: {
            currentLevel: currentPromptLevel,
            availableLevels: ['strong', 'medium', 'light'],
            description: {
              strong: '强力模式 — AI 优先使用灵动脚本完成所有桌面操作',
              medium: '中等模式 — AI 在适当时使用灵动脚本',
              light: '轻量模式 — AI 仅在明确请求时使用',
            },
          },
          timestamp: new Date().toISOString(),
        };
        jsonResponse(res, 200, response);
        return;
      }

      // --- POST /v1/prompt/switch --- 切换提示词强度
      if (method === 'POST' && pathname === '/v1/prompt/switch') {
        const body = await parseBody(req);
        const newLevel: PromptLevel = body.level;
        if (!['strong', 'medium', 'light'].includes(newLevel)) {
          jsonResponse(res, 400, {
            success: false,
            error: '无效的 level。可选: strong, medium, light',
            timestamp: new Date().toISOString(),
          });
          return;
        }
        currentPromptLevel = newLevel;
        const response: APIResponse = {
          success: true,
          data: {
            level: currentPromptLevel,
            message: `提示词已切换为 ${currentPromptLevel} 模式`,
          },
          timestamp: new Date().toISOString(),
        };
        jsonResponse(res, 200, response);
        return;
      }

      // --- 404 ---
      jsonResponse(res, 404, {
        success: false,
        error: `未知路径: ${method} ${pathname}`,
        availableEndpoints: [
          'GET  /health',
          'GET  /v1/tools',
          'POST /v1/tools/:name',
          'GET  /v1/prompt',
          'POST /v1/prompt',
          'GET  /v1/prompt/switch',
          'POST /v1/prompt/switch',
        ],
        timestamp: new Date().toISOString(),
      });
    } catch (err: any) {
      jsonResponse(res, 500, {
        success: false,
        error: `服务器错误: ${err.message}`,
        timestamp: new Date().toISOString(),
      });
    }
  });

  server.listen(port, () => {
    console.log(JSON.stringify({
      type: 'info',
      message: `灵动脚本 AI Interface HTTP API 已启动`,
      port,
      endpoints: {
        health: 'GET /health',
        tools: 'GET /v1/tools',
        toolCall: 'POST /v1/tools/:name',
        prompt: 'GET /v1/prompt',
        promptSwitch: 'POST /v1/prompt/switch',
      },
      promptLevel: currentPromptLevel,
    }));
  });

  process.on('SIGINT', () => {
    console.log(JSON.stringify({ type: 'info', message: 'HTTP 服务已关闭' }));
    process.exit(0);
  });
}

// CLI 直接入口（仅当直接运行此文件时执行）
if (require.main === module) {
  const portArgIdx = process.argv.indexOf('--port');
  const port = portArgIdx >= 0 ? parseInt(process.argv[portArgIdx + 1]) : 3456;
  startHTTPServer(port);
}
