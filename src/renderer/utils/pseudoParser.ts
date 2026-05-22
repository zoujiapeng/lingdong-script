import { FlowScript, FlowNode, FlowEdge, NodeType, NodeParams } from '../types';
import { nanoid } from 'nanoid';

// ─── Line parsing ───

interface ParseLine {
  indent: number;
  text: string;
  lineNumber: number;
}

function parseIndentation(code: string): ParseLine[] {
  const lines: ParseLine[] = [];
  for (const [i, raw] of code.split('\n').entries()) {
    const trimmed = raw.trimEnd();
    if (trimmed.trim() === '' || trimmed.trim().startsWith('#')) continue;
    const indent = trimmed.length - trimmed.trimStart().length;
    // Normalize: 4 spaces = 1 indent level
    const normalizedIndent = Math.round(indent / 2);
    // Strip leading numbering like "1. ", "1) ", "(1) "
    const clean = trimmed.trim().replace(/^(?:\d+[.)]\s*|\(\d+\)\s*)+/, '');
    lines.push({ indent: normalizedIndent, text: clean, lineNumber: i + 1 });
  }
  return lines;
}

// ─── Statement classification ───

interface ParsedStmt {
  type: string;
  params: any;
  indent: number;
  children: ParsedStmt[];
  isElse?: boolean;
  isBreak?: boolean;
}

