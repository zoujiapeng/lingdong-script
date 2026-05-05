/**
 * run-flow Tool — 执行 .flow 脚本或内联伪代码
 *
 * 这是最核心的工具：接收一个自动化脚本并执行它。
 * AI 应优先使用此工具来执行桌面自动化操作。
 */

import * as fs from 'fs';
import * as path from 'path';
import * as cp from 'child_process';
import { createTool } from './registry';

function findEngine(): string {
  const candidates = [
    path.join(__dirname, '..', '..', '..', '..', 'src', 'python', 'engine.py'),
    path.join(process.cwd(), 'src', 'python', 'engine.py'),
  ];
  for (const p of candidates) {
    if (fs.existsSync(p)) return p;
  }
  return 'src/python/engine.py';
}

function parsePseudoToFlow(pseudo: string): any {
  const { parsePseudoToFlow } = require('../../renderer/utils/pseudoParser');
  const script = parsePseudoToFlow(pseudo);
  if (!script.nodes) script.nodes = [];
  if (!script.edges) script.edges = [];
  if (!script.variables) script.variables = {};
  if (!script.settings) script.settings = { default_delay: 0.5, error_handling: 'stop', max_retries: 0, retry_delay: 1.0, log_level: 'info' };
  return script;
}

function executeScript(script: any): Promise<{ stdout: string; stderr: string; exitCode: number }> {
  return new Promise((resolve, reject) => {
    const enginePath = findEngine();
    const proc = cp.spawn('python', [enginePath], {
      stdio: ['pipe', 'pipe', 'pipe'],
      env: { ...process.env, PYTHONIOENCODING: 'utf-8' },
    });
    const _outBufs: Buffer[] = [];
    const _errBufs: Buffer[] = [];
    let _stdoutStr = '';
    let flowDone = false;

    proc.stdout!.on('data', (data: Buffer) => {
      _outBufs.push(data);
      _stdoutStr += data.toString('utf8');
      // 检测到 execution_done 后关闭 stdin，让引擎优雅退出
      if (!flowDone && _stdoutStr.includes('"execution_done"')) {
        flowDone = true;
        setTimeout(() => proc.stdin!.end(), 200);
      }
    });

    proc.stderr!.on('data', (data: Buffer) => {
      _errBufs.push(data);
    });

    proc.on('close', (code) => {
      resolve({
        stdout: Buffer.concat(_outBufs).toString('utf8'),
        stderr: Buffer.concat(_errBufs).toString('utf8'),
        exitCode: code ?? 1,
      });
    });

    proc.on('error', (err) => {
      reject(new Error(`引擎启动失败: ${err.message}`));
    });

    proc.stdin!.write(JSON.stringify({ type: 'execute', payload: { script } }) + '\n');
  });
}

export const runFlowTool = createTool(
  {
    name: 'run_flow',
    description: '【核心工具】执行一个桌面自动化脚本。支持两种模式：(1) 传入 file 参数执行已有的 .flow 文件；(2) 传入 pseudocode 直接执行伪代码。伪代码语法：点击 (x,y)、输入 "文本"、按键 键名、等待 秒数、截图、循环 N次:、如果 条件:。这是灵动脚本的核心执行工具，AI 应该优先使用它来完成桌面自动化任务。',
    priority: 'high',
    category: 'execution',
    inputSchema: {
      type: 'object',
      properties: {
        file: {
          type: 'string',
          description: '要执行的 .flow 脚本文件路径（与 pseudocode 二选一）',
        },
        pseudocode: {
          type: 'string',
          description: '内联伪代码字符串（与 file 二选一）。每行一条指令：点击 (x,y)、输入 "文本"、按键 enter、等待 0.5、截图、循环 5:、如果 图片存在 "path":',
        },
        wait: {
          type: 'boolean',
          description: '是否等待执行完成再返回结果（默认 true）',
          default: true,
        },
      },
      required: [],
    },
    examples: [
      'run_flow { pseudocode: "按键 win+r\\n等待 0.5\\n输入 \\"notepad\\"\\n按键 enter\\n等待 1\\n输入 \\"hello world\\"\\n截图" }',
      'run_flow { file: "examples/demo.flow" }',
      'run_flow { pseudocode: "循环 3:\\n  截图\\n  等待 2" }',
    ],
  },
  async (args) => {
    const { file, pseudocode } = args;
    if (!file && !pseudocode) {
      return { success: false, error: '请提供 file 或 pseudocode 参数' };
    }

    let script: any;
    if (pseudocode) {
      script = parsePseudoToFlow(pseudocode);
    } else {
      const absPath = path.resolve(file);
      if (!fs.existsSync(absPath)) {
        return { success: false, error: `文件不存在: ${absPath}` };
      }
      const ext = path.extname(file).toLowerCase();
      if (ext === '.flow' || ext === '.pseudo') {
        script = parsePseudoToFlow(fs.readFileSync(absPath, 'utf-8'));
      } else {
        script = JSON.parse(fs.readFileSync(absPath, 'utf-8'));
      }
    }

    const { stdout, stderr, exitCode } = await executeScript(script);

    return {
      success: exitCode === 0,
      data: {
        exitCode,
        stdout,
        stderr,
        nodeCount: (script.nodes || []).length,
      },
      summary: `脚本执行${exitCode === 0 ? '成功' : '失败'} (exit code: ${exitCode})，包含 ${(script.nodes || []).length} 个节点`,
    };
  },
);
