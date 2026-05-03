# 合并计划：灵动脚本 × Lobster 架构合并

> 分支：`refactor/merge-claude1`
> 创建日期：2026-05-04
> 目标：以 claude1 (Lobster) 架构为基础，融入当前项目 (灵动脚本) 的前端和 AI 特性

---

## 一、架构决策

| 决策 | 选择 | 原因 |
|------|------|------|
| Python 引擎架构 | **claude1** (python/engine/) | 更完善的状态机执行器、planner、scheduler、healing、optimizer |
| 功能注册系统 | **claude1** (python/features/registry.py) | @feature 装饰器自动生成 MCP/CLI/HTTP/DSL，架构优势明显 |
| CLI 入口 | **claude1** (cli/lobster.py) | 更成熟的 CLI 体系，支持 run/run-file/parse/list/info/test/call/mcp |
| MCP 服务器 | **claude1** (python/mcp/server.py) | 从 registry 动态生成，灵活可扩展 |
| Electron 前端 | **当前项目** (src/renderer/) | FlowCanvas 可视化编辑器完善，PropertyPanel 完整 |
| Electron 主进程 | **当前项目** (src/main/) | 与前端配套，集成更紧密 |
| AI 接口 | **合并两者** | 保留当前项目 src/ai-interface/ 的 AI 工具层，对接 claude1 的 engine |
| DSL 解析器 | **合并两者** | 当前项目 dsl/parser.py 指令完成度高，融入 claude1 的 engine/dsl_parser.py |
| `.mcp.json` | **从 claude1 引入** | 标准 MCP 配置格式 |

---

## 二、项目结构目标 (合并后)

