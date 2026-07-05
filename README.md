# 灵动脚本 / Lingdong Script

Windows 桌面自动化项目。当前代码由两条主线组成：

- **WinPilot desktop agent**：`python/winpilot_home/winpilot/`，负责用 LLM 操控真实 Windows 桌面。
- **流程图 / DSL 自动化系统**：`src/renderer/`、`python/engine/`、`python/features/`，负责可视化编排、传统自动化执行和外部集成。

两条主线刻意解耦。WinPilot 不直接使用 `python/features/` 的 `@feature` 注册表，也不直接理解流程图节点；成功的 agent trace 可以导出 `.wps.json` 或 `automation-ir/1`，再由 `src/renderer/utils/wpsToFlowScript.ts` / `src/renderer/utils/automationIR.ts` 桥接到 FlowScript。

## 当前事实

- 主线分支是 `master`。大改造先在 `codex/*` 分支验证，再合并回 `master`。
- 用户始终面对一个 WinPilot Agent。`agent/router.py` 只构建 `AgentRunner`；Coding、Computer Use、Terminal、Browser 等是同一 runner 下按任务启用的 Runtime/Capability。
- 编程/仓库能力由 `agent/runtime_profile.py` 启用 Coding Runtime；`agent/coding.py` 只是旧 import 的兼容 shim，不再有独立 CodingRunner 主循环。
- Electron 主进程 HTTP API 默认监听 `127.0.0.1:7880`。WinPilot FastAPI 由 `python/winpilot_home/serve.py` 启动，端口由 Electron 动态选择；不要直接运行 `uvicorn winpilot.server:app`。
- 默认模型配置在 `python/winpilot_home/winpilot/config.py` 和本机 `python/winpilot_home/config.json`。provider、cache、usage、cost 归一化在 `winpilot/model_adapter.py`。
- 感知默认组合 UIA、Win32、OCR、CV；VLM 默认关闭，只有在 `vlm` 配置有效时才作为辅助感知。
- 低延迟 GUI 目前已落地 controller-first routine、批量动作、变化区域观察、perception reducer、scope guard、UIA-off input locator、绘图 `draw/draws`、视觉颜色证据、工具 schema profile 和 trace 指标。它仍不是完整视觉伺服，也不是“LLM 只处理异常”的最终形态。
- `config.json`、`traces/`、`evals/`、`scripts/`、`experience_memory.json` 是本机运行数据，不提交。

## 成熟度快照

这份快照按当前代码核对，不按目标路线图美化：

| 方向 | 当前状态 |
| --- | --- |
| Raw Device Executor | `action/executor.py` 已有 raw mouse/key/focus helper，public action 进 `DesktopActionQueue`；但 Fast GUI controller 内层仍有路径回到 `ToolSession.dispatch()`，完整独立 raw executor 尚未完成。 |
| Action Queue | `action/desktop_queue.py` 是全局 single-writer 队列，支持同步 `run()` 和后台 job；`target_hwnd` 目前是元数据，不是按 hwnd 并发锁。全局串行更安全，但还不是细粒度 runtime。 |
| Perception daemon | `fast/watcher.py` 提供低成本 screen delta watcher 和 bulletin board；OCR/UIA/CV/VLM 语义理解仍按 observe/controller 触发，不是跨任务常驻语义感知 daemon。 |
| Automation IR | `automation-ir/1` 是 WPS/Flow/skill 的最小共享 envelope；`.wps.json`、FlowScript、legacy DSL 仍并存，IR 还不是唯一中间层。 |
| 前端 | Electron/React 工作台可用，Agent 页嵌入 WinPilot Web UI，FlowCanvas/Replay/设置齐全；但整体仍偏开发者工作台，概念密度高，不是消费级“一句话 Agent”界面。 |
| 真实能力 | 计算器、记事本、网页靶场、简单绘图/拖拽已有回归；QQ/微信/Steam/抖音/网易云等真实复杂软件还缺系统化非破坏性 E2E，不能用靶场成绩替代。 |

## 快速启动

```bash
npm install
npm run dev
```

前端 Agent 页面会嵌入 WinPilot 原生 Web UI。API key 可在前端设置页或 `python/winpilot_home/config.json` 中配置。

