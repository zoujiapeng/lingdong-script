import * as path from 'path';

/** Resolve a legacy macro asset without allowing callers to leave macrosDir. */
export function resolveMacroPath(macrosDir: string, requestedName: string): string {
  const raw = String(requestedName || '').trim();
  if (!raw || raw.includes('\0')) throw new Error('无效的宏名称');
  const file = raw.toLowerCase().endsWith('.json') ? raw : `${raw}.json`;
  if (path.basename(file) !== file || file === '.' || file === '..') {
    throw new Error('宏名称不能包含目录路径');
  }
  const root = path.resolve(macrosDir);
  const resolved = path.resolve(root, file);
  if (path.dirname(resolved) !== root || path.extname(resolved).toLowerCase() !== '.json') {
    throw new Error('宏文件必须是 macros 目录内的 JSON 文件');
  }
  return resolved;
}
