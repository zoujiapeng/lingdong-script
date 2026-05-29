<div align="center">

# 灵动脚本

**可视化桌面自动化流程编辑器 — AI Agent · 零Token DSL批处理 · 万物可控**

[![Electron](https://img.shields.io/badge/Electron-31-blue?logo=electron)](https://www.electronjs.org/)
[![React](https://img.shields.io/badge/React-18-61DAFB?logo=react)](https://reactjs.org/)
[![TypeScript](https://img.shields.io/badge/TypeScript-5-3178C6?logo=typescript)](https://www.typescriptlang.org/)
[![Python](https://img.shields.io/badge/Python-3.14-3776AB?logo=python)](https://www.python.org/)
[![Vite](https://img.shields.io/badge/Vite-5-646CFF?logo=vite)](https://vitejs.dev/)
[![Tests](https://img.shields.io/badge/Tests-296_✔-success)](https://github.com/zoujiapeng/lingdong-script)

</div>

📊 **[查看完整运行原理流程图 →](docs/architecture_flow.html)** （Mermaid.js 架构总览）

灵动脚本是一款**可视化桌面自动化流程编辑器**，通过拖拽节点编排流程图来创建自动化脚本。无需编程经验，即可完成屏幕点击、文字输入、OCR 识别、图片检测、进度条监控、条件分支、循环等桌面自动化操作。

**核心理念：一次 AI 生成，永久零 Token 执行。** MCP 只判断"能不能做"，DSL 一次生成后本地批量执行，不消耗任何 API token。

---

## 架构哲学

### 三执行层

```
MCP 层 (Claude Code / AI Agent)
  │  判断可行性, 生成 DSL
  ▼
DSL 层 (批处理执行)
  │  一次性生成, 本地零 Token 执行
  ▼
Zero-AI Runtime (引擎内核)
      247 @feature 工具, 190 DSL 关键字, 纯本地
```

| 层 | 调用方式 | Token 成本 | 用途 |
|---|---|---|---|
| **MCP 轮询** | 单步调用 `callFeature` | 高（每步都走 AI） | 判断可行性、生成 DSL |
| **DSL 批处理** | `run_dsl` 一次性执行 | **零 Token** | 执行自动化流程 |
| **@feature 直调** | 直接调用 Python 工具 | **零 Token** | 确定需要的功能直接调用 |

### 薄 Claude，厚 Lobster

最大化 Lobster 自身能力，最小化 AI 调用：

- **MCP 判断 + DSL 执行**：Claude Code 用 MCP 判断任务可行性，然后生成 DSL 一次性交给 Lobster 执行，不走 MCP 单步轮询
- **预设模板通用化**：CLICK_TEXT / WAITSCREEN / COLOR_CHECK / IMAGE_CLICK 等高频模式预编译为 FlowScript 模板
- **复杂逻辑走 API**：只在需要 LLM 决策（OCR 验证、失败恢复）时才调用 AI
- **本地优先**：OpenCV 模板匹配、OCR、窗口管理、UI Automation 全部本地完成，零依赖外部服务

### 结晶化（Skill Tree）

每次 DSL 执行成功后自动生成可复用模板：

1. 执行完成 → 自动从 DSL 提取流程 → 生成 `.flow` 模板文件
2. 更新分层索引 `skill_tree.json`（category/pattern）
3. 前端 TopBar 模板下拉菜单出现"最近结晶"分段
4. 下次同类任务可直接用模板，无需 AI 重新生成

### 事件触发体系（TriggerBar）

完整的可视化触发器管理：

- **6 种触发源**：定时器(cron/interval)、文件监控、进程监控、窗口监控、剪贴板监控、网络状态
- **TriggerBar**：StatusBar 上方显示所有活跃触发器状态 pill，点击可管理
- **条件守卫**：每个触发器可配置执行条件（仅特定窗口/时间段生效）
- **触发动作 DSL**：触发后执行的自动化 DSL 脚本

### VLM Provider 插件化

视觉定位三层递进，VLM 仅作为兜底：

1. **经验库 OpenCV 匹配**（零成本）→ 已有裁剪小图模板，直接模板匹配
2. **VLM 兜底**（有成本）→ 仅当经验库无匹配时才调用视觉模型
3. **失败** → 返回错误，走常规执行流程

VLM 后端可配置：GPT-4o-mini / 本地量化模型 / 关闭。所有 VLM 定位成功的坐标和按钮小图自动存入经验库，后续离线复用。

---

## 架构亮点

经过深度架构优化，关键指标大幅提升：

| 指标 | 优化前 | 优化后 |
|---|---|---|
| Python 冷启动 | 2-8 秒 | 20ms (400x) |
| 前端 JS 体积 | 1.17 MB (单文件) | ~1.3 MB (代码分割并行加载) |
| 并发 Python 进程 | 无限制 (OOM 风险) | 最多 3 (排队) |
| 调度器崩溃 | 静默死亡 | 2s 自动重启 |
| 条件评估 | 无限阻塞 | 30s 超时 |
| 前端渲染 | 全局重绘 | 精确选择器 + React.memo |
| 文件 I/O (热路径) | 同步 fs (阻塞主进程) | 异步 fsp |
| isDev 检测 | `!process.resourcesPath` (永远 false) | `!app.isPackaged` (标准 API) |

---

## 核心特性

### 可视化流程图编辑器
- **拖拽编排** — 直观的流程图编辑界面，55 种节点类型
- **实时连接** — 节点间连线定义执行顺序和条件分支
- **属性面板** — 选中节点后直接在面板编辑参数（含区域拾取、策略选择）
- **三视图模式** — 流程图 / 伪代码 / 设置 自由切换
- **自适应缩放** — 一键适配画布到视图窗口

### 智能节点库
| 分类 | 节点数 | 功能 |
|------|--------|------|
| 鼠标动作 | ~15 | 单击/双击/右键/拖拽/悬停/滚动 |
| 键盘输入 | ~5 | 输入文字/快捷键/组合键 |
| 图像识别 | ~8 | 找图点击/OCR 文字点击/颜色检测 |
| 条件控制 | ~12 | 条件分支/循环/场景判断/变量比较 |
| 等待同步 | ~6 | 定时等待/图片出现/区域稳定/进度条 |
| 弹窗处理 | ~3 | 自动关闭弹窗/多策略组合 |
| 系统工具 | ~20 | 运行程序/剪贴板(文本/图片/文件)/窗口操作(最小化/最大化/还原/贴靠)/进程管理 |
| AI 辅助 | ~10 | AI 规划/自动生成/智能调试 |
| 宏录制 | ~8 | 录制/回放/转流程图 |
| 摄像头 | ~2 | 单帧拍照/视频录制 |
| 变量工具 | ~5 | 变量设置/获取/运算 |
| 调试工具 | ~10 | 日志/断点/状态查看 |
| DSL 工具 | ~5 | DSL 转换/执行 |

#### 原生节点层次结构

每个高级节点类型通过 `sub_operation`/`action` 选择器，将多个相关功能折叠为一个入口：

```
clipboard_action  ├─ set 写入文本
                  ├─ get 读取文本
                  ├─ get_image 获取图片
                  ├─ get_files 获取文件列表
                  └─ clear 清空

window_control    ├─ minimize 最小化
                  ├─ maximize 最大化
                  ├─ restore 还原
                  ├─ close 关闭
                  ├─ resize 调整大小
                  └─ snap 贴靠(8方向)

mouse_operation   ├─ click(坐标/图片/OCR三种模式)
                  ├─ scroll 滚轮
                  ├─ hover 悬停
                  └─ move 移动
```

每种子操作映射到 Python `@feature` 函数，前端不直接调用，通过 `electronAPI.callFeature()` 桥接。

### 宏录制
- **F6 热键启停** — 独立进程录制，不阻塞 UI
- **智能过滤** — 自动跳过 <10px 的微小鼠标抖动
- **视觉宏录制** — 录制时截屏 + 智能按钮边界检测 + OCR 自动命名
- **一键转流程图** — 录制完成后自动转换为可视化节点
- **回放控制** — 支持速度调节、循环播放

### AI Agent（核心特性）
- **统一执行引擎** — 4 种执行模式（text_protocol / langgraph / planned / direct）折叠为 1 个 `_unified_run()`，通过 `AgentConfig` dataclass 解耦所有配置轴
- **设置面板可视化控制** — 7 项可调参数（LLM 协议、视觉模式、规划策略、屏幕上下文、错误处理、快速模式、截图去重），实时生效
- **MIMO 全面兼容** — 多模态模型在所有规划策略（pre_plan / per_step）、所有视觉模式（direct / som / omniparser）下正常工作
- **`resolve_auto()` 智能适配** — 多模态模型自动选择 text+direct+per_step+minimal；纯文本模型自动选择 function_calling+som+pre_plan+full
- **向后兼容** — 旧版 IPC 协议（`mimo` + `supervise` 布尔标志）自动转换为 AgentConfig，无缝过渡
- **自主规划** — AI 分析目标，自动生成执行计划（流程图可视化展示）
- **监督执行** — Agent stay alive 逐步执行，不再生成脚本后退出
- **窗口焦点安全** — 每步执行前检查目标窗口，防止输入错窗口
- **OCR 验证** — 执行后自动 OCR 验证结果，确保操作生效
- **失败恢复** — LLM 决策恢复策略（重试/替代方案/跳过/终止）
- **自然语言生成** — 用中文描述需求，AI 自动生成 DSL 并转为流程图
- **多种 LLM** — 支持 DeepSeek、MIMO、OpenAI 等兼容接口

### 语音对话
- **内置语音服务器** — 基于 WebSocket 的实时语音识别与合成
- **多引擎支持** — 5 种 STT 引擎可切换：

  | 引擎 | 大小 | 准确率 | 说明 |
  |------|------|--------|------|
  | whisper-tiny | ~75MB | 低（中文乱码） | 快速但中文准确率差 |
  | whisper-base | ~139MB | 中 | 偶有识别错误 |
  | whisper-small | ~461MB | **高** | 默认引擎，中文准确 |
  | whisper-large-v3-turbo | ~1.5GB | **最高** | 最佳质量（首次需下载） |
  | paraformer (sherpa-onnx) | ~40MB | **高** | 阿里达摩院中文 ASR，轻量准确 |

- **引擎热切换** — 设置面板 / 语音面板均可切换，实时生效
- **模型重下载** — 设置面板提供重新下载按钮
- **TTS 语音合成** — 支持系统 TTS 和 ElevenLabs（需 API Key）
- **状态栏显示** — 实时显示当前 STT 引擎名称

### 摄像头节点
- **camera_capture** — 单帧拍照，支持 png/jpg 格式和分辨率设置
- **camera_record** — 视频录制，支持 fps、duration、分辨率参数
- **camera_list** — 列出可用摄像头设备
- **camera_detect_motion** — 帧差法运动检测

**@feature 工具**: 共 5 个（camera_list, camera_capture, camera_record_start/stop, camera_detect_motion）

### DSL 脚本语言
灵动脚本内置专用的 DSL（领域特定语言），支持文本编辑模式和 190 个关键字：

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

DSL ↔ FlowScript 双向无损转换，支持往返编辑。

### @feature 工具注册表
- **247 注册工具** — 通过 `@feature` 装饰器声明，自动生成 MCP/CLI/HTTP 接口
- **懒加载** — 首次访问时才按需加载模块，冷启动 20ms
- **190 DSL 关键字** — 自动从注册表生成，含所有控制流关键字
- **MCP 协议支持** — 同时提供 Python 和 TypeScript 双 MCP 服务器
- **HTTP API 桥接** — `127.0.0.1:7880` 提供 55+ RESTful 端点
- **Agent 自我升级** — 工具集从注册表动态加载，新增功能自动纳入 Agent 可用范围

### 定时调度
- **可视化调度面板** — ScheduleDialog 管理所有定时任务
- **多触发方式** — cron 表达式 / 固定间隔 / 应用启动触发
- **条件守卫** — 触发前检查条件（特定窗口/时间段）
- **APScheduler 守护** — 独立 Python 进程，崩溃自动重启
- **TriggerBar 联动** — 前台实时显示调度状态

### 其他功能
- **区域选择器** — 全屏选框拾取坐标/区域
- **滚轮选择器** — 可视化滚轮选项选择
- **OCR 识别** — 屏幕文字识别与定位
- **模板系统** — 保存/加载常用流程模板 + 结晶化自动生成
- **撤销/重做** — 完整的操作历史

---

## 快速开始

### 下载安装

从 [Releases](https://github.com/zoujiapeng/lingdong-script/releases) 下载最新便携版（免安装），解压后直接运行 `灵动脚本.exe`。

### 从源码构建

```bash
# 克隆仓库
git clone https://github.com/zoujiapeng/lingdong-script.git
cd lingdong-script

# 安装依赖
npm install

# 开发模式（Vite HMR + Electron）
npm run dev

# 构建生产版本
npm run build

# 构建便携版
npx electron-builder --win portable

# 启动语音对话服务器（WebSocket :8765）
python python/voice/server.py 8765
```

---

## HTTP API 桥接

Electron 主进程内置 HTTP 服务器 `127.0.0.1:7880`，Claude Code 可通过 curl 实时控制前端：

```bash
# 健康检查
curl -X POST http://127.0.0.1:7880/api/ping

# DSL → 流程图 → 加载到前端
curl -X POST http://127.0.0.1:7880/api/run-dsl \
  -H "Content-Type: application/json" \
  -d '{"dsl":"CLICK 500 400\nWAIT 2\nSCREENSHOT"}'

# 添加节点
curl -X POST http://127.0.0.1:7880/api/add-node \
  -H "Content-Type: application/json" \
  -d '{"type":"click","x":200,"y":150,"params":{"x":500,"y":600}}'

# 获取当前状态
curl -X POST http://127.0.0.1:7880/api/get-state

# 启动 AI Agent
curl -X POST http://127.0.0.1:7880/api/agent-start \
  -H "Content-Type: application/json" \
  -d '{"goal":"打开记事本并输入 Hello"}'
```

55 API 端点（+ /mcp MCP over HTTP）覆盖：脚本管理、节点操作、引擎控制、宏录制、截图识别、滚轮选择、AI 生成、Agent 执行、指令标记。详见 [CLAUDE.md](CLAUDE.md)。

**注意：** 如果 API 包含中文参数，建议使用 Node.js 或 Python 发送请求（Windows Git Bash curl 无法正确处理命令行中的中文 UTF-8 编码）。

---

## 技术栈

| 层 | 技术 |
|---|---|
| 前端框架 | React 18 + TypeScript |
| 状态管理 | Zustand + immer |
| 流程图 | React Flow |
| 构建工具 | Vite 5 (代码分割) |
| 桌面框架 | Electron 31 |
| 后端引擎 | Python 3.10+ |
| 注册表系统 | Python `@feature` 装饰器 (懒加载) |
| MCP 协议 | Python MCP + TypeScript MCP |
| 图标 | Lucide React |

---

## 项目结构

```
src/
├── main/           # Electron 主进程
│   ├── main.ts     #   窗口管理, IPC, HTTP API :7880
│   ├── api.ts      #   HTTP API 服务器 (55+ 端点)
│   ├── engine.ts   #   Python 子进程管理 (队列, 并发控制)
│   ├── schedulerBridge.ts  # Python 调度器守护进程桥接
│   ├── env.ts      #   环境配置 (isDev / getBaseDir)
│   └── preload.ts  #   contextBridge API
└── renderer/       # React 前端 (Vite)
    ├── App.tsx     #   根组件/布局/快捷键
    ├── store/      #   Zustand (slices 模式) + immer
    ├── types/      #   FlowNode/FlowEdge/NodeParams
    ├── components/ #   画布/节点/属性面板/库/TriggerBar
    └── utils/      #   工具函数/节点定义/DSL生成/伪代码解析
python/
├── agent/          # AI Agent (规划/监督执行/OCR验证/失败恢复)
│   ├── __main__.py #   IPC 入口 (stdin/stdout JSON 协议)
│   ├── langgraph_agent.py # LangGraph 监督执行 + 视觉定位集成
│   ├── visual_locator.py # 视觉定位器 (经验库→OpenCV→VLM)
│   ├── experience_db.py  # 经验数据库 (模板裁剪小图存储)
│   ├── dialog_handler.py # win32 API 对话框自动化处理
│   └── menu_handler.py   # Windows 菜单操作
├── voice/          # 语音对话服务器
│   └── server.py   #   WebSocket 语音服务器 (5 引擎, TTS)
├── engine/         # 自动化执行引擎
│   ├── executor.py #   DSL 执行器 (核心)
│   ├── dsl_parser.py #  DSL 解析器
│   ├── dsl_flow_converter.py # DSL↔FlowScript 双向转换
│   ├── triggers.py #   TriggerEngine (6 种 watcher)
│   ├── crystallizer.py # 结晶化 (执行成功→模板)
│   └── scheduler_daemon.py # APScheduler 守护进程
├── features/       # @feature 注册表 (247 工具, 懒加载)
│   └── camera_features.py #   摄像头捕获/录制功能
├── providers/      # VLM Provider 抽象 (GPT-4o-mini/Local)
├── mcp/            # Python MCP 服务器
├── macros/         # 宏录制文件保存
├── interaction/    # 滚轮选择器/区域选择器
├── perception/     # OCR/图像匹配/场景检测
└── experience/     # 视觉经验数据库 (模板小图+索引)
templates/          # 工作流模板 (.flow) + skill_tree.json
```

---

## 许可

本项目基于 MIT 许可证开源。

---

## English

### Overview

**Lingdong Script** is a visual desktop automation flow editor. The core philosophy: **one AI generation, zero-token execution forever.**

Three execution layers:
1. **MCP Layer** (Claude Code / AI Agent) — feasibility check, DSL generation
2. **DSL Layer** (batch execution) — one-shot generation, local zero-token execution
3. **Zero-AI Runtime** (engine core) — 247 @feature tools, 190 DSL keywords, fully local

### Key Features

- **AI Agent** — Goal-oriented planning with supervised execution, OCR verification, window focus safety, failure recovery
- **Voice Conversation** — Built-in WebSocket voice server with 5 STT engines (whisper-tiny/base/small/large-v3-turbo/paraformer), hot-switchable, TTS support (system + ElevenLabs)
- **Camera Nodes** — camera_capture (photo), camera_record (video), camera_list, camera_detect_motion (5 @feature tools)
- **Crystallization** — Auto-generate reusable templates from successful DSL executions
- **Trigger System** — 6 trigger sources (timer/file/process/window/clipboard/network) with TriggerBar UI
- **VLM Provider** — Pluggable vision backend (GPT-4o-mini/Local), OpenCV-first fallback
- **Visual Flow Editor** — 55 native node types, hierarchical sub-operation structure
- **Macro Recording** — F6 hotkey, smart filtering, visual template extraction
- **@Feature Registry** — 247 tools (196 MCP tools, 190 DSL keywords), lazy-loaded (20ms cold start), auto MCP/CLI/HTTP/DSL
- **Architecture Optimized** — Code splitting (~1.3MB JS), Python process pool (max 3), async file I/O
- **HTTP API Bridge** — 55+ endpoints on `127.0.0.1:7880` for remote control
- **Timer Scheduling** — cron/interval/startup triggers with condition guards

### Architecture Highlights

| Metric | Before | After |
|---|---|---|
| Python cold start | 2-8s | 20ms (400x) |
| JS bundle | 1.17 MB | ~1.3 MB |
| Concurrent Python | unlimited (OOM risk) | max 3 (queued) |
| Scheduler crash | silent death | 2s auto-restart |
| Frontend render | global re-render | selective + React.memo |

### Quick Start

```bash
git clone https://github.com/zoujiapeng/lingdong-script.git
npm install
npm run dev              # Development (Vite HMR + Electron)
npm run build            # Production build
npx electron-builder --win portable  # Portable executable
```

### Tech Stack

React 18 · TypeScript · Zustand + immer · React Flow · Vite 5 (code splitting) · Electron 31 · Python 3.10+ · 247 @feature tools · MCP protocol · APScheduler

---

<div align="center">
  <p>Made with ❤️ by 邹先生</p>
  <p>邮箱：<a href="mailto:1724856284@qq.com">1724856284@qq.com</a> · 欢迎交流</p>
  <p>
    <a href="https://github.com/zoujiapeng/lingdong-script/issues">报告问题</a>
    ·
    <a href="https://github.com/zoujiapeng/lingdong-script/discussions">讨论交流</a>
  </p>
</div>
