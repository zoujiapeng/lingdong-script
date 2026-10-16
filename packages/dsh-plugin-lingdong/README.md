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

### 3.1 桌面原语（覆盖 Cua Driver 的输入能力）

| 工具 | 作用 |
| --- | --- |
| `lingdong_status` | 运行时可达性、构建标识、已加载配置 |
| `lingdong_observe` | 抓当前真实桌面并**返回图像**（模型直接看屏） |
| `lingdong_desktop` | 一次有序批处理执行 canonical 动作：click / click_element / type_text / hotkey / scroll / move_mouse / drag / hold_key / launch / focus_window / wait / read_text / zoom / screenshot / clipboard_* |
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
| 运行审计 | 窗口/元素快照 | 每 run durable trace + SHA-256 工件 |
| Agent 内核 | 用 DSH 的 | **用 DSH 的**（本插件不替换 DSH 的推理与编排） |

## 4. 已知边界（v0）

- **目标窗口是插件侧纪律，不是后端规则**。已核实：`POST /api/v2/arp/validate` 不会因为没有 `focus_window` 就拒绝一个坐标动作——"空间动作前必须建立目标"是**流程图编辑器**的守卫。所以 `lingdong_desktop` 传 `window` 时由插件自己前置 `focus_window`；坐标是屏幕绝对像素，命名目标才让动作确定。
- `lingdong_run_flow` 接受 `automation-ir/1`；`wps_replay` 类型走 Replayer，`arp_graph` 类型走 ARP。
- **观测面还不是 Cua 的严格超集**：窗口枚举、应用列表、屏幕尺寸、光标位置、无障碍树在 Python 侧已实现（`winpilot/perception/`），但尚未暴露成 REST —— 见第 6 节 v2。
- **前端界面尚未迁移**（v1）。

## 5. 验证

```powershell
# 语法
Get-ChildItem packages/dsh-plugin-lingdong/src -Filter *.mjs | ForEach-Object { node --check $_.FullName }

# 组合树（只读、不启动服务）
dsh --profile web --dump-config --patch "C:/Users/zou/Desktop/claude/packages/dsh-plugin-lingdong/dsh.patch.yml" | Select-String lingdong

# 冒烟：拉起真实 Python runtime，校验工具面与生成的图（不碰桌面，只做契约校验）
node packages/dsh-plugin-lingdong/scripts/smoke.mjs
```

已通过的验证（本机实测）：

| 检查 | 结果 |
| --- | --- |
| 5 个模块 `node --check` | 通过 |
| overlay YAML 解析 | 通过 |
| `--dump-config` 叠加 overlay | `computer-use-cua-driver-native → disabled: true`，`lingdong` 行插入，exit 0，未改任何 DSH 文件 |
| 从 profile 上下文 import 本插件 | 导出 `Config, PROVIDER_NAME, apply, inject, name` 正确 |
| 冒烟：运行时可达 + `/api/ping` | 通过（子进程启动与 SIGTERM 回收均正常） |
| 冒烟：8 个工具名称/唯一性/schema/handler/描述 | 全部通过 |
| 冒烟：observe 图与带目标的空间图过真实 ARP 校验 | 通过 |
| 冒烟：`window` 前置 `focus_window`、不传则不动 | 通过 |

## 6. 后续阶段

1. **v0（本次）**：host 插件 + 桌面/录制/重放/流程工具 + overlay patch + 冒烟脚本。
2. **v1**：把 `src/renderer` 的流程工作台迁成 DSH client plugin（`dsh.client` + `./client` 入口 + `tsdown`），注册进 Web GUI。
3. **v2**：补齐观测面（窗口枚举、应用列表、屏幕尺寸、光标位置、无障碍树），做到 Cua 的逐项超集。
