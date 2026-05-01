import * as fs from 'fs';
import * as path from 'path';
import * as cp from 'child_process';

const PSEUDO_EXTS = ['.flow', '.pseudo'];

export async function runCommand(file: string): Promise<void> {
  const absPath = path.resolve(file);

  if (!fs.existsSync(absPath)) {
    console.error(JSON.stringify({ type: 'error', message: `文件不存在: ${absPath}` }));
    process.exit(1);
  }

  let script: any;
  const ext = path.extname(file).toLowerCase();

  if (PSEUDO_EXTS.includes(ext)) {
    const pseudoCode = fs.readFileSync(absPath, 'utf-8');
    script = parsePseudoToFlow(pseudoCode);
  } else {
    try {
      script = JSON.parse(fs.readFileSync(absPath, 'utf-8'));
    } catch {
      console.error(JSON.stringify({ type: 'error', message: '无法解析脚本 JSON' }));
      process.exit(1);
    }
  }

  await spawnEngine(script);
}

export async function runCommandInline(pseudoCode: string): Promise<void> {
  const script = parsePseudoToFlow(pseudoCode);
  await spawnEngine(script);
}

function parsePseudoToFlow(pseudo: string): any {
  try {
    const { parsePseudoToFlow } = require('../../renderer/utils/pseudoParser');
    const script = parsePseudoToFlow(pseudo);
    // Ensure required fields
    if (!script.nodes) script.nodes = [];
    if (!script.edges) script.edges = [];
    if (!script.variables) script.variables = {};
    if (!script.settings) script.settings = { default_delay: 0.5, error_handling: 'stop', max_retries: 0, retry_delay: 1.0, log_level: 'info' };
    return script;
  } catch (e: any) {
    console.error(JSON.stringify({ type: 'error', message: `伪代码解析失败: ${e.message}` }));
    process.exit(1);
  }
}

async function spawnEngine(script: any): Promise<void> {
  const enginePath = findEngine();

  const proc = cp.spawn('python', [enginePath], {
    stdio: ['pipe', 'pipe', 'pipe'],
  });

  // Forward stdout as JSON lines
  proc.stdout!.on('data', (data: Buffer) => {
    const text = data.toString();
    for (const line of text.split('\n').filter(Boolean)) {
      try {
        const parsed = JSON.parse(line);
        console.log(JSON.stringify(parsed));
      } catch {
        console.log(JSON.stringify({ type: 'log', level: 'info', message: line }));
      }
    }
  });

  // Forward stderr
  proc.stderr!.on('data', (data: Buffer) => {
    console.error(JSON.stringify({ type: 'log', level: 'error', message: data.toString().trim() }));
  });

  proc.on('close', (code) => {
    console.log(JSON.stringify({ type: 'execution_done', status: code === 0 ? 'completed' : 'error', exit_code: code }));
    process.exit(code ?? 0);
  });

  proc.on('error', (err) => {
    console.error(JSON.stringify({ type: 'error', message: `引擎启动失败: ${err.message}` }));
    process.exit(1);
  });

  // Send execute command
  const msg = JSON.stringify({ type: 'execute', payload: { script } });
  proc.stdin!.write(msg + '\n');
}

function findEngine(): string {
  // Try paths relative to this file's location
  const candidates = [
    path.join(__dirname, '..', '..', 'src', 'python', 'engine.py'),
    path.join(__dirname, '..', '..', '..', 'src', 'python', 'engine.py'),
    path.join(process.cwd(), 'src', 'python', 'engine.py'),
  ];
  for (const p of candidates) {
    if (fs.existsSync(p)) return p;
  }
  // Fallback: hope python can find it
  return 'src/python/engine.py';
}