## 常用验证

```bash
python -m pytest python/winpilot_home/tests -q
npx vitest run
npm run build
```

清理生成产物和缓存：

```bash
npm run clean
```

`npm run clean:runtime` 会额外删除忽略的 `python-runtime/`。只有确认可重新准备运行时环境时再使用。

真实 GUI 评测应走和前端一致的 `/api/chat` 路径：

```bash
python scripts/gui_eval_run.py "用计算器计算6和7的和" --base-url http://127.0.0.1:8765
```

报告写到忽略目录 `python/winpilot_home/evals/`，trace 和截图在 `python/winpilot_home/traces/`。

网页靶场用于复现输入、按钮、局部变化、弹窗、拖拽和画布任务：

```bash
cd python/winpilot_home/eval_targets
python -m http.server 8877 --bind 127.0.0.1
```

然后让前端 Agent 或 `/api/chat` 打开 `http://127.0.0.1:8877/gui_agent_target.html`，用截图和 trace 核对真实行为。

## 目录边界

| 路径 | 职责 |
| --- | --- |
| `src/main/` | Electron 主进程、HTTP API、Python 子进程、WinPilot 服务生命周期 |
| `src/renderer/` | React 流程图编辑器、Agent iframe、录制导入 UI |
| `python/winpilot_home/winpilot/` | 当前 WinPilot agent、感知、动作、录制、记忆、REST |
| `python/agent/__main__.py` | Electron IPC 到 WinPilot 的适配器 |
| `python/engine/` | legacy FlowScript/DSL 执行引擎 |
| `python/features/` | legacy `@feature` 注册表，供流程图/MCP/HTTP/CLI 使用 |
| `python/core/` | legacy 公共接口层，仍被部分测试/工具引用 |
| `scripts/` | 开发、评测、清理脚本；运行输出写入忽略目录 |

## 文档

- [CLAUDE.md](CLAUDE.md)：给 AI/开发者的最短接手说明。
- [docs/README.md](docs/README.md)：文档索引。
- [docs/architecture.md](docs/architecture.md)：当前架构、状态机和边界。
- [docs/winpilot-agent.md](docs/winpilot-agent.md)：WinPilot agent 运行机制和 trace 复盘方法。
- [docs/model-providers.md](docs/model-providers.md)：模型 provider、cache、usage/cost。
- [docs/replay-recording.md](docs/replay-recording.md)：trace、`.wps.json`、replay、导入流程图。
- [docs/flow-editor.md](docs/flow-editor.md)：流程图/DSL/`@feature` 体系。

## 维护原则

- 不为单个软件硬编码专属规则；优先增强通用感知、窗口限定、验证和恢复。
- 用户始终面对一个 Agent。新能力优先作为 Runtime/Capability/Skill 接入同一 runner；只有明确隔离、安全或生命周期边界时才引入独立 runner。
- GUI 成功不能只看 agent 总结，必须用截图、文件、进程、窗口、trace 或工具证据验证。
- 不继续把大块新逻辑塞进 `agent/loop.py`、`agent/tools.py`、`python/engine/executor.py`；新增能力优先拆模块。
- 文档写当前事实，不堆历史 run 日志。历史 trace 留在本机忽略目录或 eval 报告里。
- WinPilot Agent 自带内部计划任务：`schedule_task` / `list_scheduled_tasks` / `cancel_scheduled_task` 写入 `python/winpilot_home/agent_schedules.json`，到期后仍走同一个 `AgentRunner`；这不是 Windows Task Scheduler。
- 坐标偏移排查用 `coordinate_diagnostics` / `coordinate_click_test`；它们报告物理屏幕、截图、窗口、DPI 和真实鼠标点击路径，不等同于浏览器 CSS/client 坐标。

MIT

### Memory Graph Note

WinPilot long-term memory now uses `python/winpilot_home/memory.db` as a SQLite Memory Graph. `experience_memory.json` is still kept for migration/compatibility, and old recipe-like APIs remain available. Dynamic long-term memory is injected as a bounded Memory Pack user message, not in the stable system prompt. Replay success is not done evidence.