function parseStmt(line: string): { type: string; params: any; isElse?: boolean; isBreak?: boolean } | null {
  // Comments
  if (line.startsWith('#')) return null;

  // Break / 跳出
  if (/^(?:break|跳出)(?:\b|$)/i.test(line)) return { type: 'break', params: {}, isBreak: true };

  // click image "path1" "path2" ... / 点击图片 "path1" ...
  const imgClickRe = /^(?:click\s+image|点击图片)\s+(.+)/i;
  const imgMatch = line.match(imgClickRe);
  if (imgMatch) {
    const rest = imgMatch[1].trim();
    const targets: { path: string }[] = [];
    const pathRe = /"([^"]*)"/g;
    let m;
    while ((m = pathRe.exec(rest)) !== null) {
      if (m[1]) targets.push({ path: m[1] });
    }
    // Parse options — support both English and Chinese keys
    const simMatch = rest.match(/(?:\b|(?<=\s))(?:similarity|相似度)[=:](\d+)/i);
    const timeoutMatch = rest.match(/(?:\b|(?<=\s))(?:timeout|超时)[=:](\d+(\.\d+)?)/i);
    const offsetMatch = rest.match(/(?:\b|(?<=\s))(?:offset|偏移)\((-?\d+),(-?\d+)\)/i);
    const methodMatch = rest.match(/(?:\b|(?<=\s))(single|double|right|middle|单击|双击|右击|中键)(?:\b|(?=\s|$))/i);

    const params: any = {
      mode: 'image',
      targets,
      similarity: simMatch ? parseInt(simMatch[1]) : 90,
      timeout: timeoutMatch ? parseFloat(timeoutMatch[1]) : 5,
      offset_x: offsetMatch ? parseInt(offsetMatch[1]) : 0,
      offset_y: offsetMatch ? parseInt(offsetMatch[2]) : 0,
      delay_after: 0.5,
    };
    if (methodMatch) {
      const m = methodMatch[1].toLowerCase();
      params.method = { '单击': 'single', '双击': 'double', '右击': 'right', '中键': 'middle', '三击': 'triple' }[m] || m;
    }
    return { type: 'mouse_operation', params };
  }

  // click (x, y) [method] / 点击 (x, y) [method]
  // Also handle "点击图片" already caught above, so "点击" alone is click coordinate
  const clickRe = /^(?:click|点击)\s+\((-?\d+)\s*,\s*(-?\d+)\)\s*(single|double|right|middle|triple|单击|双击|右击|中键|三击)?\s*(.*)?/i;
  const clickMatch = line.match(clickRe);
  if (clickMatch) {
    const delayMatch = line.match(/(?:\b|(?<=\s|^))(?:delay|延迟)[=:](\d+(\.\d+)?)/i);
    const m = (clickMatch[3] || 'single').toLowerCase();
    return {
      type: 'mouse_operation',
      params: {
        mode: 'coordinate',
        x: parseInt(clickMatch[1]),
        y: parseInt(clickMatch[2]),
        method: { '单击': 'single', '双击': 'double', '右击': 'right', '中键': 'middle', '三击': 'triple' }[m] || m,
        delay_after: delayMatch ? parseFloat(delayMatch[1]) : 0.5,
      },
    };
  }

  // type "text" [at (x,y)] / 输入 "text" [位置 (x,y)]
  const inputRe = /^(?:type|输入)\s+"([^"]*)"(?:\s+(?:at|位置)\s+\((\d+)\s*,\s*(\d+)\))?\s*(.*)?/i;
  const inputMatch = line.match(inputRe);
  if (inputMatch) {
    const clearMatch = line.match(/(?:\b|(?<=\s|^))(?:clear|不清空)[=:](True|False|true|false)/i);
    const clearVal = clearMatch ? clearMatch[1].toLowerCase() === 'true' : true;
    return {
      type: 'input_text',
      params: {
        text: inputMatch[1],
        x: inputMatch[2] ? parseInt(inputMatch[2]) : undefined,
        y: inputMatch[3] ? parseInt(inputMatch[3]) : undefined,
        click_before: !!inputMatch[2],
        clear_before: clearMatch ? !clearVal : true, // "不清空=True" means clear_before=false
        delay_after: 0.3,
      },
    };
  }

  // press key1+key2+... / 按键 key1+key2+...
  const pressRe = /^(?:press|按键)\s+(.+)/i;
  const pressMatch = line.match(pressRe);
  if (pressMatch) {
    const keys = pressMatch[1].split('+').map((k) => k.trim().toLowerCase());
    const modifiers = ['ctrl', 'alt', 'shift', 'win'];
    const foundMods = keys.filter((k) => modifiers.includes(k));
    const foundKeys = keys.filter((k) => !modifiers.includes(k));
    return {
      type: 'keyboard',
      params: {
        keys: foundKeys.length > 0 ? foundKeys : keys,
        modifiers: foundMods,
        delay_after: 0.2,
      },
    };
  }

  // wait N / 等待 N
  const waitTimeRe = /^(?:wait|等待|等)\s*(\d+(?:\.\d+)?)\s*(?:秒)?/i;
  const waitTimeMatch = line.match(waitTimeRe);
  if (waitTimeMatch) {
    return {
      type: 'wait',
      params: { wait_type: 'time', duration: parseFloat(waitTimeMatch[1]) },
    };
  }

  // wait image "path" / 等待图片 "path"
  const waitImgRe = /^(?:wait\s+image|等待图片)\s+"([^"]*)"(?:\s+(?:timeout|超时)[=:](\d+(\.\d+)?))?\s*/i;
  const waitImgMatch = line.match(waitImgRe);
  if (waitImgMatch) {
    return {
      type: 'wait',
      params: {
        wait_type: 'image',
        template_path: waitImgMatch[1],
        timeout: waitImgMatch[2] ? parseFloat(waitImgMatch[2]) : 10,
      },
    };
  }

  // wait window "title" / 等待窗口 "title"
  const waitWinRe = /^(?:wait\s+window|等待窗口)\s+"([^"]*)"(?:\s+(?:timeout|超时)[=:](\d+(\.\d+)?))?\s*/i;
  const waitWinMatch = line.match(waitWinRe);
  if (waitWinMatch) {
    return {
      type: 'wait',
      params: {
        wait_type: 'window',
        window_title: waitWinMatch[1],
        timeout: waitWinMatch[2] ? parseFloat(waitWinMatch[2]) : 10,
      },
    };
  }

  // swipe (x1,y1) to (x2,y2) / 滑动 (x1,y1) 到 (x2,y2)
  const swipeRe = /^(?:swipe|滑动)\s+\((-?\d+)\s*,\s*(-?\d+)\)\s+(?:to|到)\s+\((-?\d+)\s*,\s*(-?\d+)\)\s*(.*)?/i;
  const swipeMatch = line.match(swipeRe);
  if (swipeMatch) {
    const durMatch = line.match(/(?:\b|(?<=\s|^))(?:duration|时长)[=:](\d+(\.\d+)?)/i);
    return {
      type: 'swipe',
      params: {
        start_x: parseInt(swipeMatch[1]),
        start_y: parseInt(swipeMatch[2]),
        end_x: parseInt(swipeMatch[3]),
        end_y: parseInt(swipeMatch[4]),
        duration: durMatch ? parseFloat(durMatch[1]) : 0.5,
        delay_after: 0.3,
      },
    };
  }

  // screenshot / 截图
  if (/^(?:screenshot|截图)(?:\b|$)/i.test(line)) {
    return { type: 'screenshot', params: {} };
  }

  // click_text "text" / 文字点击 "text"
  const clickTextRe = /^(?:click_text|文字点击)\s+"([^"]*)"(?:\s+(?:similarity|相似度)[=:](\d+))?\s*(.*)?/i;
  const clickTextMatch = line.match(clickTextRe);
  if (clickTextMatch) {
    return {
      type: 'mouse_operation',
      params: {
        mode: 'text',
        text: clickTextMatch[1],
        similarity: clickTextMatch[2] ? parseInt(clickTextMatch[2]) : 85,
        method: 'single',
        offset_x: 0,
        offset_y: 0,
        timeout: 5,
        delay_after: 0.5,
      },
    };
  }

  // popup_close / 关闭弹窗
  if (/^(?:popup_close|关闭弹窗)(?:\b|$)/i.test(line)) {
    const strategyMatch = line.match(/(?:strategy|策略)[=:]\s*(\w+)/i);
    return {
      type: 'popup_close',
      params: {
        strategies: strategyMatch ? [strategyMatch[1]] : ['auto'],
        timeout: 5,
        delay_after: 0.3,
      },
    };
  }

  // scene_condition "name" / 场景匹配 "name" / 场景识别 "name"
  const sceneCondLineRe = /^(?:scene_condition|场景匹配|场景识别)\s+"([^"]*)"(?:\s+(?:similarity|相似度)[=:](\d+))?\s*(.*)?/i;
  const sceneCondLineMatch = line.match(sceneCondLineRe);
  if (sceneCondLineMatch) {
    return {
      type: 'scene_condition',
      params: {
        scene_name: sceneCondLineMatch[1],
        similarity: sceneCondLineMatch[2] ? parseInt(sceneCondLineMatch[2]) : 70,
      },
    };
  }

  // $name = "value" (variable_set)
  const varSetRe = /^\$(\w+)\s*=\s*"([^"]*)"/;
  const varSetMatch = line.match(varSetRe);
  if (varSetMatch) {
    return {
      type: 'variable_set',
      params: { name: varSetMatch[1], value: varSetMatch[2] },
    };
  }

  // $name (variable_get)
  const varGetRe = /^\$(\w+)\s*$/;
  const varGetMatch = line.match(varGetRe);
  if (varGetMatch) {
    return {
      type: 'variable_get',
      params: { name: varGetMatch[1] },
    };
  }

  // run "name" / 运行 "name"
  const runRe = /^(?:run|运行)\s+"([^"]*)"/i;
  const runMatch = line.match(runRe);
  if (runMatch) {
    return {
      type: 'sub_script',
      params: { script_name: runMatch[1], script_id: runMatch[1] },
    };
  }

  // if COND: / 如果 COND:
  const ifRe = /^(?:if|如果)\s+(.+):\s*$/i;
  const ifMatch = line.match(ifRe);
  if (ifMatch) {
    const condStr = ifMatch[1].trim();
    const condition = parseConditionExpr(condStr);
    return { type: 'condition', params: condition, isElse: false };
  }

  // else: / 否则:
  if (/^(?:else|否则)\s*:\s*$/i.test(line)) {
    return { type: 'else', params: {}, isElse: true };
  }

  // loop N: / 循环 N:
  const loopCountRe = /^(?:loop|循环)\s+(\d+)\s*:\s*$/i;
  const loopCountMatch = line.match(loopCountRe);
  if (loopCountMatch) {
    return {
      type: 'loop',
      params: { loop_type: 'count', count: parseInt(loopCountMatch[1]) },
    };
  }

  // loop infinite: / 无限循环:
  if (/^(?:loop\s+infinite|无限循环)\s*:\s*$/i.test(line)) {
    return { type: 'loop', params: { loop_type: 'infinite', count: 0 } };
  }

  // loop while COND: / 条件循环 COND:
  const loopWhileRe = /^(?:loop\s+while|条件循环)\s+(.+):\s*$/i;
  const loopWhileMatch = line.match(loopWhileRe);
  if (loopWhileMatch) {
    const condStr = loopWhileMatch[1].trim();
    const condition = parseConditionExpr(condStr);
    return {
      type: 'loop',
      params: { loop_type: 'condition', condition },
    };
  }

  // ── 启动 "path" / launch "path" → process_action ──
  const launchRe = /^(?:启动|launch)\s+"([^"]*)"/i;
  const launchMatch = line.match(launchRe);
  if (launchMatch) {
    return {
      type: 'process_action',
      params: { action: 'launch', target: launchMatch[1] },
    };
  }

  // ── 激活 "title" / focus "title" → process_action ──
  const focusRe = /^(?:激活|聚焦|focus)\s+"([^"]*)"/i;
  const focusMatch = line.match(focusRe);
  if (focusMatch) {
    return {
      type: 'process_action',
      params: { action: 'focus', title: focusMatch[1] },
    };
  }

  // ── 终止 "name" / kill "name" → process_action ──
  const killRe = /^(?:终止|kill)\s+"([^"]*)"/i;
  const killMatch = line.match(killRe);
  if (killMatch) {
    return {
      type: 'process_action',
      params: { action: 'kill', name: killMatch[1] },
    };
  }

  // ── 右键点击 (x,y) / right-click (x,y) → click method=right ──
  const rightClickRe = /^(?:右键点击|right-click)\s+\((-?\d+)\s*,\s*(-?\d+)\)/i;
  const rightClickMatch = line.match(rightClickRe);
  if (rightClickMatch) {
    return {
      type: 'mouse_operation',
      params: { mode: 'coordinate', x: parseInt(rightClickMatch[1]), y: parseInt(rightClickMatch[2]), method: 'right', delay_after: 0.5 },
    };
  }

  // ── 双击 (x,y) / double-click (x,y) → click method=double ──
  const doubleClickRe = /^(?:双击|double-click)\s+\((-?\d+)\s*,\s*(-?\d+)\)/i;
  const doubleClickMatch = line.match(doubleClickRe);
  if (doubleClickMatch) {
    return {
      type: 'mouse_operation',
      params: { mode: 'coordinate', x: parseInt(doubleClickMatch[1]), y: parseInt(doubleClickMatch[2]), method: 'double', delay_after: 0.5 },
    };
  }

  // ── 等待进程 "name" [超时=N] / wait_process ──
  const waitProcessRe = /^(?:等待进程|wait_process|wait\s+process)\s+"([^"]*)"(?:\s+(?:timeout|超时)[=:](\d+(\.\d+)?))?\s*/i;
  const waitProcessMatch = line.match(waitProcessRe);
  if (waitProcessMatch) {
    return {
      type: 'wait',
      params: { wait_type: 'process', process_name: waitProcessMatch[1], timeout: waitProcessMatch[2] ? parseFloat(waitProcessMatch[2]) : 30 },
    };
  }

  // ── 等待退出 "name" [超时=N] / wait_exit ──
  const waitExitRe = /^(?:等待退出|wait_exit|wait\s+exit)\s+"([^"]*)"(?:\s+(?:timeout|超时)[=:](\d+(\.\d+)?))?\s*/i;
  const waitExitMatch = line.match(waitExitRe);
  if (waitExitMatch) {
    return {
      type: 'wait',
      params: { wait_type: 'process_exit', process_name: waitExitMatch[1], timeout: waitExitMatch[2] ? parseFloat(waitExitMatch[2]) : 30 },
    };
  }

  // ── 等待屏幕变化/静止 [超时=N] / wait_screen ──
  const waitScreenRe = /^(?:等待屏幕|wait_screen)\s*(变化|change|静止|stable)?(?:\s+(?:timeout|超时)[=:](\d+(\.\d+)?))?\s*/i;
  const waitScreenMatch = line.match(waitScreenRe);
  if (waitScreenMatch) {
    const screenType = (waitScreenMatch[1] || 'change').toLowerCase();
    return {
      type: 'wait',
      params: { wait_type: screenType === '静止' || screenType === 'stable' ? 'screen_stable' : 'screen_change', timeout: waitScreenMatch[2] ? parseFloat(waitScreenMatch[2]) : 10 },
    };
  }

  // ── 剪贴板写入 "text" / clipboard_set / CLIPBOARD_SET ──
  const clipWriteRe = /^(?:剪贴板写入|clipboard_set|CLIPBOARD_SET)\s+"([^"]*)"/i;
  const clipWriteMatch = line.match(clipWriteRe);
  if (clipWriteMatch) {
    return {
      type: 'clipboard_action',
      params: { action: 'set', text: clipWriteMatch[1] },
    };
  }

  // ── 读取剪贴板 / clipboard_get / CLIPBOARD_GET ──
  if (/^(?:读取剪贴板|clipboard_get|CLIPBOARD_GET)\s*/i.test(line)) {
    return {
      type: 'clipboard_action',
      params: { action: 'get' },
    };
  }

  // ── 获取剪贴板图片 / clipboard_get_image / CLIPBOARD_GET_IMAGE ──
  const clipImgRe = /^(?:获取剪贴板图片|clipboard_get_image|CLIPBOARD_GET_IMAGE)\s*/i;
  if (clipImgRe.test(line)) {
    return {
      type: 'clipboard_action',
      params: { action: 'get_image', format_: 'base64', save_path: '' },
    };
  }

  // ── 获取剪贴板文件 / clipboard_get_files / CLIPBOARD_GET_FILES ──
  if (/^(?:获取剪贴板文件|clipboard_get_files|CLIPBOARD_GET_FILES)\s*/i.test(line)) {
    return {
      type: 'clipboard_action',
      params: { action: 'get_files' },
    };
  }

  // ── 清空剪贴板 / clipboard_clear / CLIPBOARD_CLEAR ──
  if (/^(?:清空剪贴板|clipboard_clear|CLIPBOARD_CLEAR)\s*/i.test(line)) {
    return {
      type: 'clipboard_action',
      params: { action: 'clear' },
    };
  }

  // ── 粘贴输入 / paste_input / TYPE_CLIP → keyboard ctrl+v ──
  if (/^(?:粘贴输入|paste_input|TYPE_CLIP)\s*/i.test(line)) {
    return {
      type: 'keyboard',
      params: { keys: ['v'], modifiers: ['ctrl'], delay_after: 0.3 },
    };
  }

  // ── 颜色检测 (x,y,w,h) 颜色 [容差] ──
  const colorCheckRe = /^(?:颜色检测|color_check|COLOR_CHECK)\s+\((\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\)\s+"([^"]*)"(?:\s+(?:tolerance|容差)[=:](\d+))?\s*/i;
  const colorCheckMatch = line.match(colorCheckRe);
  if (colorCheckMatch) {
    return {
      type: 'color_check',
      params: {
        x: parseInt(colorCheckMatch[1]), y: parseInt(colorCheckMatch[2]),
        w: parseInt(colorCheckMatch[3]), h: parseInt(colorCheckMatch[4]),
        expected_color: colorCheckMatch[5],
        tolerance: colorCheckMatch[6] ? parseInt(colorCheckMatch[6]) : 30,
      },
    };
  }

  // ── 系统命令（通过 tool_call 转发到 @feature） ──
  // ── 关机/重启/注销/休眠 (simple keywords → system_power) ──
  const powerSimpleRe = /^(关机|重启|注销|休眠|shutdown|reboot|logoff|hibernate)\s*/i;
  const powerSimpleMatch = line.match(powerSimpleRe);
  if (powerSimpleMatch) {
    const pm: Record<string, string> = { '关机':'shutdown','重启':'reboot','注销':'logoff','休眠':'hibernate','shutdown':'shutdown','reboot':'reboot','logoff':'logoff','hibernate':'hibernate' };
    const delayMatch = line.match(/(?:delay|延迟)[=:](\d+)/i);
    return { type: 'system_power', params: { action: pm[powerSimpleMatch[1].toLowerCase()]||'shutdown', delay_seconds: delayMatch ? parseInt(delayMatch[1]) : 0, confirm: true } };
  }

  // 音量 N / volume N
  const volRe = /^(?:音量|volume)\s+(\d+)\s*/i;
  const volMatch = line.match(volRe);
  if (volMatch) {
    return { type: 'audio_action', params: { action: 'volume_set', level: parseInt(volMatch[1]) } };
  }

  // 静音 [on/off] / mute
  if (/^(?:静音|mute)\s*/i.test(line)) {
    const muteOn = /开启|on/i.test(line);
    const muteOff = /关闭|off/i.test(line);
    return { type: 'audio_action', params: { action: 'volume_mute', mute: muteOn ? true : muteOff ? false : undefined } };
  }

  // 锁定 / lock
  if (/^(?:锁定|lock)\s*/i.test(line)) {
    return { type: 'system_power', params: { action: 'lock_workstation', confirm: true } };
  }

  // 睡眠 / sleep
  if (/^(?:睡眠|sleep)\s*/i.test(line)) {
    return { type: 'system_power', params: { action: 'system_sleep', confirm: true } };
  }

  // 桌面 / desktop (Win+D) — bare keyword only
  if (/^(?:桌面|desktop)\s*$/i.test(line)) {
    return { type: 'desktop_action', params: { action: 'show_desktop' } };
  }

  // 电池 / battery
  if (/^(?:电池|battery)\s*/i.test(line)) {
    return { type: 'battery_status', params: {} };
  }

  // 清空回收站 / empty recycle bin
  if (/^(?:清空回收站|empty_recycle_bin|回收站)\s*/i.test(line)) {
    return { type: 'desktop_action', params: { action: 'empty_recycle_bin', confirm: true } };
  }

  // 亮度 N / brightness N
  const brightRe = /^(?:亮度|brightness)\s+(\d+)\s*/i;
  const brightMatch = line.match(brightRe);
  if (brightMatch) {
    return { type: 'display_brightness', params: { level: parseInt(brightMatch[1]) } };
  }

  // 关闭显示器 / display off
  if (/^(?:关闭显示器|display_off)\s*/i.test(line)) {
    return { type: 'system_power', params: { action: 'display_off', confirm: true } };
  }

  // 壁纸 "path" / wallpaper / set_wallpaper
  const wallRe = /^(?:壁纸|wallpaper|set_wallpaper)\s+"([^"]*)"/i;
  const wallMatch = line.match(wallRe);
  if (wallMatch) {
    return { type: 'desktop_action', params: { action: 'set_wallpaper', path: wallMatch[1] } };
  }

  // ── 浏览器操作: 浏览器导航 "URL" / BROWSER_NAV URL ──
  const browserNavRe = /^(?:浏览器导航|BROWSER_NAV)\s+"?((?:https?:\/\/)?[^\s"]+)/i;
  const browserNavMatch = line.match(browserNavRe);
  if (browserNavMatch) {
    return {
      type: 'web_browser',
      params: { action: 'navigate', url: browserNavMatch[1], headless: false, timeout: 30 },
    };
  }

  // ── 浏览器操作: 浏览器点击 "selector" / BROWSER_CLICK selector ──
  const browserClickRe = /^(?:浏览器点击|BROWSER_CLICK)\s+"?([^\s"]+)/i;
  const browserClickMatch = line.match(browserClickRe);
  if (browserClickMatch) {
    return {
      type: 'web_browser',
      params: { action: 'click', selector: browserClickMatch[1], headless: false, timeout: 30 },
    };
  }

  // ── 浏览器操作: 浏览器输入 "selector" "text" / BROWSER_TYPE selector "text" ──
  const browserTypeRe = /^(?:浏览器输入|BROWSER_TYPE)\s+"?([^\s"]+)"?\s+"([^"]*)"/i;
  const browserTypeMatch = line.match(browserTypeRe);
  if (browserTypeMatch) {
    return {
      type: 'web_browser',
      params: { action: 'input_text', selector: browserTypeMatch[1], text: browserTypeMatch[2], headless: false, timeout: 30 },
    };
  }

  // ── 浏览器操作: 浏览器等待 "selector" [timeout] / BROWSER_WAIT selector [timeout] ──
  const browserWaitRe = /^(?:浏览器等待|BROWSER_WAIT)\s+"?([^\s"]+)"?(?:\s+(\d+))?/i;
  const browserWaitMatch = line.match(browserWaitRe);
  if (browserWaitMatch) {
    return {
      type: 'web_browser',
      params: { action: 'wait', selector: browserWaitMatch[1], wait_seconds: browserWaitMatch[2] ? parseInt(browserWaitMatch[2]) : 5, headless: false, timeout: 30 },
    };
  }

  // ── 浏览器操作: 浏览器获取文本 "selector" / BROWSER_TEXT selector ──
  const browserTextRe = /^(?:浏览器获取文本|BROWSER_TEXT)\s+"?([^\s"]+)/i;
  const browserTextMatch = line.match(browserTextRe);
  if (browserTextMatch) {
    return {
      type: 'web_browser',
      params: { action: 'extract', selector: browserTextMatch[1], extract_attr: 'textContent', headless: false, timeout: 30 },
    };
  }

  // ── 浏览器操作: 浏览器查找 "selector" / BROWSER_FIND selector ──
  const browserFindRe = /^(?:浏览器查找|BROWSER_FIND)\s+"?([^\s"]+)/i;
  const browserFindMatch = line.match(browserFindRe);
  if (browserFindMatch) {
    return {
      type: 'web_browser',
      params: { action: 'wait', selector: browserFindMatch[1], wait_seconds: 1, headless: false, timeout: 10 },
    };
  }

  // ── 浏览器操作: 浏览器截图 / BROWSER_SCREENSHOT ──
  if (/^(?:浏览器截图|BROWSER_SCREENSHOT)\s*$/i.test(line)) {
    return {
      type: 'web_browser',
      params: { action: 'screenshot', headless: false, timeout: 30 },
    };
  }

  // ── 浏览器操作: 浏览器执行JS "code" / BROWSER_JS code ──
  const browserJsRe = /^(?:浏览器执行JS|BROWSER_JS)\s+"?(.+)/i;
  const browserJsMatch = line.match(browserJsRe);
  if (browserJsMatch) {
    return {
      type: 'tool_call',
      params: { feature_name: 'browser_execute_js', params: { code: browserJsMatch[1] } },
    };
  }

  // ── Consolidated parameterized actions ──
  // 系统/SYSTEM power=shutdown|reboot|... [delay=N]
  const systemPowerRe = /^(?:系统|SYSTEM)\s+(?:操作=)?(?:power=)?(关机|重启|注销|休眠|锁定|睡眠|关闭显示器|shutdown|reboot|logoff|hibernate|lock|sleep|display_off)\s*(.*)/i;
  const spMatch = line.match(systemPowerRe);
  if (spMatch) {
    const action = spMatch[1].toLowerCase();
    const delayMatch = line.match(/(?:delay|延迟)[=:](\d+)/i);
    const map: Record<string, string> = {
      '关机':'shutdown','重启':'reboot','注销':'logoff','休眠':'hibernate',
      '锁定':'lock_workstation','睡眠':'system_sleep','关闭显示器':'display_off',
      'shutdown':'shutdown','reboot':'reboot','logoff':'logoff',
      'hibernate':'hibernate','lock':'lock_workstation','sleep':'system_sleep','display_off':'display_off',
    };
    return { type: 'system_power', params: { action: map[action]||'shutdown', delay_seconds: delayMatch ? parseInt(delayMatch[1]) : 0, confirm: true } };
  }

  // 音频/AUDIO volume=N | mute
  const audioVolRe = /^(?:音频|AUDIO)\s+(?:操作=)?(?:音量|volume)[=:](\d+)/i;
  const audioVolMatch = line.match(audioVolRe);
  if (audioVolMatch) return { type: 'audio_action', params: { action: 'volume_set', level: parseInt(audioVolMatch[1]) } };
  const audioMuteRe = /^(?:音频|AUDIO)\s+(?:操作=)?(?:静音|mute)(?:=(开启|on|关闭|off))?/i;
  const audioMuteMatch = line.match(audioMuteRe);
  if (audioMuteMatch) {
    const v = audioMuteMatch[1];
    return { type: 'audio_action', params: { action: 'volume_mute', mute: v ? /(开启|on)/i.test(v) : undefined } };
  }

  // 桌面/DESKTOP action=show|wallpaper|battery|empty_bin
  const desktopRe = /^(?:桌面|DESKTOP)\s+(?:操作=)?(?:action=)?(桌面|显示桌面|壁纸|电池|清空回收站|show|wallpaper|battery|empty_bin)\s*(.*)/i;
  const desktopMatch = line.match(desktopRe);
  if (desktopMatch) {
    const a = desktopMatch[1].toLowerCase();
    if (/壁纸|wallpaper/.test(a)) {
      const p = desktopMatch[2].match(/"([^"]*)"/);
      return { type: 'desktop_action', params: { action: 'set_wallpaper', path: p ? p[1] : '' } };
    }
    if (/电池|battery/.test(a)) return { type: 'battery_status', params: {} };
    if (/清空|empty_bin/.test(a)) return { type: 'desktop_action', params: { action: 'empty_recycle_bin', confirm: true } };
    return { type: 'desktop_action', params: { action: 'show_desktop' } };
  }

  // 窗口/WINDOW action=minimize|maximize|restore|close|resize|snap [title] [width=N height=N]
  const windowOpRe = /^(?:窗口|WINDOW)\s+(?:操作=)?(?:action=)?(最小化|最大化|还原|关闭|调整大小|贴靠|minimize|maximize|restore|close|resize|snap)\s*(.*)/i;
  const windowOpMatch = line.match(windowOpRe);
  if (windowOpMatch) {
    const a = windowOpMatch[1].toLowerCase();
    const rest = windowOpMatch[2];
    const tm = rest.match(/"([^"]*)"/);
    const wm = rest.match(/(?:width|宽)[=:](\d+)/i);
    const hm = rest.match(/(?:height|高)[=:](\d+)/i);
    const sm = rest.match(/(?:snap_position|位置)[=:](left|right|top|bottom|top_left|top_right|bottom_left|bottom_right|左|右|上|下|左上|右上|左下|右下)/i);
    const opMap: Record<string, string> = {
      '最小化':'minimize','最大化':'maximize','还原':'restore','关闭':'close','调整大小':'resize','贴靠':'snap',
      'minimize':'minimize','maximize':'maximize','restore':'restore','close':'close','resize':'resize','snap':'snap',
    };
    const op = opMap[a] || 'minimize';
    const snapPosMap: Record<string, string> = { '左':'left','右':'right','上':'top','下':'bottom','左上':'top_left','右上':'top_right','左下':'bottom_left','右下':'bottom_right' };
    const sp = sm ? (snapPosMap[sm[1]] || sm[1]) : 'left';
    return { type: 'window_control', params: { operation: op, title: tm ? tm[1] : '', width: wm ? parseInt(wm[1]) : 800, height: hm ? parseInt(hm[1]) : 600, snap_position: sp } };
  }

  // 图片/IMAGE action=find|click|capture "path" [similarity=N]
  const imageOpRe = /^(?:图片|IMAGE)\s+(?:操作=)?(?:action=)?(查找|点击|捕获|find|click|capture)\s+"([^"]*)"(?:\s+(?:similarity|相似度)[=:](\d+))?/i;
  const imageOpMatch = line.match(imageOpRe);
  if (imageOpMatch) {
    const action = imageOpMatch[1].toLowerCase();
    const path = imageOpMatch[2];
    const sim = imageOpMatch[3] ? parseInt(imageOpMatch[3]) : 90;
    if (/点击|click/.test(action)) return { type: 'mouse_operation', params: { mode: 'image', targets: [{ path }], similarity: sim, timeout: 5, method: 'single', delay_after: 0.5 } };
    return { type: 'tool_call', params: { feature_name: /查找|find/.test(action) ? 'find_image' : 'template_capture', params: { path, similarity: sim } } };
  }

  // OCR action=find|extract|click "text" [similarity=N]
  const ocrOpRe = /^(?:OCR)\s+(?:操作=)?(?:action=)?(查找|提取|点击|find|extract|click)\s+"([^"]*)"(?:\s+(?:similarity|相似度)[=:](\d+))?/i;
  const ocrOpMatch = line.match(ocrOpRe);
  if (ocrOpMatch) {
    const action = ocrOpMatch[1].toLowerCase();
    const text = ocrOpMatch[2];
    const sim = ocrOpMatch[3] ? parseInt(ocrOpMatch[3]) : 80;
    if (/点击|click/.test(action)) return { type: 'mouse_operation', params: { mode: 'text', text, similarity: sim, method: 'single', delay_after: 0.5 } };
    return { type: 'tool_call', params: { feature_name: /查找|find/.test(action) ? 'ocr_find_text' : 'ocr_extract_all', params: { text, similarity: sim } } };
  }

  // VLM action=analyze|condition "prompt"
  const vlmOpRe = /^(?:VLM)\s+(?:操作=)?(?:action=)?(分析|判断|analyze|condition)\s+"([^"]*)"/i;
  const vlmOpMatch = line.match(vlmOpRe);
  if (vlmOpMatch) {
    const action = vlmOpMatch[1].toLowerCase();
    const prompt = vlmOpMatch[2];
    return { type: 'tool_call', params: { feature_name: /分析|analyze/.test(action) ? 'vlm_analyze' : 'vlm_condition', params: { prompt } } };
  }

  // 剪贴板/CLIPBOARD action=set|get|get_image|get_files|clear "text"
  const clipOpRe = /^(?:剪贴板|CLIPBOARD)\s+(?:操作=)?(?:action=)?(写入|读取|set|get|get_image|get_files|clear|获取图片|获取文件|清空)\s*(.*)/i;
  const clipOpMatch = line.match(clipOpRe);
  if (clipOpMatch) {
    const a = clipOpMatch[1].toLowerCase();
    if (/写入|set/.test(a)) {
      const tm = clipOpMatch[2].match(/"([^"]*)"/);
      return { type: 'clipboard_action', params: { action: 'set', text: tm ? tm[1] : clipOpMatch[2].trim() } };
    }
    const subMap: Record<string, string> = { 'get_image':'get_image', '获取图片':'get_image', 'get_files':'get_files', '获取文件':'get_files', 'clear':'clear', '清空':'clear' };
    return { type: 'clipboard_action', params: { action: subMap[a] || 'get' } };
  }

  // ── Catch-all for ANY unknown DSL keyword (ALL_CAPS) → tool_call ──
  // Matches patterns like: VOLUME_SET level=50
  const dslCatchRe = /^([A-Z][A-Z_]{2,})\s+(.+)$/;
  const dslCatchMatch = line.match(dslCatchRe);
  if (dslCatchMatch) {
    const keyword = dslCatchMatch[1];
    const rest = dslCatchMatch[2].trim();
    // Parse key=value pairs
    const params: Record<string, any> = {};
    const pairRe = /(\w+)=("[^"]*"|\S+)/g;
    let pairMatch;
    while ((pairMatch = pairRe.exec(rest)) !== null) {
      let val: any = pairMatch[2];
      if (val.startsWith('"') && val.endsWith('"')) {
        val = val.slice(1, -1);
      } else if (/^\d+$/.test(val)) {
        val = parseInt(val, 10);
      } else if (/^\d+\.\d+$/.test(val)) {
        val = parseFloat(val);
      }
      params[pairMatch[1]] = val;
    }
    return {
      type: 'tool_call',
      params: {
        feature_name: keyword.toLowerCase(),
        params,
      },
    };
  }

  return null;
}

