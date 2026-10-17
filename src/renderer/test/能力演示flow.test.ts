import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';
import { flowToArpGraph } from '../platform/flowToArpGraph';

describe('能力演示.flow 编译检查', () => {
  it('能编译为非 legacy_flow 的 ARP 图', () => {
    const path = resolve(__dirname, '../../../python/winpilot_home/eval_targets/能力演示.flow');
    const script = JSON.parse(readFileSync(path, 'utf8'));
    const graph = flowToArpGraph(script);
    const kinds = graph.nodes.map((n) => n.kind);
    expect(kinds).not.toContain('legacy_flow');
    expect(kinds).toContain('action_batch');
    expect(kinds).toContain('condition');
    expect(kinds).toContain('delay');
    expect(kinds).toContain('replay_step');
    expect(kinds).toContain('skill_replay');
    expect(kinds).toContain('evidence_gate');
  });
});
