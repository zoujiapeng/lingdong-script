# AI 接手速览

这份文件只写最短事实，避免 AI 读太多旧文档。

## 项目主线

1. `python/winpilot_home/winpilot/`
   - 当前 WinPilot desktop agent。
   - GUI 任务：`agent/loop.py` 的 `AgentRunner`。
   - 编程/仓库任务：`agent/coding.py` 的 `CodingRunner`。
   - 路由入口：`agent/router.py`。

2. `src/renderer/` + `python/engine/` + `python/features/`
   - 旧流程图/DSL/`@feature` 自动化系统。
   - 仍在用，但不是 WinPilot 主 agent 的工具来源。

## 入口文件

| 路径 | 作用 |
| --- | --- |
| `src/main/winpilotServer.ts` | Electron 启动 WinPilot FastAPI |
| `python/winpilot_home/serve.py` | WinPilot 唯一支持的服务启动入口 |
| `python/winpilot_home/winpilot/server.py` | WinPilot REST/WebSocket |
| `python/winpilot_home/winpilot/config.py` | 真实默认配置 |
| `python/winpilot_home/winpilot/agent/tool_schemas.py` | GUI agent OpenAI function schema |
| `python/winpilot_home/winpilot/agent/task_rules.py` | GUI 任务分类和 done gate 规则辅助函数 |
| `python/winpilot_home/winpilot/agent/done_verifier.py` | GUI agent done 二次核验 mixin |
| `python/winpilot_home/winpilot/agent/reflection.py` | GUI agent 反思检查点 mixin |
| `python/winpilot_home/winpilot/agent/tools.py` | GUI agent `ToolSession`、dispatch、窗口/屏幕工具 |
| `python/winpilot_home/winpilot/agent/file_tools.py` | GUI agent 通用文件工具 mixin |
| `python/winpilot_home/winpilot/agent/tool_result.py` | GUI agent 工具返回对象 |
| `python/winpilot_home/winpilot/action/executor.py` | 鼠标、键盘、窗口、剪贴板、截图等底层动作 |
| `python/winpilot_home/winpilot/perception/fusion.py` | UIA/Win32/OCR/CV/VLM 感知融合 |
| `python/engine/executor_types.py` | legacy Flow/DSL executor 状态、上下文和控制流异常 |
| `src/main/api.ts` | Electron HTTP API，默认 `127.0.0.1:7880` |

## 不要踩坑

- 不要直接运行 `uvicorn winpilot.server:app`；用 `python/winpilot_home/serve.py`。
- 不要把 `python/features/` 当成 WinPilot 工具池。
- 不要写计算器、QQ、网易云等单软件专属规则。
- 不要相信“done 日志”等于真实成功；必须看屏幕/文件/进程/窗口/trace。
- 不要把 `config.json`、`traces/`、`scripts/`、`experience_memory.json` 当源码。
- 不要继续往 `agent/loop.py`、`agent/tools.py`、`python/engine/executor.py` 塞大块新逻辑。

## 当前清理方向

- `src/renderer/store/index.ts` 已去掉重复 defaults/templates/persistence 定义，真实来源是 `store/defaults.ts` 和 `store/persistence.ts`。
- WinPilot 截图转 base64 的公共逻辑在 `winpilot/utils/image_capture.py`。
- WinPilot GUI 工具 schema 已从 `agent/tools.py` 拆到 `agent/tool_schemas.py`，并压缩为短描述，减少 LLM 工具上下文噪音。
- 文件读写/搜索 handler 已拆到 `agent/file_tools.py`。
- WinPilot GUI 任务分类/guard 规则已拆到 `agent/task_rules.py`；done 二次核验已拆到 `agent/done_verifier.py`；反思检查点已拆到 `agent/reflection.py`。
- legacy Flow/DSL executor 的共享状态/异常类型已拆到 `python/engine/executor_types.py`，`executor.py` 仍只按 legacy 执行器维护。
- 旧 `python/core/screenshot.py` 保留 `capture_screen()` 兼容 legacy Robot Framework keyword。
- `npm run clean` 清 `dist/`、`release/`、测试/浏览器缓存和 pycache；`npm run clean:runtime` 才额外删 `python-runtime/`。
- 下一步优先拆 `agent/loop.py` 的计划上下文；`agent/tools.py` 后续继续拆窗口、输入、视觉、记忆工具。

## 验证

```bash
python -m pytest python/winpilot_home/tests -q
npx vitest run
npm run build
```
