# DSH 插件子项目 — AI 入口

包：`@lingdong/dsh-plugin-lingdong`（host，16 个工具）· `@lingdong/dsh-client-ui-lingdong`（Web GUI 工作台）。
目标：让本 provider 在 DSH 里**全面碾压**自带 computer use（`cua-driver-native` 0.28.0）。四个维度都要赢：完成率 / 成本 / 观测 / 能力面。
仓库主产品（灵动脚本 / WinPilot）与本子项目无关 → `/CLAUDE.md`。

## 硬约束（违反即失败）

1. 不改 DSH 安装目录 `C:\Users\zou\AppData\Roaming\npm\node_modules\@deepseek-ai\dsh\`
2. 不改 `~/.dsh/profiles/web/cordis.patch.yml`
3. 工作树 **82 个改动是用户的**：不提交 / 不修改 / 不暂存。`git add` 只加 `packages/` 下的文件
4. 不重启 3080 上的 dsh —— 会终止当前会话
5. 自检：`(git status --porcelain | Where-Object { $_ -notmatch 'packages/' } | Measure-Object -Line).Lines`
   —— 用户改动 **82 条** + 本子项目在 `python/` 与 `tests/` 下的改动。逐次核对：
   ```powershell
   git diff --stat -- python/winpilot_home/winpilot/fast/ python/winpilot_home/tests/test_studio_action_batch_controller.py python/features/browser_features.py
   ```
   应**只**列出 **4 个文件**：`fast/controller.py`、`fast/action_contract.py`、
   `tests/test_studio_action_batch_controller.py`、`features/browser_features.py`。
   **除这 4 个外一个字节都不要碰**（尤其 `recorder/trace.py`、`orchestrator/projection.py` 是用户的）。
   `browser_features.py` 是**用户批准的例外**（2026-10 由用户明确同意后追加），且**只追加、未改任何既有函数**。

## 状态

分支 `feat/dsh-plugin`，起点 `97cd0e2`。提交数现场数：`git log --oneline 97cd0e2..HEAD`
（**本文不写 HEAD**——写了就会立刻过期）。

| 维度 | 实测 | 判定 |
| --- | --- | --- |
| 完成率 | 8/8 对 7/8（第三轮，8 任务）；历史两轮合计 16/16 对 14/16 | 赢，且已**不再只压在一个任务上** |
| 成本 | 第三轮 **93 步对 190 步**（历史两轮 183 对 362） | **硬赢**；引用用步数，tokens 跨轮方差大 |
| 纯观测 | 8/14 步 对 6/7 步（`read_target_*`） | **仍输**，唯一还输的维度 |
| 能力面 | `invoke_menu` + 一等 `kill_app` + 浏览器 **32 个动作**（含原生 dialog / 拖拽 / PDF / cookie / storage / 网络等待）全部测通 | 基本补齐 |

原始数据 `e2e/RESULTS.md`（含第三轮与**不得省略的结论边界**）。

## 下一步（按序）

1. **把新能力变成判得出来的任务** —— `e2e/menu_target.cs` 已备好：三级原生 Win32 菜单靶窗
   （`Report > Export > As text` 被调用则写 `<token>|text` 到指定文件）。实测 Win32 读出三层、UIA 只有
   `Report, Tools` 两个 `children=0` 的一级项 → 对 B 臂是**结构性不可比**，不是"跑得慢"。
   再加一个终止类任务（`kill_app`）。**加进任务集并跑出来之后**才能写进结论。
2. **扩任务集到 ≥12 个、每任务 ≥3 轮** —— 第三轮只有 1 轮 × 8 任务，单轮不足以支撑"多点优势"。
3. **纯观测维度（唯一还输的一维）** —— 先查清 A 臂 8/14 步到底花在哪，再决定改什么，不要先动手。
4. **浏览器剩余两项** —— `visual_anchor`（视觉锚点）与状态快照恢复还没开门，按同一模式加即可。
5. `invoke_menu` / `kill_app` 的重放步骤（`_replay_for` 均返回 None）：`kill_app` **刻意不重放**（不可逆）；
   `invoke_menu` 要重放得先让重放词汇表认得它，而 `trace.py` 是用户的改动文件，**动手前先确认可以碰**。

## `invoke_menu`（已完成，实测记录）

按路径调用应用菜单。**为什么不能用 UIA**：实测 Notepad 的 `MenuBarControl` 有 5 个 `MenuItemControl` 子项，
但每个一级项的 `GetChildren()` 都是**空**——二级项在无障碍树里根本不存在。改走 Win32
`GetMenu` + `GetMenuItemInfo` + `WM_COMMAND`，与 `set_value` 同路（不进 RawDevice，靠独立回读判定）。

- 路径按**标签**或**序号**逐级解析：标签匹配前去掉 `&` 加速键、`\t` 后的快捷键、尾部省略号，再 casefold
  （`&Save As...\tCtrl+Shift+S` ≡ `Save As`），所以手册里写的名字能直接用；界面语言不确定时用序号 `[0, 0]`。
- **回绝会带回可用项**：某级找不到就返回该级的 `available`（有界、且**必须是可解析 JSON**），路径停在
  菜单标题上也一样——把整个菜单树回给模型，让它自己改正，而不是猜。
- 只发叶子命令；一级标题（有子菜单的项）绝不当命令执行。
- 判定分两层：`delivered`（WM_COMMAND 是否送达，跨进程实测**可在目标后台时送达**、不抢焦点）与
  `action_state`（窗口自己报的事实有没有变）。**命令可能异步生效**，所以在判定前有 ≤1s 的有界
  settle 等待（`menu_settle_timeout_s`），变了一样就提前退出。
- 读 `text_length` **必须读内容控件（Edit / RichEdit）而不是顶层窗口**：记事本顶层窗口对
  `WM_GETTEXTLENGTH` 返回的是标题长度（恒定 10），会让每个文档命令都被读成"没有变化"。
- 实测证据：`e2e/probe-menu.py`（UIA 二级为空）、`e2e/probe-menu-live.mjs` + `e2e/menu-oracle.py`
  （真记事本、目标全程后台、外部进程读 Edit 文本长度：9 → 10）、
  `e2e/probe-menu-nested-live.mjs` + `probe-menu-nested.py`（**自建的三级原生 Win32 菜单**：同一个菜单
  Win32 读出 6 项三层、UIA 只报 `File,Edit` 两项且 `children=0`；三级命令 `[File, Export, As text]`
  按标签与按序号都能跑通，窗口自己改标题为证）。
- **能力面是互补的，不是全面超越**：WinForms `MenuStrip`（TypeScript/Electron/WPF 一类富界面常见）
  的菜单在 UIA 里**完整可见**（实测三级都在），而它 `GetMenu` 返回 **0**——`invoke_menu` 对它无能为力，
  得走 `click_element`。所以这条补的是**原生 Win32 菜单**（记事本、老式 Win32 程序）那一半。
- 永久回归：`scripts/smoke.mjs` 的四条菜单检查（路径解析 / 独立进程确认变化 / run 自己报出变化 /
  回绝可解析且带可用项）。

## 命令

```powershell
# 验证（全绿才算完成）
node packages/dsh-plugin-lingdong/scripts/smoke.mjs              # 真 runtime + 真 apply() 挂载 + 真桌面动作
node packages/dsh-plugin-lingdong/scripts/verify-bridge.mjs      # 真 http server + 真 runtime
node packages/dsh-plugin-lingdong/scripts/verify-browser.mjs     # 真 headless Chrome + CDP
node packages/dsh-client-ui-lingdong/scripts/verify-bundle.mjs   # codec + 真 React SSR
python -m pytest python/winpilot_home/tests -q                   # 期望 1523 passed, 13 skipped

