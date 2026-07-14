const SEARCH_TOKEN_LABELS: Record<string, string> = {
  agent: '智能代理',
  'gui agent': 'GUI 智能代理',
  workflow: '工作流',
  image: '图像',
  monitor: '监控',
  loop: '循环',
  app: '应用',
  login: '登录',
  signin: '登录',
  form: '表单',
  input: '输入',
  system: '系统',
  launch: '启动',
  launcher: '启动器',
  screenshot: '截图',
  timer: '定时',
  dialog: '对话框',
  file: '文件',
  detection: '检测',
  pipeline: '流水线',
  scraper: '数据采集',
  data: '数据',
  submit: '提交',
  batch: '批处理',
  action: '交互动作',
  perception: '感知识别',
  control: '控制流',
  macro: '宏指令',
  debug: '调试诊断',
  basic: '常用',
  device: '设备',
  tools: '工具',
};

const SOURCE_LABELS: Record<string, string> = {
  store: '已保存',
  preset: '预置',
  custom: '自定义',
  dsl: 'DSL',
  flow: '流程',
};

export function displaySearchToken(value: string): string {
  const key = value.trim().toLowerCase();
  return SEARCH_TOKEN_LABELS[key] || value;
}

export function displaySourceLabel(value: string): string {
  return SOURCE_LABELS[value.trim().toLowerCase()] || value;
}

export function displaySearchReason(value: string): string {
  const match = value.match(/^(关键词|标签|分类)\s*[:：]\s*(.+)$/);
  if (!match) return value;
  const tokens = match[2].split(',').map((token) => displaySearchToken(token.trim()));
  return `${match[1]}：${tokens.join('、')}`;
}
