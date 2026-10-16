# @lingdong/dsh-client-ui-lingdong

流程工作台在 DSH Web GUI 里的客户端插件。

## 为什么可以手写，不需要打包器

DSH 客户端插件不是普通 npm 入口，而是一个 module-loader 包装（`lib/client.js` 就是最终产物）：

    window.__ModuleLoader__.load({
      id: "@lingdong/dsh-client-ui-lingdong",
      factory: (require) => {
        const react = require("react")                 // 宿主注入
        const { jsx } = require("react/jsx-runtime")
        // ctx.slots.register(...) / ctx.locale.register(...)
        exports.apply = apply
        exports.inject = inject
        return module.exports
      }
    })

`require` 由宿主提供，所以不需要 tsdown 或任何打包器；配合 `pnpm run dev:web` 的 HMR 接收器改完即生效。

## 画布就是一份 `.automation.json`

工作台画布不是另一种中间格式的界面：**画布的顺序就是文档 `steps` 的顺序**。
一个节点编译成一个 `.wps.json` 步骤，字段与录制导出（`_physical_attempt_to_wps`）
产生的步骤逐字段相同：

| 节点 | 步骤 | 说明 |
| --- | --- | --- |
| 点击 | `{tool:"click", coords:[x,y]}` (+`button`/`double`) | 与录制的点击形状一致 |
| 输入文本 | `{tool:"type_text", text_input}` | |
| 键盘 | `{tool:"hotkey", keys:[...]}` (+`repeat`) | `ctrl+s` 在画布上写，编译成 `["ctrl","s"]` |
| 等待 | `{tool:"ext.delay", seconds}` | |
| 条件 | `{tool:"ctrl.if", cond, then:[...]}` | 见下 |
| 截图 | `{tool:"ext.screenshot"}` (+`path`) | |
| 离线脚本片段 | 内嵌的那一步本身 | 录制产物的入口 |
| 运行命令 | `{tool:"run_shell", command}` (+`shell`/`timeout_s`) | |

能被命名的节点还可以带 `target_window: {title}`：重放会先切到该窗口，绝对坐标才确定。

### 条件节点为什么这样编译

线性管线里唯一的精确条件是"满足则执行后续"：条件节点编译成**一个** `ctrl.if`，
它的 `then` 就是它后面的全部步骤。这正是离线重放器对扁平 `ctrl.if` 的语义
（`replay_control.exec_if`：条件不成立时跳过 `then`，然后继续到脚本末尾）。
条件取值只用重放器真正支持的谓词：`file_exists` / `process_running` /
`process_gone` / `shell_true` / `window_appears`。

### 保守的地方

- **不认识的步骤原样保留。** 节点词表之外的步骤（`focus_window`、`ctrl.loop`、
  带 else 分支的 `ctrl.if` …）会变成"离线脚本片段"节点，步骤 JSON 逐字段写回。
- **不丢录制字段。** 一条录制点击可能带 `reference_frame`（CV 参考帧）。画布把它
  记在节点的 `extra` 里、面板上标出字段名、保存时原样写回，所以改坐标不会把参考帧弄丢。
- **不能精确编辑的条件不假装能编辑。** `cond` 不是"单个已知谓词"时，面板明说这条条件
  按原样保留，只让改 JSON。
- **拒绝而不是压扁。** `runtime.kind` 不是 `wps_replay` 的文档（例如 `arp_graph`）
  会被明确拒绝，而不是被编译成形状相似、语义不同的东西。
- **往返不变。** 打开一份录制文档再保存，除两处默认值归一（空 `else`、`repeat: 1`，
  都是重放器自己的默认值）外逐字段相同；第二次往返是不动点。

## 与宿主的连接

画布不直接访问运行时：它调用本插件 host 半边在 DSH web server 上注册的同源路由
`/lingdong/api/*`（详见 [packages/dsh-plugin-lingdong](<../dsh-plugin-lingdong>) §3.4）。
浏览器侧因此不需要运行时 token，也不需要 CORS。

运行前，画布先把文档交给**运行时自己的校验器**（`POST /lingdong/api/validate`）；
校验不过就只显示运行时的原话，不提交。运行总是提交画布上的当前文档，所以
"看到的就是跑到的"，未保存的改动也会被运行。

