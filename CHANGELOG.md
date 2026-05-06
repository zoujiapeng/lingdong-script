# 更新日志

## [1.0.1] - 2026-05-06

### 架构优化 (11 项)

#### P0 — 线上问题修复
- **前端渲染风暴**: 全局 `useStore()` 解构 → 精确选择器 + `React.memo`，消除 500+ 节点时的级联重绘
- **Python 子进程无限制创建**: 新增 `spawnPythonOneShot` 队列，最大并发 3，排队等待
- **调度器守护进程崩溃静默死亡**: 新增 `_stopping` 标志 + crash 检测 + 2s 自动重启
- **视觉条件阻塞 Tick 循环**: 改用 `ThreadPoolExecutor` + 30s 超时，单个慢条件不再阻塞全部调度
- **`/api/get-state` 永远返回错误**: 修复 JS 值捕获 bug，`cachedRendererState` 现在正确引用最新状态
- **启动调度 Race Condition**: `executeJavaScript` Promise 回调前同步代码不再执行

#### P1 — 性能优化
- **Python 冷启动**: `_all.py` 30 个 `import *` 改为懒加载，冷启动 2-8s → 20ms (400x)
- **同步文件 I/O**: `readFileSync` / `writeFileSync` 热路径改为 `fsp` 异步版本

#### P2 — 代码质量
- **`state.ts` 死代码**: 54 行 → 22 行，移除 7 组未用导出
- **`isDev` 检测**: `!process.resourcesPath` → `!app.isPackaged` (Electron 标准 API)
- **`schedulerService.ts` (冗余 JS 版)**: 删除（Python `scheduler_daemon.py` 是实际在用的）
- **宏录制 stdout 缓冲区**: 新增 64KB 上限

### 全面测试与修复 (第二轮)

#### 新增 API 端点
- **`/api/check-params`**: 检查节点是否缺少必填参数（click 缺 x/y、input_text 缺 text 等）
- **`/api/update-node-params`**: 仅更新节点参数，不影响位置等其他属性
- **`/api/create-flow`** 支持 `description` 参数: 没有 DSL 时先调 LLM 生成 DSL 再转流程图

#### Bug 修复
- **便携版 `list-templates` 读取失败**: `templates` 目录不存在时返回空列表而非 500 错误
- **便携版缺失模板**: `templates/` 加入 `extraResources`，打包时自动包含

#### 稳定性增强
- **全局未捕获异常处理**: 添加 `process.on('uncaughtException')` 和 `unhandledRejection`，防止异步错误静默崩溃 Electron
- **死代码导出清理**: `matchCron` / `runDsl`(engine.ts) / `getTemplatesDir`(env.ts) 移除未用导出

#### 前端性能
- **Vite 代码分割**: `manualChunks` 将 React/lucide-react 拆为独立 chunk
  - `index.js`: 1,176 KB → 298 KB (-75%)
  - `react-vendor.js`: 131 KB (缓存稳定)
  - `icons.js`: 728 KB (lucide-react 升级时才变)
  - 三个 chunk 并行加载，利用 HTTP/2 多路复用

#### 文档修正
- `npm test` 引用 → 移除（暂无前端测试脚本）
- CLI 路径: `node cli/lobster.js` → `python cli/lobster.py`
- 测试数量: 88 → 102
- HTTP API 端点表补充 3 个新端点

### 测试结果
```
TypeScript tsc --noEmit:     0 errors
Vite build:                  成功 (1.98s)
Python tests:                102/102 passed
NLP schedule tests:          66/66 passed
HTTP API endpoints:          29/29 正常响应
Portable build + test:       8/8 通过
```
