import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { AppErrorBoundary } from '../components/common/AppErrorBoundary';

function BrokenView(): never {
  throw new Error('malformed runtime snapshot');
}

describe('AppErrorBoundary', () => {
  it('shows a recoverable error instead of an empty renderer root', () => {
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    render(<AppErrorBoundary><BrokenView /></AppErrorBoundary>);
    expect(screen.getByRole('alert')).toHaveTextContent('界面暂时无法显示');
    expect(screen.getByText('malformed runtime snapshot')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /重新加载界面/ })).toBeInTheDocument();
    consoleError.mockRestore();
  });
});
