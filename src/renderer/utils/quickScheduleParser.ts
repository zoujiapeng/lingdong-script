/**
 * 快速调度解析器 — 用自然语言创建定时调度，纯规则匹配，无需 LLM
 *
 * 输入: "今晚9点提醒我写周报并打开企业微信"
 * 输出: { triggers, notify_message, launch_target, confidence }
 */
import type { TriggerDef } from '../types';

export type RiskLevel = 'info' | 'notify' | 'confirm' | 'critical';

export interface QuickScheduleResult {
  name: string;
  triggers: TriggerDef[];
  risk_level: RiskLevel;
  notify_message?: string;
  launch_target?: string;
  template_id?: string;
  fuzzy?: true;
  confidence: number; // 0-100
  original: string;
}

// ── 中文数字 → 阿拉伯数字 ─────────────────────────────────

const CN_TO_DIGIT: Record<string, string> = {
  '零': '0', '一': '1', '二': '2', '三': '3', '四': '4',
  '五': '5', '六': '6', '七': '7', '八': '8', '九': '9',
};

const TENS_MAP: Record<string, number> = { '一': 10, '二': 20, '三': 30, '四': 40, '五': 50 };
const ONES_MAP: Record<string, number> = { '一': 1, '二': 2, '三': 3, '四': 4, '五': 5, '六': 6, '七': 7, '八': 8, '九': 9 };

function normalizeCnNumbers(text: string): string {
  let s = text;
  // Step 1: Chinese compound tens for minute/second (三十分→30分, 三十五分→35分)
  // Must run BEFORE single-digit conversion so "五" in "三十五" is still Chinese
  s = s.replace(/([一二三四五])十([一二三四五六七八九])?\s*(?=分|秒)/g, (_, tens, ones) => {
    const t = TENS_MAP[tens] ?? 0;
    const o = ones ? (ONES_MAP[ones] ?? 0) : 0;
    return String(t + o);
  });
  // Step 2: 一~九 before time units (including date unit 号)
  for (const [cn, d] of Object.entries(CN_TO_DIGIT)) {
    s = s.replace(new RegExp(`${cn}(?=点|分|秒|时|半|后|刻|号)`, 'g'), d);
  }
  // Step 3: 十N → 1N (十二点 → 12点, 十五号 → 15号)
  s = s.replace(/十(\d)(?=点|分|秒|时|半|号)/g, (_, d) => String(10 + parseInt(d)));
  // Step 4: standalone 十 → 10
  s = s.replace(/(?<!\d)十(?=点|分|秒|时|半|刻|号)/g, '10');
  // Step 5: 零点 → 0点
  s = s.replace('零点', '0点');
  return s;
}

// ── 时段词 ────────────────────────────────────────────────

const PERIOD_HOUR_OFFSET: Record<string, number> = {
  凌晨: 0, 清晨: 0, 早上: 0, 上午: 0, 早晨: 0,
  中午: 0, 下午: 12, 傍晚: 12, 晚上: 12, 夜晚: 12,
};
const PERIOD_WORDS = Object.keys(PERIOD_HOUR_OFFSET).sort((a, b) => b.length - a.length);
const EVENING_WORDS = new Set(['今晚', '明晚', '今夜']);

function findPeriod(text: string): string {
  for (const p of PERIOD_WORDS) {
    if (text.includes(p)) return p;
  }
  return '';
}

function adjustHour(h: number, period: string, dayStr: string): number {
  if (period && period in PERIOD_HOUR_OFFSET) {
    const off = PERIOD_HOUR_OFFSET[period];
    if (off === 12 && h < 12) return h + 12;
    if (off === 0 && h >= 12) return h === 12 || h === 24 ? 0 : h - 12;
  } else if (EVENING_WORDS.has(dayStr)) {
    if (h < 6) return h + 12;
    if (h >= 6 && h < 12) return h + 12;
    if (h === 12) return 0;
  }
  return h;
}

function extractHourMin(text: string, hIdx: number, minIdx: number): { h: number; m: number } {
  let h = parseInt(text[hIdx]);
  const hasHalf = text.includes('半');
  // "半" means 30 min instead of explicit minute
  if (text[minIdx] !== undefined) {
    return { h, m: parseInt(text[minIdx]) };
  }
  return { h, m: hasHalf ? 30 : 0 };
}

