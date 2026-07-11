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
  onNodeStatus: (nodeId: string, status: NonNullable<FlowNode['status']>, error?: string) => void;
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
  const runtimeVariables: Record<string, string> = { ...(script.variables || {}) };
  script.variables = runtimeVariables;

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

  function setVariable(name: any, value: any, nodeId: string) {
    const key = String(name || '').trim();
    if (!key) throw new Error('variable name is required');
    runtimeVariables[key] = String(value ?? '');
    script.variables[key] = runtimeVariables[key];
    log('info', `变量 ${key} = "${runtimeVariables[key]}"`, nodeId);
  }

  function featureText(value: any): string {
    if (value == null) return '';
    if (typeof value === 'string') return value;
    if (typeof value !== 'object') return String(value);
    for (const key of ['text', 'value', 'stdout', 'output']) {
      if (value[key] != null) return featureText(value[key]);
    }
    if (value.result != null) return featureText(value.result);
    if (value.data != null) return featureText(value.data);
    return '';
  }

  function publicFeatureParams(params: Record<string, any>): Record<string, any> {
    return Object.fromEntries(
      Object.entries(params || {}).filter(([key]) => !key.startsWith('_'))
    );
  }

  function featureFailureMessage(res: any, featureName: string): string | undefined {
    if (res === false) return `${featureName} returned false`;
    if (res?.type === 'error') return res.error || `${featureName} failed`;
    if (res?.success === false) return res.error || `${featureName} failed`;
    if (res?.ok === false) return res.error || `${featureName} failed`;
    const inner = res?.type === 'result' ? res.result : res?.result;
    if (inner === false) return `${featureName} returned false`;
    if (inner?.success === false) return inner.error || `${featureName} failed`;
    if (inner?.ok === false) return inner.error || `${featureName} failed`;
    if (res?.type === 'result' && inner?.success === true && inner?.result === false) {
      return inner.error || `${featureName} returned false`;
    }
    return undefined;
  }

  function isWpsReplayNode(params: Record<string, any>): boolean {
    return params?._source_wps_replay === true;
  }

  function hasNonAscii(text: string): boolean {
    return /[^\x00-\x7F]/.test(text);
  }

  function coordsTarget(x: any, y: any): string {
    return `${Number(x) || 0},${Number(y) || 0}`;
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

  async function callFeatureOrThrow(
    featureName: string,
    featureParams: Record<string, any>,
    nodeId: string,
    simulateMessage?: string,
    requireBridge = false,
  ): Promise<any | undefined> {
    const win = window as any;
    if (!win.electronAPI?.callFeature) {
      if (requireBridge || script.settings?.require_real_features === true) {
        throw new Error(`${featureName} requires electronAPI.callFeature`);
      }
      log('warn', simulateMessage || `模拟工具调用: ${featureName}`, nodeId);
      await delay(300);
      return undefined;
    }
    const res = await win.electronAPI.callFeature(featureName, publicFeatureParams(featureParams));
    const failure = featureFailureMessage(res, featureName);
    if (failure) throw new Error(failure);
    return res;
  }

  async function waitFeatureSuccessOrThrow(
    featureName: string,
    featureParams: Record<string, any>,
    nodeId: string,
    timeoutSeconds: number,
  ): Promise<any> {
    const win = window as any;
    if (!win.electronAPI?.callFeature) {
      throw new Error(`${featureName} requires electronAPI.callFeature`);
    }
    const timeoutMs = Math.max(0.1, Number(timeoutSeconds) || 10) * 1000;
    const deadline = Date.now() + timeoutMs;
    let lastFailure = '';
    while (Date.now() <= deadline) {
      const res = await win.electronAPI.callFeature(featureName, publicFeatureParams(featureParams));
      const failure = featureFailureMessage(res, featureName);
      if (!failure) return res;
      lastFailure = failure;
      log('debug', `${featureName} wait probe failed: ${failure}`, nodeId);
      await delay(300);
    }
    throw new Error(`${featureName} timed out after ${timeoutSeconds}s${lastFailure ? `: ${lastFailure}` : ''}`);
  }

  function rejectBrowserSimulation(nodeType: string): void {
    if (script.settings?.require_real_features === true) {
      throw new Error(`${nodeType} requires real feature execution; browser simulation is disabled`);
    }
  }

  function buildPowerParams(featureName: string, nodeParams: Record<string, any>): Record<string, any> {
    if (featureName === 'shutdown' || featureName === 'reboot') {
      return {
        delay_seconds: nodeParams.delay_seconds ?? 0,
        confirm: nodeParams.confirm ?? true,
      };
    }
    if (featureName === 'logoff' || featureName === 'hibernate' || featureName === 'system_sleep') {
      return { confirm: nodeParams.confirm ?? true };
    }
    return {};
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
          if (isWpsReplayNode(params)) {
            const method = params.method || 'single';
            const featureName = method === 'right' ? 'right_click'
              : method === 'middle' ? 'middle_click'
                : 'click_target';
            const target = coordsTarget(params.x, params.y);
            const featureParams: Record<string, any> = featureName === 'middle_click'
              ? { target }
              : { target, region: '' };
            if (featureName === 'click_target') {
              featureParams.timeout = 5;
              featureParams.human_like = true;
              featureParams.double = method === 'double';
              featureParams.allow_uia = false;
              featureParams.physical = true;
            }
            const res = await callFeatureOrThrow(featureName, featureParams, nodeId, undefined, true);
            log('info', `WPS真实点击结果: ${JSON.stringify(res).slice(0, 200)}`, nodeId);
            await delay((params.delay_after || 0.5) * 1000);
            result = 'output';
            break;
          }
          rejectBrowserSimulation('click');
          await delay(200);
          await delay((params.delay_after || 0.5) * 1000);
          result = 'output';
          break;
        }

        case 'click_text': {
          log('info', `OCR点击文本: "${(params.text || '').substring(0, 30)}"`, nodeId);
          if (isWpsReplayNode(params)) {
            const method = params.method || 'single';
            const featureName = method === 'right' ? 'right_click'
              : method === 'middle' ? 'middle_click'
                : 'click_target';
            const target = params.text || '';
            const featureParams: Record<string, any> = featureName === 'middle_click'
              ? { target }
              : { target, region: '' };
            if (featureName === 'click_target') {
              featureParams.timeout = params.timeout ?? 5;
              featureParams.human_like = true;
              featureParams.double = method === 'double';
              featureParams.allow_uia = true;
            }
            const res = await callFeatureOrThrow(featureName, featureParams, nodeId, undefined, true);
            log('info', `WPS真实文字点击结果: ${JSON.stringify(res).slice(0, 200)}`, nodeId);
            await delay((params.delay_after || 0.5) * 1000);
            result = 'output';
            break;
          }
          rejectBrowserSimulation('click_text');
          await delay(300);
          await delay((params.delay_after || 0.5) * 1000);
          result = 'output';
          break;
        }

        case 'popup_close': {
          log('info', `关闭弹窗 (策略: ${params.strategies?.[0] || 'auto'})`, nodeId);
          const res = await callFeatureOrThrow('handle_dialog', {
            timeout: params.timeout ?? 5,
          }, nodeId, `关闭弹窗（模拟）`);
          if (res !== undefined) log('info', `弹窗处理结果: ${JSON.stringify(res).slice(0, 200)}`, nodeId);
          await delay((params.delay_after || 0.3) * 1000);
          result = 'output';
          break;
        }

        case 'color_check': {
          rejectBrowserSimulation('color_check');
          log('info', `检测颜色: ${params.expected_color || '未设置'}`, nodeId);
          await delay(200);
          // Simulate: always matches for demo
          result = 'output';
          break;
        }

        case 'progress_wait': {
          rejectBrowserSimulation('progress_wait');
          log('info', `等待进度条 (${params.wait_type || 'fill'})`, nodeId);
          await delay(500);
          result = 'output';
          break;
        }

        case 'region_wait': {
          rejectBrowserSimulation('region_wait');
          log('info', `等待区域${params.region_type === 'stable' ? '稳定' : '变化'}`, nodeId);
          await delay(500);
          result = 'output';
          break;
        }

        case 'scene_condition': {
          rejectBrowserSimulation('scene_condition');
          const sceneName = params.scene_name || '未知';
          log('info', `场景识别: "${sceneName}"（模拟: 不匹配）`, nodeId);
          await delay(300);
          result = 'no';
          break;
        }

        case 'image_click': {
          rejectBrowserSimulation('image_click');
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
          } else if (isWpsReplayNode(params) && params.wait_type === 'window') {
            const title = String(params.window_title || '').trim();
            if (!title) throw new Error('WPS window wait requires window_title');
            const res = await waitFeatureSuccessOrThrow(
              'focus_window',
              { title, exact: false },
              nodeId,
              params.timeout ?? 10,
            );
            log('info', `WPS window wait result: ${JSON.stringify(res).slice(0, 200)}`, nodeId);
          } else if (isWpsReplayNode(params)) {
            throw new Error(
              `WinPilot wait step "${params.wait_type || 'condition'}" must run through the original .wps.json Replayer`
            );
          } else {
            rejectBrowserSimulation('wait');
            log('info', `等待条件（模拟跳过）`, nodeId);
            await delay(200);
          }
          result = 'output';
          break;
        }

        case 'input_text': {
          log('info', `输入文本: "${(params.text || '').substring(0, 30)}"`, nodeId);
          if (isWpsReplayNode(params)) {
            if (params.click_before && Number.isFinite(Number(params.x)) && Number.isFinite(Number(params.y))) {
              await callFeatureOrThrow('click_target', {
                target: coordsTarget(params.x, params.y),
                region: '',
                timeout: 5,
                human_like: true,
                double: false,
                allow_uia: false,
                physical: true,
              }, nodeId, undefined, true);
            }
            if (params.clear_before) {
              await callFeatureOrThrow('hotkey', { keys: 'ctrl+a' }, nodeId, undefined, true);
            }
            const text = String(params.text || '');
            const featureName = hasNonAscii(text) ? 'type_clipboard' : 'type_text';
            const featureParams = featureName === 'type_clipboard'
              ? { text }
              : { text, clear_first: false, interval: 0 };
            const res = await callFeatureOrThrow(featureName, featureParams, nodeId, undefined, true);
            log('info', `WPS真实输入结果: ${JSON.stringify(res).slice(0, 200)}`, nodeId);
            await delay((params.delay_after || 0.3) * 1000);
            result = 'output';
            break;
          }
          rejectBrowserSimulation('input_text');
          await delay(400);
          await delay((params.delay_after || 0.3) * 1000);
          result = 'output';
          break;
        }

        case 'keyboard': {
          log('info', `按键: ${[...(params.modifiers || []), ...(params.keys || [])].join('+')}`, nodeId);
          if (isWpsReplayNode(params)) {
            const keys = [...(params.modifiers || []), ...(params.keys || [])].join('+');
            const res = await callFeatureOrThrow('hotkey', { keys }, nodeId, undefined, true);
            log('info', `WPS真实快捷键结果: ${JSON.stringify(res).slice(0, 200)}`, nodeId);
            await delay((params.delay_after || 0.2) * 1000);
            result = 'output';
            break;
          }
          rejectBrowserSimulation('keyboard');
          await delay(200);
          await delay((params.delay_after || 0.2) * 1000);
          result = 'output';
          break;
        }

        case 'swipe': {
          log('info', `滑动: (${params.start_x},${params.start_y}) → (${params.end_x},${params.end_y})`, nodeId);
          if (isWpsReplayNode(params)) {
            const res = await callFeatureOrThrow('swipe_action', {
              start_x: params.start_x ?? 0,
              start_y: params.start_y ?? 0,
              end_x: params.end_x ?? 0,
              end_y: params.end_y ?? 0,
              duration: params.duration ?? 0.5,
            }, nodeId, undefined, true);
            log('info', `WPS真实拖拽结果: ${JSON.stringify(res).slice(0, 200)}`, nodeId);
            await delay((params.delay_after || 0.3) * 1000);
            result = 'output';
            break;
          }
          rejectBrowserSimulation('swipe');
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
            rejectBrowserSimulation('condition.image_exists');
            log('info', `检查图片是否存在（模拟: 否）`, nodeId);
            await delay(300);
            result = 'no';
          } else if (cond === 'window_exists') {
            rejectBrowserSimulation('condition.window_exists');
            log('info', `检查窗口是否存在（模拟: 否）`, nodeId);
            await delay(300);
            result = 'no';
          } else if (cond === 'variable_equals' || cond === 'variable_greater' || cond === 'variable_less') {
            const name = String(params.variable_name || '').trim();
            const actual = runtimeVariables[name] ?? '';
            const expected = String(params.variable_value ?? '');
            let matched = false;
            if (cond === 'variable_equals') {
              matched = actual === expected;
            } else {
              const a = Number(actual);
              const b = Number(expected);
              matched = Number.isFinite(a) && Number.isFinite(b)
                ? (cond === 'variable_greater' ? a > b : a < b)
                : false;
            }
            log('info', `变量判断: ${name}=${actual} ${cond} ${expected} -> ${matched ? 'yes' : 'no'}`, nodeId);
            result = matched ? 'yes' : 'no';
          } else {
            rejectBrowserSimulation('condition');
            log('info', `变量判断（模拟: 假）`, nodeId);
            await delay(200);
            result = 'no';
          }
          break;
        }

        case 'loop': {
          if (params._requires_wps_replay) {
            throw new Error(
              `WinPilot control-flow step "${params._wps_tool || 'ctrl.loop'}:${params._wps_loop_type || 'loop'}" must run through the original .wps.json Replayer`
            );
          }
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
            rejectBrowserSimulation('sub_script');
            log('warn', `子脚本未找到: ${subName} (${subId || '无ID'})`, nodeId);
            await delay(200);
          }
          result = 'output';
          break;
        }

        case 'screenshot':
          log('info', '截图（模拟: 已保存）', nodeId);
          if (isWpsReplayNode(params)) {
            const region = typeof params.region === 'string'
              ? params.region
              : Array.isArray(params.region)
                ? params.region.join(',')
                : '';
            const res = await callFeatureOrThrow('screenshot', {
              region,
              format: params.format === 'jpg' ? 'jpeg' : (params.format || 'png'),
              quality: params.quality ?? 70,
            }, nodeId, undefined, true);
            log('info', `WPS真实截图结果: ${JSON.stringify(res).slice(0, 200)}`, nodeId);
            result = 'output';
            break;
          }
          rejectBrowserSimulation('screenshot');
          await delay(200);
          result = 'output';
          break;
        case 'camera_capture':
          log('info', `摄像头抓拍 (摄像头 ${(params as any).camera_id ?? 0})`, nodeId);
          {
            const res = await callFeatureOrThrow('camera_capture', {
              camera_id: (params as any).camera_id ?? 0,
              output_path: (params as any).output_path || (params as any).path || '',
              width: (params as any).width ?? 0,
              height: (params as any).height ?? 0,
              format: (params as any).format || 'jpg',
              wait_stable: (params as any).wait_stable ?? 0.5,
            }, nodeId, '摄像头抓拍（模拟）');
            if (res !== undefined) log('info', `摄像头抓拍结果: ${JSON.stringify(res).slice(0, 200)}`, nodeId);
          }
          result = 'output';
          break;
        case 'camera_record':
          log('info', `摄像头录像 (摄像头 ${(params as any).camera_id ?? 0})`, nodeId);
          {
            const res = await callFeatureOrThrow('camera_record_start', {
              camera_id: (params as any).camera_id ?? 0,
              output_path: (params as any).output_path || (params as any).path || '',
              fps: (params as any).fps ?? 15,
              duration: (params as any).duration ?? 0,
              width: (params as any).width ?? 0,
              height: (params as any).height ?? 0,
            }, nodeId, '摄像头录像（模拟）');
            if (res !== undefined) log('info', `摄像头录像结果: ${JSON.stringify(res).slice(0, 200)}`, nodeId);
          }
          result = 'output';
          break;

        case 'variable_set':
          log('info', `设置变量: ${params.name} = "${params.value}"`, nodeId);
          setVariable(params.name, params.value, nodeId);
          await delay(100);
          result = 'output';
          break;

        case 'variable_get':
          log('info', `读取变量: ${params.name} = "${runtimeVariables[String(params.name || '').trim()] ?? ''}"`, nodeId);
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
          const res = await callFeatureOrThrow(node.type, buildPowerParams(node.type, params), nodeId, `${cmdLabels[node.type]}（模拟）`);
          if (res !== undefined) log('info', `系统电源结果: ${JSON.stringify(res).slice(0, 200)}`, nodeId);
          result = 'output';
          break;
        }

        case 'web_browser': {
          const actionLabels: Record<string, string> = { navigate: '导航', click: '点击', input_text: '输入', screenshot: '截图', wait: '等待', extract: '提取', scroll: '滚动' };
          const action = actionLabels[params.action] || params.action || '未知';
          const target = params.url || params.selector || '';
          log('info', `🌐 浏览器操作: ${action} ${target}`, nodeId);
          const res = await callFeatureOrThrow('web_browser_execute', {
            action: params.action,
            url: params.url,
            selector: params.selector,
            text: params.text,
            wait_seconds: params.wait_seconds,
            extract_attr: params.extract_attr,
            scroll_direction: params.scroll_direction,
            timeout: params.timeout || 30,
            headless: params.headless || false,
          }, nodeId, `🌐 浏览器操作模拟: ${action} ${target}`);
          if (res !== undefined) {
            log('info', `🌐 浏览器结果: ${JSON.stringify(res).slice(0, 200)}`, nodeId);
            if (res?.result?.extracted) {
              log('info', `📄 提取内容: ${String(res.result.extracted).slice(0, 500)}`, nodeId);
            }
          }
          result = 'output';
          break;
        }

        case 'tool_call': {
          const featureName = params.feature_name || 'unknown';
          const featureParams = params.params || {};
          if (featureParams._requires_wps_replay) {
            throw new Error(
              `WinPilot-only replay step "${featureParams._wps_tool || featureName}" must run through the original .wps.json Replayer`
            );
          }
          if (!(window as any).electronAPI?.callFeature) {
            throw new Error(`${featureName} requires electronAPI.callFeature`);
          }
          log('info', `调用工具: ${featureName}`, nodeId);
          const res = await callFeatureOrThrow(featureName, featureParams, nodeId, `工具调用模拟: ${featureName}`);
          if (res !== undefined) log('info', `工具结果: ${JSON.stringify(res).slice(0, 200)}`, nodeId);
          result = 'output';
          break;
        }

        // ── 聚合类型 ──
        case 'system_power': {
          const action = params.action || 'shutdown';
          const featureName = action === 'lock_workstation' ? 'lock_workstation'
            : action === 'system_sleep' ? 'system_sleep'
            : action === 'display_off' ? 'display_off'
            : action;
          log('info', `系统电源: ${action}`, nodeId);
          const res = await callFeatureOrThrow(featureName, buildPowerParams(featureName, params), nodeId, `系统电源模拟: ${action}`);
          if (res !== undefined) log('info', `系统电源结果: ${JSON.stringify(res).slice(0, 200)}`, nodeId);
          result = 'output';
          break;
        }
        case 'audio_action': {
          const featureName = params.action === 'volume_mute' ? 'volume_mute' : 'volume_set';
          const featureParams = featureName === 'volume_mute'
            ? { mute: params.mute ?? null }
            : { level: params.level ?? 50 };
          log('info', params.action === 'volume_mute' ? '静音控制' : `音量: ${params.level ?? 50}%`, nodeId);
          const res = await callFeatureOrThrow(featureName, featureParams, nodeId, `音频操作模拟: ${featureName}`);
          if (res !== undefined) log('info', `音频操作结果: ${JSON.stringify(res).slice(0, 200)}`, nodeId);
          result = 'output';
          break;
        }
        case 'desktop_action': {
          const al: Record<string, string> = { show_desktop:'显示桌面', set_wallpaper:'设置壁纸', empty_recycle_bin:'清空回收站' };
          const action = params.action || 'show_desktop';
          const featureParams = action === 'set_wallpaper'
            ? { path: params.path || '', style: params.style || 'fill' }
            : action === 'empty_recycle_bin'
              ? { confirm: params.confirm ?? true }
              : {};
          log('info', al[action] || action || '桌面操作', nodeId);
          const res = await callFeatureOrThrow(action, featureParams, nodeId, `桌面操作模拟: ${action}`);
          if (res !== undefined) log('info', `桌面操作结果: ${JSON.stringify(res).slice(0, 200)}`, nodeId);
          result = 'output';
          break;
        }
        case 'clipboard_action': {
          const clipLogs: Record<string, string> = { set:'写入剪贴板', get:'读取剪贴板', get_image:'获取剪贴板图片', get_files:'获取剪贴板文件列表', clear:'清空剪贴板' };
          const clipFn: Record<string, string> = { set:'clipboard_set_text', get:'clipboard_get_text', get_image:'clipboard_get_image', get_files:'clipboard_get_files', clear:'clipboard_clear' };
          const sub = params.action || 'get';
          log('info', clipLogs[sub] || '剪贴板操作', nodeId);
          const cp: any = {};
          if (sub === 'set') cp.text = params.text || '';
          if (sub === 'get_image') { cp.format_ = params.format_ || 'base64'; cp.save_path = params.save_path || ''; }
          const res = await callFeatureOrThrow(clipFn[sub] || 'clipboard_get_text', cp, nodeId, `剪贴板操作模拟: ${clipLogs[sub]}`);
          if (res !== undefined) log('info', `剪贴板结果: ${JSON.stringify(res).slice(0, 200)}`, nodeId);
          if (sub === 'get' && params.into && res !== undefined) {
            setVariable(params.into, featureText(res), nodeId);
          }
          result = 'output';
          break;
        }
        case 'window_control': {
          const winLogs: Record<string, string> = { minimize:'最小化窗口', maximize:'最大化窗口', restore:'还原窗口', close:'关闭窗口', resize:'调整窗口大小', snap:'贴靠窗口' };
          const winFn: Record<string, string> = { minimize:'window_minimize', maximize:'window_maximize', restore:'window_restore', close:'window_close', resize:'window_resize', snap:'window_snap' };
          const op = params.operation || 'minimize';
          log('info', winLogs[op] || '窗口操作', nodeId);
          const wp: any = { title: params.title || '' };
          if (op === 'resize') { wp.width = params.width ?? 800; wp.height = params.height ?? 600; }
          if (op === 'snap') wp.position = params.snap_position || 'left';
          const res = await callFeatureOrThrow(winFn[op] || 'window_minimize', wp, nodeId, `窗口操作模拟: ${winLogs[op]}`);
          if (res !== undefined) log('info', `窗口操作结果: ${JSON.stringify(res).slice(0, 200)}`, nodeId);
          result = 'output';
          break;
        }
        case 'process_action': {
          const al: Record<string, string> = { launch:'启动程序', kill:'终止进程', wait_start:'等待启动', wait_exit:'等待退出', focus:'激活窗口' };
          const action = params.action || 'launch';
          const featureName = action === 'launch' ? 'launch_program'
            : action === 'kill' ? 'kill_process'
            : action === 'wait_exit' ? 'wait_process_exit'
            : action === 'wait_start' ? 'wait_process_start'
            : 'focus_window';
          const featureParams = action === 'launch'
            ? { target: params.target || '', args: params.args || '', wait: !!params.wait, force_new: !!params.force_new }
            : action === 'kill'
              ? { name: params.name || params.target || '', force: !!params.force }
              : action === 'focus'
                ? { title: params.title || params.target || '', exact: !!params.exact }
                : { name: params.name || '', timeout: params.timeout ?? (action === 'wait_exit' ? 60 : 30) };
          log('info', `${al[action] || action}: ${params.target || params.name || params.title || ''}`, nodeId);
          const res = await callFeatureOrThrow(featureName, featureParams, nodeId, `进程操作模拟: ${featureName}`);
          if (res !== undefined) log('info', `进程操作结果: ${JSON.stringify(res).slice(0, 200)}`, nodeId);
          result = 'output';
          break;
        }

        case 'wait_process': {
          const waitFor = params.wait_for === 'exit' ? 'exit' : 'start';
          const featureName = waitFor === 'exit' ? 'wait_process_exit' : 'wait_process_start';
          log('info', `wait_process: ${waitFor} ${params.name || ''}`, nodeId);
          const res = await callFeatureOrThrow(featureName, {
            name: params.name || '',
            timeout: params.timeout ?? (waitFor === 'exit' ? 60 : 30),
          }, nodeId, undefined, true);
          log('info', `wait_process result: ${JSON.stringify(res).slice(0, 200)}`, nodeId);
          result = 'output';
          break;
        }

        case 'click_action': {
          rejectBrowserSimulation('click_action');
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
          if (!isWpsReplayNode(params)) rejectBrowserSimulation('mouse_operation');
          const sub = params.sub_operation || 'click';
          if (sub === 'scroll') {
            log('info', `滚轮: ${params.scroll_direction || 'down'} ${params.scroll_amount || 3}格`, nodeId);
            if (isWpsReplayNode(params)) {
              const res = await callFeatureOrThrow('scroll', {
                direction: params.scroll_direction || 'down',
                amount: params.scroll_amount || 3,
                x: params.x ?? -1,
                y: params.y ?? -1,
                target: '',
              }, nodeId, undefined, true);
              log('info', `WPS真实滚动结果: ${JSON.stringify(res).slice(0, 200)}`, nodeId);
              await delay((params.delay_after || 0.5) * 1000);
              result = 'output';
              break;
            }
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
          const featureName = params.action === 'handle' ? 'handle_dialog'
            : params.action === 'list' ? 'dialog_list'
            : params.action === 'take_action' ? 'dialog_take_action'
            : 'fill_save_dialog';
          const featureParams: any = {};
          if (params.action === 'handle') featureParams.timeout = params.timeout ?? 10;
          if (params.action === 'take_action') { featureParams.action = `click:${params.button || ''}`; featureParams.timeout = params.timeout ?? 10; }
          if (params.action === 'fill_save') { featureParams.filepath = params.path || ''; featureParams.timeout = params.timeout ?? 10; }
          const res = await callFeatureOrThrow(featureName, featureParams, nodeId, '对话框操作模拟');
          if (res !== undefined) log('info', `对话框结果: ${JSON.stringify(res).slice(0, 200)}`, nodeId);
          result = 'output';
          break;
        }
        case 'a11y_action': {
          log('info', `无障碍操作: ${params.action || 'get_tree'}`, nodeId);
          const fn = params.action === 'get_tree' ? 'a11y_get_tree'
            : params.action === 'find_element' ? 'a11y_find_element'
            : 'a11y_click_element';
          const fp: any = {};
          if (params.action === 'get_tree') { fp.max_depth = params.max_depth ?? 5; fp.filter_visible = params.filter_visible !== false; }
          if (params.action !== 'get_tree') fp.name = params.name || '';
          if (params.action === 'find_element') { fp.role = params.role || ''; fp.control_type = params.control_type || ''; }
          if (params.action === 'click_element') fp.role = params.role || '';
          const res = await callFeatureOrThrow(fn, fp, nodeId, '无障碍操作模拟');
          if (res !== undefined) log('info', `无障碍结果: ${JSON.stringify(res).slice(0, 200)}`, nodeId);
          result = 'output';
          break;
        }
        case 'wifi_action': {
          log('info', `WiFi操作: ${params.action || 'list'}`, nodeId);
          const fn = params.action === 'list' ? 'wifi_list'
            : params.action === 'connect' ? 'wifi_connect'
            : 'wifi_disconnect';
          const fp: any = {};
          if (params.action === 'connect') { fp.ssid = params.ssid || ''; fp.password = params.password || ''; }
          const res = await callFeatureOrThrow(fn, fp, nodeId, 'WiFi操作模拟');
          if (res !== undefined) log('info', `WiFi结果: ${JSON.stringify(res).slice(0, 200)}`, nodeId);
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
          if (!featureName) {
            throw new Error(`Unsupported FlowScript node type: ${node.type}`);
          }
          if (featureName) {
            log('info', `调用系统工具: ${featureName}`, nodeId);
            const res = await callFeatureOrThrow(
              featureName,
              params,
              nodeId,
              `模拟系统工具: ${featureName}`,
              isWpsReplayNode(params)
            );
            if (res !== undefined) log('info', `工具结果: ${JSON.stringify(res).slice(0, 200)}`, nodeId);
            if (node.type === 'clipboard_get' && params.into && res !== undefined) {
              setVariable(params.into, featureText(res), nodeId);
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
