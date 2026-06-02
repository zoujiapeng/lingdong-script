# 灵动脚本 (Lingdong Script)

Electron + React + Python 桌面自动化流程编辑器。可视化编排屏幕点击、OCR识别、条件分支、循环等自动化操作。核心设计理念：**一次 AI 生成即可永久零 Token 执行**，同时支持像通用 Agent 一样实时操控电脑。

## 快速导航

| 命令 | 用途 |
|---|---|
| `npm run dev` | 启动开发环境 (Vite + Electron), renderer :5173, HTTP API :7880 |
| `npm run build` | 构建 renderer (Vite) + main (tsc) |
| `npx electron-builder --win portable` | 构建免安装便携版 |
| `npx electron . --dev` | 在打包后强制开发模式 (loadURL) |
| `npx electron . --minimized` | 启动后最小化到任务栏 |
| `python -m pytest tests/ -x -q` | 运行 Python 测试 (~2550) |
| `npx vitest run` | 运行 TypeScript 前端测试 (~16 files) |
| `python python/mcp/server.py` | 启动 Python MCP 服务器 (stdio JSON-RPC) |
| `npm run ai:mcp` | 启动 TypeScript MCP 服务器 (5 tools, stdio) |
| `python tools/dsl_to_flow.py < file.dsl` | DSL 转 FlowScript |
| `curl -X POST http://127.0.0.1:7880/api/ping` | 检查 HTTP API 桥接 |

## 目录结构

```
├── src/
│   ├── main/                  # Electron 主进程
│   │   ├── main.ts            #   ~1590行: 窗口生命周期, IPC 30+ handlers, Agent进程管理, 菜单
│   │   ├── api.ts             #   HTTP API 服务器 :7880 (55+ 端点 + /mcp)
│   │   ├── engine.ts          #   Python 引擎桥接 (spawn/stop/pause/resume)
│   │   ├── env.ts             #   isDev / getBaseDir 环境配置
│   │   ├── state.ts           #   全局状态管理 + agent token 系统
│   │   ├── preload.ts         #   contextBridge (40+ API 暴露)
│   │   ├── mcp-handler.ts     #   MCP 协议处理器
│   │   ├── schedulerBridge.ts #   APScheduler 守护进程桥接
│   │   ├── cronMatcher.ts     #   Cron 表达式匹配器
│   │   └── featureCallBridge.ts # 转发 @feature 调用到 Python
│   └── renderer/              # React 前端 (Vite)
│       ├── App.tsx            #   根组件, 布局, 快捷键, HTN 注册
│       ├── store/             #   Zustand + immer 状态管理 (index.ts, executor.ts)
│       ├── types/             #   FlowNode, FlowEdge, NodeParams, FlowScript, AgentRunRecord
│       ├── components/
│       │   ├── Canvas/        #   FlowCanvas, FlowNode, EdgeLayer, CanvasControls
│       │   ├── PropertyPanel/ #   参数编辑器 (65 种节点 x params/*.tsx)
│       │   ├── NodeLibrary/   #   节点库面板
│       │   └── common/        #   TopBar, LogPanel, ToolCallPanel, TriggerBar,
│       │                      #   TriggerConfigPanel, ParamPromptDialog, MacroPanel,
│       │                      #   ScriptBrowser, TemplateSelector, TemplateBrowser,
│       │                      #   ScheduleDialog, PlanVisualizer,
│       │                      #   UnifiedSearchDialog, LearnedTargetPanel,
│       │                      #   TemplateMarketDialog
│       └── utils/             #   nodeDefinitions, pseudoParser, featureRegistry,
│                               #   semanticMatcher, htnRegistry, htnPlanner,
│                               #   promptBuilder, textGenerator, scriptIngestor,
│                               #   macroConverter, unifiedSearch, llmSearcher,
│                               #   quickScheduleParser, templateMatcher,
│                               #   templateRegistry, itemMarker, constants
├── python/
│   ├── agent/                 # AI Agent 执行系统 (~18 模块)
│   │   ├── __main__.py        #   IPC 入口 (stdin/stdout JSON 协议, 5命令)
│   │   ├── agent.py           #   LingdongAgent 统一入口 (5 执行模式)
│   │   ├── config.py          #   AgentConfig dataclass (~30 参数, resolve_auto)
│   │   ├── meta_actions.py    #   26 元动作 + 注册表 + 提示词生成
│   │   ├── supervisor.py      #   监督执行层 (窗口焦点/OCR/重试/SoM/对话框)
│   │   ├── planner.py         #   Plan DAG 分解 + PlanExecutor (fallback 链)
│   │   ├── tool_path_selector.py # 工具访问模式分类 (DIRECT->BROWSER->UIA->OCR->SoM)
│   │   ├── memory.py          #   Agent 记忆 (短期 + 长期)
│   │   ├── reflection.py      #   自我反思与学习
│   │   ├── local_vision_engine.py # 本地视觉引擎 (YOLO/MobileNet/RapidOCR)
│   │   ├── experience_db.py   #   经验数据库 (SHA256 签名索引)
│   │   ├── visual_locator.py  #   语义目标 -> 屏幕坐标定位器 (OpenCV/VLM)
│   │   ├── dialog_handler.py  #   Windows 对话框检测/关闭 (win32 API)
│   │   ├── menu_handler.py    #   Windows 菜单操作
│   │   ├── mimo_agent.py      #   MIMO 纯文本模型交互引擎
│   │   ├── omniparser_client.py   # OmniParser FastAPI 客户端
│   │   └── state_persistence.py   # 运行历史持久化
│   ├── engine/                # Python 自动化引擎
│   │   ├── executor.py        #   DSL 执行器 (核心)
│   │   ├── dsl_parser.py      #   DSL 解析器 (190 关键字)
│   │   ├── dsl_flow_converter.py # DSL<->FlowScript 双向转换
│   │   ├── engine.py          #   stdin/stdout JSON 协议引擎入口
│   │   ├── condition_evaluator.py # 条件求值
│   │   ├── crystallizer.py    #   流程图->DSL 结晶化
│   │   ├── triggers.py        #   触发器引擎 (6 种 watcher)
│   │   ├── trigger_bridge.py  #   触发器前端桥接
│   │   ├── scheduler.py       #   定时调度
│   │   ├── scheduler_daemon.py #   APScheduler 独立守护进程
│   │   ├── optimizer.py       #   流程优化
│   │   ├── healing.py         #   自愈选择器
│   │   ├── nlp_schedule_parser.py # NL 自然语言调度解析
│   │   ├── unified_entry.py   #   统一引擎入口
│   │   ├── store.py           #   流程持久化存储
│   │   ├── robot_runner.py    #   Robot Framework 集成执行器
│   │   └── robot_converter.py #   Robot Framework <-> DSL 转换
│   ├── providers/             # LLM 提供商
│   │   ├── base.py            #   基础 LLM 客户端
│   │   ├── deepseek.py        #   DeepSeek API 客户端
│   │   ├── openai.py          #   OpenAI 兼容客户端
│   │   └── vlm.py             #   视觉语言模型客户端
│   ├── features/              # @feature 注册表 (49 文件, 43 模块)
│   │   ├── registry.py        #   FeatureRegistry 单例, @feature 装饰器
│   │   ├── _all.py            #   导入所有 feature 模块 (43 模块 lazy load)
│   │   ├── action_features.py, perception_features.py, system_features.py,
│   │   │   ai_plan_features.py, ai_debug_features.py, flow_features.py,
│   │   │   macro_features.py, visual_macro_features.py, camera_features.py,
│   │   │   browser_features.py, web_features.py, a11y_features.py,
│   │   │   dialog_features.py, menu_features.py, clipboard_features.py,
│   │   │   variable_features.py, file_features.py, network_features.py,
│   │   │   image_click_features.py, image_search_features.py, color_features.py,
│   │   │   timer_features.py, notification_features.py, window_features.py,
│   │   │   system_control_features.py, input_features.py, multimedia_features.py,
│   │   │   software_features.py, canvas_features.py, claude_code_features.py,
│   │   │   dsl_features.py, fusion_features.py, healing_features.py,
│   │   │   scheduler_features.py, trigger_features.py, plugin_features.py,
│   │   │   filedialog_features.py, vlm_features.py, list_features.py,
│   │   │   call_feature.py, state.py, persistence_features.py,
│   │   │   aggregate_features.py, test_flow_features.py
│   │   │   ... and more
│   ├── perception/            # 视觉感知
│   │   ├── ocr.py             #   EasyOCR / RapidOCR 文字识别
│   │   ├── vision.py          #   图像匹配/模板匹配
│   │   ├── fusion.py          #   多模态视觉融合
│   │   └── app_adapter.py     #   应用适配器
│   ├── interaction/           # 交互工具 (wheel_selector, region_picker, actions)
│   ├── mcp/server.py          # Python MCP 服务器 (stdio JSON-RPC 2.0)
│   ├── macros/                # 宏录制保存目录 (.json 事件文件)
│   └── experience/            # 视觉定位经验库 (db.json + templates/*.png)
├── tools/                     # 工具脚本 (dsl_to_flow.py 等)
├── templates/                 # 工作流模板 (.flow / .dsl 文件)
├── tests/                     # Python 测试 (~2550 tests, 15 文件)
├── src/renderer/test/         # TypeScript 测试 (~16 文件, vitest)
├── omni_parser_repo/          # OmniParser V2 仓库 (microsoft/OmniParser-v2.0)
└── .claude/                   # Claude Code 配置 (MCP 自动注册)
```

