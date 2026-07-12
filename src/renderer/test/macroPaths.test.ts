import { describe, expect, it } from 'vitest';
import * as path from 'path';
import { resolveMacroPath } from '../../main/macroPaths';

describe('resolveMacroPath', () => {
  const root = path.resolve('C:/tmp/lingdong/macros');

  it('keeps a normal macro inside the macro directory', () => {
    expect(resolveMacroPath(root, 'demo')).toBe(path.join(root, 'demo.json'));
    expect(resolveMacroPath(root, 'demo.json')).toBe(path.join(root, 'demo.json'));
  });

  it('rejects traversal and absolute paths', () => {
    for (const name of ['../secret', '..\\secret', 'C:\\secret', 'sub/demo']) {
      expect(() => resolveMacroPath(root, name)).toThrow();
    }
  });
});
