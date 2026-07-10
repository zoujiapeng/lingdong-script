# 灵动脚本 / Lingdong Script

Windows 低延迟 GUI Agent + Agent Runtime Platform（ARP）。当前产品由一个统一控制平面组成：

- **Main LLM Agent**：理解目标、下达子目标、处理异常和调整策略。
- **Fast GUI Controller**：变化触发感知、视觉锚点、批量动作、视觉伺服和动作后验证。
- **ARP Runtime + Studio**：用版本化 `arp/1` 图编排 Agent、Controller、Skill、证据门和人工门。
- **World Model + Run Projection**：统一屏幕状态、元素、页面阶段、timeline、evidence 和延迟指标。

Legacy FlowScript/DSL 仍保留，但 ARP 是新控制平面的运行契约。前端 FlowScript 通过 `flowToArpGraph.ts` 保守编译；常用 GUI 节点直接收敛到 Controller action batch，不支持的旧节点会显式标记为 `legacy_flow` 并在运行前校验失败，不会伪装成已等价迁移。

## 当前事实

- 主线分支是 `master`。大改造先在 `codex/*` 分支验证，再合并回 `master`。
- 用户始终面对一个 WinPilot Agent；Agent、ARP 图和 Controller 的事件统一投影成同一种 Run。
- Agent Cockpit 创建的目标图先本地检索并重放已验证 Skill；未命中或重放失败才走 `agent_goal`，显式 `miss` / `fail` 分支不会被 `fail_fast` 提前吞掉。
- 编程/仓库能力由 `agent/runtime_profile.py` 启用 Coding Runtime；`agent/coding.py` 只是旧 import 的兼容 shim，不再有独立 CodingRunner 主循环。
- Electron 主进程 HTTP API 默认监听 `127.0.0.1:7880`。WinPilot FastAPI 由 `python/winpilot_home/serve.py` 启动，端口由 Electron 动态选择；不要直接运行 `uvicorn winpilot.server:app`。
- 默认模型配置在 `python/winpilot_home/winpilot/config.py` 和本机 `python/winpilot_home/config.json`。provider、cache、usage、cost 归一化在 `winpilot/model_adapter.py`。
- 感知默认组合 UIA、Win32、OCR、CV；VLM 默认关闭，只有在 `vlm` 配置有效时才作为辅助感知。
- 低延迟 GUI 已落地 batch/subgoal、5-15 FPS watcher、ROI observe、predictive OCR、视觉元素缓存、anchor 复验、点击前局部视觉伺服、瞬态 press 采样、Skill replay、证据 done gate 和实时指标。
- 强 VLM 是未知界面/失败恢复 fallback，不在常规感知循环；当前尚未内置带正式权重的常驻学习型 UI detector。
- `config.json`、`traces/`、`evals/`、`scripts/`、`experience_memory.json` 是本机运行数据，不提交。

## 成熟度快照

这份快照按当前代码核对，不按目标路线图美化：

| 方向 | 当前状态 |
| --- | --- |
| Raw Device Executor | `action/executor.py` 已有 raw mouse/key/focus helper，public action 进 `DesktopActionQueue`；但 Fast GUI controller 内层仍有路径回到 `ToolSession.dispatch()`，完整独立 raw executor 尚未完成。 |
| Action Queue | `action/desktop_queue.py` 是全局 single-writer 队列，支持同步 `run()` 和后台 job；`target_hwnd` 目前是元数据，不是按 hwnd 并发锁。全局串行更安全，但还不是细粒度 runtime。 |
| Perception daemon | Level 0 watcher 常驻；变化触发 predictive OCR，UIA/Win32/OCR/CV 按成本融合，VLM 兜底。跨 run 语义页面分类仍在扩展。 |
| ARP / Automation IR | `arp/1` 是 Agent/Controller/Skill 的可执行图契约；`automation-ir/1` 是 WPS/Flow/skill envelope；legacy DSL 仍兼容存在。 |
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

WinPilot long-term memory now uses `python/winpilot_home/memory.db` as a SQLite Memory Graph. `experience_memory.json` is still kept for migration/compatibility, and old recipe-like APIs remain available. Dynamic long-term memory is injected as a bounded Memory Pack user message, not in the stable system prompt. A replay is ARP evidence only when it contains at least one verified step; cache lookup, replay start, and empty scripts are never done evidence.
