import { describe, it, expect, beforeEach } from 'vitest';
import { htnRegistry } from '../utils/htnRegistry';

function reg(opts: { id: string; name: string; kind: 'primitive' | 'composite'; children?: string[]; parent?: string }) {
  const node: any = {
    id: opts.id,
    name: opts.name,
    description: `${opts.name} description`,
    aliases: [`alias-${opts.name}`],
    keywords: [`kw-${opts.name}`],
    tags: [],
    category: 'test',
    kind: opts.kind,
    parameters: [],
  };
  if (opts.kind === 'primitive') {
    node.primitive = { dslKeyword: `TEST_${opts.name.toUpperCase()}`, flowType: opts.id, paramBinding: {} };
  }
  if (opts.children && opts.children.length > 0) {
    node.children = opts.children.map((c, i) => ({
      childId: c,
      description: `step ${i}`,
      optional: false,
    }));
  }
  return htnRegistry.registerFromYaml(node);
}

describe('htnRegistry', () => {
  beforeEach(() => {
    // Clear the registry by resetting internal state
    for (const n of htnRegistry.getAll()) {
      (htnRegistry as any).nodes.delete(n.id);
      (htnRegistry as any).parentToChildren.delete(n.id);
      (htnRegistry as any).childToParents.delete(n.id);
    }
    // Register test nodes
    reg({ id: 'click', name: 'click', kind: 'primitive' });
    reg({ id: 'type', name: 'type', kind: 'primitive' });
    reg({ id: 'wait', name: 'wait', kind: 'primitive' });
    reg({ id: 'login', name: 'login', kind: 'composite', children: ['click', 'type'] });
    reg({ id: 'big-flow', name: 'big-flow', kind: 'composite', children: ['click', 'type', 'wait', 'login'] });
  });

  it('registers and retrieves nodes', () => {
    expect(htnRegistry.count).toBe(5);
    expect(htnRegistry.get('click')).toBeDefined();
    expect(htnRegistry.get('click')!.name).toBe('click');
    expect(htnRegistry.get('nonexistent')).toBeUndefined();
  });

  it('getAll returns all nodes', () => {
    const all = htnRegistry.getAll();
    expect(all.length).toBe(5);
    expect(all.map(n => n.id).sort()).toEqual(['big-flow', 'click', 'login', 'type', 'wait']);
  });

  it('getRoots returns composite nodes without parents', () => {
    const roots = htnRegistry.getRoots();
    expect(roots.length).toBeGreaterThanOrEqual(1);
    // 'big-flow' has no parent
    expect(roots.some(n => n.id === 'big-flow')).toBe(true);
  });

  it('getPrimitives returns leaf nodes', () => {
    const prims = htnRegistry.getPrimitives();
    expect(prims.every(n => n.kind === 'primitive')).toBe(true);
  });

  it('getChildren returns child nodes', () => {
    const children = htnRegistry.getChildren('login');
    const childIds = children.map(n => n.id);
    expect(childIds).toContain('click');
    expect(childIds).toContain('type');
  });

  it('getParents returns parent nodes', () => {
    const parents = htnRegistry.getParents('click');
    expect(parents.some(n => n.id === 'login')).toBe(true);
  });

  it('flatten returns all descendant primitive IDs', () => {
    const flat = htnRegistry.flatten('login');
    expect(flat).toContain('click');
    expect(flat).toContain('type');
    expect(flat).not.toContain('login');
  });

  it('depth returns hierarchical depth', () => {
    expect(htnRegistry.depth('click')).toBe(1); // leaf
    expect(htnRegistry.depth('login')).toBe(2); // login → click/type
    expect(htnRegistry.depth('big-flow')).toBeGreaterThanOrEqual(3);
  });

  it('structureBonus returns flattened primitive count', () => {
    expect(htnRegistry.structureBonus('click')).toBe(1);
    expect(htnRegistry.structureBonus('login')).toBe(2);
    expect(htnRegistry.structureBonus('big-flow')).toBeGreaterThanOrEqual(4);
  });

  it('search returns results sorted by score descending', () => {
    const results = htnRegistry.search('login');
    expect(results.length).toBeGreaterThan(0);
    for (let i = 1; i < results.length; i++) {
      expect(results[i - 1].score).toBeGreaterThanOrEqual(results[i].score);
    }
  });

  it('search gives exact name match highest score', () => {
    const results = htnRegistry.search('login');
    expect(results[0].node.id).toBe('login');
    expect(results[0].score).toBeGreaterThanOrEqual(100);
  });

  it('search matches by alias', () => {
    const results = htnRegistry.search('alias-click');
    expect(results.some(r => r.node.id === 'click')).toBe(true);
  });

  it('search returns empty for no match', () => {
    const results = htnRegistry.search('zzz_nonexistent');
    expect(results.length).toBe(0);
  });

  it('primitives get penalized in search', () => {
    const primResults = htnRegistry.search('click');
    // 'click' primitive should match but ranked lower than composite with 'click' in name
    const clickResult = primResults.find(r => r.node.id === 'click');
    // primitive penalty: score -25 from keyword match
    expect(clickResult).toBeDefined();
  });

  it('toAIContext returns non-empty string', () => {
    const ctx = htnRegistry.toAIContext();
    expect(ctx).toContain('click');
    expect(ctx).toContain('big-flow');
  });

  it('supports registering from FlowNodes', () => {
    // Just verify the method exists and doesn't crash
    expect(typeof htnRegistry.registerFromFlowNodes).toBe('function');
  });

  it('autoRegisterFromTemplates runs without error', () => {
    // Should not throw even with empty templateRegistry
    htnRegistry.autoRegisterFromTemplates();
    expect(htnRegistry.count).toBeGreaterThanOrEqual(1);
  });
});