## HTTP API 桥接 (Claude Code 远程控制)

Electron 主进程内置 HTTP 服务器 `http://127.0.0.1:7880`（仅 localhost，可被 `API_PORT` 环境变量覆盖），Claude Code 可通过 curl 实时控制前端。

| 端点 | 用途 | 关键参数 |
|---|---|---|
| `POST /api/ping` | 健康检查 | -- |
| `POST /api/load-script` | 加载完整流程图 | `{ script: FlowScript }` |
| `POST /api/run-dsl` | DSL 文本->流程图 | `{ dsl: string }` |
| `POST /api/add-node` | 添加节点 | `{ type, x, y, params? }` |
| `POST /api/update-node` | 更新节点属性 | `{ id, updates }` |
| `POST /api/update-node-params` | 更新节点参数 | `{ id, params }` |
| `POST /api/delete-nodes` | 删除节点 | `{ ids: string[] }` |
| `POST /api/add-edge` | 添加连线 | `{ from_node, to_node, from_port?, to_port? }` |
| `POST /api/delete-edges` | 删除连线 | `{ ids: string[] }` |
| `POST /api/auto-layout` | 自动排列节点 | -- |
| `POST /api/generate-dsl` | 自然语言->DSL (调用LLM) | `{ description, apiKey? }` |
| `POST /api/create-flow` | DSL/描述->流程图 (含参数检查) | `{ description?, dsl?, apiKey? }` |
| `POST /api/check-params` | 检查节点缺失参数 | `{ nodes[] }` |
| `POST /api/new-script` | 新建空白脚本 | -- |
| `POST /api/set-view-mode` | 切换视图 | `{ mode: 'canvas'|'pseudocode'|'settings' }` |
| `POST /api/fit-screen` | 自适应缩放 | -- |
| `POST /api/get-state` | 读取当前状态 | -- |
| `POST /api/engine/start` | 启动脚本执行引擎 | `{ script }` |
| `POST /api/engine/pause` | 暂停引擎 | -- |
| `POST /api/engine/resume` | 继续引擎 | -- |
| `POST /api/macro/start-recording` | 开始宏录制 (F6 热键) | `{ name?, include_mouse?, include_keys? }` |
| `POST /api/macro/stop-recording` | 停止录制并保存 | -- |
| `POST /api/macro/list` | 列出已录制宏 | -- |
| `POST /api/macro/play` | 播放宏 | `{ name }` |
| `POST /api/macro/delete` | 删除宏 | `{ name }` |
| `POST /api/macro/get-events` | 读取宏原始事件 | `{ name }` |
| `POST /api/macro/recording-status` | 录制器状态 | -- |
| `POST /api/ai/generate` | 调用 LLM (DeepSeek/OpenAI) | `{ apiKey, messages[], apiBase?, model? }` |
| `POST /api/screenshot/capture` | 全屏截图 (交互框选) | -- |
| `POST /api/screenshot/select-region` | 框选屏幕区域 | -- |
| `POST /api/ocr/region` | OCR 识别区域文字 | `{ x, y, w, h }` |
| `POST /api/wheel-select` | 滚轮选择器 | `{ options[], values?, title?, message? }` |
| `POST /api/undo` | 撤销 | -- |
| `POST /api/redo` | 重做 | -- |
| `POST /api/save-script` | 保存脚本 | -- |
| `POST /api/set-script-name` | 重命名脚本 | `{ name }` |
| `POST /api/save-template` | 保存为模板 | `{ name, description? }` |
| `POST /api/list-templates` | 列出模板 | -- |
| `POST /api/set-ui` | 更新 UI 状态 (含 show_ai_dialog) | `{ updates: { dark_mode?, show_grid?, show_ai_dialog?, ... } }` |
| `POST /api/command` | 批量执行命令 | `{ commands: [{ action, payload?, delay? }] }` |
| `POST /api/copy-selected` | 复制选中节点 | -- |
| `POST /api/cut-selected` | 剪切选中节点 | -- |
| `POST /api/paste` | 粘贴 | -- |
| `POST /api/select-all` | 全选节点 | -- |
| `POST /api/clear-selection` | 清空选择 | -- |
| `POST /api/duplicate-nodes` | 复制节点 | `{ ids: string[] }` |
| `POST /api/agent-start` | 启动 AI Agent | `{ goal, config?, supervise? }` |
| `POST /api/features/list` | 列出所有可用 @feature 工具 | -- |
| `POST /api/features/reload` | 重新加载 @feature 注册表 | -- |
| `POST /api/features/call` | 调用任意 @feature 工具 | `{ name, params }` |
| `POST /api/search-templates` | 语义搜索模板 | `{ query, maxResults? }` |
| `POST /api/execute-js` | 在前端执行 JS 代码 | `{ code }` |
| `POST /api/crystallize` | 执行记录结晶为模板 | `{ recordId }` |
| `POST /mcp` | MCP over HTTP 端点 | 标准 MCP JSON-RPC |

