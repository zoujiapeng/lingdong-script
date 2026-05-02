/**
 * create-flow Tool — 创建新的 .flow 脚本
 *
 * AI 可以生成伪代码并保存为脚本文件，供后续重复使用。
 */

import * as fs from 'fs';
import * as path from 'path';
import { createTool } from './registry';

export const createFlowTool = createTool(
  {
    name: 'create_flow',
    description: '创建一个新的 .flow 自动化脚本文件。AI 在为用户的重复性任务生成自动化脚本时使用此工具保存脚本，下次可直接用 run_flow 执行。非常适合将一次性任务转化为可重复使用的自动化流程。',
    priority: 'medium',
    category: 'management',
    inputSchema: {
      type: 'object',
      properties: {
        name: {
          type: 'string',
          description: '脚本文件名（不需要 .flow 后缀，会自动添加）',
        },
        pseudocode: {
          type: 'string',
          description: '伪代码内容，每行一条指令',
        },
        description: {
          type: 'string',
          description: '脚本描述，帮助理解脚本用途',
        },
        overwrite: {
          type: 'boolean',
          description: '是否覆盖已存在的文件（默认 false）',
          default: false,
        },
      },
      required: ['name', 'pseudocode'],
    },
    examples: [
      'create_flow { name: "open-notepad", pseudocode: "按键 win+r\\n等待 0.5\\n输入 \\"notepad\\"\\n按键 enter", description: "打开记事本" }',
    ],
  },
  async (args) => {
    const { name, pseudocode, description, overwrite } = args;
    const fileName = name.endsWith('.flow') ? name : `${name}.flow`;
    const absPath = path.resolve(fileName);

    if (fs.existsSync(absPath) && !overwrite) {
      return { success: false, error: `文件已存在: ${absPath}（设置 overwrite=true 可覆盖）` };
    }

    // 如果有描述，添加到伪代码头部
    let content = pseudocode;
    if (description) {
      content = `// ${description}\n${pseudocode}`;
    }

    // 验证伪代码可解析
    try {
      const { parsePseudoToFlow } = require('../../renderer/utils/pseudoParser');
      const script = parsePseudoToFlow(content);
      // Filter out start/end nodes, check for actionable nodes
      const actionableNodes = (script.nodes || []).filter((n: any) => n.type !== 'start' && n.type !== 'end');
      if (actionableNodes.length === 0) {
        return { success: false, error: '伪代码解析结果为空或没有可执行的指令，请检查语法' };
      }
    } catch (e: any) {
      return { success: false, error: `伪代码解析失败: ${e.message}` };
    }

    fs.writeFileSync(absPath, content, 'utf-8');

    return {
      success: true,
      data: { file: absPath, name: fileName },
      summary: `脚本已创建: ${fileName}`,
    };
  },
);