function parseConditionExpr(expr: string): any {
  // true / 真
  if (/^(?:true|真)$/i.test(expr)) {
    return { condition_type: 'always_true' };
  }

  // image_exists "path" / 图片存在 "path"
  const imgExistsRe = /^(?:image_exists|图片存在)\s+"([^"]*)"(?:\s+(?:similarity|相似度)[=:](\d+))?\s*$/i;
  const imgMatch = expr.match(imgExistsRe);
  if (imgMatch) {
    return {
      condition_type: 'image_exists',
      template_path: imgMatch[1],
      similarity: imgMatch[2] ? parseInt(imgMatch[2]) : 85,
    };
  }

  // window_exists "title" / 窗口存在 "title"
  const winExistsRe = /^(?:window_exists|窗口存在)\s+"([^"]*)"\s*$/i;
  const winMatch = expr.match(winExistsRe);
  if (winMatch) {
    return {
      condition_type: 'window_exists',
      window_title: winMatch[1],
    };
  }

  // var_eq name "value" / 变量等于 name "value"
  const varEqRe = /^(?:var_eq|变量等于)\s+(\w+)\s+"([^"]*)"\s*$/i;
  const varEqMatch = expr.match(varEqRe);
  if (varEqMatch) {
    return {
      condition_type: 'variable_equals',
      variable_name: varEqMatch[1],
      variable_value: varEqMatch[2],
    };
  }

  // var_gt name value / 变量大于 name value
  const varGtRe = /^(?:var_gt|变量大于)\s+(\w+)\s+(\S+)\s*$/i;
  const varGtMatch = expr.match(varGtRe);
  if (varGtMatch) {
    return {
      condition_type: 'variable_greater',
      variable_name: varGtMatch[1],
      variable_value: varGtMatch[2],
    };
  }

  // var_lt name value / 变量小于 name value
  const varLtRe = /^(?:var_lt|变量小于)\s+(\w+)\s+(\S+)\s*$/i;
  const varLtMatch = expr.match(varLtRe);
  if (varLtMatch) {
    return {
      condition_type: 'variable_less',
      variable_name: varLtMatch[1],
      variable_value: varLtMatch[2],
    };
  }

  // color_match (H,S,V) / 颜色匹配 (H,S,V)
  const colorCondRe = /^(?:color_match|颜色匹配)\s+\((\d+),(\d+),(\d+)x(\d+)\)\s+"([^"]*)"(?:\s+(?:tolerance|容差)[=:](\d+))?\s*$/i;
  const colorCondMatch = expr.match(colorCondRe);
  if (colorCondMatch) {
    return {
      condition_type: 'color_match',
      x: parseInt(colorCondMatch[1]),
      y: parseInt(colorCondMatch[2]),
      w: parseInt(colorCondMatch[3]),
      h: parseInt(colorCondMatch[4]),
      expected_color: colorCondMatch[5],
      tolerance: colorCondMatch[6] ? parseInt(colorCondMatch[6]) : 30,
    };
  }

  // scene_match "name" / 场景匹配 "name"
  const sceneCondRe = /^(?:scene_match|场景匹配)\s+"([^"]*)"(?:\s+(?:similarity|相似度)[=:](\d+))?\s*$/i;
  const sceneCondMatch = expr.match(sceneCondRe);
  if (sceneCondMatch) {
    return {
      condition_type: 'scene_match',
      scene_name: sceneCondMatch[1],
      similarity: sceneCondMatch[2] ? parseInt(sceneCondMatch[2]) : 70,
    };
  }

  // Fallback
  return { condition_type: 'always_true' };
}

