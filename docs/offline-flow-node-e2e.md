# 离线流程节点逐项 E2E 矩阵

> 本文只记录可以由当前 Studio 编译为 canonical ARP、且不会落入
> `legacy_flow` 的产品节点。编译测试、mock handler 测试和可见 Electron
> 端到端是三种不同证据，不能互相冒充。

## 当前边界

- `NODE_DEFINITIONS` 仍保存 60 种历史定义，供旧文件读取。
- 当前节点目录只开放 20 种可编辑节点；其中 `agent_goal` 需要模型，不属于完全离线节点。
- 其余 19 种可编辑节点，加上脚本自带的 `start` / `end`，均可在不联网时编排。
- “离线执行”不等于“不要本机 Runtime”。桌面输入、截图、窗口、剪贴板、WPS 和技能重放仍需要本机 Python Runtime、Desktop Action Queue 或本地 Memory Store。
- `image_click`、`loop`、`sub_script`、`web_browser`、`popup_close`、`color_check`、`progress_wait`、`region_wait`、`scene_condition`、`variable_get`、系统电源/音频/桌面聚合节点、`dialog_action`、`a11y_action`、`wifi_action`、`tool_call` 和 camera 节点等 38 种历史定义没有出现在当前节点目录。它们尚未获得 canonical ARP 与逐节点可见 E2E，不得称为离线可执行节点；后续只能逐个迁移并测试，或删除。

## 证据等级

| 等级 | 含义 |
| --- | --- |
| C | Renderer 能编译为非 `legacy_flow`，本地结构校验与 Python graph contract 有覆盖。 |
| I | 本机 Runtime handler/integration 测试实际运行，允许使用 test double；不是用户端 E2E。 |
| E | 用户从可见 Electron Studio 创建/打开流程并点击运行，动作进入真实本机 Runtime，由 Runtime 之外的 oracle 验证。 |

本轮新增的完整 fixture matrix 覆盖全部 20 个目录节点的 C 级合同，并验证只有
`agent_goal` 被分类为需要模型。它只证明非 `legacy_flow` 编译路径和合同覆盖，不证明
每个参数的物理执行语义；本轮没有把这个测试结果写成 E2E 通过。

## 逐节点状态

