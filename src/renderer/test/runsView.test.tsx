import { render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { RunsView } from '../components/platform/PlatformWorkbench';
import type { ArpExecutionSnapshot, CurrentRunResponse, RunProjection, TaskContract } from '../platform/contracts';

function historicalRun(): RunProjection {
  return {
    run_id: 'arp-history-1',
    kind: 'arp',
    status: 'done',
    phase: 'terminal',
    goal: 'Historical typed contract',
    started_at: 1_700_000_000,
    updated_at: 1_700_000_003,
    finished_at: 1_700_000_003,
    timeline: [{ type: 'arp_run_done', ts: 1_700_000_003, status: 'done' }],
    evidence: [],
    metrics: {
      decision_count: 2,
      tool_count: 3,
      vlm_call_count: 1,
      anchor_cache_hit_rate: 0.5,
      visual_servo_correction_count: 1,
    } as RunProjection['metrics'],
  };
}

function historicalContract(): TaskContract {
  return {
    format: 'task-contract/1',
    task_id: 'task-history-1',
    task: 'Historical typed contract',
    intent: {
      original_text: 'Historical typed contract',
      operation: ['capture', 'send'],
      media_type: 'image',
      channel: 'chat',
      recipient: 'recipient',
      source: 'active_window',
      capture_scope: 'window',
      destination: 'conversation',
      send_mode: 'attachment',
      subject: '',
      quality: 'normal',
      confidence: 1,
      ambiguous_fields: [],
      provenance: {},
    },
    stages: [],
    obligations: [],
    final_conditions: [],
    satisfiable: true,
    missing_capabilities: [],
    experience_policy: 'allow',
    compiler_version: 'test',
  };
}

describe('RunsView historical runtime detail', () => {
  it('renders an incomplete legacy intent without crashing the renderer', async () => {
    const run = historicalRun();
    const contract = historicalContract();
    delete (contract.intent as unknown as Record<string, unknown>).operation;
    delete (contract as unknown as Record<string, unknown>).missing_capabilities;
    const arp: ArpExecutionSnapshot = {
      run_id: run.run_id,
      graph_id: 'graph-history-incomplete',
      graph_name: run.goal,
      status: 'done',
      task_contract: contract,
      artifacts: { run_id: run.run_id, count: 0, artifacts: [] },
      contract_evidence: { run_id: run.run_id, count: 0, records: [] },
      stage_trace: [],
    };
    const client = { run: vi.fn(async () => ({ ok: true, run, arp })), artifactUrl: vi.fn() };
    const plane = {
      runs: [run], baseUrl: 'http://runtime/', currentRun: null, arpSnapshot: null,
      events: [], refresh: vi.fn(async () => undefined), getClient: () => client,
    };

    render(<RunsView plane={plane as never} />);

    expect(await screen.findByText('semantic')).toBeInTheDocument();
    expect(screen.getByLabelText('任务运行时审计')).toBeInTheDocument();
  });

  it('loads the selected run contract instead of showing only its projection', async () => {
    const run = historicalRun();
    const arp: ArpExecutionSnapshot = {
      run_id: run.run_id,
      graph_id: 'graph-history-1',
      graph_name: run.goal,
      status: 'done',
      task_contract: historicalContract(),
      artifacts: { run_id: run.run_id, count: 0, artifacts: [] },
      contract_evidence: { run_id: run.run_id, count: 0, records: [] },
      stage_trace: [],
    };
    const response: CurrentRunResponse = { ok: true, run, arp };
    const client = {
      run: vi.fn(async () => response),
      artifactUrl: vi.fn((runId: string, artifactId: string) => `http://runtime/${runId}/${artifactId}`),
    };
    const plane = {
      runs: [run],
      baseUrl: 'http://runtime/',
      currentRun: null,
      arpSnapshot: null,
      events: [],
      refresh: vi.fn(async () => undefined),
      getClient: () => client,
    };

    render(<RunsView plane={plane as never} />);

    await waitFor(() => expect(client.run).toHaveBeenCalledWith(run.run_id));
    expect(await screen.findByText('任务合同与阶段运行时')).toBeInTheDocument();
    expect(screen.getByText('capture → send')).toBeInTheDocument();
    expect(screen.getByLabelText('任务运行时审计')).toBeInTheDocument();
  });

  it('renders stage, capability, and completion audit restored from an older trace', async () => {
    const run = historicalRun();
    const arp: ArpExecutionSnapshot = {
      run_id: run.run_id,
      graph_id: 'graph-history-legacy',
      graph_name: run.goal,
      status: 'done',
      stage_trace: [{
        stage_id: 'stage-capture',
        stage_type: 'CaptureArtifact',
        status: 'done',
        tools: ['capture_artifact'],
        model_tools: [],
        controller_tools: ['capture_artifact'],
        required_capabilities: ['artifact.capture'],
        verifier_requirements: ['artifact.hash'],
        exposed_capabilities: ['artifact.capture', 'artifact.hash'],
        missing_capabilities: [],
        satisfiable: true,
        adapter: '',
        model_role: 'none',
        selected_evidence_providers: {},
        artifact_id: 'art-history',
        model_calls: 0,
        model_elapsed_ms: 0,
        controller_elapsed_ms: 14,
        capture_elapsed_ms: 7,
        failure_type: '',
        recovery: {},
        error: '',
        interaction_state: {},
      }],
      artifacts: { run_id: run.run_id, count: 0, artifacts: [] },
      contract_evidence: { run_id: run.run_id, count: 0, records: [] },
      completion_contract: {
        ok: true,
        run_id: run.run_id,
        contract_id: 'historical-contract',
        obligations: [],
        final_conditions: [],
        missing: [],
        contract_errors: [],
        artifact_refs: {},
        primary_artifact_id: 'art-history',
        reason: 'persisted completion evidence',
      },
    };
    const client = {
      run: vi.fn(async () => ({ ok: true, run, arp } satisfies CurrentRunResponse)),
      artifactUrl: vi.fn(),
    };
    const plane = {
      runs: [run], baseUrl: 'http://runtime/', currentRun: null, arpSnapshot: null,
      events: [], refresh: vi.fn(async () => undefined), getClient: () => client,
    };

    render(<RunsView plane={plane as never} />);

    expect(await screen.findByText('历史阶段运行时与证据')).toBeInTheDocument();
    expect(screen.getByText(/原始 trace 和受管理工件清单恢复/)).toBeInTheDocument();
    expect(screen.getAllByText('CaptureArtifact · stage-capture')).toHaveLength(2);
    expect(screen.getByText('persisted completion evidence')).toBeInTheDocument();
  });

  it('exposes correlated model reasoning and pointer-coordinate audit details', async () => {
    const run = historicalRun();
    run.timeline = [
      {
        type: 'vnext_model_reasoning',
        ts: 1_700_000_001,
        response_index: 2,
        summary: 'turn at x=490 before moving right',
      },
      {
        type: 'vnext_model_pointer_arguments',
        ts: 1_700_000_002,
        response_index: 2,
        summary: "[{'name': 'drag', 'arguments': {'points': '10,20 30,40'}}]",
      },
      {
        type: 'vnext_pointer_dispatch',
        ts: 1_700_000_003,
        summary: "{'submitted_image_size': [1120, 630], 'physical_arguments': {'x1': 17, 'y1': 34}}",
      },
    ];
    const arp: ArpExecutionSnapshot = {
      run_id: run.run_id,
      graph_id: 'graph-pointer-audit',
      graph_name: run.goal,
      status: 'done',
      artifacts: { run_id: run.run_id, count: 0, artifacts: [] },
      contract_evidence: { run_id: run.run_id, count: 0, records: [] },
      stage_trace: [],
    };
    const client = {
      run: vi.fn(async () => ({ ok: true, run, arp } satisfies CurrentRunResponse)),
      artifactUrl: vi.fn(),
    };
    const plane = {
      runs: [run], baseUrl: 'http://runtime/', currentRun: null, arpSnapshot: null,
      events: [], refresh: vi.fn(async () => undefined), getClient: () => client,
    };

    render(<RunsView plane={plane as never} />);

    expect(await screen.findByText('模型原始思考')).toBeInTheDocument();
    expect(screen.getAllByText('响应 2')).toHaveLength(2);
    expect(screen.getByText('展开原始思考')).toBeInTheDocument();
    expect(screen.getAllByText('展开完整坐标')).toHaveLength(2);
    expect(screen.getByText('turn at x=490 before moving right')).toBeInTheDocument();
  });
});