## 交互

- **节点库**：点击追加到末尾；已选中节点时插到它后面。
- **拖动即排序**：画布是自上而下的管线，竖直位置就是执行顺序；松手后按中心重排并吸附到网格。
  拖动一个多选中的节点会把**整块**移到落点。
- **多选**：Shift/Ctrl 点击切换单个节点，空白处拖动框选，`Ctrl+A` 全选。
  删除、上移、下移都作用于整个选择；"目标窗口"可以一次写给所有可定位节点。
- **撤销重做**：`Ctrl+Z` / `Ctrl+Shift+Z` / `Ctrl+Y`，工具栏也有按钮，深度 60。
  检查点是**手势级**的：一次拖动重排、一次多选删除、一次片段插入各算一步；连续敲同一个
  参数字段会合并成一步，而不是每键一步。焦点在文本框里时 `Ctrl+Z` 交给浏览器（文本撤销），
  这是输入参数时该有的行为。
- **属性面板**：按节点类型给出可精确编译的参数；目标窗口是通用字段。
- **状态栏**：节点/连线数、文件名与未保存标记、选中数、运行状态；每个节点在运行后显示 ✓/✕。

连线由节点顺序推导，不单独存储，所以画布顺序与执行顺序不可能不一致。

## 一句话生成片段

工具栏的"一句话生成片段"用一句话（例如"点击坐标 420,300，等待 1 秒，然后输入文本 hello"）
换一份**草稿**。它不是本插件新造的模型通道：请求转发给运行时既有的
`/api/v2/flow-fragments/proposals`（`FlowFragmentProposer`），一次用户主动触发的可选调用，
不观察桌面、不读当前会话、没有 connector 或执行权限。

- 请求里的 `document` 摘要与 `catalog` 用运行时逐字段校验的那套形状；catalog 只含确定性的
  离线节点，且每个节点只声明 `next` 一个后继分支——画布就是一条管线，多给分支只会换来一份
  放不进去的草稿。录制的"离线脚本片段"不在 catalog 里。
- 草稿回来后先本地校验（类型、参数名、连线、无环），再**线性化**：每个节点只有一个后继分支，
  所以每条连线都只表示"先后"，任何拓扑序都忠实执行草稿自己的约束。
- 预览里逐条列出将要插入的步骤和插入位置。点"插入流程"才落地，且是**一个** undo 检查点，
  不会自动运行。
- 无法精确插入的草稿会被明确拒绝，而不是压扁：条件谓词不在重放器支持集合内、某一步编译不出
  精确参数、或出现环，都会在预览处报错。
- 状态栏把两件事分开写：离线运行 **0 模型调用**，片段生成用了几次。不把可选的模型调用藏在
  "0 模型调用"后面。

## 验证

    node packages/dsh-client-ui-lingdong/scripts/verify-bundle.mjs

它做三件事：

1. 用桩 `window.__ModuleLoader__` 求值真实 bundle，断言导出契约与四条注册；
2. 直接驱动 bundle 导出的画布 codec：每类节点编译出的确切步骤、编译索引映射、一份录制文档
   展开再编译后的逐字段往返、二次往返是不动点、无法表示的文档被拒绝；片段通道的请求形状
   （`document`/`catalog` 的逐字段集合、单一后继分支、不向模型暴露不可编译节点）、草稿归一化、
   线性化（含 join）、以及六类非法草稿与三类不可插入草稿的拒绝；
3. 把捕获到的 pane 组件用**真实 React** 服务端渲染，断言画布结构（节点、连线、流程库、节点库、
   运行/撤销/重做/片段控件、状态栏，以及 agent 语义节点确实不在词表里）。

当前 71/71 通过。

端到端（画布产物真的被运行时执行）在 host 半边：

    node packages/dsh-plugin-lingdong/scripts/verify-bridge.mjs

它会用**本 bundle 导出的 codec** 生成文档，交给真实运行时的校验器、存进脚本库并真的跑完，
断言重放器报出的嵌套索引路径（`2.1`）与画布的节点映射逐条对齐；片段通道则验证到运行时的
校验器为止——改坏 catalog 会让任务在**任何 provider 调用之前**失败。
