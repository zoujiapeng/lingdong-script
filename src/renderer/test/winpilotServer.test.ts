import { describe, expect, it, vi } from 'vitest';
import * as http from 'http';
import * as net from 'net';

vi.mock('electron', () => ({
  app: { isPackaged: false },
}));

import {
  findFreePort,
  isRendererSafePort,
  MIN_RENDERER_SAFE_PORT,
  rendererSafePortCandidates,
  runtimeCompatibilityProblem,
  waitReady,
} from '../../main/winpilotServer';

function compatibleRuntime() {
  return {
    runtime: {
      contract_version: 3,
      source: { loaded_stamp: 'same', current_stamp: 'same', drifted: false },
      git: { loaded_commit: 'abc', current_commit: 'abc' },
    },
  };
}

describe('WinPilot 托管服务兼容性提示', () => {
  it('启动前拒绝 Chromium 禁止和无效的端口', () => {
    expect(isRendererSafePort(6665)).toBe(false);
    expect(isRendererSafePort(10080)).toBe(false);
    expect(isRendererSafePort(MIN_RENDERER_SAFE_PORT)).toBe(true);
    expect(isRendererSafePort(49152)).toBe(true);
    expect(isRendererSafePort(65536)).toBe(false);
  });

  it('不依赖操作系统的低位动态端口范围', async () => {
    const candidates = rendererSafePortCandidates(7, 32);
    expect(candidates).toHaveLength(32);
    expect(new Set(candidates).size).toBe(32);
    expect(candidates.every(isRendererSafePort)).toBe(true);
    expect(candidates.every((port) => port >= 49152)).toBe(true);
    expect(candidates.some((port, index) => index > 0 && port !== candidates[index - 1] + 1)).toBe(true);

    const completeHighRange = rendererSafePortCandidates(7, 65_535 - 49_152 + 1);
    expect(new Set(completeHighRange).size).toBe(completeHighRange.length);

    const port = await findFreePort();
    expect(isRendererSafePort(port)).toBe(true);

    // findFreePort must resolve only after its probe listener is closed. A
    // caller should be able to bind the returned port immediately.
    await new Promise<void>((resolve, reject) => {
      const server = net.createServer();
      server.once('error', reject);
      server.listen(port, '127.0.0.1', () => {
        server.close((error) => {
          if (error) reject(error);
          else resolve();
        });
      });
    });
  });

  it('使用中文说明运行时协议过期', () => {
    expect(runtimeCompatibilityProblem({ runtime: { contract_version: 1 } }))
      .toBe('托管的 Python 运行时协议已过期。');
  });

  it('使用中文说明运行时身份不完整', () => {
    expect(runtimeCompatibilityProblem({ runtime: { contract_version: 3 } }))
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

  it('慢启动超时只安排一个后续探针且就绪后不残留重试', async () => {
    let requests = 0;
    const server = http.createServer((request, response) => {
      requests += 1;
      if (requests === 1) {
        // Leave the first response open until the client-side readiness
        // timeout destroys it, matching a temporarily stalled Python status
        // handler during a cold start.
        request.once('close', () => response.destroy());
        return;
      }
      response.writeHead(200, { 'Content-Type': 'application/json' });
      response.end(JSON.stringify(compatibleRuntime()));
    });
    const port = await new Promise<number>((resolve, reject) => {
      server.once('error', reject);
      server.listen(0, '127.0.0.1', () => {
        const address = server.address();
        if (typeof address === 'object' && address) resolve(address.port);
        else reject(new Error('test server did not bind a TCP port'));
      });
    });

    try {
      await waitReady(port, {
        timeoutMs: 500,
        requestTimeoutMs: 30,
        retryDelayMs: 10,
      });
      await new Promise((resolve) => setTimeout(resolve, 80));
      expect(requests).toBe(2);
    } finally {
      await new Promise<void>((resolve) => server.close(() => resolve()));
    }
  });
});
