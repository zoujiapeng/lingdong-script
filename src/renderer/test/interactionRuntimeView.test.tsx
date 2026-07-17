import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import {
  InteractionRuntimePanel,
  TaskRuntimeAuditPanel,
  interactionRuntimeView,
  stageRuntimeRows,
} from '../components/platform/PlatformWorkbench';
import type {
  ArpExecutionSnapshot,
  InteractionRuntimeState,
  RuntimeEvent,
  TaskContract,
} from '../platform/contracts';

function interactiveContract(): TaskContract {
  return {
    format: 'task-contract/1',
    task_id: 'task-interaction',
    task: 'observe the shared interface and take my turn',
    intent: {
      original_text: 'observe the shared interface and take my turn',
      operation: [],
      media_type: 'none',
      channel: '',
      recipient: '',
      source: 'active_window',
      capture_scope: 'window',
      destination: '',
      send_mode: 'send',
      subject: '',
      quality: 'normal',
      confidence: 0.92,
      ambiguous_fields: [],
      provenance: {},
      interaction: {
        active: true,
        mode: 'turn_based',
        actors: [],
        role_assignments: {},
        turn_owner: 'agent',
        session_mode: 'persistent',
        continuation_policy: 'wait_for_external_change',
        target: 'active_window',
        region_hint: 'existing_shared_state',
        action_modalities: ['infer_from_scene'],
        user_started: true,
        expected_external_change: true,
        confidence: 0.92,
        signals: ['shared_session', 'live_visual_state'],
        ambiguous_fields: [],
        provenance: {},
      },
    },
    session: {
      session_id: 'interaction-task-interaction',
      mode: 'turn_based',
      lifecycle: 'persistent',
      actors: [],
      role_assignments: {},
      initial_turn_owner: 'agent',
      target: 'active_window',
      authoritative_state_ref: 'interaction_scene',
      interaction_region_ref: 'interaction_region',
      continuation_policy: 'wait_for_external_change',
      max_agent_turns_per_activation: 1,
      wait_for_external_change: true,
    },
    stages: [{
      id: 'stage-wait',
      type: 'WaitForExternalChange',
      name: 'wait for external change',
      parameters: { watch_scope: 'interaction_region' },
      depends_on: [],
      inputs: [],
      outputs: [],
      state_inputs: [{
        name: 'interaction_region', state_type: 'bound_interaction_region', source: 'active_window',
        scope: 'existing_shared_state', required: true, ephemeral: true,
      }],
      state_outputs: [],
      required_capabilities: [],
      obligations: [],
      execution: null,
      allow_skill_replay: false,
    }],
    obligations: [],
    final_conditions: [],
    satisfiable: true,
    missing_capabilities: [],
    experience_policy: 'disabled',
    compiler_version: 'typed-stage-2',
  };
}

function snapshot(values: Partial<ArpExecutionSnapshot> = {}): ArpExecutionSnapshot {
  return {
    run_id: 'arp-interaction',
    graph_id: 'graph-interaction',
    graph_name: 'interactive graph',
    status: 'running',
    task_contract: interactiveContract(),
    current_stage: interactiveContract().stages[0],
    ...values,
  };
}

