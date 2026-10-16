# DSH 插件子项目 — AI 入口

包：`@lingdong/dsh-plugin-lingdong`（host，16 个工具）· `@lingdong/dsh-client-ui-lingdong`（Web GUI 工作台）。
目标：让本 provider 在 DSH 里**全面碾压**自带 computer use（`cua-driver-native` 0.28.0）。四个维度都要赢：完成率 / 成本 / 观测 / 能力面。
仓库主产品（灵动脚本 / WinPilot）与本子项目无关 → `/CLAUDE.md`。

## 硬约束（违反即失败）

1. 不改 DSH 安装目录 `C:\Users\zou\AppData\Roaming\npm\node_modules\@deepseek-ai\dsh\`
2. 不改 `~/.dsh/profiles/web/cordis.patch.yml`
3. 工作树 **82 个改动是用户的**：不提交 / 不修改 / 不暂存。`git add` 只加 `packages/` 下的文件
4. 不重启 3080 上的 dsh —— 会终止当前会话
5. 自检：`(git status --porcelain | Where-Object { $_ -notmatch 'packages/' } | Measure-Object -Line).Lines` 必须仍是 **82**

## 状态

分支 `feat/dsh-plugin`，起点 `97cd0e2`。提交数现场数：`git log --oneline 97cd0e2..HEAD`
（**本文不写 HEAD**——写了就会立刻过期）。

| 维度 | 实测 | 判定 |
| --- | --- | --- |
| 完成率 | 16/16 对 14/16（8 任务 × 2 轮） | 赢，但**优势全压在 `window_frame` 一个任务上** |
| 成本 | 183 步 / 9.07M tok 对 362 步 / 24.27M tok | **赢，最硬的一条**（2.0× 少调用、2.7× 少 tokens） |
| 纯观测 | 7–11 步 对 4–7 步 | **输**，它更精简 |
| 能力面 | 缺 `invoke_menu` / 一等 `kill_app` / 浏览器 dialog + drag | **未完成** |

原始数据 `e2e/RESULTS.md`。

## 下一步（按序，做完 1–4 再做 5）

1. **`invoke_menu`** —— 按路径调用应用菜单。已知（实测）：菜单栏与一级项 UIA 可达，**二级子项 `GetChildren()` 返回空**。改走 Win32 `GetMenu` + `GetMenuItemInfo` + `WM_COMMAND`。落点 `fast/controller.py` + `fast/action_contract.py`，与 `set_value` 同路——进 canonical 词汇表才自动获得录制/重放/审计。
2. **一等 `kill_app`** —— 能力已可达（离线流程 `run_shell` taskkill，实测 exit 0、0 模型调用），只是没做成一等工具。
3. **浏览器 dialog** —— `lingdong_browser` 无 dialog 动作。`browser_confirm` 两边都过，所以**缺口尚未被证明**；要设计一个 Playwright 静默 dismiss 就拿不到结果的任务才能暴露。
4. **浏览器 pointer drag** —— 缺 `drag`。
5. **扩任务集到 ≥12 个、每任务 ≥3 轮**，覆盖 1–4 —— 这一步才把"优势只来自一个任务"变成多点优势。

## 命令

```powershell
# 验证（全绿才算完成）
node packages/dsh-plugin-lingdong/scripts/smoke.mjs              # 真 runtime + 真 apply() 挂载 + 真桌面动作
node packages/dsh-plugin-lingdong/scripts/verify-bridge.mjs      # 真 http server + 真 runtime
node packages/dsh-plugin-lingdong/scripts/verify-browser.mjs     # 真 headless Chrome + CDP
node packages/dsh-client-ui-lingdong/scripts/verify-bundle.mjs   # codec + 真 React SSR
python -m pytest python/winpilot_home/tests -q                   # 期望 1486 passed, 13 skipped

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

- `snapshot` = 窗口级 UIA + 截图**同一帧**；`verify` = 状态断言，支持 ground truth（`file_exists`/`process_running`/`shell_true`），未支持键**按名拒绝**；`desktop` = 一次有序 canonical 动作批，**唯一物理输入入口**；`browser` = 22 个动作，**有状态**（除 `launch`/`connect` 外一律拒绝）。
- 逐动作清单与参数看 `src/tools.mjs`。

## 关键不变量

- **单写入者**：物理输入只走 canonical ARP → `fast/controller.py` → `RawDevice`。`set_value`/`set_window_frame` 是仅有的两个**非合成输入**变更动作，不进 RawDevice，靠**独立回读**判定成功。
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

## 文件地图

```
packages/dsh-plugin-lingdong/
  src/index.mjs     挂载（inject / 可选 computerUse / 路由 / 工具子插件 / prompt section）
  src/tools.mjs     16 个工具 + GUIDANCE（+ 22 个 browser 动作）
  src/bridge.mjs    /lingdong/api/*：flows / runs / fragments，先用 DSH 的 requestRejection，
                    无该服务时退回更严的回环兜底；无 webServer 也能挂
  src/browser.mjs   FeatureWorker（进程边界，跑 python/tools/feature_call.py）+ BrowserSession
  src/client.mjs    runtime REST 客户端      src/sidecar.mjs  拉起/回收 python serve.py
  src/config.mjs    Config schema            dsh.patch.yml    禁 cua + 插入本插件与 UI
  scripts/          smoke / verify-bridge / verify-browser / link-peers
  e2e/              run.mjs（配对跑）· 两个 arm 的 patch · target.ps1 · form.ps1 · confirm.html
                    probe-dpi.mjs（DPI 证据）· RESULTS.md（原始数据）
packages/dsh-client-ui-lingdong/
  lib/client.js     画布 + flowCodec + 流程库 + 多选 + 手势级撤销重做 + 片段草稿
  scripts/verify-bundle.mjs
python/winpilot_home/winpilot/
  desktop_observe.py      只读观测 + 新路由 /window /snapshot /verify
  action/clipboard_ops.py window_info_backend + _window_record
  fast/action_contract.py ALLOWED_BATCH_ACTIONS / BATCH_MUTATING_ACTIONS / 字段校验
  fast/controller.py      set_value / set_window_frame + _resolve_action_hwnd
python/winpilot_home/tests/test_studio_action_batch_controller.py   35 个用例
```
