import { describe, expect, it, vi } from 'vitest';

vi.mock('electron', () => ({
  app: { isPackaged: false },
}));

import { runtimeCompatibilityProblem } from '../../main/winpilotServer';

function compatibleRuntime() {
  return {
    runtime: {
      contract_version: 2,
      source: { loaded_stamp: 'same', current_stamp: 'same', drifted: false },
      git: { loaded_commit: 'abc', current_commit: 'abc' },
    },
  };
}

describe('WinPilot 托管服务兼容性提示', () => {
  it('使用中文说明运行时协议过期', () => {
    expect(runtimeCompatibilityProblem({ runtime: { contract_version: 1 } }))
      .toBe('托管的 Python 运行时协议已过期。');
  });

  it('使用中文说明运行时身份不完整', () => {
    expect(runtimeCompatibilityProblem({ runtime: { contract_version: 2 } }))
      .toBe('托管的 Python 运行时身份不完整。');
  });

  it('使用中文说明源码漂移', () => {
    const payload = compatibleRuntime();
    payload.runtime.source.current_stamp = 'changed';
    expect(runtimeCompatibilityProblem(payload))
      .toBe('托管的 Python 源码在启动后发生了变化。');
  });

  it('使用中文说明仓库提交不一致', () => {
    const payload = compatibleRuntime();
    payload.runtime.git.current_commit = 'def';
    expect(runtimeCompatibilityProblem(payload))
      .toBe('托管的 Python 进程来自不同的仓库提交。');
  });

  it('兼容时不返回错误', () => {
    expect(runtimeCompatibilityProblem(compatibleRuntime())).toBe('');
  });
});