## MCP 协议入口

提供 **三个 MCP 入口**：

### 1. Python MCP (主)

启动: `python python/mcp/server.py`

通过 `@feature` 注册表动态生成 MCP 工具（过滤 hidden + deprecated），覆盖 7 分类：

| 分类 | 工具数 | 功能 |
|---|---|---|
| 交互动作 | ~68 | click, type, swipe, hotkey, scroll, browser, filedialog, menu... |
| 感知识别 | ~29 | find_image, OCR, color_detect, progress, A11y, fusion, camera... |
| 控制流 | ~25 | wait, loop, condition, timer, software control, aggregate... |
| AI 规划 | ~9 | plan, execute_plan, debug, VLM analyze, screen_understanding... |
| 宏指令 | ~10 | record, playback, list, delete, convert_to_flow, visual_macro... |
| 系统功能 | ~74 | window, process, clipboard, file, wifi, volume, display, scheduler... |
| 调试诊断 | ~32 | test, validate, system_info, feature introspection, healing, trigger... |

DSL 关键字: 190 个（自动从 @feature 注册表生成，含所有控制流关键字）
Agent 工具集: 26 元动作（高层语义） + @feature 工具（底层能力），LLM 提示词中同时暴露
Agent 双路径执行: 元动作 -> `meta_execute()`（参数校验 + 类型强制 + 直接调用），其他 -> `supervisor.execute()`（完整监督流水线）

关键工具示例：
- `dsl_to_flow(dsl)` -- DSL 文本转 FlowScript JSON
- `flow_to_dsl(script)` -- 流程图转 DSL 文本
- `run_dsl(dsl)` -- 本地执行 DSL（免 token, 免后端）
- `create_flow_from_dsl(dsl, name)` -- DSL -> 保存 .flow -> 返回 JSON

### 2. TypeScript MCP (桥接)

启动: `npm run ai:mcp` (`node dist/ai-interface/index.js --mode mcp`)

5 个工具用于前端集成和文件管理：

| 工具 | 用途 |
|---|---|
| `run_flow` | 执行 .flow 文件 |
| `list_flows` | 列出模板 |
| `create_flow` | 创建流程 |
| `ask_ai` | AI 问答 |
| `screen_capture` | 截屏 |

### 3. HTTP MCP 端点

`POST http://127.0.0.1:7880/mcp` -- 标准 MCP JSON-RPC 协议，自动注册到 `~/.claude.json`（首次启动时）。

## @feature 注册表系统

`python/features/` 目录包含 **49 个 .py 文件**，其中 **43 个模块**通过 `_all.py` 的 `_FEATURE_MODULES` 列表注册。

### @feature 装饰器

```python
from features.registry import feature

@feature(
    name="my_tool",
    display_name="我的工具",
    category=FeatureCategory.ACTION,
    params=[
        ParamSpec("target", "string", description="目标", required=True),
    ],
    dsl_keyword="MY_TOOL",
)
def my_tool(target: str) -> dict:
    """工具实现"""
    return {"success": True}
```

每个 `@feature` 自动生成：
- **MCP 工具** (`lobster_{name}`, JSON Schema)
- **CLI 命令** (`{name.replace("_","-")}`, argparse)
- **HTTP 端点** (`/api/feature/{name}`)
- **DSL 关键字** (大写 SNAKE_CASE)
- **测试用例** (关联 TestCase, 可通过 `registry.run_tests()` 批量执行)

### 懒加载机制

`_all.py` 通过 `registry.set_lazy_modules()` 存储模块名，首次访问 `registry.all()` 或 `registry.get()` 时调用 `importlib.import_module()` 实际加载，避免启动时 2-8s 的导入开销。

### 插件系统

外部 `.py` 文件或目录可通过 `registry.load_plugin()` 在运行时动态加载，存储在 `self._plugins` 字典中。也支持自愈功能模块（`healing_features.py`），自动修复常见问题。

### 自动协议导出

| 方法 | 用途 |
|---|---|
| `export_mcp_tools()` | MCP JSON Schema 列表 |
| `export_openapi()` | OpenAPI 3.0 规范 |
| `export_claude_context()` | Claude Code Markdown 引用 |
| `export_dsl_reference()` | DSL 语法参考 |
| `execute(name, **params)` | 统一执行入口 (类型转换+错误处理) |

## AI Agent 系统

AI Agent 是灵动脚本的核心智能层，支持 **5 种执行模式** + **双离线模式** + **对话模式** + **4 种视觉策略**。

### 双模式入口

```
AgentView 前端入口 (AgentView.tsx)
  +-- AI Agent 模式（在线）: 输入自然语言 -> LLM 生成计划 -> 执行
  |   +-- 含监督执行: 窗口焦点/OCR验证/失败恢复
  |   +-- 含流程图模式: 生成可视化流程图后逐步骤执行
  +-- 离线模式（离线执行）:
       +-- 直接运行当前画布上的流程图
       +-- 无需 LLM / API Key
       +-- 无网络也可执行
       +-- 通过 engine:start IPC 直接调用 Python 引擎
```

**离线模式**: 前端切换开关，将当前画布上的流程图直接发送到 Python 引擎执行（`engine:start`），完全不需要 AI / API Key。适用于调试和无需 LLM 的场景。

### 5 种 Agent 执行模式

| 模式 | 入口方法 | 说明 |
|---|---|---|
| **text_protocol** | `run_text_protocol()` | 文本协议循环: LLM -> 解析"工具:/参数:" -> 执行 -> 截图反馈。适用 MIMO 等纯文本模型。支持 chat_mode + visual_strategy |
| **langgraph** | `run_langgraph()` | LangGraph `create_react_agent`，函数调用模型 (DeepSeek/GPT/Claude)。每个 @feature 工具包装为 LangChain Tool |
| **planned** | `run_planned()` | Planner 生成 DAG -> PlanExecutor 拓扑序执行（带 fallback 链）。仅需 1 次 LLM 调用，后续离线执行 |
| **computer_use** | `_computer_use_loop()` | Vision-only 循环: 截图 -> 模型分析 -> 动作 -> 新截图。ACTION:/PARAMS:/思考: 格式 |
| **direct** | `_run_direct()` | 一键生成流程图: 调用 `generate_flow_script` 返回结果，不下场执行 |

