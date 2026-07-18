import type { DesktopTargetContext, TaskContract, TaskStageType } from './contracts';

export interface DesktopHandoffResult {
  yielded: boolean;
  control_surface_pid: number;
  prepared_at: number;
  reason?: string;
}

const DESKTOP_STAGE_TYPES = new Set<TaskStageType>([
  'OpenApplication',
  'FocusWindow',
  'ExecuteDesktopTask',
  'CreateOrEditArtifact',
  'DrawArtifact',
  'CaptureArtifact',
  'SaveArtifact',
  'OpenConversation',
  'ResolveRecipient',
  'SendText',
  'SendArtifact',
  'VerifyArtifact',
  'VerifyDelivery',
  'ObserveScene',
  'InferInteraction',
  'PerformTurn',
  'VerifyLocalEffect',
  'WaitForExternalChange',
]);

const DESKTOP_CAPABILITY_PREFIXES = [
  'desktop.',
  'app.',
  'artifact.',
  'communication.',
  'controller.',
  'delivery.',
  'drawing.',
  'interaction.',
  'visual.',
  'window.',
] as const;

function isDesktopCapability(capability: string): boolean {
  return DESKTOP_CAPABILITY_PREFIXES.some((prefix) => capability.startsWith(prefix));
}

/**
 * Decide from the compiler-owned contract whether the native workbench must
 * yield foreground ownership before execution.  This deliberately consumes
 * typed stages/capabilities instead of reparsing the user's words.
 */
export function requiresDesktopHandoff(contract: TaskContract): boolean {
  if (contract.session || contract.intent?.interaction?.active) return true;
  return contract.stages.some((stage) => {
    if (DESKTOP_STAGE_TYPES.has(stage.type)) return true;
    const capabilities = [
      ...(stage.required_capabilities || []),
      ...(stage.execution?.required_capabilities || []),
      ...(stage.execution?.verifier_requirements || []),
    ];
    return capabilities.some(isDesktopCapability);
  });
}

export async function buildClientRunInputs(
  contract: TaskContract,
  submittedAt = Date.now(),
  captureTarget?: (handoff: DesktopHandoffResult) => Promise<DesktopTargetContext>,
): Promise<Record<string, unknown>> {
  const desktopHandoffRequired = requiresDesktopHandoff(contract);
  const bridge = window.electronAPI;
  let handoff: DesktopHandoffResult | null = null;
  let targetWindow: DesktopTargetContext | null = null;

  if (desktopHandoffRequired && bridge?.prepareDesktopHandoff) {
    handoff = await bridge.prepareDesktopHandoff();
    if (!handoff.yielded) {
      throw new Error(
        `客户端未能让出桌面前台控制权：${handoff.reason || 'foreground handoff failed'}`,
      );
    }
    if (!captureTarget) {
      throw new Error('客户端已让出前台，但没有可用的桌面目标捕获器。');
    }
    targetWindow = await captureTarget(handoff);
    if (!targetWindow.usable || !targetWindow.hwnd) {
      throw new Error(
        `客户端未能绑定让出前台后的目标窗口：${targetWindow.reason || targetWindow.failure_type || 'target unavailable'}`,
      );
    }
  }

  return {
    execution_context: {
      format: 'execution-context/1',
      entrypoint: bridge ? 'native_agent_cockpit' : 'web_workbench',
      task_id: contract.task_id,
      submitted_at: submittedAt,
      desktop_handoff: {
        required: desktopHandoffRequired,
        strategy: desktopHandoffRequired
          ? 'foreground_after_control_surface_yield'
          : 'not_required',
        yielded: handoff?.yielded ?? false,
        control_surface_pid: handoff?.control_surface_pid ?? 0,
        prepared_at: handoff?.prepared_at ?? 0,
        reason: handoff?.reason || (bridge ? '' : 'native_bridge_unavailable'),
      },
      target_window: targetWindow,
    },
  };
}
