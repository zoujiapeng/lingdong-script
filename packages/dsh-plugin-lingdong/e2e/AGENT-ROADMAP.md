# 下一步路线图 —— 朝向「极简且能力极强的全通用 agent」

写这份东西的前提是实测。先说清楚哪些是测出来的、哪些是判断。

## 零、第三臂跑出来了（2026-10-17，同一台机、同一批 oracle）

`node packages/dsh-plugin-lingdong/e2e/run.mjs` 现在跑**三**条臂。**干净数据**
（`output/e2e/report-1792201496311.json`，agent 臂单独重跑）：

| 臂 | 通过 | 模型步数 | tokens | 墙钟 |
|---|---|---|---|---|
| lingdong（本插件） | **12/12** | 178 | 6,739,075 | 745s |
| cua（自带驱动） | **12/12** | 273 | 14,119,687 | 1193s |
| **agent（原自研 agent）** | **10/12** | **220** | **3,726,867** | 1228s |

逐任务（agent 臂）：

| 任务 | agent 步数 | agent tokens | 结果 | 对照：lingdong |
|---|---|---|---|---|
| `shell_control` | 4 | 38,696 | PASS | 3 |
| `notepad_save` | 8 | 77,707 | PASS | 20 |
| `calculator_clipboard` | 89 | 1,150,473 | **FAIL**（422s 被取消） | 15 PASS |
| `read_target_text` | **3** | 28,427 | PASS | 7 |
| `read_target_pixels` | **3** | 28,405 | PASS | 7 |
| `fill_form` | 10 | 98,407 | PASS | 19 |
| `browser_confirm` | 5 | 47,506 | PASS | 9 |
| `window_frame` | 5 | 58,260 | **FAIL**（得到 250,188 800x600，且自报 `mission_verified`） | 29 PASS |
| `menu_path` | **4** | 38,047 | PASS | 7 |
| `terminate_app` | 61 | 1,873,916 | PASS（423s） | 7 |
| `browser_session` | 20 | 209,554 | PASS | 41 |
| `browser_drag` | 8 | 77,469 | PASS | 14 |

**这是本轮最重要的一张表，它推翻了"原 agent 追不上"的印象：**

1. **完成率 10/12，只输 2 个任务**，而且**tokens 比两条 DSH 臂都低**
   （3.73M 对 6.74M / 14.12M），步数 220 夹在两条臂中间（178 / 273）。
2. **大多数任务它更省**：`read_target_text` 3 步对 7 步、`read_target_pixels` 3 对 7、
   `menu_path` 4 对 7、`browser_confirm` 5 对 9、`notepad_save` 8 对 20。
   原因是它的固定前缀小（9k 对 27.6k / 51.4k）。
3. **它的问题是重尾，不是普遍弱**：两个任务炸掉。
   `calculator_clipboard` 89 步仍在循环（和首轮观察到的 106 次相同 `input_sequence` 是同一个病）；
   `terminate_app` 61 步 / 423s 勉强做成。
4. **`window_frame` 是一条"假成功"**：它 5 步就把窗口弄成了 250,188 800x600
   （自己写了 `movewin.ps1` 调 `MoveWindow`），然后 `mission_verified`。
   oracle 判 200,150 640x480 不成立。**这正是"停止判断/自验证"缺口的现场证据。**

**必须一起交代的一件事：首轮三臂数据是错的，已作废。**
首轮报 `agent 3/12`，原因是我的 harness 缺陷 —— 420s 超时后没有取消 run，
agent 继续在跑，而 harness 已经把下一个任务提交上去，后面的任务是在
"上一条目标还在点鼠标"的桌面上判定的。trace 时间戳是铁证：`read_target_text`
07:52 开始、08:52 才落盘；`menu_path` 08:44 开始、09:22 才落盘。
修复后重跑，同一个任务从"超时失败"变成 **3 步通过**。
**结论：作废首轮的 3/12，不要再引用它。**
为防止以后被误引用，那份报告已改名为 `output/e2e/VOID-report-1792200180356.json`；
单任务验证的那份也改成了 `PROBE-report-*.json`，两者都不会再进 `compare.mjs` 的轮次。

