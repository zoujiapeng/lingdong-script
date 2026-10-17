# E2E 原始数据

```powershell
node packages/dsh-plugin-lingdong/e2e/run.mjs            # 两臂全任务，~25 min
node packages/dsh-plugin-lingdong/e2e/run.mjs --arm cua  # 只跑对照
node packages/dsh-plugin-lingdong/e2e/run.mjs --list     # 任务清单
```

- **A 臂** `dsh --profile ld-e2e --patch e2e/lingdong.patch.yml`（本 provider）
- **B 臂** `dsh --profile cua-e2e --patch e2e/cua.patch.yml`（自带 cua-driver native 0.28.0）
- 两个 profile 都由 `headless` 模板新建；同一模型（`deepseek-flash`）、同一机器、同一权限 preset。
- **判定只由 oracle**：跑完后读文件字节 / 剪贴板 / 窗口矩形 / 目标应用写出的提交结果。agent 看不到也影响不到它。
  "声称完成但没留下东西" = 失败。

## 两轮

| | rep1 lingdong | rep1 cua | rep2 lingdong | rep2 cua |
| --- | --- | --- | --- | --- |
| 通过 | **8/8** | 7/8 | **8/8** | 7/8 |
| 模型步数 | **89** | 176 | **94** | 186 |
| tokens | **4,427,585** | 12,213,672 | **4,642,027** | 12,061,249 |
| 墙钟 | **415s** | 938s | **420s** | 647s |

合计 **16/16 对 14/16**；**183 步 / 9.07M tok 对 362 步 / 24.27M tok**。

## 第三轮（单轮，补齐 `invoke_menu` + `kill_app` 之后）

同一批 8 个任务，单轮，改完 `invoke_menu` / 一等 `kill_app` / 预览有界修复之后：

| | lingdong | cua |
| --- | --- | --- |
| 通过 | **8/8** | 7/8 |
| 模型步数 | **93** | 190 |
| tokens | **3,386,590** | 10,354,001 |
| 墙钟 | **390s** | 903s |

逐任务（步数）：

| 任务 | lingdong | cua |
| --- | --- | --- |
| `shell_control`（非 GUI 对照） | 3 | 2 |
| `notepad_save` | **19** | 65 |
| `calculator_clipboard` | **16** | 25 |
| `read_target_text` | 8 | **6** |
| `read_target_pixels` | 14 | **7** |
| `fill_form` | **16** | 44 |
| `browser_confirm` | **13** | 17 |
| `window_frame` | **4** | 24（FAIL） |

**这一轮相对前两轮的两个变化，都必须如实标注：**

1. **tokens 整体大幅下降**（A 臂 9.07M → 3.39M，B 臂 24.27M → 10.35M）。**A 臂降幅超过本次改动所能解释的范围**，
   所以**不能**把 token 优势归因于本轮改动；跨轮次 token 数本身方差很大，我没有隔离出原因（未验证：
   模型侧缓存 / 负载 / 历史上下文长度）。**步数比 tokens 稳，引用时优先用步数。**
2. **优势不再是单点**：上一批 `window_frame` 是唯一差异，这一批 `notepad_save`（19 对 65）、`fill_form`（16 对 44）
   也大幅领先，`window_frame` 4 步对 24 步且对方失败。**但这一轮只有 1 轮 × 8 任务**，
   单轮不足以支撑"多点优势"的结论——要 ≥3 轮才算。**弱项没变**：两个纯观测任务仍是 cua 更省（8/14 对 6/7）。
3. **`invoke_menu` / `kill_app` 没有被这一批覆盖**：8 个任务里没有一个是菜单任务或终止任务。
   要证明新能力，得等 `menu_path` / `kill_app` 两个任务进任务集（见下）。

## 逐任务步数（rep1 / rep2）