统一入口 `_unified_run()` 通过 `AgentConfig` 自动路由到对应模式。

### 对话模式 (Chat Mode)

开关 `chat_mode: bool = False`，开启后 Agent 可"边干活边聊天"：

```
Agent 执行循环 (run_text_protocol)
  +-- 每轮开始时: _process_chat_messages()
  |   +-- 从 guidance_queue 读取用户消息
  |   +-- 注入 LLM 消息历史: "[用户消息] ..."
  |   +-- LLM 可自然聊天回复 / 调整策略 / 接受新任务
  +-- LLM 回复解析:
  |   +-- "工具:" -> 正常执行
  |   +-- 纯文本 (>20字) -> _emit_chat_response() -> 前端显示
  +-- 前端:
      +-- 输入框切换为"输入消息与 AI 对话..."
      +-- 对话历史气泡列表
      +-- 发送 -> agent:chat IPC -> _cmd_chat() -> guidance_queue
```

### AgentConfig 配置系统

`python/agent/config.py` -- 统一配置 dataclass，~30 参数通过 IPC 序列化传输。

**核心策略 (字符串, 自动解析 auto):**
- `llm_protocol`: auto/text/function_calling/computer_use
- `vision_mode`: auto/direct/som/omniparser
- `planning`: auto/pre_plan/per_step
- `screen_context`: auto/minimal/full
- `on_error`: retry/replan/skip/stop
- `visual_strategy`: auto/cli_path/blind_guess/ocr_llm/som

**开关 (布尔值):**
- `verify`, `fast_mode`, `image_dedup`, `window_aware_ocr`, `uia_pre_focus`
- `scripted_mode`, `auto_learn`, `som_enabled`, `flow_chart_mode`, `chat_mode`

**安全限制:**
- `max_steps` (20), `max_consecutive_failures` (5), `min_success_rate` (0.1)

**LLM 参数:**
- `temperature` (0.1), `max_tokens` (4096), `llm_retries` (2)
- `screenshot_quality` (60), `sliding_window_size` (0=auto)

`resolve_auto()` 自动检测多模态能力并设置默认值：
- 多模态模型 -> text + direct + per_step + minimal
- 纯文本模型 -> function_calling + som + pre_plan + full

### 4 种视觉策略 (Visual Strategy)

通过在 AgentView 的 Perceive 节点选择，控制 Agent 如何"看"屏幕：

| 方案 | visual_strategy | 原理 | 状态 |
|---|---|---|---|
| **CLI/路径优先** | `cli_path` | 尝试 CLI 等价操作，SoM 兜底 | 可选 |
| **自主盲猜** | `blind_guess` | LLM 凭经验猜坐标（原始方式） | 可选 |
| **OCR->LLM->点击** | `ocr_llm` | OCR 截取所有文字 -> LLM 语义选择 -> 点坐标 | 可选 |
| **SoM 标号** | `som` | UIA+OCR->标号截图->LLM 选 #N->转坐标 | 默认关闭 |
| **自动** | `auto` | 多模态=直接截图, 纯文本=SoM | 默认 |

### 监督执行层 (Supervisor)

所有执行模式共享 `python/agent/supervisor.py`，每步执行流水线：

```
supervisor.execute(name, args)
  +-- 1. SoM 解引用 (#N -> 坐标)
  +-- 2. UIA 元素定位 (控件树匹配)
  +-- 3. 视觉定位兜底 (经验库->OpenCV->VLM)
  +-- 4. 窗口焦点安全 (自动聚焦目标窗口)
  +-- 5. 工具执行 (registry.execute / 元动作)
  +-- 6. 对话框自动处理 (win32 API)
  +-- 7. 屏幕稳定等待 (等待界面变化完成)
  +-- 8. OCR 验证 (可选, verify=true时)
```

元动作（26 个）走 `meta_execute()` 快速路径，绕过部分验证步骤。

### 自我反思与学习

`python/agent/reflection.py` -- Agent 在执行完成后自动反思：

1. **执行摘要** -- 汇总执行步骤、成功/失败
2. **错误分析** -- 识别失败模式和原因
3. **改进建议** -- 提出下次执行的优化策略
4. **经验存储** -- 成功定位的经验自动存入数据库

`python/agent/memory.py` -- 双轨记忆系统：
- **短期记忆** -- 当前执行会话的步骤历史和上下文
- **长期记忆** -- 跨会话的持久化经验，包括常见任务模式和成功策略

### Agent 记忆与反思 (Memory & Reflection)

Agent 在工作时会自动维护 **短期记忆**（当前会话的上下文和步骤历史），并在执行结束后或失败时触发 **自我反思**：

```
Reflection 流程:
  +-- _generate_reflection()
  |   +-- 汇总执行步骤 (step_complete 事件)
  |   +-- 分析失败原因 (工具执行错误 / 超时 / 验证失败)
  |   +-- 生成改进建议 (下次类似任务的优化策略)
  +-- _store_reflection()
  |   +-- 存入 memory.py 的长期记忆数据库
  +-- 下次执行时:
      +-- 检索相关长期记忆
      +-- 注入系统提示词作为参考
```

### 工具访问路径选择器

Agent 按优先级选择最优执行路径：

| 优先级 | 模式 | 适用场景 |
|---|---|---|
| L0 | 直接 API | 文件操作、剪贴板、进程管理 |
| L1 | 浏览器 CDP | 浏览器内自动化 |
| L2 | Windows UIA | 窗口控件操作 |
| L3 | 视觉 OCR | 文字定位、屏幕文字读取 |
| L4 | SoM 标号 | 元素检测 + 编号标注 |
| L5 | 宏录制/回放 | 录制回放、视觉宏 |
| L6 | DSL 脚本 | 可复用流程图生成 |

### 前端 AgentView 配置界面

`src/renderer/components/AgentView.tsx` (~490 行) -- 流程图风格的 Agent 配置面板，8 个可折叠节点：

1. **Goal Node** -- 目标输入, 状态指示, 离线/流程图模式切换
2. **LLM Brain** -- API Key/Base/Model, 协议/规划策略, 多模态, 温度, 对话模式
3. **Plan Node** -- PlanVisualizer, 生成计划展示, 执行/重新生成
4. **Perceive Node** -- 视觉模式, SoM/OmniParser, 截图质量, 屏幕上下文, 视觉策略
5. **Act Node** -- 窗口感知OCR, UIA预聚焦, 脚本执行, 快速模式, 自动学习
6. **Verify Node** -- OCR 验证开关
7. **Error Handler** -- 失败策略 (重试/重规划/跳过/停止), 安全限制
8. **Exec Node** -- 日志, 聊天框 (对话模式), 指令输入

