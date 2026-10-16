# @lingdong/dsh-client-ui-lingdong

流程工作台在 DSH Web GUI 里的客户端插件。宿主半边见 [dsh-plugin-lingdong](../dsh-plugin-lingdong)。

## Bundle 格式（不需要打包器）

`lib/client.js` 就是最终产物，是 `window.__ModuleLoader__.load(...)` 包装，`require` 由宿主注入
（只允许 `react` 与 `react/jsx-runtime`）。没有 tsdown/rollup/webpack 在链路里。改完配合
`pnpm run dev:web` 的 HMR 接收器即生效。

## 画布就是一份 `.automation.json`

**画布顺序 = 文档 `steps` 顺序。** 一个节点编译成一个 `.wps.json` 步骤，字段与录制导出
（`trace.py::_physical_attempt_to_wps`）逐字段相同，所以"打开一份录制"和"保存一份编辑"是同一条代码路径。

| 节点 → 步骤 | 说明 |
| --- | --- |
| `click` → `{tool:"click", coords:[x,y]}` (+`button`/`double`) | 与录制点击同形状 |
| `input_text` → `{tool:"type_text", text_input}` | |
| `keyboard` → `{tool:"hotkey", keys:[...]}` (+`repeat`) | 画布写 `ctrl+s`，编译成 `["ctrl","s"]` |
| `wait` → `{tool:"ext.delay", seconds}` | |
| `condition` → `{tool:"ctrl.if", cond, then:[...]}` | 见下 |
| `screenshot` → `{tool:"ext.screenshot"}` (+`path`) | |
| `replay_step` → 内嵌的那一步本身 | 录制产物入口 |
| `run_command` → `{tool:"run_shell", command}` (+`shell`/`timeout_s`) | |

可命名的节点还能带 `target_window:{title}`（重放先切窗口，绝对坐标才确定）。

**条件节点**编译成**一个** `ctrl.if`，其 `then` = 它后面的全部步骤——这正是重放器对扁平 `ctrl.if`
的语义（`replay_control.exec_if`：条件不成立则跳过 `then`，继续到脚本末尾）。谓词只用重放器真正支持的：
`file_exists` / `process_running` / `process_gone` / `shell_true` / `window_appears`。

## 保守规则（不要放宽）

- **不认识的步骤原样保留**成 `replay_step` 节点（`focus_window`、`ctrl.loop`、带 else 的 `ctrl.if` …）。
- **不丢录制字段**：如点击的 `reference_frame` 存在节点 `extra` 里，保存时原样写回。
- **不能精确编辑的条件不假装能编辑**：面板明说按原样保留。
- **拒绝而不是压扁**：`runtime.kind != wps_replay` 的文档（如 `arp_graph`）明确拒绝。
- **往返不变**：除两处默认值归一（空 `else`、`repeat:1`）外逐字段相同；二次往返是不动点。

## 交互

- **节点库**：点击追加到末尾；已选中则插到其后。
- **拖动即排序**：竖直位置 = 执行顺序，松手后按中心重排并吸附网格；拖动多选中的节点移动**整块**。
- **多选**：Shift/Ctrl 点击切换；空白处拖动框选；`Ctrl+A` 全选。删除/上移/下移作用于整个选择；
  目标窗口可一次写给所有可定位节点。
- **撤销重做**：`Ctrl+Z` / `Ctrl+Shift+Z` / `Ctrl+Y`，深度 60，**手势级**检查点（一次拖动重排、一次
  多选删除、一次片段插入各一步；连续敲同一字段合并成一步）。焦点在文本框时 `Ctrl+Z` 交给浏览器。
- **一句话生成片段**：转发给运行时既有的 `/api/v2/flow-fragments/proposals`。请求里的 `document`/`catalog`
  用运行时逐字段校验的形状；catalog 只含确定性离线节点、每节点只声明 `next` 一个后继分支，**不含**
  `replay_step`。草稿回来后本地校验 + 线性化（每个节点只有一个后继分支 ⇒ 每条连线只表示"先后"，
  任何拓扑序都忠实执行，包括 join），**预览后**才插入，且是**一个** undo 检查点，不自动运行。
  无法精确插入的草稿明确拒绝（谓词不在支持集、某步编译不出精确参数、有环）。
  状态栏把"离线运行 0 模型调用"和"片段生成用了几次"分开写。

## 验证

```powershell
node scripts/verify-bundle.mjs   # 当前 71/71
```

1. 用桩 `window.__ModuleLoader__` 求值真实 bundle，断言导出契约与四条注册；
2. 直接驱动导出的 `flowCodec`：每类节点的确切步骤、编译索引映射、录制文档的逐字段往返、二次往返不动点、
   无法表示的文档被拒绝；片段通道的请求形状、草稿归一化、线性化（含 join）、6 类非法草稿与 3 类不可插入草稿；
3. 把捕获的 pane 组件用**真实 React** 服务端渲染，断言画布结构。

端到端（画布产物真的被运行时执行）在宿主半边：`node ../dsh-plugin-lingdong/scripts/verify-bridge.mjs`
——用本 bundle 导出的 codec 生成文档，交给真实运行时校验、存库并跑完，断言重放器报出的嵌套索引路径
（`2.1`）与画布节点映射逐条对齐。
