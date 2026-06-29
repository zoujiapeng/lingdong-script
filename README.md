# 灵动脚本 / Lingdong Script

Windows 桌面自动化项目。当前项目由两条主线组成：

- **WinPilot desktop agent**：`python/winpilot_home/winpilot/`，负责用 LLM 操控真实 Windows 桌面。
- **流程图/DSL 自动化系统**：`src/renderer/` + `python/engine/` + `python/features/`，负责可视化编排、传统自动化执行和外部集成。

两条主线刻意解耦。WinPilot 不直接使用 `python/features/` 的 `@feature` 注册表，也不直接理解流程图节点；成功的 agent trace 可以导出 `.wps.json`，再由 `src/renderer/utils/wpsToFlowScript.ts` 转成 FlowScript。

## 当前事实

- 当前分支：`feat/winpilot-agent`。
- Electron 主进程 HTTP API 默认监听 `127.0.0.1:7880`。
- WinPilot FastAPI 由 `python/winpilot_home/serve.py` 启动，端口由 Electron 动态选择；不要直接运行 `uvicorn winpilot.server:app`。
- WinPilot 默认模型配置在 `python/winpilot_home/winpilot/config.py`，默认文本模型为 `deepseek-v4-flash`。
- 感知默认启用 UIA、Win32、OCR、CV；VLM 默认关闭，但视觉模式可在 VLM 配置有效时参与辅助感知。
- GUI 任务走 `AgentRunner`；编程/仓库任务走 `CodingRunner`。
- GUI runner 的任务规则在 `agent/task_rules.py`，反思检查点在 `agent/reflection.py`；`agent/loop.py` 仍负责主循环和 done 验证。
- `config.json`、`traces/`、`scripts/`、`experience_memory.json` 是本机运行数据，不提交。

## 快速启动

```bash
npm install
npm run dev
```

WinPilot Agent 需要在前端 Agent 页面或 `python/winpilot_home/config.json` 中配置 OpenAI 兼容 API key。

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

`npm run clean:runtime` 会额外删除忽略的 `python-runtime/`。只有在不需要打包内置 Python 版本、且能重新准备 runtime 时使用。

旧根目录测试需要额外路径：

```powershell
$env:PYTHONPATH='python'
python -m pytest tests/test_vision.py tests/test_interfaces.py -q
```

## 目录边界

| 路径 | 职责 |
| --- | --- |
| `src/main/` | Electron 主进程、HTTP API、Python 子进程、WinPilot 服务启动 |
| `src/renderer/` | React 流程图编辑器、Agent iframe、录制导入 UI |
| `python/winpilot_home/winpilot/` | 当前 WinPilot agent、感知、动作、录制、记忆、REST |
| `python/agent/__main__.py` | Electron IPC 到 WinPilot 的适配器 |
| `python/engine/` | 旧 FlowScript/DSL 执行引擎 |
| `python/features/` | 旧 `@feature` 注册表，供流程图/MCP/HTTP/CLI 使用 |
| `python/core/` | 旧公共接口层，仍被部分 legacy 测试/工具引用 |

## 文档

- [CLAUDE.md](CLAUDE.md)：给 AI/开发者的最短接手说明。
- [docs/README.md](docs/README.md)：文档索引。
- [docs/architecture.md](docs/architecture.md)：当前架构和边界。
- [docs/winpilot-agent.md](docs/winpilot-agent.md)：WinPilot agent 细节。
- [docs/apis.md](docs/apis.md)：Electron HTTP API、WinPilot REST、IPC、MCP。
- [docs/replay-recording.md](docs/replay-recording.md)：trace、`.wps.json`、replay、导入流程图。
- [docs/flow-editor.md](docs/flow-editor.md)：流程图/DSL/`@feature` 体系。

## 维护原则

- 不为单个软件硬编码专属规则；优先增强通用感知、窗口限定、验证和恢复。
- 不把 `@feature` 全塞给 WinPilot 主 agent；新专长优先做独立子 agent。
- 不只看日志判定成功；GUI 任务必须用屏幕、文件、进程、窗口或 trace 证据验证。
- 不继续扩大 `agent/loop.py`、`agent/tools.py`、`python/engine/executor.py`；新增能力优先拆模块。legacy executor 共享类型在 `python/engine/executor_types.py`。
- `release/`、`dist/`、`python-runtime/`、缓存和 pycache 是可再生成/本机产物，不当源码阅读。

MIT