// ─── Build tree from indentation ───

function buildTree(lines: ParseLine[]): ParsedStmt[] {
  const root: ParsedStmt[] = [];
  const stack: { indent: number; stmt: ParsedStmt }[] = [];
  let lastIfStmt: ParsedStmt | null = null;

  for (const line of lines) {
    const result = parseStmt(line.text);
    if (!result) continue;

    const stmt: ParsedStmt = {
      type: result.type,
      params: result.params,
      indent: line.indent,
      children: [],
      isElse: result.isElse,
      isBreak: result.isBreak,
    };

    if (result.isBreak) {
      // Attach break to innermost loop
      for (let i = stack.length - 1; i >= 0; i--) {
        if (stack[i].stmt.type === 'loop') {
          stack[i].stmt.children.push(stmt);
          break;
        }
      }
      continue;
    }

    // else: add marker but keep the condition on the stack for else-body children
    if (result.type === 'else') {
      if (lastIfStmt) {
        lastIfStmt.isElse = true;
        lastIfStmt.children.push({
          type: '_else',
          params: {},
          indent: line.indent,
          children: [],
          isElse: true,
        });
      }
      continue;
    }

    // Pop stack until we find a parent at our indent level
    while (stack.length > 0 && stack[stack.length - 1].indent >= line.indent) {
      if (stack[stack.length - 1].stmt.type === 'condition' || stack[stack.length - 1].stmt.type === 'scene_condition') {
        lastIfStmt = null;
      }
      stack.pop();
    }

    if (stack.length === 0) {
      root.push(stmt);
    } else {
      stack[stack.length - 1].stmt.children.push(stmt);
    }

    // Push control flow statements onto the stack
    if (stmt.type === 'condition' || stmt.type === 'scene_condition' || stmt.type === 'loop') {
      stack.push({ indent: line.indent, stmt });
      if (stmt.type === 'condition' || stmt.type === 'scene_condition') {
        lastIfStmt = stmt;
      }
    }
  }

  return root;
}

