# AGENTS.md — 灵动 DSH 插件 · AI 交接入口

> 这是本子项目的唯一入口。先读完本文件再动手。仓库根 `CLAUDE.md` 是另一个项目的历史记录，与本子项目无关。

## 0. 目标与现状

**目标**：让 `@lingdong/dsh-plugin-lingdong` 在 DSH 里**全面碾压**自带 computer use（`cua-driver-native`）。
"全面"= 四个维度都要赢：完成率 / 成本 / 观测 / 能力面。目前只赢了 2.5 个。

**现状（实测，非推论）**：

| 维度 | 结果 | 状态 |
| --- | --- | --- |
| 任务完成率 | 16/16 对 14/16（8 任务 × 2 轮） | 赢，但**优势全压在一个任务上**（见 §5） |
| 成本 | 183 步 / 9.07M tokens 对 362 步 / 24.27M tokens | **赢，最硬的一条**（约 2.0× 少调用、2.7× 少 tokens） |
| 纯观测 | 读取类任务 7–11 步 对 4–7 步 | **输**，它更精简 |
| 能力面 | 缺 `invoke_menu` / 一等 `kill_app` / 浏览器 dialog + pointer drag | **未完成** |

**下一步唯一要紧的事**：补掉那三项能力缺口 → 扩任务集覆盖它们 → 重测，把"完成率优势只来自一个任务"变成多点优势。

## 1. 硬约束（违反即失败）

