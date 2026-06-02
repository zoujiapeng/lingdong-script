<div align="center">

# 灵动脚本 (Lingdong Script)

**桌面自动化从未如此灵活 — AI 驱动的流程编辑器 · 5 种 Agent 模式 · 200+ 开箱即用工具**

[![Electron](https://img.shields.io/badge/Electron-33-blue?logo=electron)](https://www.electronjs.org/)
[![React](https://img.shields.io/badge/React-18-61DAFB?logo=react)](https://reactjs.org/)
[![TypeScript](https://img.shields.io/badge/TypeScript-5-3178C6?logo=typescript)](https://www.typescriptlang.org/)
[![Python](https://img.shields.io/badge/Python-3.14-3776AB?logo=python)](https://www.python.org/)
[![Vite](https://img.shields.io/badge/Vite-6-646CFF?logo=vite)](https://vitejs.dev/)
[![License](https://img.shields.io/badge/License-MIT-green)](LICENSE)

</div>

灵动脚本是一款 **AI 驱动的桌面自动化流程编辑器**，将可视化流程图编排与 AI Agent 深度融合。你可以拖拽节点搭建自动化流程，也可以让 AI 理解你的需求后自动生成并执行。核心设计理念：**AI 辅助生成，零 Token 反复执行**，兼顾灵活性与经济性。

---

## 快速开始

### 下载安装

从 [Releases](https://github.com/zoujiapeng/lingdong-script/releases) 下载最新便携版（免安装），解压后直接运行。

### 从源码构建

```bash
git clone https://github.com/zoujiapeng/lingdong-script.git
cd lingdong-script
npm install
npm run dev                # 开发模式（Vite HMR + Electron）
npm run build              # 构建生产版本
npx electron-builder --win portable  # 构建免安装便携版
```

---

## 核心特性

### AI Agent 执行引擎

五种执行模式通过 `AgentConfig`（~30 参数）统一路由，`resolve_auto()` 自动适配多模态/纯文本模型：

| 模式 | 入口 | 说明 |
|------|------|------|
| **text_protocol** | `run_text_protocol()` | 文本协议循环：LLM → 解析工具调用 → 执行 → 截图反馈 |
| **langgraph** | `run_langgraph()` | LangGraph create_react_agent，函数调用模型 |
| **planned** | `run_planned()` | Planner 生成 DAG → 拓扑序执行（一次 LLM 调用，后续离线） |
| **computer_use** | `_computer_use_loop()` | Vision-only 循环：截图 → 分析 → 动作 → 新截图 |
| **direct** | `_run_direct()` | 一键生成流程图返回结果，不下场执行 |

**双工具集暴露**：LLM 同时可见 26 个元动作（高层语义，自动选择 UIA/OCR/图像匹配/坐标执行路径）+ 200+ 个 @feature 底层工具，按 7 级访问模式分组。

**监督执行流水线**（`supervisor.py`）：SoM 解引用 → UIA 定位 → 窗口感知 OCR → 预聚焦 → 视觉定位兜底 → 窗口焦点安全 → 执行 → 对话框自动处理 → 屏幕稳定等待 → OCR 验证。

**AgentConfig 核心配置**：

| 配置 | 选项 | 说明 |
|------|------|------|
| LLM 协议 | auto / text / function_calling | 与 LLM 的通信方式 |
| 视觉模式 | auto / direct / som / omniparser | 如何理解屏幕 |
| 规划策略 | auto / per_step / pre_plan | 逐步骤 vs 预生成计划 |
| 屏幕上下文 | auto / minimal / full | 向 LLM 提供多少屏幕信息 |
| 错误处理 | retry / replan / skip / stop | 失败恢复策略 |
| 视觉策略 | auto / cli_path / blind_guess / ocr_llm / som | 控制 Agent 如何"看"屏幕 |

### 可视化流程图编辑器

- **65+ 节点类型** — 覆盖交互动作、图像识别、控制流、系统操作、AI 规划、宏录制等
- **拖拽编排** — 节点间连线定义执行顺序和条件分支
- **属性面板** — 选中节点后编辑参数（含区域拾取、策略选择）
- **三视图模式** — 流程图 / 伪代码 / 设置 自由切换
- **聚合节点** — clipboard_action、window_control、mouse_operation 等通过 sub_operation 折叠多个功能为一入口

### DSL 脚本语言

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

DSL ↔ FlowScript 双向无损转换，支持往返编辑。可通过 `python tools/dsl_to_flow.py < file.dsl` 或 HTTP API `/api/run-dsl` 即时转换。

### @feature 工具注册表

`python/features/` 目录包含 **49 个 .py 文件，43 个模块**，通过 `@feature` 装饰器声明：

```python
@feature(
    name="my_tool",
    display_name="我的工具",
    category=FeatureCategory.ACTION,
    params=[ParamSpec("target", "string", description="目标", required=True)],
    dsl_keyword="MY_TOOL",
)
def my_tool(target: str) -> dict:
    """工具实现"""
    return {"success": True}
```

每个 `@feature` 自动生成 MCP 工具、CLI 命令、HTTP 端点、DSL 关键字。懒加载机制使冷启动仅 20ms。覆盖 7 分类：

| 分类 | 工具数 | 功能 |
|------|--------|------|
| 交互动作 | ~68 | click, type, swipe, hotkey, scroll, browser, dialog, menu... |
| 感知识别 | ~29 | find_image, OCR, color_detect, progress, UIA, camera... |
| 控制流 | ~25 | wait, loop, condition, timer, software control... |
| AI 规划 | ~9 | plan, execute_plan, debug, VLM analyze, screen_understanding... |
| 宏指令 | ~10 | record, playback, list, delete, convert_to_flow, visual_macro... |
| 系统功能 | ~74 | window, process, clipboard, file, wifi, volume, display, scheduler... |
| 调试诊断 | ~32 | test, validate, system_info, feature introspection, healing, trigger... |

### 宏录制

- **F6 全局热键** — 独立进程录制，不阻塞 UI
- **智能过滤** — 自动跳过 <10px 的微小鼠标抖动
- **视觉宏录制** — 录制时截屏 + 智能按钮边界检测（floodFill/Canny/固定裁剪三层递进）+ OCR 自动命名
- **一键转流程图** — 录制完成后自动转换为可视化节点
- **回放控制** — 支持速度调节、循环播放

### 本地视觉引擎

让纯文本模型也能"看"屏幕，全部本地 GPU 完成，零 API 成本：

```
截图 → YOLOv8(144ms)检测图标 + MobileNet(10ms)分类 + RapidOCR(~3s)读文字
      → SoM 标号（#1: 「我的手机」坐标 913,263）
      → 纯文本 LLM 直接选 #N 或文字点击
```

- **YOLOv8 + ONNX DirectML** — 检测 UI 图标按钮，AMD 780M 上 144ms
- **MobileNet V3** — 图标分类（envelope、wall clock），10ms/个
- **RapidOCR** — 全屏文字识别 ~3s，附精确坐标
- **SoM 文字匹配** — LLM 说 `click(target="我的手机")`，自动查 SoM 坐标

### 视觉定位 + 经验复用学习

Agent 通过语义目标（如"搜索按钮"）自动定位屏幕坐标，并实现"越用越快"：

1. **经验库 OpenCV 匹配**（零成本） — 从已裁剪小图模板匹配
2. **VLM 兜底**（有成本） — 仅当经验库无匹配时调用视觉模型
3. 定位成功的坐标和按钮小图自动存入经验库，后续离线复用

经验数据存储于 `python/experience/`（SHA256 签名索引 JSON + PNG 模板目录）。

### 定时调度与触发器

- **6 种触发源**：cron 定时器 / 固定间隔 / 文件监控 / 进程监控 / 窗口监控 / 剪贴板监控 / 网络状态
- **TriggerBar** — 顶部栏显示所有活跃触发器状态
- **条件守卫** — 每个触发器可配置执行条件（仅特定窗口/时间段生效）
- **APScheduler 守护** — 独立 Python 进程，崩溃自动重启
- **ECA 模型** — Event-Condition-Action 触发架构

### 摄像头节点

5 个 @feature 工具：camera_list、camera_capture、camera_record_start、camera_record_stop、camera_detect_motion（帧差法运动检测）。

### 模板系统

- **30+ 预设模板** — 涵盖常见自动化场景
- **结晶化** — 每次 DSL 执行成功后自动生成可复用 `.flow` 模板
- **语义搜索** — 通过 HTTP API 按自然语言搜索模板
- **分层索引** — `skill_tree.json`（category/pattern）维护模板目录

### NLP 转流程图

```bash
curl -X POST http://127.0.0.1:7880/api/create-flow \
  -H "Content-Type: application/json" \
  -d '{"description":"打开计算器并计算 100+200"}'
```

自然语言描述 → LLM 生成 DSL → 自动转为可视化流程图，支持参数检查。

### HTN 规划器

Hierarchical Task Network 规划，每次 AI 生成的流程自动注册为 HTN 模板，同类型任务可直接复用。

### Agent IPC 协议

AI Agent 子进程通过 **stdin/stdout JSON** 与 Electron 主进程通信：

| 命令（Electron → Agent） | 参数 | 用途 |
|---|---|---|
| `start` | goal, config | 启动 Agent（5 模式自动路由） |
| `stop` | — | 请求优雅停止 |
| `guidance` | text | 注入用户指引 |
| `chat` | text | 对话模式聊天消息 |
| `status` | — | 查询当前状态 |

| 事件（Agent → Electron） | 关键字段 | 用途 |
|---|---|---|
| `agent_start` | goal, engine, model | Agent 启动 |
| `step_complete` | step_num, tool, result | 每步结果（前端实时更新） |
| `agent_done` | status, total_steps | Agent 结束 |
| `log` | level, message | 日志输出 |
| `agent_chat_response` | text | AI 聊天回复（对话模式） |

### 监督执行流水线

所有 GUI 交互动作统一经过 `supervisor.execute()` 的八步流水线：

1. **SoM 解引用** — `#N` 标号转为屏幕坐标
2. **UIA 元素定位** — 控件树匹配目标元素
3. **视觉定位兜底** — 经验库 → OpenCV → VLM
4. **窗口焦点安全** — 自动聚焦目标窗口
5. **工具执行** — 通过 feature registry 执行
6. **对话框自动处理** — win32 API 检测并关闭弹窗
7. **屏幕稳定等待** — 等待界面变化完成
8. **OCR 验证** — 验证操作结果（可选）

元动作（26 个）走 `meta_execute()` 快速路径，绕过部分验证步骤。

### 插件系统

外部 `.py` 文件可通过 `registry.load_plugin()` 在运行时动态加载，自动注册为 @feature 工具。同时支持自愈功能模块（`healing_features.py`），可自动修复常见问题。

---

## 架构概览

### 三执行层

```
AI Agent (LLM 驱动)     —  26 元动作 + 200+ @feature 工具
       ▼
DSL 批处理 (零 Token)   —  一次性生成，本地执行，可反复运行
       ▼
Zero-AI Runtime (内核)  —  246 @feature 工具，纯本地，零依赖
```

| 层 | 调用方式 | Token 成本 | 用途 |
|---|---|---|---|
| **AI Agent** | LLM 循环 + 工具集 | 有 | 通用桌面自动化 |
| **DSL 批处理** | `run_dsl` 一次性执行 | **零 Token** | 执行自动化流程 |
| **@feature 直调** | 直接调用 Python 工具 | **零 Token** | 确定功能直接调用 |

### 工具访问路径选择器

Agent 按优先级选择最优执行路径：

| 优先级 | 模式 | 适用场景 |
|--------|------|----------|
| L0 | 直接 API | 文件操作、剪贴板、进程管理 |
| L1 | 浏览器 CDP | 浏览器内自动化 |
| L2 | Windows UIA | 窗口控件操作 |
| L3 | 视觉 OCR | 文字定位、屏幕文字读取 |
| L4 | SoM 标号 | 元素检测 + 编号标注 |
| L5 | 宏录制/回放 | 录制回放、视觉宏 |
| L6 | DSL 脚本 | 可复用流程图生成 |

### SoM 标号系统

双轨引擎：**UIA + OCR 兜底**（默认工作路径，~1-3s），**OmniParser**（YOLOv8 图标检测 + Florence-2 标题，~3-8s，可选升级）。对屏幕元素进行蓝色框 + #N 编号标注，纯文本模型直接用 `#3` 指定目标。

---

## HTTP API 桥接

Electron 主进程内置 HTTP 服务器 `127.0.0.1:7880`，提供 55+ RESTful 端点，支持 Claude Code 远程控制：

```bash
# 健康检查
curl -X POST http://127.0.0.1:7880/api/ping

# DSL → 流程图
curl -X POST http://127.0.0.1:7880/api/run-dsl \
  -H "Content-Type: application/json" \
  -d '{"dsl":"CLICK 500 400\nWAIT 2\nSCREENSHOT"}'

# 启动 AI Agent
curl -X POST http://127.0.0.1:7880/api/agent-start \
  -H "Content-Type: application/json" \
  -d '{"goal":"打开记事本并输入 Hello"}'

# 获取当前状态
curl -X POST http://127.0.0.1:7880/api/get-state
```

完整 API 端点覆盖：脚本管理（load-script/new-script/save-script）、节点操作（add-node/update-node/delete-nodes）、引擎控制（engine/start/pause/resume）、宏录制（macro/start-recording/stop-recording/list/play）、截图识别（screenshot/capture/select-region + ocr/region）、AI 生成（ai/generate + generate-dsl/create-flow）、Agent 执行（agent-start）、特征工具（features/list/call）、UI 控制（set-ui/set-view-mode）、编辑器操作（undo/redo/copy/paste/select-all）。

同时支持标准 **MCP over HTTP** 端点 `POST /mcp`（自动注册到 `~/.claude.json`）以及 **Python MCP**（`python python/mcp/server.py`，stdio JSON-RPC，动态生成工具列表）和 **TypeScript MCP**（`npm run ai:mcp`，5 个工具用于前端集成）。

---

## 技术栈

| 层 | 技术 |
|---|---|
| 前端框架 | React 18 + TypeScript |
| 状态管理 | Zustand + immer |
| 流程图 | React Flow |
| 构建工具 | Vite 6 |
| 桌面框架 | Electron 33 |
| 后端引擎 | Python 3.10+ |
| 注册表系统 | Python @feature 装饰器（懒加载） |
| MCP 协议 | Python MCP + TypeScript MCP |
| 图标 | Lucide React |
| 调度 | APScheduler |
| 视觉 | YOLOv8 + MobileNet + RapidOCR + OpenCV |

---

## 项目结构

```
src/
├── main/               # Electron 主进程
│   ├── main.ts         #   窗口管理 / IPC 30+ handlers / HTTP API :7880
│   ├── api.ts          #   HTTP API 服务器 (55+ 端点 + /mcp)
│   ├── env.ts          #   环境配置 (isDev / getBaseDir)
│   ├── state.ts        #   全局状态管理 + agent token 系统
│   └── preload.ts      #   contextBridge (40+ API)
└── renderer/           # React 前端 (Vite)
    ├── App.tsx         #   根组件 / 布局 / 快捷键
    ├── store/          #   Zustand + immer 状态管理
    ├── types/          #   FlowNode / FlowEdge / NodeParams / FlowScript
    ├── components/     #   画布 / 节点 / 属性面板 / 节点库 / 通用组件
    └── utils/          #   节点定义 / DSL 生成 / 伪代码解析 / prompt 构建
python/
├── agent/              # AI Agent 执行系统 (~14 模块)
│   ├── __main__.py     #   IPC 入口 (stdin/stdout JSON 协议)
│   ├── agent.py        #   LingdongAgent 统一入口 (5 执行模式)
│   ├── config.py       #   AgentConfig dataclass (~30 参数)
│   ├── meta_actions.py #   26 元动作 + 注册表 + 提示词生成
│   ├── supervisor.py   #   监督执行层 (窗口焦点/OCR/重试/SoM/对话框)
│   ├── planner.py      #   Plan DAG 分解 + PlanExecutor
│   ├── visual_locator.py # 视觉定位器 (经验库→OpenCV→VLM)
│   ├── experience_db.py  # 经验数据库 (SHA256 签名索引)
│   ├── dialog_handler.py # win32 API 对话框检测/关闭
│   └── menu_handler.py   # Windows 菜单操作
├── engine/             # 自动化执行引擎
│   ├── executor.py     #   DSL 执行器 (核心)
│   ├── dsl_parser.py   #   DSL 解析器 (190 关键字)
│   ├── dsl_flow_converter.py # DSL↔FlowScript 双向转换
│   ├── triggers.py     #   触发器引擎 (6 种 watcher)
│   ├── crystallizer.py #   流程结晶化
│   └── scheduler_daemon.py # APScheduler 守护进程
├── features/           # @feature 注册表 (49 文件 / 43 模块 / ~246 功能)
│   ├── registry.py     #   FeatureRegistry 单例
│   └── *_features.py   #   按分类的功能模块
├── mcp/                # Python MCP 服务器
├── interaction/        # 滚轮选择器 / 区域选择器 / 交互工具
├── perception/         # OCR / 图像匹配 / 视觉融合
└── experience/         # 视觉经验数据库 (db.json + templates/*.png)
templates/              # 工作流模板 (.flow)
```

---

## 开发指南

### 关键命令

| 命令 | 用途 |
|---|---|
| `npm run dev` | 启动开发环境（Vite HMR + Electron） |
| `npm run build` | 构建 renderer + main |
| `npx electron-builder --win portable` | 构建免安装便携版 |
| `python -m pytest tests/ -x -q` | 运行 Python 测试（~2550） |
| `npx vitest run` | 运行 TypeScript 测试（~16 文件） |
| `python python/mcp/server.py` | 启动 Python MCP 服务器（stdio JSON-RPC） |
| `npm run ai:mcp` | 启动 TypeScript MCP 服务器（5 tools, stdio） |
| `curl -X POST http://127.0.0.1:7880/api/ping` | 检查 HTTP API 桥接 |
| `python tools/dsl_to_flow.py < file.dsl` | DSL 转 FlowScript JSON |
| `python -m pytest tests/test_executor.py -x -q` | 单独运行引擎测试 |

### Node types / 节点类型概览

| 分类 | 节点数 | 主要类型 |
|------|--------|----------|
| 基础交互 | ~12 | click, input_text, keyboard, swipe, mouse_operation, click_action |
| 图像识别 | ~8 | image_click, click_text, screenshot, popup_close, color_check |
| 控制流 | ~8 | condition, loop, wait, sub_script, scene_condition |
| 设备监控 | ~2 | progress_wait, region_wait |
| 系统操作 | ~20 | launch_program, kill_process, focus_window, volume_set, shutdown, clipboard, wifi, display |
| 高级工具 | ~5 | web_browser, dialog_action, a11y_action, wifi_action, tool_call |
| 聚合类型 | ~6 | system_power, audio_action, desktop_action, clipboard_action, process_action |
| 其他 | ~4 | start, end, variable_set, variable_get |

### 新增节点类型

```
types/index.ts          — 定义 FlowNode.type 枚举
nodeDefinitions.ts      — 注册节点定义（颜色/端口/默认参数）
params/*.tsx            — 属性面板参数编辑器
FlowNode.tsx            — getNodeSummary 显示摘要
PropertyPanel.tsx       — switch case 路由
```

### 新增 MCP 工具

在 `python/features/` 用 `@feature` 装饰器声明，自动注册 MCP/CLI/HTTP/DSL。

### 新增 Agent 能力

在 `python/agent/` 添加，`__main__.py` 驱动执行循环，`agent.py` 统一入口。

### 新增 API 端点

在 `src/main/api.ts` 的 `switch (url.pathname)` 中添加 case。请求解析和响应序列化参考现有模式，支持 JSON body 和 query params。

### 关键约定

- **状态管理**：Zustand + immer，`useStore` hook
- **Electron IPC**：`preload.ts` 暴露 → `main.ts` 处理 → Python 子进程
- **DSL 格式**：大写关键字 `CLICK 100 200` / `LOOP 5 ... END` / `IF cond ... ELSE ... END`
- **中文编码**：所有 Python 子进程强制 `PYTHONIOENCODING=utf-8`
- **新增 @feature**：在 `python/features/` 用 `@feature` 装饰器声明，自动注册所有接口
- **生产构建**：`npm run build` 构建 renderer (Vite) + main (tsc)，`npx electron-builder --win portable` 打包便携版

### 快捷键

| 键 | 用途 |
|---|---|
| F5 | 运行/停止画布执行 |
| F6 | 全局宏录制启停 |
| F9 | 全局 Python 引擎启停（即使应用未聚焦） |
| Ctrl+Z/Y | 撤销/重做 |
| Ctrl+C/X/V | 复制/剪切/粘贴节点 |
| Delete/Backspace | 删除选中节点/连线 |
| Tab | 切换面板显示 |

---

## 配置

### 环境变量

| 变量 | 默认值 | 说明 |
|---|---|---|
| `API_PORT` | 7880 | HTTP API 服务器端口 |

### 生产模式

打包后通过 `app.isPackaged` 判断：默认 `loadFile` 加载静态文件，`--dev` 标志强制开发模式 `loadURL`。Python 依赖通过 `extraResources` 复制到 `resources/` 目录。

| 标志 | 打包后行为 |
|---|---|
| 无 | 生产模式，`loadFile` + 无 CSP |
| `--dev` | 强制 `loadURL(http://localhost:5173)` |
| `--minimized` | 启动后最小化到任务栏 |

### AgentConfig 常用参数

| 参数 | 默认值 | 说明 |
|---|---|---|
| `temperature` | 0.1 | LLM 温度 |
| `max_tokens` | 4096 | LLM 最大输出 Token |
| `llm_retries` | 2 | LLM 调用重试次数 |
| `max_steps` | 20 | Agent 最大执行步数 |
| `max_consecutive_failures` | 5 | 连续失败终止阈值 |
| `min_success_rate` | 0.1 | 最低成功率 |
| `screenshot_quality` | 60 | 截图质量（1-100） |
| `verify` | true | 执行后 OCR 验证 |
| `image_dedup` | true | 截图去重（相同画面不重复发送） |
| `chat_mode` | false | 对话模式，Agent 可边执行边聊天 |
| `flow_chart_mode` | false | 生成流程图后监督执行 |
| `auto_learn` | false | 视觉定位成功后自动存入经验库 |

---

## 许可

本项目基于 MIT 许可证开源。
