// ── Prompt Builder: Modular AI System Prompt Assembly ──
// Assembles prompts from modules: core role, tool catalog,
// template context, and example library.
// Supports both Chinese (pseudocode) and English (DSL) modes.

import { htnRegistry } from './htnRegistry';

export type PromptLanguage = 'chinese' | 'english';

export interface PromptBuilderOptions {
  language?: PromptLanguage;
  query?: string;
  includeTools?: boolean;
  includeTemplates?: boolean;
  includeExamples?: boolean;
  /** Max templates to include (0 = all). When query is set, top N by relevance. */
  maxTemplates?: number;
  /** Max examples to include */
  maxExamples?: number;
}

// ── Tool Catalog ─────────────────────────────────────────────

interface ToolEntry {
  syntax: string;
  description: string;
}

interface ToolCategory {
  name: string;
  tools: ToolEntry[];
}

// Chinese pseudocode tool catalog
const ZH_TOOLS: ToolCategory[] = [
  {
    name: '鼠标操作',
    tools: [
      { syntax: '点击 (x, y) [双击|右击|中键]', description: '鼠标点击指定坐标' },
      { syntax: '右键点击 (x, y)', description: '鼠标右键' },
      { syntax: '双击 (x, y)', description: '鼠标双击' },
      { syntax: '滚动 (x, y) 次数 [方向]', description: '鼠标滚动' },
      { syntax: '滑动 (x1,y1) 到 (x2,y2) [时长]', description: '滑动/拖拽操作' },
      { syntax: '拖拽 (x1,y1) 到 (x2,y2)', description: '拖拽操作' },
      { syntax: '拖拽图片 "路径"', description: '拖拽图片到目标' },
    ],
  },
  {
    name: '键盘操作',
    tools: [
      { syntax: '输入 "文本" [位置 (x,y)]', description: '在指定位置输入文本' },
      { syntax: '按键 键1[+键2+...]', description: '键盘快捷键，如 按键 ctrl+c' },
      { syntax: '粘贴输入', description: '从剪贴板粘贴' },
    ],
  },
  {
    name: '视觉识别（OCR/图片）',
    tools: [
      { syntax: '点击图片 "路径" [相似度=N] [超时=N]', description: '图片识别后点击' },
      { syntax: '文字点击 "文字" [相似度=N]', description: 'OCR 文字识别后点击' },
      { syntax: '查找图片 "路径" [相似度=N]', description: '查找图片在屏幕上的位置' },
      { syntax: '查找文字 "文字"', description: 'OCR 查找文字坐标' },
      { syntax: '提取文字', description: 'OCR 提取屏幕全部文字' },
      { syntax: 'VLM分析 "描述"', description: 'VLM 视觉理解分析' },
      { syntax: 'VLM判断 "描述"', description: 'VLM 视觉条件判断（返回真/假）' },
      { syntax: '检测画布 "目标"', description: '检测画布/目标区域' },
    ],
  },
  {
    name: '等待',
    tools: [
      { syntax: '等待 秒数', description: '等待指定时间' },
      { syntax: '等待图片 "路径" [超时=N]', description: '等待图片出现' },
      { syntax: '等待窗口 "标题" [超时=N]', description: '等待窗口出现' },
      { syntax: '等待屏幕变化 [超时=N]', description: '等待屏幕内容变化' },
      { syntax: '等待屏幕静止 [超时=N]', description: '等待屏幕停止变化' },
      { syntax: '等待区域稳定 (x,y,w,h) [超时=N]', description: '等待指定区域不再变化' },
      { syntax: '等待区域变化 (x,y,w,h) [超时=N]', description: '等待指定区域发生变化' },
      { syntax: '等待进程 "进程名" [超时秒]', description: '等待进程启动' },
      { syntax: '等待退出 "进程名" [超时秒]', description: '等待进程退出' },
      { syntax: '等待进度条 (x,y,w,h) [超时]', description: '等待进度条填满/清空/变化' },
    ],
  },
  {
    name: '截图与检测',
    tools: [
      { syntax: '截图', description: '全屏截图' },
      { syntax: '截取模板 "名称"', description: '截取区域存为模板' },
      { syntax: '框选区域', description: '交互式框选屏幕区域' },
      { syntax: '颜色检测 (x,y,w,h) 颜色 [容差]', description: '检测区域颜色值' },
    ],
  },
  {
    name: '弹窗处理',
    tools: [
      { syntax: '关闭弹窗 [自动|Esc|关闭按钮|空白点击|OCR查找|全部]', description: '检测并关闭弹窗' },
    ],
  },
  {
    name: '场景识别',
    tools: [
      { syntax: '场景识别 "场景名" [相似度=N]', description: '匹配当前屏幕场景' },
    ],
  },
  {
    name: '系统指令',
    tools: [
      { syntax: '锁定', description: '锁定工作站' },
      { syntax: '音量 0-100', description: '设置系统音量' },
      { syntax: '静音 [开启|关闭]', description: '切换静音' },
      { syntax: '启动 "程序路径"', description: '启动程序或打开文件' },
      { syntax: '终止 "进程名"', description: '终止进程' },
      { syntax: '激活 "窗口标题"', description: '激活窗口到前台' },
      { syntax: '壁纸 "图片路径"', description: '设置桌面壁纸' },
      { syntax: '关闭显示器', description: '关闭所有显示器（省电）' },
      { syntax: '睡眠', description: '计算机进入睡眠' },
      { syntax: '桌面', description: '显示桌面（Win+D）' },
      { syntax: '电池', description: '获取电池电量状态' },
      { syntax: '清空回收站', description: '清空回收站' },
      { syntax: '亮度 0-100', description: '设置屏幕亮度' },
      { syntax: '剪贴板写入 "文本"', description: '将文本写入剪贴板' },
      { syntax: '读取剪贴板', description: '读取剪贴板文本' },
    ],
  },
  {
    name: '窗口操作',
    tools: [
      { syntax: '最小化 [窗口标题]', description: '最小化窗口' },
      { syntax: '最大化 [窗口标题]', description: '最大化窗口' },
      { syntax: '关闭窗口 [窗口标题]', description: '关闭窗口' },
      { syntax: '调整大小 宽度 高度 [窗口标题]', description: '调整窗口尺寸' },
    ],
  },
  {
    name: '变量',
    tools: [
      { syntax: '$变量名="值"', description: '设置变量' },
      { syntax: '读取变量 变量名', description: '读取变量值' },
    ],
  },
  {
    name: '子程序',
    tools: [
      { syntax: '子程序 名称 [参数] ... 结束', description: '定义可复用的子程序' },
      { syntax: '调用 "脚本名" [参数]', description: '调用另一个脚本或子程序' },
      { syntax: '返回 [值]', description: '从子程序返回' },
    ],
  },
  {
    name: '控制流',
    tools: [
      { syntax: '循环 N:', description: '计数循环 N 次（冒号后换行缩进）' },
      { syntax: '无限循环:', description: '无限循环（用 跳出 退出）' },
      { syntax: '当 条件:', description: '条件循环（while）' },
      { syntax: '直到 条件:', description: '直到条件满足才停止循环' },
      { syntax: '如果 条件: ... 否则: ...', description: '条件分支' },
      { syntax: '跳出', description: '跳出当前循环' },
      { syntax: '并行: 分支1 ... 分支2 ...', description: '并行执行多个分支' },
    ],
  },
  {
    name: '条件（用于 如果/当/直到）',
    tools: [
      { syntax: '图片存在 "路径" [相似度=N]', description: '检测图片是否存在' },
      { syntax: '窗口存在 "标题"', description: '检测窗口是否存在' },
      { syntax: '变量等于 变量名 "值"', description: '变量比较（等于）' },
      { syntax: '变量大于 变量名 "值"', description: '变量比较（大于）' },
      { syntax: '变量小于 变量名 "值"', description: '变量比较（小于）' },
      { syntax: '颜色匹配 (x,y,w,h) 颜色 [容差]', description: '检测区域颜色匹配' },
      { syntax: '场景匹配 "场景名"', description: '场景条件判断' },
      { syntax: '真', description: '始终为真' },
    ],
  },
  {
    name: '工具调用（150+ 内置工具）',
    tools: [
      { syntax: '调用工具 "工具名" [参数]', description: '调用任意内置自动化工具' },
    ],
  },
  {
    name: '宏录制',
    tools: [
      { syntax: '开始录制 [名称]', description: '开始宏录制（F6 热键）' },
      { syntax: '停止录制', description: '停止录制并保存' },
      { syntax: '播放宏 "名称" [速度=N]', description: '播放已录制的宏' },
      { syntax: '宏转流程 "名称"', description: '将宏转换为流程图节点' },
    ],
  },
];

