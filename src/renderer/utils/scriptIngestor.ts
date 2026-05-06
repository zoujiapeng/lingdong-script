// ── Script Ingestor: Raw Script → Semantic HTN Template ──
// Takes scripts from any platform, uses LLM to annotate each
// step with semantic meaning, then registers as HTN composites.

import { htnRegistry } from './htnRegistry';
import { HTNNode, HTNStep } from './htnTypes';
import { templateRegistry } from './templateRegistry';
import { FlowScript } from '../types';

export interface AnnotatedStep {
  originalLine: string;
  lineNumber: number;
  semanticDescription: string;
  childId: string;         // HTN primitive node id (e.g. "prim-click")
  paramBindings: Record<string, string>;
  confidence: number;      // 0-1
  optional: boolean;
}

export interface IngestResult {
  name: string;
  description: string;
  aliases: string[];
  keywords: string[];
  category: string;
  tags: string[];
  steps: AnnotatedStep[];
  /** Registered HTN node id */
  nodeId: string;
  /** Overall confidence in the analysis */
  confidence: number;
}

export type ScriptPlatform = 'ahk' | 'python' | 'sikuli' | 'playwright' | 'cypress' | 'uipath' | 'other';

const PLATFORM_NAMES: Record<ScriptPlatform, string> = {
  ahk: 'AutoHotkey',
  python: 'Python (pyautogui, etc.)',
  sikuli: 'SikuliX',
  playwright: 'Playwright',
  cypress: 'Cypress',
  uipath: 'UiPath',
  other: '其他',
};

/** Build the system prompt for script ingestion */
function buildIngestPrompt(): string {
  const primitives = htnRegistry.getPrimitives();
  const primitivesDesc = primitives.map(p => {
    const params = p.parameters.map(pa =>
      `${pa.name}${pa.required ? ' (必填)' : ` (可选, 默认=${pa.default ?? '无'})`}`
    ).join(', ');
    return `- ${p.id} (${p.name}): ${p.description} | 参数: ${params || '无'}`;
  }).join('\n');

  return `你是一个脚本语义分析专家。分析用户提供的自动化脚本，理解每个步骤的意图。

## 可用原语（步骤将映射到这些基本操作）
${primitivesDesc}

## 分析要求
1. 判断脚本的整体目的，给出名称、描述、别名、关键词
2. 逐行分析每个可执行步骤（跳过纯注释）:
   - 判断这个步骤对应哪个原语操作
   - 提取参数值（坐标、文本、按键等）
   - 用一句话描述这个步骤的语义意图（如"点击登录按钮"而非"点击(500,400)"）
3. 标记可选步骤（如弹窗关闭、日志记录等）
4. 对非标准操作标记低置信度

## 输出 JSON 格式
{
  "name": "脚本名称（中文，如"登录企业邮箱"）",
  "description": "脚本用途描述",
  "aliases": ["别名1", "别名2"],
  "keywords": ["关键词1", "关键词2"],
  "category": "app|system|web|image|data|complex|monitor|custom",
  "tags": ["标签1", "标签2"],
  "steps": [
    {
      "lineNumber": 1,
      "originalLine": "Click 500, 400",
      "semanticDescription": "点击用户名输入框，聚焦到账号字段",
      "childId": "prim-click",
      "paramBindings": {"x": "500", "y": "400"},
      "confidence": 0.95,
      "optional": false
    }
  ],
  "confidence": 0.9
}`;
}

/** Call LLM for script analysis */
async function analyzeScript(
  script: string,
  platform: ScriptPlatform,
  apiKey: string,
  apiBase?: string,
  model?: string,
): Promise<any> {
  const platformName = PLATFORM_NAMES[platform];
  const prompt = buildIngestPrompt();
  const userMessage = `平台: ${platformName}\n\n脚本内容:\n\`\`\`\n${script}\n\`\`\``;

  const api = (window as any).electronAPI;
  const baseUrl = (apiBase || (window as any).__LLM_API_BASE__ || localStorage.getItem('lingdong-llm-base') || 'https://api.deepseek.com/v1').replace(/\/+$/, '');
  const modelName = model || (window as any).__LLM_MODEL__ || localStorage.getItem('lingdong-llm-model') || 'deepseek-chat';

  const messages = [
    { role: 'system' as const, content: prompt },
    { role: 'user' as const, content: userMessage },
  ];

  if (api?.aiGenerate) {
    const data = await api.aiGenerate({ apiKey, messages, apiBase: baseUrl, model: modelName });
    if (data.error) throw new Error(data.error);
    return data;
  }

  const response = await fetch(`${baseUrl}/chat/completions`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Authorization': `Bearer ${apiKey}`,
    },
    body: JSON.stringify({
      model: modelName,
      messages,
      response_format: { type: 'json_object' },
      temperature: 0.1,
      max_tokens: 4096,
    }),
  });

  if (!response.ok) {
    const text = await response.text();
    throw new Error(`LLM API 错误 (${response.status}): ${text}`);
  }

  const data = await response.json();
  const content = data.choices?.[0]?.message?.content;
  if (!content) throw new Error('API 返回为空');
  return JSON.parse(content);
}

