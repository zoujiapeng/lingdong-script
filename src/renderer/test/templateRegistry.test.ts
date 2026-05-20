import { describe, it, expect, beforeEach } from 'vitest';
import { templateRegistry, TemplateEntry } from '../utils/templateRegistry';

function makeEntry(overrides: Partial<TemplateEntry> & { id: string; name: string }): TemplateEntry {
  return {
    description: '',
    category: 'general',
    tags: [],
    aliases: [],
    keywords: [],
    source: 'preset',
    hasScript: false,
    ...overrides,
  };
}

describe('templateRegistry', () => {
  beforeEach(() => {
    templateRegistry.clear();
  });

  it('starts empty', () => {
    expect(templateRegistry.count).toBe(0);
    expect(templateRegistry.getAll()).toEqual([]);
  });

  it('register and getById', () => {
    const entry = makeEntry({ id: 'test-1', name: '测试模板', description: '用于测试' });
    templateRegistry.register(entry);
    expect(templateRegistry.count).toBe(1);
    expect(templateRegistry.getById('test-1')?.name).toBe('测试模板');
  });

  it('unregister removes entry', () => {
    templateRegistry.register(makeEntry({ id: 't1', name: 'T1' }));
    templateRegistry.register(makeEntry({ id: 't2', name: 'T2' }));
    expect(templateRegistry.count).toBe(2);
    templateRegistry.unregister('t1');
    expect(templateRegistry.count).toBe(1);
    expect(templateRegistry.getById('t1')).toBeUndefined();
  });

  it('clear removes all entries', () => {
    templateRegistry.register(makeEntry({ id: 'a', name: 'A' }));
    templateRegistry.register(makeEntry({ id: 'b', name: 'B' }));
    templateRegistry.clear();
    expect(templateRegistry.count).toBe(0);
  });

  it('findByExactName matches by name', () => {
    templateRegistry.register(makeEntry({ id: 'login', name: '登录自动化' }));
    expect(templateRegistry.findByExactName('登录自动化')).toBeDefined();
    expect(templateRegistry.findByExactName('不存在的')).toBeUndefined();
  });

  it('findByExactName matches by alias', () => {
    templateRegistry.register(makeEntry({ id: 'login', name: '登录自动化', aliases: ['login', 'signin'] }));
    expect(templateRegistry.findByExactName('login')).toBeDefined();
    expect(templateRegistry.findByExactName('signin')).toBeDefined();
  });

  it('findByExactName matches by keyword', () => {
    templateRegistry.register(makeEntry({ id: 'monitor', name: '桌面监控', keywords: ['监控', '桌面'] }));
    expect(templateRegistry.findByExactName('监控')).toBeDefined();
  });

  it('findByExactName is case-insensitive', () => {
    templateRegistry.register(makeEntry({ id: 'test', name: 'Hello World', aliases: ['hi there'] }));
    expect(templateRegistry.findByExactName('hello world')).toBeDefined();
    expect(templateRegistry.findByExactName('HI THERE')).toBeDefined();
  });

  it('findFuzzy matches partial name', () => {
    templateRegistry.register(makeEntry({ id: 'login', name: '登录自动化' }));
    expect(templateRegistry.findFuzzy('登录')).toBeDefined();
    expect(templateRegistry.findFuzzy('自动化')).toBeDefined();
  });

  it('findFuzzy strips Chinese verb prefixes', () => {
    templateRegistry.register(makeEntry({ id: 'login', name: '登录自动化' }));
    expect(templateRegistry.findFuzzy('打开登录自动化')).toBeDefined();
    expect(templateRegistry.findFuzzy('运行登录自动化')).toBeDefined();
  });

  it('search returns scored results sorted by score', () => {
    templateRegistry.register(makeEntry({ id: 'a', name: '登录自动化', category: 'app', tags: ['login', 'app'] }));
    templateRegistry.register(makeEntry({ id: 'b', name: '桌面监控', category: 'image', tags: ['monitor'] }));
    templateRegistry.register(makeEntry({ id: 'c', name: '表单填写', category: 'app', tags: ['form'] }));

    const results = templateRegistry.search('app');
    expect(results.length).toBeGreaterThan(0);
    // Sorted descending by score
    for (let i = 1; i < results.length; i++) {
      expect(results[i - 1].score).toBeGreaterThanOrEqual(results[i].score);
    }
  });

  it('search returns empty for no match', () => {
    templateRegistry.register(makeEntry({ id: 'x', name: '中文模板' }));
    expect(templateRegistry.search('zzz_nonexistent')).toEqual([]);
  });

  it('toAIContext returns markdown string', () => {
    expect(templateRegistry.toAIContext()).toBe('');
    templateRegistry.register(makeEntry({
      id: 'login', name: '登录自动化', description: '自动登录流程',
      aliases: ['signin'], tags: ['app', 'login'], source: 'preset',
    }));
    const ctx = templateRegistry.toAIContext();
    expect(ctx).toContain('登录自动化');
    expect(ctx).toContain('自动登录流程');
    expect(ctx).toContain('登录');
  });

  it('supports multiple entries and iteration', () => {
    const ids = ['a', 'b', 'c'];
    for (const id of ids) {
      templateRegistry.register(makeEntry({ id, name: `Template ${id}` }));
    }
    expect(templateRegistry.count).toBe(3);
    const all = templateRegistry.getAll();
    expect(all.map(e => e.id).sort()).toEqual(ids);
  });
});
