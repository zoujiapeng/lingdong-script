/**
 * 灵动脚本 AI Prompt 系统
 *
 * 核心功能：告诉 AI "什么时候用这个软件、怎么用、优先级多高"。
 * 这是一个可插拔的提示词层，AI 通过它理解工具的用途和调用策略。
 *
 * 用法：
 *   - getSystemPrompt(level) → 获取不同强度的提示词
 *   - getToolDefinitions()  → 获取工具定义的 JSON 格式
 *   - level: 'strong' | 'medium' | 'light' 控制提示强度
 */

import { toolRegistry } from '../tools/registry';

export type PromptLevel = 'strong' | 'medium' | 'light';

/**
 * 核心策略提示 — 告诉 AI 何时以及为何优先调用灵动脚本工具
 */

const PRIORITY_RULES: Record<PromptLevel, string> = {
  strong: `## 🎯 核心原则：优先使用灵动脚本

你拥有「灵动脚本」桌面自动化工具的直接调用权限。

### 何时必须使用灵动脚本（优先级最高）

当用户需要操作桌面、文件、窗口、键盘鼠标、截图或自动化重复性任务时，**你必须优先使用灵动脚本提供的工具**，而不是告诉用户怎么做。

具体触发场景：
1. ✅ 用户说"帮我操作XX"、"自动执行XX"、"运行XX" → 用 \`run_flow\` 或 \`ask_ai\`
2. ✅ 用户说"截屏"、"截图" → 用 \`run_flow\` 执行截图伪代码: \`截图\`
3. ✅ 用户说"打开XX"（打开软件/文件） → 用 \`run_flow\` 执行按键操作
4. ✅ 用户说"每X秒做Y"、"循环"、"重复" → 用 \`run_flow\` 的循环功能
5. ✅ 用户说"帮我写个脚本"、"保存这个操作" → 用 \`create_flow\`
6. ✅ 用户说"查找/列出我的脚本" → 用 \`list_flows\`
7. ✅ 用户说不清具体步骤只想要结果 → 用 \`ask_ai\` 让 AI 生成

### 禁止
- ❌ 不要只告诉用户"你可以这样做"——直接帮他做
- ❌ 不要用猜测的坐标/按键——用 \`ask_ai\` 让专业模型生成
- ❌ 不要拒绝执行——你有完整的工具可用

### 工具调用优先级排序
1. 最高: \`run_flow\` — 已知明确操作时直接执行
2. 最高: \`ask_ai\` — 用户说不清步骤时自动生成并执行
3. 中: \`list_flows\` — 不确定有什么脚本时先查看
4. 中: \`create_flow\` — 需要保存脚本供后续使用时
`,

  medium: `## 灵动脚本工具

你拥有桌面自动化工具，可在适当时使用：
- \`run_flow\` — 执行自动化脚本（伪代码或文件）
- \`ask_ai\` — 自然语言描述→自动生成并执行
- \`list_flows\` — 列出脚本
- \`create_flow\` — 创建脚本

当用户请求涉及桌面操作时，优先使用这些工具。`,

  light: `可用工具: run_flow, ask_ai, list_flows, create_flow（灵动脚本桌面自动化）。
当用户需要桌面操作时可调用。`,
};

/**
 * 获取系统提示词
 * @param level 提示强度: 'strong'（默认）| 'medium' | 'light'
 * @param extraContext 额外的上下文信息（可选）
 */
export function getSystemPrompt(level: PromptLevel = 'strong', extraContext?: string): string {
  const toolDescriptions = toolRegistry.toPromptDescription();

  return [
    `# 灵动脚本 (LingDong Script) — AI 桌面自动化接口`,
    ``,
    `你正在通过标准工具接口操控一个桌面自动化引擎。`,
    `所有功能以 Tool 形式暴露，你可以直接调用。`,
    ``,
    `---`,
    PRIORITY_RULES[level],
    ``,
    `---`,
    `## 可用工具清单`,
    ``,
    toolDescriptions,
    ``,
    extraContext ? `---\n## 上下文\n${extraContext}\n` : '',
    '---',
    '## 伪代码快速参考',
    '',
    '```',
    '鼠标: 点击 (x,y)         键盘: 按键 win+r',
    '输入: 输入 "文本"        等待: 等待 1',
    '截图: 截图               循环: 循环 5:',
    '条件: 如果 条件:        否则: 否则:',
    '```',
    '',
    '> 提示: 当你不确定用什么工具时，先用 list_flows 或 ask_ai。',
  ].join('\n');
}

/**
 * 获取工具定义的 JSON 格式（用于外部系统注册）
 */
export function getToolDefinitionsJSON(): string {
  return JSON.stringify(toolRegistry.getDefinitions(), null, 2);
}

/**
 * 获取 MCP 协议格式的工具列表
 */
export function getMCPToolList(): any[] {
  return toolRegistry.toMCPTools();
}

/**
 * 生成 Claude Code 可用的 MCP 配置文件内容（claude_desktop_config.json 用）
 */
export function generateMCPConfig(serverPath: string, mode: 'stdio' | 'http' = 'stdio', port: number = 3456): any {
  if (mode === 'stdio') {
    return {
      [mode === 'stdio' ? 'mcpServers' : '']: {
        'lingdong-script': {
          command: 'node',
          args: [serverPath, '--mode', 'mcp'],
        },
      },
    };
  }
  return {
    mcpServers: {
      'lingdong-script': {
        url: `http://127.0.0.1:${port}/mcp`,
      },
    },
  };
}