// English DSL tool catalog
const EN_TOOLS: ToolCategory[] = [
  {
    name: 'Mouse Operations',
    tools: [
      { syntax: 'CLICK <x> <y> [single|double|right|middle]', description: 'Mouse click at coordinates' },
      { syntax: 'SCROLL <x> <y> <clicks> [up|down]', description: 'Mouse scroll wheel' },
      { syntax: 'SWIPE <x1> <y1> <x2> <y2> [duration]', description: 'Swipe/drag across screen' },
      { syntax: 'DRAG <x1> <y1> <x2> <y2>', description: 'Drag operation' },
      { syntax: 'DRAG_IMAGE <"path">', description: 'Drag image target' },
    ],
  },
  {
    name: 'Keyboard',
    tools: [
      { syntax: 'INPUT_TEXT <x> <y> <"text">', description: 'Input text at position' },
      { syntax: 'TYPE <"text">', description: 'Type text at current focus' },
      { syntax: 'HOTKEY <key1> [key2 ...]', description: 'Keyboard shortcut, e.g. HOTKEY ctrl c' },
      { syntax: 'TYPE_CLIP', description: 'Paste from clipboard' },
    ],
  },
  {
    name: 'Vision / OCR',
    tools: [
      { syntax: 'CLICK_IMAGE <"path"> [similarity]', description: 'Find and click image' },
      { syntax: 'IMAGE_CLICK <"path"> [similarity]', description: 'Alias for CLICK_IMAGE' },
      { syntax: 'CLICK_TEXT <"text"> [similarity]', description: 'OCR text recognition then click' },
      { syntax: 'IMAGE_FIND <"path"> [similarity]', description: 'Find image location on screen' },
      { syntax: 'OCR_FIND <"text">', description: 'OCR find text coordinates' },
      { syntax: 'OCR_EXTRACT', description: 'OCR extract all on-screen text' },
      { syntax: 'VLM_CONDITION <"description">', description: 'VLM visual condition (true/false)' },
      { syntax: 'VLM_ANALYZE <"description">', description: 'VLM visual analysis' },
      { syntax: 'FINDCANVAS <target>', description: 'Detect canvas/target area' },
    ],
  },
  {
    name: 'Waiting',
    tools: [
      { syntax: 'WAIT <seconds>', description: 'Wait specified duration' },
      { syntax: 'WAITSCREEN [timeout]', description: 'Wait for screen to change' },
      { syntax: 'SCREENSTABLE [timeout]', description: 'Wait for screen to stop changing' },
      { syntax: 'REGION_WAIT <stable|change> <x> <y> <w> <h> [timeout]', description: 'Wait for region condition' },
      { syntax: 'WAIT_PROCESS <name> [timeout]', description: 'Wait for process to start' },
      { syntax: 'WAIT_EXIT <name> [timeout]', description: 'Wait for process to exit' },
      { syntax: 'PROGRESS_WAIT <fill|empty|change> <x> <y> <w> <h> [timeout]', description: 'Wait for progress bar' },
    ],
  },
  {
    name: 'Screenshot & Region',
    tools: [
      { syntax: 'SCREENSHOT', description: 'Full screen capture' },
      { syntax: 'TEMPLATE_CAPTURE <name>', description: 'Capture region as template' },
      { syntax: 'REGION_SELECT', description: 'Interactive region selection' },
      { syntax: 'COLOR_CHECK <x> <y> <w> <h> <color> [tolerance]', description: 'Check region color' },
    ],
  },
  {
    name: 'Popup Handling',
    tools: [
      { syntax: 'POPUP_CLOSE [auto|esc|close_btn|click_away|ocr_close|all]', description: 'Detect and close popup dialogs' },
    ],
  },
  {
    name: 'System Commands',
    tools: [
      { syntax: 'LOCK', description: 'Lock workstation' },
      { syntax: 'VOLUME <0-100>', description: 'Set system volume' },
      { syntax: 'MUTE [ON|OFF]', description: 'Toggle mute' },
      { syntax: 'LAUNCH <path>', description: 'Launch program or open file' },
      { syntax: 'KILL <name>', description: 'Terminate process by name' },
      { syntax: 'FOCUS <title>', description: 'Bring window to foreground' },
      { syntax: 'WALLPAPER <path>', description: 'Set desktop wallpaper' },
      { syntax: 'DISPLAY_OFF', description: 'Turn off monitors (power save)' },
      { syntax: 'SLEEP', description: 'Put computer to sleep' },
      { syntax: 'DESKTOP', description: 'Show desktop (Win+D)' },
      { syntax: 'BATTERY', description: 'Get battery status' },
      { syntax: 'EMPTY_BIN', description: 'Empty recycle bin' },
      { syntax: 'BRIGHTNESS <0-100>', description: 'Set screen brightness' },
      { syntax: 'CLIPBOARD_SET <text>', description: 'Write text to clipboard' },
      { syntax: 'CLIPBOARD_GET', description: 'Read clipboard text' },
    ],
  },
  {
    name: 'Window Control',
    tools: [
      { syntax: 'MINIMIZE [title]', description: 'Minimize window' },
      { syntax: 'MAXIMIZE [title]', description: 'Maximize window' },
      { syntax: 'CLOSE_WINDOW [title]', description: 'Close window' },
      { syntax: 'RESIZE_WINDOW <width> <height> [title]', description: 'Resize window' },
    ],
  },
  {
    name: 'Variables',
    tools: [
      { syntax: 'SET <name> <value>', description: 'Set variable value' },
    ],
  },
  {
    name: 'Subroutines',
    tools: [
      { syntax: 'SUBROUTINE <name> [params] ... END', description: 'Define reusable subroutine' },
      { syntax: 'CALL <name> [args]', description: 'Call another script or subroutine' },
      { syntax: 'RETURN [value]', description: 'Return from subroutine' },
    ],
  },
  {
    name: 'Control Flow',
    tools: [
      { syntax: 'LOOP <count|INFINITE> ... END', description: 'Count or infinite loop' },
      { syntax: 'WHILE <condition> ... END', description: 'Conditional while loop' },
      { syntax: 'UNTIL <condition> ... END', description: 'Loop until condition met' },
      { syntax: 'IF <condition> ... [ELIF ...] [ELSE ...] END', description: 'Conditional branch' },
      { syntax: 'BREAK', description: 'Break out of current loop' },
      { syntax: 'PARALLEL WITH ... END WITH ... END END', description: 'Parallel execution' },
    ],
  },
  {
    name: 'Conditions (for IF/WHILE/UNTIL)',
    tools: [
      { syntax: 'image_exists <"path"> [similarity]', description: 'Check if image exists' },
      { syntax: 'window_exists <"title">', description: 'Check if window exists' },
      { syntax: 'variable_equals <name> <value>', description: 'Variable equals comparison' },
      { syntax: 'variable_greater <name> <value>', description: 'Variable greater-than comparison' },
      { syntax: 'variable_less <name> <value>', description: 'Variable less-than comparison' },
      { syntax: 'color_match <x> <y> <w> <h> <color> [tolerance]', description: 'Check region color match' },
      { syntax: 'scene_match <"name"> [similarity]', description: 'Scene condition check' },
      { syntax: 'always_true', description: 'Always true' },
    ],
  },
  {
    name: 'Tool Call (150+ built-in @feature tools)',
    tools: [
      { syntax: 'TOOL_CALL <feature_name> [params]', description: 'Call any built-in automation tool' },
    ],
  },
  {
    name: 'Macro Recording',
    tools: [
      { syntax: 'MACRO_START [name]', description: 'Start macro recording (F6 hotkey)' },
      { syntax: 'MACRO_STOP', description: 'Stop recording and save' },
      { syntax: 'MACRO_PLAY <name> [speed]', description: 'Play back a recorded macro' },
      { syntax: 'MACRO_TO_FLOW <name>', description: 'Convert macro to flowchart nodes' },
    ],
  },
];

