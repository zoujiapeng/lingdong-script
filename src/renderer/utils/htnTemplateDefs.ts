// ── HTN Base Primitives & Composite Template Definitions ──
// These are registered into htnRegistry at startup. Composite templates
// reference child nodes (primitives or other composites) by id, forming
// a hierarchy the HTN planner decomposes from top to bottom.

export interface HTNYamlDef {
  id?: string;
  name: string;
  description?: string;
  aliases?: string[];
  keywords?: string[];
  tags?: string[];
  category?: string;
  parameters?: any[];
  children?: any[];
  primitive?: {
    dslKeyword: string;
    flowType: string;
    paramBinding: Record<string, string>;
  };
}

// ── Base Primitives ──────────────────────────────────────────

export const BASE_PRIMITIVES: HTNYamlDef[] = [
  {
    id: 'prim-click',
    name: '点击',
    description: '点击屏幕指定坐标',
    aliases: ['点击', '单击', 'click', 'tap'],
    keywords: ['点击', 'click', '坐标', '鼠标'],
    tags: ['action', 'mouse'],
    category: 'action',
    parameters: [
      { name: 'x', type: 'number', description: 'X 坐标', required: true, acquire: 'region_select' },
      { name: 'y', type: 'number', description: 'Y 坐标', required: true, acquire: 'region_select' },
      { name: 'method', type: 'string', description: '点击方式', required: false, default: 'click' },
    ],
    primitive: { dslKeyword: 'CLICK', flowType: 'click', paramBinding: { x: 'x', y: 'y' } },
  },
  {
    id: 'prim-click-text',
    name: '文本点击',
    description: '点击指定文本（OCR）',
    aliases: ['文字点击', 'ocr点击', 'click text'],
    keywords: ['文字', '文本', 'OCR', '点击'],
    tags: ['action', 'ocr'],
    category: 'action',
    parameters: [
      { name: 'text', type: 'string', description: '要点击的文字', required: true, acquire: 'ask_text' },
      { name: 'similarity', type: 'number', description: '匹配相似度', required: false, default: 0.8 },
    ],
    primitive: { dslKeyword: 'CLICK_TEXT', flowType: 'click_text', paramBinding: { text: 'text' } },
  },
  {
    id: 'prim-input-text',
    name: '输入文本',
    description: '在指定位置输入文字',
    aliases: ['输入', '打字', 'type', 'input'],
    keywords: ['输入', '文本', '打字', '文字'],
    tags: ['action', 'keyboard'],
    category: 'action',
    parameters: [
      { name: 'text', type: 'string', description: '要输入的文本', required: true, acquire: 'ask_text' },
      { name: 'x', type: 'number', description: '输入位置 X', required: true, acquire: 'region_select' },
      { name: 'y', type: 'number', description: '输入位置 Y', required: true, acquire: 'region_select' },
    ],
    primitive: { dslKeyword: 'TYPE', flowType: 'input_text', paramBinding: { text: 'text', x: 'x', y: 'y' } },
  },
  {
    id: 'prim-wait',
    name: '等待',
    description: '等待指定时长或条件',
    aliases: ['等待', 'wait', '延迟', 'sleep'],
    keywords: ['等待', '延迟', 'wait', 'sleep'],
    tags: ['control', 'time'],
    category: 'control',
    parameters: [
      { name: 'duration', type: 'number', description: '等待秒数', required: false, default: 1 },
    ],
    primitive: { dslKeyword: 'WAIT', flowType: 'wait', paramBinding: { duration: 'duration' } },
  },
  {
    id: 'prim-screenshot',
    name: '截图',
    description: '截取屏幕或区域',
    aliases: ['截图', 'screenshot', '截屏', 'capture'],
    keywords: ['截图', 'screenshot', '截屏', '图像'],
    tags: ['action', 'image'],
    category: 'action',
    parameters: [
      { name: 'region', type: 'region', description: '截图区域', required: false, acquire: 'region_select' },
    ],
    primitive: { dslKeyword: 'SCREENSHOT', flowType: 'screenshot', paramBinding: {} },
  },
  {
    id: 'prim-keyboard',
    name: '键盘',
    description: '发送键盘快捷键',
    aliases: ['按键', '快捷键', 'hotkey', 'key'],
    keywords: ['键盘', '快捷键', '按键', 'hotkey'],
    tags: ['action', 'keyboard'],
    category: 'action',
    parameters: [
      { name: 'keys', type: 'string', description: '按键组合', required: true, acquire: 'ask_text' },
    ],
    primitive: { dslKeyword: 'KEY', flowType: 'keyboard', paramBinding: { keys: 'keys' } },
  },
  {
    id: 'prim-swipe',
    name: '滑动',
    description: '从起点滑动到终点',
    aliases: ['滑动', 'swipe', 'drag', '拖拽'],
    keywords: ['滑动', 'swipe', '拖拽', 'drag'],
    tags: ['action', 'mouse'],
    category: 'action',
    parameters: [
      { name: 'start_x', type: 'number', description: '起点 X', required: true },
      { name: 'start_y', type: 'number', description: '起点 Y', required: true },
      { name: 'end_x', type: 'number', description: '终点 X', required: true },
      { name: 'end_y', type: 'number', description: '终点 Y', required: true },
    ],
    primitive: { dslKeyword: 'SWIPE', flowType: 'swipe', paramBinding: { start_x: 'start_x', start_y: 'start_y', end_x: 'end_x', end_y: 'end_y' } },
  },
  {
    id: 'prim-popup-close',
    name: '关闭弹窗',
    description: '检测并关闭弹窗',
    aliases: ['关弹窗', '关闭弹窗', 'close popup', 'dismiss'],
    keywords: ['弹窗', 'popup', '关闭'],
    tags: ['action', 'window'],
    category: 'action',
    parameters: [],
    primitive: { dslKeyword: 'POPUP_CLOSE', flowType: 'popup_close', paramBinding: {} },
  },
  {
    id: 'prim-scene-check',
    name: '场景识别',
    description: '识别当前屏幕场景',
    aliases: ['场景', 'scene', '识别场景'],
    keywords: ['场景', '识别', '判断'],
    tags: ['control', 'perception'],
    category: 'control',
    parameters: [
      { name: 'scene_name', type: 'string', description: '场景名称', required: true },
    ],
    primitive: { dslKeyword: 'SCENE', flowType: 'scene_condition', paramBinding: { scene_name: 'scene_name' } },
  },
  {
    id: 'prim-loop',
    name: '循环（原语标记）',
    description: '循环控制（作为原语标记，复合循环见 com-loop）',
    aliases: ['loop'],
    keywords: ['循环', 'loop', '重复'],
    tags: ['control'],
    category: 'control',
    parameters: [
      { name: 'count', type: 'number', description: '循环次数', required: false, default: 10 },
    ],
    primitive: { dslKeyword: 'LOOP', flowType: 'loop', paramBinding: { count: 'count' } },
  },
];

