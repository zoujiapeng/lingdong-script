/**
 * Quick verification of the ParamPrompt logic:
 * - isParamMissing
 * - buildParamPromptQueue
 * - REQUIRED_PARAMS_MAP coverage
 */

// Copy of the logic from store/index.ts
interface ParamPromptItem {
  nodeId: string;
  nodeType: string;
  nodeLabel: string;
  groupType: 'region' | 'text' | 'file';
  params: string[];
  labels: string[];
}

const REQUIRED_PARAMS_MAP: Record<string, { params: string[]; tool: ParamPromptItem['groupType']; label: string }[]> = {
  click: [{ params: ['x', 'y'], tool: 'region', label: '点击位置' }],
  input_text: [{ params: ['text'], tool: 'text', label: '输入内容' }],
  image_click: [{ params: ['targets'], tool: 'file', label: '目标图片' }],
  click_text: [{ params: ['text'], tool: 'text', label: '目标文字' }],
  keyboard: [{ params: ['keys'], tool: 'text', label: '按键' }],
  swipe: [{ params: ['start_x', 'start_y', 'end_x', 'end_y'], tool: 'region', label: '滑动区域' }],
  color_check: [
    { params: ['x', 'y', 'w', 'h'], tool: 'region', label: '检测区域' },
    { params: ['expected_color'], tool: 'text', label: '期望颜色 (HSV)' },
  ],
  region_wait: [{ params: ['x', 'y', 'w', 'h'], tool: 'region', label: '监控区域' }],
  progress_wait: [{ params: ['x', 'y', 'w', 'h'], tool: 'region', label: '进度条区域' }],
  screenshot: [{ params: ['region'], tool: 'region', label: '截图区域' }],
  variable_set: [
    { params: ['name'], tool: 'text', label: '变量名' },
    { params: ['value'], tool: 'text', label: '变量值' },
  ],
  tool_call: [{ params: ['feature_name'], tool: 'text', label: '工具名称' }],
  sub_script: [{ params: ['script_id'], tool: 'text', label: '子流程' }],
};

function isParamMissing(value: any): boolean {
  if (value === undefined || value === null) return true;
  if (typeof value === 'string' && value.trim() === '') return true;
  if (Array.isArray(value) && value.length === 0) return true;
  return false;
}

function buildParamPromptQueue(nodes: any[]): ParamPromptItem[] {
  const queue: ParamPromptItem[] = [];
  for (const node of nodes) {
    if (node.type === 'start' || node.type === 'end') continue;
    const rules = REQUIRED_PARAMS_MAP[node.type];
    if (!rules) continue;
    for (const rule of rules) {
      const missing = rule.params.filter((p: string) => isParamMissing(node.params?.[p]));
      if (missing.length > 0) {
        queue.push({
          nodeId: node.id,
          nodeType: node.type,
          nodeLabel: node.label || node.type,
          groupType: rule.tool,
          params: missing,
          labels: [rule.label],
        });
      }
    }
  }
  return queue;
}

// ===== TESTS =====
let passed = 0;
let failed = 0;

function assert(condition: boolean, label: string) {
  if (condition) {
    console.log(`  ✓ ${label}`);
    passed++;
  } else {
    console.log(`  ✗ ${label}`);
    failed++;
  }
}

// Test isParamMissing
console.log('\n--- isParamMissing ---');
assert(isParamMissing(undefined) === true, 'undefined is missing');
assert(isParamMissing(null) === true, 'null is missing');
assert(isParamMissing('') === true, 'empty string is missing');
assert(isParamMissing('  ') === true, 'whitespace string is missing');
assert(isParamMissing([]) === true, 'empty array is missing');
assert(isParamMissing(0) === false, '0 is NOT missing');
assert(isParamMissing('hello') === false, 'non-empty string is NOT missing');
assert(isParamMissing([1, 2]) === false, 'non-empty array is NOT missing');
assert(isParamMissing(100) === false, 'number is NOT missing');

// Test buildParamPromptQueue
console.log('\n--- buildParamPromptQueue ---');

