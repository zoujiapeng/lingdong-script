import { describe, it, expect, beforeEach } from 'vitest';
import { matchSemantic } from '../utils/semanticMatcher';
import { templateRegistry } from '../utils/templateRegistry';

describe('semanticMatcher', () => {
  beforeEach(() => {
    templateRegistry.clear();
    templateRegistry.register({
      id: 'login',
      name: '登录自动化',
      description: '自动登录流程',
      category: 'app',
      tags: ['app', 'login'],
      aliases: ['login', 'signin'],
      keywords: ['账号', '密码', '登录'],
      source: 'preset',
      hasScript: true,
    });
    templateRegistry.register({
      id: 'monitor',
      name: '桌面图标监控',
      description: '循环检测桌面图标',
      category: 'image',
      tags: ['image', 'monitor'],
      aliases: ['监控', 'monitor'],
      keywords: ['桌面', '图标', '检测'],
      source: 'preset',
      hasScript: true,
    });
    templateRegistry.register({
      id: 'filler',
      name: '表单填写器',
      description: '自动填写表单',
      category: 'app',
      tags: ['app', 'form'],
      aliases: ['填表', 'form'],
      keywords: ['表单', '填写'],
      source: 'preset',
      hasScript: true,
    });
  });

  it('returns candidates sorted by score', () => {
    const result = matchSemantic('登录');
    expect(result.candidates.length).toBeGreaterThan(0);
    for (let i = 1; i < result.candidates.length; i++) {
      expect(result.candidates[i - 1].score).toBeGreaterThanOrEqual(result.candidates[i].score);
    }
  });

  it('auto-selects when score >= 90', () => {
    const result = matchSemantic('登录自动化');
    expect(result.autoSelect).toBe(true);
    expect(result.best).toBeDefined();
    expect(result.best!.template.id).toBe('login');
  });

  it('does not auto-select for low confidence', () => {
    const result = matchSemantic('完全不匹配的内容');
    expect(result.autoSelect).toBe(false);
    expect(result.best).toBeUndefined();
  });

  it('returns at most 5 candidates', () => {
    const result = matchSemantic('app');
    expect(result.candidates.length).toBeLessThanOrEqual(5);
  });

  it('records the original query', () => {
    const result = matchSemantic('登录');
    expect(result.query).toBe('登录');
  });

  it('matches by keyword with score 80', () => {
    const result = matchSemantic('账号');
    expect(result.autoSelect).toBe(false);
    expect(result.best).toBeUndefined();
    expect(result.candidates[0].score).toBe(80);
    expect(result.candidates[0].template.id).toBe('login');
  });
});
