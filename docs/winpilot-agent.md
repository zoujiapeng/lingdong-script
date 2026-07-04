# WinPilot Agent

当前 WinPilot 主体在 `python/winpilot_home/winpilot/`。本文只描述当前代码机制和排障方法。

## 路由和 Runtime

`agent/router.py` 不再按任务切换 runner：

- 所有任务都进入 `agent/loop.py` 的 `AgentRunner`。
- `agent/runtime_profile.py` 根据任务文本和配置启用 Desktop/Coding capability。
- GUI-only 任务会关闭 Coding Runtime，并通过 schema 和执行层 guard 禁止 shell/file 绕路。
- `agent/coding.py` 只保留兼容旧导入的 `CodingRunner` shim，不再有独立 planner/tool loop。

产品表达：用户面对的是一个 Agent，Coding、Computer Use、Terminal、Browser、Replay 是它的能力。

## 主循环

一次 `/api/chat` 任务的真实路径：

```text
server.py _RunState
  -> router.build_runner()
  -> AgentRunner.run()
  -> build messages + tool_schemas()
  -> model_adapter.chat()
  -> _handle_tool_calls()
  -> ToolSession.dispatch()
  -> tool mixin / FastGuiController / CodingSession
  -> BUS + trace + screenshots
  -> done_verifier.py
```

`AgentRunner` 负责：

- system prompt、user/task、tool history、interjection、context folding。
- OpenAI-compatible function-calling。
- usage/cache/cost 聚合。
- tool result 压缩写回 LLM history，同时保留完整 trace。
- done 二次核验和假成功防护。

## ToolSession

`agent/tools.py` 持有一次 run 的工具状态：

- 当前 target hwnd/title/process。
- 最近 `Snapshot`。
- plan/world/undo stack。
- RuntimeProfile。
- `dispatch()` 到各个 mixin。

工具实现分布：

| 文件 | 工具范围 |
| --- | --- |
| `window_tools.py` | `list_windows`、`scan_windows`、`focus_window`、`find_app`、`find_program` |
| `perception_tools.py` | `observe`、`read_text`、`find_image`、`vlm_describe`、template/color 等感知 |
| `interaction_tools.py` | `click`、`type_text`、`hotkey`、`scroll`、`drag`、`draw_strokes`、`wait` |
| `system_tools.py` | `launch`、`run_shell`、`run_detached`、`tray_click`、`search_files`、`undo_last` |
| `fast_tools.py` | `execute_subgoal`、`run_action_batch`、`screen_watch` |
| `plan_tools.py` | `revise_plan`、`complete_subgoal`、`done`、`fail` |
| `file_tools.py` | `list_dir`、`read_file`、`write_file`、`search_text` |
| `memory_tools.py` | `remember`、`forget`、`reuse_skill` |
| `desktop_tools.py` | clipboard、mouse low-level、screenshot、displays、installed apps |
| `coding_tools.py` | workspace-confined repo file/shell/patch/GitHub tools |

## Tool Schema

`agent/tool_schemas.py` 构造模型可见工具。执行层能力不等于模型每轮可见工具。

主要开关：

- `allow_shell`
- `allow_files`
- `planning`
- `coding_runtime`
- `profile`

Profile：

- `standard`：普通 desktop/coding 默认。
- `lean_gui`：较窄的 GUI schema。
- `drawing_gui`：严格 GUI-only 绘图/画布任务，保留 GUI/controller 核心工具，隐藏无关工具，并把 `execute_subgoal` 参数裁剪到绘图/验证相关字段。

排查 GUI-only 绕路时，直接打开 `traces/<run_id>/NNN_llm_request.json` 看 tools 列表，不要只看 agent 说了什么。

## 低延迟 GUI Controller

### execute_subgoal

`execute_subgoal` 是同一 AgentRunner 里的 controller-first routine，不是第二个 agent。它要求至少有一个具体 routine 字段：

- `input`
- `click`
- `clicks`
- `draw`
- `draws`
- `verify_texts`

只有自然语言 `subgoal` 会快速 blocked，不初始化 controller/perception。

典型用途：

- 输入 literal 到可定位输入框，点击 Apply/Search/Send，并验证可见状态。
- 点击有可见 label 的按钮，并验证可见结果。
- 按顺序点击多个明显控件。
- 在简单画布中绘制 triangle/rectangle/circle/line/check 等 shape。

关键结果字段：

- `subgoal_route`
- `actions_executed`
- `controller_state`
- `evidence_packet.matched_texts`
- `evidence_packet.missing_texts`
- `evidence_packet.candidate_click_labels`
- `evidence_packet.matched_visual_colors`
- `evidence_packet.missing_visual_colors`
- `evidence_packet.visible_colors`

如果锚点 label 没命中但 `candidate_click_labels` 有值，下一步应让模型用最接近的本地化/同义可见标签重试，而不是转去 shell 或源码搜索 GUI 文案。

### run_action_batch

`run_action_batch` 在本地连续执行多个动作，并返回每个子动作结果。顶层 `click/type_text/hotkey/scroll/focus_window/draw_strokes/drag` 也会自动 fast-wrap 成单动作 batch。

排查字段：

- `wrapped_by_fast_controller`
- `metrics`
- `screen_delta`
- `action_target`
- `auto_visual_verify`
- `visual_change_detected`

`screen_delta.changed=true` 只说明像素变化，不是业务成功证据。要结合 target window、expected evidence 和截图。

### 变化区域观察