### 怎么读多轮数据

```powershell
node packages/dsh-plugin-lingdong/e2e/compare.mjs              # 最近两轮
node packages/dsh-plugin-lingdong/e2e/compare.mjs --rounds 3   # 最近三轮
```

它会逐任务并列每轮的 `PASS/FAIL + 步数 + tokens`，并在**同一任务两轮判定不同**时标出来。
单轮不是率 —— 这正是本轮新增它的原因。

---

## 一、已测出来的五件事

### 事实 1：成本不是问题

| | 首步 prompt tokens | 每步上下文增长 |
|---|---|---|
| **原 agent** | **9,140**（本次实测，1 回合的 run） | **+380** |
| DSH A 臂（40 工具） | 27,596 | +713 |
| DSH C 臂（80 工具） | 51,377 | +2,921 |

原 agent 的固定开销最小、上下文涨得最慢。**它在成本维度不需要"追"。**

### 事实 2：粒度是最大的差距，而且是免费的

同一批任务里，DSH 的 A 臂靠 `lingdong_desktop` **一次提交一整批动作**，
C 臂是十几次单动作调用 —— 2.0× 步数差主要从这里来。

原 agent 手里有同一个能力的对应物 `input_sequence`（原子时间线），
但在 28 条 trace、309 次提议里**只用了 8.7%**。

### 事实 3：完成率的瓶颈在三处，两处是机械的

- **停止判断**：81 行记事本任务第 75 回合已完成，跑到 100 回合预算耗尽记 failed
  （`docs/handover/main-product-history.md:1456-1460`）
- **长程状态**：Blender / Calculator 因菜单识别 + 长期状态重复，60–100 回合耗尽（同文件 1462-1464）
- **几何定位**：1120×630 把 44px 压成 22px，肘部差 27–50px 必出界（同文件 1416-1417）

前两条是上下文/回执问题，第三条是模型 + 分辨率问题。
**注意：第三条"追 DSH"没有意义 —— DSH 在同一台机器上同样受 1120 帧限制。**

### 事实 4（本次新测）：非 GUI 任务上，回执不可靠

`shell_control` 是本轮唯一不需要 GUI 的对照任务：

| 臂 | 步数 | 墙钟 | 判定 |
|---|---|---|---|
| lingdong | 3 | 18s | PASS |
| cua | 3 | 9s | PASS |
| **agent（原 agent）** | **7** | 22s | PASS |

逐调用看（`explain.mjs` 现在能读原 agent 的 trace）：

```
1. run_shell  powershell -NoProfile -Command "...WriteAllText(..., ::new($false))"
   -> 回执里是 ::new(False)          # 外层 PowerShell 把 $false 展开了
2. run_shell  ...$b=[IO.File]::ReadAllBytes(...); 'len=' + $b.Length...
   -> 回执是 "=[IO.File]::ReadAllBytes(...)"   # $b / $_ 被展开成空
3. run_shell  同一条命令
   -> 未能找到文件 t6.txt
4. run_shell  Test-Path / Get-ChildItem
5. run_shell  Get-ChildItem（目录里确实没有 t6.txt）
6. run_shell  写 + 读一条搞定   -> len=12 hex=4C 44 ...
7. claim_obligation_done
```

写文件的活一行就够，它用了 6 次 shell。
根因是**模型自己把 PowerShell 又套进一层 `powershell -Command "..."`**，
双引号里的 `$false` / `$b` / `$_` 被外层先展开掉了，于是第一次写没落盘、后两次读报"文件不存在"。

### 事实 5（本次新测）：GUI 动作的重复对模型**完全不可见**

`calculator_clipboard` 上原 agent 跑满 426 秒被判失败。它的 trace（`arp-20261017-074517-25d5be.jsonl`）：

- **112 次模型调用**，111 次物理尝试
- 提议构成：**106 × `input_sequence`**、3 × `launch_application`
- **111 条回执全是 `outcome=applied` / `input_completed=True` / `error_kind=''` / `target_hwnd=0`**
- 尾部每一次 `input_sequence` 的内容**逐字相同**：
  `click left 114,347; click left 45,461; click left 114,500; click left 252,421; ...`（自己就重复了十几轮）

