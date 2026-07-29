import { readFileSync } from 'node:fs';
import path from 'node:path';
// @ts-expect-error jsdom 29 does not publish TypeScript declarations.
import { JSDOM } from 'jsdom';
import { expect, test, vi } from 'vitest';

const targetHtml = readFileSync(
  path.resolve(
    process.cwd(),
    'python/winpilot_home/eval_targets/visual_interaction_target.html',
  ),
  'utf8',
);

test('visual target reveals success and failure feedback after interaction', () => {
  const scrollIntoView = vi.fn();
  const dom = new JSDOM(targetHtml, {
    runScripts: 'dangerously',
    pretendToBeVisual: true,
    url: 'http://127.0.0.1/visual_interaction_target.html?case=visibility&scenario=precise_click&seed=1',
    beforeParse(window: Window & typeof globalThis) {
      window.fetch = vi.fn(async () => ({
        ok: true,
        json: async () => ({}),
      })) as unknown as typeof window.fetch;
      window.HTMLElement.prototype.scrollIntoView = scrollIntoView;
    },
  });
  const targetWindow = dom.window as unknown as {
    markSuccess: (message: string, event: object, details: object) => void;
    markFailure: (message: string, event: object, details: object) => void;
  };

  targetWindow.markSuccess('CLICK_SUCCEEDED', { isTrusted: true }, {});
  targetWindow.markFailure('WRONG_OBJECT_CLICKED', { isTrusted: true }, {});

  expect(scrollIntoView).toHaveBeenCalledTimes(2);
  expect(scrollIntoView).toHaveBeenNthCalledWith(1, {
    block: 'nearest',
    inline: 'nearest',
  });
  expect(scrollIntoView).toHaveBeenNthCalledWith(2, {
    block: 'nearest',
    inline: 'nearest',
  });

  dom.window.close();
});
