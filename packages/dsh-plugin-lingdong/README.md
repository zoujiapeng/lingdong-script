# @lingdong/dsh-plugin-lingdong

把「灵动脚本 / WinPilot」现有的桌面能力作为一个 **DSH 插件**接入：实时操控电脑、录制、重放、流程图式脚本编排，以及沿用原有前端界面。

目标不是另起一个 Agent，而是**让 DSH 拥有更强的 `computer use`**：能力走 Cua Driver provider 的超集路线，并通过 DSH 官方的 provider seam 替换它。

---

## 1. 设计边界：只加不改

| 原则 | 做法 |
| --- | --- |
| 不修改 DSH 安装目录里的任何文件 | 插件是仓库里的独立包，用 `dsh plugin` 安装 |
| 不修改 profile 里已有的 `cordis.patch.yml` | 单独的 overlay 文件，用 `--patch` 在启动时叠加 |
| 不修改其它插件的行为 | 只在自己的 overlay 里禁用 Cua 两行；删掉 overlay 即完全还原 |
| 不阻塞 DSH 启动 | 桌面运行时是独立进程；启动失败只降级为工具报错 |
| 不重造输入 | 键鼠动作走仓库既有的 canonical ARP 管线（物理输入单写入 + 释放清理） |

## 2. 安装（三步，全部可逆）

```powershell
# 1) 装进 web profile（官方插件命令，纯增量）
dsh plugin --profile web add "C:/Users/zou/Desktop/claude/packages/dsh-plugin-lingdong"

# 2) 为该包建立运行时 peer 链接
#    dsh 用 junction 安装本地包，模块真实路径仍在仓库里，Node 会从仓库解析裸包名。
#    DSH 自带的本地 .mjs 插件全部只用相对 import，正是为了绕开这一点；
#    本插件需要 3 个真实依赖，所以自带一个 node_modules 软链（已被 .gitignore 覆盖）。
node packages/dsh-plugin-lingdong/scripts/link-peers.mjs

# 3) 用 overlay 启动（不改任何已有文件）
dsh --profile web --patch "C:/Users/zou/Desktop/claude/packages/dsh-plugin-lingdong/dsh.patch.yml"
```

> 只做第 1 步、不带 `--patch` 启动时，插件不会挂载，DSH 行为与现在完全一致。
> 卸载：`dsh plugin --profile web remove @lingdong/dsh-plugin-lingdong`。

## 3. 能力面

### 3.1 桌面原语与观测（覆盖 Cua Driver 的输入与观测能力）

| 工具 | 作用 |
| --- | --- |
| `lingdong_status` | 运行时可达性、构建标识、已加载配置 |
| `lingdong_observe` | 抓当前真实桌面并**返回图像**（模型直接看屏） |
| `lingdong_desktop` | 一次有序批处理执行 canonical 动作：click / click_element / type_text / hotkey / scroll / move_mouse / drag / hold_key / launch / focus_window / wait / read_text / zoom / screenshot / clipboard_* |
| `lingdong_windows` | 可见窗口 + 后台应用枚举（用来取**精确窗口标题**，而不是猜） |
| `lingdong_screen` | 虚拟屏幕原点与尺寸（物理像素），即坐标的边界 |
| `lingdong_cursor` | 当前物理光标位置 |
| `lingdong_apps` | 按名称发现已安装应用，或列出有界索引 |
| `lingdong_elements` | 单个窗口的无障碍元素（UIA 可用时） |
| `lingdong_run_goal` | 把自然语言目标交给视觉 Agent 实时操控电脑（步骤未知时用） |

### 3.2 DSH 及其 Cua provider 没有的部分

| 工具 | 作用 |
| --- | --- |
| `lingdong_record` | 人类演示录制（start / stop / status） |
| `lingdong_export` | 一次 run 或整个 thread → 离线 Automation IR 文档 |
| `lingdong_flow_files` | 列出 / 读写可执行的流程与脚本文档 |
| `lingdong_run_flow` | 离线执行流程：**重放段与脚本节点在同一次 run 里共享变量、一起执行，0 次模型调用** |

### 3.3 相对 Cua Driver 的目标差异

| 维度 | Cua Driver provider | 本 provider |
| --- | --- | --- |
| 输入原语 | 有 | 有（复用 canonical ARP） |
| 看屏 | 有 | 有（权威全屏 JPEG + 受管工件） |
| 录制 → 重放 | **无** | 有 |
| 流程图编排 | **无** | 有（可视化编辑 + 离线 0-token 执行） |
| GUI 内编辑流程 | **无** | 有（浏览器工作台，见 §3.4） |
| 运行审计 | 窗口/元素快照 | 每 run durable trace + SHA-256 工件 |
| Agent 内核 | 用 DSH 的 | **用 DSH 的**（本插件不替换 DSH 的推理与编排） |

