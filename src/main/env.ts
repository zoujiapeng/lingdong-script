/**
 * 环境配置 — isDev / getBaseDir / 模板路径
 */
import * as path from 'path';
import { app } from 'electron';

// app.isPackaged 是 Electron 官方的打包检测方式
export const isDev = !app.isPackaged || process.argv.includes('--dev');

/** 项目根目录（开发时为 __dirname/../..，打包后为 process.resourcesPath） */
export function getBaseDir(): string {
  return isDev ? path.join(__dirname, '../..') : process.resourcesPath;
}

/** 获取模板目录路径 */
export function getTemplatesDir(): string {
  return path.join(getBaseDir(), 'templates');
}