也就是说：机械层每次都如实报告"已投递"，屏幕上却始终不是 731，而模型手上**没有任何事实**
能告诉它"这条完全相同的动作你已经做了 40 次"。

代码上这是确定的，不是推测：

- `project_actor_turn_input` 只从 **`card.last_transition`** 构造 `previous_action`
  （`vnext/runtime/contracts.py:817-830`），`MissionState` 也只保存 `last_transition` 一个
  （`vnext/mission/contracts.py:284`）—— **没有列表，没有计数**。
- `visual_actor.py` 里确实有一套重复标记（`_repeated_tool_result`：`result_unchanged` /
  `repeat_count`），但 `seen_tool_results` 只在**非 GUI 工具**的结果路径上被喂
  （2604 创建，2685–3020 全是 connector / shell / window-text / list-windows）。
  **GUI 提议不产生 tool result，所以这套机制对 GUI 循环一次都不会触发。**

对照：DSH 的 loop 正好有这一件 —— `dsh-repeat-tool-reminder`，在**第 3 / 5 / 8 次**完全相同的
调用时提醒模型"看上一轮结果，换办法或收尾"。**这是本次对比里最具体、最可复现的一条机械差距。**

---

## 二、因此，"极其必要的基础"只有四件

筛选标准：**每一条都必须对着一条已实测的失败**，否则不做。

### 0（新发现，最高优先）：`run_shell` 的子进程必须声明 DPI 感知

- 证据：`window_frame` 两轮得到**逐像素相同**的错答案 `250,188 800x600`，求的是 `200,150 640x480`；
  四个数字各自恰好 **×1.25**（本机显示缩放 125%）。
- 已用对照实验钉死（`e2e/probe-shell-dpi.mjs`，同一窗口、同一次 `MoveWindow`，只差 DPI 声明）：
  ```
  dpi-UNAWARE shell -> 250,188 800x600   SCALED x1.25
  dpi-AWARE   shell -> 200,150 640x480   EXACT
  ```
- 归属：**机械层**。模型给的数字是对的，是它借道的 shell 把坐标放大了。
  `action/shell.py` 里没有 `SetProcessDpiAwarenessContext`。
- 最小改动：在该文件的 PowerShell 前导里加一句 DPI 声明（进程级，一次即可）。
- 为什么它排第一：**一行、可验证、且直接换回一整个任务**（`window_frame` 10/12 → 11/12 的候选）。

### 1：让"这条动作我已经重复了 N 次"成为模型看得见的事实

- 证据：事实 5 —— 106 次逐字相同的 `input_sequence`，111 条 `applied` 回执，
  模型手上没有任何反重复事实。
- 代码依据：`last_transition` 单值（`vnext/mission/contracts.py:284`）+
  重复标记只走非 GUI 结果路径（`visual_actor.py:2604, 2685-3020`）。
- 最小改动：在 `ActorPreviousActionView` 上增加一个**机械计数**
  （"当前提议与上一次 transition 是否逐字相同、已连续多少次"），
  它和 `budget` 是同一类东西 —— **事实，不是指令**。runtime 不判断该不该继续，
  只把"你已经原样重复了 N 次"告诉模型。
- 反例警戒：**不要**做成"第 N 次就拒绝"或"自动换策略"。那是机械层替模型做决定。

### 2：回执要能区分「按住了目标」和「按在空气上」

- 证据：本轮 111 条回执全部 `target_hwnd=0` —— 物理层根本没有"这一点按在了哪个窗口/控件上"
  这个事实；文档里也已记录 corridor-v0 空按 13 次（history:1413-1415）。
- 与第 1 条互补：第 1 条说"你重复了"，这条说"你按的那一下到底打到了什么"。
- 最小改动：`click` / `drag` 的 raw receipt 增加"按下点命中的窗口/进程"。
  这同样是给模型真实信息，不替它判断。

### 3：钉死 shell 面为什么被套娃