// ── Example Library ──────────────────────────────────────────

interface ExampleEntry {
  title: string;
  description: string;
  chinese: string;
  english: string;
  tags: string[];
}

const EXAMPLES: ExampleEntry[] = [
  {
    title: '打开应用并输入文本',
    description: '启动程序，等待加载，然后输入文字。展示最基本的 点击→等待→输入 时序。',
    tags: ['basic', '启动', '输入'],
    chinese: '按键 win+r\n等待 0.5\n输入 "notepad"\n按键 enter\n等待 1\n输入 "hello world"',
    english: 'HOTKEY win r\nWAIT 0.5\nTYPE "notepad"\nHOTKEY enter\nWAIT 1\nTYPE "hello world"',
  },
  {
    title: '条件判断 + 循环：批量处理文件',
    description: '循环处理多个项目，如果出现弹窗则关闭，截图记录进度。展示 循环→条件→弹窗 组合。',
    tags: ['loop', '条件', '批量', '弹窗'],
    chinese: `循环 5:
  点击 (500, 400)
  等待 2
  如果 图片存在 "progress.png":
    等待进度条 (100, 500, 300, 20)
  关闭弹窗 [自动] (可选)
  截图`,
    english: `LOOP 5
  CLICK 500 400
  WAIT 2
  IF image_exists "progress.png" 90
    PROGRESS_WAIT fill 100 500 300 20 30
  END
  POPUP_CLOSE auto
  SCREENSHOT
END`,
  },
  {
    title: 'OCR 识别 + 多步骤登录',
    description: '在登录页面自动填写账号密码，OCR 验证结果，失败则重试。人机交互：需要用户框选输入框位置。',
    tags: ['登录', 'OCR', '验证', '重试'],
    chinese: `输入 "admin@example.com" 位置 (300, 400)
输入 "mypassword" 位置 (300, 480)
点击 (300, 550)
等待 3
文字点击 "登录成功" 相似度 80 (可选)
如果 图片存在 "login_error.png":
  点击 (300, 550)
  等待 3
  文字点击 "登录成功" 相似度 80`,
    english: `INPUT_TEXT 300 400 "admin@example.com"
INPUT_TEXT 300 480 "mypassword"
CLICK 300 550
WAIT 3
CLICK_TEXT "login success" 80
IF image_exists "login_error.png" 90
  CLICK 300 550
  WAIT 3
  CLICK_TEXT "login success" 80
END`,
  },
  {
    title: '网页数据采集（滚动 + 截图 + 翻页）',
    description: '自动滚动网页内容，截图保存，翻页继续采集。展示 循环→滚动→截图 采集模式。',
    tags: ['网页', '采集', '滚动', '截图'],
    chinese: `循环 3:
  截图
  滑动 (500, 800) 到 (500, 200)
  等待 2
  点击 (1800, 500) — 翻页按钮
  等待 1`,
    english: `LOOP 3
  SCREENSHOT
  SWIPE 500 800 500 200 0.5
  WAIT 2
  CLICK 1800 500
  WAIT 1
END`,
  },
  {
    title: '子程序调用 + 并行执行',
    description: '定义子程序后并行执行多个任务。展示 子程序→调用→并行 组合。',
    tags: ['子程序', '并行', '高级'],
    chinese: `子程序 截图记录:
  截图
  等待 5

并行:
  调用 "截图记录"
  调用 "截图记录"
  调用 "截图记录"`,
    english: `SUBROUTINE capture_log
  SCREENSHOT
  WAIT 5
END

PARALLEL
  WITH
    CALL capture_log
  END
  WITH
    CALL capture_log
  END
  WITH
    CALL capture_log
  END
END`,
  },
  {
    title: '智能检测流水线（OCR + 图片 + 弹窗）',
    description: '先截图检测区域，OCR 识别目标内容并点击，处理可能出现的弹窗。展示 视觉+弹窗 自动化。',
    tags: ['检测', 'OCR', '图片', '弹窗'],
    chinese: `截图
文字点击 "确认" 相似度 80 (可选)
等待 2
关闭弹窗 [自动] (可选)
如果 图片存在 "next_button.png":
  点击图片 "next_button.png"
  等待 1
  文字点击 "完成"`,
    english: `SCREENSHOT
CLICK_TEXT "confirm" 80
WAIT 2
POPUP_CLOSE auto
IF image_exists "next_button.png" 90
  CLICK_IMAGE "next_button.png" 90
  WAIT 1
  CLICK_TEXT "done"
END`,
  },
  {
    title: '表单填写器（循环填多个字段）',
    description: '用变量和循环在表单上自动填写多个字段。展示 变量+循环+输入 组合。',
    tags: ['表单', '填写', '变量', '循环'],
    chinese: `$i="0"
循环 5:
  $i="1"
  输入 "字段内容" 位置 (300, 400)
  等待 0.5
点击 (300, 700)
等待 2`,
    english: `SET i 0
LOOP 5
  SET i i+1
  INPUT_TEXT 300 400 "field content"
  WAIT 0.5
END
CLICK 300 700
WAIT 2`,
  },
  {
    title: '窗口监控（等待进程 + 场景判断）',
    description: '等待进程启动后检测窗口场景，根据场景执行不同操作。展示 进程→场景→分支 组合。',
    tags: ['进程', '窗口', '场景', '监控'],
    chinese: `等待进程 "notepad.exe" 超时 30
激活 "记事本"
场景识别 "编辑界面"
如果 场景匹配 "编辑界面":
  输入 "hello" 位置 (100, 200)
否则:
  点击 (100, 100)`,
    english: `WAIT_PROCESS notepad.exe 30
FOCUS "notepad"
SCENE_CONDITION "editor_view"
IF scene_match "editor_view"
  INPUT_TEXT 100 200 "hello"
ELSE
  CLICK 100 100
END`,
  },
];