// Test 1: All params filled → empty queue
const node1 = { id: 'n1', type: 'click', label: '点击', params: { x: 100, y: 200 } };
assert(buildParamPromptQueue([node1]).length === 0, 'click with x,y → empty queue');

// Test 2: Missing x,y → queue with 1 region item
const node2 = { id: 'n2', type: 'click', label: '点击', params: {} };
const q2 = buildParamPromptQueue([node2]);
assert(q2.length === 1, 'click without x,y → 1 item in queue');
assert(q2[0].groupType === 'region', 'click missing x,y → groupType region');
assert(q2[0].params.length === 2, 'click missing x,y → 2 params in item');
assert(q2[0].labels.length === 1, 'click missing x,y → 1 label (combined)');
assert(q2[0].labels[0] === '点击位置', 'click missing x,y → label is "点击位置"');

// Test 3: input_text missing text
const node3 = { id: 'n3', type: 'input_text', label: '输入', params: {} };
const q3 = buildParamPromptQueue([node3]);
assert(q3.length === 1, 'input_text without text → 1 item');
assert(q3[0].groupType === 'text', 'input_text → groupType text');

// Test 4: image_click missing targets
const node4 = { id: 'n4', type: 'image_click', label: '图片点击', params: {} };
const q4 = buildParamPromptQueue([node4]);
assert(q4.length === 1, 'image_click without targets → 1 item');
assert(q4[0].groupType === 'file', 'image_click → groupType file');

// Test 5: start/end nodes are skipped
const nodes5 = [
  { id: 's', type: 'start', params: {} },
  { id: 'e', type: 'end', params: {} },
];
assert(buildParamPromptQueue(nodes5).length === 0, 'start/end nodes skipped');

// Test 6: Complex node with multiple required groups (color_check)
const node6 = { id: 'n6', type: 'color_check', label: '颜色检测', params: {} };
const q6 = buildParamPromptQueue([node6]);
assert(q6.length === 2, 'color_check with all missing → 2 items (region + text)');
assert(q6[0].groupType === 'region', 'color_check item 0 → region');
assert(q6[1].groupType === 'text', 'color_check item 1 → text');

// Test 7: color_check partially filled
const node7 = { id: 'n7', type: 'color_check', params: { x: 10, y: 20, w: 100, h: 100 } };
const q7 = buildParamPromptQueue([node7]);
assert(q7.length === 1, 'color_check with region filled, missing color → 1 item');
assert(q7[0].groupType === 'text', 'partial color_check → text for expected_color');
assert(q7[0].params[0] === 'expected_color', 'partial color_check → expected_color param');

// Test 8: Fully filled color_check
const node8 = { id: 'n8', type: 'color_check', params: { x: 10, y: 20, w: 100, h: 100, expected_color: '30,200,200' } };
assert(buildParamPromptQueue([node8]).length === 0, 'fully filled color_check → empty queue');

// Test 9: Mixed nodes - some filled, some not
const nodes9 = [
  { id: 's', type: 'start', params: {} },
  { id: 'n1', type: 'click', params: { x: 100, y: 200 } },
  { id: 'n2', type: 'input_text', params: {} },
  { id: 'n3', type: 'image_click', params: { targets: ['img.png'] } },
  { id: 'n4', type: 'variable_set', params: {} },
  { id: 'e', type: 'end', params: {} },
];
const q9 = buildParamPromptQueue(nodes9);
assert(q9.length === 3, 'mixed nodes → 3 items (input_text + variable_set name + variable_set value)');
assert(q9[0].nodeType === 'input_text', 'mixed → first is input_text');
assert(q9[1].nodeType === 'variable_set', 'mixed → second is variable_set (name)');
assert(q9[2].nodeType === 'variable_set', 'mixed → third is variable_set (value)');

// Test 10: Node type with no rules
assert(buildParamPromptQueue([{ id: 'n', type: 'unknown_type', params: {} }]).length === 0, 'unknown type → empty queue');

// SUMMARY
console.log(`\n${'='.repeat(40)}`);
console.log(`Results: ${passed} passed, ${failed} failed out of ${passed + failed} tests`);
if (failed > 0) process.exit(1);