```
claude/
├── cli/                          ← claude1: CLI 入口
│   ├── lobster.py                 # 主 CLI (保持 lobster 命令)
│   ├── lobster_mcp.py             # MCP 入口
│   ├── lobster.bat / .ps1         # Windows 启动脚本
│   └── __init__.py
│
├── python/                       ← claude1 架构 + 当前项目模块合并
│   ├── engine/                    # claude1: 核心引擎
│   │   ├── dsl_parser.py          # 合并 claude1 + 当前项目 dsl/parser.py
│   │   ├── executor.py            # claude1: 状态机执行器
│   │   ├── scheduler.py           # claude1: 任务调度
│   │   ├── planner.py             # claude1: AI 规划引擎
│   │   ├── healing.py             # claude1: 自愈
│   │   ├── optimizer.py           # claude1: 优化分析
│   │   ├── triggers.py            # claude1: 事件触发
│   │   └── store.py               # claude1: 状态持久化
│   │
│   ├── features/                  # claude1: 功能注册系统
│   │   ├── registry.py            # @feature 注册表核心
│   │   ├── action_features.py     # 动作 (CLICK/TYPE/SCROLL/DRAG/HOTKEY)
│   │   ├── perception_features.py # 感知 (OCR/图像/颜色/进度条)
│   │   ├── vision_features.py     # [新增] 当前项目 vision/ 模块封装
│   │   ├── recognition_features.py# [新增] 当前项目 recognition/ 模块封装
│   │   ├── interaction_features.py# [新增] 当前项目 interaction/ 模块封装
│   │   ├── game_features.py       # [新增] 当前项目 modules/ (combat/navigate/recovery/rewards)
│   │   ├── system_features.py     # [新增] 当前项目 system/ 模块封装
│   │   ├── a11y_features.py       # claude1: 无障碍功能
│   │   ├── ai_debug_features.py   # claude1: AI 调试
│   │   ├── ai_plan_features.py    # claude1: AI 规划
│   │   ├── browser_features.py    # claude1: 浏览器 (Playwright)
│   │   ├── clipboard_features.py  # claude1: 剪贴板
│   │   ├── dsl_features.py        # claude1: DSL 工具
│   │   ├── _all.py                # 自动导入所有 feature 模块
│   │   └── _utils.py              # 工具函数
│   │
│   ├── mcp/                       # claude1: MCP 服务器
│   │   └── server.py
│   │
│   ├── server.py                  # claude1: HTTP 后端
│   ├── interaction/               # [保留] 当前项目，供 features 调用
│   │   ├── actions.py             # [新增] claude1 ActionHandler (实际执行层)
│   │   ├── click_smart.py, click_all.py, human_behavior.py, popup.py
│   │   └── region_picker.py       # [新增] claude1 区域框选
│   │
│   ├── perception/                # [新增] 合并 vision/ + recognition/ + core/
│   │   ├── vision.py              # 截图管理 (合并 core/screenshot.py)
│   │   ├── matcher.py             # 图像匹配 (合并 recognition/matcher.py)
│   │   ├── ocr.py                 # OCR 识别 (合并 recognition/ocr.py)
│   │   ├── color.py               # 颜色检测 (合并 vision/color.py)
│   │   ├── progress.py            # 进度条检测 (合并 vision/progress.py)
│   │   ├── scene.py               # 场景检测 (合并 vision/scene.py)
│   │   ├── change.py              # 变化检测 (合并 vision/change.py)
│   │   ├── template_cache.py      # 模板缓存 (合并 core/template_cache.py)
│   │   └── ui_element.py          # UI 元素 (合并 recognition/ui_element.py)
│   │
│   └── utils/
│       ├── bezier.py              # 贝塞尔曲线
│       └── image.py               # 图像工具
│
├── src/                           ← 保留当前项目前端
│   ├── renderer/                  # React FlowCanvas 编辑器 (完整保留)
│   │   ├── components/
│   │   ├── store/                 # 状态管理 (Zustand)
│   │   ├── types/
│   │   ├── utils/
│   │   └── styles/
│   │
│   ├── main/                      # Electron 主进程 (保留)
│   │   ├── main.ts
│   │   └── preload.ts
│   │
│   ├── ai-interface/              # [合并] AI 接口层
│   │   ├── index.ts               # 入口 (MCP/HTTP 启动)
│   │   ├── tools/                 # AI 工具注册
│   │   │   ├── registry.ts
│   │   │   ├── run-flow.ts        # ★ 当前项目特有
│   │   │   ├── list-flows.ts      # ★ 当前项目特有
│   │   │   ├── create-flow.ts     # ★ 当前项目特有
│   │   │   ├── ask-ai.ts          # ★ 当前项目特有 (NL→DSL)
│   │   │   ├── screen-capture.ts  # ★ 当前项目特有
│   │   │   └── types.ts
│   │   ├── server/
│   │   │   ├── http.ts            # HTTP 服务器
│   │   │   └── mcp.ts             # MCP 服务器
│   │   └── prompt/
│   │       └── index.ts           # 提示词管理
│   │
│   └── cli/                       # [可选保留/弃用] 当前项目 CLI
│       ├── index.ts
│       └── commands/
│           ├── run.ts, ask.ts, create.ts, list.ts, serve.ts
│
├── electron/                      ← [废弃] 使用当前项目的 src/main/ + src/renderer/
│
├── .mcp.json                      ← 从 claude1 引入
├── main.py                        ← [保留/调整] 入口点
│
├── package.json                   ← 保留当前项目 (融合 claude1 依赖)
├── tsconfig.json / vite.config.ts ← 保留当前项目
│
├── templates/                     ← 图像模板目录
├── scenarios/                     ← .lobster 场景脚本
├── memory/                        ← claude1 的记忆系统
├── tests/                         ← 合并测试
│
└── test_*.py / test_*.flow        ← 测试文件
```

---

## 三、阶段任务分解

### Phase 0：准备（当前步骤）
- [x] 创建分支 `refactor/merge-claude1`
- [x] 提交当前 master 未完成工作
- [ ] 创建 MERGE_PLAN.md（本文档）
- [ ] 保存记忆

### Phase 1：Python 基础架构合并 — 引擎层

**目标**：以 claude1 的 `python/engine/` 为基础，融入当前项目的 DSL 解析能力。

| 任务 | 文件 | 操作 | 预估复杂度 |
|------|------|------|-----------|
| 1.1 复制 claude1 引擎 | `python/engine/` | cp -r | ⭐ |
| 1.2 合并 DSL 解析器 | `python/engine/dsl_parser.py` | 将当前项目 `dsl/parser.py` 的节点类型和语法规则合并进来 | ⭐⭐⭐ |
| 1.3 合并 DSL 条件解析 | `python/engine/dsl_parser.py` | 将当前项目 `dsl/resolver.py` 的条件解析能力合并 | ⭐⭐ |
| 1.4 适配执行器 action_handler | `python/engine/executor.py` | 确保 executor 能调用新合并的 features | ⭐⭐ |

### Phase 2：Python 功能层合并 — Features 系统

**目标**：以 claude1 的 `@feature()` 注册系统为核心，将当前项目的能力封装为 feature。