describe('interactive task contract runtime view', () => {
  it('renders the authoritative target, region, turn, actions, verification, wait, and failure stage', () => {
    const state: InteractionRuntimeState = {
      session_id: 'interaction-task-interaction',
      status: 'waiting_external_change',
      mode: 'turn_based',
      frame_id: 42,
      model_calls: 1,
      baseline_artifact_id: 'frame-before',
      current_artifact_id: 'frame-after',
      target_binding: {
        hwnd: 7712, title: 'Shared visual task', capture_scope: 'target_window', frame_id: 42,
      },
      interaction_region: {
        bounds: [120, 180, 820, 900], coordinate_space: 'frame_0_1000', label: 'interaction_surface',
        confidence: 0.96, policy: 'stable_binding_retained_after_detector_jitter', generation: 3,
      },
      turn_owner: 'agent',
      planned_action: { modality: 'drag', summary: 'drag the visible token', target: 'open slot' },
      executed_action: { action: 'drag', summary: 'drag completed', transaction_id: 'tx-7' },
      local_verification: { verified: true, provider: 'local_semantic_effect_verifier', reason: 'token moved inside ROI' },
      external_change: { armed: true, waiting: true, watch_scope: 'interaction_region' },
      failure_stage: {
        stage_id: 'stage-perform', stage_type: 'PerformTurn', failure_type: 'target_moved', error: 'target window changed',
      },
    };
    const view = interactionRuntimeView(snapshot({ interaction_state: state }), []);

    expect(view).toMatchObject(state);
    render(<InteractionRuntimePanel state={view!} />);

    expect(screen.getByLabelText('实时视觉互动闭环')).toBeInTheDocument();
    expect(screen.getByText('Shared visual task')).toBeInTheDocument();
    expect(screen.getByText(/\[120, 180, 820, 900\]/)).toBeInTheDocument();
    expect(screen.getByText('drag the visible token → open slot')).toBeInTheDocument();
    expect(screen.getByText('drag completed')).toBeInTheDocument();
    expect(screen.getByText('已通过')).toBeInTheDocument();
    expect(screen.getByText('等待外部参与者操作')).toBeInTheDocument();
    expect(screen.getByText('Agent 本回合已验证，轮到外部参与者')).toBeInTheDocument();
    expect(screen.getByRole('alert')).toHaveTextContent('stage-perform · PerformTurn');
    expect(screen.getByRole('alert')).toHaveTextContent('target_moved：target window changed');
  });

  it('distinguishes a verified agent turn awaiting a participant from terminal success', () => {
    const view = interactionRuntimeView(snapshot({
      interaction_state: {
        status: 'waiting_external',
        local_verification: { verified: true, reason: 'agent turn committed' },
        external_change: {
          armed: true,
          waiting: true,
          changed: false,
          requires_external_participant: true,
          watch_scope: 'interaction_region',
        },
      },
    }), []);

    render(<InteractionRuntimePanel state={view!} />);

    expect(screen.getByText('等待外部参与者')).toBeInTheDocument();
    expect(screen.getByText('等待外部参与者操作')).toBeInTheDocument();
    expect(screen.getByText((_, element) => (
      element?.tagName === 'SMALL'
      && element.textContent?.includes('持续会话尚未终结') === true
    ))).toBeInTheDocument();
    expect(screen.queryByText('已完成')).not.toBeInTheDocument();
  });

  it('derives a compatible live view from typed stages and runtime events when the snapshot is old', () => {
    const events: RuntimeEvent[] = [
      {
        type: 'interaction_scene_bound', run_id: 'arp-interaction',
        target_binding: { hwnd: 88, title: 'Bound target', frame_id: 8 },
        interaction_region: { bounds: [10, 20, 900, 950], coordinate_space: 'frame_0_1000' },
      },
      {
        type: 'interaction_action_planned', run_id: 'arp-interaction',
        planned_action: { modality: 'click', summary: 'choose the legal target' },
      },
      {
        type: 'interaction_action_executed', run_id: 'arp-interaction',
        executed_action: { action: 'click', summary: 'target clicked' },
      },
      {
        type: 'interaction_local_effect_verified', run_id: 'arp-interaction',
        verified: true, provider: 'roi_verifier', reason: 'bound ROI changed',
      },
      {
        type: 'interaction_wait_external_change', run_id: 'arp-interaction',
        armed: true, waiting: true, watch_scope: 'interaction_region',
      },
    ];

    const view = interactionRuntimeView(snapshot(), events);

    expect(view).toMatchObject({
      session_id: 'interaction-task-interaction',
      turn_owner: 'agent',
      target_binding: { hwnd: 88, title: 'Bound target', frame_id: 8 },
      interaction_region: { bounds: [10, 20, 900, 950], coordinate_space: 'frame_0_1000' },
      planned_action: { modality: 'click', summary: 'choose the legal target' },
      executed_action: { action: 'click', summary: 'target clicked' },
      local_verification: { verified: true, provider: 'roi_verifier', reason: 'bound ROI changed' },
      external_change: { armed: true, waiting: true, changed: false, watch_scope: 'interaction_region' },
    });
    expect(view?.state_bindings?.map((item) => item.name)).toContain('interaction_region');
  });

  it('keeps the last verified mutation visible after a terminal no-action plan', () => {
    const view = interactionRuntimeView(snapshot({
      interaction_state: {
        status: 'completed',
        planned_action: { summary: 'no further action', actions: [] },
        executed_action: { deferred: true, reason: 'already complete' },
        local_verification: { verified: true, reason: 'terminal state confirmed' },
        last_mutating_plan: { modality: 'drag', summary: 'move the visible object', target: 'destination' },
        last_executed_action: { action: 'drag', summary: 'drag transaction completed', transaction_id: 'tx-final' },
        last_local_verification: { verified: true, reason: 'requested state is visibly complete' },
      },
    }), []);

    expect(view?.planned_action).toMatchObject({ summary: 'move the visible object' });
    expect(view?.executed_action).toMatchObject({ transaction_id: 'tx-final' });
    expect(view?.local_verification).toMatchObject({ reason: 'requested state is visibly complete' });
  });

  it('does not invent an interaction session for ordinary artifact tasks', () => {
    const ordinary = interactiveContract();
    ordinary.intent.interaction = undefined;
    ordinary.session = null;
    ordinary.stages = [];

    expect(interactionRuntimeView(snapshot({ task_contract: ordinary, current_stage: null }), [])).toBeNull();
  });

  it('merges typed stages with live capability, timing, artifact, evidence, budget, and completion data', () => {
    const contract = interactiveContract();
    contract.stages[0].execution = {
      required_capabilities: ['interaction.wait_external'],
      verifier_requirements: ['visual.change.verify'],
      exposed_capabilities: ['interaction.wait_external', 'visual.change.verify'],
      missing_capabilities: [],
      tools: ['wait_for_visual_change'],
      model_tools: [],
      controller_tools: ['wait_for_visual_change'],
      controller: 'stage_runtime+local_controller',
      adapter: '',
      model_role: 'none',
      satisfiable: true,
      expansions: [],
      selected_evidence_providers: { 'interaction.external_change_observed': 'persistent_change_watcher' },
    };
    contract.obligations = [{
      id: 'interaction.external_change_observed',
      predicate: 'interaction.external_change_observed',
      description: 'the bound region changed persistently',
      stage_id: 'stage-wait',
      artifact_ref: '',
      recipient: '',
      evidence_providers: [{ id: 'persistent_change_watcher', capabilities: ['visual.change.verify'] }],
    }];
    contract.final_conditions = ['interaction.external_change_observed'];

    const arp = snapshot({
      task_contract: contract,
      current_stage: contract.stages[0],
      interaction_state: {
        status: 'waiting_external_change',
        model_calls: 3,
        turn_index: 2,
        replan_count: 1,
        budget: {
          model_calls_used: 3, model_calls_limit: 12,
          semantic_turns_used: 2, semantic_turns_limit: 6,
          replans_used: 1, replans_limit: 3,
        },
      },
      history: [{
        kind: 'task_stage', node_id: 'stage-wait', ok: false, attempts: 2,
        output: {
          stage_id: 'stage-wait', stage_type: 'WaitForExternalChange', status: 'failed',
          model_elapsed_ms: 1250, controller_elapsed_ms: 340, capture_elapsed_ms: 80,
        },
      }],
      stage_trace: [{
        stage_id: 'stage-wait', stage_type: 'WaitForExternalChange', status: 'failed', attempts: 2,
        tools: ['wait_for_visual_change'], model_tools: [], controller_tools: ['wait_for_visual_change'],
        required_capabilities: ['interaction.wait_external'], verifier_requirements: ['visual.change.verify'],
        exposed_capabilities: ['interaction.wait_external', 'visual.change.verify'], missing_capabilities: [],
        satisfiable: true, adapter: '', model_role: 'none', artifact_id: 'frame-after', model_calls: 1,
        selected_evidence_providers: { 'interaction.external_change_observed': 'persistent_change_watcher' },
        controller_elapsed_ms: 340, failure_type: 'external_change_timeout', recovery: { strategy: 'stop' },
        error: 'no persistent change before timeout',
      }],
      artifacts: {
        run_id: 'arp-interaction', count: 1, artifacts: [{
          artifact_id: 'frame-after', run_id: 'arp-interaction', managed_path: 'C:/managed/frame.png',
          sha256: 'a'.repeat(64), mime_type: 'image/png', width: 800, height: 600, size_bytes: 4096,
          source_window: 'Shared visual task', source_hwnd: 7712, source_scope: 'target_window',
          created_at: 1_700_000_000, purpose: 'observation', sendable: false,
        }],
      },
      contract_evidence: {
        run_id: 'arp-interaction', count: 1, records: [{
          id: 'evidence-1', predicate: 'interaction.external_change_observed',
          source: 'persistent_change_watcher', provider: 'persistent_change_watcher', verified: false,
          stage_id: 'stage-wait', artifact_id: 'frame-after', recipient: '', run_id: 'arp-interaction',
          captured_at: 1_700_000_001, metadata: { stable: false }, text: 'transient change rejected',
        }],
      },
      completion_contract: {
        ok: false, run_id: 'arp-interaction', contract_id: 'task-interaction',
        obligations: [{
          obligation_id: 'interaction.external_change_observed',
          predicate: 'interaction.external_change_observed',
          description: 'the bound region changed persistently', satisfied: false,
          provider: '', evidence_id: '', stage_id: 'stage-wait', artifact_ref: '', artifact_id: '',
        }],
        final_conditions: [{ condition: 'interaction.external_change_observed', satisfied: false, evidence_id: '' }],
        missing: [{
          obligation_id: 'interaction.external_change_observed',
          predicate: 'interaction.external_change_observed',
          description: 'the bound region changed persistently', satisfied: false,
          provider: '', evidence_id: '', stage_id: 'stage-wait', artifact_ref: '', artifact_id: '',
        }],
        contract_errors: [], artifact_refs: {}, primary_artifact_id: '',
        reason: 'missing task-contract evidence: interaction.external_change_observed',
      },
    });

    const rows = stageRuntimeRows(contract, arp);
    expect(rows[0]).toMatchObject({
      stage_id: 'stage-wait', status: 'failed', model_elapsed_ms: 1250,
      controller_elapsed_ms: 340, capture_elapsed_ms: 80, evidence_count: 1,
      failure_type: 'external_change_timeout',
      selected_evidence_providers: {
        'interaction.external_change_observed': 'persistent_change_watcher',
      },
    });

    render(
      <TaskRuntimeAuditPanel
        contract={contract}
        arp={arp}
        artifactUrl={(artifactId) => `http://runtime.test/artifacts/${artifactId}`}
      />,
    );
    expect(screen.getByLabelText('任务运行时审计')).toBeInTheDocument();
    expect(screen.getAllByText(/WaitForExternalChange · stage-wait/).length).toBeGreaterThan(0);
    expect(screen.getByText('2 暴露 / 2 必需')).toBeInTheDocument();
    expect(screen.getByText('模型 3/12 · 轮次 2/6 · 重规划 1/3')).toBeInTheDocument();
    expect(screen.getByLabelText('stage-wait timing')).toHaveTextContent(
      '模型 1250ms · Controller 340ms · Capture 80ms · 1 次调用',
    );
    expect(screen.getByRole('alert')).toHaveTextContent(
      'external_change_timeout：no persistent change before timeout',
    );
    expect(screen.getByRole('alert')).toHaveTextContent('恢复策略：{"strategy":"stop"}');
    expect(screen.getByText('frame-after')).toBeInTheDocument();
    expect(screen.getByText('观察帧')).toBeInTheDocument();
    expect(screen.getByRole('img', { name: '观察帧 frame-after' })).toHaveAttribute(
      'src',
      'http://runtime.test/artifacts/frame-after',
    );
    expect(screen.getAllByText('interaction.external_change_observed').length).toBeGreaterThan(0);
    expect(screen.getByText(/persistent_change_watcher · stage stage-wait/)).toBeInTheDocument();
    expect(screen.getByText(/interaction.external_change_observed → persistent_change_watcher/)).toBeInTheDocument();
    expect(screen.getAllByText('未通过 · 缺少 1').length).toBeGreaterThan(0);
    expect(screen.getByText(/missing task-contract evidence/)).toBeInTheDocument();
  });

  it('normalizes a string failure stage emitted by an in-flight runtime', () => {
    const view = interactionRuntimeView(snapshot({
      interaction_state: {
        current_stage_id: 'stage-wait',
        failure_stage: 'WaitForExternalChange.watch',
      },
    }), []);

    expect(view?.failure_stage).toMatchObject({
      stage_id: 'stage-wait',
      stage_type: 'WaitForExternalChange',
      failure_type: 'WaitForExternalChange.watch',
    });
  });
});