Tool Explorer 弹窗显示 26 元动作 + 全部 @feature 工具。

## Agent IPC 协议

AI Agent 子进程通过 **stdin/stdout JSON** 与 Electron 主进程通信。设计用于实时流式展示执行进度。

### IPC 命令 (Electron -> Agent, stdin)

| 命令 | 参数 | 用途 |
|---|---|---|
| `{"type": "start", "goal": "...", "config": {...}}` | config 含 agent_config 字典 | 启动 Agent (5 种模式自动路由) |
| `{"type": "stop"}` | -- | 请求优雅停止 |
| `{"type": "guidance", "text": "..."}` | text: 指令文本 | 注入用户指引 |
| `{"type": "chat", "text": "..."}` | text: 聊天文本 | 对话模式聊天消息 |
| `{"type": "status"}` | -- | 查询当前状态 |

### IPC 事件 (Agent -> Electron, stdout)

| 事件 | 关键字段 | 用途 |
|---|---|---|
| `agent_start` | goal, engine, model | Agent 启动 |
| `step_complete` | step_num, tool, result.success/error | 每步结果 (前端实时更新) |
| `agent_done` | status, total_steps, fail_reason | Agent 结束 |
| `log` | level, message | 日志 |
| `user_guidance_sent` | text | 用户指引确认 |
| `agent_chat_response` | text | AI 聊天回复 (对话模式) |
| `plan_state` | plan | 计划状态更新 |
| `memory_snapshot` | memory | 记忆快照 |
| `agent_reflection` | reflection | 自我反思内容 |
| `error` | message | 错误 |
| `status_report` | running, stopped | 状态报告 |

### 进程生命周期

```
main.ts startAgentProcess()
  +-- 杀死旧进程 (stop + timeout + kill)
  +-- spawn: python -u -m agent (stdin/stdout/stderr 管道)
  +-- 行缓存: StringDecoder UTF-8
  +-- 300ms 延迟后发送 start 命令
  +-- 标准输出: JSON -> agent-message IPC -> renderer
  +-- 非JSON: -> agent-log IPC -> renderer
  +-- 安全超时: 300s 自动 kill
  +-- Token 系统: nextAgentToken() 防过期回调
```

## 宏录制功能

内置商业级宏录制，支持 F6 全局热键启停、鼠标智能过滤（<10px 跳过）、一键转换为流程图。

### 录制方式

| 方式 | 操作 |
|---|---|
| **F6 热键** | 按 F6 开始/停止录制（独立进程，不阻塞 UI） |
| **TopBar 按钮** | 点击录制按钮（红点动画），再点或按 F6 停止 |
| **MCP 工具** | `lobster_macro_start_recording` / `lobster_macro_stop_recording` |
| **HTTP API** | `POST /api/macro/start-recording` / `POST /api/macro/stop-recording` |

### @feature MCP 工具

| 工具 | 文件 | 用途 |
|---|---|---|
| `lobster_macro_start_recording` | macro_features.py | pynput 独立录制进程 (F6 热键) |
| `lobster_macro_stop_recording` | macro_features.py | 停止录制, 返回事件统计 |
| `lobster_macro_list` | macro_features.py | 列出所有宏 |
| `lobster_macro_playback` | macro_features.py | 回放 (speed/loop 参数) |
| `lobster_macro_delete` | macro_features.py | 删除宏文件 |
| `lobster_macro_get_events` | macro_features.py | 读取原始事件数据 |
| `lobster_macro_convert_to_flow` | macro_features.py | 宏事件 -> FlowScript 流程图 |
| `lobster_macro_start_visual_recording` | visual_macro_features.py | 视觉宏录制 (点击时截全屏) |
| `lobster_macro_stop_visual_recording` | visual_macro_features.py | 停止视觉宏录制 |
| `lobster_macro_extract_visual_templates` | visual_macro_features.py | 从点击坐标提取按钮小图 |

### 视觉宏录制 (Visual Macro Recording)

自动从录制操作中提取按钮小图，用于后续离线 OpenCV 模板匹配定位，无需视觉模型。

**设计思路：** 普通宏录制只记坐标，界面变化后坐标无效。视觉宏录制在每次点击时截全屏，录制结束后从截图中智能检测按钮边界，裁剪小图存入经验库，后续通过 OpenCV 模板匹配定位，与界面布局无关。

**三阶段流程:**

| 阶段 | 操作 | 耗时 | 说明 |
|---|---|---|---|
| 录制 | `macro_start_visual_recording` | ~10ms/click | pynput 监听 + PIL/mss 截全屏存缓存目录 |
| 停止 | `macro_stop_visual_recording` | 即时 | 保存事件 JSON（含截图文件引用路径） |
| 提取 | `macro_extract_visual_templates` | ~200ms/click | **手动触发**，遍历 click -> 智能裁图 -> OCR -> 存经验库 |

**按钮边界检测策略**（`_smart_crop_around_click`，三层递进）:

1. **floodFill 连通域**（容差 15->30->50）-- 从点击点做种子填充，取连通域的 bounding rect
2. **Canny + findContours 兜底** -- 边缘检测，找包含点击点的最小轮廓
3. **固定 40x40 裁剪兜底** -- 前两者均失败时以点击点居中裁剪

**OCR 自动命名:** 裁剪后调用 EasyOCR 识别小图中的文字，用识别结果作为 `target_text` 存入经验库。

**关键文件:**
- `python/features/visual_macro_features.py` -- 3 个 @feature 工具（录制/停止/提取）
- `python/agent/visual_locator.py` -- 定位器，查经验库 + OpenCV 匹配
- `python/agent/experience_db.py` -- 经验数据库，JSON 索引 + PNG 模板
- `python/experience/templates/` -- 裁剪小图存储目录

## 本地视觉引擎

让纯文本模型也能"看"屏幕，全部本地 GPU 完成，零 API 成本：

```
截图 -> YOLOv8(144ms)检测图标 + MobileNet(10ms)分类 + RapidOCR(~3s)读文字
      -> SoM 标号 (#1: "我的手机" 坐标 913,263)
      -> 纯文本 LLM 直接选 #N 或文字点击
```

### 技术栈

| 组件 | 技术 | 性能 | 用途 |
|---|---|---|---|
| **YOLOv8** | ONNX DirectML | ~144ms (AMD 780M) | UI 图标按钮检测 |
| **MobileNet V3** | ONNX | ~10ms/个 | 图标分类 (envelope, wall_clock...) |
| **RapidOCR** | ONNX | ~3s 全屏 | 文字检测 + 识别 + 精确坐标 |
| **OpenCV** | matchTemplate | ~50ms | 模板匹配，经验库检索 |