### 3.4 浏览器工作台的数据通道（`/lingdong/api`）

流程工作台跑在浏览器里，既拿不到运行时的控制 token，也不能跨源访问运行时的回环端口。
本插件因此在 **DSH 自己的 web server** 上注册一条前缀路由，把流程操作转给运行时：

| 路由 | 作用 |
| --- | --- |
| `GET /lingdong/api/status` | 运行时可达性与构建标识 |
| `GET /lingdong/api/flows` | 脚本库列举（沿用运行时的 `list_scripts`） |
| `GET /lingdong/api/flows/<file>` | 读一份文档 |
| `PUT /lingdong/api/flows/<file>` | 保存一份文档（校验由运行时做） |
| `POST /lingdong/api/validate` | 用运行时自己的校验器校验，不落盘 |
| `POST /lingdong/api/run` | 运行画布上尚未保存的文档 |
| `POST /lingdong/api/flows/<file>/run` | 运行已保存的文档 |
| `GET /lingdong/api/runs/<id>?kind=replay\|arp` | 轮询运行状态与逐步结果 |
| `POST /lingdong/api/runs/<id>/cancel` | 取消 |
| `POST /lingdong/api/fragments` | 一句话生成流程片段（转发给运行时自己的 `FlowFragmentProposer`） |
| `GET /lingdong/api/fragments/<job_id>` | 轮询片段生成任务 |
| `POST /lingdong/api/fragments/<job_id>/cancel` | 取消片段生成 |

设计要点：

- **不是第二个桌面面**。每个写操作都落到运行时的 canonical 端点（`/api/script/*`、
  `/api/v2/automation-ir/runs`），校验、ARP 管线、物理输入单写入者仍由运行时拥有。
  本桥只搬 JSON。
- **不自己发明准入策略**。请求先交给 DSH 自己的 `ctx.connection.requestRejection()`；
  该服务不在时退回到**更严**的回环兜底（Host 必须是回环、拒绝跨站 `Origin` 与
  `sec-fetch-site: cross-site`）。所以把 web server 绑到 `0.0.0.0` 供手机访问时，
  判定仍由操作者配置的可信主机名单决定，插件不会比 GUI 的其它 `/api` 更宽松。
- **组合里没有 web server 时不是失败**：工具面照常挂载，只是没有工作台可服务。
- **片段生成不重建第二套模型管线**。一句话生成片段转发给运行时既有的
  `/api/v2/flow-fragments/proposals`（`FlowFragmentProposer`）：一次用户主动触发的
  可选调用，不观察桌面、不读会话、没有执行权限。桥这边不拼提示词、不碰模型。

## 4. 已知边界（v0）

- **目标窗口是插件侧纪律，不是后端规则**。已核实：`POST /api/v2/arp/validate` 不会因为没有 `focus_window` 就拒绝一个坐标动作——"空间动作前必须建立目标"是**流程图编辑器**的守卫。所以 `lingdong_desktop` 传 `window` 时由插件自己前置 `focus_window`；坐标是屏幕绝对像素，命名目标才让动作确定。
- `lingdong_run_flow` 接受 `automation-ir/1`；`wps_replay` 类型走 Replayer，`arp_graph` 类型走 ARP。
- **观测面已补齐到只读枚举层**：窗口、应用、屏幕、光标、UIA 元素都已暴露为只读 REST 并被插件工具覆盖（见 [desktop_observe.py](<../../python/winpilot_home/winpilot/desktop_observe.py>)）。仍未覆盖的是 Cua 的**状态快照**类工具（`get_window_state` 式的窗口级 UIA+截图联合快照）、`verify_state` 断言和浏览器工具族。
- **工作台只编辑线性 `wps_replay` 流程**。`arp_graph` 文档会被明确拒绝而不是压扁成假参数；录制文档里工作台不认识的步骤（如 `ctrl.loop`、带 else 的 `ctrl.if`、`focus_window`）会变成"离线脚本片段"节点**原样**保留。

## 5. 验证

