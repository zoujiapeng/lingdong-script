import { nanoid } from 'nanoid';
import type { FlowEdge, FlowNode, FlowScript, NodeParams } from '../types';

/** A deterministic WinPilot replay step preserved verbatim in the flow. */
export interface WpsStep {
  tool: string;
  [key: string]: unknown;
}

export interface WpsScript {
  format?: string;
  name?: string;
  task?: string;
  source?: string;
  source_ir?: string;
  source_run?: string;
  source_runs?: unknown[];
  export_manifest?: Record<string, unknown>;
  created?: number;
  target_window?: unknown;
  target?: unknown;
  conditions?: Record<string, unknown>;
  evidence?: unknown[] | Record<string, unknown>;
  safety?: Record<string, unknown>;
  fallback?: Record<string, unknown>;
  vars?: Record<string, string>;
  steps: WpsStep[];
}

const X = 80;
const Y_STEP = 112;

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value));
}

function sourceDocument(script: WpsScript): FlowScript['source'] {
  const raw = clone(script) as unknown as Record<string, unknown>;
  delete raw.steps;
  delete raw.vars;
  delete raw.safety;
  delete raw.fallback;
  return { kind: 'wps', raw };
}

function replayTarget(script: WpsScript): unknown {
  if (script.target_window != null && script.target_window !== '') return script.target_window;
  if (script.target != null && script.target !== '') return script.target;
  const conditions = script.conditions;
  if (!conditions) return undefined;
  for (const key of ['target_window', 'window_title', 'window_appears', 'target']) {
    const value = conditions[key];
    if (value != null && value !== '') return value;
  }
  return undefined;
}

function lastReadableSentence(value: unknown): string {
  const lines = String(value || '').split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
  const line = lines[lines.length - 1] || '';
  const sentences = line.match(/[^.!?。！？]+[.!?。！？]?/g) || [];
  return (sentences[sentences.length - 1] || line).trim().replace(/[：:]$/, '');
}

function replayLabel(step: WpsStep, index: number): string {
  const tool = String(step.tool || 'unknown');
  const detail = lastReadableSentence(step.note)
    || String(step.text || step.element_text || step.title || step.name || '').trim();
  const summary = detail ? `${tool}: ${detail.slice(0, 36)}` : tool;
  return `${index + 1}. ${summary}`;
}

/**
 * Convert a WPS script to an exact, editable offline-replay graph.
 *
 * A replay_step keeps the recorded action, optional target hint, and nested
 * control flow intact. It can be mixed freely with native ARP nodes.
 */
export function wpsToFlowScript(script: WpsScript): FlowScript {
  const nodes: FlowNode[] = [];
  const edges: FlowEdge[] = [];
  const targetWindow = replayTarget(script);
  const session = String(script.source_run || `offline-${nanoid(8)}`);
  const steps = script.steps || [];
  let row = 0;

  const add = (type: FlowNode['type'], params: NodeParams, label?: string): FlowNode => {
    const node: FlowNode = {
      id: nanoid(8),
      type,
      x: X,
      y: row++ * Y_STEP,
      params,
      ...(label ? { label } : {}),
    };
    nodes.push(node);
    return node;
  };
  const link = (from: FlowNode, to: FlowNode): void => {
    edges.push({
      id: nanoid(8),
      from_node: from.id,
      from_port: 'output',
      to_node: to.id,
      to_port: 'input',
    });
  };

  const start = add('start', { label: '开始' } as NodeParams);
  let previous = start;
  for (const [index, step] of steps.entries()) {
    const stepTarget = step.target_window ?? targetWindow;
    const node = add('replay_step', {
      step: clone(step),
      session,
      pace_ms: 0,
      ...(stepTarget !== undefined ? { target_window: stepTarget } : {}),
      _source_wps_replay: true,
    } as NodeParams, replayLabel(step, index));
    link(previous, node);
    previous = node;
  }
  const end = add('end', { label: '结束' } as NodeParams);
  link(previous, end);

  const now = new Date().toISOString();
  const name = script.name || script.task || '导入的重放';
  return {
    meta: {
      id: nanoid(10),
      name,
      description: script.task || '',
      created_at: now,
      updated_at: now,
      version: '1.0.0',
      source: 'flow',
      tags: ['wps-import', 'replay-mode:offline', script.format || 'wps'],
    },
    nodes,
    edges,
    variables: script.vars || {},
    settings: {
      default_delay: 0,
      error_handling: 'stop',
      max_retries: 2,
      retry_delay: 1,
      log_level: 'info',
      require_real_features: true,
    },
    source: sourceDocument(script),
  };
}