`python/agent/local_vision_engine.py` -- 统一入口，管理模型加载、GPU 内存、推理调度。

## 视觉定位 + 经验复用学习系统

Agent 监督执行时通过 **视觉定位** 模块将语义目标（如"搜索按钮"）转为屏幕坐标，并使用 **经验数据库** 实现"越用越快"。

### 架构

```
_apply_visual_locator(name, args)
  +-- 提取 target_text（如 "搜索按钮"）
  +-- 截取全屏截图
  +-- visual_locator.locate()
  |    +-- 1. ExperienceDB -> 有模板?
  |    |   +-- OpenCV matchTemplate -> 命中 -> 返回坐标
  |    +-- 2. VLM 兜底 (多模态开启时)
  |    |   +-- VLM API 分析截图 -> 返回坐标 -> 裁剪小图 -> 存入 ExperienceDB
  |    +-- 3. 均失败 -> 返回错误，走正常执行流程
  +-- 更新 args.x / args.y -> 监督执行使用坐标
```

### 控制开关

| 开关 | localStorage key | 效果 |
|---|---|---|
| **多模态 (视觉模型)** | `lingdong-llm-multimodal` | VLM API 分析截图定位元素 |
| **自动学习** | `lingdong-auto-learn` | 成功自动截图记录, 后续 OpenCV 匹配 |

两个开关同时开启才启用 VLM 兜底；仅 auto-learn 开启时只使用 ExperienceDB + OpenCV。

### 经验数据存储

```
python/experience/
+-- db.json            # 经验索引（SHA256 签名->路径/坐标/置信度）
+-- templates/         # 裁剪小图（PNG）
```

## Set-of-Mark (SoM) 标号系统

Agent 在执行时对屏幕元素进行 **蓝色框 + #N 编号** 标注，纯文本模型直接用 `#3` 指定目标。

### 双轨 SoM 引擎

| 引擎 | 技术栈 | 定位精度 | 速度 | 状态 |
|---|---|---|---|---|
| **OmniParser** | YOLOv8 图标检测 + Florence-2 标题 + EasyOCR | 像素级 | ~3-8s (CPU) | 需 transformers 4.x |
| **UIA + OCR 兜底** | Windows UIA 控件树 + EasyOCR 文字检测 | 像素级 | ~1-3s | **默认工作路径** |

### 执行流程

```
_apply_som(b64_img)
  +-- OmniParser 优先
  |    +-- 启动 FastAPI 子进程 (lazy, 仅首次)
  |    +-- POST /parse/ -> 返回 {image_b64, label_map, count}
  |    +-- 失败 -> 静默降级到 UIA+OCR
  +-- UIA + OCR 兜底 (always available)
       +-- a11y_get_tree(depth=3) -> UIA 控件列表
       +-- ocr_extract_all() -> OCR 文字框
       +-- 去重合并 -> 编号排序 (从上到下, 从左到右)
       +-- OpenCV 画蓝色框 + #N 编号
```

### 关键文件

- `python/agent/agent.py` -- `_apply_som()` 核心方法
- `python/agent/omniparser_client.py` -- OmniParser FastAPI 客户端（子进程管理 + parse API）
- `omni_parser_repo/` -- OmniParser V2 仓库
- `omni_parser_repo/weights/` -- 模型权重（icon_detect 40MB + icon_caption 1GB）

## 摄像头节点

支持摄像头画面捕获、录像和运动检测，作为自动化流程中的视觉输入源。

### @feature 工具（5 个）

| 工具名 | 用途 |
|---|---|
| `camera_list` | 列出所有可用摄像头设备（索引 0-9） |
| `camera_capture` | 捕获一帧画面，支持保存文件或 base64 返回 |
| `camera_record_start` | 开始录制 AVI/MP4，支持限时录制 |
| `camera_record_stop` | 停止正在进行的录制，返回文件大小和时长 |
| `camera_detect_motion` | 帧差法运动检测，支持保存触发画面 |

- `python/features/camera_features.py` -- @feature 注册的摄像头功能（5 个）

## 定时调度与触发器

| 组件 | 位置 | 用途 |
|---|---|---|
| **6 种触发源** | `engine/triggers.py` | cron 定时器 / 固定间隔 / 文件监控 / 进程监控 / 窗口监控 / 剪贴板监控 / 网络状态 |
| **TriggerBar** | `renderer/components/common/TriggerBar.tsx` | 顶部栏显示所有活跃触发器状态 |
| **条件守卫** | `engine/triggers.py` | 每个触发器可配置执行条件（仅特定窗口/时间段生效） |
| **APScheduler 守护** | `engine/scheduler_daemon.py` | 独立 Python 进程，崩溃自动重启 |
| **NLP 调度解析** | `engine/nlp_schedule_parser.py` | 自然语言描述 -> cron 表达式 |
| **TriggerConfigPanel** | `renderer/components/common/TriggerConfigPanel.tsx` | 触发器配置 UI 面板 |

ECA 模型: Event-Condition-Action 触发架构。

## 模板系统

| 功能 | 位置 | 说明 |
|---|---|---|
| **30+ 预设模板** | `templates/` | 涵盖常见自动化场景 |
| **结晶化** | `engine/crystallizer.py` | 每次 DSL 执行成功后自动生成可复用 `.flow` 模板 |
| **语义搜索** | `renderer/utils/semanticMatcher.ts` | 按自然语言搜索模板（HTTP API） |
| **模板市场** | `TemplateMarketDialog.tsx` | 浏览、导入、管理模板库 |
| **模板浏览器** | `TemplateBrowser.tsx` | 分页浏览所有模板 |
| **分层索引** | `skill_tree.json` | category/pattern 维护模板目录 |

### 模板分类

- **Preset (内置)** -- 随应用发布的预置模板
- **Custom (自定义)** -- 用户保存的自定义模板
- **Crystallized (结晶化)** -- 从执行历史自动生成的模板

## 工作流

### 创建自动化流程
1. 在画布拖拽节点编排流程
2. 节点类型: 65 种（基础交互/图像识别/控制流/系统操作/高级工具/聚合类型）
3. 属性面板编辑参数 (支持区域拾取、策略选择)
4. 文本视图可切换中文伪代码 / 英文 DSL 模式
5. 保存为 .flow 文件, 或导出 DSL

### 调试执行
1. 点击 "运行" 或按 **F5** 启动（前端快捷键）
2. 引擎逐节点执行, 高亮当前节点
3. 日志面板显示执行详情
4. 支持暂停/继续/停止
5. 节点标注成功/失败/跳过状态