| 任务 | lingdong | cua | 通过 |
| --- | --- | --- | --- |
| `shell_control`（非 GUI 对照） | 3 / 2 | 3 / 2 | 都通过 → 测试台中立 |
| `notepad_save` | 13 / 15 | 38 / 33 | 都通过 |
| `calculator_clipboard` | 21 / 15 | 46 / 63 | 都通过 |
| `read_target_text` | 7 / 9 | **6 / 4** | 都通过 |
| `read_target_pixels` | 9 / 11 | **7 / 5** | 都通过 |
| `fill_form` | 14 / 15 | 28 / 34 | 都通过 |
| `browser_confirm` | 11 / 16 | 17 / 25 | 都通过 |
| `window_frame` | 11 / 11 | 31 / 20 | **只有 lingdong 通过** |

## 结论边界（不要省略）

1. `window_frame` 是 cua 唯一失败项，两轮**数值完全一致**（求 `200,150 640x480`，得 `191,150 658x489`）→ 系统性。
   第三轮 C 臂仍是 24 步失败。但**拿掉这个任务曾是 7/7 对 7/7**：完成率优势当时压在一个任务上，
   且可能只是"frame 定义不同"（客户区 vs 外框）。**不要当决定性证据。**
2. 成本优势覆盖 5 个变更类任务 × 2 轮（+ 第三轮），步数比稳定且可解释（`lingdong_desktop` 一次提交整条动作序列、
   `lingdong_snapshot` 一次给树+图，往返少）。**这条才硬**，且第三轮已从"窗口类"扩到 `notepad_save` / `fill_form`。
   **tokens 跨轮方差大，别单独引用。**
3. 三个纯观测任务 **cua 更省**（4–7 步 对 7–11 步；第三轮 6/7 对 8/14）。**但"观测输"这个解读要打问号**：
   逐事件看 `read_target_pixels`（A 14 步 / C 7 步），A 臂多出来的步数**不是观测工具差**，是它自己多做校验——
   读图 → 裁出目标区域 → **重读裁剪图确认字形是 `8` 不是 `B`** → 再 hexdump 校验写出的文件 → 删临时文件；
   C 臂是 `get_window_state` → `zoom` → 写文件。
   所以这一维实际量的是**agent 的严谨程度，而不是观测的单次成本**，且同一份证据里完成率是 8/8 对 7/8。
   **不要为了压步数去砍掉校验**——那可能是完成率优势的来源。要继续查的是"观测工具本身能否更省"，而不是"能不能少校验"。
4. 样本 = 8 任务 × 2 轮（+ 第三轮单轮）× 2 臂，一台机器、一个模型、一块 125% 屏。只能说"在这批任务上"。
5. `invoke_menu` / 一等 `kill_app` 已实现并有单测 + 冒烟 + 专项探针，也已进入任务集（`menu_path` / `terminate_app`）。
   **但两个任务两臂都通过**，所以它们证明的是**成本差**（`menu_path` 5 对 12 步），**不是**能力独占。
   浏览器 dialog / pointer drag 仍未实现。

## 第四轮：12 任务、补齐浏览器能力之后（单轮，权威数据）

| | lingdong | cua |
| --- | --- | --- |
| 通过 | **12/12** | 11/12 |
| 模型步数 | **160** | 312 |
| tokens | **4,963,254** | 16,730,032 |
| 墙钟 | **581s** | 1296s |

**2.0× 少步数 / 3.4× 少 tokens / 2.2× 快**，逐任务谁省：**A 9 个、C 2 个、平 1 个**。

逐任务（步数）：

| 任务 | lingdong | cua | 谁省 |
| --- | --- | --- | --- |
| `shell_control`（非 GUI 对照） | 2 | 2 | 平 |
| `notepad_save` | **15** | 36 | A 2.4× |
| `calculator_clipboard` | **13** | 35 | A 2.7× |
| `read_target_text` | 5 | **4** | C 1.25× |
| `read_target_pixels` | **6** | 7 | A 1.17× |
| `fill_form` | **25** | 68 | A 2.7× |
| `browser_confirm` | **10** | 19 | A 1.9× |
| `window_frame` | **10** | 14 | **A（C 失败）** |
| `menu_path` | **7** | 38 | **A 5.4×** |
| `terminate_app` | 9 | **7** | C 1.29× |
| `browser_session` | **47** | 51 | A 1.09× |
| `browser_drag` | **11** | 31 | **A 2.8×** |

