# 交接：全面重写提示词与工具描述（2026-10-18）

> 目标：**极简、有力、全是中文**。不是翻译，是重写。
> 前置：当前工作树已包含 2026-10-18 的全部机械层改动（见文末「本轮已落地」）。

## 1. 需要重写的全部提示词表面积

全部在 `python/winpilot_home/winpilot/vnext/model_roles/visual_actor.py`（行号为当前 HEAD）：

| 行 | 内容 | 说明 |
|---|---|---|
| 54-62 | `VISUAL_ACTOR_SYSTEM_CONTRACT` | 系统合同，8 行，每回合都发 |
| 64-... | `_TOOL_DESCRIPTIONS`（12 项） | 9 个 GUI 动作 + 3 个语义动作 |
| 1251 | `_connector_tool_definitions` | 2 个 MCP 连接器工具 |
| 1329 | `_shell_tool_definitions` | run_shell / run_detached / write_file |
| 1437 | `_window_text_tool_definitions` | read_window_text |
| 1480 | `_copy_current_view_tool_definitions` | copy_screen_to_clipboard |
| 1509 | `_activate_window_tool_definitions` | activate_window |
| 1550 | `_list_windows_tool_definitions` | list_windows |
| 1816 | `_read_state_tool_definitions` | read_state |

另有参数级描述（`command` / `cwd` / `script` / `name` / `path` / `content` / `app` / `query` / `connector`），
以及 `orchestrator/live_agent.py` 里 `_actor_tools()` 的 schema 内描述（约 1464 行 `launch_application.app`）。

## 2. 为什么现在这份不能用

1. **是逐句翻译，不是重写。** 上一轮的改法是把英文逐句换成中文，句式和冗余都跟着原文走，中文读起来更绕也更长。
2. **已知语义丢失**（重写时必须补回）：
   - `run_shell`：原文明确"嵌套 `powershell -Command/-File` 会另起进程，**继承不到 DPI 感知**，窗口坐标会被缩放"。现译文压掉了这句的因果。
   - `input_sequence`：原文把 `move_by`、`wheel/hwheel`、`held_keys` 的语义分别说清；现译文把 `move_by` 挤到最后一句，读起来像附属品。
   - `drag`：原文强调"第一对是按下点、最后一对是松手点，全部是提交图像素"；现译文保留了，但和 `pointer_held` 的关系没讲。
3. **系统合同是历史堆叠**：8 行里既有"读什么"，又有"怎么回话"，还有"runtime 不做分类"这种给人看的说明——对模型每回合纯属噪声。

## 3. 重写规则（请严格遵守）

- **语言**：全中文。工具名、参数名、枚举值保持英文（它们是对外契约）。
- **长度**：系统合同 **≤5 行**；每个工具描述 **≤2 句**；参数描述 **≤1 句**。
- **只写模型推不出来的事实**：
  - 会（`run_shell` 每次新进程、嵌套丢 DPI）
  - 语义（`pointer_held` 什么时候松开）
  - 坐标空间（提交图像素 = 唯一权威）
  - 二选一（该用 A 而不是 B）
- **不写**：能自己看出来的、应用配方、步骤教程、"要小心""请注意"这类语气词。
- **不搬回**：任何"重复几次就换方法""画面没变就换招"的规则——这些已经由机械层负责（见第 5 节）。
- **不许改变 schema**：只改 `description` 文本与 `VISUAL_ACTOR_SYSTEM_CONTRACT`。

## 4. 验收

1. `python -m pytest python/winpilot_home/tests tests -q` → 必须 **4097 passed / 13 skipped**（当前基线）。
   注意：有两个测试断言描述文本，改文案时要同步：
   - `test_vnext_live_model_roles.py::test_actor_uses_zero_retry_client_and_strict_visual_context`（断言 click/scroll/type_text/press_key/reply_to_user/blocked 的中文片段）
   - 同文件 `test_actor_streams_public_narration_commands_results_and_task_state`（断言 run_shell 描述里的两句）
2. 端到端（真机，必须关干净再跑）：
   ```
   关掉 QQ 和微信 → 启动 python/winpilot_home/serve.py 8799 → POST /api/v2/goals/runs
   任务：打开 QQ → 空间 → 相册（名为「相册」）→ 8月10日带星星的照片 → 保存到桌面
        → 微信「文件传输助手」发送 + 一句描述
   验收：桌面出现新的 .jpg；微信里出现新的图片气泡 + 文字气泡（按时间戳判，不要看有没有旧消息）
   ```
   基线：2026-10-18 凌晨那次 91 次模型调用 / 432 秒 / ¥0.93 完成。

## 5. 本轮已落地的机械层（重写提示词时不要动它们）

- **无进展截断**：连续 4 帧画面改动比例 < 0.25% → runtime 拒绝下一步并说明事实（`visual_actor.py` 内 `_NO_PROGRESS_RATIO / _NO_PROGRESS_LIMIT`）。
- **改动比例埋点**：每帧记录 `change_ratio_from_previous`（`live_agent.py::_screen_change_ratio`，1/4 缩图 + 灰阶差 >12 的像素占比）。
- **前台控件文字**：上一步没推动画面时，把前台窗口的真实控件标签放进 context（`foreground_labels`）。
- **物理回执**：copy/paste 后带剪贴板格式；`read_state` 返回剪贴板 + 最近 3 条回执。
- **帧宽 1568**（原 1120），测试夹具已改为从 `_MAX_SUBMITTED_SIDE` 推导。

## 6. 现状与风险

- 分支：`feat/dsh-plugin`（master 的严格快进后继）。主产品规则要求 master，合并由人工决定。
- 已有一次完整成功（2026-10-18 04:31，见第 4 节基线），但成功率仍不稳定：大约 2/6。
- 未解决：下载文件名是 `default (1)/(2).jpeg`，模型要靠 `run_shell` 反复翻 Downloads 确认，这段是主要的额外耗时（约 100 秒）。