**全局 F9 热键**: 即使在应用未聚焦时，F9 也可启停 Python 引擎（主进程全局快捷键）。

### AI 辅助
1. 点击 "AI 助手" 用自然语言描述需求
2. AI 生成 DSL -> 自动转为流程图
3. 或通过 Claude Code 调用 MCP 工具生成
4. 模板文件在 `templates/` 目录

### AI Agent 监督执行
1. 点击 "AI Agent" 输入目标（自然语言）
2. Agent 自动调用 LLM 生成流程图脚本（可视化展示）
3. Agent 进入监督执行模式：逐步骤执行，每步包含：
   - **窗口焦点安全** -- 自动检测并聚焦目标窗口
   - **执行动作** -- 通过 feature registry 执行点击/输入/快捷键等
   - **屏幕稳定等待** -- 等待操作后的界面变化完成
   - **OCR 验证** -- 自动识别屏幕文字验证操作结果
   - **失败恢复** -- 自动重试 / 替换方案 / 跳过 / 终止
4. 前端流程图实时高亮当前执行节点，显示成功/失败状态
5. 执行完成或失败后汇总报告

### 离线执行模式
1. 切换离线模式（AgentView 顶部的 WiFi/离线按钮）
2. 直接在画布上编辑流程图
3. 点击"离线执行"按钮
4. 引擎直接运行当前流程图，无 LLM 调用，无需 API Key
5. 适用于调试、无需 AI 的场景、无网络环境

## 节点类型速查 (65 种)

### 基础交互

| 类型 | 用途 | 主要参数 |
|---|---|---|
| click | 点击坐标 | x, y, method, offsetX, offsetY, delay_after |
| input_text | 输入文字 | text, click_before, clear_before, delay_after |
| keyboard | 快捷键 | keys[], modifiers[], delay_after |
| swipe | 滑动 | start_x, start_y, end_x, end_y, duration, delay_after |
| mouse_operation | 鼠标综合（点击/滚轮/悬停/移动） | sub_operation, x, y, method |
| click_action | 点击综合（坐标/图片/文字） | mode, method, delay_after |

### 图像与识别

| 类型 | 用途 | 主要参数 |
|---|---|---|
| image_click | 图片识别点击 | targets[], similarity, method, timeout, multi_scale, match_all |
| click_text | OCR 文字点击 | text, similarity, method, timeout |
| screenshot | 截图 | region, save_path, format |
| popup_close | 弹窗关闭 | strategies[], timeout, delay_after |
| color_check | 颜色检测 | x, y, w, h, expected_color, tolerance |
| camera_capture | 摄像头拍照 | camera_id, output_path, format |
| camera_record | 摄像头录像 | camera_id, output_path, fps, duration |

### 控制流

| 类型 | 用途 | 主要参数 |
|---|---|---|
| condition | 条件分支 | condition_type (image_exists/window_exists/variable_equals/color_match/scene_match/always_true), similarity |
| loop | 循环 | loop_type (count/condition/infinite), count |
| wait | 等待 | wait_type (time/image/window), duration, timeout |
| sub_script | 子流程 | script_id, script_name |
| scene_condition | 场景识别 | scene_name, similarity |

### 设备监控

| 类型 | 用途 | 主要参数 |
|---|---|---|
| progress_wait | 等待进度条 | wait_type, region, timeout, poll_interval |
| region_wait | 等待区域稳定 | region_type, x, y, w, h, timeout, poll_interval |

### 变量与数据

| 类型 | 用途 | 主要参数 |
|---|---|---|
| variable_set | 设置变量 | name, value |
| variable_get | 读取变量 | name, value |

### 系统操作

| 类型 | 用途 | 主要参数 |
|---|---|---|
| shutdown | 关机 | delay_seconds, confirm |
| reboot | 重启 | delay_seconds, confirm |
| logoff | 注销 | confirm |
| hibernate | 休眠 | delay_seconds, confirm |
| lock_workstation | 锁定工作站 | confirm |
| volume_set | 设置音量 | level |
| volume_mute | 静音控制 | mute (true/false/toggle) |
| launch_program | 启动程序 | target, args, wait |
| kill_process | 终止进程 | name, force |
| wait_process | 等待进程 | name, wait_for (start/exit), timeout |
| focus_window | 激活窗口 | title, exact |
| set_wallpaper | 设置壁纸 | path, style |
| display_off | 关闭显示器 | confirm |
| system_sleep | 睡眠 | confirm |
| show_desktop | 显示桌面 | -- |
| battery_status | 电池状态 | -- |
| empty_recycle_bin | 清空回收站 | confirm |
| display_brightness | 亮度 | level |
| clipboard_set | 剪贴板写入 | text |
| clipboard_get | 剪贴板读取 | -- |
| window_control | 窗口操作 | operation, title, width, height |

### 高级工具

| 类型 | 用途 | 主要参数 |
|---|---|---|
| web_browser | 浏览器操作 (Playwright) | action, url |
| dialog_action | 对话框操作 | action, timeout, button |
| a11y_action | 无障碍操作 (UIA) | action, name, role |
| wifi_action | WiFi 操作 | action, ssid, password |
| tool_call | 任意 @feature 工具 | feature_name, params |

### 聚合类型（多合一）

| 类型 | 包含子操作 | 用途 |
|---|---|---|
| system_power | shutdown/reboot/logoff/hibernate/lock_workstation/system_sleep/display_off | 系统电源统一入口 |
| audio_action | volume_set/volume_mute | 音频控制统一入口 |
| desktop_action | show_desktop/set_wallpaper/empty_recycle_bin | 桌面操作统一入口 |
| clipboard_action | set/get/get_image/get_files/clear | 剪贴板操作统一入口 |
| process_action | launch/kill/wait_start/wait_exit/focus | 进程操作统一入口 |

### 特殊节点

| 类型 | 用途 |
|---|---|
| start | 脚本入口点 |
| end | 脚本终止点 |

## DSL 脚本语言

内置 DSL（领域特定语言），190 个关键字，支持文本编辑模式：

```
CLICK 500 400
INPUT_TEXT 300 500 "用户名"
HOTKEY Tab
INPUT_TEXT 300 500 "密码123"
CLICK_TEXT "登录"
WAIT 3
IF WINDOW_EXISTS "确认"
  CLICK_TEXT "确认"
END
```

### DSL <-> FlowScript 双向转换

| 方向 | 方法 | 入口 |
|---|---|---|
| DSL -> FlowScript | `dsl_flow_converter.dsl_to_flow(dsl)` | `python tools/dsl_to_flow.py < file.dsl` |
| FlowScript -> DSL | `dsl_flow_converter.flow_to_dsl(script)` | HTTP `/api/run-dsl` |
| DSL -> 执行 | `executor.run_dsl_sync(dsl)` | `python -c "from engine.executor import DSLExecutor; ..."` |