# 配对实测（改任何东西后都要重跑，~25 min）
node packages/dsh-plugin-lingdong/e2e/run.mjs                    # 两臂全任务
node packages/dsh-plugin-lingdong/e2e/run.mjs --arm lingdong     # 只跑自己
node packages/dsh-plugin-lingdong/e2e/run.mjs --task fill_form   # 单任务
node packages/dsh-plugin-lingdong/e2e/run.mjs --list             # 任务清单

# 装进 profile
dsh plugin --profile web add "C:/Users/zou/Desktop/claude/packages/dsh-plugin-lingdong"
node packages/dsh-plugin-lingdong/scripts/link-peers.mjs
dsh --profile web --patch "C:/Users/zou/Desktop/claude/packages/dsh-plugin-lingdong/dsh.patch.yml"
```

E2E 用两个从 `headless` 模板建的 scratch profile：**A 臂 `ld-e2e`**（`e2e/lingdong.patch.yml`）、
**B 臂 `cua-e2e`**（`e2e/cua.patch.yml`）。**别删这两个 profile，别碰 `web`。**
判定只由外部 oracle（文件字节 / 剪贴板 / 窗口矩形 / 目标应用自己写出的结果），agent 的自述不算。
原始记录 `output/e2e/report-*.json`（已 gitignore）。

## 16 个工具

`status` `windows` `screen` `cursor` `apps` `elements` `snapshot` `verify` `observe` `desktop` `run_goal` `run_flow` `flow_files` `record` `export` `browser`（前缀 `lingdong_`）

- `snapshot` = 窗口级 UIA + 截图**同一帧**；`verify` = 状态断言，支持 ground truth（`file_exists`/`process_running`/`shell_true`），未支持键**按名拒绝**；`desktop` = 一次有序 canonical 动作批，**唯一物理输入入口**；`browser` = **32 个动作**，**有状态**（除 `launch`/`connect` 外一律拒绝）。
- 逐动作清单与参数看 `src/tools.mjs`。

## 浏览器动作面（32 个，一个工具）

设计原则：**工具面要小，能力面要大**。32 个动作全收在 `lingdong_browser` 的 `action` 参数里——
**工具数仍是 16**，提示词长度不随能力增长。

- 观察：`snapshot`、`read`、`find`、`attrs`、`console`
- 导航：`open`、`history`（back / forward / reload）
- 标签：`new_tab`、`tabs`、`switch`、`close_tab`
- 会话状态：`cookies`（get/set/clear）、`storage`（get/set）、`network`（等某个请求发生）
- 交互：`click`、`type`、`press`、`select`、`hover`、`scroll`、`wait`、`drag`、`dialog`、`eval`
- 产物：`screenshot`、`upload`、`download`、`save_pdf`
- 会话：`launch`、`connect`、`close`

**原生能力（动过 `features/browser_features.py`，只追加）**：`browser_arm_dialog` /
`browser_dialogs` / `browser_drag` / `browser_drag_path` / `browser_save_pdf`。
dialog 默认走**原生监听器**（`page.on("dialog")`），因为 JS 覆写挡不住 `beforeunload`；
`use: "script"` 才用页面内覆写。

**一个必须知道的限制**：`drag` 的 element-to-element 走 Playwright `drag_and_drop`，它驱动的是**指针**，
**不产生 HTML5 drag 事件**，所以 `dataTransfer` 是空的。需要真正 HTML5 拖拽（看板卡片、文件拖放）时用
`from_x/from_y/to_x/to_y` 的轨迹式拖拽，实测滑块 0 → 100 生效。这一点在 `verify-browser.mjs` 里被写成
断言如实标注，而不是假装能做到。

底层 browser feature 共 46 个，仍**尚未开门**的只剩 `visual_anchor`（视觉锚点）与状态快照恢复
（`browser_state_snapshot` / `browser_state_restore`）；需要时按同一模式加动作。

## 关键不变量

- **单写入者**：物理输入只走 canonical ARP → `fast/controller.py` → `RawDevice`。
  `set_value` / `set_window_frame` / `invoke_menu` / `kill_app` 是仅有的四个**非合成输入**变更动作，
  不进 RawDevice，靠**独立回读**判定成功。其中 `kill_app` **不可逆**，所以按标题终止时要求**唯一匹配**，
  多匹配就拒绝并列出候选 pid。
- **不同挂两个 provider**：槽排他，且会出现两个物理输入写入者——破坏唯一不可削弱的边界。
- **`computerUse` 必须是可选注入**（`inject=['tools','systemPrompt']` + `ctx.inject(['computerUse'],…)`）。列成必需会让插件在不带该服务的组合里永远 pending，模型拿不到任何桌面工具。
- **重放**：`set_value` 因文本敏感 `_replay_for` 返回 None；`set_window_frame` 的重放步骤**刻意未发**——重放词汇表（`recorder/wps_validate.py` + `trace.py::_run_step`）得先认得它，而 `trace.py` 是用户的改动文件，**动手前先确认可以碰**。
- **浏览器**：`connect` 自己先探 CDP，**绝不悄悄开可见浏览器**；`launch` 起的归本插件（CDP `Browser.close` 关，子进程只是 launcher），`connect` 上来的归调用者（只断开）。
- **客户端 bundle**：`lib/client.js` 是手写 `window.__ModuleLoader__.load(...)`，**不需要打包器**。画布顺序 = `automation-ir/1` 的 `steps` 顺序；不认识的步骤原样保留成 `replay_step` 节点。

## 踩过的坑（每条都真付过代价）

1. **DPI 虚拟化会骗过 oracle**。DPI 不感知的进程读到的窗口矩形会被按显示缩放虚拟化（125% → 0.8×），会把一个**正确**的 `set_window_frame`（真 640×480）读成 512×384 判失败。**任何量窗口几何的 oracle 必须先 `SetProcessDpiAwarenessContext(-4)`**。证据 `e2e/probe-dpi.mjs`。
2. **不要经 `dsh` shim 起进程**：cmd.exe 把任务文本里以 `-` 开头的 token 当 flag。直接 `node <npm root -g>/@deepseek-ai/dsh/lib/bin.js …`，`shell:false`。
3. **`launch notepad` 在已有 Notepad 时会 adopt 现有窗口**，不新建。
4. **pywin32**：`SetWindowPos` 返回 `None` 不是 BOOL；`win32gui` **没有** `IsZoomed`（用 `GetWindowPlacement()[1] == SW_SHOWMAXIMIZED`）。
5. **UIA 元素投影不要用 `dataclasses.asdict`**：`extra` 里有活的 `uia_ctrl` 句柄 → FastAPI 500。用 `UIElement.to_dict()` + 只留 `extra` 的标量。
6. **测完清桌面**（Notepad / Calculator / Chrome / 靶窗）；`run.mjs` 每任务前清、整轮后清。
7. **不要与重型全量 pytest 并发跑 GUI E2E**（会让 `/api/status` 超时）。
8. **ctypes 的两个坑（都真踩过）**：`MENUITEMINFO.dwTypeData` 写成 `LPWSTR` 会在赋值时被 ctypes **解引用**，
   于是结构里存的是空串而不是可写缓冲区，每个菜单标签都读成空——必须是地址类型。另外这个解释器里
   `int()` **不能**用于嵌套 ctypes 标量（`WPARAM`/`HWND`），会拿原始字节当作十进制解析而报
   `invalid literal for int()`；句柄用 `ctypes.cast(v, c_void_p).value`，标量用 `.value`。
9. **有界的记录必须是可解析的 JSON**。`orchestrator/projection.py` 会把每个 timeline 事件的 `summary`
   再压到 **600 字符**——按字符硬切会把 JSON 切断，模型和录制器读到的就是一段无法解析的文本。
   `_safe_event_preview` 因此按"丢字段/缩短列表"降到预算内，而不是 `text[:N]`（`_EVENT_PREVIEW_LIMIT = 560`）。
10. **记事本顶层窗口对 `WM_GETTEXTLENGTH` 返回标题长度（恒定 10）**，文档在 Edit 子控件里。
    任何"内容有没有变"的判定都必须读内容控件。
11. **发给页面的 JS 必须是"表达式"，而且不能有语句体**。`browser_execute_js` 把字符串交给
    Playwright，而 Playwright 按**表达式**求值。我为此连错两次归因，最后靠二分钉死：
    `(() => 10)()` → 10，`(() => { 10 })()` → **null**；回调写成 `(x) => { f(x); }` 也 **null**。
    另外两个坑：Python 层一旦看到 `return ` 就**再包一层** IIFE，值同样丢；逗号表达式里的
    `{...}` 会被当**语句块**解析（`top(...)` 于是不是函数调用），要多加一层括号。
    **作页脚本前先用 `node` 桩件当表达式 eval 一遍**，别拿浏览器试错。
12. **不要用 `top` 当变量名**：它是 `window.top`，重新赋值静默失败，报错是莫名其妙的
    `top is not a function`。

## 文件地图

```
packages/dsh-plugin-lingdong/
  src/index.mjs     挂载（inject / 可选 computerUse / 路由 / 工具子插件 / prompt section）
  src/tools.mjs     16 个工具 + GUIDANCE（+ **32 个 browser 动作** + 页面快照/控制台/弹窗脚本）
  src/bridge.mjs    /lingdong/api/*：flows / runs / fragments，先用 DSH 的 requestRejection，
                    无该服务时退回更严的回环兜底；无 webServer 也能挂
  src/browser.mjs   FeatureWorker（进程边界，跑 python/tools/feature_call.py）+ BrowserSession
  src/client.mjs    runtime REST 客户端      src/sidecar.mjs  拉起/回收 python serve.py
  src/config.mjs    Config schema            dsh.patch.yml    禁 cua + 插入本插件与 UI
  scripts/          smoke / verify-bridge / verify-browser / link-peers
  e2e/              run.mjs（配对跑）· 两个 arm 的 patch · target.ps1 · form.ps1 · confirm.html
                    menu_target.cs（三级原生 Win32 菜单靶窗，菜单命令写 oracle 文件）
                    probe-dpi.mjs（DPI 证据）· probe-menu.py（UIA 二级为空的证据）
                    probe-menu-live.mjs + menu-oracle.py（记事本端到端 + 外部 oracle）
                    probe-menu-nested-live.mjs + probe-menu-nested.py（三级原生菜单 + 两种视图对照）
                    RESULTS.md（原始数据）
packages/dsh-client-ui-lingdong/
  lib/client.js     画布 + flowCodec + 流程库 + 多选 + 手势级撤销重做 + 片段草稿
  scripts/verify-bundle.mjs
python/winpilot_home/winpilot/
  desktop_observe.py      只读观测 + 新路由 /window /snapshot /verify
  action/clipboard_ops.py window_info_backend + _window_record
  fast/action_contract.py ALLOWED_BATCH_ACTIONS / BATCH_MUTATING_ACTIONS / 字段校验
  fast/controller.py      set_value / set_window_frame / invoke_menu / kill_app + _resolve_action_hwnd
python/winpilot_home/tests/test_studio_action_batch_controller.py   72 个用例
```