// ── Composite Templates ──────────────────────────────────────

export const COMPOSITE_TEMPLATES: HTNYamlDef[] = [
  // ── 网页数据采集 ──
  {
    id: 'com-web-scraper',
    name: '网页数据采集与监控',
    description: '自动滚动→截图→翻页→循环采集多页数据',
    aliases: ['网页采集', 'web scraper', '爬虫', '数据采集'],
    keywords: ['网页', '采集', '爬虫', '数据', '滚动', '翻页', '截图'],
    tags: ['web', 'data', 'automation'],
    category: 'complex',
    parameters: [
      { name: 'url', type: 'string', description: '目标网页 URL', required: true, acquire: 'ask_text' },
      { name: 'pages', type: 'number', description: '采集页数', required: false, default: 3 },
    ],
    children: [
      { childId: 'prim-screenshot', description: '截图当前页', paramBindings: {} },
      { childId: 'prim-swipe', description: '向下滚动', paramBindings: { start_x: '500', start_y: '500', end_x: '500', end_y: '200' } },
      { childId: 'prim-wait', description: '等待加载', paramBindings: { duration: '2' } },
    ],
  },

  // ── 登录自动化 ──
  {
    id: 'com-login',
    name: '登录自动化',
    description: '自动输入账号密码登录系统',
    aliases: ['登录', 'login', 'signin', '账号登录'],
    keywords: ['登录', '账号', '密码', '认证', 'login'],
    tags: ['app', 'login', 'form'],
    category: 'app',
    parameters: [
      { name: 'username', type: 'string', description: '登录用户名', required: true, acquire: 'ask_text' },
      { name: 'password', type: 'string', description: '登录密码', required: true, acquire: 'ask_text' },
      { name: 'login_x', type: 'number', description: '登录按钮 X', required: true, acquire: 'region_select' },
      { name: 'login_y', type: 'number', description: '登录按钮 Y', required: true, acquire: 'region_select' },
    ],
    children: [
      { childId: 'prim-input-text', description: '输入用户名', paramBindings: { text: '$username', x: '300', y: '400' } },
      { childId: 'prim-input-text', description: '输入密码', paramBindings: { text: '$password', x: '300', y: '480' } },
      { childId: 'prim-click', description: '点击登录按钮', paramBindings: { x: '$login_x', y: '$login_y' } },
      { childId: 'prim-wait', description: '等待登录完成', paramBindings: { duration: '3' } },
      { childId: 'prim-scene-check', description: '验证登录成功', paramBindings: { scene_name: '登录成功' }, optional: true },
    ],
  },

  // ── 表单填写器 ──
  {
    id: 'com-form-filler',
    name: '表单填写器',
    description: '循环填写多个表单字段并提交',
    aliases: ['填表单', '表单填写', 'form fill', '自动填表'],
    keywords: ['表单', '填写', '字段', '提交', 'form'],
    tags: ['form', 'data', 'automation'],
    category: 'app',
    parameters: [
      { name: 'fields_count', type: 'number', description: '要填写的字段数量', required: false, default: 5 },
      { name: 'field_start_x', type: 'number', description: '第一个字段 X', required: true, acquire: 'region_select' },
      { name: 'field_start_y', type: 'number', description: '第一个字段 Y', required: true, acquire: 'region_select' },
      { name: 'field_step_y', type: 'number', description: '字段行间距', required: false, default: 60 },
      { name: 'submit_x', type: 'number', description: '提交按钮 X', required: true, acquire: 'region_select' },
      { name: 'submit_y', type: 'number', description: '提交按钮 Y', required: true, acquire: 'region_select' },
    ],
    children: [
      { childId: 'prim-input-text', description: '填写字段 1', paramBindings: { x: '$field_start_x', y: '$field_start_y' } },
      { childId: 'prim-input-text', description: '填写字段 2', paramBindings: { x: '$field_start_x', y: '$field_start_y' } },
      { childId: 'prim-click', description: '点击提交', paramBindings: { x: '$submit_x', y: '$submit_y' } },
      { childId: 'prim-wait', description: '等待提交完成', paramBindings: { duration: '2' } },
    ],
  },

  // ── 多目标智能检测流水线 ──
  {
    id: 'com-image-pipeline',
    name: '多目标智能检测流水线',
    description: '多目标图片检测 + OCR 验证 + 失败重试循环',
    aliases: ['图片检测', 'image pipeline', '目标检测', '视觉检测'],
    keywords: ['图片', '检测', 'OCR', '目标', '识别', '重试'],
    tags: ['image', 'vision', 'automation'],
    category: 'complex',
    parameters: [
      { name: 'target_count', type: 'number', description: '检测目标数量', required: false, default: 3 },
      { name: 'retry_count', type: 'number', description: '失败重试次数', required: false, default: 2 },
    ],
    children: [
      { childId: 'prim-screenshot', description: '截取检测区域', paramBindings: {} },
      { childId: 'prim-click-text', description: '点击检测到的目标', paramBindings: { text: '确认' }, optional: true },
      { childId: 'prim-wait', description: '等待处理完成', paramBindings: { duration: '2' } },
      { childId: 'prim-popup-close', description: '关闭可能出现的弹窗', paramBindings: {}, optional: true },
    ],
  },

  // ── 应用启动器 ──
  {
    id: 'com-app-launcher',
    name: '应用启动器',
    description: '启动应用并执行预定义操作序列',
    aliases: ['启动应用', 'app launcher', '打开应用', '运行程序', '记事本', 'notepad', '计算器', '浏览器'],
    keywords: ['启动', '应用', '程序', '打开', 'launch', 'app', '记事本', 'notepad', '运行'],
    tags: ['system', 'app'],
    category: 'system',
    parameters: [
      { name: 'app_name', type: 'string', description: '应用名称', required: true, acquire: 'ask_text' },
      { name: 'launch_x', type: 'number', description: '启动图标 X', required: true, acquire: 'region_select' },
      { name: 'launch_y', type: 'number', description: '启动图标 Y', required: true, acquire: 'region_select' },
    ],
    children: [
      { childId: 'prim-click', description: '双击启动图标', paramBindings: { x: '$launch_x', y: '$launch_y' } },
      { childId: 'prim-wait', description: '等待应用启动', paramBindings: { duration: '5' } },
      { childId: 'prim-click', description: '点击应用窗口', paramBindings: { x: '500', y: '300' } },
    ],
  },

  // ── 打开应用并输入文本 ──
  {
    id: 'com-launch-type',
    name: '打开并输入',
    description: '启动应用，等待加载，然后输入指定文本',
    aliases: ['打开输入', '启动输入', '打开记事本输入', '打开应用输入'],
    keywords: ['打开', '启动', '运行', '输入', '打字', '记事本', '文本'],
    tags: ['system', 'app', 'keyboard'],
    category: 'app',
    parameters: [
      { name: 'app_name', type: 'string', description: '应用名称', required: true, acquire: 'ask_text' },
      { name: 'launch_x', type: 'number', description: '启动图标 X', required: true, acquire: 'region_select' },
      { name: 'launch_y', type: 'number', description: '启动图标 Y', required: true, acquire: 'region_select' },
      { name: 'text', type: 'string', description: '要输入的文本', required: true, acquire: 'ask_text' },
      { name: 'input_x', type: 'number', description: '输入位置 X', required: true, acquire: 'region_select' },
      { name: 'input_y', type: 'number', description: '输入位置 Y', required: true, acquire: 'region_select' },
    ],
    children: [
      { childId: 'prim-click', description: '双击启动图标', paramBindings: { x: '$launch_x', y: '$launch_y', method: 'double' } },
      { childId: 'prim-wait', description: '等待应用启动', paramBindings: { duration: '3' } },
      { childId: 'prim-click', description: '点击输入区域', paramBindings: { x: '$input_x', y: '$input_y' } },
      { childId: 'prim-wait', description: '等待焦点', paramBindings: { duration: '0.5' } },
      { childId: 'prim-input-text', description: '输入文本', paramBindings: { text: '$text', x: '$input_x', y: '$input_y' } },
    ],
  },

  // ── 定时截图记录 ──
  {
    id: 'com-screenshot-logger',
    name: '定时截图记录',
    description: '每隔指定秒数自动截图并保存',
    aliases: ['定时截图', '截图记录', '自动截图', 'screenshot log'],
    keywords: ['截图', '定时', '记录', '监控', 'log', '保存', '快照'],
    tags: ['image', 'monitor', 'log'],
    category: 'monitor',
    parameters: [
      { name: 'interval', type: 'number', description: '截图间隔（秒）', required: false, default: 30 },
      { name: 'total_shots', type: 'number', description: '截图张数', required: false, default: 10 },
    ],
    children: [
      { childId: 'prim-wait', description: '等待间隔', paramBindings: { duration: '$interval' } },
      { childId: 'prim-screenshot', description: '执行截图', paramBindings: {} },
    ],
  },

  // ── 批量文件处理监控 ──
  {
    id: 'com-batch-process',
    name: '批量文件处理监控',
    description: '批量循环处理文件→弹窗关闭→进度等待→截图记录',
    aliases: ['批量处理', 'batch process', '文件处理', '批量操作'],
    keywords: ['批量', '文件', '处理', '进度', '监控', '弹窗'],
    tags: ['file', 'batch', 'automation'],
    category: 'complex',
    parameters: [
      { name: 'batch_count', type: 'number', description: '处理文件数量', required: false, default: 5 },
      { name: 'process_x', type: 'number', description: '处理按钮 X', required: true, acquire: 'region_select' },
      { name: 'process_y', type: 'number', description: '处理按钮 Y', required: true, acquire: 'region_select' },
    ],
    children: [
      { childId: 'prim-click', description: '点击处理按钮', paramBindings: { x: '$process_x', y: '$process_y' } },
      { childId: 'prim-popup-close', description: '关闭完成弹窗', paramBindings: {}, optional: true },
      { childId: 'prim-wait', description: '等待处理完成', paramBindings: { duration: '3' } },
      { childId: 'prim-screenshot', description: '截图记录结果', paramBindings: {} },
    ],
  },
];

/** All HTN definitions in one array */
export const ALL_HTN_DEFS: HTNYamlDef[] = [
  ...BASE_PRIMITIVES,
  ...COMPOSITE_TEMPLATES,
];
