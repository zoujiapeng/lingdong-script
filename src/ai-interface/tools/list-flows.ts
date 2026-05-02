/**
 * list-flows Tool — 列出可用脚本
 *
 * AI 在不确定用户有哪些脚本时，可以先调用此工具查看。
 */

import * as fs from 'fs';
import * as path from 'path';
import { createTool } from './registry';

export const listFlowsTool = createTool(
  {
    name: 'list_flows',
    description: '列出指定目录下的所有可用脚本文件（.flow / .json / .pseudo）。AI 在执行脚本前如果不确定有哪些可用脚本，可以先调用此工具。',
    priority: 'medium',
    category: 'discovery',
    inputSchema: {
      type: 'object',
      properties: {
        dir: {
          type: 'string',
          description: '要搜索的目录路径（默认当前工作目录）',
        },
      },
      required: [],
    },
    examples: [
      'list_flows {}',
      'list_flows { dir: "/path/to/scripts" }',
    ],
  },
  async (args) => {
    const dir = args.dir ? path.resolve(args.dir) : process.cwd();
    if (!fs.existsSync(dir)) {
      return { success: false, error: `目录不存在: ${dir}` };
    }

    const allFiles = fs.readdirSync(dir);
    const scripts: any[] = [];
    for (const file of allFiles) {
      try {
        const absFile = path.join(dir, file);
        if (fs.statSync(absFile).isDirectory()) continue;
        const ext = path.extname(file).toLowerCase();
        let nodeCount = 0;
        let description = '';
        if (ext === '.flow' || ext === '.pseudo') {
          const { parsePseudoToFlow } = require('../../renderer/utils/pseudoParser');
          const s = parsePseudoToFlow(fs.readFileSync(absFile, 'utf-8'));
          nodeCount = (s.nodes || []).length;
          description = s.meta?.description || '';
          scripts.push({ name: file, path: absFile, nodeCount, description });
        } else if (ext === '.json') {
          const s = JSON.parse(fs.readFileSync(absFile, 'utf-8'));
          // Only include JSON files that have FlowScript structure
          if (s.nodes && s.meta) {
            nodeCount = s.nodes.length;
            description = s.meta?.description || '';
            scripts.push({ name: s.meta?.name || file, path: absFile, nodeCount, description });
          }
        }
      } catch { /* skip invalid/unparseable files */ }
    }

    return {
      success: true,
      data: { directory: dir, scripts, count: scripts.length },
      summary: `找到 ${scripts.length} 个脚本文件`,
    };
  },
);