/** Register analyzed result into htnRegistry and templateRegistry */
function registerIngested(result: IngestResult, originalScript: string): string {
  const id = `script-${result.name.replace(/\s+/g, '-').toLowerCase()}-${Date.now().toString(36)}`;

  const children: HTNStep[] = result.steps
    .filter(s => s.childId && s.confidence >= 0.3) // Skip very low-confidence steps
    .map(s => ({
      childId: s.childId,
      description: s.semanticDescription,
      optional: s.optional,
      paramBindings: s.paramBindings,
    }));

  if (children.length === 0) {
    throw new Error('没有足够的可识别步骤来创建模板（置信度过低）');
  }

  const node: HTNNode = {
    id,
    name: result.name,
    description: result.description,
    aliases: result.aliases || [],
    keywords: result.keywords || [],
    tags: result.tags || [],
    category: result.category || 'custom',
    kind: 'composite',
    parameters: [],
    children,
  };

  htnRegistry.register(node);

  // Also register in templateRegistry for flat search
  templateRegistry.register({
    id,
    name: result.name,
    description: result.description,
    category: result.category || 'custom',
    tags: result.tags || [],
    aliases: result.aliases || [],
    keywords: result.keywords || [],
    source: 'custom',
    hasScript: false,
    dslContent: originalScript,
  });

  return id;
}

/** Build a FlowScript from annotated steps for visual editing */
function buildFlowScript(result: IngestResult, originalScript: string): FlowScript {
  const nodes: any[] = [
    { id: 'start', type: 'start', data: { label: '开始' }, position: { x: 50, y: 300 } },
  ];
  const edges: any[] = [];

  let prevId = 'start';
  const stepHeight = 120;
  const startY = 200;

  for (let i = 0; i < result.steps.length; i++) {
    const step = result.steps[i];
    const nodeId = `step-${i}`;
    const nodeType = mapPrimitiveToNodeType(step.childId);

    nodes.push({
      id: nodeId,
      type: nodeType,
      data: {
        label: step.semanticDescription,
        ...step.paramBindings,
      },
      position: { x: 250, y: startY + i * stepHeight },
    });

    edges.push({ id: `edge-${i}`, source: prevId, target: nodeId });
    prevId = nodeId;
  }

  // End node
  const endId = 'end';
  nodes.push({
    id: endId,
    type: 'end',
    data: { label: '结束' },
    position: { x: 250 + 200, y: startY + result.steps.length * stepHeight },
  });
  edges.push({ id: 'edge-end', source: prevId, target: endId });

  const now = new Date().toISOString();
  return {
    meta: {
      id: `flow-${Date.now().toString(36)}`,
      name: result.name,
      description: result.description,
      created_at: now,
      updated_at: now,
      version: '1.0',
    },
    nodes: nodes as any,
    edges,
    variables: {},
    settings: {
      default_delay: 0.5,
      error_handling: 'stop',
      max_retries: 0,
      retry_delay: 1,
      log_level: 'info',
    },
  };
}

/** Map HTN primitive id to FlowScript node type */
function mapPrimitiveToNodeType(childId: string): string {
  const map: Record<string, string> = {
    'prim-click': 'click',
    'prim-click-text': 'click_text',
    'prim-input-text': 'input_text',
    'prim-wait': 'wait',
    'prim-screenshot': 'screenshot',
    'prim-keyboard': 'keyboard',
    'prim-swipe': 'swipe',
    'prim-popup-close': 'popup_close',
    'prim-scene-check': 'scene_condition',
    'prim-loop': 'loop',
  };
  return map[childId] || 'tool_call';
}

// ── Main API ────────────────────────────────────────────────

export class ScriptIngestor {
  /**
   * Ingest a raw script: analyze with LLM → register as HTN template.
   * Returns the ingest result with the registered nodeId.
   */
  async ingest(
    script: string,
    platform: ScriptPlatform = 'other',
    apiKey?: string,
    apiBase?: string,
    model?: string,
  ): Promise<IngestResult> {
    if (!apiKey) {
      apiKey = (window as any).__DEEPSEEK_KEY__;
      if (!apiKey) {
        try { apiKey = localStorage.getItem('lingdong-deepseek-key') || ''; } catch {}
      }
    }
    if (!apiKey) {
      throw new Error('需要配置 DeepSeek API 密钥才能分析脚本');
    }

    const raw = await analyzeScript(script, platform, apiKey, apiBase, model);

    if (!raw || !raw.name || !raw.steps) {
      throw new Error('LLM 返回格式异常，未能提取脚本结构');
    }

    const result: IngestResult = {
      name: raw.name,
      description: raw.description || raw.name,
      aliases: raw.aliases || [],
      keywords: raw.keywords || [],
      category: raw.category || 'custom',
      tags: raw.tags || [],
      steps: Array.isArray(raw.steps) ? raw.steps.map((s: any) => ({
        originalLine: s.originalLine || '',
        lineNumber: s.lineNumber || 0,
        semanticDescription: s.semanticDescription || s.description || '',
        childId: s.childId || 'prim-wait',
        paramBindings: s.paramBindings || {},
        confidence: s.confidence ?? 0.5,
        optional: !!s.optional,
      })) : [],
      nodeId: '',
      confidence: raw.confidence ?? 0.5,
    };

    result.nodeId = registerIngested(result, script);

    return result;
  }

  /**
   * Batch ingest multiple scripts. Each script is analyzed independently.
   */
  async ingestBatch(
    scripts: Array<{ name: string; script: string; platform?: ScriptPlatform }>,
    apiKey?: string,
  ): Promise<IngestResult[]> {
    const results: IngestResult[] = [];
    for (const { script, platform } of scripts) {
      try {
        const r = await this.ingest(script, platform, apiKey);
        results.push(r);
      } catch (e: any) {
        console.error(`Script ingestion failed:`, e.message);
      }
    }
    return results;
  }

  /**
   * Convert ingested result to FlowScript for visual editing.
   */
  toFlowScript(result: IngestResult): FlowScript {
    return buildFlowScript(result, '');
  }
}

export const scriptIngestor = new ScriptIngestor();