| 任务 | 文件 | 操作 | 预估复杂度 |
|------|------|------|-----------|
| 2.1 复制 registry | `python/features/registry.py` | cp | ⭐ |
| 2.2 复制 claude1 已有 features | `python/features/*.py` | cp (action/perception/a11y/ai_debug/ai_plan/browser/clipboard/dsl/_all/_utils) | ⭐ |
| 2.3 创建 vision_features | `python/features/vision_features.py` | 将 `vision/color.py`, `vision/scene.py`, `vision/change.py` 封装为 @feature | ⭐⭐⭐ |
| 2.4 创建 progress_features | `python/features/progress_features.py` | 将 `vision/progress.py` + `src/python/progress_display.py` 封装 | ⭐⭐ |
| 2.5 创建 recognition_features | `python/features/recognition_features.py` | 将 `recognition/matcher.py`, `recognition/ocr.py`, `recognition/ui_element.py` 封装 | ⭐⭐ |
| 2.6 创建 game_features | `python/features/game_features.py` | 将 `modules/combat.py`, `navigate.py`, `recovery.py`, `rewards.py` 封装 | ⭐⭐ |
| 2.7 创建 system_features | `python/features/system_features.py` | 将 `system/process.py`, `resolution.py`, `window.py` 封装 | ⭐ |
| 2.8 整合 ActionHandler | `python/interaction/actions.py` | 创建统一的 action 执行层，供 features 调用 | ⭐⭐⭐ |

### Phase 3：感知层合并

**目标**：统一两边的感知模块，消除重复代码。

| 任务 | 文件 | 操作 | 预估复杂度 |
|------|------|------|-----------|
| 3.1 合并截图管理 | `python/perception/vision.py` | 合并 `core/screenshot.py` + claude1 的截图逻辑 | ⭐⭐ |
| 3.2 合并图像匹配 | `python/perception/matcher.py` | 合并 `recognition/matcher.py` (多尺度匹配) | ⭐⭐ |
| 3.3 合并 OCR | `python/perception/ocr.py` | 合并 `recognition/ocr.py` | ⭐ |
| 3.4 合并颜色/进度/场景 | `python/perception/color.py` 等 | 从 `vision/` 移入 | ⭐ |

### Phase 4：CLI & MCP 合并

**目标**：采用 claude1 的 CLI + MCP，融合当前项目的 AI interface。

| 任务 | 文件 | 操作 | 预估复杂度 |
|------|------|------|-----------|
| 4.1 复制 claude1 CLI | `cli/` | cp lobster.py, lobster_mcp.py | ⭐ |
| 4.2 配置 .mcp.json | `.mcp.json` | 指向新的 MCP 入口 | ⭐ |
| 4.3 合并 AI Interface | `src/ai-interface/` | 保留当前项目 AI 工具层，对接 claude1 engine | ⭐⭐⭐ |
| 4.4 AI 工具适配 | `src/ai-interface/tools/` | 让 ask-ai / create-flow / run-flow 等工具通过 MCP 调用 claude1 engine | ⭐⭐⭐ |

### Phase 5：前端集成

**目标**：当前项目前端不变，确保能调用新的 Python 后端。

| 任务 | 文件 | 操作 | 预估复杂度 |
|------|------|------|-----------|
| 5.1 适配 Electron main | `src/main/main.ts` | 确保 main process 启动新的 Python engine | ⭐⭐ |
| 5.2 适配 preload | `src/main/preload.ts` | 暴露新 engine 的 IPC 通道 | ⭐ |
| 5.3 适配 renderer store | `src/renderer/store/` | 确保前端状态管理能对接新 engine | ⭐⭐ |
| 5.4 前端执行器适配 | `src/renderer/store/executor.ts` | 确保 FlowCanvas 的执行器调用新 engine | ⭐⭐ |

### Phase 6：清理 & 测试

| 任务 | 操作 | 预估复杂度 |
|------|------|-----------|
| 6.1 删除重复文件 | 移除旧版 dsl/, core/, vision/, recognition/ 中已合并的模块 | ⭐ |
| 6.2 修复 import | 全局更新 import 路径 | ⭐⭐ |
| 6.3 运行测试 | `tests/test_dsl.py`, `tests/test_vision.py` | ⭐⭐ |
| 6.4 端到端验证 | 启动全流程测试 | ⭐⭐⭐ |
| 6.5 更新 package.json | 合并依赖 | ⭐ |
| 6.6 更新 CLAUDE.md | 项目文档 | ⭐ |

---

## 四、重复模块对照表