function nextWeekday(targetDow: number, includeToday = false): Date {
  const today = new Date();
  const current = today.getDay() === 0 ? 7 : today.getDay(); // Mon=1...Sun=7
  let daysAhead = targetDow - current;
  if (daysAhead < 0 || (daysAhead === 0 && !includeToday)) {
    daysAhead += 7;
  }
  const result = new Date(today);
  result.setDate(result.getDate() + daysAhead);
  return result;
}

// ── 时间解析 ──────────────────────────────────────────────

const WEEKDAY_NAMES: Record<string, number> = {
  一: 1, 二: 2, 三: 3, 四: 4, 五: 5, 六: 6, 日: 7, 天: 7,
};

interface TimeParseResult {
  cron?: string;
  interval_seconds?: number;
  fuzzy?: true;
  confidence: number;
  matched: string;
  remaining: string;
}

function parseTime(text: string): TimeParseResult {
  let remaining = normalizeCnNumbers(text.trim());

  // 1. cron: 每星期X / 每周X (+半 suffix)
  let m = remaining.match(
    /每(?:星期|周)([一二三四五六日天])\s*(?:凌晨|清晨|早上|上午|早晨|中午|下午|傍晚|晚上|夜晚)?\s*(\d+)\s*点\s*(?:(\d+)\s*分?|半)?/
  );
  if (m) {
    const dow = WEEKDAY_NAMES[m[1]];
    if (dow && dow <= 7) {
      let h = parseInt(m[2]);
      const hasHalf = m[0].includes('半');
      const explicitMin = m[3] ? parseInt(m[3]) : 0;
      const mi = explicitMin || (hasHalf ? 30 : 0);
      const period = findPeriod(m[0]);
      h = adjustHour(h, period, '');
      if (h >= 0 && h <= 23 && mi >= 0 && mi <= 59) {
        const cronDow = dow === 7 ? 0 : dow;
        return { cron: `${mi} ${h} * * ${cronDow}`, confidence: 95, matched: m[0], remaining: remaining.replace(m[0], '').trim() };
      }
    }
  }

  // 2. cron: 每工作日 (+半 suffix)
  m = remaining.match(
    /(?:每工作日|每个工作日|每工作天)\s*(?:凌晨|清晨|早上|上午|早晨|中午|下午|傍晚|晚上|夜晚)?\s*(\d+)\s*点\s*(?:(\d+)\s*分?|半)?/
  );
  if (m) {
    let h = parseInt(m[1]);
    const hasHalf = m[0].includes('半');
    const explicitMin = m[2] ? parseInt(m[2]) : 0;
    const mi = explicitMin || (hasHalf ? 30 : 0);
    const period = findPeriod(m[0]);
    h = adjustHour(h, period, '');
    if (h >= 0 && h <= 23 && mi >= 0 && mi <= 59) {
      return { cron: `${mi} ${h} * * 1-5`, confidence: 95, matched: m[0], remaining: remaining.replace(m[0], '').trim() };
    }
  }

  // 3. cron: 每天 / 每日 / 每晚 / 每夜 / 每早 / 每朝 (+半 suffix)
  m = remaining.match(
    /(?:每天|每日|天天|每晚|每夜|每早|每朝)\s*(?:凌晨|清晨|早上|上午|早晨|中午|下午|傍晚|晚上|夜晚)?\s*(\d+)\s*点\s*(?:(\d+)\s*分?|半)?/
  );
  if (m) {
    let h = parseInt(m[1]);
    const hasHalf = m[0].includes('半');
    const explicitMin = m[2] ? parseInt(m[2]) : 0;
    const mi = explicitMin || (hasHalf ? 30 : 0);
    const period = findPeriod(m[0]);
    h = adjustHour(h, period, '');
    // Auto-adjust: 每晚/每夜 默认+12h
    const prefix = m[0];
    if (prefix.includes('每晚') || prefix.includes('每夜')) {
      if (h < 12) { h += 12; if (h === 24) h = 0; }
      else if (h === 12) h = 0; // "每晚12点" → 0:00 (午夜)
    }
    if (h >= 0 && h <= 23 && mi >= 0 && mi <= 59) {
      return { cron: `${mi} ${h} * * *`, confidence: 95, matched: m[0], remaining: remaining.replace(m[0], '').trim() };
    }
  }

  // 4. cron: 每N小时
  m = remaining.match(/每(\d+)\s*小时/);

  // 3b. cron: 每周末
  m = remaining.match(
    /每周末\s*(?:凌晨|清晨|早上|上午|早晨|中午|下午|傍晚|晚上|夜晚)?\s*(\d+)\s*点\s*(?:(\d+)\s*分?|半)?/
  );
  if (m) {
    let h = parseInt(m[1]);
    const hasHalf = m[0].includes('半');
    const explicitMin = m[2] ? parseInt(m[2]) : 0;
    const mi = explicitMin || (hasHalf ? 30 : 0);
    const period = findPeriod(m[0]);
    h = adjustHour(h, period, '');
    if (h >= 0 && h <= 23 && mi >= 0 && mi <= 59) {
      return { cron: `${mi} ${h} * * 0,6`, confidence: 95, matched: m[0], remaining: remaining.replace(m[0], '').trim() };
    }
  }

  // 3c. cron: 每月X号
  m = remaining.match(
    /每月(\d+)[号日]\s*(?:凌晨|清晨|早上|上午|早晨|中午|下午|傍晚|晚上|夜晚)?\s*(\d+)\s*点\s*(?:(\d+)\s*分?|半)?/
  );
  if (m) {
    const dom = parseInt(m[1]);
    let h = parseInt(m[2]);
    const hasHalf = m[0].includes('半');
    const explicitMin = m[3] ? parseInt(m[3]) : 0;
    const mi = explicitMin || (hasHalf ? 30 : 0);
    const period = findPeriod(m[0]);
    h = adjustHour(h, period, '');
    if (dom >= 1 && dom <= 31 && h >= 0 && h <= 23 && mi >= 0 && mi <= 59) {
      return { cron: `${mi} ${h} ${dom} * *`, confidence: 95, matched: m[0], remaining: remaining.replace(m[0], '').trim() };
    }
  }

  // 4. cron: 每N小时
  m = remaining.match(/每(\d+)\s*小时/);
  if (m) {
    const n = parseInt(m[1]);
    if (n >= 1 && n <= 23) {
      return { cron: `0 */${n} * * *`, confidence: 80, matched: m[0], remaining: remaining.replace(m[0], '').trim() };
    }
  }

  // 4b. 每半小时 (interval)
  m = remaining.match(/每半\s*(?:个)?\s*小时/);
  if (m) {
    return { interval_seconds: 1800, confidence: 80, matched: m[0], remaining: remaining.replace(m[0], '').trim() };
  }

  // 5. 相对时间 (intervals)
  // 5a. 半分钟
  m = remaining.match(/半\s*分钟\s*(?:后|以后)/);
  if (m) {
    return { interval_seconds: 30, confidence: 75, matched: m[0], remaining: remaining.replace(m[0], '').trim() };
  }

  // 5b. N小时后
  m = remaining.match(/(\d+)\s*小时\s*(?:后|以后)/);
  if (m) {
    return { interval_seconds: parseInt(m[1]) * 3600, confidence: 80, matched: m[0], remaining: remaining.replace(m[0], '').trim() };
  }

  // 5c. N小时M分后
  m = remaining.match(/(\d+)\s*(?:小时|时)\s*(\d+)\s*分\s*(?:后|以后)/);
  if (m) {
    return { interval_seconds: parseInt(m[1]) * 3600 + parseInt(m[2]) * 60, confidence: 75, matched: m[0], remaining: remaining.replace(m[0], '').trim() };
  }

  // 5d. 每隔N分钟
  m = remaining.match(/每隔\s*(\d+)\s*分钟/);
  if (m) {
    return { interval_seconds: parseInt(m[1]) * 60, confidence: 80, matched: m[0], remaining: remaining.replace(m[0], '').trim() };
  }

  // 5e. 每隔N秒
  m = remaining.match(/每隔\s*(\d+)\s*秒(?:钟)?/);
  if (m) {
    return { interval_seconds: parseInt(m[1]), confidence: 80, matched: m[0], remaining: remaining.replace(m[0], '').trim() };
  }

  // 5f. N分钟后
  m = remaining.match(/(\d+)\s*分钟\s*(?:后|以后)/);
  if (m) {
    return { interval_seconds: parseInt(m[1]) * 60, confidence: 85, matched: m[0], remaining: remaining.replace(m[0], '').trim() };
  }

  // 5g. N秒后
  m = remaining.match(/(\d+)\s*秒(?:钟)?\s*(?:后|以后)/);
  if (m) {
    return { interval_seconds: parseInt(m[1]), confidence: 85, matched: m[0], remaining: remaining.replace(m[0], '').trim() };
  }

  // 6. 固定时间: 今晚/明天/后天/大后天 + 时间
  m = remaining.match(
    /(今晚|今天|今夜|明早|明天|明晚|明日|后天|後天|大后天|大後天)\s*(凌晨|清晨|早上|上午|早晨|中午|下午|傍晚|晚上|夜晚)?\s*(\d+)\s*点\s*(?:(\d+)\s*分?|半)?/
  );
  if (m) {
    const dayStr = m[1];
    const period = m[2] || '';
    let h = parseInt(m[3]);
    const hasHalf = m[0].includes('半');
    const explicitMin = m[4] ? parseInt(m[4]) : 0;
    const mi = explicitMin || (hasHalf ? 30 : 0);
    h = adjustHour(h, period, dayStr);
    if (h >= 0 && h <= 23 && mi >= 0 && mi <= 59) {
      const dayOffsets: Record<string, number> = { 今晚: 0, 今天: 0, 今夜: 0, 明早: 1, 明天: 1, 明晚: 1, 明日: 1, 后天: 2, 後天: 2, 大后天: 3, 大後天: 3 };
      const offset = dayOffsets[dayStr] ?? 0;
      const date = new Date();
      date.setDate(date.getDate() + offset);
      date.setHours(h, mi, 0, 0);
      if (offset > 0 || date > new Date()) {
        return { cron: `${mi} ${h} ${date.getDate()} ${date.getMonth() + 1} *`, confidence: 92, matched: m[0], remaining: remaining.replace(m[0], '').trim() };
      }
      return { cron: `${mi} ${h} * * *`, confidence: 60, matched: m[0], remaining: remaining.replace(m[0], '').trim() };
    }
  }

  // 6b. 相对星期: 下周一/本周五
  m = remaining.match(
    /(下|本|这)(?:周|星期|週)\s*([一二三四五六日天])\s*(?:凌晨|清晨|早上|上午|早晨|中午|下午|傍晚|晚上|夜晚)?\s*(\d+)\s*点\s*(?:(\d+)\s*分?|半)?/
  );
  if (m) {
    const prefix = m[1];
    const dow = WEEKDAY_NAMES[m[2]];
    if (dow && dow <= 7) {
      let h = parseInt(m[3]);
      const hasHalf = m[0].includes('半');
      const explicitMin = m[4] ? parseInt(m[4]) : 0;
      const mi = explicitMin || (hasHalf ? 30 : 0);
      const period = findPeriod(m[0]);
      h = adjustHour(h, period, '');
      if (h >= 0 && h <= 23 && mi >= 0 && mi <= 59) {
        const includeToday = prefix === '本' || prefix === '这';
        const target = nextWeekday(dow, includeToday);
        target.setHours(h, mi, 0, 0);
        if (includeToday && target <= new Date()) {
          target.setDate(target.getDate() + 7);
        }
        return { cron: `${mi} ${h} ${target.getDate()} ${target.getMonth() + 1} *`, confidence: 90, matched: m[0], remaining: remaining.replace(m[0], '').trim() };
      }
    }
  }

  // 8. 模糊时间: 9点左右/9点多/9点刚过
  m = remaining.match(/^[^\d]*?(\d+)\s*点\s*(左右|多|刚过|出头|来钟)/);
  if (m) {
    let h = parseInt(m[1]);
    const period = findPeriod(m[0]);
    h = adjustHour(h, period, '');
    if (h >= 0 && h <= 23) {
      return { cron: `0 ${h} * * *`, confidence: 55, matched: m[0], remaining: remaining.replace(m[0], '').trim() };
    }
  }

  // 7. 裸时间: 只有 "X点"
  m = remaining.match(/^[^\d]*?(\d+)\s*点\s*(?:(\d+)\s*分?|半)?/);
  if (m) {
    let h = parseInt(m[1]);
    const hasHalf = m[0].includes('半');
    const explicitMin = m[2] ? parseInt(m[2]) : 0;
    const mi = explicitMin || (hasHalf ? 30 : 0);
    if (h >= 0 && h <= 23 && mi >= 0 && mi <= 59) {
      return { cron: `${mi} ${h} * * *`, confidence: 40, matched: m[0], remaining: remaining.replace(m[0], '').trim() };
    }
  }

  return { confidence: 0, matched: '', remaining: text };
}

