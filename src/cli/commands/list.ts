import * as fs from 'fs';
import * as path from 'path';

interface ListOptions {
  pretty: boolean;
}

interface ScriptInfo {
  name: string;
  path: string;
  nodeCount: number;
  description: string;
  updatedAt: string;
}

export async function listCommand(dir: string, options: ListOptions): Promise<void> {
  const absDir = path.resolve(dir);

  if (!fs.existsSync(absDir)) {
    console.error(JSON.stringify({ type: 'error', message: `目录不存在: ${absDir}` }));
    process.exit(1);
  }

  const files = fs.readdirSync(absDir).filter((f) =>
    f.endsWith('.json') || f.endsWith('.flow') || f.endsWith('.pseudo')
  );
  const scripts: ScriptInfo[] = [];

  for (const file of files) {
    try {
      const absFile = path.join(absDir, file);
      const ext = path.extname(file).toLowerCase();
      if (ext === '.flow' || ext === '.pseudo') {
        // Parse pseudocode file to get metadata
        const pseudoCode = fs.readFileSync(absFile, 'utf-8');
        const { parsePseudoToFlow } = require('../../renderer/utils/pseudoParser');
        const script = parsePseudoToFlow(pseudoCode);
        scripts.push({
          name: file,
          path: absFile,
          nodeCount: (script.nodes || []).length,
          description: script.meta?.description || '',
          updatedAt: script.meta?.updated_at || '',
        });
      } else {
        const content = fs.readFileSync(absFile, 'utf-8');
        const script = JSON.parse(content);
        // Only include valid FlowScript files (must have nodes and meta)
        if (!script.nodes || !script.meta) continue;
        scripts.push({
          name: script.meta?.name || file,
          path: absFile,
          nodeCount: (script.nodes || []).length,
          description: script.meta?.description || '',
          updatedAt: script.meta?.updated_at || '',
        });
      }
    } catch {
      // Skip invalid files
    }
  }

  // Sort by name
  scripts.sort((a, b) => a.name.localeCompare(b.name));

  if (options.pretty) {
    if (scripts.length === 0) {
      console.log(`目录 "${absDir}" 中没有找到脚本文件`);
    } else {
      console.log(`找到 ${scripts.length} 个脚本:`);
      for (const s of scripts) {
        console.log(`  ${s.name}`);
        console.log(`    路径: ${s.path}`);
        console.log(`    节点: ${s.nodeCount}`);
        console.log(`    描述: ${s.description}`);
        console.log();
      }
    }
  } else {
    console.log(JSON.stringify({ type: 'list', scripts, count: scripts.length, directory: absDir }));
  }
}
