/**
 * 简易 cron 表达式匹配器
 * 格式: "分 时 日 月 周" (5字段)
 *
 *  字段 | 范围   | 特殊字符
 *  ---- | ------ | --------
 *  分   | 0-59   | all values, step, list, range
 *  时   | 0-23   | all values, step, list, range
 *  日   | 1-31   | all values, step, list, range
 *  月   | 1-12   | all values, step, list, range
 *  周   | 0-7    | all values, step, list, range (0和7都表示周日)
 *
 * 支持: 所有值(*)、步进、列表(N,M,O)、范围(N-M)
 */

function matchField(value: number, pattern: string): boolean {
  if (pattern === '*') return true;

  // 逗号分隔列表
  if (pattern.includes(',')) {
    return pattern.split(',').some(p => matchField(value, p.trim()));
  }

  // 步进: */N 或 N-M/N
  const stepMatch = pattern.match(/^(\d+)(?:-(\d+))?\/(\d+)$/);
  if (stepMatch) {
    const start = stepMatch[1] ? parseInt(stepMatch[1]) : 0;
    const end = stepMatch[2] ? parseInt(stepMatch[2]) : 59;
    const step = parseInt(stepMatch[3]);
    if (step === 0) return false;
    return value >= start && value <= end && (value - start) % step === 0;
  }

  // 范围: N-M
  const rangeMatch = pattern.match(/^(\d+)-(\d+)$/);
  if (rangeMatch) {
    const low = parseInt(rangeMatch[1]);
    const high = parseInt(rangeMatch[2]);
    return value >= low && value <= high;
  }

  // 精确数字
  return parseInt(pattern) === value;
}

function matchCron(cronExpr: string, date: Date = new Date()): boolean {
  const parts = cronExpr.trim().split(/\s+/);
  if (parts.length !== 5) return false;

  const [minute, hour, dayOfMonth, month, dayOfWeek] = parts;

  const m = date.getMinutes();
  const h = date.getHours();
  const d = date.getDate();
  const mo = date.getMonth() + 1; // JS month is 0-based
  const dw = date.getDay();       // 0=Sun, 6=Sat

  if (!matchField(m, minute)) return false;
  if (!matchField(h, hour)) return false;
  if (!matchField(d, dayOfMonth)) return false;
  if (!matchField(mo, month)) return false;
  // dayOfWeek: 0 and 7 both mean Sunday
  if (!matchField(dw, dayOfWeek) && !matchField(dw === 0 ? 7 : dw, dayOfWeek)) return false;

  return true;
}