// ── Prompt Builder ──────────────────────────────────────────

function buildToolContext(lang: PromptLanguage): string {
  const catalog = lang === 'chinese' ? ZH_TOOLS : EN_TOOLS;
  let ctx = `\n## 可用操作（${catalog.length} 类）\n`;
  for (const cat of catalog) {
    ctx += `\n### ${cat.name}\n`;
    for (const t of cat.tools) {
      ctx += `- \`${t.syntax}\` — ${t.description}\n`;
    }
  }
  return ctx;
}

function buildTemplateContext(maxTemplates?: number): string {
  const htnNodes = htnRegistry.getAll();

  if (htnNodes.length === 0) {
    return '';
  }

  // Show all or limited templates
  const roots = htnRegistry.getRoots();
  const allNodes = maxTemplates && maxTemplates > 0
    ? roots.slice(0, maxTemplates)
    : roots;

  if (allNodes.length === 0) return '';

  let ctx = '\n## 可用模板\n优先使用已有模板，不要从头生成重复逻辑：\n\n';

  for (const node of allNodes) {
    ctx += `### ${node.name} (${node.id})\n`;
    if (node.aliases.length > 0) {
      ctx += `  别名: ${node.aliases.join(', ')}\n`;
    }
    ctx += `  用途: ${node.description}\n`;
    if (node.category) {
      ctx += `  分类: ${node.category}\n`;
    }
    if (node.keywords.length > 0) {
      ctx += `  关键词: ${node.keywords.join(', ')}\n`;
    }

    // Parameters
    const required = node.parameters.filter(p => p.required);
    const optional_p = node.parameters.filter(p => !p.required);
    if (required.length > 0) {
      ctx += `  必填参数: ${required.map(p => `${p.name}（${p.description}）`).join(', ')}\n`;
    }
    if (optional_p.length > 0) {
      ctx += `  可选参数: ${optional_p.map(p => `${p.name}${p.default !== undefined ? `=${p.default}` : ''}（${p.description}）`).join(', ')}\n`;
    }

    // Internal steps (children)
    if (node.children && node.children.length > 0) {
      ctx += `  内部步骤:\n`;
      for (const step of node.children) {
        const child = htnRegistry.get(step.childId);
        const childName = child?.name || step.childId;
        const opt = step.optional ? ' (可选)' : '';
        ctx += `    ${step.description ? `${step.description} → ` : ''}${childName}${opt}\n`;
      }
    }

    // Structure info
    const size = htnRegistry.structureBonus(node.id);
    if (size > 1) {
      ctx += `  展开步骤数: ${size}\n`;
    }

    ctx += '\n';
  }

  ctx += '引用模板的格式：在伪代码中使用 `调用 "模板名"` 或直接引用模板步骤。\n';
  return ctx;
}