| 节点 | 无网络编排 | 执行依赖 | C | I | E | E 级独立 oracle |
| --- | --- | --- | --- | --- | --- | --- |
| `start` / `end` | 是 | 纯本地 graph | 已有 | 已有 | YES 组合链通过；独立逐项待测 | 目标状态不变 + 完整节点生命周期；生命周期本身不能证明任务成功 |
| `wait` (`time`) | 是 | 本地计时 | 已有 | 已有 | 待测 | harness 单调时钟测得实际延迟 |
| `wait` (`window`) | 是 | 本机窗口枚举 | 已有 | 部分 | 待测 | 测试窗口的真实 HWND/标题出现 |
| `condition` | 是 | 本地变量/输入 | 已有 | 已有 | YES 组合链通过；NO 待测 | YES/NO 分支分别点击靶场按钮，JSON 只允许预期分支增加 |
| `variable_set` | 是 | 本地 graph 变量 | 已有 | 已有 | 待测 | 后继 condition 选择的外部按钮状态 |
| `evidence_gate` | 是 | 本地 evidence registry | 已有 | 已有 | 待测 | pass/fail 后继分别修改靶场状态；缺证据必须 fail closed |
| `human_gate` | 是 | 人工确认 | 已有 | 已有 | 待测 | Electron 中批准/拒绝后，只有对应外部按钮增加 |
| `action_batch` | 是 | 本机桌面控制器 | 已有 | 已有 | 待测 | 一个 batch 后 JSON 中点击、文本和键盘事实完全匹配 |
| `click` | 是 | 本机鼠标 | 已有 | 共用 controller | **独立 E2E 通过（1/19）** | `click_count` 精确从 0 增加到 1，其他输入计数保持 0；截图只作完成证据 |
| `click_text` | 是 | 本机观察/OCR + 鼠标 | 已有 | 共用 controller | 待测 | `click_count` 精确增加 1；目标文本每轮随机摆放 |
| `input_text` | 是 | 本机键盘 | 已有 | 共用 controller | 待测 | `entry_text` 精确等于 nonce；多/少字符均失败 |
| `keyboard` | 是 | 本机键盘 | 已有 | 共用 controller | 待测 | JSON `key_events` 精确包含预期组合键/按键 |
| `swipe` | 是 | 本机鼠标拖拽 | 已有 | 共用 controller | 待测 | `drag_start` / `drag_end` 位于两个预期区域且只发生一次 |
| `mouse_operation.click` | 是 | 本机鼠标 | 已有 | 共用 controller | 待测 | 与 `click` 相同的精确计数 |
| `mouse_operation.scroll` | 是 | 本机滚轮 | 已有 | 共用 controller | 待测 | `scroll_events` 增加且 `scroll_position` 按指定方向变化 |
| `mouse_operation.move` | 是 | 本机鼠标 | 已有 | 共用 controller | 待测 | OS 指针与 JSON `last_pointer` 进入预期区域 |
| `mouse_operation.hover` | 是 | 本机鼠标 + 本地等待 | 已有 | 共用 controller | 待测 | `hover_enter_count` 与 `hover_duration_ms` 达到要求 |
| `screenshot` | 是 | 本机截图 | 已有 | 共用 controller | 仅有组合证据；独立逐项待测 | 实际图像工件非空、尺寸正确并包含当轮 target 标识 |
| `launch_program` | 是 | 本机进程/窗口 | 已有 | 共用 controller | 待测 | 新 PID、窗口和当轮 JSON state 文件均存在 |
| `focus_window` | 是 | 本机窗口 | 已有 | 共用 controller | 待测 | OS foreground HWND 精确等于 target HWND |
| `clipboard_set` | 是 | 本机剪贴板 | 已有 | 共用 controller | YES 组合链通过；独立逐项待测 | 独立 OS clipboard reader 得到精确 nonce；空串允许用于清空 |
| `clipboard_get` | 是 | 本机剪贴板 | 已有 | 共用 controller | YES 组合链通过；独立逐项待测 | harness 先写 nonce，节点输出与后继可见文本精确一致 |
| `replay_step` | 是 | 本机 Replayer | 已有 | 已有 | 既有组合证据；未计入独立矩阵 | 既有双窗口 JSON/标题 oracle；仍需按 WPS tool 分支继续拆测 |
| `skill_replay` | 是 | 本地 Memory + Replayer | 已有 | 已有 | Studio 节点 E2E 待测 | 现有证据来自相邻的 Memory 页面链路，不是 Studio 节点 E2E |
| `agent_goal` | 否 | 模型 + 本机 Runtime | 已有 | 已有 | Studio 节点 E2E 待测；不计入离线矩阵 | 主 Agent 的相邻 E2E 不能替代 Studio `agent_goal` 节点 E2E |

### 严格独立矩阵：1/19（仅 `click`）

严格 case 的计数单位是“一个 DUT（device under test）节点”，不是“整张图只能有一个动作节点”。
为了让真实桌面动作具备合法目标和让 Runtime 到达可审计终态，case 可以加入通用的目标建立节点
或证据节点；这些辅助节点不计为独立通过，也不能替代 DUT 的外部业务 oracle。

2026-08-02 从可见 Electron Studio 打开
`python/winpilot_home/eval_targets/offline_flow_click_e2e.json`，点击校验和运行。图为
`start -> focus_window -> click -> screenshot -> end`：其中只有 `click` 是 DUT，
`focus_window` 只建立真实窗口目标，managed `screenshot` 只给 Runtime 提供完成证据。

运行前先验证了两种 fail-closed 行为：

1. 纯 `start -> click -> end` 被目标 scope guard 拒绝，外部 `click_count` 保持 0；
2. 目标建立和点击拆在两个 `action_batch` 后，旧实现因每个节点新建 `ToolSession` 而丢失绑定，
   仍被 scope guard 拒绝且没有物理点击。

通用修复是同一 ARP run 的 `action_batch` 共享一个 `ToolSession`，在 run 结束时统一关闭；同时
Renderer 与 Python graph contract 都做 path-sensitive 目标校验：空间动作之前必须在同一路径上
已建立目标，分支合流时任一前驱路径缺目标都会拒绝。修复没有增加应用配方、坐标补偿或任务分类。

最终 run `arp-20260802-052309-82cf06` 为 `done`，5 个节点全部 `ok`，
`trace_integrity=complete`，模型调用为 0。Runtime 之外的 JSON oracle 显示
`click_count: 0 -> 1`，其余目标输入计数全部保持 0。截图工件
`art-677d7d0ea779442c` 为 138390 bytes，SHA-256 为
`7a04783ba7c6d80ba81d0da86fd991b9e158a5315dc7608dd51b0c690e775e04`，视觉检查可见
`CLICKED 1`。业务通过依据是外部 JSON；截图只证明真实工件已捕获并为 graph 提供完成证据，
不能单独证明点击语义。因此本轮只把 `click` 计为 1 项，`focus_window` 与 `screenshot` 仍待各自
独立 E2E。