- 证据：事实 4 的 5 步浪费。
- 要回答的问题：`run_shell` 的 schema 是否让模型以为"这只是一个普通命令行"，
  于是它自己再套一层 PowerShell？
- 这属于**工具事实**（我的命令在哪一层执行），不是"教模型怎么做"。

### 已排除的一条（重要）

我原先以为"`input_sequence` 只用了 8.7%，所以批量能力没被认领"是主要杠杆。
**本次实测推翻了这个判断**：calculator 上它用了 106 次 `input_sequence`，
步数照样烧光。所以"批量"不是瓶颈，**"重复而不知情"才是**。
（历史 8.7% 是跨任务平均，被大量 shell/click 任务稀释了。）

---

## 三、明确不做

不做这些的理由不是保守，是它们会直接毁掉"极简"：

- ❌ 第二角色 / 验证者 / 分类器 / 应用 recipe
  （根 `AGENTS.md` 明令禁止；18/18 是**删掉**这类东西之后拿到的）
- ❌ 把 DSH 的 30 节提示词搬进来
  （DSH 的 C 臂光固定开销就 51.4k tokens/步，是原 agent 的 5.6×）
- ❌ 为了压步数砍掉模型自发的校验
  （`e2e/RESULTS.md:87` 已经写明：那可能正是完成率优势的来源）
- ❌ 在没有第三臂数据的情况下宣称"完成率提升了"

---

## 四、顺序

1. ~~把第三臂固化进 `run.mjs`~~ —— **本轮已完成**：
   `--arm agent` 直连 `/api/v2/goals/runs`，同一个 oracle，同一个报告。
   从今往后任何"完成率"的说法都必须带上它，否则无法证伪。
   附带修好的两件事：
   - 超时后**取消 run** 并等到 runtime 空闲（否则后面的任务会被上一条目标干扰）
   - `explain.mjs` 现在能直接读原 agent 的 trace（按 `run_id`），不受 transcript 截断影响
2. **修 harness 的另一个洞**：agent 臂会**往工作树里写文件**。
   本轮它在 `python/winpilot_home/` 留下了一个 `focus_ldform.ps1`
   （为了把 LD Form 窗口拉到前台自己生成的），把"用户 82 个改动"这条不变量打破了，
   已手动删除。跑 agent 臂之前/之后需要一起清理，否则每次跑都会污染工作树。
3. **把"单轮"变成"多轮"**：单轮不是率。`compare.mjs` 已备好，
   每个进入结论的臂至少要 **2 轮**（`AGENTS.md` 原本要求 ≥3 轮的规矩没变，
   只是先把双轮跑起来）。当前 lingdong/cua 各 1 轮、agent 2 轮。
4. **扩任务集** —— **本轮已完成两个**：`cross_app_pipeline`（Calculator → Notepad 存盘）
   与 `cross_window_token`（LD Target → LD Form）。两个都在插件臂上验证成立
   （22 / 28 步），而原 agent **两个都跑到 86–88 步超时**。
   这批数据把诊断从"长程能力弱"收窄成**"跨边界的状态传递缺失"**：
   边界内自洽的任务它比插件臂更快，凡是值必须跨过一道边界的任务它都循环到超时。
   接下来再补的应该是**同一类里的其它边界**（跨窗口拖放、跨应用复制粘贴），
   而不是继续加单应用任务。
5. **按上面四件基础，一次只动一件，动完重跑三臂。**
6. **三臂数据进 `e2e/RESULTS.md`**，并保留结论边界。

---
## 五、补丁设计（**已全部授权并落地**）

> 用户已授权修改 `vnext/**` 与 `action/shell.py`。下面是**实际做了什么**，
> 以及每条改动被什么证据支持、复验方式是什么。

### 已落地 0：shell 子进程声明 DPI 感知

- 文件：`winpilot/action/shell.py`（`_PS_DPI_PREFIX`，插在 UTF-8 前导之前）。
- 复验：`e2e/probe-shell-dpi.mjs` → dpi-AWARE 一条 `EXACT`；
  再直连 shell 实测 → `[200, 150, 640, 480]`，且模型自己再调 `SetProcessDpiAwarenessContext`
  会返回 FALSE（证明前缀已生效）。
