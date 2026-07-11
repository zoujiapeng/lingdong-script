# 灵动脚本 / Lingdong Script

Windows 低延迟 GUI Agent + Agent Runtime Platform（ARP）。当前产品由一个统一控制平面组成：

- **Main LLM Agent**：理解目标、下达子目标、处理异常和调整策略。
- **Fast GUI Controller**：变化触发感知、视觉锚点、批量动作、视觉伺服和动作后验证。
- **ARP Runtime + Studio**：用版本化 `arp/1` 图编排 Agent、Controller、Skill、证据门和人工门。
- **World Model + Run Projection**：统一屏幕状态、元素、页面阶段、timeline、evidence 和延迟指标。

`automation-ir/1` 是新保存流程、Skill、WPS 导出和 Flow 编辑的规范中间层；`arp/1` 是可执行图契约。Legacy WPS、FlowScript 和 DSL 仍作为导入/导出或兼容格式存在，不支持的旧节点会显式标记为 `legacy_flow` 并在运行前失败，不会伪装成等价迁移。

## 当前事实

- 主线分支是 `master`。大改造先在 `codex/*` 分支验证，再合并回 `master`。
- 用户始终面对一个 WinPilot Agent；Agent、ARP 图和 Controller 的事件统一投影成同一种 Run。
- Agent Cockpit 创建的目标图先本地检索并重放已验证 Skill；未命中或重放失败才走 `agent_goal`，显式 `miss` / `fail` 分支不会被 `fail_fast` 提前吞掉。
- 编程/仓库能力由 `agent/runtime_profile.py` 启用 Coding Runtime；`agent/coding.py` 只是旧 import 的兼容 shim，不再有独立 CodingRunner 主循环。
- Electron 主进程 HTTP API 默认监听 `127.0.0.1:7880`。WinPilot FastAPI 由 `python/winpilot_home/serve.py` 启动，端口由 Electron 动态选择；两者除公开 ping 外都使用进程随机 bearer token。不要直接运行 `uvicorn winpilot.server:app`。
- 默认模型配置在 `python/winpilot_home/winpilot/config.py` 和本机 `python/winpilot_home/config.json`。provider、cache、usage、cost 归一化在 `winpilot/model_adapter.py`。
- 感知默认组合 UIA、Win32、OCR、CV；VLM 默认关闭，只有在 `vlm` 配置有效时才作为辅助感知。
- 低延迟 GUI 已落地 batch/subgoal、5-15 FPS watcher、变化 ROI 的增量 OCR/Win32 预取、视觉元素缓存、anchor 复验、点击前局部视觉伺服、瞬态 press 采样、Skill replay、证据 done gate 和实时指标。
- 强 VLM 是未知界面/失败恢复 fallback，不在常规感知循环；当前尚未内置带正式权重的常驻学习型 UI detector。
- `config.json`、`traces/`、`evals/`、`scripts/`、`experience_memory.json` 是本机运行数据，不提交。

## 成熟度快照

这份快照按当前代码核对，不按目标路线图美化：

| 方向 | 当前状态 |
| --- | --- |
| Raw Device Executor | `action/raw_device.py` 让 Controller batch 的常见 click/type/key/drag/focus/launch 子动作直接调用已授权 handler，不再递归支付完整 `ToolSession.dispatch()`；少数复杂动作仍走外层工具生命周期。 |
| Action Queue | `action/desktop_queue.py` 是公平资源队列：真实鼠标/键盘仍按 `physical` 全局单写；明确使用 PostMessage、不会移动鼠标的后台窗口动作可按 `window:<hwnd>` 在不同窗口并发，同一 hwnd 串行；剪贴板另有独占资源。 |
| Perception daemon | `fast/perception_daemon.py` 按 hwnd 复用 watcher 与 ElementCache，并在有意义的 ROI 变化后合并调度轻量 OCR/Win32；changed-only observe 可消费热缓存，UIA/VLM 不进入 daemon 内环。它还不是常驻学习型 YOLO/UI detector。 |
| Runtime jobs | Agent、ARP、Replay 和计划任务统一进入 `runtime_jobs.py`；API 可查询/取消，交互任务按 `interactive_runtime` 排队，真实鼠标键盘仍由 Desktop Action Queue 单写保护。 |
| ARP / Automation IR | `automation-ir/1` 是新 artifact 的规范中间层。Flow editor 在 IR 的 `runtime.arp_graph` 中保存一次编译后的 `arp/1` 投影及 fingerprint，后端校验后直接运行；WPS/FlowScript/DSL 是兼容 adapter，不是新的并行事实源。 |
| 前端 | 原生 Cockpit、ARP Studio、Runs、Skills、System 共用 `/api/v2` 和 WebSocket，不再依赖 iframe Agent 页面。 |
| 真实能力 | 计算器、记事本、网页靶场、简单绘图/拖拽已有回归；QQ/微信/Steam/抖音/网易云等真实复杂软件还缺系统化非破坏性 E2E，不能用靶场成绩替代。 |

