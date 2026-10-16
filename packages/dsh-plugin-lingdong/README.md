# @lingdong/dsh-plugin-lingdong

DSH 的 computer-use provider：真实 Windows 桌面操控 + 录制 + 离线重放 + 流程编排 + 浏览器会话。

> **状态、实测数据、未完成项、踩过的坑 → [AGENTS.md](AGENTS.md)。本文件只是参考手册。**

## 安装（3 步，全部可逆）

```powershell
# 1) 装进 profile（官方命令，纯增量）
dsh plugin --profile web add "C:/Users/zou/Desktop/claude/packages/dsh-plugin-lingdong"
# 2) 建运行时 peer 链接（dsh 用 junction 装本地包，Node 要从仓库解析裸包名）
node packages/dsh-plugin-lingdong/scripts/link-peers.mjs
# 3) 用 overlay 启动（不改任何已有文件；不带 --patch 插件不挂载，DSH 行为不变）
dsh --profile web --patch "C:/Users/zou/Desktop/claude/packages/dsh-plugin-lingdong/dsh.patch.yml"
```

卸载：`dsh plugin --profile web remove @lingdong/dsh-plugin-lingdong`。

`dsh.patch.yml` 做两件事：禁用 `computer-use-cua-driver-native` 释放排他槽；插入本插件 + 客户端 UI。

## 16 个工具

| 工具 | 作用 | 关键点 |
| --- | --- | --- |
| `lingdong_status` | 运行时可达性/构建标识/配置 | 会话第一个动作 |
| `lingdong_windows` | 可见窗口 + 后台应用 | 取**精确窗口标题**，别猜 |
| `lingdong_screen` | 虚拟屏幕原点与尺寸（物理像素） | 坐标边界 |
| `lingdong_cursor` | 物理光标位置 | |
| `lingdong_apps` | 按名发现已安装应用 | 启动前先解析 |
| `lingdong_elements` | 单窗口 UIA 元素 | 标准控件优先用它 |
| `lingdong_snapshot` | **窗口级 UIA + 截图，同一帧** | 小控件比全屏更清楚；最小化窗口返回 `image:null` 并说明 |
| `lingdong_verify` | **状态断言**（运行时自己的判定器） | 支持 ground truth：`file_exists`/`process_running`/`shell_true` 等；未支持键**按名拒绝** |
| `lingdong_observe` | 抓全屏并**返回图像** | 每次变更后重新观察 |
| `lingdong_desktop` | 一次有序 canonical 动作批 | 唯一物理输入入口 |
| `lingdong_run_goal` | 自然语言目标交给视觉 Agent | 步骤未知时用 |
| `lingdong_record` | 人类演示录制 start/stop/status | 重放段的来源 |
| `lingdong_export` | 一次 run / 整个 thread → Automation IR | 可离线重跑 |
| `lingdong_flow_files` | 流程脚本 list/read/write | |
| `lingdong_run_flow` | 离线执行流程 | **0 次模型调用** |
| `lingdong_browser` | CDP 浏览器会话（22 个动作） | 见下 |

### `lingdong_desktop` 动作表

```
observe click click_element type_into_element type_text set_value hotkey scroll
move_mouse hold_key drag draw_strokes focus_window set_window_frame launch wait
wait_change wait_for_visual_change wait_stable read_text zoom screenshot
clipboard_read clipboard_write clipboard_paste
```

- 传 `window` 时插件自己前置 `focus_window`；坐标是**绝对物理像素**。
- **`set_value`**：走控件 UIA value pattern 直接换值——不点、不合成按键、目标不必在前台。
  用 `automation_id`/`x,y`/`element_id`/`label` 定位；**独立回读**判定成功；无 value pattern 按名拒绝
  （可退回 `type_into_element`）。文本敏感：不进 trace、不成为重放步骤。
- **`set_window_frame`**：`SWP_NOACTIVATE|SWP_NOZORDER`，不抢焦点，报 `foreground_before/after`；
  独立回读矩形判定。只有窗口**真的**拒绝改动且 placement 是最大化时才还原重试一次。
- 两者都**不经过物理输入**（不进 RawDevice），是 canonical 词汇表里仅有的两个这类动作。

### `lingdong_browser` 动作表（22）