// ── 动作解析 ──────────────────────────────────────────────

function parseActions(text: string): {
  notify_message?: string;
  launch_target?: string;
  template_id?: string;
  confidence: number;
} {
  let confidence = 0;
  const result: any = {};

  // 提醒我X
  let m = text.match(/提醒我\s*(.+?)(?:$|并|而且|还有|然后|再)/);
  if (!m) m = text.match(/提醒我\s*(.+)/);
  if (m) {
    const notify = m[1].trim().replace(/(并|而且|还有|然后|再)\s*$/, '').trim();
    if (notify) { result.notify_message = notify; confidence += 40; }
  }

  // 打开X / 启动X
  m = text.match(/(?:打开|启动|开启|运行程序)\s*(.+?)(?:$|并|而且|还有|然后|再)/);
  if (!m) m = text.match(/(?:打开|启动|开启|运行程序)\s*(.+)/);
  if (m) {
    const launch = m[1].trim();
    if (launch) { result.launch_target = launch; confidence += 30; }
  }

  // 运行X / 执行X
  m = text.match(/(?:运行|执行|启动流程)\s*(.+?)(?:$|并|而且|还有|然后|再)/);
  if (!m) m = text.match(/(?:运行|执行|启动流程)\s*(.+)/);
  if (m) {
    const run = m[1].trim();
    if (run) { result.template_id = run; confidence += 30; }
  }

  return { ...result, confidence: Math.min(confidence, 90) };
}

