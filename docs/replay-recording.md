# 录制、Trace、Replay

WinPilot 的复用链路是：

```text
Agent 实时操作 -> trace -> .wps.json -> Replayer 或 FlowScript
```

## Trace

运行时 trace 在 `python/winpilot_home/traces/`，属于本机运行数据，不提交。

当 `agent.llm_trace=true` 时，系统会记录 LLM 请求/响应、工具调用、截图引用和事件，用于复现模型每一步看到的信息。调试假成功时优先看 trace 和真实屏幕，不只看最终 `done`。

关键文件/事件：

- `python/winpilot_home/traces/<run_id>.jsonl`：BUS 事件流，含 `agent_step`、`agent_tool_start`、`agent_tool`、`agent_batch_action_start`、`agent_batch_action`、`done_check`、`agent_done`。
- `python/winpilot_home/traces/<run_id>/NNN_llm_request.json` / `NNN_llm_response.json`：模型每步真实输入输出。
- `before_NNN.jpg` / `after_NNN.jpg`：顶层动作前后截图。
- `batch_before_NNN.jpg` / `batch_after_NNN.jpg`：`run_action_batch` 内部子动作截图。

`GET /api/trace/{run_id}/summary` 会汇总工具耗时、done 证据、`low_latency` 和 `fast_gui` 指标。低延迟复盘优先看 summary，再打开对应截图和 LLM request/response 对照。

真实 GUI 评测可用脚本生成证据报告：

```bash
python scripts/gui_eval_run.py "用计算器计算6和7的和" --base-url http://127.0.0.1:8765
```

脚本走 `/api/chat`，也就是前端 Agent 页面同一路径；它不会直接调用内部 runner。输出在 `python/winpilot_home/evals/`，只保存本机评测索引，不提交。报告会列出 `run_id`、trace 文件、截图目录、工具序列和 done 核验摘要。

当前低延迟 GUI 靶场位于 `python/winpilot_home/eval_targets/gui_agent_target.html`。它用于测试 `run_action_batch(subgoal=...)`、自动 `screen_watch`、recent/action ROI、`observe(changed_only=true)` 和 fresh-screen done gate，不是生产功能的专属规则。

## .wps.json

`.wps.json` 是 WinPilot 侧的语义重放格式，不是 FlowScript。

它应尽量记录：

- 工具名和参数。
- 目标窗口/元素/坐标。
- 语义 selector。
- expect 验证。
- 必要的变量和控制结构。

保存/读取接口在 WinPilot REST：

- `GET /api/scripts`
- `GET /api/script/{file}`
- `PUT /api/script/{file}`
- `POST /api/export/{run_id}`
- `POST /api/replay`

## 人类演示录制

接口：

- `POST /api/human/start`
- `POST /api/human/stop`
- `GET /api/human/status`

人类录制用于生成可重放动作，不代表可以直接训练模型；如果要用于学习，必须经过语义化、验证和失败样本分析。

## 导入流程图

前端转换器：`src/renderer/utils/wpsToFlowScript.ts`。

测试重点：

- `.wps.json` step 类型能否转换成正确 FlowScript 节点。
- 参数名是否和 `nodeDefinitions` / `dsl_flow_converter.py` 对齐。
- 转换后的流程图能否被旧 engine 执行。

## 维护原则

- Replayer 与 FlowScript converter 都要跟 `.wps.json` schema 一起演进。
- 不要让 Agent 直接编辑流程图内部格式。
- 不要把运行 trace 当成源码文档提交。
- 不要把 batch 顶层结果当成全部证据；`run_action_batch` 内部动作要看 `agent_batch_action` 和 `batch_before/batch_after`。
