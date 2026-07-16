/**
 * MCP over HTTP — 使 :5877 API 服务器兼作 MCP HTTP 端点
 *
 * 实现 Model Context Protocol (Streamable HTTP Transport)
 * 供 Claude Code 通过 HTTP 连接，无需 stdio 路径（便携版关键）
 *
 * 暴露工具：Python @feature registry 中的所有 ~120 个工具
 * 通过 feature_list.py / feature_call.py 代理调用
 */
import * as path from 'path';
import * as fs from 'fs';
import { runPythonCollect } from './engine';
import { getBaseDir } from './env';

interface MCPTool {
  name: string;
  description: string;
  inputSchema: {
    type: 'object';
    properties: Record<string, any>;
    required: string[];
  };
}

export interface MCPResult {
  status: number;
  contentType: string;
  body: string;
}

let _toolsCache: MCPTool[] | null = null;
let _pythonAvailable: boolean | null = null;

/** 检查 Python 是否可用（只检查一次并缓存结果） */
async function checkPython(): Promise<boolean> {
  if (_pythonAvailable !== null) return _pythonAvailable;
  try {
    const scriptPath = path.join(getBaseDir(), 'python', 'tools', 'feature_list.py');
    if (!fs.existsSync(scriptPath)) {
      console.error('[MCP] Python tools not found at:', scriptPath);
      _pythonAvailable = false;
      return false;
    }
    const result = await runPythonCollect(['--version'], { cwd: getBaseDir() });
    _pythonAvailable = result.stderr.includes('Python') || result.stdout.includes('Python');
    if (!_pythonAvailable) console.error('[MCP] Python check failed:', result.stderr);
  } catch (e: any) {
    console.error('[MCP] Python not available:', e.message);
    _pythonAvailable = false;
  }
  return _pythonAvailable;
}

/** 从 Python @feature registry 获取工具列表，转换为 MCP schema 格式 */
async function fetchTools(): Promise<MCPTool[]> {
  if (_toolsCache) return _toolsCache;
  if (!await checkPython()) return [];

  try {
    const scriptPath = path.join(getBaseDir(), 'python', 'tools', 'feature_list.py');
    const result = await runPythonCollect([scriptPath], { cwd: getBaseDir() });
    const features = JSON.parse(result.stdout.trim());

    if (Array.isArray(features)) {
      _toolsCache = features.map((f: any) => ({
        name: `lobster_${f.name}`,
        description: `${f.display_name || f.name}: ${f.description || ''}`,
        inputSchema: {
          type: 'object' as const,
          properties: Object.fromEntries(
            (f.params || []).map((p: any) => [
              p.name,
              { type: p.type || 'string', description: p.description || '' },
            ])
          ),
          required: (f.params || [])
            .filter((p: any) => p.required)
            .map((p: any) => p.name),
        },
      }));
    }
  } catch (e) {
    console.error('[MCP] Failed to fetch tools:', e);
    _toolsCache = [];
  }

  return _toolsCache || [];
}

/** 执行工具调用 — 通过 feature_call.py 代理到 Python */
async function callTool(name: string, args: Record<string, any>): Promise<any> {
  const featureName = name.startsWith('lobster_') ? name.slice(8) : name;
  if (!await checkPython()) return { success: false, error: 'Python not available on this system' };

  try {
    const scriptPath = path.join(getBaseDir(), 'python', 'tools', 'feature_call.py');
    const result = await runPythonCollect(
      [scriptPath],
      { cwd: getBaseDir(), stdio: ['pipe', 'pipe', 'pipe'] },
      JSON.stringify({ name: featureName, params: args || {} })
    );
    try {
      return JSON.parse(result.stdout.trim());
    } catch {
      return { success: false, error: result.stderr?.trim() || '调用失败' };
    }
  } catch (e: any) {
    return { success: false, error: e.message };
  }
}

function rpc(id: number | string | null, result?: any, error?: { code: number; message: string }): string {
  const msg: any = { jsonrpc: '2.0', id };
  if (error) msg.error = error;
  else msg.result = result;
  return JSON.stringify(msg);
}

/** 如果需要 SSE，将 JSON 响应包装为 text/event-stream 格式 */
function maybeSse(jsonBody: string, acceptHeader: string): { contentType: string; body: string } {
  const wantsSse = acceptHeader && acceptHeader.includes('text/event-stream');
  if (!wantsSse) return { contentType: 'application/json', body: jsonBody };
  return {
    contentType: 'text/event-stream',
    body: `event: message\ndata: ${jsonBody}\n\n`,
  };
}

/**
 * 处理 MCP HTTP POST /mcp 请求
 * acceptHeader 用于判断是否需要 SSE 格式
 */
export async function handleMCPRequest(body: string, acceptHeader: string = ''): Promise<MCPResult> {
  let request: any;
  try {
    request = JSON.parse(body);
  } catch {
    const json = rpc(null, undefined, { code: -32700, message: 'Parse error' });
    const { contentType, body: b } = maybeSse(json, acceptHeader);
    return { status: 400, contentType, body: b };
  }

  if (request.jsonrpc !== '2.0' || !request.method) {
    const json = rpc(request.id ?? null, undefined, { code: -32600, message: 'Invalid Request' });
    const { contentType, body: b } = maybeSse(json, acceptHeader);
    return { status: 400, contentType, body: b };
  }

  // Notification（无 id）— 不响应
  if (request.id === undefined || request.id === null) {
    return { status: 202, contentType: 'text/event-stream', body: '' };
  }

  try {
    switch (request.method) {
      case 'initialize': {
        const json = rpc(request.id, {
          protocolVersion: '2024-11-05',
          serverInfo: { name: 'lingdong-script', version: '1.0.0' },
          capabilities: { tools: {} },
        });
        const { contentType, body: b } = maybeSse(json, acceptHeader);
        return { status: 200, contentType, body: b };
      }

      case 'tools/list': {
        const tools = await fetchTools();
        const json = rpc(request.id, { tools });
        const { contentType, body: b } = maybeSse(json, acceptHeader);
        return { status: 200, contentType, body: b };
      }

      case 'tools/call': {
        const { name, arguments: args } = request.params || {};
        if (!name) {
          const json = rpc(request.id, undefined, { code: -32602, message: 'Missing tool name' });
          const { contentType, body: b } = maybeSse(json, acceptHeader);
          return { status: 200, contentType, body: b };
        }

        const result = await callTool(name, args || {});
        const isError = !result || result.success === false;
        const json = rpc(request.id, {
          content: [{ type: 'text', text: JSON.stringify(result) }],
          isError,
        });
        const { contentType, body: b } = maybeSse(json, acceptHeader);
        return { status: 200, contentType, body: b };
      }

      default: {
        const json = rpc(request.id, undefined, { code: -32601, message: `Method not supported: ${request.method}` });
        const { contentType, body: b } = maybeSse(json, acceptHeader);
        return { status: 200, contentType, body: b };
      }
    }
  } catch (err: any) {
    const json = rpc(request.id, undefined, { code: -32603, message: `Internal error: ${err.message}` });
    const { contentType, body: b } = maybeSse(json, acceptHeader);
    return { status: 500, contentType, body: b };
  }
}

/** 清空工具缓存（feature 注册表变更时调用） */
export function clearToolsCache(): void {
  _toolsCache = null;
}