// ── 风险等级判定 ─────────────────────────────────────────

const RISK_CRITICAL_KW = ['删除', '格式化', '清除文件', '卸载', '清空', '擦除'];
const RISK_CONFIRM_KW = ['关机', '重启', '注销', '关闭电脑', '重新启动', '重启电脑', '睡眠', '休眠'];
const RISK_NOTIFY_KW = ['打开', '启动', '运行', '执行', '运行程序', '开启'];

function detectRiskLevel(text: string, actions: Record<string, any>): RiskLevel {
  for (const kw of RISK_CRITICAL_KW) { if (text.includes(kw)) return 'critical'; }
  for (const kw of RISK_CONFIRM_KW) { if (text.includes(kw)) return 'confirm'; }
  if (actions.launch_target || actions.template_id) {
    for (const kw of RISK_NOTIFY_KW) { if (text.includes(kw)) return 'notify'; }
  }
  return 'info';
}

// ── 主入口 ────────────────────────────────────────────────

const SCHEDULE_KEYWORDS = [
  '提醒', '定时', '预约', '计划', '安排',
  '今晚', '明天', '后天', '明早', '明晚',
  '每天', '每日', '每周', '每工作日',
  '分钟后', '秒后', '小时后',
  '星期',  // 每星期一~日
  '周',    // 每周一~日
  '每晚', '每夜', '每早', '每朝',  // 每日变体
  '每隔',       // 每隔N分钟/秒
  '零点', '零时',  // 午夜
  '号',         // 每月X号
  '点左右',     // 模糊时间
];

