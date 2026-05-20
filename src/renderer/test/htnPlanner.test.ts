import { describe, it, expect, beforeEach } from 'vitest';
import { HTNPlanner } from '../utils/htnPlanner';
import { htnRegistry } from '../utils/htnRegistry';

function reg(opts: { id: string; name: string; kind: 'primitive' | 'composite'; children?: string[] }) {
  const node: any = {
    id: opts.id, name: opts.name, description: `${opts.name} test description`,
    aliases: [`alias-${opts.name}`], keywords: [`kw-${opts.name}`],
    tags: [], category: 'test', kind: opts.kind, parameters: [],
  };
  if (opts.kind === 'primitive') {
    node.primitive = { dslKeyword: opts.id.toUpperCase(), flowType: opts.id, paramBinding: { x: 'x', y: 'y' } };
  }
  if (opts.children && opts.children.length > 0) {
    node.children = opts.children.map((c, i) => ({ childId: c, description: `step ${i}`, optional: false }));
  }
  return htnRegistry.registerFromYaml(node);
}

describe('HTNPlanner', () => {
  let planner: HTNPlanner;

  beforeEach(() => {
    // Clear registry
    for (const n of htnRegistry.getAll()) {
      (htnRegistry as any).nodes.delete(n.id);
      (htnRegistry as any).parentToChildren.delete(n.id);
      (htnRegistry as any).childToParents.delete(n.id);
    }
    reg({ id: 'click', name: 'click', kind: 'primitive' });
    reg({ id: 'type', name: 'type', kind: 'primitive' });
    reg({ id: 'login', name: 'login', kind: 'composite', children: ['click', 'type'] });
    planner = new HTNPlanner();
  });

  it('returns null for unmatched query', () => {
    const result = planner.plan('zzz_nonexistent');
    expect(result).toBeNull();
  });

  it('plans a composite template', () => {
    const result = planner.plan('login');
    expect(result).not.toBeNull();
    expect(result!.rootId).toBe('login');
    expect(result!.rootName).toBe('login');
    expect(result!.score).toBeGreaterThan(90);
    expect(result!.steps.length).toBeGreaterThan(0);
  });

  it('generates DSL lines for primitive children', () => {
    const result = planner.plan('login');
    expect(result!.dsl).toBeTruthy();
    expect(result!.dsl).toContain('CLICK');
    expect(result!.dsl).toContain('TYPE');
  });

  it('generates pseudocode', () => {
    const result = planner.plan('login');
    expect(result!.pseudocode).toBeTruthy();
    expect(typeof result!.pseudocode).toBe('string');
  });

  it('generates explanation', () => {
    const result = planner.plan('login');
    expect(result!.explanation).toBeTruthy();
    expect(result!.explanation).toContain('login');
  });

  it('passes extracted params to decomposition', () => {
    const result = planner.plan('login', { x: 100, y: 200 });
    expect(result).not.toBeNull();
    // Params should be available in bindings
    const clickPrimitive = result!.steps.find(s => s.nodeId === 'click');
    expect(clickPrimitive).toBeDefined();
  });

  it('toExecutablePseudocode converts plan to pseudocode', () => {
    const result = planner.plan('login');
    expect(result).not.toBeNull();
    const pseudo = planner.toExecutablePseudocode(result!);
    expect(pseudo).toBeTruthy();
  });
});
