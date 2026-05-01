#!/usr/bin/env node
/**
 * 灵动脚本 CLI - AI-optimized command-line interface
 *
 * Usage:
 *   lingdong run <file>          Execute .flow/.json script
 *   lingdong run -c <code>       Execute inline pseudocode
 *   lingdong list [dir]          List available scripts
 *   lingdong serve [--port N]    Start HTTP API server
 *   lingdong --help              Show help
 *
 * All output is JSONL (one JSON object per line) — machine-parseable.
 */

import { runCommand, runCommandInline } from './commands/run';
import { listCommand } from './commands/list';
import { serveCommand } from './commands/serve';
import { createCommand } from './commands/create';

const args = process.argv.slice(2);
const pretty = args.includes('--pretty');
const help = args.includes('--help') || args.includes('-h');

if (help || args.length === 0) {
  // --help outputs plain text (not JSONL) for readability
  console.log(`
lingdong v1.0.0 — AI 自动化脚本工具

用法:
  lingdong run <file>             执行 .flow 或 .json 脚本
  lingdong run -c <伪代码>        执行内联伪代码（AI 推荐用法）
  lingdong create <名> [伪代码]   创建 .flow 脚本（内容可从 stdin 管道输入）
  lingdong list [目录]            列出可用脚本
  lingdong serve [--port N]       启动 HTTP API 服务
  lingdong --help                 显示帮助

选项:
  -c, --code     以伪代码字符串形式输入
  --pretty       人类可读的输出
  --help, -h     显示帮助

输出格式: JSONL（每行一个 JSON 对象）
  {"type":"log","level":"info","message":"..."}
  {"type":"node_status","node_id":"...","status":"running"}
  {"type":"execution_done","status":"completed","exit_code":0}

示例:
  lingdong run -c "click (500, 400)"
  lingdong run -c 'click (500, 400)
    wait 1
    type "hello"'
  lingdong create login.flow "click (500, 400)"
  echo "click (500, 400)" | lingdong create login.flow
  lingdong run login.flow
  lingdong list
  lingdong serve --port 3456
`);
  process.exit(0);
}

const cmd = args[0];

async function main() {
  try {
    switch (cmd) {
      case 'run': {
        const inlineIdx = args.indexOf('-c') >= 0 ? args.indexOf('-c') : args.indexOf('--code');
        if (inlineIdx >= 0) {
          const inlineCode = args.slice(inlineIdx + 1).join(' ');
          if (!inlineCode) { console.error(JSON.stringify({ type: 'error', message: '-c/--code 后需要伪代码' })); process.exit(1); }
          await runCommandInline(inlineCode);
        } else {
          const nonFlagArgs = args.slice(1).filter(a => !a.startsWith('--') && !a.startsWith('-'));
          const file = nonFlagArgs[0];
          if (!file) { console.error(JSON.stringify({ type: 'error', message: '请指定脚本文件或使用 -c' })); process.exit(1); }
          await runCommand(file);
        }
        break;
      }

      case 'list': {
        const nonFlagArgs = args.slice(1).filter(a => !a.startsWith('--') && !a.startsWith('-'));
        const dir = nonFlagArgs[0] || process.cwd();
        await listCommand(dir, { pretty });
        break;
      }

      case 'create': {
        const nonFlagArgs = args.slice(1).filter(a => !a.startsWith('--') && !a.startsWith('-'));
        const name = nonFlagArgs[0];
        if (!name) { console.error(JSON.stringify({ type: 'error', message: '请指定文件名' })); process.exit(1); }
        const inlineCode = nonFlagArgs.slice(1).join(' ') || undefined;
        await createCommand(name, inlineCode);
        break;
      }

      case 'serve': {
        const portIdx = args.indexOf('--port');
        const port = portIdx >= 0 ? parseInt(args[portIdx + 1]) : 3456;
        await serveCommand(port);
        break;
      }

      default:
        console.error(JSON.stringify({ type: 'error', message: `未知命令: ${cmd}。运行 lingdong --help 查看帮助` }));
        process.exit(1);
    }
  } catch (err: any) {
    console.error(JSON.stringify({ type: 'error', message: err.message }));
    process.exit(1);
  }
}

main();