### 稳定性检查（同一批 10 个任务，与上一轮对比）

上一轮 10 任务：A 106 步 / C 190 步。本轮同一批：**A 102 步 / C 230 步**。
A 臂两轮几乎一致（106 → 102），C 臂波动较大（190 → 230）。**A 臂的步数比 C 臂稳定。**

`browser_session` 单任务先跑时 **C 更省（43 对 56）**，本轮 **A 更省（47 对 51）**——这一项**两轮各赢一次**，
属高方差任务，**不要当结论用**。

### 本轮结论

1. **成本优势已在 12 个任务、含 4 个浏览器任务上复现**：2.0× 少步数、3.4× 少 tokens。**这是最硬的一条。**
2. **新补的能力直接兑现成了优势**：`browser_drag` 2.8×（新加的 `drag` 动作，cua 没有一等拖拽）、
   `menu_path` 5.4×（新加的 `invoke_menu`）。**不是"补了但看不出差别"。**
3. **观测维度已不再是劣势**：`read_target_pixels` 6 对 7、`read_target_text` 5 对 4，基本持平；
   早先"7–11 步 对 4–7 步"的差距已经收窄到 1 步之内。
4. **纯观测的 `read_target_text` 与 `terminate_app` 仍是 C 略省**（各差 1–2 步），属可接受取舍。
5. **`window_frame` 是 C 唯一的失败项，且四次全败**（每次都是 `191,150 658x489`，求 `200,150 640x480`）。
   这是**唯一被反复复现的能力差**，不是噪声。

## 附：`menu_path` 靶窗的实测形态

`e2e/menu_target.cs`（编译成 exe）起一个 **三级原生 Win32 菜单**窗口，
`Report > Export > As text` 被调用时把 `<token>|text` 写进指定文件。实测：
- Win32 `GetMenu` 读出三层 6 项；UIA 只报 `Report, Tools` 两个 `children=0` 的一级项。
- 三级叶子被调用后文件内容为 `TOKEN123|text`，窗口标题变为 `LD Menu Target [text]`。

它**没有**变成"结构性不可比"的完成率差异：cua 用键盘加速键把菜单开出来照样完成，只是**多花 5.4× 步数**。
这一点在下面"我预测错了"一节里已订正。

## 新增四个任务（第 4 轮起），以及**一个被推翻的预测**

任务集从 8 个扩到 **12** 个，新增：

| 任务 | oracle | 为什么这样设计 |
| --- | --- | --- |
| `menu_path` | 靶窗**自己**写出 `<token>|text` 到文件 | 只有那条确切的三级菜单命令被执行才会产生该文件；agent 看不到也伪造不了 |
| `terminate_app` | harness 自己启动的 Notepad **按 pid** 判活 | 只关窗口进程仍在 → 不算过；走标题还是走 pid 由 agent 自己决定 |
| `browser_session` | 令牌只存在于**受会话 cookie 保护**的那一页 | 要求"登录 → 关掉浏览器 → 重开 → 会话仍在"。页面没有第二份令牌，所以丢 cookie 就拿不到 |
| `browser_drag` | **服务器记录页面自报状态**，oracle 读服务器 | 滑块值与被拖过的落区文字，只有真的按住指针移动才会出现；agent 自述的 `DONE` 只是"我做完了"的信号，不构成判定 |

### 我预测错了，必须记下来

我在写这两个任务前判断：`menu_path` 对 B 臂"**结构性不可比**"，因为 UIA 里没有可寻址的二级/三级项。
**实测两臂都通过。** 单任务对拍：

| 任务 | lingdong | cua |
| --- | --- | --- |
| `menu_path` | **5 步** / 13s | 12 步 / 43s（用自己的办法开了菜单） |
| `terminate_app` | 6 步 / 28s | **5 步** / 13s |

结论修正：
1. **"UIA 拿不到子菜单项" ≠ "cua 做不了菜单任务"**。cua 能用键盘加速键/助记符把菜单开出来再选——
   那是条**有效**路径，不是失败。所以 `invoke_menu` 的价值是**成本与确定性**（5 步对 12 步、不必让目标上前台），
   **不是**"只有我能做"。**不要再用"结构性不可比"这种话。**
