/**
 * ask-ai Tool — 用自然语言描述需求，AI 自动生成并执行脚本
 *
 * 这是"自然语言 → 自动化"的入口。
 * 当用户说不清具体操作、只想要某个结果时，AI 应使用此工具。
 */

import { createTool } from './registry';

const DEEPSEEK_API = 'https://api.deepseek.com/v1/chat/completions';
const MODEL = 'deepseek-chat';

const SYSTEM_PROMPT = `你是一个桌面自动化脚本生成器。将用户的自然语言需求转换为灵动脚本(lingdong)伪代码。

## 伪代码语法规则

每条指令一行，控制流用缩进（2空格）表示。
**必须使用中文关键词，不要使用英文关键词。**

### 鼠标操作
点击 (x, y) [双击|右击|中键]
点击图片 "路径" [相似度=N] [超时=N] [双击|右击|中键] [偏移(x,y)]
滑动 (x1,y1) 到 (x2,y2) [时长=N]

### 键盘操作
输入 "文本" [位置 (x,y)]
按键 键[+键...]
  可用键: ctrl, alt, shift, win, enter, tab, esc, space, backspace, delete, up, down, left, right, f1-f12, a-z, 0-9

### 等待
等待 秒数
等待图片 "路径" [超时=N]
等待窗口 "标题" [超时=N]

### 屏幕
截图

### 变量
$变量名 = "值"

### 子脚本
运行 "脚本名"

### 控制流
如果 条件:
  指令...
否则:
  指令...

循环 次数:
  指令...

无限循环:
  指令...

跳出

### 条件表达式
真
图片存在 "路径" [相似度=N]
窗口存在 "标题"
变量等于 变量名 "值"

## 输出格式
返回 JSON：{ "pseudocode": "伪代码...", "explanation": "简要说明..." }
不要包含 markdown 标记或代码块。`;

async function callDeepSeek(userMessage: string): Promise<{ pseudocode: string; explanation: string }> {
  const apiKey = process.env.DEEPSEEK_API_KEY;
  if (!apiKey) {
    throw new Error('请设置 DEEPSEEK_API_KEY 环境变量');
  }

  const response = await fetch(DEEPSEEK_API, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Authorization': `Bearer ${apiKey}`,
    },
    body: JSON.stringify({
      model: MODEL,
      messages: [
        { role: 'system', content: SYSTEM_PROMPT },
        { role: 'user', content: userMessage },
      ],
      response_format: { type: 'json_object' },
      temperature: 0.1,
      max_tokens: 2048,
    }),
  });

  if (!response.ok) {
    const text = await response.text();
    throw new Error(`DeepSeek API 错误 (${response.status}): ${text}`);
  }

  const data: any = await response.json();
  const content = data.choices?.[0]?.message?.content;
  if (!content) throw new Error('DeepSeek 返回为空');

  let parsed: any;
  try {
    parsed = JSON.parse(content);
  } catch {
    const jsonMatch = content.match(/\{[\s\S]*\}/);
    if (jsonMatch) {
      parsed = JSON.parse(jsonMatch[0]);
    } else {
      throw new Error(`无法解析 DeepSeek 响应: ${content}`);
    }
  }

  return {
    pseudocode: parsed.pseudocode || '',
    explanation: parsed.explanation || '',
  };
}

export const askAITool = createTool(
  {
    name: 'ask_ai',
    description: '【自然语言→自动化】用自然语言描述你想要完成的任务，AI 会自动生成并执行伪代码。当用户说不清具体操作步骤（如坐标、按键）时使用此工具。需要设置 DEEPSEEK_API_KEY 环境变量。',
    priority: 'high',
    category: 'ai-assist',
    inputSchema: {
      type: 'object',
      properties: {
        description: {
          type: 'string',
          description: '自然语言描述你想要完成的任务，越详细越好。例如："帮我打开计算器并计算 100+200"',
        },
        dry_run: {
          type: 'boolean',
          description: '如果为 true，只生成伪代码但不执行（默认 false）',
          default: false,
        },
      },
      required: ['description'],
    },
    examples: [
      'ask_ai { description: "打开记事本输入hello然后截图" }',
      'ask_ai { description: "每5秒截一次图，总共截3次", dry_run: true }',
    ],
  },
  async (args) => {
    const { description, dry_run } = args;

    const result = await callDeepSeek(description);
    if (!result.pseudocode.trim()) {
      return { success: false, error: 'AI 未生成伪代码' };
    }

    const response: any = {
      explanation: result.explanation,
      pseudocode: result.pseudocode,
      dry_run: !!dry_run,
    };

    if (!dry_run) {
      // 执行生成的伪代码
      const { runFlowTool } = require('./run-flow');
      const execResult = await runFlowTool.handler({ pseudocode: result.pseudocode });
      response.execution = execResult;
    }

    return {
      success: true,
      data: response,
      summary: dry_run
        ? `已生成伪代码 (${result.pseudocode.split('\n').length} 行): ${result.explanation}`
        : `已生成并执行: ${result.explanation}`,
    };
  },
);
