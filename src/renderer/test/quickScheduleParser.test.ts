import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { parseQuickSchedule, isScheduleIntent } from '../utils/quickScheduleParser';

describe('isScheduleIntent', () => {
  it('detects schedule keywords', () => {
    expect(isScheduleIntent('今晚9点提醒我')).toBe(true);
    expect(isScheduleIntent('明天早上8点打开记事本')).toBe(true);
    expect(isScheduleIntent('每工作日9点打卡')).toBe(true);
    expect(isScheduleIntent('5分钟后提醒我')).toBe(true);
  });

  it('rejects short or non-schedule text', () => {
    expect(isScheduleIntent('hi')).toBe(false);
    expect(isScheduleIntent('打开记事本')).toBe(false);
    expect(isScheduleIntent('你好')).toBe(false);
  });
});

describe('parseQuickSchedule', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date(2026, 4, 16, 20, 0, 0)); // May 16, 2026 8pm
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('parses "今晚9点提醒我写周报"', () => {
    const r = parseQuickSchedule('今晚9点提醒我写周报');
    expect(r.confidence).toBeGreaterThanOrEqual(65);
    expect(r.triggers[0].type).toBe('cron');
    expect(r.triggers[0].cron_expr).toMatch(/^0 21/);
    expect(r.notify_message).toBe('写周报');
  });

  it('parses "今晚11点提醒我睡觉"', () => {
    const r = parseQuickSchedule('今晚11点提醒我睡觉');
    expect(r.confidence).toBeGreaterThanOrEqual(60);
    expect(r.triggers[0].cron_expr).toMatch(/^0 23/);
  });

  it('parses "明天下午5点半提醒我开会"', () => {
    const r = parseQuickSchedule('明天下午5点半提醒我开会');
    expect(r.confidence).toBeGreaterThanOrEqual(60);
    expect(r.triggers[0].cron_expr).toMatch(/^30 17/);
  });

  it('parses "每工作日上午9点打开企业微信"', () => {
    const r = parseQuickSchedule('每工作日上午9点打开企业微信');
    expect(r.confidence).toBeGreaterThanOrEqual(60);
    expect(r.triggers[0].cron_expr).toBe('0 9 * * 1-5');
  });

  it('parses "每星期一上午9点运行日报"', () => {
    const r = parseQuickSchedule('每星期一上午9点运行日报');
    expect(r.confidence).toBeGreaterThanOrEqual(60);
    expect(r.triggers[0].cron_expr).toMatch(/\* \* 1$/);
  });

  it('parses "5分钟后提醒我检查邮件"', () => {
    const r = parseQuickSchedule('5分钟后提醒我检查邮件');
    expect(r.triggers[0].type).toBe('interval');
    expect(r.triggers[0].interval_seconds).toBe(300);
  });

  it('parses "大后天下午3点提醒我"', () => {
    const r = parseQuickSchedule('大后天下午3点提醒我');
    expect(r.triggers[0].type).toBe('cron');
    expect(r.triggers[0].cron_expr).toMatch(/^0 15/);
  });

  it('parses "下周一上午9点提醒我开会"', () => {
    const r = parseQuickSchedule('下周一上午9点提醒我开会');
    expect(r.triggers[0].type).toBe('cron');
    expect(r.triggers[0].cron_expr).toMatch(/^0 9/);
  });

  it('parses "每周五晚上8点提醒我"', () => {
    const r = parseQuickSchedule('每周五晚上8点提醒我');
    expect(r.triggers[0].type).toBe('cron');
    expect(r.triggers[0].cron_expr).toMatch(/0 20 \* \* 5$/);
  });

  it('parses "每晚9点提醒我吃药"', () => {
    const r = parseQuickSchedule('每晚9点提醒我吃药');
    expect(r.triggers[0].cron_expr).toBe('0 21 * * *');
  });

  it('parses "每早8点打开日报"', () => {
    const r = parseQuickSchedule('每早8点打开日报');
    expect(r.triggers[0].cron_expr).toBe('0 8 * * *');
  });

  it('parses "每晚12点" as midnight', () => {
    const r = parseQuickSchedule('每晚12点提醒我');
    expect(r.triggers[0].cron_expr).toBe('0 0 * * *');
  });

  it('parses "每隔5分钟提醒我"', () => {
    const r = parseQuickSchedule('每隔5分钟提醒我');
    expect(r.triggers[0].type).toBe('interval');
    expect(r.triggers[0].interval_seconds).toBe(300);
  });

  it('parses "每3小时提醒我喝水"', () => {
    const r = parseQuickSchedule('每3小时提醒我喝水');
    expect(r.triggers[0].cron_expr).toBe('0 */3 * * *');
  });

  it('parses "9点左右提醒我吃药" as fuzzy', () => {
    const r = parseQuickSchedule('9点左右提醒我吃药');
    expect(r.triggers[0].cron_expr).toBe('0 9 * * *');
    expect(r.fuzzy).toBe(true);
  });

  it('parses "每月1号上午10点打开日报"', () => {
    const r = parseQuickSchedule('每月1号上午10点打开日报');
    expect(r.triggers[0].cron_expr).toBe('0 10 1 * *');
  });

  it('detects risk level: info for notify only', () => {
    expect(parseQuickSchedule('今晚9点提醒我喝水').risk_level).toBe('info');
  });

  it('detects risk level: notify for launch', () => {
    expect(parseQuickSchedule('每工作日上午9点打开企业微信').risk_level).toBe('notify');
  });

  it('detects risk level: confirm for shutdown', () => {
    expect(parseQuickSchedule('明天下午3点关机').risk_level).toBe('confirm');
  });

  it('detects risk level: critical for delete', () => {
    expect(parseQuickSchedule('每星期一上午9点删除临时文件').risk_level).toBe('critical');
  });

  it('normalizes Chinese numerals', () => {
    const r = parseQuickSchedule('今晚十一点半提醒我睡觉');
    expect(r.triggers[0].cron_expr).toMatch(/^30 23/);
  });

  it('parses "每周末上午9点打开企业微信"', () => {
    const r = parseQuickSchedule('每周末上午9点打开企业微信');
    expect(r.triggers[0].cron_expr).toBe('0 9 * * 0,6');
  });
});
