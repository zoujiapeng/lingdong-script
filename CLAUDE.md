# 灵动脚本 (Lingdong Script)

Electron + React + Python 桌面自动化流程编辑器。可视化编排屏幕点击、OCR识别、条件分支、循环等自动化操作。

## 快速导航

| 命令 | 用途 |
|---|---|
| `npm run dev` | 启动开发环境 (Vite + Electron) |
| `npm run build` | 构建 |
| `python -m pytest tests/` | 运行 Python 测试 (88 test) |
| `python python/mcp/server.py` | 启动 Python MCP 服务器 |
| `npm run ai:mcp` | 启动 TypeScript MCP 服务器 |
| `node cli/lobster.js run-flow <file.flow>` | CLI 执行流程 |
| `python tools/dsl_to_flow.py < file.dsl` | DSL 转 FlowScript |
| `curl -X POST http://127.0.0.1:5577/api/ping` | 检查 HTTP API 桥接 |

## 目录结构

```
├── src/
│   ├── main/           # Electron 主进程 (IPC handlers, 窗口管理)
│   │   ├── main.ts     #   - 引擎管理, IPC handlers, HTTP API :5577
│   │   └── preload.ts  #   - contextBridge API 暴露
│   └── renderer/       # React 前端 (Vite)
│       ├── App.tsx     #   - 根组件, 布局, 快捷键
│       ├── store/      #   - Zustand + immer 状态管理
│       ├── types/      #   - FlowNode, FlowEdge, NodeParams 类型
│       ├── components/
│       │   ├── Canvas/ #   - FlowCanvas, FlowNode
│       │   ├── PropertyPanel/ # - 节点参数编辑器 (16+ 种)
│       │   ├── NodeLibrary/   # - 节点库面板 (127 个节点)
│       │   └── common/ #   - TopBar, StatusBar, LogPanel, ToolCallPanel
│       └── utils/      #   - textGenerator, featureRegistry, nodeDefinitions
├── python/
│   ├── engine/         # Python 自动化引擎
│   │   ├── executor.py #   - DSL 执行器 (核心)
│   │   ├── dsl_parser.py #  - DSL 解析器
│   │   └── dsl_flow_converter.py # - DSL↔FlowScript 双向转换
│   ├── features/       # @feature 注册表 (~120 功能)
│   │   ├── registry.py #   - 装饰器, 自动生成 MCP/CLI/HTTP
│   │   └── _all.py     #   - 导入所有 feature 模块
│   ├── mcp/server.py   # Python MCP 服务器 (stdio 协议)
│   ├── interaction/    # 交互工具
│   │   ├── wheel_selector.py # - 全屏滚轮选择器
│   │   └── region_picker.py  # - 全屏区域选择器 (tkinter)
│   └── perception/     # OCR, 图像匹配
├── cli/lobster.js      # TypeScript CLI
├── tools/              # 工具脚本 (dsl_to_flow.py 等)
├── templates/          # 工作流模板 (.flow 文件)
└── tests/              # Python 测试套件
```

## HTTP API 桥接 (Claude Code 远程控制)

Electron 主进程内置 HTTP 服务器 `http://127.0.0.1:5577`（仅 localhost），Claude Code 可通过 curl 实时控制前端。

| 端点 | 用途 | 关键参数 |
|---|---|---|
| `POST /api/ping` | 健康检查 | — |
| `POST /api/load-script` | 加载完整流程图 | `{ script: FlowScript }` |
| `POST /api/run-dsl` | DSL 文本→流程图 | `{ dsl: string }` |
| `POST /api/add-node` | 添加节点 | `{ type, x, y, params? }` |
| `POST /api/update-node` | 更新节点属性 | `{ id, updates }` |
| `POST /api/update-node-params` | 更新节点参数 | `{ id, params }` |
| `POST /api/delete-nodes` | 删除节点 | `{ ids: string[] }` |
| `POST /api/add-edge` | 添加连线 | `{ from_node, to_node, from_port?, to_port? }` |
| `POST /api/delete-edges` | 删除连线 | `{ ids: string[] }` |
| `POST /api/auto-layout` | 自动排列节点 | — |
| `POST /api/new-script` | 新建空白脚本 | — |
| `POST /api/set-view-mode` | 切换视图 | `{ mode: 'canvas'\|'pseudocode'\|'settings' }` |
| `POST /api/fit-screen` | 自适应缩放 | — |
| `POST /api/get-state` | 读取当前状态 | — |

## MCP 工具

提供 **两个 MCP 服务器**：

### 1. Python MCP (主)

启动: `python python/mcp/server.py`

通过 `@feature` 注册表动态生成 ~120 工具，覆盖：

