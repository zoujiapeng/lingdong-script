/**
 * 灵动脚本 Tool Registry — 所有工具的注册中心
 *
 * 职责：
 *  1. 注册所有可用 Tool（定义 + 实现）
 *  2. 提供按名称查找、列出所有定义的能力
 *  3. 生成 MCP / HTTP 所需的工具列表格式
 *  4. 生成 AI System Prompt 所需的工具描述
 *
 * 扩展方式：在 registerAll() 中加一行 toolRegistry.register(tool) 即可。
 */

import { Tool, ToolDefinition, ToolHandler, ToolResult } from './types';

export class ToolRegistry {
  private tools: Map<string, Tool> = new Map();

  register(tool: Tool): void {
    if (this.tools.has(tool.definition.name)) {
      console.warn(`[registry] Tool "${tool.definition.name}" 已注册，将被覆盖`);
    }
    this.tools.set(tool.definition.name, tool);
  }

  get(name: string): Tool | undefined {
    return this.tools.get(name);
  }

  getAll(): Tool[] {
    return Array.from(this.tools.values());
  }

  getDefinitions(): ToolDefinition[] {
    return this.getAll().map(t => t.definition);
  }

  async call(name: string, args: Record<string, any>): Promise<ToolResult> {
    const tool = this.get(name);
    if (!tool) {
      return { success: false, error: `未知工具: "${name}"。可用工具: ${this.listNames().join(', ')}` };
    }
    try {
      return await tool.handler(args);
    } catch (err: any) {
      return { success: false, error: `工具 "${name}" 执行失败: ${err.message}` };
    }
  }

  listNames(): string[] {
    return Array.from(this.tools.keys());
  }

  /** 生成 MCP 协议格式的工具列表 */
  toMCPTools() {
    return this.getDefinitions().map(def => ({
      name: def.name,
      description: def.description,
      inputSchema: def.inputSchema,
    }));
  }

  /** 生成 AI System Prompt 格式的工具描述（告诉 AI 有什么工具、怎么用） */
  toPromptDescription(): string {
    return this.getDefinitions()
      .map(def => {
        const params = Object.entries(def.inputSchema.properties)
          .map(([k, v]) => {
            const req = def.inputSchema.required.includes(k) ? ' (必填)' : '';
            return `    - ${k}${req}: ${v.type} — ${v.description}`;
          })
          .join('\n');

        const examples = def.examples
          .map(ex => `    > ${ex}`)
          .join('\n');

        return [
          `  📌 ${def.name} [优先级: ${def.priority}]`,
          `     ${def.description}`,
          `     分类: ${def.category}`,
          `     参数:`,
          params || '     (无参数)',
          examples ? `     示例:\n${examples}` : '',
        ].filter(Boolean).join('\n');
      })
      .join('\n\n');
  }
}

/** 全局单例 */
export const toolRegistry = new ToolRegistry();

/**
 * 辅助函数：创建 Tool 对象
 */
export function createTool(
  definition: ToolDefinition,
  handler: ToolHandler,
): Tool {
  return { definition, handler };
}