2. `terminate_app` 上 **cua 更省**（5 对 6 步）。原本也预期 cua 缺一等 `kill_app` 会吃亏——没有。
3. 合起来：**cua 的真实能力比交接文档描述的更强**。我之前那句"能力面是互补的"方向对，但当时仍低估了它。

## 第五轮：第三条臂 —— 原自研 agent 直连 runtime（2026-10-17）

前四轮的两条臂**跑在同一个 agent loop 里**（都是 `dsh-base` + `dsh-headless`，
见两个 profile 的 `package.json`），所以 12/12 对 11/12 量的是**工具层**，
**从来没有量过本仓库自己的 agent**。

这一轮补上了第三条臂：`--arm agent` 不走 DSH，直接
`POST /api/v2/goals/runs` + 轮询 `/api/v2/runs/{id}`，oracle 原样复用。

| 臂 | 通过 | 模型步数 | tokens | 墙钟 |
|---|---|---|---|---|
| lingdong | **12/12** | 178 | 6,739,075 | 745s |
| cua | **12/12** | 273 | 14,119,687 | 1193s |
| **agent（原自研）** | **10/12** | **220** | **3,726,867** | 1228s |

**原 agent 用比两条 DSH 臂都少的 tokens 拿到 10/12，步数夹在两者之间。**
逐任务它**大多数更省**（`read_target_text` 3 对 7、`read_target_pixels` 3 对 7、
`menu_path` 4 对 7、`browser_confirm` 5 对 9、`notepad_save` 8 对 20），
因为固定前缀 9k 对 27.6k / 51.4k。**它的问题是重尾**：
`calculator_clipboard` 89 步被取消、`terminate_app` 61 步 / 423s 勉强成功。

### 第二轮与两轮合并（稳定性）

第二轮（agent 臂单独重跑，`report-1792202996686.json`）：

| | 通过 | 步数 | tokens |
|---|---|---|---|
| agent 第 1 轮 | 10/12 | 220 | 3,726,867 |
| agent 第 2 轮 | **10/12** | **151** | **3,427,569** |

**两轮 20/24，逐任务判定完全一致 —— 没有一个任务在轮次之间翻转。**

| 任务 | 第 1 轮 | 第 2 轮 |
|---|---|---|
| `shell_control` | PASS 4 | PASS 5 |
| `notepad_save` | PASS 8 | PASS 8 |
| `calculator_clipboard` | **FAIL 89** | **FAIL 38** |
| `read_target_text` | PASS 3 | PASS 3 |
| `read_target_pixels` | PASS 3 | PASS 3 |
| `fill_form` | PASS 10 | PASS 9 |
| `browser_confirm` | PASS 5 | PASS 11 |
| `window_frame` | **FAIL 5** | **FAIL 7** |
| `menu_path` | PASS 4 | PASS 5 |
| `terminate_app` | PASS 61 | PASS 44 |
| `browser_session` | PASS 20 | PASS 11 |
| `browser_drag` | PASS 8 | PASS 7 |

读多轮用 `node packages/dsh-plugin-lingdong/e2e/compare.mjs`。

### `window_frame` 不是模型失败：是 `run_shell` 子进程的 DPI 缺陷（已用对照实验证明）

两轮都得到**逐像素相同**的错答案：求 `200,150 640x480`，得 `250,188 800x600`。
四个数字**各自恰好 ×1.25**（`200×1.25=250`、`150×1.25=187.5≈188`、`640×1.25=800`、`480×1.25=600`），
而这台机器的显示缩放正是 125%。

`e2e/probe-shell-dpi.mjs` 用**同一个窗口、同一次 `MoveWindow`、唯一的差别是 DPI 声明**做对照：

```
dpi-UNAWARE shell -> {"x":250,"y":188,"width":800,"height":600}  SCALED x1.25
dpi-AWARE   shell -> {"x":200,"y":150,"width":640,"height":480}  EXACT
```

