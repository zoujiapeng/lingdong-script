import { nanoid } from 'nanoid';
import { FlowScript, FlowNode, FlowEdge, MacroEvent } from '../types';

/**
 * Convert recorded macro events into a FlowScript.
 *
 * @deprecated (P5 migration) This converts the legacy pynput macro — absolute
 * coordinates + keystrokes only, no element selectors — so the result breaks on
 * any layout/resolution change. Prefer the WinPilot **human-demonstration
 * recorder** (Agent → 重放 → 录制演示), which captures element selectors at click
 * time and produces a `.wps.json` that re-locates targets by text on replay.
 * Kept for backward compatibility with existing recorded macros.
 *
 * Rules:
 * - click (pressed) → CLICK node
 * - key press (single char) → INPUT_TEXT node
 * - key press (special key like Key.enter) → KEYBOARD node
 * - gaps >= 300ms between events → WAIT node(s)
 * - Generates start/end nodes + edges connecting everything
 */
export function macroEventsToFlow(events: MacroEvent[], name: string): FlowScript {
  const nodes: FlowNode[] = [];
  const edges: FlowEdge[] = [];
  const startId = nanoid();
  const endId = nanoid();

  // Start node
  nodes.push({
    id: startId,
    type: 'start',
    x: 360,
    y: 20,
    params: { label: '开始' },
    status: 'idle',
  });

  // End node
  nodes.push({
    id: endId,
    type: 'end',
    x: 360,
    y: 200,
    params: { label: '结束' },
    status: 'idle',
  });

  let prevTime = 0;
  let prevNodeId = startId;
  const chain: { nodeId: string; time: number }[] = [{ nodeId: startId, time: 0 }];

  for (const ev of events) {
    const gap = ev.time - prevTime;

    // Insert WAIT node for gaps >= 300ms
    if (gap >= 0.3 && prevNodeId !== startId) {
      const waitId = nanoid();
      nodes.push({
        id: waitId,
        type: 'wait',
        x: 360,
        y: 0, // will layout below
        params: { wait_type: 'time', duration: Math.round(gap * 10) / 10, description: `等待 ${Math.round(gap * 10) / 10}秒` },
        label: `等待 ${Math.round(gap * 10) / 10}秒`,
        status: 'idle',
      });
      edges.push({ id: nanoid(), from_node: prevNodeId, from_port: 'output', to_node: waitId, to_port: 'input' });
      prevNodeId = waitId;
      chain.push({ nodeId: waitId, time: ev.time });
    }

    // Create event node
    const nodeId = nanoid();
    let nodeType: string;
    let nodeParams: any;

    if (ev.type === 'click' && ev.pressed) {
      nodeType = 'click';
      nodeParams = {
        x: ev.x || 0,
        y: ev.y || 0,
        method: ev.button?.includes('right') ? 'right' : ev.button?.includes('middle') ? 'middle' : 'single',
        delay_after: 0.3,
        description: `点击 (${ev.x}, ${ev.y})`,
      };
      nodes.push({
        id: nodeId,
        type: 'click',
        x: 360,
        y: 0,
        params: nodeParams,
        label: `点击 ${ev.x},${ev.y}`,
        status: 'idle',
      });
    } else if (ev.type === 'key' && ev.pressed && ev.key) {
      // Check if it's a special key
      const specialKeyMatch = ev.key.match(/^Key\.(.+)$/);
      if (specialKeyMatch) {
        nodeType = 'keyboard';
        nodeParams = {
          keys: [specialKeyMatch[1]],
          modifiers: [],
          delay_after: 0.2,
          description: `按键: ${specialKeyMatch[1]}`,
        };
        nodes.push({
          id: nodeId,
          type: 'keyboard',
          x: 360,
          y: 0,
          params: nodeParams,
          label: `按键 ${specialKeyMatch[1]}`,
          status: 'idle',
        });
      } else if (ev.key.length === 1) {
        // Single character → INPUT_TEXT
        nodeType = 'input_text';
        nodeParams = {
          text: ev.key,
          click_before: false,
          clear_before: false,
          delay_after: 0.1,
          description: `输入: ${ev.key}`,
        };
        nodes.push({
          id: nodeId,
          type: 'input_text',
          x: 360,
          y: 0,
          params: nodeParams,
          label: `输入 ${ev.key}`,
          status: 'idle',
        });
      } else {
        // Other key → KEYBOARD
        nodeType = 'keyboard';
        nodeParams = {
          keys: [ev.key],
          modifiers: [],
          delay_after: 0.2,
          description: `按键: ${ev.key}`,
        };
        nodes.push({
          id: nodeId,
          type: 'keyboard',
          x: 360,
          y: 0,
          params: nodeParams,
          label: `按键 ${ev.key}`,
          status: 'idle',
        });
      }
    } else {
      // Skip moves and non-pressed keys
      prevTime = ev.time;
      continue;
    }

    edges.push({ id: nanoid(), from_node: prevNodeId, from_port: 'output', to_node: nodeId, to_port: 'input' });
    prevNodeId = nodeId;
    chain.push({ nodeId, time: ev.time });
    prevTime = ev.time;
  }

  // Connect last node to end
  edges.push({ id: nanoid(), from_node: prevNodeId, from_port: 'output', to_node: endId, to_port: 'input' });

  // Layout nodes vertically with 100px spacing
  const nodeIds = [startId, ...nodes.filter(n => n.id !== startId && n.id !== endId).map(n => n.id), endId];
  nodeIds.forEach((id, i) => {
    const node = nodes.find(n => n.id === id);
    if (node) {
      node.x = 360;
      node.y = 40 + i * 100;
    }
  });

  return {
    meta: {
      id: nanoid(),
      name: `宏: ${name}`,
      description: `从宏录制 "${name}" 转换 (${events.length} 个事件)`,
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
      version: '1.0',
    },
    nodes,
    edges,
    variables: {},
    settings: {
      default_delay: 0.5,
      error_handling: 'stop',
      max_retries: 3,
      retry_delay: 1,
      log_level: 'info',
    },
  };
}
