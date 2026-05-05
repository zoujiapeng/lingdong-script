<div align="center">

#  灵动脚本

**可视化桌面自动化流程编辑器 — 拖拽编排，万物可控**

[![Electron](https://img.shields.io/badge/Electron-31-blue?logo=electron)](https://www.electronjs.org/)
[![React](https://img.shields.io/badge/React-18-61DAFB?logo=react)](https://reactjs.org/)
[![TypeScript](https://img.shields.io/badge/TypeScript-5-3178C6?logo=typescript)](https://www.typescriptlang.org/)
[![Python](https://img.shields.io/badge/Python-3.10-3776AB?logo=python)](https://www.python.org/)
[![Vite](https://img.shields.io/badge/Vite-5-646CFF?logo=vite)](https://vitejs.dev/)

</div>

灵动脚本是一款**可视化桌面自动化流程编辑器**，通过拖拽节点编排流程图来创建自动化脚本。无需编程经验，即可完成屏幕点击、文字输入、OCR 识别、图片检测、进度条监控判断 条件分支、循环等桌面自动化操作。

支持agent接入通过cli/mcp实时规划操控，生成规划后实现0token消耗
简单来说：说人话给ai>自动规划生成流程图>执行

无论是软件测试、数据录入、日常办公自动化，还是复杂的 GUI 交互流程，灵动脚本都能通过直观的流程图方式快速实现。

### ✨ 核心特性

#### 🎨 可视化流程图编辑器
- **拖拽编排** — 直观的流程图编辑界面，127+ 种节点类型
- **实时连接** — 节点间连线定义执行顺序和条件分支
- **属性面板** — 选中节点后直接在面板编辑参数
- **自适应缩放** — 一键适配画布到视图窗口

#### 🧠 智能节点库
| 分类 | 节点数 | 功能 |
|------|--------|------|
| 鼠标动作 | ~15 | 单击/双击/右键/拖拽/悬停/滚动 |
| 键盘输入 | ~5 | 输入文字/快捷键/组合键 |
| 图像识别 | ~8 | 找图点击/OCR文字点击/颜色检测 |
| 条件控制 | ~12 | 条件分支/循环/场景判断/变量比较 |
| 等待同步 | ~6 | 定时等待/图片出现/区域稳定/进度条 |
| 弹窗处理 | ~3 | 自动关闭弹窗/多策略 |
| 系统工具 | ~15 | 运行程序/剪贴板/文件操作/进程管理 |
| AI 辅助 | ~10 | AI 规划/自动生成/智能调试 |
| 宏录制 | ~8 | 录制/回放/转流程图 |
| 变量工具 | ~5 | 变量设置/获取/运算 |
| 调试工具 | ~10 | 日志/断点/状态查看 |
| DSL 工具 | ~5 | DSL 转换/执行 |

#### 🎬 宏录制
- **F6 热键启停** — 独立进程录制，不阻塞 UI
- **智能过滤** — 自动跳过 <10px 的微小鼠标抖动
- **一键转流程图** — 录制完成后自动转换为可视化节点
- **回放控制** — 支持速度调节、循环播放

#### 🤖 AI 辅助
- **自然语言生成** — 用中文描述需求，AI 自动生成 DSL 并转为流程图
- **智能补全** — AI 推荐下一步操作节点
- **多种 LLM** — 支持 DeepSeek、OpenAI 等兼容接口

#### 📝 DSL 脚本语言
灵动脚本内置专用的 DSL（领域特定语言），支持文本编辑模式：
```
CLICK 500 400
INPUT_TEXT 300 500 "用户名"
KEYBOARD Tab
INPUT_TEXT 300 500 "密码123"
CLICK_TEXT "登录"
WAIT 3
IF COLOR_CHECK 100 200 50 30 #4CAF50
  CLICK_TEXT "确认"
END
```

#### 🔧 @feature 工具注册表
- **120+ 注册工具** — 通过 `@feature` 装饰器声明，自动生成 MCP/CLI/HTTP 接口
- **MCP 协议支持** — 同时提供 Python 和 TypeScript 双 MCP 服务器
- **HTTP API 桥接** — `127.0.0.1:5577` 提供 40+ RESTful 端点

#### 🛠 其他功能
- **区域选择器** — 全屏选框拾取坐标/区域
- **滚轮选择器** — 可视化滚轮选项选择
- **OCR 识别** — 屏幕文字识别与定位
- **模板系统** — 保存/加载常用流程模版
- **多视图模式** — 流程图/伪代码/设置三视图
- **撤销/重做** — 完整的操作历史

### 🚀 快速开始

#### 下载安装

从 [Releases](https://github.com/zoujiapeng/lingdong-script/releases) 下载最新便携版（免安装），解压后直接运行 `灵动脚本.exe`。

#### 从源码构建

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
```

### 🏗 技术栈

| 层 | 技术 |
|---|---|
| 前端框架 | React 18 + TypeScript |
| 状态管理 | Zustand + immer |
| 流程图 | React Flow (ReactFlow) |
| 构建工具 | Vite 5 |
| 桌面框架 | Electron 31 |
| 后端引擎 | Python 3.10+ |
| 注册表系统 | Python `@feature` 装饰器 |
| MCP 协议 | Python MCP + TypeScript MCP |
| 图标 | Lucide React |

### 📁 项目结构

```
src/
├── main/           # Electron 主进程
│   ├── main.ts     #   窗口管理, IPC, HTTP API :5577
│   └── preload.ts  #   contextBridge API
└── renderer/       # React 前端 (Vite)
    ├── App.tsx     #   根组件/布局/快捷键
    ├── store/      #   Zustand 状态管理
    ├── types/      #   FlowNode/FlowEdge 类型
    ├── components/ #   画布/节点/面板组件
    └── utils/      #   工具函数/节点定义
python/
├── engine/         # 自动化执行引擎
├── features/       # @feature 注册表 (~120 工具)
├── mcp/            # Python MCP 服务器
├── interaction/    # 滚轮选择器/区域选择器
├── perception/     # OCR/图像匹配
└── macros/         # 宏录制文件
```

### 🔌 HTTP API

Electron 主进程内置 HTTP 服务器（`127.0.0.1:5577`），支持远程控制：

```bash
# 健康检查
curl -X POST http://127.0.0.1:5577/api/ping

# DSL → 流程图 → 加载到前端
curl -X POST http://127.0.0.1:5577/api/run-dsl \
  -H "Content-Type: application/json" \
  -d '{"dsl":"CLICK 500 400\nWAIT 2\nSCREENSHOT"}'

# 获取当前状态
curl -X POST http://127.0.0.1:5577/api/get-state
```

详见 [CLAUDE.md](CLAUDE.md) 获取完整 API 文档。

### 📜 许可

本项目基于 MIT 许可证开源。

---

## English

### Overview

**Lingdong Script** is a visual desktop automation flow editor that lets you create automation scripts by drag-and-drop node编排. It supports screen clicking, text input, OCR recognition, image detection, conditional branching, loops, and more — all through an intuitive flowchart interface.

### Key Features

- **Visual Flow Editor** — 127+ node types, drag-and-drop interface
- **Macro Recording** — Record mouse/keyboard actions (F6 hotkey), convert to flowchart
- **AI-Powered** — Natural language to automation scripts, supports DeepSeek/OpenAI
- **DSL Scripting** — Built-in domain-specific language for text-based editing
- **@Feature Registry** — 120+ registered tools, auto-generates MCP/CLI/HTTP interfaces
- **Image Recognition** — Template matching, OCR text detection, color checking
- **MCP Protocol** — Dual Python + TypeScript MCP servers for AI integration
- **HTTP API Bridge** — 40+ RESTful endpoints for remote control

### Quick Start

```bash
# Install dependencies
npm install

# Development mode
npm run dev

# Production build
npm run build

# Build portable executable
npx electron-builder --win portable
```

Download the latest portable release from [Releases](https://github.com/zoujiapeng/lingdong-script/releases).

### Tech Stack

React 18 · TypeScript · Zustand · React Flow · Vite 5 · Electron 31 · Python 3.10+

---

<div align="center">
  <p>Made with ❤️ by 邹嘉鹏</p>
  <p>邮箱：<a href="mailto:1724856284@qq.com">1724856284@qq.com</a> · 欢迎交流</p>
  <p>
    <a href="https://github.com/zoujiapeng/lingdong-script/issues">报告问题</a>
    ·
    <a href="https://github.com/zoujiapeng/lingdong-script/discussions">讨论交流</a>
  </p>
</div>
