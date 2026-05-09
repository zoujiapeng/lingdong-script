# 更新日志

## [1.1.1] - 2026-05-09

### Agent 执行稳定性修复

#### Bug 修复
- **类型处理阻塞** — `type` 步骤包装守护线程 + 8s 超时，防止 pyautogui.typewrite 在特定窗口下永久阻塞
- **快捷键阻塞** — `hotkey` 步骤包装守护线程 + 5s 超时
- **保存对话框阻塞** — `file_save` 步骤包装守护线程 + 12s 超时；`_auto_handle_save_dialog` 15s 超时
- **焦点窗口处理** — `focus` 步骤增加 `target`/`name` 回退字段，聚焦后增加 sleep 等待窗口就绪
- **Agent 安全超时** — 从 90s 提升至 180s，适配复杂多步骤流程

#### 本地步骤生成优化
- **APP_MAP 中文映射** — `记事本` → `notepad.exe`，`计算器` → `calc.exe` 等，修复未找到可执行文件错误
- **步骤顺序保留** — 三段式 `finditer` + 位置排序，保持原始目标描述中的步骤顺序
- **中文正则修复** — 修复贪婪匹配捕获不完整中文词组的问题
- **类型参数修复** — `re.compile(pattern, goal)` → `re.compile(pattern)`，修复字符串被误当 flags 参数的错误

#### 文档修正
- **API 端口 5577 → 5877** — 全面更新 README.md、CLAUDE.md、cli/lobster.py 及 Python feature 中的过时端口引用（共 20+ 处）
- **补充缺失 API 端点** — 新增 `/api/agent-start`、`/api/features/list`、`/api/features/call`、`/api/search-templates`、`/api/execute-js` 文档
- **端点计数更新** — 45+ → 50+

## [1.1.0] - 2026-05-08

### AI Agent 监督执行架构

#### 新增模块
- **`python/agent/pseudocode.py`** — 伪代码解析器，将自然语言生成的脚本解析为结构化步骤（click/type/hotkey/wait/scroll/launch/focus/run_command 等 11 种类型）
- **`python/agent/controller.py`** — ReAct 循环控制器 + 监督执行器，三阶段架构：生成脚本 → 解析步骤 → 逐步骤监督执行
- **`python/agent/planner.py`** — LLM 决策引擎（DeepSeek），支持 `decide_recovery()` 失败恢复策略（retry/alternative/skip/fail）
- **`python/agent/memory.py`** — 短期/长期记忆系统

#### 监督执行流程
Agent 不再生成脚本后退出，改为 stay-alive 逐步骤执行：
1. **窗口焦点安全** — 每步执行前调用 `get_active_window` 检查目标窗口，防止误输入
2. **动作执行** — 通过 feature registry 调用 click/type/hotkey/launch 等操作
3. **屏幕稳定等待** — `wait_screen_stable` 确保操作后的界面变化完成
4. **OCR 验证** — 输入后 OCR 确认文字出现，启动后检测屏幕变化，聚焦后验证窗口标题
5. **失败恢复** — 内置规则（超时/未找到自动重试）+ planner.decide_recovery() 复杂决策

#### 恢复策略
- **retry** — 超时/未找到/启动失败等瞬时错误自动重试
- **alternative** — LLM 提供替代方案执行
- **skip** — 跳过当前步骤继续（如 OCR 未验证到文字但文本可能以其他形式出现）
- **fail** — 目标不可能达成，终止执行

#### IPC 协议
- Agent 子进程 `__main__.py` 通过 stdin/stdout JSON 与 Electron 主进程通信
- 支持 start/stop/guidance/status 命令
- 流式输出 agent_start/step_complete/agent_done/log 事件
- 前端实时高亮当前执行节点、显示成功/失败状态

#### 前端适配
- AgentView 传入 `supervise: true` 参数
- store 移除 `step_complete` → 自动 `api.engineStart()` 逻辑
- 新增 `execution.status` / `current_node_id` 状态追踪
- `agent_done` 事件重置执行状态

#### 其他变更
- `generate_flow_script` 提示词更新：新增 `启动` 命令（GUI 非阻塞启动），`运行命令` 仅用于 CLI
- Pseudocode 生成规则优化：启动 GUI 后自动添加等待+聚焦步骤
- 停止按钮支持：stop_event 在线程间传递，监督循环中每步检测

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