```
status launch connect close open new_tab tabs switch close_tab read find click
type press select hover scroll wait eval screenshot upload download
```

**会话有状态**：除 `launch`/`connect` 外一律拒绝——底层 helper 会在无会话时自己起一个
**可见** Chrome（5 秒预算，失败后退回可能没下载的 Playwright 内建浏览器）。`connect` 先自己探
CDP 端点，探不到就报错。`launch` 起的浏览器归本插件所有（私有 profile/端口，默认 headless），
用 CDP `Browser.close` 关闭（启动进程只是 launcher，它的退出说明不了任何事）；`connect` 附加上去的
是调用者的，只断开不关闭。

实现：复用仓库既有的 `python/tools/feature_call.py` 常驻 worker（**进程边界**，不 import 旧
`features.*` 命名空间；Electron 本来就这样驱动它）。

## 流程工作台的宿主通道

浏览器工作台（[packages/dsh-client-ui-lingdong](../dsh-client-ui-lingdong)）是同源路由，
不持有 runtime token、不需要 CORS：

```
GET  /lingdong/api/status
GET  /lingdong/api/flows                       PUT  /lingdong/api/flows/<file>
GET  /lingdong/api/flows/<file>                POST /lingdong/api/flows/<file>/run
POST /lingdong/api/validate                    POST /lingdong/api/run
GET  /lingdong/api/runs/<id>?kind=replay|arp   POST /lingdong/api/runs/<id>/cancel
POST /lingdong/api/fragments                   GET  /lingdong/api/fragments/<job_id>
POST /lingdong/api/fragments/<job_id>/cancel
```

- **不是第二个桌面面**：每个写操作落到 canonical 端点，校验/ARP 管线/单写入者仍在 runtime。
- **不自己发明准入**：先交给 DSH 的 `ctx.connection.requestRejection()`；该服务不在时退回**更严**
  的回环兜底（拒绝跨站 Origin 与 `sec-fetch-site: cross-site`）。
- 组合里没有 `webServer` 不是失败：工具面照常挂载。
- `computerUse` 是**可选**注入（必需注入会让插件在不带该服务的组合里永远 pending）。
- 片段生成转发给运行时既有的 `FlowFragmentProposer`：一次用户主动触发的可选调用，不观察桌面、
  不读会话、无执行权限；桥不拼提示词、不碰模型。

## 配置键

| 键 | 默认 | 作用 |
| --- | --- | --- |
| `repoRoot` | 本包的 monorepo 根 | 含 `python/`、`python-runtime/` |
| `python` | 空 → 内建 runtime → PATH | 解释器 |
| `host` / `port` | `127.0.0.1` / `8790` | runtime 回环地址 |
| `token` | 空 → 每次加载随机 | 控制令牌 |
| `autoStart` | `true` | 没监听就拉起 |
| `registerComputerUse` | `true` | 占排他槽 |
| `requestTimeoutMs` / `startupTimeoutMs` | `120000` / `60000` | |
| `browserEnabled` | `true` | 挂浏览器工具 |
| `browserExecutable` | `''` | 空 → 探测常见位置再查 PATH |
| `browserPort` / `browserTimeoutMs` / `browserStartupTimeoutMs` | `9222` / `120000` / `60000` | |

## 边界与不变量

- **单写入者**：所有物理输入走 canonical ARP → `fast/controller.py` → `RawDevice`。
- **不同挂两个 provider**：槽排他，且会出现两个物理输入写入者——破坏唯一不可削弱的边界。
- **`_replay_for`**：`set_value` 因文本敏感返回 None；`set_window_frame` 的重放步骤**刻意未发**，
  重放词汇表得先认得它（`recorder/wps_validate.py` + `trace.py::_run_step`）。
- **回归**：`python/winpilot_home/tests` 应为 `1486 passed, 13 skipped`。

## 验证

```powershell
node scripts/smoke.mjs            # 真 runtime + 真 apply() 挂载 + 真桌面动作
node scripts/verify-bridge.mjs    # 真 http server + 真 runtime 全链路
node scripts/verify-browser.mjs   # 真 headless Chrome + CDP 全链路
node e2e/run.mjs                  # 与 cua-driver 配对实测（~25 min）
```

每轮实测的原始数字在 [e2e/RESULTS.md](e2e/RESULTS.md)。
