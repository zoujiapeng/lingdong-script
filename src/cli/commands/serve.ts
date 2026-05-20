import * as http from 'http';
import * as cp from 'child_process';
import * as path from 'path';
import * as fs from 'fs';

export async function serveCommand(port: number): Promise<void> {
  const server = http.createServer(async (req, res) => {
    res.setHeader('Access-Control-Allow-Origin', '*');
    res.setHeader('Access-Control-Allow-Methods', 'GET, POST, OPTIONS');
    res.setHeader('Access-Control-Allow-Headers', 'Content-Type');

    if (req.method === 'OPTIONS') {
      res.writeHead(204);
      res.end();
      return;
    }

    try {
      const url = new URL(req.url || '/', `http://localhost:${port}`);
      const pathname = url.pathname;

      // --- Health ---
      if (req.method === 'GET' && pathname === '/health') {
        res.writeHead(200, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ status: 'ok', timestamp: new Date().toISOString() }));
        return;
      }

      // --- Execute a FlowScript ---
      if (req.method === 'POST' && pathname === '/run') {
        const body = await readBody(req);
        let script: any;
        try {
          const parsed = JSON.parse(body);
          if (parsed.pseudo) {
            const { parsePseudoToFlow } = require('../../renderer/utils/pseudoParser');
            script = parsePseudoToFlow(parsed.pseudo);
          } else if (parsed.script) {
            script = parsed.script;
          } else {
            throw new Error('需要 script 或 pseudo 字段');
          }
        } catch (e: any) {
          res.writeHead(400, { 'Content-Type': 'application/json' });
          res.end(JSON.stringify({ error: e.message }));
          return;
        }

        res.writeHead(200, {
          'Content-Type': 'application/json',
          'Transfer-Encoding': 'chunked',
        });

        const enginePath = findEngine();
        const proc = cp.spawn('python', [enginePath], {
          stdio: ['pipe', 'pipe', 'pipe'],
          env: { ...process.env, PYTHONIOENCODING: 'utf-8' },
        });

        proc.stdout!.on('data', (data: Buffer) => {
          for (const line of data.toString('utf8').split('\n').filter(Boolean)) {
            try {
              res.write(JSON.stringify(JSON.parse(line)) + '\n');
            } catch {
              res.write(JSON.stringify({ type: 'log', level: 'info', message: line }) + '\n');
            }
          }
        });

        proc.stderr!.on('data', (data: Buffer) => {
          res.write(JSON.stringify({ type: 'log', level: 'error', message: data.toString('utf8').trim() }) + '\n');
        });

        proc.on('close', (code) => {
          res.write(JSON.stringify({ type: 'execution_done', status: code === 0 ? 'completed' : 'error', exit_code: code }) + '\n');
          res.end();
        });

        proc.stdin!.write(JSON.stringify({ type: 'execute', payload: { script } }) + '\n');
        return;
      }

      // --- List scripts ---
      if (req.method === 'GET' && pathname === '/list') {
        const dir = process.cwd();
        const entries = fs.readdirSync(dir).filter(f => f.endsWith('.json') || f.endsWith('.flow') || f.endsWith('.pseudo'));
        const scripts: any[] = [];
        for (const file of entries) {
          try {
            const absFile = path.join(dir, file);
            const ext = path.extname(file).toLowerCase();
            if (ext === '.flow' || ext === '.pseudo') {
              const { parsePseudoToFlow } = require('../../renderer/utils/pseudoParser');
              const s = parsePseudoToFlow(fs.readFileSync(absFile, 'utf-8'));
              scripts.push({ name: file, path: absFile, nodeCount: (s.nodes || []).length });
            } else {
              const s = JSON.parse(fs.readFileSync(absFile, 'utf-8'));
              scripts.push({ name: s.meta?.name || file, path: absFile, nodeCount: (s.nodes || []).length });
            }
          } catch {}
        }
        res.writeHead(200, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ type: 'list', scripts, count: scripts.length }));
        return;
      }

      res.writeHead(404, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ error: 'Not found' }));
    } catch (err: any) {
      res.writeHead(500, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ error: err.message }));
    }
  });

  server.listen(port, () => {
    const msg = JSON.stringify({
      type: 'info',
      message: `灵动脚本 API 运行在 http://localhost:${port}`,
      endpoints: {
        health: `GET /health`,
        run: `POST /run  { script | pseudo }`,
        list: `GET /list`,
      },
    });
    console.log(msg);
  });

  process.on('SIGINT', () => {
    console.log(JSON.stringify({ type: 'info', message: '服务已关闭' }));
    process.exit(0);
  });
}

function readBody(req: http.IncomingMessage): Promise<string> {
  return new Promise((resolve, reject) => {
    const chunks: Buffer[] = [];
    req.on('data', (c: Buffer) => chunks.push(c));
    req.on('end', () => resolve(Buffer.concat(chunks).toString('utf8')));
    req.on('error', reject);
  });
}

function findEngine(): string {
  const candidates = [
    path.join(__dirname, '..', '..', '..', 'python', 'engine', 'engine.py'),
    path.join(process.cwd(), 'python', 'engine', 'engine.py'),
  ];
  for (const p of candidates) {
    if (fs.existsSync(p)) return p;
  }
  return 'python/engine/engine.py';
}
