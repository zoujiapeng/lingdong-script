import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { AgentCockpit } from '../components/platform/PlatformWorkbench';

describe('AgentCockpit runtime canary selector', () => {
  afterEach(() => {
    localStorage.clear();
  });

  it('defaults to legacy and submits an explicitly selected vNext run', async () => {
    const startGoal = vi.fn(async () => ({ run_id: 'arp-1' }));
    const plane = {
      currentRun: null,
      arpSnapshot: null,
      overview: null,
      events: [],
      baseUrl: '',
      runtimeIssue: '',
      startGoal,
    };
    const user = userEvent.setup();

    render(<AgentCockpit plane={plane as never} />);

    const selector = screen.getByLabelText('实验运行时');
    const startPaused = screen.getByRole('checkbox', {
      name: '启动后暂停（确认环境后继续）',
    });
    expect(selector).toHaveValue('legacy');
    expect(startPaused).not.toBeChecked();
    expect(screen.getByRole('option', { name: 'Legacy（默认）' })).toBeInTheDocument();
    expect(screen.getByRole('option', { name: 'vNext（实验）' })).toBeInTheDocument();

    await user.type(
      screen.getByPlaceholderText('描述希望代理在这台电脑上完成的目标……'),
      '完成当前界面上的测试。',
    );
    await user.selectOptions(selector, 'vnext');
    await user.click(startPaused);
    await user.click(screen.getByRole('button', { name: '运行任务' }));

    await waitFor(() => expect(startGoal).toHaveBeenCalledWith(
      '完成当前界面上的测试。', 40, false, 'vnext', true,
    ));
  });
});
