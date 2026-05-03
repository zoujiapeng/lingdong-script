import { FlowScript, FlowNode, FlowEdge, LogEntry } from '../types';

// ============================================================
// Browser-based Flow Executor (runs without Python)
// ============================================================

export interface ExecutionController {
  pause: () => void;
  resume: () => void;
  stop: () => void;
}

interface ExecCallbacks {
  onNodeStatus: (nodeId: string, status: FlowNode['status'], error?: string) => void;
  onLog: (entry: Omit<LogEntry, 'timestamp'>) => void;
  onDone: (status: 'completed' | 'stopped' | 'error') => void;
  onCurrentNode: (nodeId: string | undefined) => void;
  onIteration?: (current: number, total: number, nodeId: string) => void;
}

export function createFlowExecutor(
  script: FlowScript,
  callbacks: ExecCallbacks
): ExecutionController {
  let _paused = false;
  let _stopped = false;
  let resumeResolve: (() => void) | null = null;

  const nodeMap = new Map<string, FlowNode>();
  script.nodes.forEach((n) => nodeMap.set(n.id, n));

  const edgesByFrom = new Map<string, FlowEdge[]>();
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
        await new Promise<void>((r) => { resumeResolve = r; });
        resumeResolve = null;
        if (_stopped) throw new Error('stopped');
      }
      await new Promise((r) => setTimeout(r, 30));
    }
  }

  async function checkPauseStop() {
    while (_paused && !_stopped) {
      await new Promise<void>((r) => { resumeResolve = r; });
      resumeResolve = null;
    }
    if (_stopped) throw new Error('stopped');
  }

  async function executeNode(nodeId: string): Promise<string> {
    const node = nodeMap.get(nodeId);
    if (!node) { log('error', `节点未找到: ${nodeId}`); return ''; }
    if (node.disabled) { log('info', `跳过已禁用节点: ${node.type}`, nodeId); return 'output'; }

    callbacks.onCurrentNode(nodeId);
    callbacks.onNodeStatus(nodeId, 'running');
    const params = node.params as any;
    const settings = script.settings || {};

    try {
      let result: string;

      switch (node.type) {
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
            await delay(Math.min(dur, 2) * 1000); // cap at 2s for demo
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

        case 'sub_script':
          log('info', `调用子脚本: ${params.script_name || '未设置'}`, nodeId);
          await delay(200);
          result = 'output';
          break;

        case 'screenshot':
          log('info', '截图（模拟: 已保存）', nodeId);
          await delay(200);
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

        default:
          log('warn', `未知节点类型: ${node.type}`, nodeId);
          result = 'output';
      }

      callbacks.onNodeStatus(nodeId, 'success');
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
      if (resumeResolve) resumeResolve();
    },
    stop: () => {
      _stopped = true;
      if (resumeResolve) resumeResolve();
    },
  };
}
