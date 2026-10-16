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

## 原始记录

`output/e2e/report-<ts>.json`：每任务 step 数 / tokens / 耗时 / oracle 判定 / agent 事件流 / stderr。`output/` 已 gitignore。

## DPI 陷阱（`probe-dpi.mjs` 是证据）

第一版 oracle 用 **DPI 不感知**的 PowerShell 读取器量窗口矩形。125% 缩放下 Windows 把它读到的每个矩形按 0.8× 虚拟化，
于是把一个**完全正确**的 `set_window_frame`（真 640×480）读成 512×384 判失败。加上 per-monitor DPI 感知后，runtime 与独立进程读数完全一致。

**oracle 自己错了会判错正确的工作，而插件这边不会有任何迹象。** 任何量窗口几何的 oracle 必须先 `SetProcessDpiAwarenessContext(-4)`。