// ─── Build FlowScript from tree ───

const DEFAULT_SETTINGS = {
  default_delay: 0.5,
  error_handling: 'stop' as const,
  max_retries: 0,
  retry_delay: 1.0,
  log_level: 'info' as const,
};

function getDefaultParams(type: string): any {
  switch (type) {
    case 'click': return { x: 0, y: 0, method: 'single', delay_after: 0.5 };
    case 'image_click': return { targets: [], similarity: 90, timeout: 5, method: 'single', offset_x: 0, offset_y: 0, delay_after: 0.5, multi_scale: false, match_all: false };
    case 'click_text': return { text: '', similarity: 80, method: 'single', offset_x: 0, offset_y: 0, timeout: 5, delay_after: 0.5 };
    case 'click_action': return { mode: 'coordinate', x: 0, y: 0, method: 'single', delay_after: 0.5, targets: [], text: '', similarity: 90, offset_x: 0, offset_y: 0, timeout: 5 };
    case 'mouse_operation': return { sub_operation: 'click', mode: 'coordinate', x: 0, y: 0, method: 'single', delay_after: 0.5, targets: [], text: '', similarity: 90, timeout: 5, scroll_direction: 'down', scroll_amount: 3, hover_x: 0, hover_y: 0, hover_duration: 0.5, move_x: 0, move_y: 0 };
    case 'wait': return { wait_type: 'time', duration: 1 };
    case 'input_text': return { text: '', click_before: false, clear_before: true, delay_after: 0.3 };
    case 'keyboard': return { keys: [], modifiers: [], delay_after: 0.2 };
    case 'swipe': return { start_x: 0, start_y: 0, end_x: 0, end_y: 0, duration: 0.5, delay_after: 0.3 };
    case 'condition': return { condition_type: 'always_true' };
    case 'loop': return { loop_type: 'count', count: 10 };
    case 'screenshot': return {};
    case 'sub_script': return { script_id: '', script_name: '' };
    case 'variable_set': return { name: '', value: '' };
    case 'variable_get': return { name: '' };
    case 'popup_close': return { strategies: ['auto'], timeout: 5, delay_after: 0.3 };
    case 'color_check': return { x: 0, y: 0, w: 100, h: 100, expected_color: '', tolerance: 30 };
    case 'progress_wait': return { wait_type: 'fill', timeout: 30, region: { x: 0, y: 0, w: 200, h: 20 }, poll_interval: 0.5 };
    case 'region_wait': return { region_type: 'stable', x: 0, y: 0, w: 200, h: 200, timeout: 10, poll_interval: 0.3 };
    case 'scene_condition': return { scene_name: '', similarity: 70 };
    case 'launch_program': return { target: '' };
    case 'focus_window': return { title: '' };
    case 'kill_process': return { name: '' };
    case 'clipboard_set': return { text: '' };
    case 'clipboard_get': return {};
    case 'volume_set': return { level: 50 };
    case 'volume_mute': return {};
    case 'lock_workstation': return {};
    case 'system_sleep': return {};
    case 'show_desktop': return {};
    case 'battery_status': return {};
    case 'empty_recycle_bin': return {};
    case 'display_brightness': return { level: 50 };
    case 'display_off': return {};
    case 'web_browser': return { action: 'navigate', url: '', selector: '', text: '', wait_seconds: 1, timeout: 30, headless: false };
    case 'set_wallpaper': return { path: '' };
    case 'system_power': return { action: 'shutdown', delay_seconds: 0, confirm: true };
    case 'audio_action': return { action: 'volume_set', level: 50 };
    case 'desktop_action': return { action: 'show_desktop' };
    case 'clipboard_action': return { action: 'set', text: '', format_: 'base64', save_path: '' };
    case 'window_control': return { operation: 'minimize', title: '', width: 800, height: 600, snap_position: 'left' };
    case 'dialog_action': return { action: 'handle', title: '', button: '', path: '', timeout: 10 };
    case 'a11y_action': return { action: 'get_tree', name: '', role: '', control_type: '', max_depth: 5, filter_visible: true };
    case 'wifi_action': return { action: 'list', ssid: '', password: '' };
    case 'process_action': return { action: 'launch', target: '', args: '', name: '', timeout: 30, title: '', force: false };
    default: return {};
  }
}