| 功能 | 当前项目 | claude1 | 合并策略 |
|------|---------|---------|---------|
| DSL 解析 | `dsl/parser.py` | `python/engine/dsl_parser.py` | 合并到 claude1 |
| DSL 执行 | `dsl/executor.py` | `python/engine/executor.py` | 采用 claude1（状态机更强） |
| 条件解析 | `dsl/resolver.py` | (内嵌在 dsl_parser) | 合并到 dsl_parser |
| 截图 | `core/screenshot.py` | `perception/vision.py` 内部 | 统一到 `python/perception/vision.py` |
| 图像匹配 | `recognition/matcher.py` | `perception/vision.py` 内部 | 统一到 `python/perception/matcher.py` |
| OCR | `recognition/ocr.py` | (feature 调用) | 统一到 `python/perception/ocr.py` |
| 颜色检测 | `vision/color.py` | `features/perception_features.py` | 统一到 `python/perception/color.py` + feature |
| 进度条 | `vision/progress.py` | `features/perception_features.py` | 统一到 `python/perception/progress.py` + feature |
| 模拟点击 | `interaction/click_smart.py` | `interaction/actions.py` | 统一到 `python/interaction/` |
| human behavior | `interaction/human_behavior.py` | (内嵌) | 统一 |
| 弹窗检测 | `interaction/popup.py` | `features/a11y_features.py` | 统一到 feature |
| 变量系统 | `logic/variables.py` | `engine/executor.py` 内嵌 | 采用 claude1 |
| 重试/超时 | `logic/retry.py`, `logic/timeout.py` | `engine/executor.py` 内嵌 | 采用 claude1 |
| 窗口管理 | `system/window.py` | `features/a11y_features.py` | 统一到 feature |
| 进程管理 | `system/process.py` | - | 保留 |
| MCP 服务器 | `src/ai-interface/server/mcp.ts` | `python/mcp/server.py` | 双轨保留（TS + Python） |
| AI 工具 | `src/ai-interface/tools/` | `features/ai_debug_features.py` + `features/ai_plan_features.py` | 双轨保留，对接打通 |
| CLI | `src/cli/index.ts` (Node) | `cli/lobster.py` (Python) | 采用 claude1 (Python) |

---

## 五、当前项目特有功能（需保留）

以下功能是 claude1 没有的，合并时必须保留：

1. **FlowCanvas 可视化编辑器** — 节点拖拽、连线、属性面板
2. **伪代码生成器** (`src/renderer/utils/pseudoGenerator.ts`) — 节点图 → 伪代码
3. **伪代码解析器** (`src/renderer/utils/pseudoParser.ts`) — 伪代码 → 节点图
4. **AI ask 命令** (`src/cli/commands/ask.ts`) — 自然语言 → AI 生成伪代码
5. **screen-capture 工具** (`src/ai-interface/tools/screen-capture.ts`) — AI 截图工具
6. **create-flow 工具** (`src/ai-interface/tools/create-flow.ts`) — AI 创建工作流
7. **list-flows 工具** (`src/ai-interface/tools/list-flows.ts`) — AI 列出工作流
8. **game modules** — combat, navigate, recovery, rewards
9. **场景变化检测** (`vision/change.py`, `vision/scene.py`)
10. **进度条实时显示** (`src/python/progress_display.py`)
11. **Electron builder 配置** — Windows 安装包构建

---

## 六、执行顺序

```
Phase 0: 准备
    ↓
Phase 1: Python 引擎 (engine/) — 基础架构
    ↓
Phase 2: Python 功能 (features/) — 核心体系
    ↓
Phase 3: 感知层 (perception/) — 基础设施
    ↓
Phase 4: CLI & MCP 合并 — 对外接口
    ↓
Phase 5: 前端适配 — 用户界面
    ↓
Phase 6: 清理 & 测试 — 收尾
```

每阶段完成后，执行 `python tests/run_tests.py` 验证基础功能可用。

---

## 七、完成标准

- [ ] `python -m pytest tests/` 全部通过
- [ ] CLI `lobster run` / `lobster list` 正常运作
- [ ] MCP 服务可启动并被 Claude Code 发现
- [ ] Electron 前端可启动并加载 FlowCanvas
- [ ] 节点编辑 → 生成伪代码 → 执行 → 结果回显 全链路可用
- [ ] AI Interface (MCP/HTTP) 可调用所有 feature
- [ ] 无重复的 Python 模块（已全部合并或删除）
