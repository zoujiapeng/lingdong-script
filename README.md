<div align="center">

# 灵动脚本(开发测试中，功能不稳定)

**可视化桌面自动化流程编辑器 — 拖拽编排，万物可控**

[![Electron](https://img.shields.io/badge/Electron-31-blue?logo=electron)](https://www.electronjs.org/)
[![React](https://img.shields.io/badge/React-18-61DAFB?logo=react)](https://reactjs.org/)
[![TypeScript](https://img.shields.io/badge/TypeScript-5-3178C6?logo=typescript)](https://www.typescriptlang.org/)
[![Python](https://img.shields.io/badge/Python-3.14-3776AB?logo=python)](https://www.python.org/)
[![Vite](https://img.shields.io/badge/Vite-5-646CFF?logo=vite)](https://vitejs.dev/)
[![Tests](https://img.shields.io/badge/Tests-102_✔-success)](https://github.com/zoujiapeng/lingdong-script)
[![Bundle](https://img.shields.io/badge/Bundle-298_KB_JS-FF6B35)](https://github.com/zoujiapeng/lingdong-script)

</div>

灵动脚本是一款**可视化桌面自动化流程编辑器**，通过拖拽节点编排流程图来创建自动化脚本。无需编程经验，即可完成屏幕点击、文字输入、OCR 识别、图片检测、进度条监控、条件分支、循环等桌面自动化操作。

支持 AI / MCP / HTTP API 多种接入方式，Claude Code 可实时规划操控。从自然语言描述到自动化执行，全流程闭环。

**三种用法：** ① 手动拖拽编排流程图 ② 内置 AI 助手自然语言生成 ③ Claude Code / MCP 远程控制

问题：1打包版本未内置Python，需完整环境
     2复杂流程没有完整实机测试，真正到可用的指令比较少
     3界面可能闪烁待修复
     


---

## 架构亮点

经过深度架构优化，关键指标大幅提升：

| 指标 | 优化前 | 优化后 |
|---|---|---|
| Python 冷启动 | 2-8 秒 | 20ms (400x) |
| 前端 JS 体积 | 1.17 MB (单文件) | 298 KB (代码分割并行加载) |
| 并发 Python 进程 | 无限制 (OOM 风险) | 最多 3 (排队) |
| 调度器崩溃 | 静默死亡 | 2s 自动重启 |
| 条件评估 | 无限阻塞 | 30s 超时 |
| 前端渲染 | 全局重绘 | 精确选择器 + React.memo |
| 文件 I/O (热路径) | 同步 fs (阻塞主进程) | 异步 fsp |
| isDev 检测 | `!process.resourcesPath` (永远 false) | `!app.isPackaged` (标准 API) |

---

### 核心特性

#### 可视化流程图编辑器
- **拖拽编排** — 直观的流程图编辑界面，100+ 种节点类型
- **实时连接** — 节点间连线定义执行顺序和条件分支
- **属性面板** — 选中节点后直接在面板编辑参数（含区域拾取、策略选择）
- **三视图模式** — 流程图 / 伪代码 / 设置 自由切换
- **自适应缩放** — 一键适配画布到视图窗口

#### 智能节点库
| 分类 | 节点数 | 功能 |
|------|--------|------|
| 鼠标动作 | ~15 | 单击/双击/右键/拖拽/悬停/滚动 |
| 键盘输入 | ~5 | 输入文字/快捷键/组合键 |
| 图像识别 | ~8 | 找图点击/OCR 文字点击/颜色检测 |
| 条件控制 | ~12 | 条件分支/循环/场景判断/变量比较 |
| 等待同步 | ~6 | 定时等待/图片出现/区域稳定/进度条 |
| 弹窗处理 | ~3 | 自动关闭弹窗/多策略组合 |
| 系统工具 | ~15 | 运行程序/剪贴板/文件操作/进程管理 |
| AI 辅助 | ~10 | AI 规划/自动生成/智能调试 |
| 宏录制 | ~8 | 录制/回放/转流程图 |
| 变量工具 | ~5 | 变量设置/获取/运算 |
| 调试工具 | ~10 | 日志/断点/状态查看 |
| DSL 工具 | ~5 | DSL 转换/执行 |

#### 宏录制
- **F6 热键启停** — 独立进程录制，不阻塞 UI
- **智能过滤** — 自动跳过 <10px 的微小鼠标抖动
- **一键转流程图** — 录制完成后自动转换为可视化节点
- **回放控制** — 支持速度调节、循环播放

#### AI 辅助
- **自然语言生成** — 用中文描述需求，AI 自动生成 DSL 并转为流程图
- **智能补全** — AI 推荐下一步操作节点
- **多种 LLM** — 支持 DeepSeek、OpenAI 等兼容接口

#### DSL 脚本语言
灵动脚本内置专用的 DSL（领域特定语言），支持文本编辑模式：
```
CLICK 500 400
INPUT_TEXT 300 500 "用户名"
HOTKEY Tab
INPUT_TEXT 300 500 "密码123"
CLICK_TEXT "登录"
WAIT 3
IF COLOR_CHECK 100 200 50 30 #4CAF50
  CLICK_TEXT "确认"
END
```

#### @feature 工具注册表
- **160+ 注册工具** — 通过 `@feature` 装饰器声明，自动生成 MCP/CLI/HTTP 接口
- **懒加载** — 首次访问时才按需加载模块，冷启动 20ms
- **MCP 协议支持** — 同时提供 Python 和 TypeScript 双 MCP 服务器
- **HTTP API 桥接** — `127.0.0.1:5577` 提供 45+ RESTful 端点

#### 其他功能
- **区域选择器** — 全屏选框拾取坐标/区域
- **滚轮选择器** — 可视化滚轮选项选择
- **OCR 识别** — 屏幕文字识别与定位
- **模板系统** — 保存/加载常用流程模版
- **撤销/重做** — 完整的操作历史
- **定时调度** — 支持 cron/interval/startup 触发 + 条件守卫

---

### 快速开始

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

---

### HTTP API 桥接

Electron 主进程内置 HTTP 服务器 `127.0.0.1:5577`，Claude Code 可通过 curl 实时控制前端：

```bash
# 健康检查
curl -X POST http://127.0.0.1:5577/api/ping

# DSL → 流程图 → 加载到前端
curl -X POST http://127.0.0.1:5577/api/run-dsl \
  -H "Content-Type: application/json" \
  -d '{"dsl":"CLICK 500 400\nWAIT 2\nSCREENSHOT"}'

# 添加节点
curl -X POST http://127.0.0.1:5577/api/add-node \
  -H "Content-Type: application/json" \
  -d '{"type":"click","x":200,"y":150,"params":{"x":500,"y":600}}'

# 检查节点参数
curl -X POST http://127.0.0.1:5577/api/check-params \
  -H "Content-Type: application/json" \
  -d '{"nodes":[{"id":"n1","type":"click","params":{"x":100}}]}'

# 获取当前状态
curl -X POST http://127.0.0.1:5577/api/get-state

# 开始宏录制
curl -X POST http://127.0.0.1:5577/api/macro/start-recording \
  -H "Content-Type: application/json" \
  -d '{"name":"登录流程"}'
```

45+ API 端点覆盖：脚本管理、节点操作、引擎控制、宏录制、截图识别、滚轮选择、AI 生成。详见 [CLAUDE.md](CLAUDE.md)。

**注意：** 如果 API 包含中文参数，建议使用 Node.js 或 Python 发送请求（Windows Git Bash curl 无法正确处理命令行中的中文 UTF-8 编码）。

---

### 技术栈

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

### 项目结构

```
src/
├── main/           # Electron 主进程
│   ├── main.ts     #   窗口管理, IPC, HTTP API :5577
│   ├── api.ts      #   HTTP API 服务器 (45+ 端点)
│   ├── engine.ts   #   Python 子进程管理 (队列, 并发控制)
│   ├── schedulerBridge.ts  # Python 调度器守护进程桥接
│   ├── env.ts      #   环境配置 (isDev / getBaseDir)
│   └── preload.ts  #   contextBridge API
└── renderer/       # React 前端 (Vite)
    ├── App.tsx     #   根组件/布局/快捷键
    ├── store/      #   Zustand (slices 模式) + immer
    ├── types/      #   FlowNode/FlowEdge/NodeParams
    ├── components/ #   画布/节点/属性面板/库
    └── utils/      #   工具函数/节点定义
python/
├── engine/         # 自动化执行引擎 (DSL执行/条件评估)
├── features/       # @feature 注册表 (~160 工具, 懒加载)
├── mcp/            # Python MCP 服务器
├── macros/         # 宏录制文件保存
├── interaction/    # 滚轮选择器/区域选择器
└── perception/     # OCR/图像匹配
```

---

### 许可

本项目基于 MIT 许可证开源。

---

## English

### Overview

**Lingdong Script** is a visual desktop automation flow editor. Create automation scripts by drag-and-drop flowchart editing — no coding required.

Three ways to use: ① Manual flowchart editing ② Built-in AI assistant (natural language to DSL) ③ Claude Code / MCP remote control.

### Key Features

- **Visual Flow Editor** — 100+ node types, 3 view modes (canvas/pseudocode/settings)
- **Macro Recording** — F6 hotkey, smart filtering, one-click convert to flowchart
- **AI-Powered** — Natural language to automation scripts (DeepSeek/OpenAI)
- **DSL Scripting** — Built-in domain-specific language for text-based editing
- **@Feature Registry** — 160+ tools, lazy-loaded (20ms cold start), auto MCP/CLI/HTTP
- **Architecture Optimized** — Code splitting (298KB JS), Python process pool (max 3), async file I/O
- **HTTP API Bridge** — 45+ endpoints on `127.0.0.1:5577` for remote control
- **MCP Protocol** — Python + TypeScript dual MCP servers
- **Timer Scheduling** — cron/interval/startup triggers with condition guards

### Architecture Highlights

| Metric | Before | After |
|---|---|---|
| Python cold start | 2-8s | 20ms (400x) |
| JS bundle | 1.17 MB | 298 KB |
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

Download the latest portable release from [Releases](https://github.com/zoujiapeng/lingdong-script/releases).

### Tech Stack

React 18 · TypeScript · Zustand + immer · React Flow · Vite 5 (code splitting) · Electron 31 · Python 3.10+ · 160+ @feature tools · MCP protocol

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