type NodeWithBranch = {
  id: string;
  type: string;
  hasElse?: boolean;
};

function buildFlowScript(stmts: ParsedStmt[]): { nodes: FlowNode[]; edges: FlowEdge[] } {
  const nodes: FlowNode[] = [];
  const edges: FlowEdge[] = [];
  const convergenceQueue: string[] = []; // nodes that need to connect to the next sibling
  const conditionFallthroughs: string[] = []; // condition IDs that need a `no → next` edge

  // Create start node
  const startId = nanoid();
  nodes.push({
    id: startId,
    type: 'start',
    x: 60,
    y: 60,
    params: {},
  });

  function createNode(stmt: ParsedStmt, x: number, y: number): string {
    const id = nanoid();
    const defaultParams = getDefaultParams(stmt.type);
    const mergedParams = { ...defaultParams, ...stmt.params };
    nodes.push({
      id,
      type: stmt.type as NodeType,
      x,
      y,
      params: mergedParams as NodeParams,
    });
    return id;
  }

  function connect(fromId: string, fromPort: string, toId: string) {
    edges.push({
      id: nanoid(),
      from_node: fromId,
      from_port: fromPort,
      to_node: toId,
      to_port: 'input',
    });
  }

  function flushConvergence(toId: string) {
    for (const cid of convergenceQueue) {
      connect(cid, 'output', toId);
    }
    convergenceQueue.length = 0;
    for (const cid of conditionFallthroughs) {
      connect(cid, 'no', toId);
    }
    conditionFallthroughs.length = 0;
  }

  let yOffset = 60;

  function processStmts(
    stmtList: ParsedStmt[],
    parentId: string,
    parentPort: string
  ): string | null {
    let prevId: string | null = parentId;
    let prevPort = parentPort;

    for (const stmt of stmtList) {
      if (stmt.type === '_else') continue;

      if (stmt.isBreak) {
        const endId = createNode({ ...stmt, type: 'end', params: {} }, 60, yOffset + 120);
        yOffset += 120;
        if (prevId) {
          connect(prevId, prevPort, endId);
        }
        return endId;
      }

      if (stmt.type === 'condition' || stmt.type === 'scene_condition') {
        const condId = createNode(stmt, 60, yOffset + 120);
        yOffset += 120;

        // Flush any pending convergence to the condition node
        flushConvergence(condId);
        if (prevId) {
          connect(prevId, prevPort, condId);
        }

        // Split children into yes (before _else marker) and no (after _else marker)
        const yesChildren: ParsedStmt[] = [];
        const noChildren: ParsedStmt[] = [];
        let inElse = false;
        for (const child of stmt.children) {
          if (child.type === '_else') { inElse = true; continue; }
          if (inElse) noChildren.push(child);
          else yesChildren.push(child);
        }

        let lastYesId: string | null = null;
        let lastNoId: string | null = null;

        if (yesChildren.length > 0) {
          lastYesId = processStmts(yesChildren, condId, 'yes');
        }
        if (noChildren.length > 0) {
          lastNoId = processStmts(noChildren, condId, 'no');
        }

        // If a branch has no children, create an end node
        if (yesChildren.length === 0) {
          const endId = createNode({ ...stmt, type: 'end', params: {} }, 60, yOffset + 120);
          yOffset += 120;
          connect(condId, 'yes', endId);
          lastYesId = endId;
        }
        if (noChildren.length === 0 && stmt.isElse) {
          const endId = createNode({ ...stmt, type: 'end', params: {} }, 60, yOffset + 120);
          yOffset += 120;
          connect(condId, 'no', endId);
          lastNoId = endId;
        }

        // Convergence: both branches lead to the next sibling
        if (lastYesId && lastNoId) {
          convergenceQueue.push(lastYesId);
          convergenceQueue.push(lastNoId);
          prevId = null;
          prevPort = 'output';
        } else if (lastYesId) {
          // No else: yes-branch converges with cond-no fallthrough
          convergenceQueue.push(lastYesId);
          conditionFallthroughs.push(condId);
          prevId = null;
          prevPort = 'output';
        } else if (lastNoId) {
          convergenceQueue.push(lastNoId);
          conditionFallthroughs.push(condId);
          prevId = null;
          prevPort = 'output';
        } else {
          prevId = condId;
          prevPort = 'output';
        }
        continue;
      }

      if (stmt.type === 'loop') {
        const loopId = createNode(stmt, 60, yOffset + 120);
        yOffset += 120;

        flushConvergence(loopId);
        if (prevId) {
          connect(prevId, prevPort, loopId);
        }

        // Process loop body
        if (stmt.children.length > 0) {
          processStmts(stmt.children, loopId, 'loop_body');
        }

        prevId = loopId;
        prevPort = 'loop_end';
        continue;
      }

      // Linear node
      const nodeId = createNode(stmt, 60, yOffset + 120);
      yOffset += 120;

      flushConvergence(nodeId);
      if (prevId) {
        connect(prevId, prevPort, nodeId);
      }

      prevId = nodeId;
      prevPort = 'output';
    }

    return prevId;
  }

  const lastId = processStmts(stmts, startId, 'output');

  // Create end node
  const endId = nanoid();
  nodes.push({
    id: endId,
    type: 'end',
    x: 60,
    y: yOffset + 120,
    params: {},
  });

  // Connect last main-flow node to end. Convergence queue handles branches.
  if (convergenceQueue.length > 0) {
    flushConvergence(endId);
  } else if (lastId) {
    connect(lastId, 'output', endId);
  }

  return { nodes, edges };
 }

// ─── Public API ───

export function parsePseudoToFlow(pseudo: string): FlowScript {
  const lines = parseIndentation(pseudo);
  const stmts = buildTree(lines);
  const { nodes, edges } = buildFlowScript(stmts);

  return {
    meta: {
      id: nanoid(),
      name: 'untitled',
      description: '从伪代码生成',
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
      version: '1.0.0',
    },
    nodes,
    edges,
    variables: {},
    settings: { ...DEFAULT_SETTINGS },
  };
}