`screen_watch` 记录最近变化 ROI；动作工具还会记录 action ROI。`observe(changed_only=true)` 优先看 action/recent ROI。

如果 ROI 中没有命中 query，会返回明确的 query fallback，再退回全窗口 observe，避免局部裁剪误导模型。

### 输入框定位

`fast/input_locator.py` 不是某个软件规则。它组合：

- UIA role/name。
- OCR label / placeholder / search-like 文本。
- 附近矩形。
- focused non-button。
- caret-like 过滤。
- synthetic candidate。

用于 UIA 覆盖不到的自绘输入框，例如 QQ、浏览器页面、自定义客户端。

### 绘图/画布

`execute_subgoal.draw` / `draws` 会：

- 找 likely canvas。
- 必要时通过通用颜色面板/色块检测选色。
- 生成 strokes。
- 调 `draw_strokes`。
- 用局部视觉颜色 evidence 验证。

颜色面板 opener 可以缓存，但具体色块坐标每次重扫，避免 stale swatch。`draws[]` 可合并相邻 shape/color/region 计划；纯视觉确认项可由 controller 颜色证据自动完成。terminal done 仍走 `done_verifier.py`。

## 感知

感知来源：

- UIA
- Win32
- OCR
- CV
- VLM（默认关闭）

`perception/fusion.py` 生成 `Snapshot`。`perception/reducer.py` 给 LLM compact 摘要，包括 stage、目标窗口、候选元素、疑点、recent changes 和推荐 controller action。原始 snapshot 仍写入 trace。

VLM 只是兜底感知，不应成为每步主循环。`low_latency.vlm_call_count` 只统计真实 VLM 工作，不统计未配置时的 0ms 占位。

## Done Gate

`done_verifier.py` 防止假成功。有效证据包括：

- fresh screen OCR/read_text。
- action/batch/subgoal evidence。
- 文件存在/内容。
- 进程/窗口状态。
- VLM 或视觉颜色证据。
- 明确 replay 结果加后续当前状态验证。

不接受：

- 模型口头总结。
- 点击前就存在的按钮 label。
- 与 target hwnd 不匹配的前台窗口证据。
- replay 成功但没有当前状态确认。
- 画图任务中凭空生成的 `file_exists` 检查，除非用户真的要求保存文件。

## 记忆和 Replay

| 机制 | 文件 | 当前事实 |
| --- | --- | --- |
| long-term memory | `memory/experience.py` | 本机 `experience_memory.json`，recipe/note 有 conditions/evidence |
| session memory | `agent/session.py` | 只解析“继续/刚才那个/再来一次”等指代 |
| world advisory | `perception/world.py`、`context_guidance.py`、`run_state.py` | 提示重复、卡住、需要 observe、变化信号 |
| reuse_skill | `agent/memory_tools.py` | 强匹配 `.wps.json` replay，成功后仍要 done evidence |
| trace/replay | `recorder/trace.py` | trace、`.wps.json`、control flow replay |

清除记忆后 agent 变笨，通常说明 routine/感知/验证硬实力还不够；不能靠用户历史记忆撑通用能力。

## Coding Runtime

Coding 不是独立 agent。触发后：

- `RuntimeProfile.coding=true`。
- schema 加入 repo 工具，例如 `apply_patch`、`github_search`。
- `list_dir/read_file/write_file/search_text/run_shell` 委托到 workspace-confined `CodingSession`。
- clone/build/test/run 这类任务会走 anti-fake-success gate：没有实际 shell/构建/测试/环境检查尝试，不能 done。

GUI-only 任务不应暴露 coding/file/shell 工具。

## 如何复盘工具细节

1. 确认 runtime：`/api/status.runtime` 或前端 runtime badge。
2. 找 `run_id`。
3. 看 `python/winpilot_home/traces/<run_id>.jsonl`：
   - `agent_start`
   - `agent_step`
   - `agent_tool_start`
   - `agent_tool`
   - `agent_batch_action*`
   - `done_check`
   - `agent_done`
4. 打开同目录截图，优先核对 `batch_after_NNN.jpg` 和 `after_NNN.jpg`。
5. 看 `NNN_llm_request.json` 和 `NNN_llm_response.json`，确认模型实际看见了什么。
6. 看 `NNN_tool_result.json` 获取完整原始工具结果；LLM history 里的 tool result 可能已被 compact。
7. 如果截图和工具结果不一致，按工具名定位 mixin：
   - 窗口：`window_tools.py`
   - 感知：`perception_tools.py`
   - 输入动作：`interaction_tools.py`
   - 启动/shell/tray：`system_tools.py`
   - batch/subgoal：`fast_tools.py`、`fast/controller.py`、`fast/subgoal.py`
   - done/plan：`plan_tools.py`、`done_verifier.py`

## 配置

配置默认值在 `config.py`，本机覆盖在 `python/winpilot_home/config.json`。

常见项：

- `api.provider/base_url/api_key/model`
- `api.thinking`
- `api.pricing`
- `vlm.enabled/base_url/api_key/model`
- `perception.uia/win32/ocr/cv/vlm`
- `fusion.screen_watch_autostart`
- `agent.shell_enabled`
- `agent.planning_enabled`
- `agent.experience_memory`
- `agent.tool_profile`
- `coding.workspace`

不要提交本机 `config.json` 或 key。

## 验证

```bash
python -m pytest python/winpilot_home/tests -q
npx vitest run
npm run build
```

GUI 能力改动还需要跑 `/api/chat` 路径，并人工核对截图。成本敏感时优先用便宜模型、小步数、单个真实任务回归。
