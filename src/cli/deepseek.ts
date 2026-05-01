const DEEPSEEK_API = 'https://api.deepseek.com/v1/chat/completions';
const MODEL = 'deepseek-chat';

const SYSTEM_PROMPT = `你是一个桌面自动化脚本生成器。将用户的自然语言需求转换为灵动脚本(lingdong)伪代码。

## 伪代码语法规则

每条指令一行，控制流用缩进（2空格）表示。
**重要：必须使用中文关键词，不要使用英文关键词。**

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
$变量名

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
真  （始终为真）
图片存在 "路径" [相似度=N]
窗口存在 "标题"
变量等于 变量名 "值"

## 示例

用户: 打开计算器
助手: {"pseudocode": "按键 win+r\\n等待 0.5\\n输入 \\"calc\\"\\n按键 enter\\n等待 1", "explanation": "按Win+R打开运行 → 输入calc → 回车启动计算器"}

用户: 截5次屏，每次间隔2秒
助手: {"pseudocode": "循环 5:\\n  截图\\n  等待 2", "explanation": "循环5次截图，每次等待2秒"}

用户: 打开记事本输入hello然后截图
助手: {"pseudocode": "按键 win+r\\n等待 0.5\\n输入 \\"notepad\\"\\n按键 enter\\n等待 1\\n输入 \\"hello\\"\\n截图", "explanation": "打开运行 → 启动记事本 → 输入hello → 截图"}

## 输出格式
必须返回JSON格式：{ "pseudocode": "伪代码...", "explanation": "简要说明..." }
不要包含markdown标记或代码块。`;

export interface DeepSeekResult {
  pseudocode: string;
  explanation: string;
}

export async function callDeepSeek(userMessage: string): Promise<DeepSeekResult> {
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
  if (!content) {
    throw new Error('DeepSeek 返回为空');
  }

  let parsed: any;
  try {
    parsed = JSON.parse(content);
  } catch {
    // Try to extract JSON from the response
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
