import { NodeDefinition, NodeType } from '../types';

export const NODE_DEFINITIONS: Record<NodeType, NodeDefinition> = {
  start: {
    type: 'start',
    label: '开始',
    description: '脚本执行入口点',
    icon: 'Play',
    category: 'basic',
    color: '#6366f1',
    defaultParams: { label: '开始' },
    ports: {
      inputs: [],
      outputs: [{ id: 'output', type: 'flow' }],
    },
  },
  end: {
    type: 'end',
    label: '结束',
    description: '脚本执行终止点',
    icon: 'Square',
    category: 'basic',
    color: '#ef4444',
    defaultParams: { label: '结束' },
    ports: {
      inputs: [{ id: 'input', type: 'flow' }],
      outputs: [],
    },
  },
  click: {
    type: 'click',
    label: '点击',
    description: '点击屏幕上的指定位置',
    icon: 'MousePointer',
    category: 'basic',
    color: '#3b82f6',
    defaultParams: {
      x: 960,
      y: 540,
      offsetX: 0,
      offsetY: 0,
      method: 'single',
      delay_after: 0.5,
    },
    ports: {
      inputs: [{ id: 'input', type: 'flow' }],
      outputs: [{ id: 'output', type: 'flow' }],
    },
  },
  image_click: {
    type: 'image_click',
    label: '点击图片',
    description: '找到图片并点击',
    icon: 'Crosshair',
    category: 'image',
    color: '#8b5cf6',
    defaultParams: {
      targets: [],
      similarity: 90,
      timeout: 5,
      method: 'single',
      offset_x: 0,
      offset_y: 0,
      delay_after: 0.5,
      multi_scale: true,
      match_all: false,
    },
    ports: {
      inputs: [{ id: 'input', type: 'flow' }],
      outputs: [
        { id: 'output', label: '成功', type: 'flow' },
        { id: 'fail', label: '失败', type: 'flow' },
      ],
    },
  },
  wait: {
    type: 'wait',
    label: '等待',
    description: '等待指定时间或条件',
    icon: 'Clock',
    category: 'basic',
    color: '#0ea5e9',
    defaultParams: {
      wait_type: 'time',
      duration: 1,
      timeout: 10,
      similarity: 90,
    },
    ports: {
      inputs: [{ id: 'input', type: 'flow' }],
      outputs: [{ id: 'output', type: 'flow' }],
    },
  },
  input_text: {
    type: 'input_text',
    label: '输入文本',
    description: '在指定位置输入文字',
    icon: 'Type',
    category: 'basic',
    color: '#10b981',
    defaultParams: {
      text: '',
      click_before: true,
      clear_before: true,
      delay_after: 0.3,
    },
    ports: {
      inputs: [{ id: 'input', type: 'flow' }],
      outputs: [{ id: 'output', type: 'flow' }],
    },
  },
  keyboard: {
    type: 'keyboard',
    label: '键盘事件',
    description: '模拟键盘按键',
    icon: 'Command',
    category: 'device',
    color: '#f59e0b',
    defaultParams: {
      keys: ['enter'],
      modifiers: [],
      delay_after: 0.2,
    },
    ports: {
      inputs: [{ id: 'input', type: 'flow' }],
      outputs: [{ id: 'output', type: 'flow' }],
    },
  },
  swipe: {
    type: 'swipe',
    label: '滑动拖拽',
    description: '在屏幕上滑动或拖拽',
    icon: 'MoveHorizontal',
    category: 'device',
    color: '#06b6d4',
    defaultParams: {
      start_x: 400,
      start_y: 300,
      end_x: 800,
      end_y: 300,
      duration: 0.5,
      delay_after: 0.3,
    },
    ports: {
      inputs: [{ id: 'input', type: 'flow' }],
      outputs: [{ id: 'output', type: 'flow' }],
    },
  },
  condition: {
    type: 'condition',
    label: '条件判断',
    description: '根据条件选择不同路径',
    icon: 'GitBranch',
    category: 'control',
    color: '#f97316',
    defaultParams: {
      condition_type: 'image_exists',
      similarity: 85,
    },
    ports: {
      inputs: [{ id: 'input', type: 'flow' }],
      outputs: [
        { id: 'yes', label: '是', type: 'flow' },
        { id: 'no', label: '否', type: 'flow' },
      ],
    },
  },
  loop: {
    type: 'loop',
    label: '循环',
    description: '循环执行一系列操作',
    icon: 'RefreshCw',
    category: 'control',
    color: '#22c55e',
    defaultParams: {
      loop_type: 'count',
      count: 10,
    },
    ports: {
      inputs: [{ id: 'input', type: 'flow' }],
      outputs: [
        { id: 'loop_body', label: '循环体', type: 'flow' },
        { id: 'loop_end', label: '结束后', type: 'flow' },
      ],
    },
  },
  sub_script: {
    type: 'sub_script',
    label: '运行子脚本',
    description: '调用其他脚本',
    icon: 'FileCode',
    category: 'control',
    color: '#a855f7',
    defaultParams: {
      script_id: '',
      script_name: '未选择',
    },
    ports: {
      inputs: [{ id: 'input', type: 'flow' }],
      outputs: [{ id: 'output', type: 'flow' }],
    },
  },
  screenshot: {
    type: 'screenshot',
    label: '截图',
    description: '截取屏幕图像',
    icon: 'Camera',
    category: 'image',
    color: '#64748b',
    defaultParams: {},
    ports: {
      inputs: [{ id: 'input', type: 'flow' }],
      outputs: [{ id: 'output', type: 'flow' }],
    },
  },
  variable_set: {
    type: 'variable_set',
    label: '设置变量',
    description: '设置全局变量的值',
    icon: 'Variable',
    category: 'data',
    color: '#ec4899',
    defaultParams: {
      name: 'myVar',
      value: '',
    },
    ports: {
      inputs: [{ id: 'input', type: 'flow' }],
      outputs: [{ id: 'output', type: 'flow' }],
    },
  },
  variable_get: {
    type: 'variable_get',
    label: '读取变量',
    description: '读取全局变量的值',
    icon: 'Database',
    category: 'data',
    color: '#d946ef',
    defaultParams: {
      name: 'myVar',
      value: '',
    },
    ports: {
      inputs: [{ id: 'input', type: 'flow' }],
      outputs: [{ id: 'output', type: 'flow' }],
    },
  },
};

export const NODE_CATEGORIES = [
  { id: 'basic', label: '常用', icon: 'Zap' },
  { id: 'image', label: '图像', icon: 'Image' },
  { id: 'control', label: '控制', icon: 'GitBranch' },
  { id: 'device', label: '设备', icon: 'Monitor' },
  { id: 'data', label: '数据', icon: 'Database' },
  { id: 'system', label: '系统', icon: 'Settings' },
] as const;

export function getNodeDefinition(type: NodeType): NodeDefinition {
  return NODE_DEFINITIONS[type];
}

export function getNodesByCategory(category: string): NodeDefinition[] {
  return Object.values(NODE_DEFINITIONS).filter((d) => d.category === category);
}