### 补充的组合 E 级批次（不计入 1/19）

2026-08-02 从可见 Electron Studio 打开
`python/winpilot_home/eval_targets/offline_flow_clipboard_screenshot.json` 并点击运行。流程为
`start -> clipboard_get -> condition`，YES 分支执行 `clipboard_set -> screenshot -> end`，
NO 分支执行另一个 `clipboard_set -> end`。实际只走 YES：独立 OS clipboard reader 精确得到
`OFFLINE-NODE-E2E-PASS-20260802`；run `arp-20260802-024859-8093ed` 的截图工件为
1920x1080、182009 bytes，manifest 与文件 SHA 一致，视觉检查确认为真实 Studio 桌面。

这只给 `start/end`、`clipboard_get/set`、`condition.YES` 与基础 `screenshot` 提供一条组合
证据。`condition.NO` 没有运行，截图没有包含并核对当轮 target nonce，各节点也没有做到
一 run 一被测节点；因此表中仍明确保留独立逐项待测，不能把这次批次称为全节点 E2E。

## 通用测试靶场

测试专用靶场位于：

```text
python/winpilot_home/eval_targets/offline_flow_node_target.py
```

它是普通 Tk 桌面窗口，不依赖网络，也不读取 WinPilot 状态。按钮、文本框、滚动区、
hover 区和拖拽区把可观察事实原子写入外部 JSON。示例启动命令：

```powershell
python python/winpilot_home/eval_targets/offline_flow_node_target.py `
  --state-file <isolated-temp-dir>/oracle.json `
  --case-id click-001
```

2026-08-02 已对靶场本身做过一次可见桌面 smoke：真实点击、输入、滚轮、hover、
拖拽分别得到 `click_count=1`、精确 nonce、`scroll_events=1`、
`hover_enter_count=1`、`drag_count=1`，关闭窗口后 `closed=true` 且 PID 退出。
这只证明 oracle 靶场可用，不算任何 Flow 节点的 E 级通过。

靶场当前也有明确边界：case id/nonce 没有签名，没有系统级全局点击日志，也没有独立验证
修饰键释放；`hover_duration_ms` 只在指针离开 hover 区时结算。在补齐这些能力之前，文档不得
把它描述为 signed harness，也不得用靶场内部计数证明目标窗口之外绝无输入。

每个 E 级 case 必须遵守同一链路：

1. harness 启动独立 target 并保存基线 JSON；
2. 像用户一样在可见 Electron Studio 中打开或编排只含一个 DUT 的流程；通用目标建立/证据节点
   可以存在，但不计作其他节点通过；
3. 从前端点击校验和运行，不直接调用内部 handler；
4. 等待 canonical RuntimeJob 终态；
5. 从 target JSON、OS HWND/clipboard 或图像工件读取独立结果；
6. 对多余动作、错误分支和重复修改同样判失败；
7. 清理当轮 PID、临时脚本和 oracle 文件。

## 执行顺序

按下列顺序逐组推进；未通过时不扩大生产节点面：

1. 精确设备输入：`click`、`input_text`、`keyboard`、`swipe`、四种 `mouse_operation`；
2. 纯本地与控制流：`start/end`、`wait.time`、`variable_set`、`condition`；
3. 门控：`evidence_gate`、`human_gate`；
4. 观察与窗口：`click_text`、`screenshot`、`wait.window`、`launch_program`、`focus_window`；
5. 剪贴板：`clipboard_set`、`clipboard_get`；
6. 组合 owner：`action_batch`、`replay_step`、`skill_replay`；
7. 联网对照：单独测试 `agent_goal`，不并入离线通过率。

下一轮不增加节点类型，先把现有 `screenshot` 的区域参数作为单一 DUT 做可见 Electron
实验：用户在属性面板直接框选，编译器明确输出 Runtime 已支持的
`region: {x, y, width, height}`，工件尺寸、SHA-256 和当轮随机标识由 Runtime 外部核对；
125% DPI、第二显示器/负坐标和用户取消都必须有明确结果。通过前保持当前 region fail closed，
也不开放历史图片模板节点。之后再沿上述顺序继续逐 DUT matrix；组合批次不能替代独立 case。