function buildExampleContext(lang: PromptLanguage, maxExamples?: number): string {
  const count = maxExamples && maxExamples > 0 ? Math.min(maxExamples, EXAMPLES.length) : EXAMPLES.length;
  let ctx = `\n## 示例（${count} 个）\n以下示例展示真实场景的伪代码写法：\n\n`;

  const selected = EXAMPLES.slice(0, count);
  for (const ex of selected) {
    ctx += `### ${ex.title}\n`;
    ctx += `${ex.description}\n`;
    ctx += '```\n';
    ctx += lang === 'chinese' ? ex.chinese : ex.english;
    ctx += '\n```\n\n';
  }

  return ctx;
}

function buildCorePrompt(lang: PromptLanguage): string {
  if (lang === 'chinese') {
    return `你是一个桌面自动化脚本生成器。
将用户需求转换为灵动脚本伪代码。返回 JSON：{"pseudocode":"伪代码文本","explanation":"简短解释"}

## 核心原则
1. 如果用户需求与已有模板匹配，优先引用模板（用 \`调用 "模板名"\`），不要重新生成
2. 坐标应合理（通常屏幕范围 0-1920 x 0-1080，点击按钮一般在 300-600, 300-800）
3. 如果用户明确指定了时间间隔（如「N秒后」「等N秒」「隔N秒」「每N秒」），必须严格遵守，在对应操作前添加「等待 N」步骤
4. 操作之间加适当的等待时间（普通步骤间 0.5-2 秒，启动应用后 1-2 秒）
5. 弹窗关闭、OCR 验证等非关键步骤标记为"（可选）"
6. 复杂操作拆分为清晰的步骤序列
7. 涉及视觉检测的步骤，如果不知道具体路径/文字，用合理描述占位
8. 启动程序时，可以用中文名称直接启动（如 \`启动 "微信"\`、\`启动 "记事本"\`、\`启动 "微星小飞机"\`），系统会自动解析路径。无需提前查找完整路径
  } else {
    return `You are a DSL generator for desktop automation named Lingdong Script.
Generate ONLY raw DSL code, no markdown, no explanations in the DSL portion.
Return JSON: {"pseudocode":"DSL code","explanation":"brief explanation"}

## Core Principles
1. If the user request matches an existing template, reference it with \`CALL "template_name"\` instead of regenerating
2. Use reasonable coordinates (typical screen: 0-1920 x 0-1080, buttons usually at 300-600, 300-800)
3. If the user specifies explicit time delays (like "after N seconds", "wait N seconds", "every N seconds"), strictly follow them by adding a WAIT N step before the corresponding action
4. Add reasonable WAIT steps between ordinary actions (0.5-2 seconds, 1-2 seconds after launching an app)
5. Mark non-critical steps (popup close, OCR verify) with comment "optional"
6. Break complex tasks into clear step sequences
7. Use placeholder paths/text when specific values are unknown
8. When launching a program, use the common name directly (e.g. \`启动 "微信"\`, \`启动 "记事本"\`, \`启动 "chrome"\`). The system auto-resolves them to full paths — no need to look up paths manually.
  }
}

// ── Main Builder ─────────────────────────────────────────────

export class PromptBuilder {
  /**
   * Assemble a complete system prompt from modular sections.
   */
  build(options: PromptBuilderOptions = {}): string {
    const {
      language = 'chinese',
      includeTools = true,
      includeTemplates = true,
      includeExamples = true,
      maxTemplates,
      maxExamples,
    } = options;

    const parts: string[] = [];

    // 1. Core role + principles
    parts.push(buildCorePrompt(language));

    // 2. Tool catalog by category
    if (includeTools) {
      parts.push(buildToolContext(language));
    }

    // 3. Template context (HTN hierarchy)
    if (includeTemplates) {
      parts.push(buildTemplateContext(maxTemplates));
    }

    // 4. Examples
    if (includeExamples) {
      parts.push(buildExampleContext(language, maxExamples));
    }

    return parts.join('\n');
  }

  /**
   * Build just the tool catalog. Useful for injecting tool descriptions
   * into other prompts (e.g. Claude Code tool descriptions).
   */
  buildToolContext(language: PromptLanguage = 'chinese'): string {
    return buildToolContext(language);
  }
}

export const promptBuilder = new PromptBuilder();