## 快速启动

```bash
npm install
npm run dev
```

启动后第一屏就是原生 Agent Cockpit；ARP Studio 可直接 Validate/Run 默认安全图。API key 可在 System 页面或 `python/winpilot_home/config.json` 中配置。

当前已登录的 ChatGPT/Codex 桌面客户端也可直接作为高层规划器，不需要另配 API key：WinPilot 在 `/mcp` 暴露观察、controller 子目标、批量动作、Skill 与 ARP 工具。连接方式和安全边界见 [docs/chatgpt-desktop.md](docs/chatgpt-desktop.md)。

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

直接 Agent GUI 评测走 `/api/chat`；当前原生前端目标入口走 `/api/v2/arp/runs`，先尝试已验证 Skill，miss/fail 后才进入同一个 `AgentRunner`：

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
| `src/renderer/` | 原生 Agent Cockpit、ARP Studio、Runs、Skills、System、FlowCanvas |
| `python/winpilot_home/winpilot/` | 当前 WinPilot agent、感知、动作、录制、记忆、REST |
| `python/winpilot_home/winpilot/orchestrator/` | `arp/1` contract、runtime、adapter、capability catalog、Run Projection |
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
- [docs/replay-recording.md](docs/replay-recording.md)：trace、Automation IR、兼容 WPS、replay、导入流程图。
- [docs/flow-editor.md](docs/flow-editor.md)：流程图/DSL/`@feature` 体系。

## 维护原则

- 不为单个软件硬编码专属规则；优先增强通用感知、窗口限定、验证和恢复。
- 用户始终面对一个 Agent。新能力优先作为 Runtime/Capability/Skill 接入同一 runner；只有明确隔离、安全或生命周期边界时才引入独立 runner。
- GUI 成功不能只看 agent 总结，必须用截图、文件、进程、窗口、trace 或工具证据验证。
- 验证可做有限并发：文件/进程只读检查会与 UIA/OCR 重叠；`shell_true` 不并发。真实物理输入保持全局 single-writer，只有明确证明为后台安全的窗口消息动作才允许跨 hwnd 并发。
- 不继续把大块新逻辑塞进 `agent/loop.py`、`agent/tools.py`、`python/engine/executor.py`；新增能力优先拆模块。
- 文档写当前事实，不堆历史 run 日志。历史 trace 留在本机忽略目录或 eval 报告里。
- WinPilot Agent 自带计划任务：System 页面和 `schedule_task` / `list_scheduled_tasks` / `cancel_scheduled_task` 共用 `python/winpilot_home/agent_schedules.json`；到期后提交统一 RuntimeJob，再走同一个 `AgentRunner`。这不是 Windows Task Scheduler。
- `search_files` 优先使用随包提供的 Everything ES 客户端连接本机 Everything 索引，支持文件/目录、扩展名和排序；索引不可用时只做有时间上限的本地扫描。
- Electron 会在任务完成、失败或停止时设置 Windows 任务栏状态、闪烁后台窗口并发送系统通知；同一 `run_id` 只提醒一次。
- `winpilot/__init__.py` 会在任何动作/感知模块之前通过 `utils/dpi.py` 初始化 Per-Monitor V2；坐标偏移排查用 `coordinate_diagnostics` / `coordinate_click_test`，它们报告物理屏幕、截图、窗口、DPI 和真实鼠标点击路径，不等同于浏览器 CSS/client 坐标。

MIT

### Memory Graph Note

WinPilot long-term memory now uses `python/winpilot_home/memory.db` as a SQLite Memory Graph. `experience_memory.json` is still kept for migration/compatibility, and old recipe-like APIs remain available. Dynamic long-term memory is injected as a bounded Memory Pack user message, not in the stable system prompt. A replay is ARP evidence only when it contains at least one verified step; cache lookup, replay start, and empty scripts are never done evidence.