支持往返编辑：DSL -> 流程图 -> 修改节点 -> 导出 DSL，无损转换。

### Robot Framework 集成

引擎支持将 Robot Framework 测试用例转换为 DSL 并执行：

- `robot_converter.py` -- Robot Framework <-> DSL 双向转换
- `robot_runner.py` -- Robot Framework 测试执行器

## HTN 规划器

Hierarchical Task Network 规划，每次 AI 生成的流程自动注册为 HTN 模板，同类型任务可直接复用。

| 组件 | 位置 | 用途 |
|---|---|---|
| htnRegistry | `utils/htnRegistry.ts` | HTN 模板注册和管理 |
| htnPlanner | `utils/htnPlanner.ts` | HTN 任务分解与规划 |
| htnTemplateDefs | `utils/htnTemplateDefs.ts` | 预定义 HTN 模板 |
| htnTypes | `utils/htnTypes.ts` | HTN 类型定义 |

## 关键约定

- **新增节点类型**: `types/index.ts` + `nodeDefinitions.ts` + `params/*.tsx` + `FlowNode.tsx` (getNodeSummary) + `PropertyPanel.tsx` (switch case)
- **新增 MCP 工具**: 在 `python/features/` 用 `@feature` 装饰器声明，自动注册 MCP/CLI/HTTP/DSL
- **新增 Agent 能力**: 在 `python/agent/` 添加，`__main__.py` 驱动执行循环，`agent.py` 统一入口
- **新增 API 端点**: 在 `src/main/api.ts` 的 `switch (url.pathname)` 中添加 case
- **状态管理**: Zustand + immer，`useStore` hook
- **Electron IPC**: `preload.ts` 暴露 -> `main.ts` 处理 -> Python 子进程
- **DSL 格式**: 大写关键字 `CLICK 100 200` / `LOOP 5 ... END` / `IF cond ... ELSE ... END`
- **Agent IPC 协议**: `python/agent/__main__.py` stdin/stdout JSON（start/stop/guidance/chat/status 命令, 10+ 种事件）
- **Python 引擎协议**: `engine/engine.py` stdin/stdout JSON
- **测试**: `python -m pytest tests/` ~2550 tests (Python), `npx vitest run` ~16 文件 (TS)
- **中文编码**: 所有 Python 子进程通过 `spawnPython()` 强制 `PYTHONIOENCODING=utf-8`
- **快捷键**:
  - **F5**: 前端画布"运行/停止"执行
  - **F6**: 全局宏录制启停
  - **F9**: 全局系统级 Python 引擎启停（即使应用未聚焦）
  - **Ctrl+Z/Y**: 撤销/重做
  - **Ctrl+C/X/V**: 复制/剪切/粘贴节点
  - **Delete/Backspace**: 删除选中节点/连线
  - **Tab**: 切换面板显示
- **生产模式**: `app.isPackaged` 控制 `loadURL` (dev) 或 `loadFile` (prod)。`--dev` 标志可在打包后强制开发模式。
- **对话框处理**: `agent/dialog_handler.py` 纯 win32 API 检测/关闭对话框。现代 IFileDialog (Windows 8+) 无法通过自动化保存（DirectUI 渲染），Agent 应使用 `file_write` 程序化保存文件而非触发 UI 对话框
- **视觉定位系统**: `agent/visual_locator.py` + `agent/experience_db.py`。语义目标->屏幕坐标的两步策略：经验库->OpenCV 匹配，VLM 兜底。VLM 定位成功的坐标和按钮小图自动存入经验数据库供后续离线复用
- **内置 LLM 提供商**: `providers/base.py` (openai/deepseek/vlm 统一接口), `providers/openai.py`, `providers/deepseek.py`, `providers/vlm.py`
- **Agent 自反思**: `agent/reflection.py` 自动分析失败原因并存储改进建议，下次执行时取用

## 常见任务

```
# 运行 Python 测试
python -m pytest tests/ -x -q

# 运行 TypeScript 测试
npx vitest run

# DSL -> 流程图 往返验证
echo "CLICK 100 200" | python tools/dsl_to_flow.py

# 本地 DSL 执行（无需 Electron）
python -c "from engine.executor import DSLExecutor; e=DSLExecutor(None); e.run_dsl_sync('CLICK 100 200')"

# 查看 MCP 工具列表
python -c "from features._all import *; from features.registry import registry; print(registry.export_claude_context())"

# 启动滚轮选择器（独立测试）
python python/interaction/wheel_selector.py

# 通过 HTTP API 控制前端 (端口 7880)
curl -X POST http://127.0.0.1:7880/api/ping

# DSL -> 流程图 -> 加载到前端（一键）
curl -X POST http://127.0.0.1:7880/api/run-dsl -H "Content-Type: application/json" -d '{"dsl":"CLICK 500 400\nWAIT 2\nSCREENSHOT"}'

# 框选屏幕区域
curl -X POST http://127.0.0.1:7880/api/screenshot/select-region

# 启动 AI Agent
curl -X POST http://127.0.0.1:7880/api/agent-start -H "Content-Type: application/json" -d '{"goal":"打开记事本并输入文字","supervise":true}'
```

## 构建与部署

```bash
# 完整构建
npm run build                    # 构建 renderer (Vite) + main (tsc)

# 构建便携版
npx electron-builder --win portable   # 输出到 release/灵动脚本-*-portable.exe

# 测试生产模式
npx electron . --dev             # 打包后强制开发模式 (loadURL)
npx electron . --minimized       # 启动后最小化到任务栏
# 无 --prod 标志。打包后默认 loadFile；未打包时默认 loadURL。
# 使用 --dev 在打包后强制 loadURL。

# 发布到 GitHub Release
gh release create v1.0.1 "release/灵动脚本-1.0.0-portable.exe" --generate-notes
```

### 生产模式注意事项

- **CSP**：生产模式下从 `file://` 加载，已移除 CSP 头防止阻塞模块脚本
- **`isDev` 判断**: `!app.isPackaged || process.argv.includes('--dev')` -- 打包后默认生产模式，加 `--dev` 恢复开发模式
- **`--minimized` 标志**：默认窗口正常显示，加此标志则启动后最小化
- **`getBaseDir()`**：所有 Python 子进程的 `cwd` 通过此函数统一解析，开发时指向项目根目录，生产时指向 `process.resourcesPath`
- **`extraResources`**：`python/`、`tools/` 目录通过 `extraResources` 复制到 asar 外部，Python 子进程可直接访问
- **Python 路径**：生产模式下 Python 脚本位于 `resources/python/`，通过 `getEngineScriptPath()` 统一解析
- **API 端口**: 默认 `7880`，可通过 `API_PORT` 环境变量覆盖
