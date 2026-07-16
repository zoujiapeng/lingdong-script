import { Component, type ErrorInfo, type ReactNode } from 'react';
import { AlertTriangle, RefreshCw } from 'lucide-react';

interface AppErrorBoundaryProps {
  children: ReactNode;
}

interface AppErrorBoundaryState {
  error: Error | null;
}

/** Keep one malformed runtime snapshot from turning the entire Electron window blank. */
export class AppErrorBoundary extends Component<AppErrorBoundaryProps, AppErrorBoundaryState> {
  state: AppErrorBoundaryState = { error: null };

  static getDerivedStateFromError(error: Error): AppErrorBoundaryState {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('[renderer] uncaught view error', error, info.componentStack);
  }

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <main className="arp-crash-fallback" role="alert">
        <AlertTriangle size={28} />
        <h1>界面暂时无法显示</h1>
        <p>运行数据中存在无法显示的内容。任务运行时不会因此被当作成功。</p>
        <code>{this.state.error.message || 'unknown renderer error'}</code>
        <button type="button" onClick={() => window.location.reload()}>
          <RefreshCw size={14} />重新加载界面
        </button>
      </main>
    );
  }
}