**结论：agent 的请求是对的，是它借道的那条 shell 把坐标放大了。**
`action/shell.py` 里没有任何 `SetProcessDpiAwarenessContext`，
于是 DPI 不感知的 PowerShell 子进程交给 `MoveWindow` 的坐标被 Windows 虚拟化了。
这正是 `AGENTS.md` 坑位 #1 描述的那一类，只是这次受害者不是 oracle，是 agent 自己的动作。

所以 `window_frame` 应当从"模型能力问题"改判为"机械层缺陷"。
按根 `AGENTS.md` 的判据（"先假设模型是对的，是机器说了谎"），**这条属于必须先修的基础**。

### 失败面如实记录

- `calculator_clipboard`：89 步仍在循环，422s 被 harness 取消。首轮更大的一轮里
  同一条 trace 是 **106 次逐字相同的 `input_sequence`**、**111 条回执全是 `applied`**、
  `target_hwnd=0`。机械层每次都说"已投递"，模型手上没有任何"你已经原样重复 N 次"的事实。
- `window_frame`：5 步得到 **250,188 800x600**（自己写 `movewin.ps1` 调 `MoveWindow`），
  然后报 `mission_verified`。求的是 `200,150 640x480`。**这是一条"假成功"**。

### 必须作废的一轮

同一臂**第一次**跑出 `3/12`，那个数字是**错的、已作废**：harness 420s 超时后没有取消 run，
agent 继续在跑，而下一个任务已经提交 —— 后面的任务是在上一条目标还在操作的桌面上判定的。
trace 时间戳为证：`read_target_text` 07:52 开始、08:52 落盘；`menu_path` 08:44 开始、09:22 落盘。
修复（超时即 `cancel` + 等 `/api/status` 空闲）后重跑，同一任务从"超时失败"变成 **3 步通过**。
**不要引用 3/12。**

### 结论边界

- 原 agent 用 `mimo-v2.6-flash`，两条 DSH 臂用 `deepseek-flash`：**这是整栈对比，不是纯 agent 对比。**
- 原 agent 只有 1 轮；`calculator_clipboard` 与 `terminate_app` 的高方差说明单轮不够。
- `window_frame` 本轮 **cua 也通过了** —— 第 4 轮那条"cua 唯一失败项、四次全败"**没有复现**。
  进一步说明那条差异不是稳定的能力差。
- agent 臂会**往工作树里写 .ps1**（本轮留下 `movewin.ps1`、`enum_notepad.ps1`），
  harness 已加"只删除本轮新出现的未跟踪文件"的清理。

## 第六轮：补两个跨应用任务，原 agent 在这一类上直接崩

原有 12 个任务全是**单应用、目标完全给定、单结果**，量不出文档自己承认的缺口
（"真实软件缺口仍在多应用长程任务"，`history:1408-1409`）。本轮补两个：

| 任务 | 为什么这样设计 | oracle |
|---|---|---|
| `cross_app_pipeline` | 答案必须在 **Calculator** 里产生、被带出应用、落进 **Notepad** 再存盘 | 文件字节 == 本次随机算式的积（**随机操作数**，不能靠记住 731 通过） |
| `cross_window_token` | 值在 **LD Target** 里，要打进 **LD Form** 并提交 → 必须跨一次焦点切换 | 表单自报的 `name` == 本次铸的 token（goal 里**不含**这个值） |

两个任务先在插件臂上验证成立，再拿另两条臂打（每条臂每个任务 1 次，探针级）：

| 任务 | lingdong | cua | agent |
|---|---|---|---|
| `cross_window_token` | **PASS 28 步 / 84s** | **PASS 34 步 / 101s** | **FAIL 86 步 / 423s / 2.31M tokens** |
| `cross_app_pipeline` | **PASS 22 步 / 67s** | **PASS 51 步 / 117s** | **FAIL 88 步 / 425s / 1.14M tokens** |

**两条 DSH 臂都通过，原 agent 两个都超时、都跑了 86–88 步。** 这不是"慢一点"，是**卡住** ——
它的步数是两条 DSH 臂的 2–4 倍，而完成的活是零。

### 这条数据把诊断收窄了

