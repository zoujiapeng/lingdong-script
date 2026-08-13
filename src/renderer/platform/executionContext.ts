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
 * assigns a run or task identity. Hiding the cockpit is a convenience for the
 * model's first frame, never authority over whether the desktop may be used.
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
    // The backend returns a neutral full-desktop anchor. Capture it even when
    // Electron could not yield focus; that telemetry is useful to the model
    // and diagnostics but must not become an execution gate.
    if (captureTarget) targetWindow = await captureTarget(handoff);
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
