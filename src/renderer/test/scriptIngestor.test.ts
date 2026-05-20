import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { scriptIngestor } from '../utils/scriptIngestor';
import { htnRegistry } from '../utils/htnRegistry';
import { templateRegistry } from '../utils/templateRegistry';

// Register some primitives so buildIngestPrompt() has content
function registerPrimitive(id: string, name: string) {
  const node: any = {
    id, name, description: `${name} description`,
    aliases: [], keywords: [], tags: [], category: 'test',
    kind: 'primitive', parameters: [],
    primitive: { dslKeyword: id.toUpperCase(), flowType: id, paramBinding: {} },
  };
  htnRegistry.registerFromYaml(node);
}

const MOCK_LLM_RESPONSE = {
  name: '打开记事本并输入',
  description: '打开记事本应用并输入文字',
  aliases: ['notepad', '记事本'],
  keywords: ['打开', '记事本', '输入'],
  category: 'app',
  tags: ['app', 'text'],
  steps: [
    {
      lineNumber: 1,
      originalLine: 'Click 500, 400',
      semanticDescription: '点击运行按钮',
      childId: 'prim-click',
      paramBindings: { x: '500', y: '400' },
      confidence: 0.95,
      optional: false,
    },
    {
      lineNumber: 2,
      originalLine: 'Type "hello"',
      semanticDescription: '输入文字',
      childId: 'prim-input-text',
      paramBindings: { text: 'hello' },
      confidence: 0.9,
      optional: false,
    },
  ],
  confidence: 0.9,
};

describe('ScriptIngestor', () => {
  beforeEach(() => {
    // Clear htnRegistry
    for (const n of htnRegistry.getAll()) {
      (htnRegistry as any).nodes.delete(n.id);
      (htnRegistry as any).parentToChildren.delete(n.id);
      (htnRegistry as any).childToParents.delete(n.id);
    }
    // Clear templateRegistry
    templateRegistry.clear();

    // Register some primitives
    registerPrimitive('prim-click', 'click');
    registerPrimitive('prim-input-text', 'input text');

    // Mock fetch for LLM API calls
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        choices: [{ message: { content: JSON.stringify(MOCK_LLM_RESPONSE) } }],
      }),
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('ingest parses script and registers template', async () => {
    const result = await scriptIngestor.ingest(
      'Click 500, 400\nType "hello"',
      'python',
      'test-api-key',
    );
    expect(result).toBeDefined();
    expect(result.name).toBe('打开记事本并输入');
    expect(result.steps.length).toBe(2);
    expect(result.nodeId).toBeTruthy();
    expect(result.confidence).toBe(0.9);

    // Verify registered in htnRegistry
    const registered = htnRegistry.get(result.nodeId);
    expect(registered).toBeDefined();
    expect(registered!.kind).toBe('composite');

    // Verify registered in templateRegistry
    const templates = templateRegistry.getAll();
    expect(templates.some(t => t.id === result.nodeId)).toBe(true);
  });

  it('ingest registers correct step details', async () => {
    const result = await scriptIngestor.ingest('Click 500, 400', 'other', 'key');
    expect(result.steps[0].childId).toBe('prim-click');
    expect(result.steps[0].paramBindings).toEqual({ x: '500', y: '400' });
    expect(result.steps[0].confidence).toBe(0.95);
    expect(result.steps[0].optional).toBe(false);
  });

  it('ingest throws when no apiKey provided', async () => {
    await expect(scriptIngestor.ingest('click', 'python', '')).rejects.toThrow('API 密钥');
  });

  it('ingest throws on malformed LLM response', async () => {
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        choices: [{ message: { content: '{"bad": "response"}' } }],
      }),
    });
    await expect(scriptIngestor.ingest('click', 'python', 'key')).rejects.toThrow('格式异常');
  });

  it('ingest throws when LLM API returns error status', async () => {
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 401,
      text: async () => 'Unauthorized',
    });
    await expect(scriptIngestor.ingest('click', 'python', 'bad-key')).rejects.toThrow('LLM API 错误');
  });

  it('ingestBatch handles multiple scripts', async () => {
    const results = await scriptIngestor.ingestBatch(
      [
        { name: 'script1', script: 'Click 500, 400', platform: 'python' },
        { name: 'script2', script: 'Click 100, 200', platform: 'python' },
      ],
      'test-key',
    );
    expect(results.length).toBe(2);
    expect(results[0].name).toBe('打开记事本并输入');
    expect(results[1].name).toBe('打开记事本并输入');
  });

  it('ingestBatch continues on failure', async () => {
    globalThis.fetch = vi.fn()
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          choices: [{ message: { content: JSON.stringify(MOCK_LLM_RESPONSE) } }],
        }),
      })
      .mockRejectedValueOnce(new Error('Network error'));
    const results = await scriptIngestor.ingestBatch(
      [
        { name: 's1', script: 'click', platform: 'python' },
        { name: 's2', script: 'click', platform: 'python' },
      ],
      'key',
    );
    expect(results.length).toBe(1);
  });

  it('toFlowScript converts result to FlowScript', () => {
    const result = {
      name: 'test',
      description: 'test flow',
      aliases: [],
      keywords: [],
      category: 'app',
      tags: [],
      steps: [
        {
          originalLine: 'Click 500, 400',
          lineNumber: 1,
          semanticDescription: '点击按钮',
          childId: 'prim-click',
          paramBindings: { x: '500', y: '400' },
          confidence: 0.95,
          optional: false,
        },
      ],
      nodeId: 'script-test',
      confidence: 0.9,
    };
    const flow = scriptIngestor.toFlowScript(result);
    expect(flow.meta.name).toBe('test');
    expect(flow.nodes.length).toBeGreaterThanOrEqual(3); // start + steps + end
    expect(flow.edges.length).toBeGreaterThanOrEqual(2);
  });
});