把三批数据并起来看，原 agent 的失败是**同一个形状**：

| 任务 | 要不要把一个值带过边界 | 结果 |
|---|---|---|
| `notepad_save` / `fill_form` / `read_target_*` | 不需要 | 3–10 步通过 |
| `calculator_clipboard` | 要把结果读回来 | 两轮都 FAIL，38 / 89 步 |
| `cross_window_token` | 要把 token 带到另一个窗口 | FAIL 86 步 |
| `cross_app_pipeline` | 要把积带出计算器 | FAIL 88 步 |

**凡是"值必须跨过一道边界"的任务，它都会循环到超时；凡是边界内自洽的任务，它比插件臂更快。**
所以真正的描述不是"长程能力弱"，而是**"跨边界的状态传递缺失"** ——
这正好落在第二节那几件机械事实上（重复不可见、回执不说打到了什么）。

### 结论边界

- 这两个任务**每个只跑了 1 次 / 1 条臂**，是探针级证据，不是率。
- `cross_app_pipeline` 的跨应用要求是**软**的：模型自己心算也能得到正确积。
  它仍然必须把值带进另一个应用并正确存盘，但"必须用 Calculator"没有被强制。
- 两个任务的 agent 臂都是被 420s 上限取消的，不是自己放弃。
  它们在取消前分别跑了 86 / 88 步。

## 第七轮：修掉三条实测缺陷之后（2026-10-17，agent 臂，14 任务一轮）

改了四处（详见 `AGENT-ROADMAP.md` 第五节）：

1. `action/shell.py` 的 PowerShell 前导声明 DPI 感知
2. `visual_actor.py` 的 shell 工具补一条工具事实（命令已在 PowerShell 内，嵌套会丢状态）
3. `mission/contracts.py` + `runtime/contracts.py` + `mission_runtime.py`：
   `same_action_attempts`（同一动作本任务内已尝试几次，累计而非连续）
4. `window_frame` 的 oracle 改成接受 `GetWindowRect` 与 DWM 外框两种约定

结果：**agent 10/14，240 步，3,448,700 tokens，1868s**。

### 和修之前的 12 任务轮对比

| 任务 | 修前（两轮） | 修后 |
|---|---|---|
| `shell_control` | PASS 4 / 5 | PASS 5 |
| `notepad_save` | PASS 8 / 8 | PASS 7 |
| `calculator_clipboard` | **FAIL 89 / 38** | **FAIL 38** |
| `read_target_text` | PASS 3 / 3 | PASS 4 |
| `read_target_pixels` | PASS 3 / 3 | PASS 3 |
| `fill_form` | PASS 10 / 9 | **FAIL 8**（表单没提交） |
| `browser_confirm` | PASS 5 / 11 | PASS 7 |
| `window_frame` | **FAIL 5 / 7** | **PASS 4** |
| `menu_path` | PASS 4 / 5 | PASS 5 |
| `terminate_app` | PASS 61 / 44 | **PASS 3** |
| `browser_session` | PASS 20 / 11 | PASS 13 |
| `browser_drag` | PASS 8 / 7 | PASS 6 |
| `cross_app_pipeline` | FAIL（探针 88 步） | **FAIL 61** |
| `cross_window_token` | FAIL（探针 86 步） | **FAIL 76** |

### 结论：完成率没有变好

- **旧 12 任务上：修后 9/12，修前 10/12。** `window_frame` 拿回来了，`fill_form` 掉了，
  净值为零甚至略负。**不能宣称"完成率提升了"。**
- 两个跨边界任务仍然两轮都失败，`calculator` 仍然失败。

### 但有三条是真实的

1. **`window_frame` 从"两轮都错、错得一模一样"变成 PASS。** 那条 ×1.25 是机器说谎，
   已用对照实验钉死并修掉。
2. **`terminate_app` 从 61 / 44 步降到 3 步。** 同一个任务，同样通过，成本降一个数量级。
3. **`calculator` 出现过一次 8 步通过**（单独跑时模型改用 `type_text` 输入算式），
   但同一天全量轮里又回到 38 步失败。**一条机械事实能改变策略选择，不能保证每次都改。**

