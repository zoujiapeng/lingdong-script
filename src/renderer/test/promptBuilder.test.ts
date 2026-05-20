import { describe, it, expect, beforeEach } from 'vitest';
import { promptBuilder } from '../utils/promptBuilder';
import { htnRegistry } from '../utils/htnRegistry';

function registerTestNode(opts: { id: string; name: string; kind: 'primitive' | 'composite'; children?: string[] }) {
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
    node.primitive = { dslKeyword: opts.id.toUpperCase(), flowType: opts.id, paramBinding: {} };
  }
  if (opts.children && opts.children.length > 0) {
    node.children = opts.children.map((c, i) => ({ childId: c, description: `step ${i}`, optional: false }));
  }
  htnRegistry.registerFromYaml(node);
}

describe('PromptBuilder', () => {
  beforeEach(() => {
    for (const n of htnRegistry.getAll()) {
      (htnRegistry as any).nodes.delete(n.id);
      (htnRegistry as any).parentToChildren.delete(n.id);
      (htnRegistry as any).childToParents.delete(n.id);
    }
    registerTestNode({ id: 'click', name: 'click', kind: 'primitive' });
    registerTestNode({ id: 'type', name: 'type', kind: 'primitive' });
    registerTestNode({ id: 'login', name: 'login', kind: 'composite', children: ['click', 'type'] });
  });

  it('build returns non-empty string with default options', () => {
    const result = promptBuilder.build();
    expect(result.length).toBeGreaterThan(100);
    expect(result).toContain('桌面自动化脚本生成器');
  });

  it('build with english language uses DSL terminology', () => {
    const result = promptBuilder.build({ language: 'english' });
    expect(result).toContain('DSL generator');
    expect(result).not.toContain('桌面自动化脚本生成器');
  });

  it('build includes tools catalog by default', () => {
    const result = promptBuilder.build({ includeTools: true, includeTemplates: false, includeExamples: false });
    expect(result).toContain('## 可用操作');
    expect(result).toContain('鼠标操作');
  });

  it('build excludes tools when includeTools=false', () => {
    const result = promptBuilder.build({ includeTools: false, includeTemplates: false, includeExamples: false });
    expect(result).not.toContain('## 可用操作');
  });

  it('build includes templates when includeTemplates=true and htn nodes exist', () => {
    const result = promptBuilder.build({ includeTemplates: true, includeTools: false, includeExamples: false });
    expect(result).toContain('## 可用模板');
    expect(result).toContain('login');
    expect(result).toContain('内部步骤');
  });

  it('build excludes templates when includeTemplates=false', () => {
    const result = promptBuilder.build({ includeTemplates: false, includeTools: false, includeExamples: false });
    expect(result).not.toContain('## 可用模板');
  });

  it('build includes examples by default', () => {
    const result = promptBuilder.build({ includeTools: false, includeTemplates: false, includeExamples: true });
    expect(result).toContain('## 示例');
  });

  it('build excludes examples when includeExamples=false', () => {
    const result = promptBuilder.build({ includeExamples: false });
    expect(result).not.toContain('## 示例');
  });

  it('build respects maxTemplates limit', () => {
    // Register extra root composites
    registerTestNode({ id: 'complex-flow', name: 'complex-flow', kind: 'composite', children: ['click'] });
    registerTestNode({ id: 'data-flow', name: 'data-flow', kind: 'composite', children: ['type'] });

    const result = promptBuilder.build({ maxTemplates: 2, includeTools: false, includeExamples: false });
    // Should only list 2 of the root templates
    const templateSection = result.split('## 可用模板')[1] || '';
    const templateCount = (templateSection.match(/### /g) || []).length;
    expect(templateCount).toBeLessThanOrEqual(2);
  });

  it('build respects maxExamples limit', () => {
    const result = promptBuilder.build({ maxExamples: 1, includeTools: false, includeTemplates: false });
    const exampleSection = result.split('## 示例')[1] || '';
    const exampleCount = (exampleSection.match(/### /g) || []).length;
    expect(exampleCount).toBe(1);
  });

  it('buildToolContext returns chinese catalog by default', () => {
    const result = promptBuilder.buildToolContext();
    expect(result).toContain('鼠标操作');
    expect(result).toContain('键盘操作');
  });

  it('buildToolContext returns english catalog when specified', () => {
    const result = promptBuilder.buildToolContext('english');
    expect(result).toContain('Mouse Operations');
    expect(result).toContain('Keyboard');
  });

  it('build returns empty template section when no nodes registered', () => {
    // Clear all nodes
    for (const n of htnRegistry.getAll()) {
      (htnRegistry as any).nodes.delete(n.id);
      (htnRegistry as any).parentToChildren.delete(n.id);
      (htnRegistry as any).childToParents.delete(n.id);
    }
    const result = promptBuilder.build({ includeTemplates: true, includeTools: false, includeExamples: false });
    expect(result).not.toContain('## 可用模板');
  });
});
