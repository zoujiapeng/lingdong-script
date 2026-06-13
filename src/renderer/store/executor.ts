import { FlowScript, FlowNode, FlowEdge, LogEntry } from '../types';

// ============================================================
// Browser-based Flow Executor (runs without Python)
// ============================================================

export interface ExecutionController {
  pause: () => void;
  resume: () => void;
  stop: () => void;
  stepNext: () => void;
}

interface ExecCallbacks {
  onNodeStatus: (nodeId: string, status: FlowNode['status'], error?: string) => void;
  onLog: (entry: Omit<LogEntry, 'timestamp'>) => void;
  onDone: (status: 'completed' | 'stopped' | 'error') => void;
  onCurrentNode: (nodeId: string | undefined) => void;
  onIteration?: (current: number, total: number, nodeId: string) => void;
  onPausedAtBreakpoint?: (nodeId: string) => void;
}

export function createFlowExecutor(
  script: FlowScript,
  callbacks: ExecCallbacks,
  scripts?: Record<string, FlowScript>,
  breakpoints?: string[],
  stepping?: boolean,
): ExecutionController {
  let _paused = false;
  let _stopped = false;
  let _stepping = stepping || false;
  let _breakpoints = new Set(breakpoints || []);
  const _resolve = { resume: null as (() => void) | null, step: null as (() => void) | null };
  let _subFlowDepth = 0;
  const MAX_SUB_FLOW_DEPTH = 10;

  let nodeMap = new Map<string, FlowNode>();
  script.nodes.forEach((n) => nodeMap.set(n.id, n));

  let edgesByFrom = new Map<string, FlowEdge[]>();
  script.edges.forEach((e) => {
    const arr = edgesByFrom.get(e.from_node) || [];
    arr.push(e);
    edgesByFrom.set(e.from_node, arr);
  });

  function getOutputEdges(nodeId: string, port: string): FlowEdge[] {
    return (edgesByFrom.get(nodeId) || []).filter(
      (e) => e.from_port === port || (port === 'output' && !e.from_port)
    );
  }

  function log(level: string, message: string, nodeId?: string) {
    callbacks.onLog({ level: level as any, message, node_id: nodeId });
  }

  async function delay(ms: number) {
    if (ms <= 0) return;
    const start = Date.now();
    while (Date.now() - start < ms) {
      if (_stopped) throw new Error('stopped');
      while (_paused) {
        await new Promise<void>((r) => { _resolve.resume = r; });
        _resolve.resume = null;
        if (_stopped) throw new Error('stopped');
      }
      await new Promise((r) => setTimeout(r, 30));
    }
  }

  async function checkPauseStop() {
    while (_paused && !_stopped) {
      await new Promise<void>((r) => { _resolve.resume = r; });
      _resolve.resume = null;
    }
    if (_stopped) throw new Error('stopped');
  }

  async function executeNode(nodeId: string): Promise<string> {
    const node = nodeMap.get(nodeId);
    if (!node) { log('error', `节点未找到: ${nodeId}`); return ''; }
    if (node.disabled) { log('info', `跳过已禁用节点: ${node.type}`, nodeId); return 'output'; }

    // Breakpoint check: pause before executing if node has a breakpoint
    if (_breakpoints.has(nodeId)) {
      log('info', `🔴 断点命中: ${node.type}`, nodeId);
      _paused = true;
      callbacks.onPausedAtBreakpoint?.(nodeId);
      while (_paused && !_stopped) {
        await new Promise<void>((r) => { _resolve.resume = r; });
        _resolve.resume = null;
      }
      if (_stopped) throw new Error('stopped');
    }

    callbacks.onCurrentNode(nodeId);
    callbacks.onNodeStatus(nodeId, 'running');
    const params = node.params as any;
    const settings = script.settings || {};

    try {
      let result: string;

      switch (node.type as string) {
        case 'start':
          result = 'output';
          break;

        case 'end':
          result = '';
          break;

        case 'click': {
          log('info', `点击: (${params.x}, ${params.y}) [${params.method}]`, nodeId);
          await delay(200);
          await delay((params.delay_after || 0.5) * 1000);
          result = 'output';
          break;
        }

        case 'click_text': {
          log('info', `OCR点击文本: "${(params.text || '').substring(0, 30)}"`, nodeId);
          await delay(300);
          await delay((params.delay_after || 0.5) * 1000);
          result = 'output';
          break;
        }

        case 'popup_close': {
          log('info', `关闭弹窗 (策略: ${params.strategies?.[0] || 'auto'})`, nodeId);
          await delay(500);
          await delay((params.delay_after || 0.3) * 1000);
          result = 'output';
          break;
        }

        case 'color_check': {
          log('info', `检测颜色: ${params.expected_color || '未设置'}`, nodeId);
          await delay(200);
          // Simulate: always matches for demo
          result = 'output';
          break;
        }

        case 'progress_wait': {
          log('info', `等待进度条 (${params.wait_type || 'fill'})`, nodeId);
          await delay(500);
          result = 'output';
          break;
        }

        case 'region_wait': {
          log('info', `等待区域${params.region_type === 'stable' ? '稳定' : '变化'}`, nodeId);
          await delay(500);
          result = 'output';
          break;
        }

        case 'scene_condition': {
          const sceneName = params.scene_name || '未知';
          log('info', `场景识别: "${sceneName}"（模拟: 不匹配）`, nodeId);
          await delay(300);
          result = 'no';
          break;
        }

        case 'image_click': {
          const target = (params.targets || [])[0];
          const fname = target ? target.path.split(/[\\/]/).pop() : '未知';
          log('info', `检测图片: ${fname}`, nodeId);
          await delay(200);

          // Simulate — all images "not found" so demo cycles through all priorities
          // Last one in chain succeeds to show a click happening
          const failEdges = getOutputEdges(nodeId, 'fail');
          const isLast = failEdges.length === 0;

          if (isLast) {
            log('info', `✅ 匹配成功: ${fname}，执行单击`, nodeId);
            await delay(200);
            await delay((params.delay_after || 0.3) * 1000);
            result = 'output';
          } else {
            log('info', `⏳ 未匹配到 ${fname}，切换下一张`, nodeId);
            await delay(100);
            result = 'fail';
          }
          break;
        }

        case 'wait': {
          if (params.wait_type === 'time') {
            const dur = params.duration || 1;
            log('info', `等待 ${dur} 秒`, nodeId);
            await delay(Math.min(dur, 10) * 1000); // cap at 10s for demo (no cap in Python engine)
          } else {
            log('info', `等待条件（模拟跳过）`, nodeId);
            await delay(200);
          }
          result = 'output';
          break;
        }

        case 'input_text': {
          log('info', `输入文本: "${(params.text || '').substring(0, 30)}"`, nodeId);
          await delay(400);
          await delay((params.delay_after || 0.3) * 1000);
          result = 'output';
          break;
        }

        case 'keyboard': {
          log('info', `按键: ${[...(params.modifiers || []), ...(params.keys || [])].join('+')}`, nodeId);
          await delay(200);
          await delay((params.delay_after || 0.2) * 1000);
          result = 'output';
          break;
        }

        case 'swipe': {
          log('info', `滑动: (${params.start_x},${params.start_y}) → (${params.end_x},${params.end_y})`, nodeId);
          await delay(500);
          await delay((params.delay_after || 0.3) * 1000);
          result = 'output';
          break;
        }

        case 'condition': {
          const cond = params.condition_type || 'always_true';
          if (cond === 'always_true') {
            result = 'yes';
          } else if (cond === 'image_exists') {
            log('info', `检查图片是否存在（模拟: 否）`, nodeId);
            await delay(300);
            result = 'no';
          } else if (cond === 'window_exists') {
            log('info', `检查窗口是否存在（模拟: 否）`, nodeId);
            await delay(300);
            result = 'no';
          } else {
            log('info', `变量判断（模拟: 假）`, nodeId);
            await delay(200);
            result = 'no';
          }
          break;
        }

        case 'loop': {
          const loopType = params.loop_type || 'count';
          const count = params.count || 10;

          log('info', loopType === 'infinite' ? '♾️ 开始无限循环 (按 F9 停止)' : `开始循环 ${count} 次`, nodeId);

          const bodyEdges = getOutputEdges(nodeId, 'loop_body');
          let iter = 0;

          while (true) {
            if (loopType === 'count' && iter >= count) break;
            await checkPauseStop();
            iter++;
            callbacks.onIteration?.(iter, count, nodeId);
            log('info', `循环第 ${iter} 次`, nodeId);

            if (bodyEdges.length > 0) {
              await followFlow(bodyEdges[0].to_node);
            }

            if (loopType === 'infinite') {
              // small pause between iterations so logs are readable
              await delay(300);
            }
          }

          log('info', '循环结束', nodeId);
          result = 'loop_end';
          break;
        }

        case 'sub_script': {
          const subId = params.script_id;
          const subName = params.script_name || '未设置';
          log('info', `调用子脚本: ${subName}`, nodeId);

          if (subId && scripts && scripts[subId]) {
            if (_subFlowDepth >= MAX_SUB_FLOW_DEPTH) {
              log('error', `子脚本调用深度超过限制 (${MAX_SUB_FLOW_DEPTH})`, nodeId);
              result = 'output';
              break;
            }

            _subFlowDepth++;
            try {
              const subScript = scripts[subId];
              const subNodeMap = new Map<string, FlowNode>();
              subScript.nodes.forEach((n) => subNodeMap.set(n.id, n));

              const subEdgesByFrom = new Map<string, FlowEdge[]>();
              subScript.edges.forEach((e) => {
                const arr = subEdgesByFrom.get(e.from_node) || [];
                arr.push(e);
                subEdgesByFrom.set(e.from_node, arr);
              });

              const subStartNodes = subScript.nodes.filter((n) => n.type === 'start');
              if (subStartNodes.length > 0) {
                // Follow sub-flow's edges using the parent's flow control
                let subCurrent = subStartNodes[0].id;
                while (subCurrent && !_stopped) {
                  await checkPauseStop();
                  // Temporarily swap nodeMap and edgesByFrom for sub-flow execution
                  const origNodeMap = nodeMap;
                  const origEdgesByFrom = edgesByFrom;
                  nodeMap = subNodeMap;
                  edgesByFrom = subEdgesByFrom;

                  const subOutputPort = await executeNode(subCurrent);

                  // Restore parent maps
                  nodeMap = origNodeMap;
                  edgesByFrom = origEdgesByFrom;

                  if (!subOutputPort) break;
                  let nextEdges = subEdgesByFrom.get(subCurrent)?.filter(
                    (e) => e.from_port === subOutputPort || (subOutputPort === 'output' && !e.from_port)
                  ) || [];
                  if (nextEdges.length === 0) {
                    nextEdges = subEdgesByFrom.get(subCurrent)?.filter(
                      (e) => e.from_port === 'output' || !e.from_port
                    ) || [];
                  }
                  if (nextEdges.length === 0) break;
                  subCurrent = nextEdges[0].to_node;
                }
              }
            } finally {
              _subFlowDepth--;
            }
            log('info', `子脚本执行完毕: ${subName}`, nodeId);
          } else {
            log('warn', `子脚本未找到: ${subName} (${subId || '无ID'})`, nodeId);
            await delay(200);
          }
          result = 'output';
          break;
        }

        case 'screenshot':
          log('info', '截图（模拟: 已保存）', nodeId);
          await delay(200);
          result = 'output';
          break;
        case 'camera_capture':
          log('info', `摄像头抓拍 (摄像头 ${(params as any).camera_id ?? 0})`, nodeId);
          await delay(500);
          result = 'output';
          break;
        case 'camera_record':
          log('info', `摄像头录像 (摄像头 ${(params as any).camera_id ?? 0})`, nodeId);
          await delay(300);
          result = 'output';
          break;

        case 'variable_set':
          log('info', `设置变量: ${params.name} = "${params.value}"`, nodeId);
          await delay(100);
          result = 'output';
          break;

        case 'variable_get':
          log('info', `读取变量: ${params.name}`, nodeId);
          await delay(100);
          result = 'output';
          break;

        case 'shutdown':
        case 'reboot':
        case 'logoff':
        case 'hibernate': {
          const cmdLabels: Record<string, string> = {
            shutdown: '关机', reboot: '重启', logoff: '注销', hibernate: '休眠',
          };
          const delay = params.delay_seconds ?? 0;
          log('info', `${cmdLabels[node.type]}` + (delay > 0 ? ` (${delay}秒后)` : ''), nodeId);
          await delay(100);
          result = 'output';
          break;
        }

        case 'web_browser': {
          const actionLabels: Record<string, string> = { navigate: '导航', click: '点击', input_text: '输入', screenshot: '截图', wait: '等待', extract: '提取', scroll: '滚动' };
          const action = actionLabels[params.action] || params.action || '未知';
          const target = params.url || params.selector || '';
          log('info', `🌐 浏览器操作: ${action} ${target}`, nodeId);
          const win = window as any;
          if (win.electronAPI?.callFeature) {
            try {
              const res = await win.electronAPI.callFeature('web_browser_execute', {
                action: params.action,
                url: params.url,
                selector: params.selector,
                text: params.text,
                wait_seconds: params.wait_seconds,
                extract_attr: params.extract_attr,
                scroll_direction: params.scroll_direction,
                timeout: params.timeout || 30,
                headless: params.headless || false,
              });
              log('info', `🌐 浏览器结果: ${JSON.stringify(res).slice(0, 200)}`, nodeId);
              if (res?.result?.extracted) {
                log('info', `📄 提取内容: ${String(res.result.extracted).slice(0, 500)}`, nodeId);
              }
            } catch (e: any) {
              log('error', `🌐 浏览器操作失败: ${e.message}`, nodeId);
            }
          } else {
            log('warn', `🌐 浏览器操作模拟: ${action} ${target}`, nodeId);
            await delay(500);
          }
          result = 'output';
          break;
        }

        case 'tool_call': {
          const featureName = params.feature_name || 'unknown';
          log('info', `调用工具: ${featureName}`, nodeId);
          const win = window as any;
          if (win.electronAPI?.callFeature) {
            try {
              const res = await win.electronAPI.callFeature(featureName, params.params || {});
              log('info', `工具结果: ${JSON.stringify(res).slice(0, 200)}`, nodeId);
            } catch (e: any) {
              log('error', `工具调用失败: ${e.message}`, nodeId);
            }
          } else {
            log('warn', `工具调用模拟: ${featureName}`, nodeId);
            await delay(300);
          }
          result = 'output';
          break;
        }

        // ── 聚合类型 ──
        case 'system_power': {
          const action = params.action || 'shutdown';
          log('info', `系统电源: ${action}`, nodeId);
          await delay(100);
          result = 'output';
          break;
        }
        case 'audio_action': {
          log('info', params.action === 'volume_mute' ? '静音控制' : `音量: ${params.level ?? 50}%`, nodeId);
          await delay(100);
          result = 'output';
          break;
        }
        case 'desktop_action': {
          const al: Record<string, string> = { show_desktop:'显示桌面', set_wallpaper:'设置壁纸', empty_recycle_bin:'清空回收站' };
          log('info', al[params.action] || params.action || '桌面操作', nodeId);
          await delay(100);
          result = 'output';
          break;
        }
        case 'clipboard_action': {
          const clipLogs: Record<string, string> = { set:'写入剪贴板', get:'读取剪贴板', get_image:'获取剪贴板图片', get_files:'获取剪贴板文件列表', clear:'清空剪贴板' };
          const clipFn: Record<string, string> = { set:'clipboard_set_text', get:'clipboard_get_text', get_image:'clipboard_get_image', get_files:'clipboard_get_files', clear:'clipboard_clear' };
          const sub = params.action || 'get';
          log('info', clipLogs[sub] || '剪贴板操作', nodeId);
          const cw = window as any;
          if (cw.electronAPI?.callFeature) {
            try {
              const cp: any = {};
              if (sub === 'set') cp.text = params.text || '';
              if (sub === 'get_image') { cp.format_ = params.format_ || 'base64'; cp.save_path = params.save_path || ''; }
              const res = await cw.electronAPI.callFeature(clipFn[sub] || 'clipboard_get_text', cp);
              log('info', `剪贴板结果: ${JSON.stringify(res).slice(0, 200)}`, nodeId);
            } catch (e: any) { log('error', `剪贴板操作失败: ${e.message}`, nodeId); }
          } else { log('warn', `剪贴板操作模拟: ${clipLogs[sub]}`, nodeId); await delay(100); }
          result = 'output';
          break;
        }
        case 'window_control': {
          const winLogs: Record<string, string> = { minimize:'最小化窗口', maximize:'最大化窗口', restore:'还原窗口', close:'关闭窗口', resize:'调整窗口大小', snap:'贴靠窗口' };
          const winFn: Record<string, string> = { minimize:'window_minimize', maximize:'window_maximize', restore:'window_restore', close:'window_close', resize:'window_resize', snap:'window_snap' };
          const op = params.operation || 'minimize';
          log('info', winLogs[op] || '窗口操作', nodeId);
          const ww = window as any;
          if (ww.electronAPI?.callFeature) {
            try {
              const wp: any = { title: params.title || '' };
              if (op === 'resize') { wp.width = params.width ?? 800; wp.height = params.height ?? 600; }
              if (op === 'snap') wp.position = params.snap_position || 'left';
              const res = await ww.electronAPI.callFeature(winFn[op] || 'window_minimize', wp);
              log('info', `窗口操作结果: ${JSON.stringify(res).slice(0, 200)}`, nodeId);
            } catch (e: any) { log('error', `窗口操作失败: ${e.message}`, nodeId); }
          } else { log('warn', `窗口操作模拟: ${winLogs[op]}`, nodeId); await delay(300); }
          result = 'output';
          break;
        }
        case 'process_action': {
          const al: Record<string, string> = { launch:'启动程序', kill:'终止进程', wait_start:'等待启动', wait_exit:'等待退出', focus:'激活窗口' };
          log('info', `${al[params.action] || params.action}: ${params.target || params.name || params.title || ''}`, nodeId);
          await delay(100);
          result = 'output';
          break;
        }

        case 'click_action': {
          const mode = params.mode || 'coordinate';
          if (mode === 'image') {
            const target = (params.targets || [])[0];
            const fname = target ? target.path.split(/[\\/]/).pop() : '未知';
            log('info', `检测图片: ${fname}`, nodeId);
            await delay(200);
          } else if (mode === 'text') {
            log('info', `OCR点击文本: "${(params.text || '').substring(0, 30)}"`, nodeId);
            await delay(300);
            await delay((params.delay_after || 0.5) * 1000);
          } else {
            log('info', `点击: (${params.x}, ${params.y}) [${params.method}]`, nodeId);
            await delay(200);
            await delay((params.delay_after || 0.5) * 1000);
          }
          result = 'output';
          break;
        }

        case 'mouse_operation': {
          const sub = params.sub_operation || 'click';
          if (sub === 'scroll') {
            log('info', `滚轮: ${params.scroll_direction || 'down'} ${params.scroll_amount || 3}格`, nodeId);
          } else if (sub === 'hover') {
            log('info', `悬停 (${params.hover_x},${params.hover_y}) ${params.hover_duration || 0.5}s`, nodeId);
          } else if (sub === 'move') {
            log('info', `移动至 (${params.move_x},${params.move_y})`, nodeId);
          } else {
            const mode = params.mode || 'coordinate';
            if (mode === 'image') {
              const target = (params.targets || [])[0];
              const fname = target ? target.path.split(/[\\/]/).pop() : '未知';
              log('info', `检测图片: ${fname} (相似度${params.similarity || 90}%, 超时${params.timeout || 5}s)`, nodeId);
              await delay(200);
            } else if (mode === 'text') {
              log('info', `OCR点击文本: "${(params.text || '').substring(0, 30)}" (相似度${params.similarity || 80}%)`, nodeId);
              await delay(300);
            } else {
              log('info', `点击: (${params.x}, ${params.y}) [${params.method}]`, nodeId);
              await delay(200);
            }
          }
          await delay((params.delay_after || 0.5) * 1000);
          result = 'output';
          break;
        }

        case 'dialog_action': {
          log('info', `对话框操作: ${params.action || 'handle'}`, nodeId);
          const win = window as any;
          if (win.electronAPI?.callFeature) {
            try {
              const featureName = params.action === 'handle' ? 'handle_dialog'
                : params.action === 'list' ? 'dialog_list'
                : params.action === 'take_action' ? 'dialog_take_action'
                : 'fill_save_dialog';
              const featureParams: any = {};
              if (params.action === 'handle') featureParams.timeout = params.timeout ?? 10;
              if (params.action === 'take_action') { featureParams.action = `click:${params.button || ''}`; featureParams.timeout = params.timeout ?? 10; }
              if (params.action === 'fill_save') { featureParams.filepath = params.path || ''; featureParams.timeout = params.timeout ?? 10; }
              const res = await win.electronAPI.callFeature(featureName, featureParams);
              log('info', `对话框结果: ${JSON.stringify(res).slice(0, 200)}`, nodeId);
            } catch (e: any) { log('error', `对话框操作失败: ${e.message}`, nodeId); }
          } else { log('warn', '对话框操作模拟', nodeId); await delay(300); }
          result = 'output';
          break;
        }
        case 'a11y_action': {
          log('info', `无障碍操作: ${params.action || 'get_tree'}`, nodeId);
          const win2 = window as any;
          if (win2.electronAPI?.callFeature) {
            try {
              const fn = params.action === 'get_tree' ? 'a11y_get_tree'
                : params.action === 'find_element' ? 'a11y_find_element'
                : 'a11y_click_element';
              const fp: any = {};
              if (params.action === 'get_tree') { fp.max_depth = params.max_depth ?? 5; fp.filter_visible = params.filter_visible !== false; }
              if (params.action !== 'get_tree') fp.name = params.name || '';
              if (params.action === 'find_element') { fp.role = params.role || ''; fp.control_type = params.control_type || ''; }
              if (params.action === 'click_element') fp.role = params.role || '';
              const res = await win2.electronAPI.callFeature(fn, fp);
              log('info', `无障碍结果: ${JSON.stringify(res).slice(0, 200)}`, nodeId);
            } catch (e: any) { log('error', `无障碍操作失败: ${e.message}`, nodeId); }
          } else { log('warn', '无障碍操作模拟', nodeId); await delay(300); }
          result = 'output';
          break;
        }
        case 'wifi_action': {
          log('info', `WiFi操作: ${params.action || 'list'}`, nodeId);
          const win3 = window as any;
          if (win3.electronAPI?.callFeature) {
            try {
              const fn = params.action === 'list' ? 'wifi_list'
                : params.action === 'connect' ? 'wifi_connect'
                : 'wifi_disconnect';
              const fp: any = {};
              if (params.action === 'connect') { fp.ssid = params.ssid || ''; fp.password = params.password || ''; }
              const res = await win3.electronAPI.callFeature(fn, fp);
              log('info', `WiFi结果: ${JSON.stringify(res).slice(0, 200)}`, nodeId);
            } catch (e: any) { log('error', `WiFi操作失败: ${e.message}`, nodeId); }
          } else { log('warn', 'WiFi操作模拟', nodeId); await delay(300); }
          result = 'output';
          break;
        }
        default:
          // System command types → forward to @feature via callFeature
          const FEATURE_MAP: Record<string, string> = {
            launch_program: 'launch_program',
            focus_window: 'focus_window',
            kill_process: 'kill_process',
            clipboard_set: 'clipboard_set_text',
            clipboard_get: 'clipboard_get_text',
            volume_set: 'volume_set',
            volume_mute: 'volume_mute',
            lock_workstation: 'lock_workstation',
            system_sleep: 'system_sleep',
            show_desktop: 'show_desktop',
            battery_status: 'battery_status',
            empty_recycle_bin: 'empty_recycle_bin',
            display_brightness: 'display_brightness',
            display_off: 'display_off',
            set_wallpaper: 'set_wallpaper',
          };
          const featureName = FEATURE_MAP[node.type];
          if (featureName) {
            log('info', `调用系统工具: ${featureName}`, nodeId);
            const win = window as any;
            if (win.electronAPI?.callFeature) {
              try {
                const res = await win.electronAPI.callFeature(featureName, params);
                log('info', `工具结果: ${JSON.stringify(res).slice(0, 200)}`, nodeId);
              } catch (e: any) {
                log('error', `工具调用失败: ${e.message}`, nodeId);
              }
            } else {
              log('warn', `模拟系统工具: ${featureName}`, nodeId);
              await delay(300);
            }
          } else {
            log('warn', `未知节点类型: ${node.type}`, nodeId);
          }
          result = 'output';
      }

      callbacks.onNodeStatus(nodeId, 'success');

      // Step mode: pause after this node completes so next node waits
      if (_stepping && !_paused) {
        _paused = true;
        log('debug', '⏭️ 单步模式 — 按 F10 继续', nodeId);
      }

      return result;

    } catch (e: any) {
      if (e.message === 'stopped') throw e;
      log('error', `执行出错: ${e.message}`, nodeId);
      callbacks.onNodeStatus(nodeId, 'error', e.message);

      const errorHandling = settings.error_handling || 'stop';
      if (errorHandling === 'continue') return 'output';
      if (errorHandling === 'retry') return executeNode(nodeId);
      throw e;
    }
  }

  async function followFlow(startId: string) {
    let current = startId;
    while (current && !_stopped) {
      await checkPauseStop();
      const outputPort = await executeNode(current);
      if (!outputPort) break;

      let nextEdges = getOutputEdges(current, outputPort);
      if (nextEdges.length === 0) {
        nextEdges = getOutputEdges(current, 'output');
      }
      if (nextEdges.length === 0) break;

      // Fan-out: if multiple edges from same port, execute all branches
      if (nextEdges.length === 1) {
        current = nextEdges[0].to_node;
      } else {
        // Run branches sequentially (user can add parallel=true later)
        for (const edge of nextEdges) {
          await followFlow(edge.to_node);
        }
        break;
      }
    }
  }

  // Start execution
  const runPromise = (async () => {
    try {
      log('info', '🔄 脚本开始执行（浏览器模拟模式）');

      const startNodes = script.nodes.filter((n) => n.type === 'start');
      if (startNodes.length === 0) {
        log('error', '未找到开始节点');
        callbacks.onDone('error');
        return;
      }

      await followFlow(startNodes[0].id);

      if (_stopped) {
        log('info', '⏹️ 脚本已停止');
        callbacks.onDone('stopped');
      } else {
        log('info', '✅ 脚本执行完成');
        callbacks.onDone('completed');
      }
    } catch (e: any) {
      if (e.message === 'stopped') {
        log('info', '⏹️ 脚本已停止');
        callbacks.onDone('stopped');
      } else {
        log('error', `执行异常: ${e.message}`);
        callbacks.onDone('error');
      }
    } finally {
      callbacks.onCurrentNode(undefined);
    }
  })();

  return {
    pause: () => {
      _paused = true;
    },
    resume: () => {
      _paused = false;
      _resolve.resume?.();
    },
    stop: () => {
      _stopped = true;
      _resolve.resume?.();
      _resolve.step?.();
    },
    stepNext: () => {
      _paused = false;
      _resolve.resume?.();
      _resolve.step?.();
    },
  };
}