1. 不改 DSH 安装目录：`C:\Users\zou\AppData\Roaming\npm\node_modules\@deepseek-ai\dsh\`
2. 不改 `C:\Users\zou\.dsh\profiles\web\cordis.patch.yml`
3. 工作树里 **82 个改动是用户的**：不提交、不修改、不暂存。`git add` 只加 `packages/` 下自己的文件。
4. **不要重启 3080 上的 dsh**（pid 会变）——重启会终止当前会话。
5. 只提交自己的文件。验证方式：`git status --porcelain | grep -v packages/ | wc -l` 必须仍是 82。

## 2. 仓库状态

```
repo    C:\Users\zou\Desktop\claude
branch  feat/dsh-plugin
HEAD    8e99bb5          （本子项目从 97cd0e2 起，共 9 个提交）
```

## 3. 文件地图

```
packages/dsh-plugin-lingdong/          host 插件（16 个工具）
  src/index.mjs        挂载：inject=['tools','systemPrompt']，computerUse 走 ctx.inject 可选
  src/tools.mjs        16 个工具定义 + GUIDANCE
  src/bridge.mjs       /lingdong/api/* 同源路由（浏览器工作台 → runtime），用 DSH 自己的准入
  src/browser.mjs      CDP 浏览器会话：FeatureWorker(常驻进程) + BrowserSession(有状态)
  src/client.mjs       runtime REST 客户端
  src/sidecar.mjs      拉起/回收 python serve.py
  src/config.mjs       Config schema + resolveConfig
  dsh.patch.yml        web profile 的 overlay（禁 Cua、插入本插件）
  scripts/smoke.mjs    冒烟：真 runtime + 真 apply() 挂载 + 真桌面动作
  scripts/verify-bridge.mjs    真 http server + 真 runtime 全链路
  scripts/verify-browser.mjs   真 headless Chrome + CDP 全链路
  e2e/                 配对实测（本子项目最重要的资产）
    run.mjs            双 profile 配对跑；外部 oracle 判定；--json 取 steps/tokens
    lingdong.patch.yml 臂 A：ld-e2e profile
    cua.patch.yml      臂 B：cua-e2e profile
    target.ps1         靶窗：token 放在 TextBox 或画在画布上
    form.ps1           靶窗：5 个控件（文本框/下拉/复选/单选/按钮）
    confirm.html       靶页：confirm() 决定标题，静默 dismiss 拿不到
    probe-dpi.mjs      DPI 虚拟化证据（见 §7 坑 1）
    RESULTS.md         两轮实测数据（原始数字都在这里）

packages/dsh-client-ui-lingdong/       客户端插件（浏览器工作台）
  lib/client.js        手写 bundle（无打包器）：画布 + codec + 流程库 + 撤销重做 + 片段生成
  scripts/verify-bundle.mjs  bundle 求值 + codec 往返 + 真 React SSR

python/winpilot_home/winpilot/
  desktop_observe.py   只读观测 + /window /snapshot /verify 三个新路由
  action/clipboard_ops.py  window_info_backend + _window_record（与枚举共用逐窗口读）
  fast/action_contract.py  ALLOWED_BATCH_ACTIONS/BATCH_MUTATING_ACTIONS/字段校验
  fast/controller.py    set_value / set_window_frame 两个 handler + _resolve_action_hwnd
python/winpilot_home/tests/test_studio_action_batch_controller.py  35 个用例
```

## 4. 验证命令（全绿才算完成）

```powershell
node packages/dsh-plugin-lingdong/scripts/smoke.mjs              # 真 runtime + 真挂载
node packages/dsh-plugin-lingdong/scripts/verify-bridge.mjs      # 桥 + runtime 全链路
node packages/dsh-plugin-lingdong/scripts/verify-browser.mjs     # 真 Chrome + CDP
node packages/dsh-client-ui-lingdong/scripts/verify-bundle.mjs   # codec + SSR
python -m pytest python/winpilot_home/tests -q                   # 应为 1486 passed, 13 skipped
dsh --profile web --dump-config --patch "C:/Users/zou/Desktop/claude/packages/dsh-plugin-lingdong/dsh.patch.yml"
```

配对实测（**改任何东西后都要重跑**）：

```powershell
node packages/dsh-plugin-lingdong/e2e/run.mjs                    # 两臂全任务，~25 min
node packages/dsh-plugin-lingdong/e2e/run.mjs --arm lingdong     # 只跑自己
node packages/dsh-plugin-lingdong/e2e/run.mjs --task fill_form   # 单个任务
node packages/dsh-plugin-lingdong/e2e/run.mjs --list             # 任务清单
```

`output/e2e/report-*.json` 是原始记录（含每任务 steps/tokens/耗时/事件流），`output/` 已 gitignore。

## 5. 已实测的事实

**E2E 两轮（8 任务 × 2 臂）**

| 任务 | 我们步数 | cua 步数 | 通过 |
| --- | --- | --- | --- |
| shell_control（非 GUI 对照） | 3 / 2 | 3 / 2 | 都通过（证明测试台中立） |
| notepad_save | 13 / 15 | 38 / 33 | 都通过 |
| calculator_clipboard | 21 / 15 | 46 / 63 | 都通过 |
| read_target_text | 7 / 9 | **6 / 4** | 都通过 |
| read_target_pixels | 9 / 11 | **7 / 5** | 都通过 |
| fill_form | 14 / 15 | 28 / 34 | 都通过 |
| browser_confirm | 11 / 16 | 17 / 25 | 都通过 |
| window_frame | 11 / 11 | 31 / 20 | **只有我们通过** |

- 合计：我们 89+94=183 步 / 4.43M+4.64M=9.07M tokens；cua 176+186=362 步 / 12.21M+12.06M=24.27M tokens。
- `window_frame` cua 两轮都失败且**数值完全一致**（求 `200,150 640x480`，得 `191,150 658x489`）→ 系统性。
- **但**：拿掉这一个任务就是 7/7 对 7/7。完成率优势对"frame 定义"敏感，别当成决定性证据。
- 成本优势覆盖 5 个变更类任务 × 2 轮，可解释：`lingdong_desktop` 一次提交整条动作序列，`lingdong_snapshot` 一次给树+图；cua 要分开调。**这条才是硬结论。**

**能力面已补（相对 cua 0.28.0，均为实测）**

| 能力 | 落点 | 证据 |
| --- | --- | --- |
| `set_value`（UIA 原子写值，不点不抢焦点） | `fast/controller.py::_set_value` | smoke：回读一致；e2e 未覆盖 |
| `set_window_frame`（不抢焦点、独立回读） | `fast/controller.py::_set_window_frame` | smoke + e2e 两轮精确命中 |
| 浏览器 file upload | `lingdong_browser` action `upload` | verify-browser：页面自己确认收到文件 |
| 窗口级 UIA+截图同帧快照 | `/api/v2/desktop/snapshot` + `lingdong_snapshot` | smoke：JPEG 帧头尺寸与自报一致 |
| 状态断言（含 ground truth） | `/api/v2/desktop/verify` + `lingdong_verify` | smoke：成立/不成立/未支持键按名拒绝 |
| 启动解析（`notepad` 不解析成 Notepad++） | ARP `launch` PATH 优先 | e2e 前置观察：cua 解析成了 Notepad++ |

## 6. 未完成（按优先级）

1. **`invoke_menu`** — 按路径调用应用菜单。
   - 已知：菜单栏与一级项可达；二级子项 `GetChildren()` 返回空（实测）。
   - 下一步：改用 Win32 `GetMenu`/`GetMenuItemInfo` + `WM_COMMAND` 遍历，或走 UIA 的 LegacyIAccessible。
   - 落点：canonical 动作词汇（`fast/controller.py` + `fast/action_contract.py`），与 `set_value` 同路。
2. **一等 `kill_app`** — 目前能力可达（离线流程 `run_shell` taskkill，实测 exit 0、0 模型调用），但不是一等工具。
3. **浏览器 dialog 处理** — `lingdong_browser` 无 dialog 动作。e2e `browser_confirm` 目前**两边都过**，所以还没被证明是缺口；要设计一个 Playwright 静默 dismiss 就拿不到结果的任务才能暴露。
4. **浏览器 pointer drag** — 缺 `drag`。
5. **扩任务集 + 多轮重复** — 目标是把"完成率优势只来自一个任务"变成多点优势；至少再补 4 个任务覆盖 §6.1–6.4，每任务跑 ≥3 轮。

## 7. 踩过的坑（不要再踩）

1. **DPI 虚拟化会骗过 oracle**。DPI 不感知的进程读到的每个窗口矩形都会被按显示缩放虚拟化（125% → 0.8×）。第一版 oracle 就是这样把一个完全正确的 `set_window_frame`（真 640×480）读成 512×384 并判失败。**任何读窗口几何的 oracle 必须先 `SetProcessDpiAwarenessContext(-4)`**。证据：`e2e/probe-dpi.mjs`。
2. **不要经 `dsh` shim 起进程**。cmd.exe 会把任务文本里以 `-` 开头的 token 当 flag（报 "`-` must be the only task argument"）。直接 `node <npm root -g>/@deepseek-ai/dsh/lib/bin.js ...`，`shell:false`。
3. **`launch notepad` 在已有 Notepad 时会 adopt 现有窗口**，不新建。smoke 已改成"存在可用窗口"而不是"出现新窗口"，且只关自己开的。
4. **pywin32**：`SetWindowPos` 返回 `None` 不是 BOOL；`win32gui` **没有** `IsZoomed`（用 `GetWindowPlacement()[1] == SW_SHOWMAXIMIZED`）。两者都曾让代码静默错误。
5. **`computerUse` 必须是可选注入**。列进必需注入会让插件在不带该服务的组合里永远 pending，模型拿不到任何桌面工具（headless 上实测）。
6. **UIA 元素投影不要用 `dataclasses.asdict`**：`extra` 里有活的 `uia_ctrl` 句柄，会让 FastAPI 500。用 `UIElement.to_dict()` + 只保留 `extra` 的标量。
7. **测完清桌面**：Notepad / Calculator / Chrome / 靶窗。`run.mjs` 每个 GUI 任务开始前清、整轮结束再清。
8. **不要与重型全量 pytest 并发跑 GUI E2E**（历史记录：会让 `/api/status` 超时）。
9. **临时 profile 不要删**：`ld-e2e` / `cua-e2e` 是 E2E 的运行基础；`~/.dsh/profiles/web` 不要碰。

## 8. 关键实现约定

- **单写入者**：所有物理输入走 canonical ARP → `fast/controller.py` → `RawDevice`。`set_value`/`set_window_frame` 是**非合成输入**的变更动作，不进 raw_device，靠独立回读判定成功。
- **不要同挂两个 provider**：DSH 的 computer-use 槽排他；更要紧的是会出现两个物理输入写入者，破坏唯一不可削弱的机械边界。臂 A 与臂 B 是两个互斥 profile，不是同时挂。
- **动作要能被录制/重放**：加进 canonical 词汇表才能进 `.automation.json`。`set_value` 因文本敏感被排除出重放（`_replay_for` 返回 None）；`set_window_frame` 的重放步骤**刻意还没发**——重放词汇表（`recorder/wps_validate.py` + `recorder/trace.py::_run_step`）得先认得它，而 `trace.py` 是用户的改动文件，**动手前先确认可以碰**。
- **浏览器会话有状态**：除 `launch`/`connect` 外一律拒绝；`connect` 自己先探 CDP 端点，**绝不悄悄开可见浏览器**。launch 起的用 CDP `Browser.close` 关（子进程只是 launcher）。
- **网页工作台**：`lib/client.js` 是手写 bundle（`window.__ModuleLoader__.load`），**不需要打包器**。画布顺序 = `automation-ir/1` 的 `steps` 顺序；不认识的步骤原样保留成 `replay_step` 节点。

## 9. 新窗口第一条命令

```powershell
cd C:\Users\zou\Desktop\claude
git switch feat/dsh-plugin
git log --oneline -3
node packages/dsh-plugin-lingdong/e2e/run.mjs --list
```

然后读 §6，从 1 开始做。