- **代价**：约 **+199 ms / 次 shell 调用**（`Add-Type` 编译），实测得出。
  `__COMPAT_LAYER=HIGHDPIAWARE` **不管用**（已实测否证）。
- **残留**：模型把活写进 `.ps1` 再用 `powershell -File` 启动时，**第二层进程不继承**
  DPI 声明（Windows 不传递该上下文）。这一段由下面的"shell 工具事实"处理。

### 已落地 3：shell 工具的事实说明

- 文件：`vnext/model_roles/visual_actor.py`（`_shell_tool_definitions`）。
- 补的是**工具事实**：命令本身已经在 PowerShell 进程里；嵌套
  `powershell -Command/-File` 会起一个**不继承**本进程状态（变量、DPI 感知）的新进程。
- 效果：`window_frame` 上模型不再套娃，改用直连命令，
  错答案从 `250,188 800x600`（正好 ×1.25）变成 `190,140 660x500`（＝ DWM 外框 200,150 640x480）。

### 已落地 1：`same_action_attempts` —— 同一动作本任务内已尝试几次

- 文件：`vnext/mission/contracts.py`、`vnext/runtime/contracts.py`、
  `vnext/runtime/mission_runtime.py`。
- **第一版做错了，记录下来**：先实现的是"**连续**相同次数"。实测它在真实循环里
  **从不触发** —— calculator 那轮 34 次 `input_sequence` 只有 **4 种**序列，
  模型在几种变体之间**交替**，连续计数永远回到 1。
- 改成**本任务内累计**：`sha256(action + canonical args)` 计数，`local_intent` 排除在外
  （它是模型的每轮散文，把它算进去会让每次重试都"看起来是新的"）。
  实测交替序列也能累加：`1,1,2,2,3,3`。
- **效果是真实的但不稳定**：改完后**单独**跑 `calculator_clipboard`
  一次拿到 **8 步通过**（模型改用 `type_text` 输入算式，而不是点键盘），
  但**同一天的全量轮里它又回到 38 步失败**。
  → **一条机械事实能改变策略选择，但不能保证每次都改。**

### 未落地 2：按下点命中回执

**刻意没做。** 本批 14 个任务里没有任何一条失败能归因于"点在了空气上"：
`window_frame` 是 DPI/外框定义，`calculator` / 跨边界任务是重复循环。
按根 `AGENTS.md`"只修实测证明分歧的那一层"，没有证据就不加。
等有 (a) 空按类任务 或 (b) 明确的 `applied` 但无变化证据时再做。

### 顺带修好的一件：`window_frame` 的 oracle 本身有歧义

不是 agent 的错，是**任务定义**的错。同一个窗口有两个都正确的"外框"：

| API | 读数 | 含义 |
|---|---|---|
| `GetWindowRect` | 660x500 @190,140 | 含 Win10+ 的隐形边框；插件的 `set_window_frame` 瞄准它 |
| `DwmGetWindowAttribute(9)` | 640x480 @200,150 | 人真正看到的外框；agent 瞄准它 |

原来的 oracle 只认前者 —— 等于在量"你猜中了哪个 API"，不是"你能不能摆好窗口"。
已改成**两者任一命中即通过**（`windowFrames()`），三条臂复跑全部通过
（lingdong 13 步 / cua 18 步 / agent 4 步）。
**这也解释了第 4 轮那条"`window_frame` 是 cua 唯一失败项"** —— 那是同一类约定分歧，
不是能力差。

---

## 六、结论边界

- 事实 1/2/4 是本轮实测；事实 3 是**文档记载**，本轮未复现。
- 原 agent 用的模型是 `mimo-v2.6-flash`，两个 DSH 臂用的是 `deepseek-flash`。
  **三个臂不是同一个模型**，所以这是"整栈对比"，不是"纯 agent 对比"。
- `shell_control` 只有 1 个任务、1 次运行。它的价值在于暴露机制，不在于统计。
- 单机、单屏（125%）。
- 第三节的三件基础**尚未实施**；实施前需要用户授权碰 `vnext/**`。
