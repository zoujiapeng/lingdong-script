import * as fs from 'fs';
import * as path from 'path';

export async function createCommand(name: string, inlineCode?: string): Promise<void> {
  let pseudoCode: string;

  if (inlineCode) {
    pseudoCode = inlineCode;
  } else {
    // Read from stdin
    pseudoCode = await readStdin();
  }

  if (!pseudoCode.trim()) {
    console.error(JSON.stringify({ type: 'error', message: '伪代码内容为空' }));
    process.exit(1);
  }

  // Validate by parsing
  try {
    const { parsePseudoToFlow } = require('../../renderer/utils/pseudoParser');
    const script = parsePseudoToFlow(pseudoCode);
    if (!script.nodes || script.nodes.length === 0) {
      console.error(JSON.stringify({ type: 'error', message: '伪代码解析结果为空' }));
      process.exit(1);
    }
  } catch (e: any) {
    console.error(JSON.stringify({ type: 'error', message: `伪代码解析失败: ${e.message}` }));
    process.exit(1);
  }

  // Ensure .flow extension
  const fileName = name.endsWith('.flow') ? name : name + '.flow';
  const absPath = path.resolve(fileName);

  // Check if file exists
  if (fs.existsSync(absPath)) {
    console.error(JSON.stringify({ type: 'error', message: `文件已存在: ${absPath}` }));
    process.exit(1);
  }

  fs.writeFileSync(absPath, pseudoCode, 'utf-8');

  console.log(JSON.stringify({
    type: 'created',
    file: absPath,
    name: fileName,
  }));
}

function readStdin(): Promise<string> {
  return new Promise((resolve) => {
    const chunks: Buffer[] = [];
    const stdin = process.stdin;
    stdin.setEncoding('utf-8');
    stdin.on('data', (chunk: string | Buffer) => chunks.push(Buffer.from(chunk)));
    stdin.on('end', () => resolve(Buffer.concat(chunks).toString('utf8')));
    if (stdin.isTTY) resolve('');
  });
}