```powershell
# 语法
Get-ChildItem packages/dsh-plugin-lingdong/src -Filter *.mjs | ForEach-Object { node --check $_.FullName }

# 组合树（只读、不启动服务）
dsh --profile web --dump-config --patch "C:/Users/zou/Desktop/claude/packages/dsh-plugin-lingdong/dsh.patch.yml" | Select-String lingdong

# 冒烟：拉起真实 Python runtime，校验工具面、生成的图，并把插件真的挂载一次
node packages/dsh-plugin-lingdong/scripts/smoke.mjs

# 数据通道端到端：真实 HTTP server + 真实 runtime，跑完 读/写/校验/运行/准入
node packages/dsh-plugin-lingdong/scripts/verify-bridge.mjs

# 浏览器工作台：bundle 求值 + 画布 codec 往返 + 真实 React 服务端渲染
node packages/dsh-client-ui-lingdong/scripts/verify-bundle.mjs
```

`verify-bridge.mjs` 不是形状检查：它起一个真实 `node:http` server 挂上真实路由，用插件自己的
sidecar 拉起仓库的 runtime，然后走一遍工作台会走的请求序列，最后**用浏览器 bundle 自己导出的
codec** 生成一份文档，交给运行时校验、保存并真的跑完——包括 `ctrl.if` 嵌套步骤的索引路径
（`2.1`）与画布的节点映射逐条对齐。它跑的流程只有一个 `ext.delay`，不碰桌面、不产生物理输入。

已通过的验证（本机实测）：

| 检查 | 结果 |
| --- | --- |
| 6 个模块 `node --check` | 通过 |
| overlay YAML 解析 | 通过 |
| `--dump-config` 叠加 overlay | `computer-use-cua-driver-native → disabled: true`，`lingdong` 行插入，exit 0，未改任何 DSH 文件 |
| 从 profile 上下文 import 本插件 | 导出 `Config, PROVIDER_NAME, apply, inject, name` 正确 |
| 冒烟：运行时可达 + `/api/ping` | 通过（子进程启动与 SIGTERM 回收均正常） |
| 冒烟：13 个工具名称/唯一性/schema/handler/描述 | 全部通过 |
| 冒烟：`/api/v2/desktop/screen` / `cursor` / `windows` / `apps` | 通过（1920x1080；窗口枚举；发现 625 个应用）|
| 冒烟：真实 `apply()` 挂载（有/无 web server 两种组合） | 通过：占用 computer-use 槽、注册 13 个工具、1 段提示词、1 条 `/lingdong/api` 前缀路由；无 web server 时工具面不受影响 |
| **真实 DSH 启动中挂载** | 通过：临时实例（3099）启动后 `apply()` 执行并拉起 Python 运行时（8790 监听）；3080 上的原实例全程未受影响 |
| 冒烟：observe 图与带目标的空间图过真实 ARP 校验 | 通过 |
| 冒烟：`window` 前置 `focus_window`、不传则不动 | 通过 |
| 数据通道：真实 server + 真实 runtime 全链路 60 项 | 全部通过（含准入拒绝、路径穿越拒绝、非法文档拒绝、运行轮询、逐步结果、片段生成路由与请求形状） |
| 数据通道：画布 codec 产物被运行时接受并真的执行 | 通过：`ctrl.if` 条件求值为真、`2.1` 步骤执行、索引→节点映射与画布一致 |
| 片段生成：运行时的校验发生在任何 provider 调用之前 | 通过：画布构建的 `document` 摘要通过运行时逐字段检查，被改坏的 catalog 由运行时自己的校验器拒绝 |
| 工作台：bundle 求值 + 71 项 codec/渲染断言 | 全部通过 |

## 6. 后续阶段

1. **v0 ✅**：host 插件 + overlay patch + 冒烟脚本；已在真实 DSH 启动中证明挂载。
2. **v2（进行中 ✅ 大部分）**：只读观测面已补齐（屏幕/光标/窗口/应用/UIA 元素）。
3. **v1 ✅ 数据接通**：流程工作台在 DSH Web GUI 里可读、可写、可运行 `.automation.json`
   —— [packages/dsh-client-ui-lingdong](<../dsh-client-ui-lingdong>)。关键结论：DSH 客户端插件是
   `window.__ModuleLoader__.load(...)` 包装，`require` 由宿主注入，**不需要 tsdown 或任何打包器**，
   改完即生效。画布与宿主之间走本插件自己的 `/lingdong/api` 同源路由（§3.4）。
4. **v1 ✅ 交互补齐**：多选（Shift/Ctrl 点击 + 框选 + 整块移动）、撤销重做（手势级检查点、
   `Ctrl+Z`/`Ctrl+Shift+Z`/`Ctrl+Y`）、一句话生成片段（运行时既有 proposal 通道，预览后
   作为**一个** undo 检查点插入）。
5. **v2 余项**：窗口级状态快照（UIA + 截图联合）、`verify_state` 式断言、浏览器工具族。
