# 重放与录制

当前主线是 WinPilot trace / 人类演示录制生成 `.wps.json`，再离线重放或导入流程图。

## `.wps.json`

`.wps.json` 是 WinPilot 的语义重放格式，当前由 `winpilot/recorder/trace.py` 和 `winpilot/recorder/human.py` 生成，由 `winpilot/recorder/replay_control.py` 执行控制流。

脚本通常包含：

- `format`
- `name`
- `source`
- `vars`
- `steps`

常见步骤：

| 类型 | 说明 |
|---|---|
| `click` | 可带 `text`、`role`、`coords`、`source` 等语义选择器 |
| `type_text` | 输入文本 |
| `hotkey` | 快捷键 |
| `launch` | 启动程序 |
| `focus_window` | 聚焦窗口 |
| `scroll` / `drag` / `wait` | 基础交互 |
| `run_shell` | shell 动作 |
| `ctrl.if` | 条件分支 |
| `ctrl.loop` | 循环 |
| `ext.delay`、`ext.var_set`、`ext.clipboard_set`、`ext.screenshot`、`ext.run_shell` | 离线扩展步骤 |

`expect` 和控制流条件复用 `action/verify.py` 的验证逻辑，例如文本出现、窗口出现、文件存在、进程存在、shell 断言。

## Agent trace

`AgentRunner` 执行动作时会把可重放动作记录到 trace：

- 动作工具名和参数
- 语义选择器
- expect
- 截图
- 运行 ID

FastAPI 相关端点：

- `GET /api/traces`
- `GET /api/trace/{run_id}`
- `GET /api/trace/{run_id}/shot/{name}`
- `POST /api/export/{run_id}`

导出后脚本保存在 `python/winpilot_home/scripts/`。这是运行数据，应被 git 忽略。

## 人类演示录制

实现：`winpilot/recorder/human.py`。

它不是旧的纯坐标宏，而是在点击瞬间尝试通过 `fusion.observe` 抓取命中的元素文字、角色、来源和坐标。键盘输入会聚合成 `type_text`，组合键会记录为 `hotkey`。

FastAPI 端点：

- `POST /api/human/start`
- `POST /api/human/stop`
- `GET /api/human/status`

## 离线 Replayer

实现：`winpilot/recorder/trace.py` 和 `winpilot/recorder/replay_control.py`。

特点：

- 不调用 LLM。
- 优先按文字/角色重新定位。
- 坐标是兜底，不是唯一依据。
- `ctrl.if` / `ctrl.loop` 本地执行。
- 每步可跑 expect 验证。
- 失败时报告具体步骤，供用户或 agent 接管。

FastAPI 端点：`POST /api/replay`。

## 导入流程图

前端实现：

- `src/renderer/components/ReplayEditorView.tsx`
- `src/renderer/utils/wpsToFlowScript.ts`

转换关系：

| `.wps.json` 步骤 | FlowScript 节点 |
|---|---|
| `click` 带文本 | `click_text` |
| `click` 坐标 | `click` |
| `type_text` | `input_text` |
| `hotkey` | `keyboard` |
| `launch` | `launch_program` |
| `focus_window` | `focus_window` |
| `scroll` / `drag` | `mouse_operation` / `swipe` |
| `ctrl.if` | `condition` + yes/no 端口 |
| `ctrl.loop` | `loop` |
| `run_shell` / `ext.run_shell` | `tool_call` |
| `ext.delay` | `wait` |
| `ext.var_set` | `variable_set` |
| `ext.clipboard_set` | `clipboard_set` |
| `ext.screenshot` | `screenshot` |

转换是纯前端代码完成，agent 不参与，也不需要理解流程图格式。

## 与普通宏的关系

旧流程图体系仍保留普通宏接口：

- `features/macro_features.py`
- `src/renderer/utils/macroConverter.ts`
- Electron HTTP `/api/macro/*`

普通宏主要是坐标录制，稳定性不如 `.wps.json` 语义录制。旧“视觉宏录制”相关文件当前不在 Git 跟踪代码中，不应再作为可用主功能写入文档。
