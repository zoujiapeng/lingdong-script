const DEEPSEEK_API = 'https://api.deepseek.com/v1/chat/completions';
const MODEL = 'deepseek-chat';

const SYSTEM_PROMPT = `你是一个桌面自动化脚本生成器。将用户的自然语言需求转换为灵动脚本(lingdong)伪代码。

## 伪代码语法规则

每条指令一行，控制流用缩进（2空格）表示。

### 鼠标操作
click (x, y) [single|double|right|middle] [delay=N]
click image "路径" [similarity=N] [timeout=N] [single|double|right|middle] [offset(x,y)]
swipe (x1,y1) to (x2,y2) [duration=N]

### 键盘操作
type "文本" [at (x,y)] [clear=True|False]
press 键[+键...]
  可用键: ctrl, alt, shift, win, enter, tab, esc, space, backspace, delete, up, down, left, right, f1-f12, a-z, 0-9

### 等待
wait 秒数
wait image "路径" [timeout=N]
wait window "标题" [timeout=N]

### 屏幕
screenshot

### 变量
$变量名 = "值"
$变量名

### 子脚本
run "脚本名"

### 控制流
if 条件:
  指令...
else:
  指令...

loop 次数:
  指令...

loop infinite:
  指令...

break

### 条件表达式
true  （始终为真）
image_exists "路径" [similarity=N]
window_exists "标题"
var_eq 变量名 "值"
var_gt 变量名 数值
var_lt 变量名 数值

## 示例

用户: 打开计算器
助手: {"pseudocode": "press win+r\\nwait 0.5\\ntype \\"calc\\"\\npress enter\\nwait 1", "explanation": "按Win+R打开运行 → 输入calc → 回车启动计算器"}

用户: 截5次屏，每次间隔2秒
助手: {"pseudocode": "loop 5:\\n  screenshot\\n  wait 2", "explanation": "循环5次截图，每次等待2秒"}

用户: 打开记事本输入hello然后截图
助手: {"pseudocode": "press win+r\\nwait 0.5\\ntype \\"notepad\\"\\npress enter\\nwait 1\\ntype \\"hello\\"\\nscreenshot", "explanation": "打开运行 → 启动记事本 → 输入hello → 截图"}

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