/** 检查文本是否可能是调度意图 */
export function isScheduleIntent(text: string): boolean {
  const t = text.trim();
  if (t.length < 4) return false;
  return SCHEDULE_KEYWORDS.some(k => t.includes(k));
}

/** 解析自然语言调度文本 */
export function parseQuickSchedule(text: string): QuickScheduleResult {
  const original = text.trim();

  const timeResult = parseTime(original);
  const actionResult = parseActions(original);

  // 计算总置信度
  let confidence = 0;
  if (timeResult.confidence > 0) confidence += timeResult.confidence * 0.55;
  if (actionResult.confidence > 0) confidence += actionResult.confidence * 0.45;

  // 生成调度名
  let name = '定时任务';
  if (actionResult.notify_message) name = actionResult.notify_message;
  else if (actionResult.launch_target) name = `打开${actionResult.launch_target}`;
  else if (actionResult.template_id) name = `运行${actionResult.template_id}`;

  // 构建 trigger
  const triggers: TriggerDef[] = [];
  if (timeResult.cron) {
    triggers.push({ type: 'cron', cron_expr: timeResult.cron });
  } else if (timeResult.interval_seconds !== undefined) {
    triggers.push({ type: 'interval', interval_seconds: timeResult.interval_seconds });
  }

  if (triggers.length === 0 && actionResult.confidence > 0) {
    confidence = Math.min(confidence, 40);
  }

  return {
    name,
    triggers,
    risk_level: detectRiskLevel(original, actionResult),
    notify_message: actionResult.notify_message,
    launch_target: actionResult.launch_target,
    template_id: actionResult.template_id,
    ...(timeResult.fuzzy ? { fuzzy: true as const } : {}),
    confidence: Math.round(Math.min(confidence, 99)),
    original,
  };
}