### 结论边界

- 这是修复后**第一轮**，且 `fill_form` 此前两轮都通过 → 它很可能是方差，不是回归。
  要判定必须再跑两轮（`compare.mjs` 已备好）。
- 14 个任务里有 2 个（跨应用）此前只有探针级 1 次数据。
- 修 DPI 的代价是 **+199 ms / 次 shell 调用**，已实测；本轮 `terminate_app` 反而快了，
  说明该代价被行为改善盖过，但**没有逐任务隔离过**。

## 第八轮：给模型"窗口自己说的话"之后（2026-10-17 晚，agent 臂 + 三臂对照）

主产品侧只改了两处机械层（详见 `docs/handover/main-product-history.md` 同日条目）：

1. **`read_window_text` 可以点名窗口**：`window` 可选参数（`list_windows` 的句柄 / 标题或进程名片段），
   空则回到焦点窗口；未命中或歧义返回候选，不再静默解析到错的窗口。
2. **每个完成的 GUI 动作，结果里自带焦点窗口的 UIA 文本**（≤600 字符 + `window_title` / `window_hwnd`）。
   1120px 提交帧里读不清的表达式行/字段值，从此以文本形式随回执到达；读不到就省略字段。

### agent 臂：4 个任务，改前 / 改后

| 任务 | 改前 | 改后（各 2 轮，`fill_form`/`calculator` 1 轮） |
|---|---|---|
| `cross_window_token` | **FAIL** 40 步 / 424s | **PASS** 26 步 / 423s · **PASS** 34 步 / 423s |
| `cross_app_pipeline` | **FAIL** 43 步 / 425s | **PASS 13 步 / 94s** · **FAIL** 63 步 / 425s |
| `fill_form` | PASS 9 步 | PASS 12 步 / 77s |
| `calculator_clipboard` | FAIL 41 步 / 424s | **FAIL** 64 步 / 423s |

**改前 1/4，改后逐回合 4/6；4 个任务里 3 个至少过一次（改前 1 个）。**

### 同一批 oracle 上的三条臂

| 任务 | agent（本产品） | cua（新 profile 的 dsh + 自带 computer use） | lingdong（本插件） |
|---|---|---|---|
| `cross_window_token` | PASS 26 / PASS 34 步 | PASS 35 步 / 110s | PASS 15 步 / 59s |
| `cross_app_pipeline` | PASS 13 / FAIL 63 步 | PASS 53 步 / 132s | PASS 23 步 / 69s |

报告：`report-1792235221603` / `report-1792236268433`（agent · 跨窗）、
`report-1792235320589` / `report-1792236697670`（agent · 跨应用）、
`report-1792236811624` / `report-1792236950151`（cua）、
`report-1792237012767` / `report-1792237087903`（lingdong）。

### 结论（不要省略）

- **完成率这一轮确实变好了，但幅度是"从必失败到会成功"，不是稳定通过**：
  `cross_window_token` 2/2、`cross_app_pipeline` 1/2。
- **`cross_window_token` 两次都在 423 秒通过**，正好压在 harness 的 420 秒超时线上；
  提交发生在取消前后，外侧 oracle 读到了提交结果，PASS 成立，但**不能读成"快"**。
  产品预算是 200 回合 / 7200 秒，420 秒是测量台的耐心。
- **仍然失败的两次都是同一个病：模型把数字键盘读成列优先布局**。
  改后失败的那轮 62 笔 `click`（30 次同一点），成功的那轮用
  `type_text "36*54"` 直接算。同一份上下文里两条路都开着 → **这是模型层，不是机械层**。
  按根 `AGENTS.md` 记下来，不加硬门禁。
- 三个臂**仍不是同一个模型**（agent = `mimo-v2.6-flash`，两条 DSH 臂 = `deepseek-flash`）。
- 墙钟不可跨臂比：agent 臂 423s 对 110s / 59s，差额主要是模型延迟（每步 5–8s）+ 每回合一次 UIA 读数。

### 追加：真前端一轮 + 同模型对照（同日）

