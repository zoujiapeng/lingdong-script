import { describe, it, expect, beforeEach } from 'vitest';
import { matchExact } from '../utils/templateMatcher';
import { templateRegistry } from '../utils/templateRegistry';

describe('templateMatcher', () => {
  beforeEach(() => {
    templateRegistry.clear();
    templateRegistry.register({
      id: 'login-automation',
      name: '登录自动化',
      description: '自动登录流程',
      category: 'app',
      tags: ['app', 'login'],
      aliases: ['登录', 'login', 'signin'],
      keywords: ['账号', '密码', '登录', '认证', '表单'],
      source: 'preset',
      hasScript: true,
    });
    templateRegistry.register({
      id: 'desktop-monitor',
      name: '桌面图标监控',
      description: '循环检测桌面多张目标图片',
      category: 'image',
      tags: ['image', 'monitor'],
      aliases: ['桌面监控', '图标监控', 'monitor'],
      keywords: ['监控', '检测', '桌面', '图标'],
      source: 'preset',
      hasScript: true,
    });
  });

  it('returns null for empty query', () => {
    expect(matchExact('')).toBeNull();
    expect(matchExact('  ')).toBeNull();
  });

  it('matches exact name', () => {
    const result = matchExact('登录自动化');
    expect(result).not.toBeNull();
    expect(result!.confidence).toBe('exact');
    expect(result!.template.id).toBe('login-automation');
  });

  it('matches by alias', () => {
    const result = matchExact('login');
    expect(result).not.toBeNull();
    expect(result!.confidence).toBe('exact');
    expect(result!.template.id).toBe('login-automation');
  });

  it('matches with Chinese verb prefix stripped', () => {
    const result = matchExact('打开登录自动化');
    expect(result).not.toBeNull();
    expect(result!.confidence).toBe('exact');
  });

  it('matches fuzzy partial name', () => {
    const result = matchExact('桌面图标');
    expect(result).not.toBeNull();
    expect(result!.confidence).toBe('fuzzy');
    expect(result!.template.id).toBe('desktop-monitor');
  });

  it('returns null for no match', () => {
    expect(matchExact('完全不存在的模板名')).toBeNull();
  });

  it('is case-insensitive', () => {
    expect(matchExact('LOGIN')).not.toBeNull();
    expect(matchExact('Monitor')).not.toBeNull();
  });

  it('fuzzy strips prefix then matches', () => {
    const result = matchExact('运行监控');
    expect(result).not.toBeNull();
    expect(result!.template.id).toBe('desktop-monitor');
  });
});