| 分类 | 工具数 | 功能 |
|---|---|---|
| 交互动作 | ~15 | click, type, swipe, hotkey, scroll, screenshot... |
| 感知识别 | ~12 | find_image, OCR, color_detect, progress_detect... |
| 控制流 | ~4 | wait, loop, condition... |
| AI 规划 | ~8 | plan, execute_plan, debug... |
| 系统功能 | ~10 | window, process, clipboard, file... |
| 调试诊断 | ~10 | test, validate, system_info... |
| DSL 工具 | 5 | run_dsl, dsl_to_flow, flow_to_dsl, create_flow_from_dsl |

关键工具示例：
- `dsl_to_flow(dsl: string)` — DSL 文本转 FlowScript JSON
- `flow_to_dsl(script: object)` — 流程图转 DSL 文本
- `run_dsl(dsl: string)` — 本地执行 DSL（免 token, 免后端）
- `create_flow_from_dsl(dsl, name)` — DSL → 保存 .flow → 返回 JSON

### 2. TypeScript MCP (桥接)

启动: `npm run ai:mcp`

5 个工具用于前端集成和文件管理。

## 工作流

### 创建自动化流程
1. 在画布拖拽节点编排流程
2. 节点类型: 点击、输入、截图、OCR、条件、循环、等待
3. 属性面板编辑参数 (支持区域拾取、策略选择)
4. 文本视图可切换中文伪代码 / 英文 DSL 模式
5. 保存为 .flow 文件, 或导出 DSL

### 调试执行
1. 点击 "运行" 或按 F9 启动
2. 引擎逐节点执行, 高亮当前节点
3. 日志面板显示执行详情
4. 支持暂停/继续/停止
5. 节点标注成功/失败/跳过状态

### AI 辅助
1. 点击 "AI 助手" 用自然语言描述需求
2. AI 生成 DSL → 自动转为流程图
3. 或通过 Claude Code 调用 MCP 工具生成
4. 模板文件在 `templates/` 目录

## 关键约定

- **新增节点类型**: `types/index.ts` + `nodeDefinitions.ts` + `params/*.tsx` + `FlowNode.tsx` (getNodeSummary) + `engine.py` (execute_*)
- **新增 MCP 工具**: 在 `python/features/` 用 `@feature` 装饰器声明，自动注册
- **新增 API 端点**: 在 `src/main/main.ts` 的 `startApiServer()` switch 中添加 case
- **状态管理**: Zustand + immer，`useStore` hook
- **Electron IPC**: `preload.ts` 暴露 → `main.ts` 处理 → Python 子进程
- **DSL 格式**: 大写关键字 `CLICK 100 200` / `LOOP 5 ... END` / `IF cond ... ELSE ... END`
- **Python 协议**: `engine.py` 通过 stdin/stdout JSON 与 Electron 通信
- **测试**: `pytest tests/` 88 tests, `npm test` 前端测试
- **中文编码**: 所有 Python 子进程通过 `spawnPython()` 强制 `PYTHONIOENCODING=utf-8`

## 节点类型速查

| 类型 | 用途 | 参数 |
|---|---|---|
| click | 点击坐标 | x, y, method, offset |
| input_text | 输入文字 | text, x, y, clear_before |
| keyboard | 快捷键 | keys[], modifiers[] |
| swipe | 滑动 | start_x/y, end_x/y, duration |
| wait | 等待 | time / image / window |
| condition | 条件分支 | image_exists / variable_equals / color_match |
| loop | 循环 | count / condition / infinite |
| screenshot | 截图 | region, save_path, format |
| image_click | 图片识别点击 | targets[], similarity |
| click_text | OCR 文字点击 | text, similarity, method |
| popup_close | 弹窗关闭 | strategies[], timeout |
| color_check | 颜色检测 | region, expected_color, tolerance |
| progress_wait | 等待进度条 | region, wait_type, timeout |
| region_wait | 等待区域稳定 | region, region_type, timeout |
| scene_condition | 场景识别 | scene_name, similarity |
| sub_script | 子流程 | script_id |
| variable_set/get | 变量操作 | name, value |
| tool_call | 任意 @feature 工具 | feature_name, params |

## 常见任务

```
# 运行测试
python -m pytest tests/ -x -q

# DSL → 流程图 往返验证
echo "CLICK 100 200" | python tools/dsl_to_flow.py

# 本地 DSL 执行（无需 Electron）
python -c "from engine.executor import DSLExecutor; e=DSLExecutor(None); e.run_dsl_sync('CLICK 100 200')"

# 查看 MCP 工具列表
python -c "from features._all import *; from features.registry import registry; print(registry.export_claude_context())"

# 启动滚轮选择器（独立测试）
python python/interaction/wheel_selector.py

# 通过 HTTP API 控制前端
curl -X POST http://127.0.0.1:5577/api/ping

# DSL → 流程图 → 加载到前端（一键）
curl -X POST http://127.0.0.1:5577/api/run-dsl -H "Content-Type: application/json" -d '{"dsl":"CLICK 500 400\nWAIT 2\nSCREENSHOT"}'
```