前面的 agent 臂都是直连 `POST /api/v2/goals/runs`（前端自己也只有这一条路由）。补的一轮是
**Electron 里手动键入目标并回车**：

- 模型不是代码默认，而是本机 `config.json` 的 `active_model_profile`（当时是 `mimo`）。
  切成 `deepseek` 档案后重启前端，chip 显示 `deepseek-flash`。
- 同一目标（35×80 → 存 `output/e2e/t10.txt`）从 UI 提交：**PASS**，
  **24 回合 / 32 次模型调用 / 2 分 5 秒**，磁盘文件逐字节 `2800`。
- 于是 `cross_app_pipeline` 的三臂在**同一模型**下可比：
  **agent 24 回合 PASS · cua 53 回合 PASS · lingdong 23 回合 PASS**。
- `deepseek-flash` 同样先点计算器键盘（前 11 回合坐标漂移），后来改成输入/文件路径并自检才救回来。
  **读错键盘布局不是单个模型的问题。**

### 第九轮：**三臂同模型**（agent 切到 `deepseek-flash`），4 任务 × 2 轮

把本机 `config.json` 的当前档案从 `mimo` 切成 `deepseek`（`model_profiles.deepseek`）后，
三个臂第一次跑在**同一个模型**上。同一批 oracle、同一台机、同一轮：

| 任务 | agent r1 | agent r2 | cua | lingdong |
|---|---|---|---|---|
| `cross_app_pipeline` | **PASS 8 步 / 34s** | PASS 24 步 / 87s | PASS 53 / 132s | PASS 23 / 69s |
| `cross_window_token` | PASS 17 / 47s | PASS 21 / 57s | PASS 35 / 110s | **PASS 15 / 59s** |
| `calculator_clipboard` | PASS 13 / 55s | **PASS 5 / 29s** | PASS 36 / 119s | PASS 10 / 43s |
| `fill_form` | PASS 15 / 43s | PASS 14 / 37s | PASS 35 / 104s | PASS 18 / 50s |
| **合计** | **4/4 · 53 步 · 0.59M tok** | **4/4 · 64 步 · 0.77M tok** | **4/4 · 159 步 · 7.87M tok** | **4/4 · 66 步 · 3.49M tok** |

报告：agent r1 `report-1792238{123325,172487,230371,275592}`；
agent r2 `report-1792238{372733,432246,464590,504371}`；
cua `report-1792238{626769,734213}` + `report-1792236{811624,950151}`；
lingdong `report-1792238{780868,834087}` + `report-1792237{012767,087903}`。

**结论（同模型下）**：

- **完成率在这一批 4 个任务上三臂打平：都是 4/4。** 没有任何一臂在这批任务上掉过。
  也就是说：**这批任务已经不能区分完成率了**——要区分，必须换更难/更长的任务集。
- **成本分层很清楚**：步数 agent 53–64 ≈ lingdong 66 ≪ cua 159；
  tokens agent 0.59–0.77M ≪ lingdong 3.49M ≪ cua 7.87M。agent 的固定前缀最小（约 9k 对 27.6k / 51.4k）。
- **同一任务两轮步数方差很大**（`cross_app_pipeline` 8 对 24、`calculator_clipboard` 13 对 5），
  **完成率稳定、步数不稳**——引用成本必须多轮取中位数，不能拿单轮。

## 原始记录

`output/e2e/report-<ts>.json`：每任务 step 数 / tokens / 耗时 / oracle 判定 / agent 事件流 / stderr。`output/` 已 gitignore。

## DPI 陷阱（`probe-dpi.mjs` 是证据）

第一版 oracle 用 **DPI 不感知**的 PowerShell 读取器量窗口矩形。125% 缩放下 Windows 把它读到的每个矩形按 0.8× 虚拟化，
于是把一个**完全正确**的 `set_window_frame`（真 640×480）读成 512×384 判失败。加上 per-monitor DPI 感知后，runtime 与独立进程读数完全一致。

**oracle 自己错了会判错正确的工作，而插件这边不会有任何迹象。** 任何量窗口几何的 oracle 必须先 `SetProcessDpiAwarenessContext(-4)`。
