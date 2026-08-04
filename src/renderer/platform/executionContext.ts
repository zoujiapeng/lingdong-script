import type {
  ClientExecutionContext,
  DesktopTargetContext,
} from './contracts';

export interface DesktopHandoffResult {
  yielded: boolean;
  control_surface_pid: number;
  prepared_at: number;
  reason?: string;
}

/**
 * Build the context owned by the native Agent workbench before the backend
 * assigns a run or task identity. The primary goal entrypoint always yields
 * foreground ownership and captures the resulting external target first.
 */
export async function buildClientExecutionContext(
  submittedAt = Date.now(),
  captureTarget?: (handoff: DesktopHandoffResult) => Promise<DesktopTargetContext>,
): Promise<ClientExecutionContext> {
  const bridge = window.electronAPI;
  let handoff: DesktopHandoffResult | null = null;
  let targetWindow: DesktopTargetContext | null = null;

  if (bridge?.prepareDesktopHandoff) {
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
    format: 'execution-context/1',
    entrypoint: bridge ? 'native_agent_cockpit' : 'web_workbench',
    submitted_at: submittedAt,
    desktop_handoff: {
      required: true,
      strategy: 'foreground_after_control_surface_yield',
      yielded: handoff?.yielded ?? false,
      control_surface_pid: handoff?.control_surface_pid ?? 0,
      prepared_at: handoff?.prepared_at ?? 0,
      reason: handoff?.reason || (bridge ? '' : 'native_bridge_unavailable'),
    },
    target_window: targetWindow,
  };
}
