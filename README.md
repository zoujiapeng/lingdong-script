<div align="center">

# 灵动脚本

**AI Agent 桌面操控系统 — 5 种执行模式 · 200+ 原生工具 · 可视化流程编排**

[![Electron](https://img.shields.io/badge/Electron-33-blue?logo=electron)](https://www.electronjs.org/)
[![React](https://img.shields.io/badge/React-18-61DAFB?logo=react)](https://reactjs.org/)
[![TypeScript](https://img.shields.io/badge/TypeScript-5-3178C6?logo=typescript)](https://www.typescriptlang.org/)
[![Python](https://img.shields.io/badge/Python-3.14-3776AB?logo=python)](https://www.python.org/)
[![Vite](https://img.shields.io/badge/Vite-6-646CFF?logo=vite)](https://vitejs.dev/)

</div>

灵动脚本是一个 **AI Agent 桌面操控系统**。它让 LLM 直接控制 Windows 桌面——点击按钮、输入文字、识别图像、操作浏览器、管理窗口进程等。

Agent 有 5 种执行模式，从纯视觉循环到函数调用到预生成计划，覆盖不同场景。所有 GUI 操作经过监督流水线：窗口焦点安全 → 元素定位 → 执行 → OCR 验证 → 失败恢复。

---

## 快速开始

```bash
git clone https://github.com/zoujiapeng/lingdong-script.git
cd lingdong-script
npm install
npm run dev
```

从 [Releases](https://github.com/zoujiapeng/lingdong-script/releases) 下载免安装便携版直接运行。

---

## 核心概念

### AI Agent：系统的核心

整个系统的中心是一个 **AI Agent**，它接收自然语言目标，通过 `AgentConfig`（约 30 个参数）决定执行策略，然后调度 26 个元动作 + 200+ 原生工具完成任务。

**5 种执行模式**通过 `_unified_run()` 统一路由：

| 模式 | 原理 | 适用场景 |
|------|------|----------|
| **computer_use** | 截图 → 模型分析 → 操作 → 新截图（纯视觉循环） | Claude Computer Use、视觉密集型任务 |
| **text_protocol** | LLM 回复"工具:/参数:" → 解析执行 → 截图反馈 | MIMO 等纯文本模型 |
| **langgraph** | LangGraph create_react_agent，函数调用 | DeepSeek/GPT/Claude 函数调用模型 |
| **planned** | 1 次 LLM 调用生成 DAG → 拓扑序离线执行 | 确定性任务，后续执行零 Token |
| **direct** | 调用 `generate_flow_script` 生成流程图，不下场 | 仅需流程编排，无需 Agent 下场 |

**AgentConfig 自动适配** — `resolve_auto()` 根据模型自动选择最佳配置组合。多模态模型走直接截图路径，纯文本模型走 SoM 标号路径。所有参数可在前端 AgentView 面板可视化调节。

### 双工具集

LLM 在同一提示词中看到两层工具：

- **26 元动作** — 高层语义操作（click、type_text、manage_window、browser_do、scroll_to 等），每个元动作自动路由到最佳底层路径（UIA 无障碍树 → OCR 文字 → 图像模板匹配 → 直接坐标），LLM 只需说"做什么"，不用管"怎么做"
- **200+ @feature 工具** — 底层原子能力，按 7 级访问模式分组（直接 API → 浏览器 → UIA → OCR → SoM → 宏 → DSL），元动作不够用时 LLM 直接调用底层工具

### 监督执行流水线

所有 GUI 交互动作经过 `supervisor.execute()`：

```
SoM 解引用 → UIA 元素定位 → 视觉定位兜底(经验库→OpenCV→VLM)
→ 窗口焦点安全 → 工具执行 → 对话框自动处理 → 屏幕稳定等待 → OCR 验证
```

每一步有重试、跳过、替代方案三层失败恢复。

---

## 执行模式详解

### 1. Computer Use 模式

纯视觉循环，不依赖任何 DOM/无障碍树：

```
截图 → 送入多模态模型 → 模型分析当前屏幕 → 输出动作指令
→ 执行动作 → 等界面变化 → 截新图 → 重复
```

适合：模型直接"看"屏幕操作的场景，无需元素定位。

### 2. Text Protocol 模式

文本模型通过结构化格式控制电脑：

```
LLM 回复:
工具: click
参数: x=500, y=300
思考: 我已看到登录按钮在屏幕中央偏右位置...
```

Agent 解析 `工具:` 和 `参数:` 行，通过 supervisor 执行，截图反馈给模型进行下一轮。支持 chat_mode，Agent 可边干活边聊天。

### 3. LangGraph 模式

标准函数调用协议，每个 @feature 工具包装为 LangChain Tool：

```
LLM 选择 tool_call → Agent 执行并返回结果 → LLM 继续规划
```

适合 DeepSeek/GPT/Claude 等原生支持 function calling 的模型，兼容 LangGraph create_react_agent。

### 4. Planned 模式

只需一次 LLM 调用，后续全部离线执行：

```
LLM 分析目标 → 生成 Plan DAG（节点间可并行+fallback链）
→ PlanExecutor 拓扑序执行 → 每步完成自动触发下游
→ 节点失败走 fallback 链（Plan B → C → 报错）
```

适合：目标明确、步骤可预见的任务。一次生成，后续执行零 Token 零 LLM 调用。

### 5. Direct 模式

调用 `generate_flow_script` 生成可视化流程图到画布，不下场执行。用户可审查、修改流程图后再手动运行。

---

## @feature 工具注册表

`python/features/` 目录 **49 个文件，43 个模块**，通过 `@feature` 装饰器声明，自动生成 MCP 工具、CLI 命令、HTTP 端点、DSL 关键字。

```python
@feature(name="my_tool", category=FeatureCategory.ACTION,
    params=[ParamSpec("target", "string", description="目标", required=True)])
def my_tool(target: str) -> dict:
    """工具实现"""
    return {"success": True}
```

| 分类 | 工具数 | 涵盖 |
|------|--------|------|
| 交互动作 | ~68 | click, type, swipe, hotkey, scroll, browser, filedialog, menu... |
| 感知识别 | ~29 | find_image, OCR, color_detect, UIA, camera, vision fusion... |
| 控制流 | ~25 | wait, loop, condition, timer, aggregate... |
| AI 规划 | ~9 | plan, execute_plan, debug, VLM analyze... |
| 宏指令 | ~10 | record, playback, convert_to_flow, visual_macro... |
| 系统功能 | ~74 | window, process, clipboard, file, wifi, volume, scheduler... |
| 调试诊断 | ~32 | test, validate, healing, trigger, introspection... |

懒加载机制，冷启动仅 20ms。新增功能自动纳入 Agent 可用范围，无需重启 Agent。

---

## 视觉系统

### SoM 标号（Set-of-Mark）

对屏幕元素标注蓝色框 + #N 编号，纯文本模型直接用 `#3` 指定目标：

- **UIA + OCR 兜底**（默认，~1-3s）— 控件树 + OCR 文字框 → 去重合并 → 编号排序 → 画框
- **OmniParser**（可选，~3-8s）— YOLOv8 图标检测 + Florence-2 标题识别

### 本地视觉引擎

让纯文本模型"看"屏幕，全部本地完成：

```
截图 → YOLOv8(144ms)检测图标 → MobileNet(10ms)分类 → RapidOCR(~3s)读文字
      → SoM 标号：#1「我的手机」坐标(913,263)
      → 纯文本 LLM 选 #1 或文字点击
```

### 视觉定位 + 经验复用

Agent 将语义目标（"搜索按钮"）转为屏幕坐标，越用越快：

1. **经验库 OpenCV 匹配**（零成本）— 已裁剪小图直接模板匹配
2. **VLM 兜底**（有成本）— 仅经验库无匹配时调用
3. 成功结果自动存入经验库（`python/experience/`，SHA256 索引 + PNG 模板）

---

## 宏录制

全局 F6 热键启停，独立录制进程不阻塞 UI：

- **智能过滤** — 自动跳过 <10px 鼠标抖动
- **视觉宏录制** — 每次点击时截全屏 + 智能按钮边界检测（floodFill/Canny/固定裁剪三层递进）+ OCR 自动命名
- **一键转流程图** — 宏事件 → FlowScript 可视化节点

---

## 流程图编辑器

Agent 的辅助工具之一，用于可视化和编排流程：

- **65+ 节点类型** — 交互动作、图像识别、控制流、系统操作、摄像头、AI 规划等
- **聚合节点** — clipboard_action / window_control / mouse_operation 等通过 sub_operation 折叠多操作为一入口
- **DSL ↔ FlowScript** — 190 关键字 DSL 与可视化流程图双向无损转换
- **三视图** — 流程图 / 伪代码 / Agent 面板

---

## HTTP API

Electron 主进程内置 HTTP 服务器 `127.0.0.1:7880`，55+ 端点，支持 Claude Code 等外部工具远程控制：

```bash
# AI Agent
curl -X POST http://127.0.0.1:7880/api/agent-start \
  -H "Content-Type: application/json" \
  -d '{"goal":"打开记事本并输入 Hello"}'

# DSL → 流程图
curl -X POST http://127.0.0.1:7880/api/run-dsl \
  -d '{"dsl":"CLICK 500 400\nWAIT 2\nSCREENSHOT"}'

# 执行流程
curl -X POST http://127.0.0.1:7880/api/engine/start \
  -d '{"script":{...}}'

# 宏录制
curl -X POST http://127.0.0.1:7880/api/macro/start-recording

# 工具调用
curl -X POST http://127.0.0.1:7880/api/features/call \
  -d '{"name":"ocr_region", "params":{"x":0,"y":0,"w":1920,"h":1080}}'

# MCP over HTTP
POST http://127.0.0.1:7880/mcp
```

同时提供 Python MCP（stdio JSON-RPC，自动注册所有 @feature 工具）和 TypeScript MCP 两个标准 MCP 入口。

---

## 快速参考

### IPC 协议（Agent 子进程 ↔ Electron）

| 命令 | 参数 | 说明 |
|------|------|------|
| `start` | goal, config, supervise | 启动 Agent |
| `stop` | — | 停止 |
| `guidance` / `chat` | text | 用户指引 / 对话消息 |
| `status` | — | 查询状态 |

| 事件 | 说明 |
|------|------|
| `agent_start` | Agent 启动 |
| `step_complete` | 每步结果（前端实时更新） |
| `agent_done` | 执行结束 |
| `log` / `error` | 日志 / 错误 |

### 开发命令

| 命令 | 用途 |
|------|------|
| `npm run dev` | 开发模式（Vite HMR + Electron） |
| `npm run build` | 构建生产版本 |
| `npx electron-builder --win portable` | 打包免安装便携版 |
| `python -m pytest tests/ -x -q` | 运行 Python 测试 |
| `npx vitest run` | 运行 TS 测试 |
| `python python/mcp/server.py` | Python MCP 服务器 |

### 快捷键

| 键 | 用途 |
|---|---|
| F5 | 流程图运行/停止 |
| F6 | 全局宏录制启停 |
| F9 | 全局引擎启停（应用未聚焦也可用） |
| Tab | 切换所有面板显示 |

---

## 技术栈

| 层 | 技术 |
|---|---|
| 前端 | React 18 + TypeScript + Vite 6 |
| 状态管理 | Zustand + immer |
| 桌面框架 | Electron 33 |
| 后端引擎 | Python 3.10+ |
| 工具注册表 | @feature 装饰器（懒加载） |
| AI Agent | 自定义 LangGraph / 文本协议 / 纯视觉循环 |
| MCP | Python stdio MCP + TypeScript MCP |
| 视觉 | YOLOv8 + MobileNet + RapidOCR + OpenCV |
| 调度 | APScheduler |

## 许可

MIT
