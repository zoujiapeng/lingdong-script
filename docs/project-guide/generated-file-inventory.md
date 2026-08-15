# 全项目逐文件机械事实索引

> 本文件由 `scripts/generate_project_guide.py` 从 `git ls-files -z` 和当前讲解文档生成。
> 它保证路径、行数、字节数、SHA-256、Python AST、TS/JS 明确声明与直接依赖来自真实源码。
> 语义架构、调用关系和通俗伪代码见同目录人工审计章节；本索引负责零遗漏与防漂移。

## 覆盖摘要

- 被索引文件：**745**
- 代码/测试/配置口径：**695**
- 文本文件总行数：**353,417**
- 总字节数：**14,146,866**
- 生成文件自身被排除，避免自引用 hash；生成脚本自身被纳入代码覆盖。

### 分类

| 分类 | 文件数 |
| --- | ---: |
| `asset-or-binary` | 7 |
| `configuration` | 19 |
| `developer-script` | 28 |
| `documentation` | 34 |
| `e2e-or-harness` | 11 |
| `electron-or-typescript-product` | 102 |
| `fixture-or-template` | 12 |
| `project-asset` | 7 |
| `supporting-code` | 126 |
| `test` | 197 |
| `winpilot-product` | 202 |

### 代码与配置语言

| 语言/格式 | 文件数 |
| --- | ---: |
| AutoHotkey | 1 |
| Batch | 2 |
| CSS | 4 |
| CommonJS | 1 |
| Flow DSL | 5 |
| FlowScript JSON | 7 |
| HTML | 5 |
| JSON | 21 |
| Java | 1 |
| JavaScript | 2 |
| PowerShell | 3 |
| Pytest configuration | 1 |
| Python | 509 |
| Python requirements | 1 |
| React TypeScript | 49 |
| TypeScript | 81 |
| TypeScript module | 1 |
| YAML | 1 |

## 逐文件事实

## `.gitattributes`

<!-- PROJECT-FILE: .gitattributes -->
### `.gitattributes`

[打开文件](<../../.gitattributes>)

- 分类/语言：`project-asset` / Binary or extensionless project file
- 规模：1 行，13 bytes
- SHA-256：`f80dcc94402f38c7b9be9d27d83a5f3010189ba041cdc5f27094480b4f909d0d`
- 源码事实摘要：text=auto
- 顶层符号/结构：—
- 直接静态依赖：—
- API/IPC 入口：—

## `.github`

<!-- PROJECT-FILE: .github/workflows/ci.yml -->
### `.github/workflows/ci.yml`

[打开文件](<../../.github/workflows/ci.yml>)

- 分类/语言：`configuration` / YAML
- 规模：41 行，934 bytes
- SHA-256：`8ea9d0e94a0dc261adcd61ad140ff07101e80b584ed04614eba8b5789f24d658`
- 源码事实摘要：name: CI
- 顶层符号/结构：—
- 直接静态依赖：—
- API/IPC 入口：—

## `.gitignore`

<!-- PROJECT-FILE: .gitignore -->
### `.gitignore`

[打开文件](<../../.gitignore>)

- 分类/语言：`project-asset` / Binary or extensionless project file
- 规模：114 行，2,263 bytes
- SHA-256：`efa22061a27276c4627a3c863ec93276e5ee0b0d42e2a4589236ce318a47a0fb`
- 源码事实摘要：node_modules/
- 顶层符号/结构：—
- 直接静态依赖：—
- API/IPC 入口：—

## `.mcp.example.json`

<!-- PROJECT-FILE: .mcp.example.json -->
### `.mcp.example.json`

[打开文件](<../../.mcp.example.json>)

- 分类/语言：`configuration` / JSON
- 规模：12 行，274 bytes
- SHA-256：`30232e45716ce1b7703516ad79c7c381674bc08e923231b95cddea0e3718a7ba`
- 源码事实摘要：配置 .mcp.example 对应的构建、运行、依赖或测试行为。
- 顶层符号/结构：`key:mcpServers`
- 直接静态依赖：—
- API/IPC 入口：—

## `.rgignore`

<!-- PROJECT-FILE: .rgignore -->
### `.rgignore`

[打开文件](<../../.rgignore>)

- 分类/语言：`project-asset` / Binary or extensionless project file
- 规模：32 行，641 bytes
- SHA-256：`d74d67085d20b1d1c240b257f7fc772c548d1cfbb6bdb21abee7c678c51bdc0a`
- 源码事实摘要：Generated dependencies/build output
- 顶层符号/结构：—
- 直接静态依赖：—
- API/IPC 入口：—

## `AGENTS.md`

<!-- PROJECT-FILE: AGENTS.md -->
### `AGENTS.md`

[打开文件](<../../AGENTS.md>)

- 分类/语言：`documentation` / Markdown
- 规模：65 行，3,833 bytes
- SHA-256：`8f3bc5a6cb86f4428cd958b41684ed1d71ca09153606d1ed1aad1b4de1d82142`
- 源码事实摘要：Repository agent instructions
- 顶层符号/结构：—
- 直接静态依赖：—
- API/IPC 入口：—

## `CHANGELOG.md`

<!-- PROJECT-FILE: CHANGELOG.md -->
### `CHANGELOG.md`

[打开文件](<../../CHANGELOG.md>)

- 分类/语言：`documentation` / Markdown
- 规模：168 行，26,519 bytes
- SHA-256：`be2ed0d548a9a4c810ff8d87cc7da8bda881a4f455ffc958b134b74eab4a8328`
- 源码事实摘要：Changelog
- 顶层符号/结构：—
- 直接静态依赖：—
- API/IPC 入口：—

## `CLAUDE.md`

<!-- PROJECT-FILE: CLAUDE.md -->
### `CLAUDE.md`

[打开文件](<../../CLAUDE.md>)

- 分类/语言：`documentation` / Markdown
- 规模：1373 行，99,346 bytes
- SHA-256：`c44365ff8465801411d016a624e8ca9db56f2bd62215fac402ab3257f6c20922`
- 源码事实摘要：AI / Codex / 开发者接手入口
- 顶层符号/结构：—
- 直接静态依赖：—
- API/IPC 入口：—

## `CONTRIBUTING.md`

<!-- PROJECT-FILE: CONTRIBUTING.md -->
### `CONTRIBUTING.md`

[打开文件](<../../CONTRIBUTING.md>)

- 分类/语言：`documentation` / Markdown
- 规模：116 行，5,396 bytes
- SHA-256：`58ae9e8f95a1bc55b5d4ad1f5415af24b9603907e896d5e75daaff2798e12291`
- 源码事实摘要：贡献指南
- 顶层符号/结构：—
- 直接静态依赖：—
- API/IPC 入口：—

## `README.md`

<!-- PROJECT-FILE: README.md -->
### `README.md`

[打开文件](<../../README.md>)

- 分类/语言：`documentation` / Markdown
- 规模：225 行，17,161 bytes
- SHA-256：`68dbeb336c672c0b7399fb47787715b2e3422171d927591660c771cebe98f57c`
- 源码事实摘要：灵动脚本 / Lingdong Script
- 顶层符号/结构：—
- 直接静态依赖：—
- API/IPC 入口：—

## `SECURITY.md`

<!-- PROJECT-FILE: SECURITY.md -->
### `SECURITY.md`

[打开文件](<../../SECURITY.md>)

- 分类/语言：`documentation` / Markdown
- 规模：70 行，4,048 bytes
- SHA-256：`9baa6b9ab68f08feb3bc0eb2c0a8e59e03aeaee80d4834988a2acbbe0a9cd3b8`
- 源码事实摘要：安全政策
- 顶层符号/结构：—
- 直接静态依赖：—
- API/IPC 入口：—

## `_e2e_harness.py`

<!-- PROJECT-FILE: _e2e_harness.py -->
### `_e2e_harness.py`

[打开文件](<../../_e2e_harness.py>)

- 分类/语言：`e2e-or-harness` / Python
- 规模：88 行，3,156 bytes
- SHA-256：`9a84c64eeb00f6e59406e9e9c077686e8e044e32da711de6a43f7cd70039b0b0`
- 源码事实摘要：Ad-hoc E2E harness: run one WinPilot task on the REAL desktop, stream steps,
- 顶层符号/结构：`shot`, `_drain`, `main`, `const:OUT`
- 直接静态依赖：`sys`, `time`, `threading`, `queue`, `pathlib`, `cv2`, `winpilot.utils.log`, `winpilot.utils.screenshot`, `winpilot.agent.loop`
- API/IPC 入口：—

## `assets`

<!-- PROJECT-FILE: assets/icon.png -->
### `assets/icon.png`

[打开文件](<../../assets/icon.png>)

- 分类/语言：`asset-or-binary` / PNG image
- 规模：binary 行，89,789 bytes
- SHA-256：`8ab86a926198e67725680075d79605aa00ac15e1d80cd33078c24f7cbe51ce10`
- 源码事实摘要：PNG 256x256
- 顶层符号/结构：—
- 直接静态依赖：—
- API/IPC 入口：—
- 解析说明：binary

## `demo_complex.flow`

<!-- PROJECT-FILE: demo_complex.flow -->
### `demo_complex.flow`

[打开文件](<../../demo_complex.flow>)

- 分类/语言：`fixture-or-template` / FlowScript JSON
- 规模：49 行，860 bytes
- SHA-256：`3de01cef5196bed0798ac9aa6f378aa955d94612a5605c9e031979c54fbce95f`
- 源码事实摘要：demo complex 的流程、模板或测试 fixture 数据。
- 顶层符号/结构：—
- 直接静态依赖：—
- API/IPC 入口：—
- 解析说明：JSON parse failed at line 1: Expecting value

## `docs`

<!-- PROJECT-FILE: docs/GUI_AGENT_ARCHITECTURE_AUDIT.md -->
### `docs/GUI_AGENT_ARCHITECTURE_AUDIT.md`

[打开文件](<../../docs/GUI_AGENT_ARCHITECTURE_AUDIT.md>)

- 分类/语言：`documentation` / Markdown
- 规模：236 行，13,191 bytes
- SHA-256：`a80f9c417f499a3169651379283f5a070199335a658883d607ffc32a8f921271`
- 源码事实摘要：GUI Agent 架构审计
- 顶层符号/结构：—
- 直接静态依赖：—
- API/IPC 入口：—

<!-- PROJECT-FILE: docs/GUI_AGENT_ARCHITECTURE_RESET_2026-07-29.md -->
### `docs/GUI_AGENT_ARCHITECTURE_RESET_2026-07-29.md`

[打开文件](<../../docs/GUI_AGENT_ARCHITECTURE_RESET_2026-07-29.md>)

- 分类/语言：`documentation` / Markdown
- 规模：2419 行，171,498 bytes
- SHA-256：`183b4907c2fa588fbb317b460255bcd5920d5017afa05e11e2831dd63da0148f`
- 源码事实摘要：GUI Agent 全分支与真实端到端架构审计
- 顶层符号/结构：—
- 直接静态依赖：—
- API/IPC 入口：—

<!-- PROJECT-FILE: docs/README.md -->
### `docs/README.md`

[打开文件](<../../docs/README.md>)

- 分类/语言：`documentation` / Markdown
- 规模：62 行，3,974 bytes
- SHA-256：`d756a3ae4c1fe315f9b0dbdc42a85ce87551f39f8b18d381a863fea1cda78b68`
- 源码事实摘要：文档索引
- 顶层符号/结构：—
- 直接静态依赖：—
- API/IPC 入口：—

<!-- PROJECT-FILE: docs/apis.md -->
### `docs/apis.md`

[打开文件](<../../docs/apis.md>)

- 分类/语言：`documentation` / Markdown
- 规模：163 行，19,988 bytes
- SHA-256：`3a952534364d43665a3d586f94c1cc50734a8a3a8b73a12d34f2abaa3b910623`
- 源码事实摘要：API / IPC / MCP
- 顶层符号/结构：—
- 直接静态依赖：—
- API/IPC 入口：—

<!-- PROJECT-FILE: docs/architecture.md -->
### `docs/architecture.md`

[打开文件](<../../docs/architecture.md>)

- 分类/语言：`documentation` / Markdown
- 规模：450 行，28,891 bytes
- SHA-256：`5e9103b0d00797bfab0e0fe2c602ea0d43cf66feb6d7017a2cc465dcd7d1fffb`
- 源码事实摘要：GUI Agent 架构与运行原理
- 顶层符号/结构：—
- 直接静态依赖：—
- API/IPC 入口：—

<!-- PROJECT-FILE: docs/chatgpt-desktop.md -->
### `docs/chatgpt-desktop.md`

[打开文件](<../../docs/chatgpt-desktop.md>)

- 分类/语言：`documentation` / Markdown
- 规模：127 行，9,489 bytes
- SHA-256：`fcf10707d42ab0645e4ad7affd25bbd405fff08b2deafbb7fb0b19ba4091ec61`
- 源码事实摘要：ChatGPT / Codex 桌面桥接
- 顶层符号/结构：—
- 直接静态依赖：—
- API/IPC 入口：—

<!-- PROJECT-FILE: docs/flow-editor.md -->
### `docs/flow-editor.md`

[打开文件](<../../docs/flow-editor.md>)

- 分类/语言：`documentation` / Markdown
- 规模：153 行，14,927 bytes
- SHA-256：`1cf80e56f2dfa65358c0399553a1608bbef63b0bafe3cc9db2cdac01e29c2080`
- 源码事实摘要：流程图 / DSL / @feature
- 顶层符号/结构：—
- 直接静态依赖：—
- API/IPC 入口：—

<!-- PROJECT-FILE: docs/frontend.md -->
### `docs/frontend.md`

[打开文件](<../../docs/frontend.md>)

- 分类/语言：`documentation` / Markdown
- 规模：214 行，13,712 bytes
- SHA-256：`d22417e2339453b51bddd766677a176693856b7cd251bc75c56ab7f9088a4f3a`
- 源码事实摘要：原生前端与运行时视图
- 顶层符号/结构：—
- 直接静态依赖：—
- API/IPC 入口：—

<!-- PROJECT-FILE: docs/memory-graph.md -->
### `docs/memory-graph.md`

[打开文件](<../../docs/memory-graph.md>)

- 分类/语言：`documentation` / Markdown
- 规模：115 行，9,372 bytes
- SHA-256：`36aa3758b874ed988b7a1e0a21401d52df0942f794787aa2e2e1c1bcfc40fe52`
- 源码事实摘要：WinPilot Memory Graph
- 顶层符号/结构：—
- 直接静态依赖：—
- API/IPC 入口：—

<!-- PROJECT-FILE: docs/model-providers.md -->
### `docs/model-providers.md`

[打开文件](<../../docs/model-providers.md>)

- 分类/语言：`documentation` / Markdown
- 规模：207 行，11,854 bytes
- SHA-256：`fad3ee5ada396283d69bf417a73541de56ceb2c3594fc8502e0cd281247e4646`
- 源码事实摘要：模型 Provider、视觉路由、Thinking、缓存与成本
- 顶层符号/结构：—
- 直接静态依赖：—
- API/IPC 入口：—

<!-- PROJECT-FILE: docs/offline-flow-node-e2e.md -->
### `docs/offline-flow-node-e2e.md`

[打开文件](<../../docs/offline-flow-node-e2e.md>)

- 分类/语言：`documentation` / Markdown
- 规模：191 行，14,994 bytes
- SHA-256：`8285644f1dea5f9d37fa21d45f7e23b527a64100f3c31ce67d57299dae66c87b`
- 源码事实摘要：离线流程节点逐项 E2E 矩阵
- 顶层符号/结构：—
- 直接静态依赖：—
- API/IPC 入口：—

<!-- PROJECT-FILE: docs/project-guide/01-system-architecture.md -->
### `docs/project-guide/01-system-architecture.md`

[打开文件](<../../docs/project-guide/01-system-architecture.md>)

- 分类/语言：`documentation` / Markdown
- 规模：390 行，21,480 bytes
- SHA-256：`2cf4418f5d2591badacac28aadf6093857187e6268f06b60165358b04d7b76d2`
- 源码事实摘要：01. 系统架构：进程、层次与唯一 owner
- 顶层符号/结构：—
- 直接静态依赖：—
- API/IPC 入口：—

<!-- PROJECT-FILE: docs/project-guide/02-runtime-flows.md -->
### `docs/project-guide/02-runtime-flows.md`

[打开文件](<../../docs/project-guide/02-runtime-flows.md>)

- 分类/语言：`documentation` / Markdown
- 规模：622 行，28,471 bytes
- SHA-256：`f88d366b938f4b561e80a655dbf862e5d43b9c22b19e755b46549d781bf665d1`
- 源码事实摘要：02. 运行时全流程：从点击发送到外部结果
- 顶层符号/结构：—
- 直接静态依赖：—
- API/IPC 入口：—

<!-- PROJECT-FILE: docs/project-guide/03-contracts-and-interfaces.md -->
### `docs/project-guide/03-contracts-and-interfaces.md`

[打开文件](<../../docs/project-guide/03-contracts-and-interfaces.md>)

- 分类/语言：`documentation` / Markdown
- 规模：597 行，29,063 bytes
- SHA-256：`9b909d4f83081844b4fffb04ad61c266f1edf97a439406d24d5d0543e5bb004a`
- 源码事实摘要：03. 合同、状态与接口参考
- 顶层符号/结构：—
- 直接静态依赖：—
- API/IPC 入口：—

<!-- PROJECT-FILE: docs/project-guide/04-testing-and-debugging.md -->
### `docs/project-guide/04-testing-and-debugging.md`

[打开文件](<../../docs/project-guide/04-testing-and-debugging.md>)

- 分类/语言：`documentation` / Markdown
- 规模：453 行，19,191 bytes
- SHA-256：`f97a38a5b54041075bbc8f0952703b76924620b6b2c8deec476cc9367d06c6a9`
- 源码事实摘要：04. 测试、真实 E2E 与故障定位
- 顶层符号/结构：—
- 直接静态依赖：—
- API/IPC 入口：—

<!-- PROJECT-FILE: docs/project-guide/05-file-reference-typescript-electron.md -->
### `docs/project-guide/05-file-reference-typescript-electron.md`

[打开文件](<../../docs/project-guide/05-file-reference-typescript-electron.md>)

- 分类/语言：`documentation` / Markdown
- 规模：476 行，58,176 bytes
- SHA-256：`dcf8a470aa298d1e4950bc48ff372035b598f257914f121452d5059ab24591b4`
- 源码事实摘要：05. TypeScript / Electron 逐文件精确讲解
- 顶层符号/结构：—
- 直接静态依赖：—
- API/IPC 入口：—

<!-- PROJECT-FILE: docs/project-guide/06-file-reference-winpilot-python.md -->
### `docs/project-guide/06-file-reference-winpilot-python.md`

[打开文件](<../../docs/project-guide/06-file-reference-winpilot-python.md>)

- 分类/语言：`documentation` / Markdown
- 规模：1147 行，87,803 bytes
- SHA-256：`ce6a18fdfc08691cf0a525ef0d11581edb11c9ba32c152552f9e269f9d91d6ee`
- 源码事实摘要：06. WinPilot Python 核心逐文件精确讲解
- 顶层符号/结构：—
- 直接静态依赖：—
- API/IPC 入口：—

<!-- PROJECT-FILE: docs/project-guide/07-file-reference-support-tests.md -->
### `docs/project-guide/07-file-reference-support-tests.md`

[打开文件](<../../docs/project-guide/07-file-reference-support-tests.md>)

- 分类/语言：`documentation` / Markdown
- 规模：707 行，75,938 bytes
- SHA-256：`59dedb72928afdd153bb159de17f26e561bb1afdda5be46b6802e42ed60e8388`
- 源码事实摘要：07. 支撑代码、测试、脚本与配置逐文件精确讲解
- 顶层符号/结构：—
- 直接静态依赖：—
- API/IPC 入口：—

<!-- PROJECT-FILE: docs/project-guide/README.md -->
### `docs/project-guide/README.md`

[打开文件](<../../docs/project-guide/README.md>)

- 分类/语言：`documentation` / Markdown
- 规模：180 行，13,961 bytes
- SHA-256：`44667251ae67335b720953b8268f02c0e60756633fef2749b8a26608f63326e6`
- 源码事实摘要：灵动脚本全项目精确讲解
- 顶层符号/结构：—
- 直接静态依赖：—
- API/IPC 入口：—

<!-- PROJECT-FILE: docs/replay-recording.md -->
### `docs/replay-recording.md`

[打开文件](<../../docs/replay-recording.md>)

- 分类/语言：`documentation` / Markdown
- 规模：199 行，21,183 bytes
- SHA-256：`36430d651236d539b5e4e2ff6b8b0c3f3ea7df875085ce94b996e445d600b0ce`
- 源码事实摘要：录制、Trace、Replay
- 顶层符号/结构：—
- 直接静态依赖：—
- API/IPC 入口：—

<!-- PROJECT-FILE: docs/testing.md -->
### `docs/testing.md`

[打开文件](<../../docs/testing.md>)

- 分类/语言：`documentation` / Markdown
- 规模：549 行，35,855 bytes
- SHA-256：`ffc3cb2bc3c2920c13454e0632a43628f4a57298a1984d2c69f8de18c19e4596`
- 源码事实摘要：GUI Agent 端到端测试规范
- 顶层符号/结构：—
- 直接静态依赖：—
- API/IPC 入口：—

<!-- PROJECT-FILE: docs/visual-runtime-principles.md -->
### `docs/visual-runtime-principles.md`

[打开文件](<../../docs/visual-runtime-principles.md>)

- 分类/语言：`documentation` / Markdown
- 规模：97 行，4,318 bytes
- SHA-256：`f8750c4a85579ca1586ecaaa100e5866bf3ee9edf2cd6860d434c9aadaaab51c`
- 源码事实摘要：Visual runtime principles
- 顶层符号/结构：—
- 直接静态依赖：—
- API/IPC 入口：—

<!-- PROJECT-FILE: docs/vnext-migration.md -->
### `docs/vnext-migration.md`

[打开文件](<../../docs/vnext-migration.md>)

- 分类/语言：`documentation` / Markdown
- 规模：401 行，26,129 bytes
- SHA-256：`aee6a3d4a17febce110b9b5704b75a6f175cd94a93c8273bafaf57997b7058b3`
- 源码事实摘要：vNext 迁移
- 顶层符号/结构：—
- 直接静态依赖：—
- API/IPC 入口：—

<!-- PROJECT-FILE: docs/winpilot-agent.md -->
### `docs/winpilot-agent.md`

[打开文件](<../../docs/winpilot-agent.md>)

- 分类/语言：`documentation` / Markdown
- 规模：244 行，13,619 bytes
- SHA-256：`0cc0f901705f98fcfb5d74198ecb657b6d2eef592be70410c0ecacf80f3eecc3`
- 源码事实摘要：WinPilot Agent 实现说明
- 顶层符号/结构：—
- 直接静态依赖：—
- API/IPC 入口：—

<!-- PROJECT-FILE: docs/项目导览-面试版.md -->
### `docs/项目导览-面试版.md`

[打开文件](<../../docs/项目导览-面试版.md>)

- 分类/语言：`documentation` / Markdown
- 规模：84 行，4,219 bytes
- SHA-256：`0bc9046c4a5c3614c9c170d2397b412e495ff47bc7dfa9aa0d1dc7e205ee84ef`
- 源码事实摘要：项目导览（面试版）
- 顶层符号/结构：—
- 直接静态依赖：—
- API/IPC 入口：—

## `examples`

<!-- PROJECT-FILE: examples/auto_collect.txt -->
### `examples/auto_collect.txt`

[打开文件](<../../examples/auto_collect.txt>)

- 分类/语言：`project-asset` / Text
- 规模：11 行，251 bytes
- SHA-256：`8c1438e101fba4cefa8b56a8e31371f25de3f1858cd232a37cf9e75171072f33`
- 源码事实摘要：自动收菜/收集奖励脚本
- 顶层符号/结构：—
- 直接静态依赖：—
- API/IPC 入口：—

<!-- PROJECT-FILE: examples/battle_loop.txt -->
### `examples/battle_loop.txt`

[打开文件](<../../examples/battle_loop.txt>)

- 分类/语言：`project-asset` / Text
- 规模：23 行，468 bytes
- SHA-256：`d3f1796f6164efaa91d980e9a19d9ddb0ca99a4aee755830967ec1df4109bdee`
- 源码事实摘要：自动战斗脚本
- 顶层符号/结构：—
- 直接静态依赖：—
- API/IPC 入口：—

<!-- PROJECT-FILE: examples/demo-login.json -->
### `examples/demo-login.json`

[打开文件](<../../examples/demo-login.json>)

- 分类/语言：`configuration` / JSON
- 规模：122 行，3,099 bytes
- SHA-256：`0e01d1abbcae034397c563b680084479cf3abea041046aabf03f502d7ebca72e`
- 源码事实摘要：配置 demo login 对应的构建、运行、依赖或测试行为。
- 顶层符号/结构：`key:meta`, `key:nodes`, `key:edges`, `key:variables`, `key:settings`, `node:start×1`, `node:click×2`, `node:wait×1`, `node:input_text×2`, `node:end×1`
- 直接静态依赖：—
- API/IPC 入口：—

<!-- PROJECT-FILE: examples/dsl_demo.txt -->
### `examples/dsl_demo.txt`

[打开文件](<../../examples/dsl_demo.txt>)

- 分类/语言：`project-asset` / Text
- 规模：64 行，1,209 bytes
- SHA-256：`0e1a792a44850ea3cfded77068d128a8bfadfb5870eaacc55745c607ad84cd20`
- 源码事实摘要：DSL 演示脚本 - 展示所有功能
- 顶层符号/结构：—
- 直接静态依赖：—
- API/IPC 入口：—

## `java-homework`

<!-- PROJECT-FILE: java-homework/.gitignore -->
### `java-homework/.gitignore`

[打开文件](<../../java-homework/.gitignore>)

- 分类/语言：`project-asset` / Binary or extensionless project file
- 规模：1 行，9 bytes
- SHA-256：`9aab1721ab971359d082afe69e59fce2c87ffe6867f5a16d3f859fde00d96f23`
- 源码事实摘要：.class
- 顶层符号/结构：—
- 直接静态依赖：—
- API/IPC 入口：—

<!-- PROJECT-FILE: java-homework/CONTROL_IDS.md -->
### `java-homework/CONTROL_IDS.md`

[打开文件](<../../java-homework/CONTROL_IDS.md>)

- 分类/语言：`documentation` / Markdown
- 规模：33 行，1,897 bytes
- SHA-256：`80f91c23812ca8874e47c603aeea9937b5bb52569ab355ebd5a02fc23aba67e3`
- 源码事实摘要：MiniAISandbox 控件 ID 表
- 顶层符号/结构：—
- 直接静态依赖：—
- API/IPC 入口：—

<!-- PROJECT-FILE: java-homework/MiniAISandbox.java -->
### `java-homework/MiniAISandbox.java`

[打开文件](<../../java-homework/MiniAISandbox.java>)

- 分类/语言：`supporting-code` / Java
- 规模：215 行，9,196 bytes
- SHA-256：`b233b721295af26881600c9786a653a76a0d69c5d1f3fd971513c885695bc9a6`
- 源码事实摘要：import javax.swing.*;
- 顶层符号/结构：—
- 直接静态依赖：—
- API/IPC 入口：—

<!-- PROJECT-FILE: java-homework/README.md -->
### `java-homework/README.md`

[打开文件](<../../java-homework/README.md>)

- 分类/语言：`documentation` / Markdown
- 规模：44 行，2,062 bytes
- SHA-256：`314c470d39b4e3047763a67966a5cb8a54016e597cd3070dbf2a3d4aa32139eb`
- 源码事实摘要：MiniAISandbox
- 顶层符号/结构：—
- 直接静态依赖：—
- API/IPC 入口：—

<!-- PROJECT-FILE: java-homework/SMOKE_TEST.md -->
### `java-homework/SMOKE_TEST.md`

[打开文件](<../../java-homework/SMOKE_TEST.md>)

- 分类/语言：`documentation` / Markdown
- 规模：18 行，246 bytes
- SHA-256：`8f60e06771e6e7df0702923af0f478d5998fc845fbbfc75433e320b3b9c4d253`
- 源码事实摘要：自检
- 顶层符号/结构：—
- 直接静态依赖：—
- API/IPC 入口：—

<!-- PROJECT-FILE: java-homework/run-windows.bat -->
### `java-homework/run-windows.bat`

[打开文件](<../../java-homework/run-windows.bat>)

- 分类/语言：`developer-script` / Batch
- 规模：6 行，129 bytes
- SHA-256：`6d0a5afede7db22fa68d763cf5cf8417e4d0f71dc45da39177368c8df8395199`
- 源码事实摘要：@echo off
- 顶层符号/结构：—
- 直接静态依赖：—
- API/IPC 入口：—

## `kill-electron.ps1`

<!-- PROJECT-FILE: kill-electron.ps1 -->
### `kill-electron.ps1`

[打开文件](<../../kill-electron.ps1>)

- 分类/语言：`developer-script` / PowerShell
- 规模：4 行，262 bytes
- SHA-256：`a81aa69da19c7274af8b096630b8cc77e8ed92478459d09371d279cbbedbb836`
- 源码事实摘要：Get-Process electron -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.Id -Force }
- 顶层符号/结构：—
- 直接静态依赖：—
- API/IPC 入口：—

## `lobster.bat`

<!-- PROJECT-FILE: lobster.bat -->
### `lobster.bat`

[打开文件](<../../lobster.bat>)

- 分类/语言：`developer-script` / Batch
- 规模：3 行，77 bytes
- SHA-256：`38aa1abfa4006f170c9c3ac5e90114607fdd70b6d18b04765d99699277191d53`
- 源码事实摘要：@echo off
- 顶层符号/结构：—
- 直接静态依赖：—
- API/IPC 入口：—

## `lobster.ps1`

<!-- PROJECT-FILE: lobster.ps1 -->
### `lobster.ps1`

[打开文件](<../../lobster.ps1>)

- 分类/语言：`developer-script` / PowerShell
- 规模：2 行，66 bytes
- SHA-256：`ca6b4534e916b516402153cdc40a43709ba9c2a9ef849cf44c8d7b08df13d326`
- 源码事实摘要：!/usr/bin/env pwsh
- 顶层符号/结构：—
- 直接静态依赖：—
- API/IPC 入口：—

## `package-lock.json`

<!-- PROJECT-FILE: package-lock.json -->
### `package-lock.json`

[打开文件](<../../package-lock.json>)

- 分类/语言：`configuration` / JSON
- 规模：6642 行，240,011 bytes
- SHA-256：`02448d3f88c513c4ec0ed7d468abddde1ddfb73a813e5e0585a44bcb6f22a21f`
- 源码事实摘要：lingdong-script
- 顶层符号/结构：`key:name`, `key:version`, `key:lockfileVersion`, `key:requires`, `key:packages`
- 直接静态依赖：—
- API/IPC 入口：—

## `package.json`

<!-- PROJECT-FILE: package.json -->
### `package.json`

[打开文件](<../../package.json>)

- 分类/语言：`configuration` / JSON
- 规模：143 行，4,813 bytes
- SHA-256：`3f1940cda581003ebbe72145fde97bccac87812a34ceb9ece3a07914fa600efb`
- 源码事实摘要：视觉优先、证据闭环的 Windows GUI Agent
- 顶层符号/结构：`key:name`, `key:version`, `key:description`, `key:repository`, `key:bugs`, `key:homepage`, `key:main`, `key:bin`, `key:scripts`, `key:dependencies`, `key:devDependencies`, `key:build`, `script:predev`, `script:dev`，另有 14 项
- 直接静态依赖：`immer`, `lucide-react`, `nanoid`, `react`, `react-dom`, `zustand`, `@testing-library/jest-dom`, `@testing-library/react`, `@testing-library/user-event`, `@types/node`, `@types/react`, `@types/react-dom`, `@vitejs/plugin-react`, `concurrently`，另有 7 项
- API/IPC 入口：—

## `pytest.ini`

<!-- PROJECT-FILE: pytest.ini -->
### `pytest.ini`

[打开文件](<../../pytest.ini>)

- 分类/语言：`configuration` / Pytest configuration
- 规模：8 行，236 bytes
- SHA-256：`c58b46e2ae99c5f6342d291abc9773971482117f4639ebc52004294b8e911fe3`
- 源码事实摘要：[pytest]
- 顶层符号/结构：—
- 直接静态依赖：—
- API/IPC 入口：—

## `python`

<!-- PROJECT-FILE: python/__init__.py -->
### `python/__init__.py`

[打开文件](<../../python/__init__.py>)

- 分类/语言：`supporting-code` / Python
- 规模：0 行，0 bytes
- SHA-256：`e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`
- 源码事实摘要：Python 包 python 的初始化与公开边界。
- 顶层符号/结构：—
- 直接静态依赖：—
- API/IPC 入口：—

<!-- PROJECT-FILE: python/agent/__init__.py -->
### `python/agent/__init__.py`

[打开文件](<../../python/agent/__init__.py>)

- 分类/语言：`supporting-code` / Python
- 规模：1 行，84 bytes
- SHA-256：`7fc2d15a97cec857981aeb4b0dc7b584eb077f2ab7d14774883c16e1da363d44`
- 源码事实摘要：Agent module — autonomous desktop agent powered by LLM + feature registry.
- 顶层符号/结构：—
- 直接静态依赖：—
- API/IPC 入口：—

<!-- PROJECT-FILE: python/agent/dialog_handler.py -->
### `python/agent/dialog_handler.py`

[打开文件](<../../python/agent/dialog_handler.py>)

- 分类/语言：`supporting-code` / Python
- 规模：1363 行，48,180 bytes
- SHA-256：`df5f711edef474b15a99a8f6efe72e543b3f54647f2318b8ce28252dfe6f8767`
- 源码事实摘要：dialog_handler.py — Windows 对话框处理模块（UIA + win32 双层）。
- 顶层符号/结构：`_match_button_keyword`, `_sort_buttons_by_priority`, `_get_buttons_safe`, `_uia_available`, `_ensure_com`, `_uia_get_buttons`, `_uia_find_window_by_hwnd`, `_uia_click_button`, `_uia_get_window_title`, `_uia_has_only_panes`, `DialogInfo`, `_is_popup_window`, `find_all_dialogs`, `find_dialog_by_title`，另有 32 项
- 直接静态依赖：`__future__`, `logging`, `os`, `threading`, `time`, `typing`, `pythoncom`, `win32gui`, `win32con`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/agent/menu_handler.py -->
### `python/agent/menu_handler.py`

[打开文件](<../../python/agent/menu_handler.py>)

- 分类/语言：`supporting-code` / Python
- 规模：525 行，19,876 bytes
- SHA-256：`427a6b628a7c05b010e44c7e6dcf6baa26f6f748b6f8f37d7c666ed2bb6ab7da`
- 源码事实摘要：menu_handler.py — 通用 Windows 菜单操作模块。
- 顶层符号/结构：`MenuHandler`, `menu_click`, `get_menu_structure`, `const:WM_COMMAND`, `const:WM_SYSCOMMAND`, `const:VK_MENU`, `const:VK_RETURN`, `const:VK_ESCAPE`, `const:VK_LEFT`, `const:VK_UP`, `const:VK_RIGHT`
- 直接静态依赖：`__future__`, `logging`, `time`, `ctypes`, `typing`, `win32gui`, `win32con`, `win32api`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/core/__init__.py -->
### `python/core/__init__.py`

[打开文件](<../../python/core/__init__.py>)

- 分类/语言：`supporting-code` / Python
- 规模：15 行，455 bytes
- SHA-256：`bf76d80d0a46383da688bd0d9e10ffac0a8a59d711b7e0b0107c21449b42653e`
- 源码事实摘要：core — Typed interfaces and shared abstractions.
- 顶层符号/结构：—
- 直接静态依赖：`.interfaces`, `.events`, `.logging`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/core/events.py -->
### `python/core/events.py`

[打开文件](<../../python/core/events.py>)

- 分类/语言：`supporting-code` / Python
- 规模：129 行，4,032 bytes
- SHA-256：`20ddb3cea4f2f72fea6841a24d934daddb244e654ec79957d4d3f3e1a4a4673d`
- 源码事实摘要：Event system — typed pub/sub event bus.
- 顶层符号/结构：`Event`, `EventBus`, `const:EVENT_AGENT_START`, `const:EVENT_AGENT_STOP`, `const:EVENT_AGENT_DONE`, `const:EVENT_AGENT_THINKING`, `const:EVENT_STEP_START`, `const:EVENT_STEP_COMPLETE`, `const:EVENT_STEP_FAIL`, `const:EVENT_STEP_SKIP`
- 直接静态依赖：`__future__`, `logging`, `time`, `dataclasses`, `typing`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/core/interfaces.py -->
### `python/core/interfaces.py`

[打开文件](<../../python/core/interfaces.py>)

- 分类/语言：`supporting-code` / Python
- 规模：175 行，5,512 bytes
- SHA-256：`583e88e81b5432ee62364b6d11e3ff1e9d8257bffbef6831578439128475baf2`
- 源码事实摘要：Core type definitions for the automation platform.
- 顶层符号/结构：`ToolResult`, `Observation`, `StepRecord`, `TaskState`, `Tool`, `LLMProvider`
- 直接静态依赖：`__future__`, `time`, `dataclasses`, `typing`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/core/logging.py -->
### `python/core/logging.py`

[打开文件](<../../python/core/logging.py>)

- 分类/语言：`supporting-code` / Python
- 规模：139 行，5,100 bytes
- SHA-256：`f46e8ff5dec2bd1fd70a498dcca423e2ad9b7f286d429dd4e319361c904c5a09`
- 源码事实摘要：Structured logging and replay system.
- 顶层符号/结构：`_serialize`, `StructuredLogger`
- 直接静态依赖：`__future__`, `json`, `threading`, `time`, `dataclasses`, `typing`, `.events`, `.interfaces`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/core/screenshot.py -->
### `python/core/screenshot.py`

[打开文件](<../../python/core/screenshot.py>)

- 分类/语言：`supporting-code` / Python
- 规模：117 行，4,455 bytes
- SHA-256：`acb43a32ea859515377b68155eb5d797a3c496ac92e2bb520cf1dce4ff30e871`
- 源码事实摘要：Screenshot cache and region capture for performance optimization.
- 顶层符号/结构：`ScreenshotManager`, `capture_screen`
- 直接静态依赖：`time`, `hashlib`, `typing`, `functools`, `pyautogui`, `numpy`, `PIL`, `cv2`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/engine/__init__.py -->
### `python/engine/__init__.py`

[打开文件](<../../python/engine/__init__.py>)

- 分类/语言：`supporting-code` / Python
- 规模：5 行，431 bytes
- SHA-256：`d9d05283ffade36fc423d0c40bdc346a2f77e355cd4cd618743eb20284e81ab6`
- 源码事实摘要：Python 包 python.engine 的初始化与公开边界。
- 顶层符号/结构：—
- 直接静态依赖：`.dsl_parser`, `.executor`, `.scheduler`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/engine/condition_evaluator.py -->
### `python/engine/condition_evaluator.py`

[打开文件](<../../python/engine/condition_evaluator.py>)

- 分类/语言：`supporting-code` / Python
- 规模：250 行，9,786 bytes
- SHA-256：`e2418157223ba23013ebbe2bd36cb92af6cc9edf43201bb657b93fd3c3866563`
- 源码事实摘要：条件评估引擎 — 为调度守卫条件和 DSL IF/WHILE 提供统一评估。
- 顶层符号/结构：`register_condition`, `condition`, `list_condition_types`, `evaluate`, `evaluate_one`, `_eval_single`, `_always_true`, `_window_exists`, `_process_running`, `_is_weekday`, `_is_weekend`, `_day_of_week`, `_time_between`, `_image_exists`，另有 4 项
- 直接静态依赖：`__future__`, `datetime`, `logging`, `typing`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/engine/crystallizer.py -->
### `python/engine/crystallizer.py`

[打开文件](<../../python/engine/crystallizer.py>)

- 分类/语言：`supporting-code` / Python
- 规模：235 行，8,564 bytes
- SHA-256：`aea8aad6eb1ee4f6b0d1544f0aff75f17d4cf823a7934ff816ae85037854c1bb`
- 源码事实摘要：结晶化 — 执行成功后将流程自动保存为可复用模板。
- 顶层符号/结构：`_ensure_dirs`, `_derive_template_name`, `_sanitize_name`, `_load_tree`, `_save_tree`, `_categorize`, `_make_flow_id`, `_crystallize_flow`, `crystallize`, `list_crystallized`, `get_skill_tree`, `const:_PYTHON_DIR`, `const:_TEMPLATES_DIR`, `const:_TREE_PATH`
- 直接静态依赖：`__future__`, `json`, `os`, `re`, `time`, `logging`, `pathlib`, `typing`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/engine/dsl_flow_converter.py -->
### `python/engine/dsl_flow_converter.py`

[打开文件](<../../python/engine/dsl_flow_converter.py>)

- 分类/语言：`supporting-code` / Python
- 规模：1451 行，56,476 bytes
- SHA-256：`97e0140669b8124546490409eef1013a77c187e9501ec4e290cc6c20152aae17`
- 源码事实摘要：DSL ↔ FlowScript 双向转换器
- 顶层符号/结构：`get_dsl_to_flow_type`, `get_flow_to_dsl_type`, `dsl_to_flow`, `_dsl_args_to_params`, `_dsl_label`, `_default_settings`, `_error_flow_script`, `flow_to_dsl`, `roundtrip_verify`, `const:_BASE_DSL_TO_FLOW`, `const:_BASIC_DSL_TO_FLOW`, `const:BRANCH_TYPES`, `const:LOOP_TYPES`, `const:NOOP_TYPES`，另有 1 项
- 直接静态依赖：`__future__`, `json`, `re`, `typing`, `engine.dsl_parser`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/engine/dsl_parser.py -->
### `python/engine/dsl_parser.py`

[打开文件](<../../python/engine/dsl_parser.py>)

- 分类/语言：`supporting-code` / Python
- 规模：592 行，24,322 bytes
- SHA-256：`600f85ee4e2d41efac6c73018554ecc0381c41c2978d6710aabcaa6590f3db16`
- 源码事实摘要：DSL Parser: 将 Lobster DSL 文本解析为 AST
- 顶层符号/结构：`NodeType`, `ASTNode`, `DSLParseError`, `DSLParser`, `const:_SIMPLE_NODE_TYPES`
- 直接静态依赖：`__future__`, `re`, `dataclasses`, `typing`, `enum`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/engine/engine.py -->
### `python/engine/engine.py`

[打开文件](<../../python/engine/engine.py>)

- 分类/语言：`supporting-code` / Python
- 规模：160 行，6,866 bytes
- SHA-256：`24fa4efc62848ba06f4b9020bfb631b654380653cc088c449f9a35339334c274`
- 源码事实摘要：引擎入口：Electron 主进程通过子进程调用。
- 顶层符号/结构：`emit`, `execute_flow`, `main`
- 直接静态依赖：`json`, `os`, `sys`, `time`, `traceback`, `pathlib`, `engine.executor`, `engine.dsl_flow_converter`, `engine.crystallizer`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/engine/executor.py -->
### `python/engine/executor.py`

[打开文件](<../../python/engine/executor.py>)

- 分类/语言：`supporting-code` / Python
- 规模：1906 行，88,250 bytes
- SHA-256：`2014bae4cbfce83b929a4394d264519cd80b6cc606565fff5b9d6c1968e38a0c`
- 源码事实摘要：执行层: 状态机驱动的 DSL 执行器
- 顶层符号/结构：`DSLExecutor`
- 直接静态依赖：`__future__`, `os`, `re`, `time`, `threading`, `traceback`, `concurrent.futures`, `typing`, `engine.dsl_parser`, `engine.executor_types`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/engine/executor_types.py -->
### `python/engine/executor_types.py`

[打开文件](<../../python/engine/executor_types.py>)

- 分类/语言：`supporting-code` / Python
- 规模：84 行，2,713 bytes
- SHA-256：`514b6c415e41ad692ed3e0a03efb319ff5f9bec789a0c80c03ea07554821203a`
- 源码事实摘要：Shared types for the legacy Flow/DSL executor.
- 顶层符号/结构：`ExecutorState`, `ExecutionContext`, `ExecutionError`, `ReturnSignal`, `BreakLoop`, `result_indicates_failure`, `result_error_message`
- 直接静态依赖：`__future__`, `dataclasses`, `enum`, `typing`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/engine/healing.py -->
### `python/engine/healing.py`

[打开文件](<../../python/engine/healing.py>)

- 分类/语言：`supporting-code` / Python
- 规模：254 行，10,967 bytes
- SHA-256：`f8d984ef83f090a156fa1354dd5387183e96eb8c579623fc565d7dcb3612467f`
- 源码事实摘要：自愈管道: 执行失败时的智能恢复策略链
- 顶层符号/结构：`HealResult`, `HealAttempt`, `HealingPipeline`, `get_healing_pipeline`
- 直接静态依赖：`__future__`, `math`, `time`, `random`, `traceback`, `typing`, `dataclasses`, `engine.executor_types`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/engine/nlp_schedule_parser.py -->
### `python/engine/nlp_schedule_parser.py`

[打开文件](<../../python/engine/nlp_schedule_parser.py>)

- 分类/语言：`supporting-code` / Python
- 规模：597 行，21,964 bytes
- SHA-256：`7155e1701b504773de8c4cf03f4ec4144870a1a1ef27867da8ea9a30a20a66bc`
- 源码事实摘要：自然语言调度解析器 — 纯规则匹配，无需 LLM
- 顶层符号/结构：`_normalize_cn_numbers`, `_find_period`, `_adjust_hour`, `_next_weekday`, `_parse_time`, `_parse_actions`, `_detect_risk_level`, `is_schedule_intent`, `parse`, `const:WEEKDAY_NAMES`, `const:DAY_OFFSET`, `const:PERIOD_HOUR_OFFSET`, `const:_CN_TO_DIGIT`, `const:_WEEKDAY_CN`，另有 3 项
- 直接静态依赖：`__future__`, `re`, `datetime`, `typing`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/engine/optimizer.py -->
### `python/engine/optimizer.py`

[打开文件](<../../python/engine/optimizer.py>)

- 分类/语言：`supporting-code` / Python
- 规模：175 行，6,811 bytes
- SHA-256：`71e19e8bc345321923f40971138c1267b89a8bdc41d480ab9e41e6d27430eddc`
- 源码事实摘要：Telemetry-driven DSL Optimizer
- 顶层符号/结构：`TelemetryAnalyzer`
- 直接静态依赖：`__future__`, `typing`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/engine/planner.py -->
### `python/engine/planner.py`

[打开文件](<../../python/engine/planner.py>)

- 分类/语言：`supporting-code` / Python
- 规模：379 行，14,036 bytes
- SHA-256：`1f6657926cf198fb83b7d8e0b880a1079a1b7f2a28e3b586426b7574e15a556a`
- 源码事实摘要：AI 规划引擎: 内部 Agent 闭环
- 顶层符号/结构：`PlanStep`, `Planner`, `get_planner`
- 直接静态依赖：`__future__`, `json`, `os`, `time`, `base64`, `uuid`, `typing`, `dataclasses`, `engine.store`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/engine/robot_converter.py -->
### `python/engine/robot_converter.py`

[打开文件](<../../python/engine/robot_converter.py>)

- 分类/语言：`supporting-code` / Python
- 规模：338 行，10,637 bytes
- SHA-256：`7316dcd7c7bb45f71fafc22d8674a96b19bd12e0a13f12456f279ed0e18b6f6a`
- 源码事实摘要：DSL → Robot Framework .robot 转换器。
- 顶层符号/结构：`_escape_robot_arg`, `_parse_dsl_line`, `_to_rpa_hotkey`, `dsl_to_robot`, `dsl_to_robot_file`, `const:_KEYWORD_MAP`
- 直接静态依赖：`re`, `os`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/engine/robot_runner.py -->
### `python/engine/robot_runner.py`

[打开文件](<../../python/engine/robot_runner.py>)

- 分类/语言：`e2e-or-harness` / Python
- 规模：158 行，5,789 bytes
- SHA-256：`c1b6bf5d6368c5b2449833a72309816f8968bce1254294b4255406ca48d58b77`
- 源码事实摘要：Robot Framework 执行器 — 将 DSL 转为 .robot 并执行。
- 顶层符号/结构：`run_robot`
- 直接静态依赖：`os`, `tempfile`, `time`, `xml.etree.ElementTree`, `engine.robot_converter`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/engine/scheduler.py -->
### `python/engine/scheduler.py`

[打开文件](<../../python/engine/scheduler.py>)

- 分类/语言：`supporting-code` / Python
- 规模：153 行，4,580 bytes
- SHA-256：`f8e0145b56994f9fe34a29d95477facad7b3b813cc7b7657daa460b4eaa56748`
- 源码事实摘要：节点调度系统: 基于 APScheduler 的任务队列管理
- 顶层符号/结构：`TaskPriority`, `ScheduledTask`, `TaskScheduler`
- 直接静态依赖：`__future__`, `threading`, `time`, `dataclasses`, `typing`, `enum`, `apscheduler.schedulers.background`, `apscheduler.executors.pool`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/engine/scheduler_daemon.py -->
### `python/engine/scheduler_daemon.py`

[打开文件](<../../python/engine/scheduler_daemon.py>)

- 分类/语言：`supporting-code` / Python
- 规模：288 行，11,041 bytes
- SHA-256：`278a3508da398566f15d4336816bd38bd9e7b95fd432c69dcd97776444b017bf`
- 源码事实摘要：调度器守护进程 — 基于 APScheduler
- 顶层符号/结构：`SchedulerDaemon`, `main`
- 直接静态依赖：`json`, `sys`, `os`, `time`, `threading`, `logging`, `uuid`, `datetime`, `typing`, `apscheduler.schedulers.background`, `apscheduler.triggers.cron`, `apscheduler.triggers.interval`, `engine.condition_evaluator`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/engine/secure_store.py -->
### `python/engine/secure_store.py`

[打开文件](<../../python/engine/secure_store.py>)

- 分类/语言：`supporting-code` / Python
- 规模：88 行，2,611 bytes
- SHA-256：`e912c50ae68fc3542e0b9bf43aefa8efef5d7cd42d32ec2ca245d2c76e667388`
- 源码事实摘要：Windows DPAPI helpers for secrets persisted by the legacy execution store.
- 顶层符号/结构：`_DataBlob`, `_blob`, `_require_windows`, `is_protected`, `protect_text`, `unprotect_text`, `const:PREFIX`, `const:_DESCRIPTION`, `const:_ENTROPY`, `const:_CRYPTPROTECT_UI_FORBIDDEN`
- 直接静态依赖：`__future__`, `base64`, `ctypes`, `hashlib`, `os`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/engine/store.py -->
### `python/engine/store.py`

[打开文件](<../../python/engine/store.py>)

- 分类/语言：`supporting-code` / Python
- 规模：388 行，15,283 bytes
- SHA-256：`88e484c76bee5de5ac33d672e322a8daaa053fb9b8aacc0624c9a9293f008dc9`
- 源码事实摘要：执行状态持久化存储: SQLite 后端
- 顶层符号/结构：`_get_db_path`, `Checkpoint`, `TelemetryEntry`, `LearnedTarget`, `ExecutionStore`, `get_store`, `const:_DB_PATH`
- 直接静态依赖：`__future__`, `json`, `os`, `sqlite3`, `threading`, `time`, `typing`, `dataclasses`, `pathlib`, `.secure_store`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/engine/trigger_bridge.py -->
### `python/engine/trigger_bridge.py`

[打开文件](<../../python/engine/trigger_bridge.py>)

- 分类/语言：`supporting-code` / Python
- 规模：44 行，1,344 bytes
- SHA-256：`dcb09a856a15de52adaacdd263d43d00b62160699103b42437c06d3800240e01`
- 源码事实摘要：TriggerEngine → stdout JSON 事件桥接
- 顶层符号/结构：`setup_trigger_bridge`
- 直接静态依赖：`json`, `sys`, `typing`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/engine/triggers.py -->
### `python/engine/triggers.py`

[打开文件](<../../python/engine/triggers.py>)

- 分类/语言：`supporting-code` / Python
- 规模：518 行，19,482 bytes
- SHA-256：`692a771fa38380b22c19e26aaffd876c38f07313c8902947655156386d140494`
- 源码事实摘要：反应式事件引擎 — Trigger 定义、监控线程、执行回调
- 顶层符号/结构：`TriggerCategory`, `TriggerSpec`, `TriggerInstance`, `trigger_type`, `list_trigger_types`, `TriggerEngine`, `_watch_file_created`, `_watch_process_exit`, `_watch_process_start`, `_watch_window_open`, `_watch_clipboard`, `_watch_network`, `_watch_timer_interval`, `_reg_file_created`，另有 10 项
- 直接静态依赖：`__future__`, `os`, `time`, `json`, `threading`, `logging`, `pathlib`, `typing`, `dataclasses`, `enum`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/engine/unified_entry.py -->
### `python/engine/unified_entry.py`

[打开文件](<../../python/engine/unified_entry.py>)

- 分类/语言：`supporting-code` / Python
- 规模：107 行，3,499 bytes
- SHA-256：`0b41b6ad77273ff3fc76977128481563b6781e5f690129eb3db8385ba890ce08`
- 源码事实摘要：统一入口：将 DSL 文本和 FlowScript JSON 统一路由到 DSL 执行器。
- 顶层符号/结构：`run`, `_run_dsl`, `_run_flow`, `quick_validate`
- 直接静态依赖：`__future__`, `json`, `time`, `logging`, `typing`, `engine.executor`, `engine.dsl_flow_converter`, `engine.dsl_parser`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/features/__init__.py -->
### `python/features/__init__.py`

[打开文件](<../../python/features/__init__.py>)

- 分类/语言：`supporting-code` / Python
- 规模：5 行，202 bytes
- SHA-256：`0be133d007f1f77cc809a0d3fc840d335a792d3cb419ab1b5d0f565b1f22d02d`
- 源码事实摘要：Lobster 功能注册系统
- 顶层符号/结构：—
- 直接静态依赖：`.registry`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/features/_all.py -->
### `python/features/_all.py`

[打开文件](<../../python/features/_all.py>)

- 分类/语言：`supporting-code` / Python
- 规模：51 行，1,738 bytes
- SHA-256：`bce89462d3e7278fddc66ce61e2c58dec9204752e8fdff5aba0ecbcf9fa53a70`
- 源码事实摘要：单一入口：声明所有功能模块（但延迟加载）。
- 顶层符号/结构：`const:_FEATURE_MODULES`
- 直接静态依赖：`features.registry`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/features/_feature_template.py -->
### `python/features/_feature_template.py`

[打开文件](<../../python/features/_feature_template.py>)

- 分类/语言：`supporting-code` / Python
- 规模：93 行，3,402 bytes
- SHA-256：`305d16d85b998bd9e73deccaa513a13cde3b925742a57e847d39c8b90c7f37ec`
- 源码事实摘要：新增功能模板
- 顶层符号/结构：`my_simple_action`, `my_perception`, `my_macro`
- 直接静态依赖：`features.registry`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/features/_utils.py -->
### `python/features/_utils.py`

[打开文件](<../../python/features/_utils.py>)

- 分类/语言：`supporting-code` / Python
- 规模：31 行，863 bytes
- SHA-256：`e82da888239ed0114aec6cb56f2562647a6559754e57501936a8b4d16f6af3eb`
- 源码事实摘要：功能模块共享工具函数
- 顶层符号/结构：`parse_region`, `resolve_coord`, `sim_delay`
- 直接静态依赖：`typing`, `random`, `time`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/features/a11y_features.py -->
### `python/features/a11y_features.py`

[打开文件](<../../python/features/a11y_features.py>)

- 分类/语言：`supporting-code` / Python
- 规模：205 行，6,959 bytes
- SHA-256：`739dec4ba085f6b07739aba98bdadc2f52f8368bcf05fc764df75e27c63d093c`
- 源码事实摘要：无障碍树功能
- 顶层符号/结构：`_get_fg_control`, `_get_name`, `_get_role`, `_get_bounds`, `_is_visible`, `_walk_tree`, `a11y_get_tree`, `_find_controls`, `_control_to_dict`, `a11y_find_element`, `a11y_click_element`
- 直接静态依赖：`features.registry`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/features/action_features.py -->
### `python/features/action_features.py`

[打开文件](<../../python/features/action_features.py>)

- 分类/语言：`supporting-code` / Python
- 规模：231 行，10,040 bytes
- SHA-256：`886468ecb05b041cef3515a5b8b426a0f3a8b0349938cb9d48d5ec39ddd06474`
- 源码事实摘要：动作类功能注册
- 顶层符号/结构：`_do`, `click_target`, `type_text`, `scroll`, `swipe_action`, `drag`, `hotkey`, `type_clipboard`
- 直接静态依赖：`re`, `time`, `features.registry`, `features._utils`, `features.input_features`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/features/action_map.py -->
### `python/features/action_map.py`

[打开文件](<../../python/features/action_map.py>)

- 分类/语言：`supporting-code` / Python
- 规模：258 行，8,444 bytes
- SHA-256：`e29638a12187915f8ab45054d2e54995cbfe8551591855356b0dac57e1564fd3`
- 源码事实摘要：动作名称 → @feature 名称映射表
- 顶层符号/结构：`_rpa_handler`, `resolve_feature`, `make_action_handler`, `_eval_condition`, `const:ACTION_TO_FEATURE`, `const:_RPA_ACTION_MAP`, `const:STEP_TYPE_TO_FEATURE`
- 直接静态依赖：`__future__`, `logging`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/features/aggregate_features.py -->
### `python/features/aggregate_features.py`

[打开文件](<../../python/features/aggregate_features.py>)

- 分类/语言：`supporting-code` / Python
- 规模：46 行，2,578 bytes
- SHA-256：`f015a169ec4d74607cdb9a08f5a702e84b0ee7f43f6032210e5ffc7ee3c2290f`
- 源码事实摘要：聚合功能：将相关子功能合并为单一入口，通过 action 参数选择具体操作。
- 顶层符号/结构：`wait_condition`
- 直接静态依赖：`features.registry`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/features/ai_debug_features.py -->
### `python/features/ai_debug_features.py`

[打开文件](<../../python/features/ai_debug_features.py>)

- 分类/语言：`supporting-code` / Python
- 规模：788 行，33,775 bytes
- SHA-256：`639f6662c5bf544b81eeae0053a8659962fcac7fa9e42df477bee7440d7fd7ce`
- 源码事实摘要：AI 规划 + 调试功能注册
- 顶层符号/结构：`ai_generate_dsl`, `dsl_to_graph`, `graph_to_dsl`, `dsl_to_flow_feature`, `flow_to_dsl_feature`, `create_flow_from_dsl`, `debug_get_logs`, `debug_run_tests`, `debug_list_features`, `debug_validate_dsl`, `debug_system_info`, `debug_generate_test_flow`, `_parse_template_metadata`, `_parse_flow_metadata`，另有 9 项
- 直接静态依赖：`json`, `os`, `sys`, `time`, `pathlib`, `typing`, `features.registry`, `features.state`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/features/ai_plan_features.py -->
### `python/features/ai_plan_features.py`

[打开文件](<../../python/features/ai_plan_features.py>)

- 分类/语言：`supporting-code` / Python
- 规模：44 行，1,858 bytes
- SHA-256：`ba022568051ed067f956e56f8297cdfb4d90f69b1eb475706fa1a607154bab44`
- 源码事实摘要：AI 规划执行功能注册
- 顶层符号/结构：`ai_plan_and_execute`, `ai_plan_history`
- 直接静态依赖：`features.registry`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/features/browser_features.py -->
### `python/features/browser_features.py`

[打开文件](<../../python/features/browser_features.py>)

- 分类/语言：`supporting-code` / Python
- 规模：1756 行，66,287 bytes
- SHA-256：`28d5e6378e34f0f33976f9282187be56681a66466053f5f62de744d641880c08`
- 源码事实摘要：浏览器交互功能 — 通过 Playwright 实现全面的 Web 控制。
- 顶层符号/结构：`_ensure_playwright`, `_find_chrome`, `_is_port_open`, `_is_cdp_endpoint`, `_start_chrome_debug`, `_cleanup_chrome_debug`, `_get_page`, `_connect_browser`, `_cleanup`, `_full_cleanup`, `_not_installed_msg`, `_pick_another_page`, `_pwrap`, `browser_connect`，另有 72 项
- 直接静态依赖：`atexit`, `json`, `os`, `shutil`, `socket`, `subprocess`, `tempfile`, `threading`, `time`, `typing`, `features.registry`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/features/call_feature.py -->
### `python/features/call_feature.py`

[打开文件](<../../python/features/call_feature.py>)

- 分类/语言：`supporting-code` / Python
- 规模：26 行，798 bytes
- SHA-256：`5966b620338a75ce79465589e575341dcb19642dad07abe3c765764c4c389999`
- 源码事实摘要：调用单个 feature 并输出结果（one-shot）
- 顶层符号/结构：—
- 直接静态依赖：`sys`, `json`, `pathlib`, `features._all`, `features.registry`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/features/camera_features.py -->
### `python/features/camera_features.py`

[打开文件](<../../python/features/camera_features.py>)

- 分类/语言：`supporting-code` / Python
- 规模：379 行，14,541 bytes
- SHA-256：`d545fe4242871d23bcf1a33e69ef2d5aca4c255e8564f8a0780abd63027a44a7`
- 源码事实摘要：摄像头功能
- 顶层符号/结构：`_opencv_available`, `camera_list`, `camera_capture`, `camera_record_start`, `camera_record_stop`, `camera_detect_motion`
- 直接静态依赖：`base64`, `io`, `os`, `time`, `threading`, `datetime`, `features.registry`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/features/canvas_features.py -->
### `python/features/canvas_features.py`

[打开文件](<../../python/features/canvas_features.py>)

- 分类/语言：`supporting-code` / Python
- 规模：115 行，4,470 bytes
- SHA-256：`d15838be800edd3567b59b2ca6065bf8c2b714347a2251cdaffb956e1980be26`
- 源码事实摘要：画布检测功能
- 顶层符号/结构：`_take_screenshot`, `_find_white_rect`, `find_canvas`
- 直接静态依赖：`features.registry`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/features/claude_code_features.py -->
### `python/features/claude_code_features.py`

[打开文件](<../../python/features/claude_code_features.py>)

- 分类/语言：`supporting-code` / Python
- 规模：119 行，5,042 bytes
- SHA-256：`848db03c0616d452a65cf8430f5ce932ba08cb0b7771fe2a22c82795d0327623`
- 源码事实摘要：Claude Code 委托功能
- 顶层符号/结构：`_detect_claude`, `claude_code_delegate`
- 直接静态依赖：`os`, `subprocess`, `sys`, `tempfile`, `time`, `features.registry`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/features/clipboard_features.py -->
### `python/features/clipboard_features.py`

[打开文件](<../../python/features/clipboard_features.py>)

- 分类/语言：`supporting-code` / Python
- 规模：185 行，6,943 bytes
- SHA-256：`8e6b6bc513124994a31f2cc7b5e827c6af03aafd06b05ab5bd5b43d921e50d32`
- 源码事实摘要：剪贴板增强功能（待完善 → 已实现）
- 顶层符号/结构：`clipboard_get_text`, `clipboard_get_image`, `clipboard_get_files`, `clipboard_clear`, `clipboard_set_text`
- 直接静态依赖：`base64`, `io`, `os`, `time`, `features.registry`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/features/dialog_features.py -->
### `python/features/dialog_features.py`

[打开文件](<../../python/features/dialog_features.py>)

- 分类/语言：`supporting-code` / Python
- 规模：170 行，5,799 bytes
- SHA-256：`e26dff8e222271962b857ee8336aa2479cec451d08445f6063273439374612bc`
- 源码事实摘要：对话框处理功能注册
- 顶层符号/结构：`handle_dialog`, `dialog_list`, `dialog_take_action`, `fill_save_dialog`
- 直接静态依赖：`logging`, `features.registry`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/features/dsl_features.py -->
### `python/features/dsl_features.py`

[打开文件](<../../python/features/dsl_features.py>)

- 分类/语言：`supporting-code` / Python
- 规模：227 行，9,519 bytes
- SHA-256：`33b7347a349d88ead744db7851dd3b099e8fcea90ceaeb0302fb616312f2b4ae`
- 源码事实摘要：DSL 执行功能注册
- 顶层符号/结构：`run_dsl`, `run_dsl_sync`, `run_dsl_robot`, `run_script`, `dsl_optimize_analyze`
- 直接静态依赖：`__future__`, `json`, `os`, `pathlib`, `features.registry`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/features/file_features.py -->
### `python/features/file_features.py`

[打开文件](<../../python/features/file_features.py>)

- 分类/语言：`supporting-code` / Python
- 规模：265 行，9,978 bytes
- SHA-256：`aaf98fceea8d3da46c84be251f9f398301c40088ff320269decd144d46e3727f`
- 源码事实摘要：文件操作功能
- 顶层符号/结构：`file_read`, `file_write`, `file_append`, `file_delete`, `file_list`, `file_exists`, `file_copy`, `file_move`, `file_edit`
- 直接静态依赖：`os`, `shutil`, `pathlib`, `features.registry`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/features/filedialog_features.py -->
### `python/features/filedialog_features.py`

[打开文件](<../../python/features/filedialog_features.py>)

- 分类/语言：`supporting-code` / Python
- 规模：322 行，11,179 bytes
- SHA-256：`5ffe11e690d8f9562ae724704f3861ec8e25b3a4f39f871a5f23e127b701430c`
- 源码事实摘要：文件对话框功能
- 顶层符号/结构：`_find_filename_combo`, `_find_filename_edit`, `_type_path_and_confirm`, `_find_file_dialog`, `_bring_dialog_to_front`, `_try_handle_overwrite`, `filedialog_open`, `filedialog_save`, `filedialog_folder`, `select_images`
- 直接静态依赖：`time`, `features.registry`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/features/flow_features.py -->
### `python/features/flow_features.py`

[打开文件](<../../python/features/flow_features.py>)

- 分类/语言：`supporting-code` / Python
- 规模：818 行，44,831 bytes
- SHA-256：`a8d877514dfe18fcdbaf20f01024f404710ac63fbc1751be2f05bfb7a578c2d3`
- 源码事实摘要：流程图脚本生成功能
- 顶层符号/结构：`_build_commands_section`, `_build_structured_types_section`, `_build_dsl_reference`, `_get_generation_prompt`, `_steps_to_dsl`, `_steps_to_pseudocode`, `_is_mimo_model`, `generate_flow_script`, `const:AGENT_CAPABILITIES`, `const:CONTROL_FLOW_CAPS`, `const:_DSL_REFERENCE_CACHE`, `const:_GEN_PROMPT_CACHE`, `const:_BUILD_GEN_PROMPT_TEMPLATE`, `const:STEP_TYPE_ALIASES`，另有 1 项
- 直接静态依赖：`json`, `os`, `re`, `time`, `features.registry`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/features/fusion_features.py -->
### `python/features/fusion_features.py`

[打开文件](<../../python/features/fusion_features.py>)

- 分类/语言：`supporting-code` / Python
- 规模：190 行，9,607 bytes
- SHA-256：`eaf0b8f6be03d7401bc3f2b4339f3a6cfa3fb185b635f52aaf65651731529e01`
- 源码事实摘要：多模态融合感知功能注册
- 顶层符号/结构：`find_element`, `wait_element`
- 直接静态依赖：`features.registry`, `features._utils`, `pathlib`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/features/healing_features.py -->
### `python/features/healing_features.py`

[打开文件](<../../python/features/healing_features.py>)

- 分类/语言：`supporting-code` / Python
- 规模：61 行，1,940 bytes
- SHA-256：`32f3b21ac45592596d50a94c612666e328afc30e0543bf96c0b8a78038ba58a9`
- 源码事实摘要：自愈管道功能注册
- 顶层符号/结构：`heal_reliability`, `heal_history`, `heal_reset_stats`
- 直接静态依赖：`features.registry`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/features/image_click_features.py -->
### `python/features/image_click_features.py`

[打开文件](<../../python/features/image_click_features.py>)

- 分类/语言：`supporting-code` / Python
- 规模：98 行，4,260 bytes
- SHA-256：`d7515825fd13c8c0458aa07f99de6d6fd82a7e8eff34398cd7f5791126b22150`
- 源码事实摘要：图像点击：多目标优先级图像匹配 + 点击
- 顶层符号/结构：`image_click`
- 直接静态依赖：`features.registry`, `features._utils`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/features/image_search_features.py -->
### `python/features/image_search_features.py`

[打开文件](<../../python/features/image_search_features.py>)

- 分类/语言：`supporting-code` / Python
- 规模：177 行，6,942 bytes
- SHA-256：`17871900d90e2fd18ce820df27882005c8e9b3508753eafd1bd9f1b54ccf8343`
- 源码事实摘要：自然语言图片搜索：根据自然语言描述在本地搜索匹配的图片文件
- 顶层符号/结构：`_extract_keywords`, `_score_match`, `search_images`, `const:_STOPWORDS`, `const:_KEEP_WORDS`
- 直接静态依赖：`os`, `re`, `pathlib`, `difflib`, `features.registry`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/features/input_features.py -->
### `python/features/input_features.py`

[打开文件](<../../python/features/input_features.py>)

- 分类/语言：`supporting-code` / Python
- 规模：230 行，8,634 bytes
- SHA-256：`9872b7ba14b3906f2152cc9e2a2b195abd103607b58f919a0104b09f72e36809`
- 源码事实摘要：高级输入功能
- 顶层符号/结构：`_do`, `_find_target_coords`, `right_click`, `hover`, `mouse_move`, `triple_click`, `select_text`, `middle_click`, `get_cursor_pos`
- 直接静态依赖：`time`, `random`, `features.registry`, `features._utils`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/features/list_features.py -->
### `python/features/list_features.py`

[打开文件](<../../python/features/list_features.py>)

- 分类/语言：`supporting-code` / Python
- 规模：10 行，442 bytes
- SHA-256：`456455855028bf1cfad98d9bfd689bc54143093d4e36d2665bd6d138a6f9023b`
- 源码事实摘要：Export all registered features as JSON array (one-shot)
- 顶层符号/结构：—
- 直接静态依赖：`sys`, `json`, `pathlib`, `features._all`, `features.registry`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/features/macro_features.py -->
### `python/features/macro_features.py`

[打开文件](<../../python/features/macro_features.py>)

- 分类/语言：`supporting-code` / Python
- 规模：431 行，15,560 bytes
- SHA-256：`cb38214f0862a290968af9694755a5d59e74557af69668560062c380334e4b49`
- 源码事实摘要：宏录制播放功能
- 顶层符号/结构：`_macro_path`, `macro_playback`, `macro_list`, `_record_pynput`, `_stop_pynput`, `macro_start_recording`, `macro_stop_recording`, `macro_delete`, `macro_get_events`, `macro_convert_to_flow`, `const:_MACRO_DIR`, `const:_HAS_AHK`, `const:_HAS_PYNPUT`
- 直接静态依赖：`json`, `os`, `time`, `threading`, `sys`, `datetime`, `pathlib`, `features.registry`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/features/menu_features.py -->
### `python/features/menu_features.py`

[打开文件](<../../python/features/menu_features.py>)

- 分类/语言：`supporting-code` / Python
- 规模：76 行，2,867 bytes
- SHA-256：`2e0e25d84e45a1a5535e971388ba207d666dba23aad34c1d0a5d8c5bb784bf34`
- 源码事实摘要：菜单操作功能注册
- 顶层符号/结构：`menu_click`, `menu_structure`
- 直接静态依赖：`logging`, `features.registry`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/features/multimedia_features.py -->
### `python/features/multimedia_features.py`

[打开文件](<../../python/features/multimedia_features.py>)

- 分类/语言：`supporting-code` / Python
- 规模：230 行，8,454 bytes
- SHA-256：`6ec45c81adcbb3f693063fcdcb00429820fbefa3a6784cae2fac5934fe63d1f2`
- 源码事实摘要：多媒体功能
- 顶层符号/结构：`_ffmpeg_available`, `audio_volume_get`, `screenshot_region_live`, `screen_record_start`, `screen_record_stop`
- 直接静态依赖：`base64`, `io`, `os`, `signal`, `subprocess`, `time`, `threading`, `datetime`, `features.registry`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/features/network_features.py -->
### `python/features/network_features.py`

[打开文件](<../../python/features/network_features.py>)

- 分类/语言：`supporting-code` / Python
- 规模：161 行，5,775 bytes
- SHA-256：`57c738336b6ed9589ec25cb0f1c2c4c4061052159e51866f1ac3376e89791c2a`
- 源码事实摘要：网络功能
- 顶层符号/结构：`_netsh`, `network_status`, `wifi_list`, `wifi_connect`, `wifi_disconnect`
- 直接静态依赖：`socket`, `subprocess`, `re`, `json`, `features.registry`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/features/notification_features.py -->
### `python/features/notification_features.py`

[打开文件](<../../python/features/notification_features.py>)

- 分类/语言：`supporting-code` / Python
- 规模：93 行，3,400 bytes
- SHA-256：`7d067b64bb500c7f555bbdac5c42da1d36d35cd681c73cd7c14396adc6bf5464`
- 源码事实摘要：通知功能
- 顶层符号/结构：`send_notification`, `const:_NOTIFICATION_ID`
- 直接静态依赖：`features.registry`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/features/perception_features.py -->
### `python/features/perception_features.py`

[打开文件](<../../python/features/perception_features.py>)

- 分类/语言：`supporting-code` / Python
- 规模：776 行，34,372 bytes
- SHA-256：`212c94438d34a508bd372e55c313ef4162b0bfc6b0dde7a6a84240a60e9389c5`
- 源码事实摘要：感知类功能注册
- 顶层符号/结构：`region_select`, `_do_capture_multi`, `capture_multi`, `ocr_find_text`, `ocr_extract_all`, `find_image`, `detect_color`, `detect_progress`, `screenshot`, `popup_close`, `progress_wait`, `progress_init`, `progress_relocate`, `color_check`，另有 5 项
- 直接静态依赖：`features.registry`, `features._utils`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/features/persistence_features.py -->
### `python/features/persistence_features.py`

[打开文件](<../../python/features/persistence_features.py>)

- 分类/语言：`supporting-code` / Python
- 规模：300 行，10,780 bytes
- SHA-256：`a07125284a11ee3704f12da2c21008e09464cf1879def68f9ef6b25aa328876e`
- 源码事实摘要：持久化功能注册
- 顶层符号/结构：`exec_list_checkpoints`, `exec_get_checkpoint`, `exec_delete_checkpoint`, `exec_telemetry_stats`, `exec_vault_set`, `exec_vault_get`, `exec_vault_list`, `learned_save`, `learned_get`, `learned_list`, `learned_delete`, `learned_record`
- 直接静态依赖：`features.registry`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/features/plugin_features.py -->
### `python/features/plugin_features.py`

[打开文件](<../../python/features/plugin_features.py>)

- 分类/语言：`supporting-code` / Python
- 规模：46 行，1,626 bytes
- SHA-256：`f07f8a0d8211490b0da2666b1ed3c7e79f2be956d757c7af6f1f27e2f493d66e`
- 源码事实摘要：插件管理功能注册
- 顶层符号/结构：`plugin_load`, `plugin_list`, `plugin_unload`
- 直接静态依赖：`features.registry`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/features/program_registry.py -->
### `python/features/program_registry.py`

[打开文件](<../../python/features/program_registry.py>)

- 分类/语言：`supporting-code` / Python
- 规模：951 行，38,724 bytes
- SHA-256：`06ee0d1d898ddd2b83ee95b201a1cec7f6cec62143e152a7cdc72fcc2bbe42f5`
- 源码事实摘要：程序别名注册表 — 持久化程序名→完整路径的映射。
- 顶层符号/结构：`ProgramRegistry`, `resolve_program`, `get_program_registry`, `program_refresh`, `program_resolve`, `program_list`, `program_add_alias`, `const:_DATA_DIR`, `const:_CACHE_PATH`, `const:_SCAN_INTERVAL`, `const:_START_MENU_DIRS`, `const:_DESKTOP_DIRS`, `const:_LOCALAPPDATA_PROGRAMS`, `const:_COMMON_APP_DIRS`，另有 1 项
- 直接静态依赖：`__future__`, `json`, `logging`, `os`, `subprocess`, `sys`, `threading`, `time`, `pathlib`, `features.registry`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/features/registry.py -->
### `python/features/registry.py`

[打开文件](<../../python/features/registry.py>)

- 分类/语言：`supporting-code` / Python
- 规模：545 行，22,135 bytes
- SHA-256：`210325968447787b55557e1dedca63f938dd29f1b8290c1c989075692a2131b8`
- 源码事实摘要：╔══════════════════════════════════════════════════════════════════╗
- 顶层符号/结构：`FeatureCategory`, `ParamSpec`, `PortDef`, `VisualMeta`, `TestCase`, `FeatureSpec`, `FeatureRegistry`, `feature`, `_convert_type`, `const:CAT_NAMES`, `const:P`, `const:TC`, `const:F`
- 直接静态依赖：`__future__`, `importlib`, `importlib.util`, `json`, `os`, `sys`, `time`, `traceback`, `dataclasses`, `typing`, `enum`, `pathlib`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/features/scheduler_features.py -->
### `python/features/scheduler_features.py`

[打开文件](<../../python/features/scheduler_features.py>)

- 分类/语言：`supporting-code` / Python
- 规模：295 行，11,908 bytes
- SHA-256：`4ab4744f83811df965f5241ccc8f1f9de854b10621167bb5e46e4c961ba8e5ad`
- 源码事实摘要：调度器管理功能注册
- 顶层符号/结构：`_get_schedules_path`, `_read_schedules`, `_write_schedules`, `_gen_id`, `scheduler_list`, `scheduler_get`, `scheduler_create`, `scheduler_update`, `scheduler_delete`, `scheduler_condition_types`, `scheduler_trigger_types`, `quick_schedule`, `const:TRIGGER_TYPES`
- 直接静态依赖：`__future__`, `json`, `os`, `time`, `pathlib`, `features.registry`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/features/software_features.py -->
### `python/features/software_features.py`

[打开文件](<../../python/features/software_features.py>)

- 分类/语言：`supporting-code` / Python
- 规模：176 行，6,141 bytes
- SHA-256：`6aee0b51ea32d493f32401fc3722483583a42b99127832312e7095433f414ab5`
- 源码事实摘要：Software control features — control 灵动脚本 via its HTTP API (127.0.0.1:5877).
- 顶层符号/结构：`_api_post`, `software_new_script`, `software_add_node`, `software_add_edge`, `software_load_script`, `software_auto_layout`, `software_set_view`, `software_get_state`, `software_save_script`, `software_run_script`, `const:_API_BASE`
- 直接静态依赖：`logging`, `functools`, `requests`, `features.registry`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/features/state.py -->
### `python/features/state.py`

[打开文件](<../../python/features/state.py>)

- 分类/语言：`supporting-code` / Python
- 规模：39 行，983 bytes
- SHA-256：`59a976e4ead4fdc66e52d5d599053330c2dbf71515001fedeb6fa14b3a2a7acd`
- 源码事实摘要：共享运行时状态（日志缓存、变量存储等）
- 顶层符号/结构：`push_log`, `get_logs`, `set_var`, `get_var`, `list_vars`, `clear_vars`, `const:_MAX_LOGS`
- 直接静态依赖：`time`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/features/system_control_features.py -->
### `python/features/system_control_features.py`

[打开文件](<../../python/features/system_control_features.py>)

- 分类/语言：`supporting-code` / Python
- 规模：547 行，20,851 bytes
- SHA-256：`4371de6a66048663e7fa7a70135b6d14db901a8e2e43cf8b070c8bf98f1d4895`
- 源码事实摘要：系统控制功能
- 顶层符号/结构：`volume_set`, `volume_mute`, `display_brightness`, `lock_workstation`, `battery_status`, `empty_recycle_bin`, `shutdown_fn`, `reboot_fn`, `logoff_fn`, `hibernate_fn`, `kill_process`, `wait_process_start`, `wait_process_exit`, `set_wallpaper`，另有 3 项
- 直接静态依赖：`time`, `features.registry`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/features/system_features.py -->
### `python/features/system_features.py`

[打开文件](<../../python/features/system_features.py>)

- 分类/语言：`supporting-code` / Python
- 规模：511 行，20,102 bytes
- SHA-256：`91b0690dd9fa394dbc4869ac2900c6fa07dc826b26febc87e13d411a235e74dd`
- 源码事实摘要：系统功能注册
- 顶层符号/结构：`_ensure_dpi_awareness`, `detect_process`, `find_processes`, `focus_window`, `list_windows`, `_get_visible_window_titles`, `get_active_window`, `_find_matching_pids`, `_try_focus_existing`, `launch_program`, `run_command`, `const:_DPI_AWARENESS_SET`
- 直接静态依赖：`logging`, `features.registry`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/features/test_flow_features.py -->
### `python/features/test_flow_features.py`

[打开文件](<../../python/features/test_flow_features.py>)

- 分类/语言：`test` / Python
- 规模：144 行，4,871 bytes
- SHA-256：`6e236c57c7eb9f2e4ed428026f7115209f5a085e3934acfe64ea097b801d3c13`
- 源码事实摘要：保存测试流程到 tests/flows/ 目录，用于批量测试和回归测试
- 顶层符号/结构：`save_test_flow`, `list_test_flows`, `run_test_flow`, `const:PROJECT_ROOT`, `const:TEST_FLOWS_DIR`
- 直接静态依赖：`json`, `os`, `time`, `pathlib`, `features.registry`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/features/timer_features.py -->
### `python/features/timer_features.py`

[打开文件](<../../python/features/timer_features.py>)

- 分类/语言：`supporting-code` / Python
- 规模：204 行，7,468 bytes
- SHA-256：`0e6eec51d6781f169a0545377e2699cb02cf9475216ab36b0cda63c16283d2e9`
- 源码事实摘要：定时条件功能
- 顶层符号/结构：`_poll`, `timer_wait_file`, `timer_wait_process`, `timer_wait_network`, `timer_wait_time`, `wait_action`, `timer_countdown`
- 直接静态依赖：`time`, `os`, `datetime`, `features.registry`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/features/trigger_features.py -->
### `python/features/trigger_features.py`

[打开文件](<../../python/features/trigger_features.py>)

- 分类/语言：`supporting-code` / Python
- 规模：127 行，4,224 bytes
- SHA-256：`aecdcac8efc11b8f905eb6069aae955e9876c752a4738a9842c4deb057ec25c4`
- 源码事实摘要：触发器管理功能注册
- 顶层符号/结构：`trigger_list_types`, `trigger_list`, `trigger_stop`, `trigger_stop_all`, `trigger_get`, `crystallized_list`, `skill_tree_get`
- 直接静态依赖：`features.registry`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/features/variable_features.py -->
### `python/features/variable_features.py`

[打开文件](<../../python/features/variable_features.py>)

- 分类/语言：`supporting-code` / Python
- 规模：51 行，1,933 bytes
- SHA-256：`b2c317b3f52fb02503aaaf57337d7b30fa2abec64e39de9bb67479ed59155acd`
- 源码事实摘要：变量功能注册
- 顶层符号/结构：`set_variable`, `get_variable`
- 直接静态依赖：`features.registry`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/features/vlm_features.py -->
### `python/features/vlm_features.py`

[打开文件](<../../python/features/vlm_features.py>)

- 分类/语言：`supporting-code` / Python
- 规模：75 行，3,088 bytes
- SHA-256：`666e0d40be061838d4727988150a8f85662ae44ce2b1609d0962c73bb505e969`
- 源码事实摘要：VLM 视觉语言模型功能注册
- 顶层符号/结构：`_make_error_result`, `vlm_analyze`
- 直接静态依赖：`os`, `time`, `features.registry`, `providers.vlm`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/features/web_features.py -->
### `python/features/web_features.py`

[打开文件](<../../python/features/web_features.py>)

- 分类/语言：`supporting-code` / Python
- 规模：137 行，5,720 bytes
- SHA-256：`9f15324b746ef73a607fd9fac96719ae57454ae0b8b692bee3fb7a3c48f6a992`
- 源码事实摘要：Web 浏览器自动化功能注册
- 顶层符号/结构：`_ensure_browser`, `_close_browser`, `web_browser_execute`
- 直接静态依赖：`__future__`, `logging`, `time`, `typing`, `features.registry`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/features/window_features.py -->
### `python/features/window_features.py`

[打开文件](<../../python/features/window_features.py>)

- 分类/语言：`supporting-code` / Python
- 规模：229 行，7,530 bytes
- SHA-256：`23603b466dd42f557f23fc51205d1f2b36e02f4435793136155d959dc4482a5c`
- 源码事实摘要：窗口操作功能
- 顶层符号/结构：`_find_hwnd`, `_show_window`, `window_minimize`, `window_maximize`, `window_restore`, `window_resize`, `window_snap`, `window_close`
- 直接静态依赖：`time`, `features.registry`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/interaction/__init__.py -->
### `python/interaction/__init__.py`

[打开文件](<../../python/interaction/__init__.py>)

- 分类/语言：`supporting-code` / Python
- 规模：4 行，127 bytes
- SHA-256：`73aecfa43d635a24ff22ebc5aa13f26f95feb8ea461baa43bce2c1c37612ef63`
- 源码事实摘要：Python 包 python.interaction 的初始化与公开边界。
- 顶层符号/结构：—
- 直接静态依赖：`.wheel_selector`, `.actions`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/interaction/actions.py -->
### `python/interaction/actions.py`

[打开文件](<../../python/interaction/actions.py>)

- 分类/语言：`supporting-code` / Python
- 规模：787 行，30,309 bytes
- SHA-256：`62bd47a5de387157f2e447053ac1a25dc446f2b8346ae5dad1ef122064a6152a`
- 源码事实摘要：ActionHandler: DSL 执行器的底层动作回调。
- 顶层符号/结构：`set_uia_enabled`, `is_uia_enabled`, `set_screen_scale`, `_ensure_win32`, `_ensure_uia`, `_ensure_pyautogui`, `_win32_set_cursor`, `_win32_click`, `_win32_scroll`, `_win32_screenshot`, `_win32_hotkey`, `_uia_get_foreground_root`, `_uia_find_control`, `_uia_click_by_name`，另有 5 项
- 直接静态依赖：`logging`, `os`, `time`, `pathlib`, `tempfile`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/interaction/wheel_selector.py -->
### `python/interaction/wheel_selector.py`

[打开文件](<../../python/interaction/wheel_selector.py>)

- 分类/语言：`supporting-code` / Python
- 规模：165 行，5,240 bytes
- SHA-256：`5258f09665fb69e3f39893e2b15edca596f2c67f7ab2ac902414478bf9c5cddb`
- 源码事实摘要：Wheel Selector: 全屏遮罩，滚轮/方向键切换选项，回车确认
- 顶层符号/结构：`WheelSelector`, `_safe_destroy`, `_ipc_main`
- 直接静态依赖：`__future__`, `tkinter`, `json`, `sys`, `typing`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/lobster_mcp/__init__.py -->
### `python/lobster_mcp/__init__.py`

[打开文件](<../../python/lobster_mcp/__init__.py>)

- 分类/语言：`supporting-code` / Python
- 规模：1 行，78 bytes
- SHA-256：`f0b8f7831a54bbf3771ce15d6d1f8e7475b5033b54e502beb6ea3c87a009633d`
- 源码事实摘要：Legacy feature-registry MCP server, separate from the official mcp SDK.
- 顶层符号/结构：—
- 直接静态依赖：—
- API/IPC 入口：—

<!-- PROJECT-FILE: python/lobster_mcp/server.py -->
### `python/lobster_mcp/server.py`

[打开文件](<../../python/lobster_mcp/server.py>)

- 分类/语言：`supporting-code` / Python
- 规模：415 行，14,193 bytes
- SHA-256：`2813022ec21a5d5131425345b3a8448b1ee0241b86f687c43f034e6119306284`
- 源码事实摘要：Lobster MCP Server v2 — 自给自足版
- 顶层符号/结构：`_create_executor`, `handle_tool_call`, `_exec_run_dsl`, `_exec_run_dsl_sync`, `_exec_dsl_to_flow`, `_exec_flow_to_dsl`, `_exec_create_flow_from_dsl`, `_format_result`, `_proxy_to_backend`, `_build_tools_list`, `_write_message`, `_send_notification`, `_notification_worker`, `_start_notifications`，另有 9 项
- 直接静态依赖：`__future__`, `json`, `os`, `sys`, `time`, `traceback`, `threading`, `pathlib`, `features._all`, `features.registry`, `engine.dsl_parser`, `engine.executor`, `engine.dsl_flow_converter`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/macros/录制_2026-6-8 18-40-26.json -->
### `python/macros/录制_2026-6-8 18-40-26.json`

[打开文件](<../../python/macros/录制_2026-6-8 18-40-26.json>)

- 分类/语言：`configuration` / JSON
- 规模：651 行，10,381 bytes
- SHA-256：`51debd2cf34448bd1d65a98c327d790c85de1c5453cd43a53200095d17b5a9db`
- 源码事实摘要：录制_2026-6-8 18-40-26
- 顶层符号/结构：`key:name`, `key:events`, `key:duration`, `key:created`, `key:include_mouse`, `key:include_keys`
- 直接静态依赖：—
- API/IPC 入口：—

<!-- PROJECT-FILE: python/perception/__init__.py -->
### `python/perception/__init__.py`

[打开文件](<../../python/perception/__init__.py>)

- 分类/语言：`supporting-code` / Python
- 规模：4 行，329 bytes
- SHA-256：`a1630b284d5e2c9d06efc15997dfdd2daa40d76560480862561faca25fb6351d`
- 源码事实摘要：Python 包 python.perception 的初始化与公开边界。
- 顶层符号/结构：—
- 直接静态依赖：`.vision`, `.ocr`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/perception/app_adapter.py -->
### `python/perception/app_adapter.py`

[打开文件](<../../python/perception/app_adapter.py>)

- 分类/语言：`supporting-code` / Python
- 规模：323 行，11,930 bytes
- SHA-256：`f7b14d44a18c76f464bd87cd6b3a180c0012b0514a1169d6cc58c610a2fc8dd5`
- 源码事实摘要：AppAdapter 基类 — 桌面应用适配器框架
- 顶层符号/结构：`AdapterWindowInfo`, `AppAdapter`
- 直接静态依赖：`__future__`, `os`, `subprocess`, `time`, `logging`, `typing`, `dataclasses`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/perception/fusion.py -->
### `python/perception/fusion.py`

[打开文件](<../../python/perception/fusion.py>)

- 分类/语言：`supporting-code` / Python
- 规模：164 行，5,730 bytes
- SHA-256：`43e2df83ce681d8c8d3dfc3fb56799fbac8a4ff2b7fc059b8fc3b0a0e01ae082`
- 源码事实摘要：Local fusion resolver: UI Automation → OCR → template matching.
- 顶层符号/结构：`FusionResult`, `LocalFusionEngine`, `get_fusion_engine`
- 直接静态依赖：`__future__`, `time`, `dataclasses`, `pathlib`, `typing`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/perception/ocr.py -->
### `python/perception/ocr.py`

[打开文件](<../../python/perception/ocr.py>)

- 分类/语言：`supporting-code` / Python
- 规模：664 行，24,588 bytes
- SHA-256：`c1c5065659689fbc4e21135929620edbd2d1a7ca0cb7a79c40e434005589b643`
- 源码事实摘要：感知层: OCR 文字识别模块
- 顶层符号/结构：`_fuzzy_match`, `OCRResult`, `UmiOCRBackend`, `WindowsOcrBackend`, `OCREngine`, `MultiBackendOCR`
- 直接静态依赖：`__future__`, `base64`, `re`, `time`, `typing`, `cv2`, `numpy`, `perception.vision`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/perception/vision.py -->
### `python/perception/vision.py`

[打开文件](<../../python/perception/vision.py>)

- 分类/语言：`supporting-code` / Python
- 规模：547 行，19,438 bytes
- SHA-256：`3fd27560adb8824d858d144d658ed0102f6b34ba4d25a737c4bc0a401bcbe1ef`
- 源码事实摘要：感知层: 图像识别模块
- 顶层符号/结构：`ScreenCapture`, `TemplateMatcher`, `ChangeDetector`, `ColorDetector`, `SceneClassifier`
- 直接静态依赖：`__future__`, `time`, `hashlib`, `pathlib`, `typing`, `cv2`, `numpy`, `PIL`, `mss`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/providers/__init__.py -->
### `python/providers/__init__.py`

[打开文件](<../../python/providers/__init__.py>)

- 分类/语言：`supporting-code` / Python
- 规模：11 行，324 bytes
- SHA-256：`16da963a1309a1654df3da82fa9670c6d035c29fa95831e7fda6bc26e8239a92`
- 源码事实摘要：providers — LLM provider abstraction layer.
- 顶层符号/结构：—
- 直接静态依赖：`.base`, `.deepseek`, `.openai`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/providers/base.py -->
### `python/providers/base.py`

[打开文件](<../../python/providers/base.py>)

- 分类/语言：`supporting-code` / Python
- 规模：50 行，1,491 bytes
- SHA-256：`ebbb7fda2a48d1a24c147099c24e2350e49bbf875609924ba2a6e78c6437b2aa`
- 源码事实摘要：Abstract base for LLM providers.
- 顶层符号/结构：`LLMProvider`
- 直接静态依赖：`__future__`, `abc`, `typing`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/providers/deepseek.py -->
### `python/providers/deepseek.py`

[打开文件](<../../python/providers/deepseek.py>)

- 分类/语言：`supporting-code` / Python
- 规模：91 行，2,918 bytes
- SHA-256：`d94e10a001161710c04d584aac6d69e10e083ce68fae143e7af50900648b6df2`
- 源码事实摘要：DeepSeek LLM provider.
- 顶层符号/结构：`DeepSeekProvider`, `const:DEFAULT_API_BASE`, `const:DEFAULT_MODEL`
- 直接静态依赖：`__future__`, `json`, `logging`, `re`, `time`, `typing`, `requests`, `.base`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/providers/openai.py -->
### `python/providers/openai.py`

[打开文件](<../../python/providers/openai.py>)

- 分类/语言：`supporting-code` / Python
- 规模：129 行，4,310 bytes
- SHA-256：`2a7d352a714a65a63bcd84e545e7ed71704d2e84e2878c72b2eeca7eb1893511`
- 源码事实摘要：OpenAI LLM provider.
- 顶层符号/结构：`OpenAIProvider`, `const:DEFAULT_API_BASE`, `const:DEFAULT_MODEL`
- 直接静态依赖：`__future__`, `logging`, `os`, `typing`, `.base`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/providers/vlm.py -->
### `python/providers/vlm.py`

[打开文件](<../../python/providers/vlm.py>)

- 分类/语言：`supporting-code` / Python
- 规模：226 行，9,431 bytes
- SHA-256：`be377b2437f49f68768165e045a1f5b6c173b2f0cb047f88696e69d6f8204386`
- 源码事实摘要：VLM Provider 抽象 — 截图→描述/坐标 的视觉模型后端
- 顶层符号/结构：`_is_multimodal_enabled`, `_screenshot_b64`, `VLMProvider`, `GPT4oMiniProvider`, `LocalVLMProvider`, `get_vlm_provider`, `estimate_cost`, `const:LOCAL_VLM_HOST`, `const:LOCAL_VLM_MODEL`
- 直接静态依赖：`__future__`, `base64`, `json`, `logging`, `os`, `time`, `abc`, `typing`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/rpa_keywords/__init__.py -->
### `python/rpa_keywords/__init__.py`

[打开文件](<../../python/rpa_keywords/__init__.py>)

- 分类/语言：`supporting-code` / Python
- 规模：4 行，191 bytes
- SHA-256：`612b139bd63a770aac4fe64584260595c124e4de4f522c82d5a3afa61b8e3b06`
- 源码事实摘要：Lobster RPA 关键字库 — 将 RPA.Windows 操作封装为 Robot Framework 关键字。
- 顶层符号/结构：—
- 直接静态依赖：—
- API/IPC 入口：—

<!-- PROJECT-FILE: python/rpa_keywords/desktop_actions.py -->
### `python/rpa_keywords/desktop_actions.py`

[打开文件](<../../python/rpa_keywords/desktop_actions.py>)

- 分类/语言：`supporting-code` / Python
- 规模：255 行，9,261 bytes
- SHA-256：`8022926985b812a64c34c795b32dc922b3789232f324a26bcea680837460eee5`
- 源码事实摘要：桌面自动化关键字库 — 封装 RPA.Windows + pywinauto 操作。
- 顶层符号/结构：`desktop_actions`
- 直接静态依赖：`time`, `robot.api.deco`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/server.py -->
### `python/server.py`

[打开文件](<../../python/server.py>)

- 分类/语言：`supporting-code` / Python
- 规模：286 行，9,857 bytes
- SHA-256：`b034f4f45cc3e12dfb5a33a0abe6c2ffd828c30f75461aca308955da39cf0f29`
- 源码事实摘要：Lobster Python 后端服务器 v2
- 顶层符号/结构：`broadcast`, `health`, `get_registry`, `list_features`, `call_feature`, `parse_dsl`, `run_dsl`, `run_dsl_sync`, `get_logs`, `pause`, `resume`, `stop`, `status`, `dsl_state`，另有 6 项
- 直接静态依赖：`__future__`, `json`, `os`, `sys`, `time`, `uuid`, `pathlib`, `flask`, `flask_cors`, `flask_socketio`, `features._all`, `features.registry`, `features.state`, `engine.dsl_parser`，另有 2 项
- API/IPC 入口：—

<!-- PROJECT-FILE: python/templates/框选_2026-06-08T14-19-34-440Z.png -->
### `python/templates/框选_2026-06-08T14-19-34-440Z.png`

[打开文件](<../../python/templates/框选_2026-06-08T14-19-34-440Z.png>)

- 分类/语言：`asset-or-binary` / PNG image
- 规模：binary 行，1,327 bytes
- SHA-256：`d7dbbe5e8e91a9c57a2358705d6a55ac9f1a1d4ef8159a97c13fa4df816dc6d9`
- 源码事实摘要：PNG 59x51
- 顶层符号/结构：—
- 直接静态依赖：—
- API/IPC 入口：—
- 解析说明：binary

<!-- PROJECT-FILE: python/templates/框选_2026-06-08T14-19-42-338Z.png -->
### `python/templates/框选_2026-06-08T14-19-42-338Z.png`

[打开文件](<../../python/templates/框选_2026-06-08T14-19-42-338Z.png>)

- 分类/语言：`asset-or-binary` / PNG image
- 规模：binary 行，4,699 bytes
- SHA-256：`4d58cae8545e376c17d67a93efad785dd04a69a5a8f21af11d687f7a1540936e`
- 源码事实摘要：PNG 82x95
- 顶层符号/结构：—
- 直接静态依赖：—
- API/IPC 入口：—
- 解析说明：binary

<!-- PROJECT-FILE: python/templates/框选_2026-06-08T14-19-55-618Z.png -->
### `python/templates/框选_2026-06-08T14-19-55-618Z.png`

[打开文件](<../../python/templates/框选_2026-06-08T14-19-55-618Z.png>)

- 分类/语言：`asset-or-binary` / PNG image
- 规模：binary 行，2,037 bytes
- SHA-256：`7dd714b4775a60141ecf4cfc9b11e18f342d79a7bf9fbabd556b1349bce07330`
- 源码事实摘要：PNG 77x30
- 顶层符号/结构：—
- 直接静态依赖：—
- API/IPC 入口：—
- 解析说明：binary

<!-- PROJECT-FILE: python/test_browser_comprehensive.py -->
### `python/test_browser_comprehensive.py`

[打开文件](<../../python/test_browser_comprehensive.py>)

- 分类/语言：`test` / Python
- 规模：371 行，17,683 bytes
- SHA-256：`f3e833044a019e52321c6eceaac29b071b0879738451e12f69abe793cf447b19`
- 源码事实摘要：浏览器功能深度测试 — 覆盖 browser_features.py + web_features.py 全部功能。
- 顶层符号/结构：`suite`, `test`, `const:_TEST_HTML`
- 直接静态依赖：`sys`, `os`, `json`, `time`, `tempfile`, `features.browser_features`, `features.web_features`, `playwright.sync_api`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/tools/__init__.py -->
### `python/tools/__init__.py`

[打开文件](<../../python/tools/__init__.py>)

- 分类/语言：`supporting-code` / Python
- 规模：13 行，415 bytes
- SHA-256：`2991d411bba29f7ed7301c81c5da71ed2450a390451ed66b49d074a672eb1d21`
- 源码事实摘要：tools — Typed tool abstraction layer over the @feature registry.
- 顶层符号/结构：—
- 直接静态依赖：`.base`, `.registry`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/tools/ahk_manager.py -->
### `python/tools/ahk_manager.py`

[打开文件](<../../python/tools/ahk_manager.py>)

- 分类/语言：`supporting-code` / Python
- 规模：249 行，8,204 bytes
- SHA-256：`7034f2d66f0c0f68ee4c86dd226b0cc3ce117ca3b864adb0fe927de015362102`
- 源码事实摘要：AutoHotkey 管理器 — 自动检测/下载/启动 AHK v2 便携版
- 顶层符号/结构：`is_ahk_available`, `download_ahk`, `get_ahk_path`, `start_recorder`, `stop_recorder`, `recorder_status`, `const:_AHK_DIR`, `const:_AHK_EXE`, `const:_RECORDER_SCRIPT`, `const:_AHK_DOWNLOAD_URL`
- 直接静态依赖：`__future__`, `json`, `os`, `subprocess`, `sys`, `time`, `threading`, `urllib.request`, `zipfile`, `pathlib`, `typing`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/tools/ahk_recorder.ahk -->
### `python/tools/ahk_recorder.ahk`

[打开文件](<../../python/tools/ahk_recorder.ahk>)

- 分类/语言：`supporting-code` / AutoHotkey
- 规模：230 行，4,760 bytes
- SHA-256：`cdbea6a82229df0870e0efaf16c21d2f90a944432047707738e8a9d8fdd16de8`
- 源码事实摘要：AHK v2 宏录制器
- 顶层符号/结构：—
- 直接静态依赖：—
- API/IPC 入口：—

<!-- PROJECT-FILE: python/tools/base.py -->
### `python/tools/base.py`

[打开文件](<../../python/tools/base.py>)

- 分类/语言：`supporting-code` / Python
- 规模：158 行，5,175 bytes
- SHA-256：`cf936956ac4df484c316dc8c4ee8dbb2bbbc26c863663530f5d69ac3da588eba`
- 源码事实摘要：ToolWrapper — wraps @feature registry FeatureSpec into typed Tool interface.
- 顶层符号/结构：`ToolWrapper`
- 直接静态依赖：`__future__`, `typing`, `core.interfaces`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/tools/feature_call.py -->
### `python/tools/feature_call.py`

[打开文件](<../../python/tools/feature_call.py>)

- 分类/语言：`supporting-code` / Python
- 规模：50 行，1,509 bytes
- SHA-256：`88c543910fd378f1178dfeb1c0cf4c98fb9cdc62d0275896b563d8dc44e02677`
- 源码事实摘要：通过 stdin/out 持续调用 @feature（支持持久化状态）。
- 顶层符号/结构：`handle_line`, `main`
- 直接静态依赖：`sys`, `json`, `threading`, `features._all`, `features.registry`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/tools/feature_list.py -->
### `python/tools/feature_list.py`

[打开文件](<../../python/tools/feature_list.py>)

- 分类/语言：`supporting-code` / Python
- 规模：32 行，1,034 bytes
- SHA-256：`0e81870cfeabac63b76cdd835fa8fee3d1f638e8cf90319e906d4fdf35b6ed4f`
- 源码事实摘要：列出所有已注册的 @feature（供 Electron IPC / HTTP API 调用）。
- 顶层符号/结构：—
- 直接静态依赖：`sys`, `json`, `features._all`, `features.registry`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/tools/macro_play_cli.py -->
### `python/tools/macro_play_cli.py`

[打开文件](<../../python/tools/macro_play_cli.py>)

- 分类/语言：`supporting-code` / Python
- 规模：14 行，356 bytes
- SHA-256：`18ea4ba1df4e041a1991ac38429befc44ed76a1c219100538a71a75a98540b22`
- 源码事实摘要：命令行宏回放。
- 顶层符号/结构：—
- 直接静态依赖：`sys`, `json`, `features.macro_features`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/tools/macro_recorder.py -->
### `python/tools/macro_recorder.py`

[打开文件](<../../python/tools/macro_recorder.py>)

- 分类/语言：`supporting-code` / Python
- 规模：313 行，8,726 bytes
- SHA-256：`8d984c94a4682a8cfcc3617d65a2955429d83c4c74393b7df15644e025bb18d9`
- 源码事实摘要：独立宏录制进程
- 顶层符号/结构：`_ensure_dir`, `_emit`, `_on_click`, `_on_move`, `_on_press`, `_on_release`, `_start_recording`, `_stop_recording`, `_handle_stdin`, `main`, `const:_MACRO_DIR`
- 直接静态依赖：`json`, `os`, `sys`, `time`, `threading`, `datetime`, `pathlib`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/tools/registry.py -->
### `python/tools/registry.py`

[打开文件](<../../python/tools/registry.py>)

- 分类/语言：`supporting-code` / Python
- 规模：126 行，4,045 bytes
- SHA-256：`8a5c9cad127005025bcc4d76e54f1f6bb1bbdb7df72573f31fc0def497f4badb`
- 源码事实摘要：ToolRegistry — discovers @feature tools and wraps them as typed Tool interfaces.
- 顶层符号/结构：`ToolRegistry`
- 直接静态依赖：`__future__`, `typing`, `core.interfaces`, `features.registry`, `.base`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/tools/run_dsl.py -->
### `python/tools/run_dsl.py`

[打开文件](<../../python/tools/run_dsl.py>)

- 分类/语言：`supporting-code` / Python
- 规模：18 行，508 bytes
- SHA-256：`601835e3725df9ca51d40bffa08a05cb34ab12a84212b92f38fb19abda57c7f8`
- 源码事实摘要：通过 spawnPython 执行 DSL 文本。
- 顶层符号/结构：—
- 直接静态依赖：`sys`, `json`, `engine.executor`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/tools/template_market.py -->
### `python/tools/template_market.py`

[打开文件](<../../python/tools/template_market.py>)

- 分类/语言：`supporting-code` / Python
- 规模：342 行，11,336 bytes
- SHA-256：`18c16b508844dec65e8aa973803835187ea979ea7ebe636268ce76f2e263b162`
- 源码事实摘要：Self-hosted Template Market — 社区模板市场
- 顶层符号/结构：`_get_template_dir`, `_get_source_url`, `_get_raw_base`, `fetch_index`, `search`, `install`, `list_installed`, `publish`, `main`, `const:DEFAULT_SOURCE`, `const:RAW_BASE`, `const:INDEX_CACHE_TTL`, `const:_INDEX_CACHE`
- 直接静态依赖：`__future__`, `json`, `os`, `sys`, `time`, `pathlib`, `typing`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/utils/__init__.py -->
### `python/utils/__init__.py`

[打开文件](<../../python/utils/__init__.py>)

- 分类/语言：`supporting-code` / Python
- 规模：1 行，14 bytes
- SHA-256：`a8002c84368058f89fed03396bab6dfd105c15f0c61ed337823103d42db9351c`
- 源码事实摘要：Python 包 python.utils 的初始化与公开边界。
- 顶层符号/结构：—
- 直接静态依赖：—
- API/IPC 入口：—

<!-- PROJECT-FILE: python/utils/bezier.py -->
### `python/utils/bezier.py`

[打开文件](<../../python/utils/bezier.py>)

- 分类/语言：`supporting-code` / Python
- 规模：71 行，3,311 bytes
- SHA-256：`2b8c9aacf61d015229df9f613f909bd52f28f9f568cced8943f4f2c1d3037367`
- 源码事实摘要：Bezier curve generation for human-like mouse movement.
- 顶层符号/结构：`BezierCurve`
- 直接静态依赖：`math`, `random`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/config.example.json -->
### `python/winpilot_home/config.example.json`

[打开文件](<../../python/winpilot_home/config.example.json>)

- 分类/语言：`configuration` / JSON
- 规模：116 行，3,148 bytes
- SHA-256：`d5b0ea23e089ed4fded31f51451a4f43514754cb8f3f9b5f50738ee9c84097d7`
- 源码事实摘要：配置 config.example 对应的构建、运行、依赖或测试行为。
- 顶层符号/结构：`key:api`, `key:vlm`, `key:perception`, `key:fusion`, `key:agent`, `key:controller`, `key:ocr`, `key:search`, `key:coding`, `key:integrations`, `key:vnext`, `key:server`
- 直接静态依赖：—
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/eval_targets/__init__.py -->
### `python/winpilot_home/eval_targets/__init__.py`

[打开文件](<../../python/winpilot_home/eval_targets/__init__.py>)

- 分类/语言：`e2e-or-harness` / Python
- 规模：1 行，79 bytes
- SHA-256：`403d5d4b1871f8ccd7017ec48ba6e00c3e3a31751ca5dd5edef08151d4ff42f6`
- 源码事实摘要：Local-only visual targets and ground-truth recorders for real E2E tests.
- 顶层符号/结构：—
- 直接静态依赖：—
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/eval_targets/gui_agent_target.html -->
### `python/winpilot_home/eval_targets/gui_agent_target.html`

[打开文件](<../../python/winpilot_home/eval_targets/gui_agent_target.html>)

- 分类/语言：`e2e-or-harness` / HTML
- 规模：645 行，18,105 bytes
- SHA-256：`519d94d0fe44faaa1d35ca23a1da0002b1564b698f821e66e7a1cbe161d0c0e8`
- 源码事实摘要：WinPilot GUI Agent Target
- 顶层符号/结构：—
- 直接静态依赖：—
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/eval_targets/offline_flow_click_e2e.json -->
### `python/winpilot_home/eval_targets/offline_flow_click_e2e.json`

[打开文件](<../../python/winpilot_home/eval_targets/offline_flow_click_e2e.json>)

- 分类/语言：`e2e-or-harness` / JSON
- 规模：94 行，1,905 bytes
- SHA-256：`a4a47c14322437432f58155320578bc68abb2e18085cb418dddecf9d639e8a71`
- 源码事实摘要：为 offline flow click e2e 提供端到端运行、靶场或外部 oracle 支撑。
- 顶层符号/结构：`key:meta`, `key:nodes`, `key:edges`, `key:variables`, `key:settings`, `node:start×1`, `node:focus_window×1`, `node:click×1`, `node:screenshot×1`, `node:end×1`
- 直接静态依赖：—
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/eval_targets/offline_flow_clipboard_screenshot.json -->
### `python/winpilot_home/eval_targets/offline_flow_clipboard_screenshot.json`

[打开文件](<../../python/winpilot_home/eval_targets/offline_flow_clipboard_screenshot.json>)

- 分类/语言：`e2e-or-harness` / JSON
- 规模：136 行，2,774 bytes
- SHA-256：`ca3d35a705c063c4c008e02f9f076a372b008ee7571988843e3f4393e2864033`
- 源码事实摘要：为 offline flow clipboard screenshot 提供端到端运行、靶场或外部 oracle 支撑。
- 顶层符号/结构：`key:meta`, `key:nodes`, `key:edges`, `key:variables`, `key:settings`, `node:start×1`, `node:clipboard_get×1`, `node:condition×1`, `node:clipboard_set×2`, `node:screenshot×1`, `node:end×1`
- 直接静态依赖：—
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/eval_targets/offline_flow_node_target.py -->
### `python/winpilot_home/eval_targets/offline_flow_node_target.py`

[打开文件](<../../python/winpilot_home/eval_targets/offline_flow_node_target.py>)

- 分类/语言：`e2e-or-harness` / Python
- 规模：271 行，11,656 bytes
- SHA-256：`3e1dbb250c95962056a8ae48201758cc4f327c1411f41997bd2936edbf05f199`
- 源码事实摘要：Generic, fully local desktop target for per-node Flow/ARP E2E.
- 顶层符号/结构：`OfflineFlowNodeTarget`, `main`
- 直接静态依赖：`__future__`, `argparse`, `hashlib`, `json`, `os`, `pathlib`, `time`, `tkinter`, `typing`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/eval_targets/visual_interaction_server.py -->
### `python/winpilot_home/eval_targets/visual_interaction_server.py`

[打开文件](<../../python/winpilot_home/eval_targets/visual_interaction_server.py>)

- 分类/语言：`e2e-or-harness` / Python
- 规模：391 行，14,403 bytes
- SHA-256：`1fae21f0e2376a51719f21cb40cc694bbd10b8d3d8b8930fbc248efe0758c566`
- 源码事实摘要：Local-only ground-truth server for real visual-interaction E2E tests.
- 顶层符号/结构：`_quiz_questions`, `_public_quiz_spec`, `_normalize_quiz_answer`, `_evaluate_quiz_submission`, `_accepted_success_event`, `_state_snapshot`, `_safe_value`, `_prepare_event`, `Handler`, `main`, `const:ROOT`, `const:TARGET`, `const:BASIC_INPUT_TARGET`, `const:STATE_LOCK`，另有 1 项
- 直接静态依赖：`__future__`, `json`, `random`, `sys`, `threading`, `time`, `unicodedata`, `http.server`, `pathlib`, `typing`, `urllib.parse`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/eval_targets/visual_interaction_target.html -->
### `python/winpilot_home/eval_targets/visual_interaction_target.html`

[打开文件](<../../python/winpilot_home/eval_targets/visual_interaction_target.html>)

- 分类/语言：`e2e-or-harness` / HTML
- 规模：642 行，49,850 bytes
- SHA-256：`b804659793e0b1a4994b7ae479feba43d6d08944494ddf7d327c0523f1aee9b4`
- 源码事实摘要：WinPilot Visual Interaction Target
- 顶层符号/结构：—
- 直接静态依赖：—
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/eval_targets/vnext_canary_server.py -->
### `python/winpilot_home/eval_targets/vnext_canary_server.py`

[打开文件](<../../python/winpilot_home/eval_targets/vnext_canary_server.py>)

- 分类/语言：`e2e-or-harness` / Python
- 规模：791 行，30,898 bytes
- SHA-256：`57c0e110db8e8557e276f277495d82ba03145e9892cad6d6c6e4c4f237f4a609`
- 源码事实摘要：Independent local oracle for the minimal live vNext canary.
- 顶层符号/结构：`_safe_value`, `_events_of`, `_details`, `_visible_state`, `_canonical_hash`, `_action_observation`, `derive_action_observations`, `_validate_click_double`, `_validate_scroll_roundtrip`, `_validate_type_hotkey`, `validate_case_events`, `_state_snapshot`, `Handler`, `main`，另有 6 项
- 直接静态依赖：`__future__`, `hashlib`, `json`, `math`, `sys`, `threading`, `time`, `http.server`, `pathlib`, `typing`, `urllib.parse`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/eval_targets/vnext_canary_target.html -->
### `python/winpilot_home/eval_targets/vnext_canary_target.html`

[打开文件](<../../python/winpilot_home/eval_targets/vnext_canary_target.html>)

- 分类/语言：`e2e-or-harness` / HTML
- 规模：559 行，23,561 bytes
- SHA-256：`4de9c4a8b475651bd022a7481c9fdfc7f171d7745ac9de996d058ee4e18266b3`
- 源码事实摘要：WinPilot vNext Canary
- 顶层符号/结构：—
- 直接静态依赖：—
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/go_game.py -->
### `python/winpilot_home/go_game.py`

[打开文件](<../../python/winpilot_home/go_game.py>)

- 分类/语言：`supporting-code` / Python
- 规模：118 行，3,639 bytes
- SHA-256：`f1b60a6cfaa16fa087293dda34a81d6e7e586b643df9f9a8f86b66aea2e738c8`
- 源码事实摘要：实现或支撑 go game 模块；精确公开面见下列静态符号与依赖。
- 顶层符号/结构：`GoGame`
- 直接静态依赖：`random`, `tkinter`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/playbooks/coding-project.json -->
### `python/winpilot_home/playbooks/coding-project.json`

[打开文件](<../../python/winpilot_home/playbooks/coding-project.json>)

- 分类/语言：`configuration` / JSON
- 规模：54 行，4,852 bytes
- SHA-256：`5ee03679d980172db9595053b0bf9c0cb5182767bae93ca5340a408901bbf98b`
- 源码事实摘要：编程/软件项目工作流：用命令行做全仓分析、编辑、构建、测试、调试、git
- 顶层符号/结构：`key:name`, `key:description`, `key:keywords`, `key:window_triggers`, `key:content`
- 直接静态依赖：—
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/playbooks/file-dialog.json -->
### `python/winpilot_home/playbooks/file-dialog.json`

[打开文件](<../../python/winpilot_home/playbooks/file-dialog.json>)

- 分类/语言：`configuration` / JSON
- 规模：7 行，841 bytes
- SHA-256：`afc564817d2ccc58bfeb54b2d0f04c3c5696811c3d75bb35a95faef37da60c5a`
- 源码事实摘要：文件对话框必胜法（另存为/打开）
- 顶层符号/结构：`key:name`, `key:description`, `key:keywords`, `key:window_triggers`, `key:content`
- 直接静态依赖：—
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/playbooks/media-playback.json -->
### `python/winpilot_home/playbooks/media-playback.json`

[打开文件](<../../python/winpilot_home/playbooks/media-playback.json>)

- 分类/语言：`configuration` / JSON
- 规模：7 行，1,426 bytes
- SHA-256：`6c2b96597f555756742ffca43473b7ea67b9539908eeb0204dfb808ea022239a`
- 源码事实摘要：播放媒体的正确姿势
- 顶层符号/结构：`key:name`, `key:description`, `key:keywords`, `key:window_triggers`, `key:content`
- 直接静态依赖：—
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/playbooks/tray-wakeup.json -->
### `python/winpilot_home/playbooks/tray-wakeup.json`

[打开文件](<../../python/winpilot_home/playbooks/tray-wakeup.json>)

- 分类/语言：`configuration` / JSON
- 规模：16 行，1,406 bytes
- SHA-256：`dfeba2cfd45293a231b5ca935887ea63e36def57067415d91b36c9e3146a4177`
- 源码事实摘要：运行中但无可见窗口时的通用托盘恢复
- 顶层符号/结构：`key:name`, `key:description`, `key:keywords`, `key:window_triggers`, `key:conditions`, `key:evidence`, `key:content`
- 直接静态依赖：—
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/serve.py -->
### `python/winpilot_home/serve.py`

[打开文件](<../../python/winpilot_home/serve.py>)

- 分类/语言：`supporting-code` / Python
- 规模：39 行，1,385 bytes
- SHA-256：`a5772faf8d02b44a770b5df3840d645e03d26e0c904fb8d7d00e42e20f41241f`
- 源码事实摘要：WinPilot server launcher — the ONLY supported way to start WinPilot.
- 顶层符号/结构：`main`, `const:_HERE`
- 直接静态依赖：`__future__`, `os`, `secrets`, `sys`, `uvicorn`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/tests/conftest.py -->
### `python/winpilot_home/tests/conftest.py`

[打开文件](<../../python/winpilot_home/tests/conftest.py>)

- 分类/语言：`test` / Python
- 规模：159 行，4,873 bytes
- SHA-256：`8611d391edf36580062e05cc9d5b178fedf64bf11cff0f3949bb890db721cc91`
- 源码事实摘要：Pytest bootstrap for the WinPilot package.
- 顶层符号/结构：`_WinDll`, `_NoopModule`, `_ConstModule`, `_Mss`, `_missing`, `isolated_config`
- 直接静态依赖：`ctypes`, `importlib.util`, `os`, `subprocess`, `sys`, `types`, `copy`, `pytest`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/tests/test_admitted_public_action_telemetry.py -->
### `python/winpilot_home/tests/test_admitted_public_action_telemetry.py`

[打开文件](<../../python/winpilot_home/tests/test_admitted_public_action_telemetry.py>)

- 分类/语言：`test` / Python
- 规模：600 行，17,576 bytes
- SHA-256：`2d16ed1577ecca2df5a1dc6128d63cffb9d5fb00a6716b69c0d320451820f473`
- 源码事实摘要：验证 test admitted public action telemetry 对应的行为、回归或合同。
- 顶层符号/结构：`_nested_keys`, `_nested_values`, `_assert_private_parameters_absent`, `_assert_public_action_event`, `_action_events`, `_next_action_event`, `strict_backend_environment`, `_run_scroll`, `_run_drag`, `_run_type_text`, `_run_hotkey`, `_run_click`, `test_admitted_action_summary_rejects_invalid_field_combinations`, `test_admitted_action_summary_rejects_unknown_keyword`，另有 16 项
- 直接静态依赖：`__future__`, `hashlib`, `inspect`, `json`, `queue`, `threading`, `collections.abc`, `typing`, `pytest`, `winpilot.action.raw_device`, `winpilot.action.replay_policy`, `winpilot.utils.log`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/tests/test_agent_scheduler.py -->
### `python/winpilot_home/tests/test_agent_scheduler.py`

[打开文件](<../../python/winpilot_home/tests/test_agent_scheduler.py>)

- 分类/语言：`test` / Python
- 规模：279 行，10,389 bytes
- SHA-256：`55d14895048ae12a86d559d7975a72dff97461f55f486cad4df7fa8b6c6c0561`
- 源码事实摘要：验证 test agent scheduler 对应的行为、回归或合同。
- 顶层符号/结构：`test_agent_scheduler_create_due_finish_and_cancel`, `test_agent_scheduler_accepts_iso_run_at`, `test_agent_scheduler_deduplicates_immediate_model_retry`, `test_reminder_classifier_separates_notification_from_mixed_work`, `test_due_reminder_uses_notification_job_without_llm`, `test_agent_scheduler_rejects_invalid_run_at`, `test_agent_scheduler_rejects_invalid_delay_seconds`, `test_agent_schedule_rest_create_list_cancel`, `test_agent_schedule_rest_rejects_invalid_run_at`, `test_agent_schedule_rest_updates_and_reenables_task`, `test_scheduler_thread_executes_due_task_through_unified_runner`
- 直接静态依赖：`datetime`, `threading`, `types`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/tests/test_agent_threads.py -->
### `python/winpilot_home/tests/test_agent_threads.py`

[打开文件](<../../python/winpilot_home/tests/test_agent_threads.py>)

- 分类/语言：`test` / Python
- 规模：343 行，11,284 bytes
- SHA-256：`cc5e362e9fbc6de2c9958aed32415226cbe5a8b80816cd925ecd94422a25c970`
- 源码事实摘要：验证 test agent threads 对应的行为、回归或合同。
- 顶层符号/结构：`test_thread_history_contains_only_successful_user_facing_turns`, `test_thread_capacity_never_evicts_an_active_turn`, `test_thread_continuation_checkpoint_round_trips_exact_history`, `test_thread_checkpoint_keeps_only_the_bounded_model_history`, `test_invalid_thread_checkpoint_fails_closed_without_partial_load`, `test_checkpoint_write_failure_never_blocks_conversation`, `test_checkpoint_omits_threads_without_successful_history`, `test_checkpoint_with_non_string_status_fails_closed`, `test_restored_result_projection_is_a_consistent_terminal`
- 直接静态依赖：`json`, `pytest`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/tests/test_app_resolution.py -->
### `python/winpilot_home/tests/test_app_resolution.py`

[打开文件](<../../python/winpilot_home/tests/test_app_resolution.py>)

- 分类/语言：`test` / Python
- 规模：428 行，13,650 bytes
- SHA-256：`dc9a4f9db405d9f7407a7656a38ea14797b866fb4938b33eaa1cb203be85d7d7`
- 源码事实摘要：验证 test app resolution 对应的行为、回归或合同。
- 顶层符号/结构：`test_app_alias_expansion_returns_only_the_normalized_literal`, `test_app_index_uses_only_generic_discovery_sources`, `test_resolve_fictional_app_by_discovered_display_name`, `test_resolve_fictional_app_by_discovered_shortcut_alias`, `test_resolve_keeps_display_typos_but_rejects_fuzzy_path_aliases`, `test_launch_discovered_exe_passes_generic_arguments`, `test_resolve_refreshes_a_cached_multisource_index_once_on_miss`, `test_index_keeps_same_kind_entries_with_distinct_launch_targets`, `test_start_app_record_adds_only_catalog_proven_runtime_identity`, `test_start_app_record_does_not_invent_a_process_from_display_or_app_id`, `test_start_apps_json_parser_preserves_executable_and_package_identity`, `test_start_apps_discovery_uses_system_catalog_metadata_once`, `test_app_paths_ignores_stale_executable_targets`, `test_index_merges_structured_identity_for_the_same_app_id`，另有 3 项
- 直接静态依赖：`__future__`, `json`, `sys`, `types`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/tests/test_application_launch_intent.py -->
### `python/winpilot_home/tests/test_application_launch_intent.py`

[打开文件](<../../python/winpilot_home/tests/test_application_launch_intent.py>)

- 分类/语言：`test` / Python
- 规模：41 行，1,542 bytes
- SHA-256：`0fa74fa93628889b2e9205fe4dd914acfd4298d64b1dc18ad0772079ef146cb2`
- 源码事实摘要：验证 test application launch intent 对应的行为、回归或合同。
- 顶层符号/结构：`test_complete_launch_expressions_compile_to_typed_open_application`, `test_opening_media_or_url_is_not_claimed_as_application_launch`
- 直接静态依赖：`__future__`, `pytest`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/tests/test_apprentice_mode.py -->
### `python/winpilot_home/tests/test_apprentice_mode.py`

[打开文件](<../../python/winpilot_home/tests/test_apprentice_mode.py>)

- 分类/语言：`test` / Python
- 规模：237 行，7,833 bytes
- SHA-256：`bc075e16165140ec1981b1aba82b9f7f8bf83c075f5d0895fd618536752cc63b`
- 源码事实摘要：验证 test apprentice mode 对应的行为、回归或合同。
- 顶层符号/结构：`FakeRecorder`, `test_apprentice_lesson_becomes_editable_script_and_provisional_skill`, `test_apprentice_requires_named_task`, `test_apprentice_rejects_unsafe_explicit_macro_name`, `test_apprentice_rejects_existing_macro_name_case_insensitively`, `test_apprentice_blank_macro_name_reuses_unique_session_id`, `test_apprentice_keeps_existing_canonical_file_on_stop_race`, `test_apprentice_clears_failed_recording_session`, `test_apprentice_keeps_session_when_recorder_stop_is_retryable`
- 直接静态依赖：`json`, `pytest`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/tests/test_automation_ir.py -->
### `python/winpilot_home/tests/test_automation_ir.py`

[打开文件](<../../python/winpilot_home/tests/test_automation_ir.py>)

- 分类/语言：`test` / Python
- 规模：251 行，8,465 bytes
- SHA-256：`b7419e7c4c20e1c7b941b15bacb315f004416396afdd55ae97f308b2f2be0875`
- 源码事实摘要：验证 test automation ir 对应的行为、回归或合同。
- 顶层符号/结构：`_write_trace`, `test_automation_ir_from_wps_preserves_replay_metadata`, `test_automation_ir_roundtrips_to_wps`, `test_wps_automation_runtime_rejects_empty_execution_projection`, `test_non_wps_automation_ir_does_not_convert_to_wps`, `test_export_automation_ir_from_trace`, `test_reject_invalid_automation_ir`, `test_automation_ir_validates_and_returns_canonical_arp_runtime_graph`, `test_automation_ir_runtime_rejects_missing_flow_graph`, `test_projection_fingerprint_matches_cross_runtime_contract`, `test_wps_runtime_safety_cannot_understate_detached_shell_execution`, `test_flow_projection_rejects_dangling_editor_edge`
- 直接静态依赖：`json`, `winpilot`, `winpilot.recorder`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/tests/test_aux_model_runtime_migration.py -->
### `python/winpilot_home/tests/test_aux_model_runtime_migration.py`

[打开文件](<../../python/winpilot_home/tests/test_aux_model_runtime_migration.py>)

- 分类/语言：`test` / Python
- 规模：77 行，2,765 bytes
- SHA-256：`f9e4424d2896b42920c70f8e27cf020ef0b4cbcd86e7e1154df152a6965beb0e`
- 源码事实摘要：验证 test aux model runtime migration 对应的行为、回归或合同。
- 顶层符号/结构：`_response`, `test_planner_uses_single_attempt_central_model_boundary`, `test_planner_model_call_error_keeps_legacy_empty_plan_fallback`, `test_done_verifier_uses_single_attempt_boundary_and_fails_closed`
- 直接静态依赖：`threading`, `types`, `winpilot.agent`, `winpilot.agent.loop`, `winpilot.modeling`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/tests/test_chatgpt_mcp.py -->
### `python/winpilot_home/tests/test_chatgpt_mcp.py`

[打开文件](<../../python/winpilot_home/tests/test_chatgpt_mcp.py>)

- 分类/语言：`test` / Python
- 规模：795 行，28,682 bytes
- SHA-256：`716bfedd0e3c2da3ea3d37d16d17538ed8115b104ccbd5853677b72e2a7cb5a1`
- 源码事实摘要：验证 test chatgpt mcp 对应的行为、回归或合同。
- 顶层符号/结构：`test_runtime_python_cwd_does_not_shadow_official_mcp_sdk`, `_hooks`, `_graph`, `test_mcp_catalog_exposes_controller_first_tools_and_annotations`, `test_chatgpt_graph_rejects_nested_api_agent_but_accepts_controller_batch`, `test_mutation_guard_blocks_controller_calls_while_runtime_is_busy`, `test_unbound_task_change_atomically_rebuilds_all_semantic_session_state`, `test_failed_unbound_session_preparation_leaves_previous_snapshot_atomic`, `test_bound_task_remains_contract_authority_for_later_subgoal_labels`, `test_visual_fallback_returns_raw_image_bytes_without_leaking_base64`, `test_mcp_frame_coordinates_are_bound_and_mapped_from_exact_capture`, `test_mcp_balanced_pointer_path_is_one_frame_bound_local_gesture`, `_jpeg`, `_jpeg_with_block`，另有 14 项
- 直接静态依赖：`asyncio`, `base64`, `hashlib`, `io`, `json`, `pathlib`, `subprocess`, `sys`, `types`, `PIL`, `pytest`, `winpilot.agent.tool_result`, `winpilot.integrations.chatgpt_mcp`, `winpilot.orchestrator.projection`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/tests/test_client_complex_orchestration_e2e_harness.py -->
### `python/winpilot_home/tests/test_client_complex_orchestration_e2e_harness.py`

[打开文件](<../../python/winpilot_home/tests/test_client_complex_orchestration_e2e_harness.py>)

- 分类/语言：`test` / Python
- 规模：273 行，8,328 bytes
- SHA-256：`a3090e1eaf0dd1ece147415b513fd2610062f9175eaf12dad08eff93edbddb84`
- 源码事实摘要：验证 test client complex orchestration e2e harness 对应的行为、回归或合同。
- 顶层符号/结构：`_load_harness`, `test_orchestration_evidence_reports_real_parallelism_and_recovery`, `test_orchestration_evidence_keeps_legacy_pairing_and_flags_unmatched`, `test_ui_activity_probe_does_not_override_task_completion`, `test_live_ui_probe_requires_interleaved_activity_and_task_board`, `test_live_ui_probe_accepts_task_board_as_the_models_progress_signal`
- 直接静态依赖：`__future__`, `importlib.util`, `json`, `sys`, `pathlib`, `types`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/tests/test_client_e2e_harness.py -->
### `python/winpilot_home/tests/test_client_e2e_harness.py`

[打开文件](<../../python/winpilot_home/tests/test_client_e2e_harness.py>)

- 分类/语言：`test` / Python
- 规模：1719 行，53,581 bytes
- SHA-256：`01f2a783e80d2e30878df3ce8f966e69aab879922cc1d1f3be7039586d3d2e72`
- 源码事实摘要：验证 test client e2e harness 对应的行为、回归或合同。
- 顶层符号/结构：`_load_harness`, `test_harness_keeps_the_exact_eleven_and_one_combined_long_goal`, `test_harness_has_no_execution_policy_selector_argument`, `test_control_value_uses_complete_text_when_value_pattern_is_truncated`, `test_document_text_can_verify_a_long_goal_when_edit_value_is_truncated`, `test_named_button_lookup_short_circuits_a_large_accessibility_tree`, `test_partial_name_lookup_short_circuits_the_goal_editor`, `test_goal_entry_uses_native_keyboard_input_without_value_pattern`, `test_session_reset_reuses_the_count_control_instead_of_rewalking_cards`, `test_navigation_reasserts_the_client_foreground_before_click`, `test_trace_snapshot_tracks_outer_arp_jsonl_files_not_artifact_directories`, `test_runtime_status_retries_a_transient_managed_endpoint_timeout`, `test_runtime_status_rejects_a_wrong_control_token_without_retrying`, `test_cold_start_runtime_probe_uses_one_long_request_budget`，另有 31 项
- 直接静态依赖：`__future__`, `importlib.util`, `json`, `pathlib`, `types`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/tests/test_client_netease_music_e2e_harness.py -->
### `python/winpilot_home/tests/test_client_netease_music_e2e_harness.py`

[打开文件](<../../python/winpilot_home/tests/test_client_netease_music_e2e_harness.py>)

- 分类/语言：`test` / Python
- 规模：481 行，14,375 bytes
- SHA-256：`32f75b62d70c75453f827acc574e11ff448797cb46aad799d5a788616d14eed2`
- 源码事实摘要：验证 test client netease music e2e harness 对应的行为、回归或合同。
- 顶层符号/结构：`_load_harness`, `_node`, `_successful_nodes`, `test_client_gets_only_original_natural_language_goal`, `test_readonly_coreaudio_reader_uses_only_getters`, `test_visible_playback_first_result_and_five_percent_pass_candidate`, `test_pause_artist_and_track_must_be_in_bottom_player_region`, `test_generic_play_pause_label_is_not_proof_of_active_playback`, `test_muted_or_wrong_volume_fails_even_when_player_is_correct`, `test_second_result_cannot_pass_as_the_first_song`, `test_missing_result_row_is_diagnostic_not_a_false_failure`, `test_admission_binding_accepts_only_a_new_vnext_arp_job`, `test_current_run_fallback_must_match_the_submitted_goal`, `test_transient_capture_error_does_not_erase_the_last_player_verdict`，另有 3 项
- 直接静态依赖：`__future__`, `importlib.util`, `inspect`, `sys`, `pathlib`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/tests/test_client_paint_game_e2e_harness.py -->
### `python/winpilot_home/tests/test_client_paint_game_e2e_harness.py`

[打开文件](<../../python/winpilot_home/tests/test_client_paint_game_e2e_harness.py>)

- 分类/语言：`test` / Python
- 规模：421 行，12,336 bytes
- SHA-256：`8cd6c171d90d97f6cb87b5d57bc8d8815540dcf129de760503c86ade718b532b`
- 源码事实摘要：验证 test client paint game e2e harness 对应的行为、回归或合同。
- 顶层符号/结构：`_load_script`, `_load_paint_harness`, `_board`, `_draw_red_x`, `test_client_receives_only_the_unchanged_natural_language_goal`, `test_logical_board_bbox_scales_with_the_target_window_dpi`, `test_paint_capture_adds_scaled_relative_bbox_to_physical_window_origin`, `test_baseline_accepts_the_empty_center_and_rejects_an_occupied_cell`, `test_baseline_rejects_a_large_dark_mark_without_a_grid`, `test_transient_mouse_pointer_does_not_fail_empty_cell_or_retention`, `test_complete_red_x_in_the_expected_cell_passes_one_frame_evaluation`, `test_one_red_diagonal_is_not_a_complete_x`, `test_thin_wrong_cell_x_is_preserved_as_wrong_red_evidence`, `test_corrected_current_board_can_pass_after_a_wrong_intermediate_frame`，另有 5 项
- 直接静态依赖：`__future__`, `importlib.util`, `sys`, `pathlib`, `cv2`, `numpy`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/tests/test_client_qq_groups_e2e_harness.py -->
### `python/winpilot_home/tests/test_client_qq_groups_e2e_harness.py`

[打开文件](<../../python/winpilot_home/tests/test_client_qq_groups_e2e_harness.py>)

- 分类/语言：`test` / Python
- 规模：695 行，21,513 bytes
- SHA-256：`61d2bbf1c103ba5c675aaf497053965403f9c27228a433335234e28f4cf26bfd`
- 源码事实摘要：验证 test client qq groups e2e harness 对应的行为、回归或合同。
- 顶层符号/结构：`_load_harness`, `_node`, `_ocr_element`, `_fake_ocr`, `_qq_window_image`, `_ocr_qq_state`, `harness_ocr_analysis`, `test_ocr_state_detects_rows_and_active_group_from_pixels`, `test_ocr_rows_filter_times_counts_and_preview_lines`, `test_ocr_mode_browse_complete_needs_switches_not_scroll_or_end`, `test_ocr_mode_never_completes_without_switches`, `test_ocr_mode_requires_visiting_every_discovered_row`, `test_oracle_falls_back_to_ocr_when_qq_uia_is_black_box`, `test_uia_rich_mode_merges_ocr_selection_when_selection_state_missing`，另有 9 项
- 直接静态依赖：`__future__`, `importlib.util`, `json`, `sys`, `pathlib`, `types`, `PIL`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/tests/test_client_random_tasks_e2e_harness.py -->
### `python/winpilot_home/tests/test_client_random_tasks_e2e_harness.py`

[打开文件](<../../python/winpilot_home/tests/test_client_random_tasks_e2e_harness.py>)

- 分类/语言：`test` / Python
- 规模：187 行，5,609 bytes
- SHA-256：`4074e5ebbf5f0594fb3eca6bdd10a2f59543da287cbac9b8d05353424d8882ed`
- 源码事实摘要：验证 test client random tasks e2e harness 对应的行为、回归或合同。
- 顶层符号/结构：`_load_harness`, `_paint_frame`, `test_find_window_prefers_foreground_then_topmost_z_order`, `test_chat_then_task_phrasings_match_exact_file_oracle`, `test_claim_texts_are_bound_to_the_exact_run`, `test_uia_text_reader_uses_declared_uiautomation_dependency`, `test_paint_oracle_accepts_a_horizontal_canvas_stroke`, `test_paint_verifier_focuses_live_window_before_terminal_capture`
- 直接静态依赖：`__future__`, `importlib.util`, `json`, `sys`, `pathlib`, `PIL`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/tests/test_client_thread_connector_e2e_harness.py -->
### `python/winpilot_home/tests/test_client_thread_connector_e2e_harness.py`

[打开文件](<../../python/winpilot_home/tests/test_client_thread_connector_e2e_harness.py>)

- 分类/语言：`test` / Python
- 规模：787 行，25,318 bytes
- SHA-256：`93ace0f03ebc582efad7f655ca3d39e2b63a582d45f8c3f0308f4d442e2b525b`
- 源码事实摘要：验证 test client thread connector e2e harness 对应的行为、回归或合同。
- 顶层符号/结构：`_load_harness`, `test_fixture_is_one_explicit_stdio_server_and_keeps_token_out_of_goal`, `test_preopened_notepad_task_keeps_target_and_file_path_out_of_goal`, `test_real_stdio_fixture_advertises_and_executes_one_read_only_tool`, `test_invocation_oracle_filters_calls_before_the_case_boundary`, `test_thread_evidence_binds_exact_run_turn_order_and_connector_reply`, `test_late_context_probe_keeps_marker_close_and_requires_exact_recall`, `test_thread_case_reuses_base_native_frontend_path_and_same_thread`, `test_file_oracle_requires_fresh_connector_call_and_exact_saved_value`, `test_usage_projection_reports_cache_tokens_without_model_content`, `test_connector_probe_projects_raw_canonical_receipt_chain_without_content`, `test_history_projection_and_text_turn_side_effect_oracle`, `test_history_ab_controls_use_observed_fingerprints_and_actual_history`, `test_history_ab_cleanup_interference_has_priority_over_arm_outcomes`，另有 3 项
- 直接静态依赖：`__future__`, `importlib.util`, `json`, `sys`, `time`, `pathlib`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/tests/test_client_visual_e2e_harness.py -->
### `python/winpilot_home/tests/test_client_visual_e2e_harness.py`

[打开文件](<../../python/winpilot_home/tests/test_client_visual_e2e_harness.py>)

- 分类/语言：`test` / Python
- 规模：869 行，29,386 bytes
- SHA-256：`a47b1c7d6d116eeade5cd304599e38576fec4799eb7d876080114e2fa5378c4a`
- 源码事实摘要：验证 test client visual e2e harness 对应的行为、回归或合同。
- 顶层符号/结构：`_load_harness`, `_state`, `test_real_client_visual_matrix_is_generic_and_covers_gesture_shapes`, `test_client_case_specs_preserve_spoken_gui_requirements`, `test_target_focus_preserves_bound_geometry_unless_minimized`, `test_external_verdict_requires_precise_click_geometry_and_no_wrong_click`, `test_external_verdict_requires_complete_trusted_ordered_click_progression`, `test_external_verdict_requires_server_verified_mixed_scroll_quiz`, `test_external_verdict_requires_complete_object_placement`, `test_external_verdict_checks_slider_band_and_corridor_path`, `test_corridor_zero_clearance_is_valid`, `test_external_verdict_accepts_a_complete_held_pointer_retry`, `test_external_verdict_accepts_trusted_two_stroke_drawing_with_short_final_stroke`, `test_external_verdict_accepts_keyboard_mouse_interleaving`，另有 14 项
- 直接静态依赖：`__future__`, `importlib.util`, `inspect`, `sys`, `pathlib`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/tests/test_clipboard_ops.py -->
### `python/winpilot_home/tests/test_clipboard_ops.py`

[打开文件](<../../python/winpilot_home/tests/test_clipboard_ops.py>)

- 分类/语言：`test` / Python
- 规模：246 行，7,156 bytes
- SHA-256：`dfff24d273b9dc8d2f4381469f396dcce25bf467d84f05ec36285c479b158081`
- 源码事实摘要：验证 test clipboard ops 对应的行为、回归或合同。
- 顶层符号/结构：`_FakeUser32`, `_MixedVisibilityUser32`, `test_capture_screen_delegates_relative_path_to_backend`, `test_list_windows_keeps_frontmost_24_with_truthful_facts`, `test_list_windows_does_not_depend_on_ctypes_wintypes_side_effect`, `test_list_windows_reports_deduped_background_applications`, `_FakeKernel32`, `test_process_name_uses_pid_path_basename_and_fails_empty`
- 直接静态依赖：`__future__`, `types`, `winpilot.action`, `winpilot.vnext`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/tests/test_coding_reuse_exclusion.py -->
### `python/winpilot_home/tests/test_coding_reuse_exclusion.py`

[打开文件](<../../python/winpilot_home/tests/test_coding_reuse_exclusion.py>)

- 分类/语言：`test` / Python
- 规模：103 行，4,669 bytes
- SHA-256：`8910efc8ed9f7af286a3db7d92721b6c1a459e2602cdfbcbd66965e788329872`
- 源码事实摘要：Coding tasks: excluded from action-replay reuse, but keep factual notes.
- 顶层符号/结构：`fresh_memory`, `test_notes_only_excludes_action_recipes`, `test_manual_notes_survive_failure_feedback`, `test_auto_recipe_still_evicted_on_repeated_failure`, `test_volatile_window_state_tasks_do_not_reuse_or_record`, `test_is_coding_task_detection`
- 直接静态依赖：`importlib`, `pytest`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/tests/test_coding_run_gate.py -->
### `python/winpilot_home/tests/test_coding_run_gate.py`

[打开文件](<../../python/winpilot_home/tests/test_coding_run_gate.py>)

- 分类/语言：`test` / Python
- 规模：38 行，1,451 bytes
- SHA-256：`f163ca7b2ffc950daa0a933f6521e2beb87397d56c5f46ae4543ffd209e96989`
- 源码事实摘要：验证 test coding run gate 对应的行为、回归或合同。
- 顶层符号/结构：`test_clone_run_task_requires_real_run_attempt`, `test_clone_run_task_accepts_runtime_check_as_attempt`, `test_report_filename_does_not_trigger_repo_run_gate`
- 直接静态依赖：—
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/tests/test_coding_tools.py -->
### `python/winpilot_home/tests/test_coding_tools.py`

[打开文件](<../../python/winpilot_home/tests/test_coding_tools.py>)

- 分类/语言：`test` / Python
- 规模：78 行，2,945 bytes
- SHA-256：`c16a568f48708f14c55215eb167070bc4872bd63a7112ea39ad58d60d266840e`
- 源码事实摘要：CodingSession file/repo tools — confined to a workspace, deterministic.
- 顶层符号/结构：`sess`, `test_schemas_shape`, `test_write_then_read_roundtrip`, `test_apply_patch_unique`, `test_apply_patch_not_found`, `test_apply_patch_ambiguous`, `test_list_dir_skips_junk`, `test_search_text_finds_matches`, `test_path_escape_is_rejected`, `test_done_and_fail_are_terminal`, `test_unknown_tool`
- 直接静态依赖：`pathlib`, `winpilot.agent.coding_tools`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/tests/test_comms_done_gate.py -->
### `python/winpilot_home/tests/test_comms_done_gate.py`

[打开文件](<../../python/winpilot_home/tests/test_comms_done_gate.py>)

- 分类/语言：`test` / Python
- 规模：528 行，20,385 bytes
- SHA-256：`b13d409808c363d31e8de54c5ef2127971ab2674fb97f756df6bb2f80779c793`
- 源码事实摘要：Tests for communication task done-gate fixes.
- 顶层符号/结构：`mocked_visual_judge_capability`, `test_communication_mode_honors_draft_only_instruction`, `test_draft_communication_accepts_grounded_typed_payload_without_readback`, `test_draft_communication_rejects_any_successful_send_action`, `test_draft_goal_does_not_install_a_task_keyword_action_interceptor`, `_make_runner`, `test_comms_done_gate_no_payload_requires_read_text`, `test_comms_done_gate_no_payload_rejects_read_text_without_send_action`, `test_communication_send_requires_bare_enter_or_return`, `test_communication_send_accepts_successful_bare_enter_or_return`, `test_unconsumed_press_enter_fields_do_not_count_as_send_actions`, `test_one_enter_proves_only_the_latest_typed_turn_without_deciding_completion`, `test_batch_communication_requires_literal_success_for_each_nested_action`, `test_execute_subgoal_counts_only_successful_executed_send_child`，另有 12 项
- 直接静态依赖：`json`, `threading`, `unittest.mock`, `pytest`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/tests/test_connector_gateway.py -->
### `python/winpilot_home/tests/test_connector_gateway.py`

[打开文件](<../../python/winpilot_home/tests/test_connector_gateway.py>)

- 分类/语言：`test` / Python
- 规模：464 行，14,438 bytes
- SHA-256：`450ff6a8833465fb822dca260f9b04e3206052784494a8ac7d09752958dafc4d`
- 源码事实摘要：验证 test connector gateway 对应的行为、回归或合同。
- 顶层符号/结构：`_tool`, `_discovery`, `_canonical_sha256`, `test_available_reflects_only_explicit_connector_configuration`, `test_mcp_config_values_resolve_whole_environment_references`, `test_explicit_mcp_config_has_highest_server_name_precedence`, `test_mcp_tool_cache_is_scoped_to_exact_server_configuration`, `test_mcp_coroutine_obeys_one_timeout_or_cancel_boundary`, `test_lazy_search_and_exact_read_only_call_emit_bounded_receipt`, `test_unknown_or_external_tool_is_blocked`, `test_changed_descriptor_invalidates_exact_tool_reference`, `test_argument_json_fails_closed_before_remote_call`, `test_large_remote_result_is_hashed_then_bounded`, `test_tampered_reference_is_blocked_without_discovery_or_call`，另有 1 项
- 直接静态依赖：`__future__`, `asyncio`, `hashlib`, `json`, `threading`, `time`, `contextlib`, `pathlib`, `types`, `pytest`, `winpilot.integrations.connector_gateway`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/tests/test_control_plane_auth.py -->
### `python/winpilot_home/tests/test_control_plane_auth.py`

[打开文件](<../../python/winpilot_home/tests/test_control_plane_auth.py>)

- 分类/语言：`test` / Python
- 规模：61 行，2,367 bytes
- SHA-256：`0b985ee5f26ce4f995d9e1b6d9bb7c3ed152050ddcab65ccc2b42355b7602f75`
- 源码事实摘要：验证 test control plane auth 对应的行为、回归或合同。
- 顶层符号/结构：`test_control_plane_requires_bearer_token_when_configured`, `test_control_plane_allows_valid_renderer_cors_preflight`, `test_control_plane_websocket_rejects_wrong_token`
- 直接静态依赖：`__future__`, `pytest`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/tests/test_coordinate_diagnostics.py -->
### `python/winpilot_home/tests/test_coordinate_diagnostics.py`

[打开文件](<../../python/winpilot_home/tests/test_coordinate_diagnostics.py>)

- 分类/语言：`test` / Python
- 规模：105 行，3,753 bytes
- SHA-256：`4df9f5d1211eb96d570e1588406acc41eb0dc2fab71c17d874ff6041c1156855`
- 源码事实摘要：验证 test coordinate diagnostics 对应的行为、回归或合同。
- 顶层符号/结构：`test_coordinate_diagnostics_reports_capture_mismatch`, `test_coordinate_click_test_uses_real_executor_click_and_verifies_text`, `test_coordinate_click_test_rejects_real_cursor_offset`
- 直接静态依赖：`json`, `numpy`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/tests/test_desktop_action_queue.py -->
### `python/winpilot_home/tests/test_desktop_action_queue.py`

[打开文件](<../../python/winpilot_home/tests/test_desktop_action_queue.py>)

- 分类/语言：`test` / Python
- 规模：923 行，28,801 bytes
- SHA-256：`1512bdc3a50e7f17477eee01c5535fa0906b9cf7b9d76822a41255182b08fa49`
- 源码事实摘要：验证 test desktop action queue 对应的行为、回归或合同。
- 顶层符号/结构：`test_desktop_action_queue_serializes_parallel_jobs`, `test_desktop_action_queue_allows_reentrant_nested_jobs`, `_run_competing_jobs`, `test_window_scoped_actions_parallelize_only_across_different_hwnds`, `test_physical_actions_remain_globally_serialized_across_hwnds`, `test_physical_action_conflicts_with_same_window_but_not_other_window`, `test_unknown_physical_target_blocks_every_window_resource`, `test_clipboard_resource_is_independent_but_single_writer`, `test_nested_action_cannot_escalate_its_resource_lease`, `test_desktop_action_queue_marks_controller_lease_for_nested_coalescing`, `test_desktop_action_queue_emits_bus_events_and_errors`, `test_public_click_uses_one_outer_queue_event`, `test_typed_click_uses_one_outer_queue_event`, `test_window_scoped_decorator_binds_positional_hwnd`，另有 18 项
- 直接静态依赖：`threading`, `time`, `pytest`, `winpilot.action`, `winpilot.action.desktop_queue`, `winpilot.action.raw_device`, `winpilot.utils.log`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/tests/test_desktop_file_tools.py -->
### `python/winpilot_home/tests/test_desktop_file_tools.py`

[打开文件](<../../python/winpilot_home/tests/test_desktop_file_tools.py>)

- 分类/语言：`test` / Python
- 规模：218 行，8,471 bytes
- SHA-256：`b5ff733e978bf2bb037d18a812e7eb515daa27334f3d83f3e2773510a1cd911c`
- 源码事实摘要：验证 test desktop file tools 对应的行为、回归或合同。
- 顶层符号/结构：`test_screenshot_saves_target_window_without_shell`, `test_flow_managed_screenshot_uses_run_artifact_store`, `test_flow_managed_region_screenshot_preserves_physical_coordinates`, `test_explicit_artifact_regions_fail_closed_before_capture`, `test_managed_artifact_bridges_preserve_invalid_region_for_owner_validation`, `test_artifact_capture_rejects_frame_dimension_drift`, `test_model_screenshot_without_managed_mode_keeps_base64_path`
- 直接静态依赖：`json`, `sys`, `numpy`, `PIL`, `pytest`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/tests/test_desktop_geometry.py -->
### `python/winpilot_home/tests/test_desktop_geometry.py`

[打开文件](<../../python/winpilot_home/tests/test_desktop_geometry.py>)

- 分类/语言：`test` / Python
- 规模：25 行，1,090 bytes
- SHA-256：`19fe189a9eeaa4d14e5773f52edc2ad0468ed2959d6a83f25eb159401fb7307a`
- 源码事实摘要：验证 test desktop geometry 对应的行为、回归或合同。
- 顶层符号/结构：`test_virtual_desktop_clamp_preserves_negative_monitor_coordinates`, `test_desktop_geometry_maps_capture_pixels_to_physical_screen`
- 直接静态依赖：`__future__`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/tests/test_desktop_image_evidence.py -->
### `python/winpilot_home/tests/test_desktop_image_evidence.py`

[打开文件](<../../python/winpilot_home/tests/test_desktop_image_evidence.py>)

- 分类/语言：`test` / Python
- 规模：290 行，10,867 bytes
- SHA-256：`59e6cef5b223e9809b207da2f10af8701ce618467b94a7cff796d8939efb4a0d`
- 源码事实摘要：验证 test desktop image evidence 对应的行为、回归或合同。
- 顶层符号/结构：`_jpeg_b64`, `test_fold_base64_adds_text_only_note_and_nonvisual_summary`, `test_trace_saves_draw_strokes_before_after_screenshots`, `test_trace_saves_batch_child_before_after_screenshots`, `test_trace_writer_is_async_and_pairs_interleaved_action_ids`, `test_trace_after_screenshot_does_not_reuse_pre_action_daemon_frame`, `test_strict_trace_seals_after_frame_before_next_action`, `test_fold_base64_adds_likely_canvas_summary_for_mixed_zoom`
- 直接静态依赖：`base64`, `json`, `time`, `cv2`, `numpy`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/tests/test_draw_strokes_feedback.py -->
### `python/winpilot_home/tests/test_draw_strokes_feedback.py`

[打开文件](<../../python/winpilot_home/tests/test_draw_strokes_feedback.py>)

- 分类/语言：`test` / Python
- 规模：457 行，16,170 bytes
- SHA-256：`f25f9f0160a08c2e7f25b8dfe69d88e437d914c17338394729638c3530f20af7`
- 源码事实摘要：验证 test draw strokes feedback 对应的行为、回归或合同。
- 顶层符号/结构：`test_draw_stroke_normalizer_accepts_flat_segments_and_unwrapped_polyline`, `test_draw_strokes_visual_change_marks_action_verified`, `test_draw_strokes_rejects_cursor_only_pixel_delta`, `test_draw_strokes_parks_pointer_before_current_pixel_path_proof`, `test_compacted_pixel_effect_does_not_look_like_goal_verification`, `test_draw_strokes_without_pixel_change_is_a_fail_closed_result`, `test_draw_strokes_default_motion_budget_is_shared_across_many_paths`, `test_draw_strokes_returns_frame_preparation_evidence`, `test_normalized_strokes_map_through_current_target_surface`, `test_normalized_strokes_reject_missing_or_ambiguous_surface`, `test_normalized_strokes_reject_out_of_bounds_and_mixed_sources`, `test_pre_action_surface_cache_survives_drawn_pixels_for_same_window`, `test_physical_strokes_remain_compatible_and_prime_surface_cache`
- 直接静态依赖：`json`, `types`, `cv2`, `numpy`, `pytest`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/tests/test_drawing_closed_loop_recovery.py -->
### `python/winpilot_home/tests/test_drawing_closed_loop_recovery.py`

[打开文件](<../../python/winpilot_home/tests/test_drawing_closed_loop_recovery.py>)

- 分类/语言：`test` / Python
- 规模：319 行，12,266 bytes
- SHA-256：`be539515a63683b05605ef426f3f84b8075dea96a341aac745cd3f604618314b`
- 源码事实摘要：验证 test drawing closed loop recovery 对应的行为、回归或合同。
- 顶层符号/结构：`test_done_rejection_does_not_install_a_drawing_specific_mutation_guard`, `test_model_draw_batch_is_forwarded_without_one_repair_compilation`, `test_tool_session_preserves_partial_subgoal_output_without_progress_classification`, `test_huge_drawing_result_compacts_outcome_and_actual_colors_before_geometry`, `test_blocked_bulk_undo_batch_compaction_keeps_destructive_outcome`, `test_folded_checkpoint_keeps_facts_and_drops_controller_tactics`, `test_repeated_no_progress_subgoal_always_reaches_the_action_owner`
- 直接静态依赖：`json`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/tests/test_eval_summary.py -->
### `python/winpilot_home/tests/test_eval_summary.py`

[打开文件](<../../python/winpilot_home/tests/test_eval_summary.py>)

- 分类/语言：`test` / Python
- 规模：1783 行，62,487 bytes
- SHA-256：`d2b43e9c27aeb1b91fbc504c6d66db204a7805945849c0a6d7a44caa3a1ce8c4`
- 源码事实摘要：验证 test eval summary 对应的行为、回归或合同。
- 顶层符号/结构：`test_summarize_events_counts_tools_usage_and_slowest`, `test_aggregate_reports_totals_usage_and_slow_tools`, `test_summarize_events_extracts_fast_gui_metrics`, `test_summarize_events_includes_native_visual_frame_latency`, `test_summarize_events_extracts_execute_subgoal_metrics`, `test_summarize_events_includes_batch_action_screenshots`, `test_summarize_events_reports_low_latency_metrics`, `test_summarize_events_ignores_empty_vlm_placeholder`, `test_aggregate_reports_treats_policy_failure_as_not_passed`, `test_aggregate_reports_treats_unverified_done_as_not_passed`, `test_aggregate_reports_requires_passing_performance_budget`, `test_aggregate_reports_never_counts_ungrounded_or_stopped_runs`, `test_gui_eval_provenance_is_reproducible_and_excludes_secrets`, `test_gui_eval_marks_missing_checks_as_ungrounded`，另有 26 项
- 直接静态依赖：`winpilot.eval`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/tests/test_execution_context_handoff.py -->
### `python/winpilot_home/tests/test_execution_context_handoff.py`

[打开文件](<../../python/winpilot_home/tests/test_execution_context_handoff.py>)

- 分类/语言：`test` / Python
- 规模：344 行，10,741 bytes
- SHA-256：`3963db96060c9cc7e81bc005dfad8ead9979124c9c6f5b752ae7b2acbcae7480`
- 源码事实摘要：验证 test execution context handoff 对应的行为、回归或合同。
- 顶层符号/结构：`_inputs`, `test_execution_context_normalization_keeps_only_runtime_contract_fields`, `test_native_run_fails_before_model_when_control_surface_did_not_yield`, `test_native_run_rejects_control_surface_as_active_target_after_handoff`, `test_native_run_accepts_a_different_foreground_application`, `test_api_run_without_native_handoff_context_keeps_existing_semantics`, `test_foreground_capture_records_external_window_after_client_yields`, `test_foreground_inventory_uses_visible_dwm_bounds_for_maximized_window`, `test_foreground_capture_rejects_the_native_client_process`, `test_foreground_capture_classifies_unavailable_window_inventory`, `test_foreground_capture_falls_back_to_windows_shell_desktop`, `test_windows_shell_context_reads_localized_win32_identity`, `test_native_desktop_workspace_does_not_bind_arbitrary_foreground_window`, `test_native_desktop_workspace_reports_missing_shell_without_foreground_fallback`，另有 3 项
- 直接静态依赖：`__future__`, `winpilot.tasking.execution_context`, `winpilot.utils.screenshot`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/tests/test_executor_launch.py -->
### `python/winpilot_home/tests/test_executor_launch.py`

[打开文件](<../../python/winpilot_home/tests/test_executor_launch.py>)

- 分类/语言：`test` / Python
- 规模：51 行，1,920 bytes
- SHA-256：`2ae049eea5deb3db610ef5b74d6c9422076c3821bff0d1fec7031336d684c85d`
- 源码事实摘要：验证 test executor launch 对应的行为、回归或合同。
- 顶层符号/结构：`test_launch_url_uses_windows_protocol_handler`, `test_launch_browser_wrapper_extracts_url`, `test_launch_non_url_keeps_shell_path`
- 直接静态依赖：—
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/tests/test_failure_classifier.py -->
### `python/winpilot_home/tests/test_failure_classifier.py`

[打开文件](<../../python/winpilot_home/tests/test_failure_classifier.py>)

- 分类/语言：`test` / Python
- 规模：664 行，21,107 bytes
- SHA-256：`06589e0bae689b05093d83713156cf7264825a4823e4f80828fa996054151d85`
- 源码事实摘要：验证 test failure classifier 对应的行为、回归或合同。
- 顶层符号/结构：`_classify`, `test_scope_guard_foreground_mismatch_is_recorded_as_window_focus_error`, `test_visual_grounding_block_is_not_misreported_as_action_success`, `test_execute_subgoal_needs_target_window_is_window_recovery_failure`, `test_focus_window_missing_live_hwnd_is_window_not_found`, `test_click_element_anchor_missing_with_candidates_is_control_not_found`, `test_successful_pending_mutation_is_not_reclassified_as_failure`, `test_candidate_labels_do_not_mean_control_not_found_when_root_action_succeeded`, `test_located_visual_servo_needing_observe_is_not_control_not_found`, `test_format_postcondition_handoff_is_not_a_recovery_failure`, `test_ocr_missing_text_verify_detail_is_ocr_or_verify_failed`, `test_screen_delta_foreground_mismatch_is_window_focus`, `test_wait_change_or_wait_stable_unmet_is_wait_timeout`, `test_successful_wait_stable_timeout_setting_is_not_wait_timeout`，另有 17 项
- 直接静态依赖：`__future__`, `json`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/tests/test_false_success_guards.py -->
### `python/winpilot_home/tests/test_false_success_guards.py`

[打开文件](<../../python/winpilot_home/tests/test_false_success_guards.py>)

- 分类/语言：`test` / Python
- 规模：3518 行，125,328 bytes
- SHA-256：`060a4a0a46865a27a08a642018fdd519bb9d3ce91ef37ec4bff02739e5958554`
- 源码事实摘要：验证 test false success guards 对应的行为、回归或合同。
- 顶层符号/结构：`coordinate_semantic_snap_enabled`, `mocked_visual_judge_capability`, `test_visual_done_without_declared_vision_judge_fails_closed`, `test_track_verification_requires_literal_boolean_true`, `test_track_verification_accepts_literal_boolean_true`, `test_xlsx_artifact_verifier_checks_cells_formulas_results_and_bold_rows`, `test_visible_state_done_accepts_fresh_screen_ocr_spacing`, `test_visible_state_targets_extract_historical_list`, `test_visible_state_targets_ignore_descriptive_ui_nouns`, `test_visible_state_targets_ignore_english_articles_before_visual_descriptions`, `test_done_defers_missing_visible_payload_to_fresh_native_judge`, `test_done_allows_present_distinctive_literals_to_reach_judge`, `test_visible_evidence_ledger_ignores_negative_query_hits`, `test_visible_evidence_ignores_reducer_why_and_trace_path_metadata`，另有 97 项
- 直接静态依赖：`json`, `types`, `pytest`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/tests/test_fast_controller.py -->
### `python/winpilot_home/tests/test_fast_controller.py`

[打开文件](<../../python/winpilot_home/tests/test_fast_controller.py>)

- 分类/语言：`test` / Python
- 规模：8202 行，293,729 bytes
- SHA-256：`3e59a6a8de8d2a5db58f7ca31d0e2e99130622e8336d4159348efb54cf9ee2ee`
- 源码事实摘要：验证 test fast controller 对应的行为、回归或合同。
- 顶层符号/结构：`test_batch_observe_actions_are_allowed_or_internal`, `test_batch_contract_exposes_real_continuous_input_primitives`, `test_batch_contract_rejects_unbalanced_or_unbounded_continuous_input`, `test_batch_contract_accepts_explicit_cross_frame_pointer_transitions`, `test_controller_preserves_only_an_explicit_cross_frame_pointer_session`, `test_batch_pointer_continuation_preserves_one_absolute_lease`, `test_failed_batch_does_not_silently_forget_a_held_pointer`, `test_controller_releases_held_pointer_when_transaction_aborts`, `test_controller_executes_continuous_input_sequence_in_one_batch`, `test_changed_regions_are_screen_space`, `test_element_cache_locates_visual_anchor_in_region`, `test_element_cache_primary_exact_label_beats_exact_fallback`, `test_batch_mutation_invalidates_pre_action_observe_cache_before_read_text`, `test_element_cache_row_header_rejects_name_box_substring`，另有 222 项
- 直接静态依赖：`json`, `types`, `numpy`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/tests/test_frame_authority.py -->
### `python/winpilot_home/tests/test_frame_authority.py`

[打开文件](<../../python/winpilot_home/tests/test_frame_authority.py>)

- 分类/语言：`test` / Python
- 规模：264 行，8,210 bytes
- SHA-256：`3aa876a0814e84068841b093a1034f41f3550f45e266849a0bb78ba07d08a596`
- 源码事实摘要：验证 test frame authority 对应的行为、回归或合同。
- 顶层符号/结构：`_commit`, `test_frame_authority_commits_one_immutable_current_state`, `test_frame_authority_rejects_lost_update`, `test_run_if_current_pins_the_complete_identity_and_calls_once`, `test_run_if_current_rejects_each_stale_identity_field_without_calling`, `test_run_if_current_rejects_when_no_frame_is_current`, `test_run_if_current_propagates_callback_exception_and_releases_pin`, `test_run_if_current_blocks_other_thread_frame_commit_until_callback_returns`, `test_run_if_current_fails_fast_on_same_thread_reentrant_frame_commit`, `test_run_if_current_fails_fast_on_same_thread_nested_pin`, `test_runner_and_session_share_the_same_frame_authority`, `test_spatial_grounding_rejects_a_superseded_frame_in_the_same_epoch`
- 直接静态依赖：`dataclasses`, `threading`, `types`, `pytest`, `winpilot.visual_runtime.frame_authority`, `winpilot.agent.loop`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/tests/test_frame_observer.py -->
### `python/winpilot_home/tests/test_frame_observer.py`

[打开文件](<../../python/winpilot_home/tests/test_frame_observer.py>)

- 分类/语言：`test` / Python
- 规模：258 行，8,552 bytes
- SHA-256：`92efe9d432c27f6aaf42f69588e60c7cdb4fcda3e4f1c1f63d006a71c5e327a1`
- 源码事实摘要：验证 test frame observer 对应的行为、回归或合同。
- 顶层符号/结构：`_observer`, `test_provided_region_and_image_are_committed_as_one_immutable_observation`, `test_matching_cached_capture_reuses_exact_image_region_pair`, `test_scope_selection_and_capture_are_injected_without_task_semantics`, `test_initial_prebind_uses_injected_settled_capture`, `test_tiny_region_and_empty_capture_fail_without_committing`, `test_commit_rejects_authority_change_after_capture`, `test_legacy_visual_frame_delegates_capture_and_commit_to_shared_owner`
- 直接静态依赖：`dataclasses`, `pytest`, `winpilot.visual_runtime.frame_authority`, `winpilot.utils.image_capture`, `winpilot.utils.screenshot`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/tests/test_fusion_modal_skip.py -->
### `python/winpilot_home/tests/test_fusion_modal_skip.py`

[打开文件](<../../python/winpilot_home/tests/test_fusion_modal_skip.py>)

- 分类/语言：`test` / Python
- 规模：19 行，910 bytes
- SHA-256：`5719536db5c0502ecef559053a9cc08b3b3d85c463fa7ce9f4464b543b9c02ce`
- 源码事实摘要：验证 test fusion modal skip 对应的行为、回归或合同。
- 顶层符号/结构：`test_foreground_modal_overlay_detection`, `test_no_modal_overlay_when_foreground_is_target`
- 直接静态依赖：—
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/tests/test_general_agent_regressions.py -->
### `python/winpilot_home/tests/test_general_agent_regressions.py`

[打开文件](<../../python/winpilot_home/tests/test_general_agent_regressions.py>)

- 分类/语言：`test` / Python
- 规模：1567 行，56,588 bytes
- SHA-256：`8cd7dc6b5f672c0b7c137b109383fc6b2e78aeb2618b143b5171d1eafa017b20`
- 源码事实摘要：验证 test general agent regressions 对应的行为、回归或合同。
- 顶层符号/结构：`test_composite_desktop_requests_keep_the_whole_goal`, `test_explicit_drawing_application_is_not_replaced_by_paint`, `test_unnamed_surface_output_uses_the_same_general_stage_from_each_entrypoint`, `test_gui_entrypoint_reaches_the_live_frame_without_compile_time_clarification`, `test_same_board_different_user_intent_does_not_switch_runtime_mode`, `test_execute_desktop_stage_passes_its_model_tools_to_runtime_profile`, `test_desktop_answer_can_finish_without_inventing_a_gui_mutation`, `test_unseen_slider_drag_uses_generic_visual_interaction_contract`, `test_hold_and_path_phrasing_uses_generic_visual_interaction`, `test_deictic_live_surface_is_not_parsed_as_an_application`, `test_named_application_before_hold_remains_an_application_constraint`, `test_explanation_about_holding_and_dragging_is_not_a_live_gui_command`, `test_visual_surface_noun_does_not_turn_drag_into_drawing`, `test_unknown_gameplay_keeps_full_goal_and_all_spatial_primitives`，另有 36 项
- 直接静态依赖：`__future__`, `json`, `threading`, `time`, `types`, `pytest`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/tests/test_graphical_affordances.py -->
### `python/winpilot_home/tests/test_graphical_affordances.py`

[打开文件](<../../python/winpilot_home/tests/test_graphical_affordances.py>)

- 分类/语言：`test` / Python
- 规模：65 行，1,898 bytes
- SHA-256：`e6456a94b83c82604485ea5697f01205d9f903c9aabbefe2ff79ef5e37c391c6`
- 源码事实摘要：验证 test graphical affordances 对应的行为、回归或合同。
- 顶层符号/结构：`_jpeg`, `_glyph`, `test_repeated_pixel_glyphs_form_one_unlabeled_layout_family`, `test_flat_pixels_do_not_invent_graphical_controls`
- 直接静态依赖：`__future__`, `base64`, `cv2`, `numpy`, `winpilot.agent.graphical_affordances`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/tests/test_human_recorder.py -->
### `python/winpilot_home/tests/test_human_recorder.py`

[打开文件](<../../python/winpilot_home/tests/test_human_recorder.py>)

- 分类/语言：`test` / Python
- 规模：407 行，14,009 bytes
- SHA-256：`0f4be5c1f82f975958cf9d1e9e7eaff6a19ed1133be3faf847a0c2bb079fe3d6`
- 源码事实摘要：Human input recording into replay-safe .wps.json scripts.
- 顶层符号/结构：`test_printable_keys_coalesce_into_one_type_text`, `test_space_is_buffered_as_text`, `test_special_key_flushes_then_emits_hotkey`, `test_modifier_combo_emits_hotkey_and_flushes_buffer`, `test_shift_alone_is_not_a_combo`, `test_click_carries_selector`, `test_click_without_selector_has_no_text`, `test_drag_when_release_is_far`, `test_typed_text_before_click_is_flushed_first`, `test_ctrl_keycode_control_character_normalizes_to_letter`, `test_text_paste_reuses_type_text_with_frozen_clipboard_content`, `test_non_text_or_unavailable_paste_keeps_normalized_hotkey`, `test_printable_text_flushes_when_the_target_window_changes`, `_window`，另有 13 项
- 直接静态依赖：`json`, `sys`, `types`, `pytest`, `winpilot.recorder`, `winpilot.recorder.human`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/tests/test_human_replay_integration.py -->
### `python/winpilot_home/tests/test_human_replay_integration.py`

[打开文件](<../../python/winpilot_home/tests/test_human_replay_integration.py>)

- 分类/语言：`test` / Python
- 规模：69 行，2,713 bytes
- SHA-256：`ff9c38b59386244a220faeea3ce16e09d4e43209a7dea1fef910d33d59154cd3`
- 源码事实摘要：Integration: human-recorded steps → validate → offline replay.
- 顶层符号/结构：`test_recorded_steps_validate_and_replay`
- 直接静态依赖：`json`, `winpilot`, `winpilot.recorder.human`, `winpilot.recorder`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/tests/test_image_ink_stats.py -->
### `python/winpilot_home/tests/test_image_ink_stats.py`

[打开文件](<../../python/winpilot_home/tests/test_image_ink_stats.py>)

- 分类/语言：`test` / Python
- 规模：161 行，5,858 bytes
- SHA-256：`3a0559582b155c1b383a259efa33b35afcccfb5f974ae3db94ecfee8d2fe9c00`
- 源码事实摘要：验证 test image ink stats 对应的行为、回归或合同。
- 顶层符号/结构：`test_image_ink_stats_detects_nonblank_image`, `test_image_ink_stats_detects_blankish_image`, `test_image_color_stats_detects_requested_colors`, `test_color_presence_reports_largest_color_component`, `test_likely_canvas_bbox_prefers_large_light_surface`, `test_likely_canvas_bbox_excludes_vertical_overlay_on_work_surface`, `test_likely_canvas_bbox_does_not_shrink_after_closed_ink_is_added`, `test_likely_canvas_bbox_keeps_internal_regions_split_by_existing_content`, `test_credible_light_work_surface_rejects_ambiguous_panels`, `test_credible_light_work_surface_rejects_nested_light_modal`
- 直接静态依赖：`cv2`, `numpy`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/tests/test_input_sequence.py -->
### `python/winpilot_home/tests/test_input_sequence.py`

[打开文件](<../../python/winpilot_home/tests/test_input_sequence.py>)

- 分类/语言：`test` / Python
- 规模：119 行，3,229 bytes
- SHA-256：`4399d4c23323a57b375e4e10c161fbd483482fc6226589956c5fd4eb8ca63be7`
- 源码事实摘要：验证 test input sequence 对应的行为、回归或合同。
- 顶层符号/结构：`test_parser_accepts_human_friendly_mixed_device_timeline`, `test_pointer_buttons_accept_inline_coordinates_and_default_left`, `test_parser_leaves_device_validation_to_the_physical_event_contract`, `test_parser_accepts_observed_click_and_two_word_variants`, `test_parser_rejects_only_text_that_cannot_form_an_event`
- 直接静态依赖：`__future__`, `pytest`, `winpilot.visual_runtime.input_sequence`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/tests/test_interaction_action_grounding.py -->
### `python/winpilot_home/tests/test_interaction_action_grounding.py`

[打开文件](<../../python/winpilot_home/tests/test_interaction_action_grounding.py>)

- 分类/语言：`test` / Python
- 规模：1452 行，53,354 bytes
- SHA-256：`ac8a1ac9d5aa506a16626c97dd64280553b7459dac32051a0a1a4f872f2e08a8`
- 源码事实摘要：Focused contract tests for generic typed visual action grounding.
- 顶层符号/结构：`_decision`, `_mapping`, `_mapping_many`, `_coloured_draw`, `test_draw_colour_obligation_lowers_to_controller_selection_before_paths`, `test_draw_preparation_can_be_committed_before_spatial_mapping`, `test_explicit_matching_colour_selection_is_not_duplicated`, `test_draw_without_colour_keeps_single_physical_gesture`, `test_multiple_freehand_paths_share_one_input_mode_precondition`, `test_tiny_closed_local_draw_path_is_rejected_before_physical_input`, `test_visible_closed_local_draw_path_remains_valid`, `test_long_narrow_open_stroke_is_not_rejected_as_tiny`, `test_blocking_ambiguity_still_forbids_an_acting_plan`, `test_typed_spatial_resolution_requirement_is_not_execution_evidence_by_itself`，另有 47 项
- 直接静态依赖：`__future__`, `pathlib`, `random`, `pytest`, `pydantic`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/tests/test_interaction_runtime_components.py -->
### `python/winpilot_home/tests/test_interaction_runtime_components.py`

[打开文件](<../../python/winpilot_home/tests/test_interaction_runtime_components.py>)

- 分类/语言：`test` / Python
- 规模：3944 行，142,740 bytes
- SHA-256：`7848c1d0b399b92547781e2ba77412c8ddee78590321af76dd02f30b4dbb95c8`
- 源码事实摘要：Focused component tests for generic typed visual interaction runtime.
- 顶层符号/结构：`_decision`, `_stage_output`, `_legacy_interaction_contract`, `_legacy_interaction_graph`, `_legacy_stage`, `test_persistent_stage_graph_accepts_post_action_reobserve_but_not_idle_loop`, `test_compiler_routes_visual_interaction_to_one_model_owned_desktop_stage`, `test_frozen_legacy_contract_preserves_serialized_budgets`, `test_model_and_semantic_turn_budgets_fail_with_precise_dimensions`, `test_frozen_legacy_graph_routes_recovery_through_fresh_observe`, `test_frozen_persistent_contract_keeps_wait_nonterminal`, `test_interaction_complete_branch_reaches_later_typed_stages`, `test_role_and_turn_ownership_cannot_authorize_the_other_actor_action`, `test_visual_planner_derives_external_watch_from_typed_continuation`，另有 59 项
- 直接静态依赖：`__future__`, `threading`, `time`, `pytest`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/tests/test_interactive_task_contract.py -->
### `python/winpilot_home/tests/test_interactive_task_contract.py`

[打开文件](<../../python/winpilot_home/tests/test_interactive_task_contract.py>)

- 分类/语言：`test` / Python
- 规模：396 行，16,673 bytes
- SHA-256：`5cda4e936b47e6a44d6d6dee25f471fdd83fe26d1a1d4d1864882d8b5ad84123`
- 源码事实摘要：验证 test interactive task contract 对应的行为、回归或合同。
- 顶层符号/结构：`_assert_single_visual_model_stage`, `test_short_visual_commands_reach_bounded_typed_interaction`, `test_equivalent_direct_manipulation_rewrites_have_the_same_contract_shape`, `test_action_words_in_descriptive_text_do_not_route_to_visual_interaction`, `test_non_visual_action_homonyms_do_not_become_gui_sessions`, `test_high_confidence_semantic_extractor_can_veto_structural_candidate`, `test_shared_visual_interaction_uses_one_visual_model_stage`, `test_minimal_shared_turn_handoff_observes_scene_before_acting`, `test_turn_like_prose_without_complete_shared_session_does_not_activate`, `test_actor_assignments_keep_role_ownership_instead_of_flattening_values`, `test_interaction_routing_requires_a_live_relation_or_pointer_plus_visual_target`, `test_interaction_detection_generalizes_across_apps_languages_and_actions`, `test_semantic_extractor_can_supply_interaction_frame_without_keyword_routing`, `test_new_interaction_contract_does_not_precompute_ephemeral_protocol_state`，另有 8 项
- 直接静态依赖：`__future__`, `pytest`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/tests/test_keyboard_contracts.py -->
### `python/winpilot_home/tests/test_keyboard_contracts.py`

[打开文件](<../../python/winpilot_home/tests/test_keyboard_contracts.py>)

- 分类/语言：`test` / Python
- 规模：77 行，2,859 bytes
- SHA-256：`f2356fd72eb274eaaad414be988756969822c9d8446df60d44ddbf101178cbee`
- 源码事实摘要：Unit tests for hotkey argument normalization in the keyboard contracts.
- 顶层符号/结构：`test_normalize_accepts_array`, `test_normalize_accepts_plus_separated_string`, `test_normalize_accepts_comma_separated_string`, `test_normalize_accepts_json_array_string`, `test_normalize_accepts_bracketed_snippet_with_extra_text`, `test_normalize_none_returns_empty`, `test_normalize_passthrough_unknown_names`, `test_canonical_folds_aliases`, `test_canonical_folds_left_right_modifier_variants`, `test_canonical_folds_natural_named_system_keys`, `test_canonicalized_left_right_chord_passes_hotkey_contract`, `test_canonical_orders_modifiers_first_in_canonical_order`, `test_canonical_keeps_modifierless_order`, `test_canonicalized_chord_passes_hotkey_contract`
- 直接静态依赖：`__future__`, `winpilot.visual_runtime.keyboard_contracts`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/tests/test_launch_verification.py -->
### `python/winpilot_home/tests/test_launch_verification.py`

[打开文件](<../../python/winpilot_home/tests/test_launch_verification.py>)

- 分类/语言：`test` / Python
- 规模：1215 行，40,450 bytes
- SHA-256：`1b895b76d337d5e5940543bb8d849bd92abbf7d7c3d68c095c46b5e9786a005a`
- 源码事实摘要：验证 test launch verification 对应的行为、回归或合同。
- 顶层符号/结构：`test_open_app_focuses_existing_visible_window_without_launch`, `test_open_app_reuses_title_matched_window_when_process_snapshot_misses`, `test_redundant_open_app_reuses_stable_identity_when_window_title_changes`, `test_open_app_restores_background_window_without_launching_duplicate`, `test_open_app_refuses_duplicate_when_existing_hidden_window_cannot_restore`, `test_open_app_failure_advice_does_not_require_an_unexposed_scan_tool`, `test_open_app_returns_ranked_identity_facts_for_ambiguous_local_matches`, `test_non_url_launch_skips_url_window_polling_immediately`, `test_resolve_launch_target_accepts_registered_multiword_application_name`, `test_scan_windows_no_match_upgrades_open_intent_to_open_app`, `test_scan_windows_does_not_launch_dialog_label`, `test_launch_verifies_visible_process_window`, `test_launch_does_not_verify_hidden_process`, `test_launch_does_not_rebind_unchanged_prior_foreground`，另有 10 项
- 直接静态依赖：`json`, `types`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/tests/test_live_agent_composition.py -->
### `python/winpilot_home/tests/test_live_agent_composition.py`

[打开文件](<../../python/winpilot_home/tests/test_live_agent_composition.py>)

- 分类/语言：`test` / Python
- 规模：4286 行，136,736 bytes
- SHA-256：`b16108106f37581c80e871be95aa5cb07c4dcd2782e3d697db0f8bab222c4072`
- 源码事实摘要：验证 test live agent composition 对应的行为、回归或合同。
- 顶层符号/结构：`_nested_keys`, `_nested_values`, `_assert_private_runtime_evidence_absent`, `_jpeg`, `_graph_dict`, `_graph`, `_inputs`, `_config`, `test_budget_freezes_configured_long_task_limits_without_derived_caps`, `_observe_desktop_response`, `_direct_reply_response`, `_prepare`, `_run_with_desktop_lease`, `_run_prepared`，另有 89 项
- 直接静态依赖：`__future__`, `base64`, `copy`, `dataclasses`, `hashlib`, `itertools`, `json`, `pathlib`, `types`, `threading`, `typing`, `cv2`, `numpy`, `pytest`，另有 14 项
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/tests/test_local_search.py -->
### `python/winpilot_home/tests/test_local_search.py`

[打开文件](<../../python/winpilot_home/tests/test_local_search.py>)

- 分类/语言：`test` / Python
- 规模：146 行，4,617 bytes
- SHA-256：`9586a74b6b06e202b3277391c0c42226a630b335d67998607c6493681c2578c3`
- 源码事实摘要：验证 test local search 对应的行为、回归或合同。
- 顶层符号/结构：`test_find_es_prefers_bundled_binary`, `test_everything_starts_client_without_blocking_current_query`, `test_everything_returns_indexed_results`, `test_everything_timeout_returns_bounded_fallback_hint`, `test_scandir_fallback_filters_kind_extension_and_sort`, `test_search_uses_bounded_fallback_and_reports_latency`, `test_everything_warmup_runs_in_background`
- 直接静态依赖：`pathlib`, `subprocess`, `types`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/tests/test_memory_compiler.py -->
### `python/winpilot_home/tests/test_memory_compiler.py`

[打开文件](<../../python/winpilot_home/tests/test_memory_compiler.py>)

- 分类/语言：`test` / Python
- 规模：89 行，3,830 bytes
- SHA-256：`2e7bce6013229b42e29240c318e4e67041ef6f31321daadef91a8e294fefff25`
- 源码事实摘要：验证 test memory compiler 对应的行为、回归或合同。
- 顶层符号/结构：`test_automatic_run_ingestion_still_respects_disabled_switch`, `test_run_ingestion_is_idempotent_and_binds_script_hash`, `test_run_ingestion_recovers_when_only_episode_was_written`
- 直接静态依赖：`types`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/tests/test_memory_context_compression.py -->
### `python/winpilot_home/tests/test_memory_context_compression.py`

[打开文件](<../../python/winpilot_home/tests/test_memory_context_compression.py>)

- 分类/语言：`test` / Python
- 规模：346 行，11,432 bytes
- SHA-256：`be5f3011e1f24c5385c71faea1ac779fffd39b4b853947157f88b8d2b222ee3a`
- 源码事实摘要：验证 test memory context compression 对应的行为、回归或合同。
- 顶层符号/结构：`fresh_memory`, `test_memory_hints_render_conditions_evidence_and_truncate_steps`, `test_reusable_script_requires_strong_implicit_match`, `test_session_module_does_not_classify_references_with_task_keywords`, `test_session_generation_rejects_a_late_result_after_clear`, `test_clearing_an_empty_session_still_advances_the_generation`, `test_fold_checkpoint_preserves_goal_screen_and_recent_evidence`, `test_fresh_shell_evidence_is_not_reduced_to_ok_only`, `test_large_shell_output_keeps_a_bounded_stdout_prefix`, `test_legacy_playbook_gets_conditions_and_evidence`, `test_done_does_not_score_unused_injected_memory`, `test_reuse_skill_records_actual_recipe_use`, `test_reuse_skill_records_failed_replay_attempt`, `test_experience_feedback_scores_used_recipe_not_all_hints`，另有 2 项
- 直接静态依赖：`importlib`, `types`, `pytest`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/tests/test_memory_graph_compat.py -->
### `python/winpilot_home/tests/test_memory_graph_compat.py`

[打开文件](<../../python/winpilot_home/tests/test_memory_graph_compat.py>)

- 分类/语言：`test` / Python
- 规模：281 行，11,017 bytes
- SHA-256：`f249e21100b308bb87db533b8780bf2e144ce9fca85b44dd88758f7eedcfd497`
- 源码事实摘要：验证 test memory graph compat 对应的行为、回归或合同。
- 顶层符号/结构：`_fresh_experience`, `_trusted_record`, `test_experience_api_stays_recipe_like`, `test_apprentice_skill_is_not_reusable_until_verified`, `test_verified_boolean_without_source_proof_stays_provisional`, `test_explicit_id_still_requires_intent_app_and_unchanged_script`, `test_editing_verified_skill_revokes_trust_and_manual_activation_is_rejected`, `test_hash_without_verification_provenance_is_not_trusted`, `test_retired_candidate_cannot_be_reactivated_by_late_replay`, `test_new_provisional_version_keeps_prior_verified_skill`, `test_same_title_in_different_apps_does_not_supersede`, `test_legacy_json_migration_is_idempotent`
- 直接静态依赖：`hashlib`, `importlib`, `json`, `pytest`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/tests/test_memory_graph_feedback.py -->
### `python/winpilot_home/tests/test_memory_graph_feedback.py`

[打开文件](<../../python/winpilot_home/tests/test_memory_graph_feedback.py>)

- 分类/语言：`test` / Python
- 规模：50 行，2,020 bytes
- SHA-256：`3b7b692181aff4d6f23241fe35985e3bc74416b01fceb7d3df852f3674b1eb2c`
- 源码事实摘要：验证 test memory graph feedback 对应的行为、回归或合同。
- 顶层符号/结构：`test_packed_but_unused_does_not_score_success`, `test_replay_failed_done_does_not_add_success`
- 直接静态依赖：—
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/tests/test_memory_graph_retrieval.py -->
### `python/winpilot_home/tests/test_memory_graph_retrieval.py`

[打开文件](<../../python/winpilot_home/tests/test_memory_graph_retrieval.py>)

- 分类/语言：`test` / Python
- 规模：66 行，2,488 bytes
- SHA-256：`692117a98a4982f0f23213815b6697989b941cb0bd0f8aa76002b6e902023931`
- 源码事实摘要：验证 test memory graph retrieval 对应的行为、回归或合同。
- 顶层符号/结构：`test_memory_pack_filters_coding_and_volatile_tasks`, `test_memory_pack_skips_superseded_and_includes_warning`
- 直接静态依赖：—
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/tests/test_memory_graph_store.py -->
### `python/winpilot_home/tests/test_memory_graph_store.py`

[打开文件](<../../python/winpilot_home/tests/test_memory_graph_store.py>)

- 分类/语言：`test` / Python
- 规模：88 行，3,087 bytes
- SHA-256：`26444a389ec88b9c2e1b419c9b8641c69050d53a591b4ce0f6f6740d5dfe3c41`
- 源码事实摘要：验证 test memory graph store 对应的行为、回归或合同。
- 顶层符号/结构：`test_memory_store_schema_crud_edges_and_fallback`, `test_memory_store_redacts_secret_like_values`
- 直接静态依赖：`sqlite3`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/tests/test_model_adapter.py -->
### `python/winpilot_home/tests/test_model_adapter.py`

[打开文件](<../../python/winpilot_home/tests/test_model_adapter.py>)

- 分类/语言：`test` / Python
- 规模：471 行，14,502 bytes
- SHA-256：`92053384dcf052fc59b3eb462a2e12b094a8a586c139176b84471dd142d59f2c`
- 源码事实摘要：验证 test model adapter 对应的行为、回归或合同。
- 顶层符号/结构：`_usage`, `test_profile_infers_provider_and_vision_capability`, `test_native_provider_vision_capability_is_explicit_or_conservative`, `test_native_protocol_selection_is_explicit_and_aggregator_safe`, `test_native_protocol_rejects_unknown_explicit_protocol`, `test_normalize_usage_accepts_deepseek_cache_fields`, `test_normalize_usage_accepts_chat_and_responses_cached_token_details`, `test_normalize_usage_accepts_openai_cache_write_token_details`, `test_normalize_usage_accepts_anthropic_style_cache_read_fields`, `test_adapter_leaves_provider_thinking_at_native_default`, `test_main_agent_adapter_keeps_configured_thinking_until_request_routing`, `test_native_visual_request_keeps_mimo_provider_thinking_default`, `test_native_visual_request_skips_text_only_escalation_model`, `test_thinking_tool_history_can_preserve_complete_reasoning_content`，另有 1 项
- 直接静态依赖：`types`, `unittest.mock`, `pytest`, `winpilot.model_adapter`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/tests/test_model_call_runtime.py -->
### `python/winpilot_home/tests/test_model_call_runtime.py`

[打开文件](<../../python/winpilot_home/tests/test_model_call_runtime.py>)

- 分类/语言：`test` / Python
- 规模：668 行，20,978 bytes
- SHA-256：`7335bedd40fde930d34ad0b4515f2d9fdbf1fdc3b3b8662a00409da2bcd39f48`
- 源码事实摘要：Unified model-request retry and mutation replay contracts.
- 顶层符号/结构：`_request`, `_envelope`, `test_envelope_freezes_request_without_aliasing_mutable_containers`, `test_envelope_fingerprint_is_key_order_stable_and_value_sensitive`, `test_envelope_rejects_non_json_values`, `test_envelope_rejects_cyclic_request_as_a_controlled_contract_error`, `test_runtime_retries_only_the_exact_frozen_request`, `test_runtime_retries_transient_http_statuses`, `test_runtime_preserves_rate_limit_specific_backoff`, `test_runtime_clamps_extreme_finite_backoff_without_overflow`, `test_runtime_does_not_retry_non_transient_http_statuses`, `test_runtime_retries_exact_structured_multimodal_decoder_400`, `test_runtime_does_not_retry_spoofed_or_other_multimodal_400s`, `test_runtime_honors_cancellation_before_first_send`，另有 16 项
- 直接静态依赖：`__future__`, `threading`, `time`, `pytest`, `winpilot.action.replay_policy`, `winpilot.modeling`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/tests/test_model_timing_audit.py -->
### `python/winpilot_home/tests/test_model_timing_audit.py`

[打开文件](<../../python/winpilot_home/tests/test_model_timing_audit.py>)

- 分类/语言：`test` / Python
- 规模：259 行，8,124 bytes
- SHA-256：`97d12c60bb53f121fb9826f857a0b37c723604143f2e908049be70f1a13a4cf7`
- 源码事实摘要：验证 test model timing audit 对应的行为、回归或合同。
- 顶层符号/结构：`_CompletionsClient`, `_runner`, `_install_elapsed_recorder`, `test_model_elapsed_accumulator_uses_monotonic_delta`, `test_chat_accumulates_successful_model_wait_once`, `test_chat_accumulates_failed_model_wait`, `test_chat_accumulates_only_until_canceled_call_is_abandoned`, `test_semantic_stage_metrics_subtract_model_time`, `_semantic_stage_context`, `_FakeSemanticRunner`, `test_create_stage_success_outputs_split_model_and_controller_time`, `test_create_stage_failure_output_keeps_model_and_controller_time`, `test_semantic_stage_failure_before_model_still_outputs_zero_model_time`
- 直接静态依赖：`__future__`, `threading`, `types`, `pytest`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/tests/test_model_transport_ownership.py -->
### `python/winpilot_home/tests/test_model_transport_ownership.py`

[打开文件](<../../python/winpilot_home/tests/test_model_transport_ownership.py>)

- 分类/语言：`test` / Python
- 规模：75 行，2,741 bytes
- SHA-256：`f9dfc2c68c6809836034153f31c19fe19088fec1b5eb05d4e513e3808873ca23`
- 源码事实摘要：Prevent new model transports from bypassing the Runtime retry contract.
- 顶层符号/结构：`_attribute_chain`, `_TransportVisitor`, `test_chat_completion_transport_has_a_small_explicit_owner_set`, `test_product_package_has_no_unowned_responses_transport`, `const:PRODUCT_ROOT`, `const:ALLOWED_TRANSPORT_CALLS`
- 直接静态依赖：`__future__`, `ast`, `pathlib`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/tests/test_native_double_click.py -->
### `python/winpilot_home/tests/test_native_double_click.py`

[打开文件](<../../python/winpilot_home/tests/test_native_double_click.py>)

- 分类/语言：`test` / Python
- 规模：134 行，4,668 bytes
- SHA-256：`eac3de64f6a4af3f91dea735f19329529fd63f0454233c3eaeea6767057ea2ec`
- 源码事实摘要：验证 test native double click 对应的行为、回归或合同。
- 顶层符号/结构：`test_explicit_double_click_reuses_click_adapter_and_forces_double`, `test_double_click_dispatch_is_a_mutation_and_advances_epoch`, `test_fast_controller_wrap_canonicalizes_double_click_to_click_double_true`, `test_fast_controller_dispatches_double_click_as_one_click_transaction`, `test_no_progress_distinguishes_hover_single_double_and_mouse_button`
- 直接静态依赖：`__future__`, `json`, `types`, `winpilot.agent.interaction_tools`, `winpilot.agent.loop`, `winpilot.agent.tool_result`, `winpilot.agent.tools`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/tests/test_native_intent_freedom.py -->
### `python/winpilot_home/tests/test_native_intent_freedom.py`

[打开文件](<../../python/winpilot_home/tests/test_native_intent_freedom.py>)

- 分类/语言：`test` / Python
- 规模：326 行，12,010 bytes
- SHA-256：`c58c897258fcd61ff9ee55340207aae152a8acfccefd86649e9b3a8c88662a6f`
- 源码事实摘要：验证 test native intent freedom 对应的行为、回归或合同。
- 顶层符号/结构：`_assistant_response`, `_native_desktop_runner`, `_prepare_run`, `test_native_prompt_does_not_require_a_gui_action_for_an_answer`, `test_native_execute_desktop_does_not_prebind_before_first_model_decision`, `test_native_execute_desktop_plain_reply_is_verified_and_special_hints_are_absent`, `test_rejected_native_plain_reply_continues_until_judge_accepts`, `test_unverified_native_plain_reply_cannot_succeed`, `test_fresh_image_judge_receives_full_candidate_frame_actions_and_evidence`, `test_native_candidate_reply_bypasses_keyword_preclassification_and_reaches_judge`, `test_native_verify_done_sends_bounded_prior_visual_effects_to_judge`
- 直接静态依赖：`__future__`, `pathlib`, `types`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/tests/test_normalized_frame_coordinates.py -->
### `python/winpilot_home/tests/test_normalized_frame_coordinates.py`

[打开文件](<../../python/winpilot_home/tests/test_normalized_frame_coordinates.py>)

- 分类/语言：`test` / Python
- 规模：128 行，3,874 bytes
- SHA-256：`055238a730a3ba6edcc355f344762f01158c572fc595da83b76f027b42ffbc45`
- 源码事实摘要：验证 test normalized frame coordinates 对应的行为、回归或合同。
- 顶层符号/结构：`test_maps_all_corners_inside_the_inclusive_physical_pixel_bounds`, `test_preserves_the_existing_rounding_formula`, `test_rejects_non_numeric_non_finite_and_out_of_bounds_coordinates`, `test_rejects_invalid_physical_regions`, `test_randomized_mapping_matches_formula_and_remains_inside_region`, `test_randomized_mapping_is_monotonic_per_axis`
- 直接静态依赖：`__future__`, `math`, `random`, `pytest`, `winpilot.visual_runtime.normalized_frame_coordinates`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/tests/test_observe_cache.py -->
### `python/winpilot_home/tests/test_observe_cache.py`

[打开文件](<../../python/winpilot_home/tests/test_observe_cache.py>)

- 分类/语言：`test` / Python
- 规模：750 行，32,511 bytes
- SHA-256：`e107131012f8f85051cbf3718878da0a5b7d954b76b0e60070f950ba718a4a66`
- 源码事实摘要：验证 test observe cache 对应的行为、回归或合同。
- 顶层符号/结构：`_snapshot`, `test_observe_reuses_cache_for_same_epoch`, `test_controller_structured_observe_override_reaches_fusion`, `test_native_visual_observe_query_refreshes_pixels_without_uia_or_ocr`, `test_observe_includes_fast_screen_bulletin_when_available`, `test_repeated_full_observe_is_downgraded_but_raw_trace_remains_available`, `test_read_text_query_uses_uia_index_and_requires_non_input`, `test_full_observe_query_runs_targeted_ocr_when_uia_misses_content`, `test_observe_changed_only_uses_recent_screen_delta_roi`, `test_changed_only_ocr_reads_cached_transient_press_frame`, `test_changed_only_observe_uses_predictive_ocr_without_second_inference`, `test_changed_only_observe_reuses_shared_daemon_incremental_ocr`, `test_observe_changed_only_query_miss_falls_back_to_full_observe`, `test_observe_changed_only_uses_recent_roi_after_screen_stabilizes`，另有 7 项
- 直接静态依赖：`json`, `winpilot.agent.tools`, `winpilot.perception`, `winpilot.perception.model`, `winpilot.utils.screenshot`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/tests/test_ocr_warmup.py -->
### `python/winpilot_home/tests/test_ocr_warmup.py`

[打开文件](<../../python/winpilot_home/tests/test_ocr_warmup.py>)

- 分类/语言：`test` / Python
- 规模：157 行，4,686 bytes
- SHA-256：`4d92f7b53a51f9b6b26cec9c82bacedf826b89155eb880ca80a20dc18107eccc`
- 源码事实摘要：验证 test ocr warmup 对应的行为、回归或合同。
- 顶层符号/结构：`test_ocr_warm_async_starts_once`, `test_ocr_warm_async_skips_existing_engine`, `test_foreground_priority_defers_predictive_ocr`, `test_waiting_foreground_runs_before_second_background`, `test_canceled_queued_background_never_calls_engine`
- 直接静态依赖：—
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/tests/test_operator_action_contract.py -->
### `python/winpilot_home/tests/test_operator_action_contract.py`

[打开文件](<../../python/winpilot_home/tests/test_operator_action_contract.py>)

- 分类/语言：`test` / Python
- 规模：324 行，11,293 bytes
- SHA-256：`d1c52e85a075b55e44e673c1d221947dbec5fd74764348d428b0525d1208aa00`
- 源码事实摘要：验证 test operator action contract 对应的行为、回归或合同。
- 顶层符号/结构：`test_atomic_action_parser_preserves_metadata_and_top_level_precedence`, `test_atomic_action_parser_matches_controller_shorthand_and_skips_malformed_children`, `test_batch_validator_reuses_precanonicalized_actions_without_reparsing`, `test_focus_window_contract_accepts_either_window_identity`, `test_execute_subgoal_atomic_actions_have_stable_declared_order`, `test_action_envelope_binds_window_frame_region_and_policy`, `test_action_envelope_distinguishes_frame_and_canvas_coordinates`, `test_controller_emits_typed_action_lifecycle_envelope`, `test_agent_runner_direct_tool_emits_one_immutable_typed_lifecycle`, `test_supported_actions_is_stable_and_minimal_contract_projection`, `test_top_level_controller_transactions_require_post_frame`
- 直接静态依赖：`__future__`, `types`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/tests/test_orchestrator.py -->
### `python/winpilot_home/tests/test_orchestrator.py`

[打开文件](<../../python/winpilot_home/tests/test_orchestrator.py>)

- 分类/语言：`test` / Python
- 规模：2241 行，82,520 bytes
- SHA-256：`e68c0bf9c17ad77c9fbe2ea904bc84a7b01c6d0d8a1f7c5f657af0eb112cec22`
- 源码事实摘要：验证 test orchestrator 对应的行为、回归或合同。
- 顶层符号/结构：`test_runtime_preview_preserves_replay_evidence_structure`, `test_interaction_snapshot_never_drops_causal_action_or_verification_fields`, `test_stage_trace_preserves_model_capture_and_contract_budget_metrics`, `_graph`, `test_graph_validation_rejects_missing_route_target`, `test_graph_validation_enforces_evidence_policy_shape`, `test_graph_validation_rejects_empty_evidence_gate_and_retired_legacy_node`, `test_supported_historical_legacy_node_is_readable_but_has_no_execution_owner`, `test_graph_validation_uses_exact_wps_contract_for_replay_steps`, `test_graph_validation_requires_focus_on_every_path_before_spatial_batches`, `test_replay_steps_share_one_session_and_mix_with_native_arp`, `test_replay_clipboard_get_updates_arp_variables_for_conditions`, `test_managed_screenshot_action_batch_surfaces_run_artifact`, `test_action_batches_share_target_session_until_run_cleanup`，另有 43 项
- 直接静态依赖：`threading`, `time`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/tests/test_orchestrator_api.py -->
### `python/winpilot_home/tests/test_orchestrator_api.py`

[打开文件](<../../python/winpilot_home/tests/test_orchestrator_api.py>)

- 分类/语言：`test` / Python
- 规模：1804 行，60,280 bytes
- SHA-256：`e06cca80298064eb034b2d8e85c2c7fdec709f8eac771bff0cb6ca0af6a161d7`
- 源码事实摘要：验证 test orchestrator api 对应的行为、回归或合同。
- 顶层符号/结构：`test_server_import_does_not_load_legacy_agent_composition`, `client`, `_graph_payload`, `_client_execution_context`, `_waiting_desktop_lease`, `test_platform_overview_exposes_contracts_and_capabilities`, `test_product_and_example_config_default_to_vnext`, `test_config_migrates_retired_live_agent_key_at_input_boundary`, `test_config_rejects_conflicting_live_agent_keys_without_mutation`, `test_config_api_reports_conflicting_live_agent_keys`, `test_platform_overview_exposes_fail_closed_vnext_live_agent_budget`, `test_public_trace_routes_reject_private_runtime_evidence_paths`, `test_public_trace_routes_preserve_safe_historical_trace_and_image`, `test_public_trace_detail_is_bounded_and_reports_read_integrity`，另有 30 项
- 直接静态依赖：`json`, `os`, `pathlib`, `subprocess`, `sys`, `threading`, `time`, `types`, `pytest`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/tests/test_perception_confidence.py -->
### `python/winpilot_home/tests/test_perception_confidence.py`

[打开文件](<../../python/winpilot_home/tests/test_perception_confidence.py>)

- 分类/语言：`test` / Python
- 规模：397 行，13,560 bytes
- SHA-256：`e1f7df816131a1f552544d757a043758d1b9da66af0aa0a225fe131e960726ba`
- 源码事实摘要：验证 test perception confidence 对应的行为、回归或合同。
- 顶层符号/结构：`_mock_vlm_response`, `test_vlm_calls_use_unified_role_scoped_model_boundary`, `test_vlm_json_parse_failure_is_not_retried`, `test_vlm_collect_keeps_only_valid_explicitly_confident_groundings`, `test_vlm_verify_rejects_low_confidence_pass`, `test_vlm_verify_rejects_contradictory_pass`, `test_vlm_verify_accepts_consistent_high_confidence_pass`, `test_vlm_verify_ignores_aesthetics_not_requested_by_user`, `test_vlm_verify_keeps_explicit_color_and_detail_requirements`, `test_element_locator_uses_perception_confidence`, `test_element_locator_rejects_near_tied_distinct_candidates`, `test_reducer_separates_relevance_from_perception_confidence`
- 直接静态依赖：`json`, `types`, `pytest`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/tests/test_perception_daemon.py -->
### `python/winpilot_home/tests/test_perception_daemon.py`

[打开文件](<../../python/winpilot_home/tests/test_perception_daemon.py>)

- 分类/语言：`test` / Python
- 规模：511 行，17,655 bytes
- SHA-256：`927e49ccdca16c90453f3f01cd485d5bf3bbb7969128fcf0d490808a4193a283`
- 源码事实摘要：验证 test perception daemon 对应的行为、回归或合同。
- 顶层符号/结构：`_pin_daemon_config`, `test_perception_daemon_reuses_watcher_and_cache_per_hwnd`, `test_perception_daemon_does_not_analyze_released_window_changes`, `test_perception_daemon_discards_inflight_result_after_final_release`, `test_perception_daemon_reacquire_clears_prior_history_and_anchor_cache`, `_EmittingWatcher`, `_wait_until`, `_snapshot_for_change`, `test_perception_daemon_coalesces_normal_churn_and_reuses_latest_roi`, `test_perception_daemon_preserves_action_delta_before_normal_update`, `test_perception_daemon_supersedes_stale_normal_evidence_in_same_roi`, `test_perception_daemon_skips_caret_noise_and_large_video_churn`, `test_perception_daemon_default_analyzer_uses_only_roi_ocr_and_win32`, `test_perception_daemon_prune_detaches_listener_and_stops_worker`
- 直接静态依赖：`__future__`, `time`, `numpy`, `pytest`, `winpilot.config`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/tests/test_perception_reducer.py -->
### `python/winpilot_home/tests/test_perception_reducer.py`

[打开文件](<../../python/winpilot_home/tests/test_perception_reducer.py>)

- 分类/语言：`test` / Python
- 规模：341 行，14,130 bytes
- SHA-256：`4ade65afaa9e9bed9ded52d57a61e74688f026f9b63a4cf10c04e14ad63be6ae`
- 源码事实摘要：验证 test perception reducer 对应的行为、回归或合同。
- 顶层符号/结构：`test_perception_reducer_prioritizes_relevant_input_and_button`, `test_render_reduced_snapshot_keeps_compact_candidate_text`, `test_perception_reducer_marks_open_chat_and_stops_contact_reclick_loop`, `test_grounded_explicit_send_observe_returns_proposal_without_mutation`, `test_grounded_chat_does_not_autogenerate_content_for_open_ended_chat`, `test_file_dialog_reducer_prefers_filename_field_over_grid_item_names`, `test_perception_reducer_and_raw_sample_exclude_far_off_window_uia_children`, `test_perception_reducer_marks_recovery_banner_non_blocking_when_grid_is_editable`, `test_snapshot_multi_field_query_matches_any_term_and_ranks_multi_hits_first`, `test_editable_spreadsheet_suppresses_stale_creating_document_from_model_view`, `test_writer_treats_stale_creating_document_notice_as_non_blocking`, `test_writer_ocr_chrome_is_enough_to_mark_recovery_notice_non_blocking`
- 直接静态依赖：`winpilot.perception`, `winpilot.perception.fusion`, `winpilot.perception.model`, `winpilot.utils.screenshot`, `json`, `types`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/tests/test_plan_tools.py -->
### `python/winpilot_home/tests/test_plan_tools.py`

[打开文件](<../../python/winpilot_home/tests/test_plan_tools.py>)

- 分类/语言：`test` / Python
- 规模：644 行，21,840 bytes
- SHA-256：`b1d9a084870dff46ad3b69e50963c74d14ee41bfc100f36bcb3217e52db5980d`
- 源码事实摘要：验证 test plan tools 对应的行为、回归或合同。
- 顶层符号/结构：`test_revise_plan_preserves_model_steps_without_drawing_keyword_filtering`, `test_revise_plan_keeps_requested_drawing_save_step`, `test_planner_grounds_invented_public_desktop_path_to_current_user`, `test_planner_preserves_long_explicit_output_path_and_adds_file_check`, `test_planner_simplifies_multi_paragraph_input_check_to_stable_first_paragraph`, `_false_verify`, `test_complete_subgoal_accepts_historical_visible_evidence`, `test_complete_subgoal_rejects_historical_evidence_for_current_task`, `test_complete_subgoal_accepts_current_execute_subgoal_evidence`, `test_complete_subgoal_accepts_current_visual_color_evidence`, `test_complete_subgoal_all_done_returns_terminal_done`, `test_complete_subgoal_is_idempotent_after_controller_completion`, `test_complete_research_source_step_requires_matching_working_set_subject`, `test_revise_plan_restores_source_progress_from_working_set`，另有 17 项
- 直接静态依赖：`types`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/tests/test_planner.py -->
### `python/winpilot_home/tests/test_planner.py`

[打开文件](<../../python/winpilot_home/tests/test_planner.py>)

- 分类/语言：`test` / Python
- 规模：379 行，12,828 bytes
- SHA-256：`284f3288ee3c68a3a486e3e12b1db7f51a4518605251e67ffde90fcf1914d376`
- 源码事实摘要：验证 test planner 对应的行为、回归或合同。
- 顶层符号/结构：`test_plan_drops_button_label_echo_text_check`, `test_plan_keeps_real_state_text_after_click`, `test_parse_steps_sanitizes_chinese_button_label_echo`, `test_parse_steps_recovers_badly_quoted_string_list`, `test_plan_merges_click_then_visible_state_verify`, `test_plan_keeps_model_authored_visual_check_as_guidance`, `test_plan_does_not_apply_drawing_specific_check_sanitization`, `test_plan_keeps_state_token_check_for_visual_confirmation`, `test_verbose_two_action_gui_task_skips_upfront_plan`, `test_repeated_click_policy_word_does_not_create_extra_plan_step`, `test_four_explicit_gui_actions_still_use_upfront_plan`, `test_typed_intent_drives_upfront_plan_without_reparsing_task`, `test_context_guidance_passes_runner_typed_intent_to_planner`, `test_full_desktop_stage_forces_a_provisional_plan_with_live_context`，另有 6 项
- 直接静态依赖：`winpilot.agent`, `winpilot.tasking.contracts`, `types`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/tests/test_provider_protocols.py -->
### `python/winpilot_home/tests/test_provider_protocols.py`

[打开文件](<../../python/winpilot_home/tests/test_provider_protocols.py>)

- 分类/语言：`test` / Python
- 规模：784 行，24,026 bytes
- SHA-256：`36a785ff2b8ed06b8a615b3c9dfb397cb63afb67707df6b51b1cd089932ad9f1`
- 源码事实摘要：验证 test provider protocols 对应的行为、回归或合同。
- 顶层符号/结构：`_common_request`, `test_responses_request_translates_multimodal_tools_and_results`, `test_anthropic_request_translates_system_images_and_tool_blocks`, `test_gemini_request_translates_parts_and_function_round_trip`, `test_provider_tool_choice_shapes_follow_each_native_protocol`, `test_responses_round_trip_replays_every_native_output_item_untouched`, `test_gemini_round_trip_preserves_full_content_and_absent_native_call_id`, `test_gemini_synthetic_ids_do_not_collide_across_responses`, `test_anthropic_signed_thinking_is_replayed_before_the_tool_use`, `test_gemini_usage_counts_thoughts_as_completion_and_preserves_total`, `test_responses_non_success_states_never_become_empty_stop`, `test_provider_tool_calls_require_valid_ids_names_and_object_args`, `_FakeTransport`, `test_adapter_uses_one_injected_http_boundary`，另有 11 项
- 直接静态依赖：`__future__`, `copy`, `pytest`, `winpilot.modeling.provider_protocols`, `winpilot.modeling.chat`, `winpilot.modeling.contracts`, `winpilot.modeling.runtime`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/tests/test_qq_chat_component.py -->
### `python/winpilot_home/tests/test_qq_chat_component.py`

[打开文件](<../../python/winpilot_home/tests/test_qq_chat_component.py>)

- 分类/语言：`test` / Python
- 规模：406 行，17,246 bytes
- SHA-256：`d36fbb40190f816517c7c99c354ce2f4f14994102f85a43b8309e54995cfa583`
- 源码事实摘要：Component-level behavioural tests for "用QQ找我的手机聊天".
- 顶层符号/结构：`_make_runner`, `TestVisionPrompt`, `TestNoMemory`, `TestCommHint`, `TestDoneGate`, `TestPerceptionMode`, `const:TASK`
- 直接静态依赖：`__future__`, `threading`, `unittest.mock`, `pytest`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/tests/test_raw_device_contract.py -->
### `python/winpilot_home/tests/test_raw_device_contract.py`

[打开文件](<../../python/winpilot_home/tests/test_raw_device_contract.py>)

- 分类/语言：`test` / Python
- 规模：3013 行，103,178 bytes
- SHA-256：`77b5de22fd4514c389c06d56b331f70e8097c90c6d7fd0b6794fe9739278195b`
- 源码事实摘要：验证 test raw device contract 对应的行为、回归或合同。
- 顶层符号/结构：`_context`, `_executor`, `test_fast_controller_names_reflective_session_adapter_as_legacy`, `test_raw_device_requires_an_explicit_typed_backend`, `test_scroll_rejects_invalid_typed_input_before_backend`, `test_window_context_change_is_advisory_for_physical_input`, `test_scroll_uses_explicit_backend_completion_fact`, `test_generic_ok_mapping_cannot_claim_input_completion`, `test_backend_exception_after_possible_input_is_unknown_and_not_replayed`, `test_invalid_post_call_fact_is_unknown_instead_of_escaping`, `test_backend_facts_are_scalar_finite_and_immutable`, `test_receipt_rejects_contradictory_completion_identity`, `test_raw_device_has_no_session_or_second_action_parser`, `test_scroll_property_preserves_signed_coordinates_and_single_call`，另有 84 项
- 直接静态依赖：`__future__`, `ast`, `json`, `math`, `pathlib`, `random`, `pytest`, `winpilot.action.raw_device`, `winpilot.action.replay_policy`, `winpilot.fast.legacy_session_actions`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/tests/test_recovery_policy.py -->
### `python/winpilot_home/tests/test_recovery_policy.py`

[打开文件](<../../python/winpilot_home/tests/test_recovery_policy.py>)

- 分类/语言：`test` / Python
- 规模：107 行，3,609 bytes
- SHA-256：`72b2d20802787e07d38efbb09cdcf6e3af5c6d6c0fd69fc961de9fbae2a80483`
- 源码事实摘要：Failure-specific recovery must resume the failed typed stage safely.
- 顶层符号/结构：`test_pointer_ownership_loss_rebinds_instead_of_replaying_old_geometry`, `test_repeated_pointer_ownership_loss_stops_after_bounded_recovery`, `test_semantic_failure_after_mutation_never_replays_pointer_input`, `test_execute_desktop_task_failure_does_not_enter_stage_recovery_policy`
- 直接静态依赖：`__future__`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/tests/test_replay_anchor_cache.py -->
### `python/winpilot_home/tests/test_replay_anchor_cache.py`

[打开文件](<../../python/winpilot_home/tests/test_replay_anchor_cache.py>)

- 分类/语言：`test` / Python
- 规模：136 行，4,910 bytes
- SHA-256：`8e7485574347b79234d2b110b90ff666d161507c092a85f126f2dc9cd23c0ed8`
- 源码事实摘要：验证 test replay anchor cache 对应的行为、回归或合同。
- 顶层符号/结构：`_anchor`, `test_replay_anchor_profile_round_trips_through_memory_meta`, `test_cached_anchor_uses_local_template_and_skips_uia`, `test_stale_anchor_falls_back_to_semantic_selector`, `test_verified_fallback_can_learn_normalized_template`
- 直接静态依赖：`pathlib`, `types`, `numpy`, `winpilot.memory`, `winpilot.memory.store`, `winpilot.recorder`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/tests/test_replay_control.py -->
### `python/winpilot_home/tests/test_replay_control.py`

[打开文件](<../../python/winpilot_home/tests/test_replay_control.py>)

- 分类/语言：`test` / Python
- 规模：239 行，8,895 bytes
- SHA-256：`7349d3d8fdcd1b632e49fdb5e51311582a9c9ba56466a1874eb265ae0a4ebfa9`
- 源码事实摘要：Unit tests for replay_control: interpolation + ctrl.if / ctrl.loop.
- 顶层符号/结构：`test_interpolate_replaces_known_vars`, `test_interpolate_keeps_unknown_vars_verbatim`, `test_interpolate_walks_lists_and_dicts`, `test_interpolate_non_string_scalars_passthrough`, `test_interpolate_step_skips_nested_step_lists`, `FakeReplayer`, `leaf`, `test_if_runs_then_when_cond_true`, `test_if_runs_else_when_cond_false`, `test_if_false_without_else_is_ok_noop`, `test_if_no_cond_fails_instead_of_taking_then`, `test_if_explicit_always_true_takes_then`, `test_if_propagates_branch_failure`, `test_loop_count_runs_body_n_times`，另有 11 项
- 直接静态依赖：`winpilot.recorder`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/tests/test_replay_job_api.py -->
### `python/winpilot_home/tests/test_replay_job_api.py`

[打开文件](<../../python/winpilot_home/tests/test_replay_job_api.py>)

- 分类/语言：`test` / Python
- 规模：522 行，17,811 bytes
- SHA-256：`9b572d3980db33139eae26e0b3c5734547845b4c297cedc0a48794e8498ca25f`
- 源码事实摘要：验证 test replay job api 对应的行为、回归或合同。
- 顶层符号/结构：`client`, `_write_script`, `_wait_for`, `test_replay_returns_queryable_job`, `test_replay_failure_preserves_failed_terminal_status`, `test_replay_cancel_sets_job_stop_event`, `test_status_breaks_down_replay_busy_state`, `test_get_script_as_automation_ir`, `_arp_automation_ir`, `test_automation_ir_api_validates_canonical_runtime`, `test_automation_ir_validate_api_preserves_individual_graph_errors`, `test_automation_ir_api_starts_arp_projection`, `test_script_save_rejects_stale_automation_runtime_projection`, `test_automation_ir_api_routes_wps_runtime_to_replayer`，另有 7 项
- 直接静态依赖：`json`, `threading`, `time`, `pytest`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/tests/test_router_gui_only.py -->
### `python/winpilot_home/tests/test_router_gui_only.py`

[打开文件](<../../python/winpilot_home/tests/test_router_gui_only.py>)

- 分类/语言：`test` / Python
- 规模：87 行，3,196 bytes
- SHA-256：`3d792a3e3d43e92ca3f87ceda43147cccbbeb956f876158fd327e6cbfca25f0a`
- 源码事实摘要：验证 test router gui only 对应的行为、回归或合同。
- 顶层符号/结构：`test_gui_only_task_stays_on_unified_desktop_agent`, `test_coding_task_uses_unified_agent_with_coding_runtime`, `test_runner_and_tool_session_share_runtime_profile`, `test_runner_can_disable_shell_tools_for_arp_policy`, `test_explicit_project_artifact_task_uses_coding_runtime`, `test_report_file_artifact_task_uses_coding_runtime`, `test_webpage_build_then_browser_task_uses_mixed_runtime`, `test_coding_plan_progress_is_not_blocked_by_gui_action_counter`, `test_desktop_plan_progress_still_requires_pending_action_evidence`
- 直接静态依赖：`winpilot.agent.loop`, `winpilot.agent.router`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/tests/test_run_state.py -->
### `python/winpilot_home/tests/test_run_state.py`

[打开文件](<../../python/winpilot_home/tests/test_run_state.py>)

- 分类/语言：`test` / Python
- 规模：283 行，9,201 bytes
- SHA-256：`bf96dd5fcc038a51091ecfaab17d8e3f173134453652cdedcac8179d8e0eb2ca`
- 源码事实摘要：验证 test run state 对应的行为、回归或合同。
- 顶层符号/结构：`test_run_state_unverified_action_needs_observe_once`, `test_run_state_expected_action_failure_recovers`, `test_run_state_owns_repeated_subgoal_progress_evidence`, `test_run_state_owns_no_progress_retry_count_and_termination_only`, `test_run_state_reads_batch_expect_from_params`, `test_run_state_batch_with_final_plain_observe_keeps_action_debt`, `test_run_state_batch_with_explicit_matches_result_is_ready`, `test_run_state_observe_only_batch_preserves_existing_action_debt`, `test_run_state_batch_with_final_zoom_still_needs_semantic_verify`, `test_run_state_batch_verified_result_is_ready`, `test_run_state_generic_observe_keeps_pending_causal_debt`, `test_run_state_zoom_does_not_clear_pending_unverified_count`, `test_unrelated_inventory_does_not_clear_pending_action_count`, `test_targeted_read_text_does_not_clear_pending_action_without_transition`，另有 3 项
- 直接静态依赖：`json`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/tests/test_runtime_capability_preflight.py -->
### `python/winpilot_home/tests/test_runtime_capability_preflight.py`

[打开文件](<../../python/winpilot_home/tests/test_runtime_capability_preflight.py>)

- 分类/语言：`test` / Python
- 规模：285 行，10,463 bytes
- SHA-256：`afb4285af6a2e97482db9c5c48a8707b6f3160410e9646bbcb84d183d2dda304`
- 源码事实摘要：验证 test runtime capability preflight 对应的行为、回归或合同。
- 顶层符号/结构：`_fictional_communication_provider_type`, `test_visual_model_runtime_preflight_requires_a_multimodal_provider`, `test_static_and_live_capability_checks_are_separate`, `test_semantic_verifier_fails_preflight_when_vlm_is_unconfigured`, `test_semantic_verifier_rejects_configured_text_only_model`, `test_selected_unregistered_communication_adapter_fails_before_run`, `test_adapter_registry_probe_does_not_claim_login_or_installation`, `test_desktop_stage_runtime_profile_does_not_reclassify_generated_goal`, `test_legacy_runtime_profile_keeps_raw_classifier_fallback`
- 直接静态依赖：`__future__`, `pytest`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/tests/test_runtime_info.py -->
### `python/winpilot_home/tests/test_runtime_info.py`

[打开文件](<../../python/winpilot_home/tests/test_runtime_info.py>)

- 分类/语言：`test` / Python
- 规模：141 行，4,971 bytes
- SHA-256：`bb7ca958157e47c87a95d1ca876b13938ab6b51951eaa3b42434387073faf865`
- 源码事实摘要：验证 test runtime info 对应的行为、回归或合同。
- 顶层符号/结构：`runtime_info_module`, `test_runtime_fingerprint_exposes_non_secret_service_identity`, `test_runtime_checks_reuse_independent_short_ttl_caches`, `test_source_change_is_reported_within_short_ttl_and_stays_fail_closed`, `test_runtime_cache_refresh_is_single_flight_across_threads`
- 直接静态依赖：`threading`, `time`, `concurrent.futures`, `pytest`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/tests/test_runtime_jobs.py -->
### `python/winpilot_home/tests/test_runtime_jobs.py`

[打开文件](<../../python/winpilot_home/tests/test_runtime_jobs.py>)

- 分类/语言：`test` / Python
- 规模：124 行，3,954 bytes
- SHA-256：`bb183abba6fb30b590b9ab847bd262d66896ea3dffbcf5ed5c931d85c7351141`
- 源码事实摘要：验证 test runtime jobs 对应的行为、回归或合同。
- 顶层符号/结构：`test_runtime_jobs_serialize_shared_resource_but_allow_independent_work`, `test_runtime_job_cancel_propagates_stop_event`, `test_runtime_job_reports_active_jobs_and_runs_finish_callback`, `test_runtime_job_submit_rolls_back_when_thread_start_fails`, `test_queued_runner_registration_does_not_replace_current_runner`
- 直接静态依赖：`__future__`, `threading`, `time`, `types`, `pytest`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/tests/test_runtime_perception_policy.py -->
### `python/winpilot_home/tests/test_runtime_perception_policy.py`

[打开文件](<../../python/winpilot_home/tests/test_runtime_perception_policy.py>)

- 分类/语言：`test` / Python
- 规模：69 行，2,206 bytes
- SHA-256：`52a924118d2d32c59b7598decacdf6466643fc1ac402846ca896e50b7669d2fb`
- 源码事实摘要：验证 test runtime perception policy 对应的行为、回归或合同。
- 顶层符号/结构：`test_native_desktop_profile_does_not_start_background_semantic_perception`, `test_legacy_runtime_can_explicitly_enable_background_perception`, `test_server_ocr_warmup_requires_explicit_startup_opt_in`, `test_default_config_keeps_uia_and_ocr_opt_in`
- 直接静态依赖：—
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/tests/test_scene_coverage.py -->
### `python/winpilot_home/tests/test_scene_coverage.py`

[打开文件](<../../python/winpilot_home/tests/test_scene_coverage.py>)

- 分类/语言：`test` / Python
- 规模：355 行，11,393 bytes
- SHA-256：`a21d80b11c57d1fd8c45b80c7f2102709be1f9eb0899329396d7bae8e49cdfdb`
- 源码事实摘要：验证 test scene coverage 对应的行为、回归或合同。
- 顶层符号/结构：`_jpeg`, `_scene`, `_tool_call`, `test_exact_scene_cycle_records_real_revisit`, `test_revisited_scene_blocks_only_already_explored_scroll_edge`, `test_unexecuted_action_does_not_create_coverage_edge`, `test_click_edge_requires_cross_scene_cycle_before_blocking`, `test_small_system_border_change_still_matches_same_content_scene`, `test_materially_different_content_is_a_new_scene`, `test_successful_downward_scroll_keeps_a_measured_frontier`, `test_two_stationary_scrolls_measure_boundary_and_reverse_sign`, `test_pixel_motion_estimator_reads_vertical_content_direction`
- 直接静态依赖：`__future__`, `base64`, `json`, `types`, `cv2`, `numpy`, `winpilot.agent.scene_coverage`, `winpilot.agent.loop`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/tests/test_scope_guard.py -->
### `python/winpilot_home/tests/test_scope_guard.py`

[打开文件](<../../python/winpilot_home/tests/test_scope_guard.py>)

- 分类/语言：`test` / Python
- 规模：294 行，10,612 bytes
- SHA-256：`f95452e0252cab4aca25ce43c10cf49b3d8bf26e89eee3ca6ecce89708f8056f`
- 源码事实摘要：验证 test scope guard 对应的行为、回归或合同。
- 顶层符号/结构：`_Session`, `test_scope_guard_blocks_when_exact_target_refocus_fails`, `test_scope_guard_uses_real_hit_window_when_auxiliary_window_owns_foreground`, `test_scope_guard_refocuses_keyboard_target_and_continues`, `test_scope_guard_refocuses_but_withholds_stale_spatial_coordinates`, `test_scope_guard_records_same_process_window_replacement_as_stale_frame`, `test_scope_guard_does_not_encode_application_specific_task_order`, `test_scope_guard_does_not_use_product_title_blacklists`, `test_scope_guard_does_not_mistake_winpilot_named_browser_target_for_control_plane`, `test_window_title_alone_never_changes_scope_ownership`, `test_scope_guard_allows_explicit_control_plane_test`, `test_scope_guard_fails_closed_for_spatial_mutation_without_target`, `test_scope_guard_rebinds_a_previously_proven_app_after_hwnd_replacement`, `test_scope_guard_batch_must_bind_target_before_first_spatial_action`，另有 2 项
- 直接静态依赖：`json`, `winpilot.agent`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/tests/test_script_validation.py -->
### `python/winpilot_home/tests/test_script_validation.py`

[打开文件](<../../python/winpilot_home/tests/test_script_validation.py>)

- 分类/语言：`test` / Python
- 规模：1101 行，40,502 bytes
- SHA-256：`bdce67de436967778f6494cb30a577103ed13ea97094889ea1c61101e5c436ac`
- 源码事实摘要：validate_script (pure) + GET/PUT /api/script HTTP round-trip.
- 顶层符号/结构：`test_structured_evidence_validation_rejects_unknown_fields`, `test_invalid_canonical_script_does_not_hide_valid_wps_fallback`, `test_valid_v1_script`, `test_valid_v2_nested_script`, `test_valid_agent_replay_only_tools`, `test_reject_bad_format`, `test_reject_non_dict`, `test_reject_steps_not_list`, `test_reject_unknown_tool`, `test_reject_unknown_tool_nested_in_branch`, `test_reject_if_without_cond`, `test_allow_if_with_explicit_always_cond`, `test_reject_script_without_any_executable_leaf`, `test_reject_missing_required_leaf_fields`，另有 44 项
- 直接静态依赖：`json`, `pytest`, `winpilot.recorder`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/tests/test_shell_pi_runtime.py -->
### `python/winpilot_home/tests/test_shell_pi_runtime.py`

[打开文件](<../../python/winpilot_home/tests/test_shell_pi_runtime.py>)

- 分类/语言：`test` / Python
- 规模：368 行，11,286 bytes
- SHA-256：`65a46840f958a30925facc9d2269b61ec9b57c67b1de18e8810828a8e6bcfd3a`
- 源码事实摘要：Focused tests for the Pi-style shell result semantics.
- 顶层符号/结构：`test_tail_truncation_keeps_the_latest_lines`, `test_tail_truncation_never_breaks_a_utf8_character`, `test_nonzero_result_is_actionable_and_preserves_full_output_path`, `test_timeout_result_gives_the_model_partial_state`, `test_selected_result_below_generic_boundary_is_not_truncated`, `test_redundant_selected_shell_wrapper_is_unwrapped`, `test_other_shell_wrapper_is_left_to_the_command_body`, `test_runtime_host_termination_is_the_one_process_kill_boundary`, `test_targeted_child_process_kill_remains_available`, `test_result_above_generic_boundary_is_recoverable`, `test_powershell_default_file_writes_are_utf8`, `test_powershell_preserves_native_exit_code_and_stderr`, `test_powershell_uses_final_statement_status`, `test_powershell_preserves_formatted_object_output`，另有 4 项
- 直接静态依赖：`__future__`, `json`, `subprocess`, `time`, `pathlib`, `pytest`, `winpilot.action`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/tests/test_stop_responsiveness.py -->
### `python/winpilot_home/tests/test_stop_responsiveness.py`

[打开文件](<../../python/winpilot_home/tests/test_stop_responsiveness.py>)

- 分类/语言：`test` / Python
- 规模：46 行，1,876 bytes
- SHA-256：`1cc50cc2b32692d79adaf98fbcd679da589afa4b05a2089f65683ebb5633701e`
- 源码事实摘要：Stop responsiveness: verify polling and the LLM call both honor stop_event.
- 顶层符号/结构：`test_check_aborts_on_stop_event`, `test_check_without_stop_runs_to_timeout`, `test_chat_abandons_inflight_call_on_stop`
- 直接静态依赖：`threading`, `time`, `winpilot.action`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/tests/test_submitted_image_coordinates.py -->
### `python/winpilot_home/tests/test_submitted_image_coordinates.py`

[打开文件](<../../python/winpilot_home/tests/test_submitted_image_coordinates.py>)

- 分类/语言：`test` / Python
- 规模：482 行，16,868 bytes
- SHA-256：`8479b17a251b700fe445b655f055fa12c2684e74af5cb61d31e0654b45bfbed5`
- 源码事实摘要：验证 test submitted image coordinates 对应的行为、回归或合同。
- 顶层符号/结构：`_map_point`, `_map_drag`, `_map_strokes`, `test_point_mapping_is_pure_and_preserves_exact_provenance`, `test_point_mapping_rejects_ambiguous_or_invalid_values_atomically`, `test_extent_style_right_bottom_edge_clamps_to_last_real_pixel`, `test_point_mapping_is_monotonic_for_random_sizes`, `test_invalid_dimensions_are_ignored_when_no_image_coordinate_is_consumed`, `test_constraint_mapping_is_pure_and_preserves_both_coordinate_layers`, `test_constraint_mapping_rejects_invalid_values_without_mutating_input`, `test_drag_mapping_preserves_path_order_adjacent_dedup_and_input`, `test_drag_maps_each_endpoint_and_meaningful_path_point_once`, `test_drag_mapping_fail_closed_without_mutating_input`, `test_stroke_mapping_preserves_stroke_point_order_count_and_input`，另有 4 项
- 直接静态依赖：`__future__`, `copy`, `math`, `random`, `pytest`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/tests/test_surface_binding.py -->
### `python/winpilot_home/tests/test_surface_binding.py`

[打开文件](<../../python/winpilot_home/tests/test_surface_binding.py>)

- 分类/语言：`test` / Python
- 规模：166 行，5,528 bytes
- SHA-256：`42a85a1f82104ed00cad25d716c96919090aece9821cc9338681b1496f4a8a81`
- 源码事实摘要：验证 test surface binding 对应的行为、回归或合同。
- 顶层符号/结构：`region`, `update`, `test_surface_binding_retains_outer_geometry_when_content_splits_detector`, `test_surface_binding_uses_client_region_as_authority_envelope`, `test_surface_binding_rejects_stale_frame_and_ambiguous_current_pixels`, `test_surface_binding_requires_repeated_layout_displacement`, `test_explicit_action_roi_is_scoped_without_rebinding_surface`, `test_new_window_identity_creates_fresh_binding_and_clears_action_rois`, `test_interaction_region_rejects_coordinate_space_mixups`
- 直接静态依赖：`__future__`, `pytest`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/tests/test_system_control_features.py -->
### `python/winpilot_home/tests/test_system_control_features.py`

[打开文件](<../../python/winpilot_home/tests/test_system_control_features.py>)

- 分类/语言：`test` / Python
- 规模：44 行，1,159 bytes
- SHA-256：`1c5829e6ed378b99a37e754063f9e376cf34a75d0ac5d8da608bb68ebf1aea87`
- 源码事实摘要：验证 test system control features 对应的行为、回归或合同。
- 顶层符号/结构：`_without_pycaw`, `test_volume_set_fails_closed_when_exact_backend_is_missing`, `test_volume_mute_does_not_claim_unknown_fallback_state`, `const:PYTHON_ROOT`
- 直接静态依赖：`__future__`, `builtins`, `sys`, `pathlib`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/tests/test_system_tools_undo.py -->
### `python/winpilot_home/tests/test_system_tools_undo.py`

[打开文件](<../../python/winpilot_home/tests/test_system_tools_undo.py>)

- 分类/语言：`test` / Python
- 规模：22 行，678 bytes
- SHA-256：`cf4ace3ca422f891df9747fc76fe4fa2ecc09e478cb76d9491fe705e645f1695`
- 源码事实摘要：验证 test system tools undo 对应的行为、回归或合同。
- 顶层符号/结构：`test_undo_last_without_run_owned_token_does_not_send_ctrl_z`
- 直接静态依赖：`json`, `winpilot.agent.tools`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/tests/test_target_window_anchor.py -->
### `python/winpilot_home/tests/test_target_window_anchor.py`

[打开文件](<../../python/winpilot_home/tests/test_target_window_anchor.py>)

- 分类/语言：`test` / Python
- 规模：310 行，12,689 bytes
- SHA-256：`250a8f74035269594a58601f5b367a8dacc90a72cb223765b0837042fb893c54`
- 源码事实摘要：验证 test target window anchor 对应的行为、回归或合同。
- 顶层符号/结构：`test_resolve_hwnd_prefers_live_session_target`, `test_action_hwnd_prefers_session_target_over_unrelated_foreground`, `test_resolve_hwnd_prefers_owned_foreground_dialog`, `test_resolve_hwnd_promotes_owned_dialog_even_with_explicit_parent`, `test_resolve_hwnd_ignores_dead_explicit_dialog_and_recovers_by_hint`, `test_resolve_hwnd_prefers_matching_foreground_over_stale_target`, `test_resolve_hwnd_adopts_same_process_foreground_sibling`, `test_resolve_hwnd_recovers_target_by_title_before_foreground`, `test_recover_bound_target_window_uses_process_identity_when_title_changes`, `test_refresh_bound_target_follows_matching_foreground_successor`, `test_refresh_bound_target_keeps_live_binding_for_unrelated_foreground`, `test_read_text_merges_forced_ocr`
- 直接静态依赖：`winpilot.agent.tools`, `types`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/tests/test_task_contract_runtime.py -->
### `python/winpilot_home/tests/test_task_contract_runtime.py`

[打开文件](<../../python/winpilot_home/tests/test_task_contract_runtime.py>)

- 分类/语言：`test` / Python
- 规模：1557 行，62,904 bytes
- SHA-256：`b498598d5f52cb5a567a35c7230f20c79a7d767e4dfa85bc72404ed3d63cb7ad`
- 源码事实摘要：验证 test task contract runtime 对应的行为、回归或合同。
- 顶层符号/结构：`_contract_test_provider_type`, `_compile`, `test_structured_intent_treats_send_synonyms_equally`, `test_structured_intent_extracts_open_ended_destination_relation`, `test_structured_intent_owns_chat_execution_mode`, `test_text_send_compiles_payload_and_typed_stages`, `test_text_send_never_compiles_an_empty_unowned_payload`, `test_non_gui_tasks_compile_to_capability_typed_runtimes`, `test_message_authorization_fails_closed_for_empty_or_missing_context`, `test_message_stage_runtime_passes_one_current_run_body_to_adapter`, `test_send_text_never_calls_adapter_without_current_task_authorization`, `test_cross_application_artifact_tasks_keep_one_lossless_desktop_goal`, `test_compiled_frontend_graph_has_no_whole_task_agent_goal`, `test_runtime_snapshot_keeps_every_evidence_and_artifact_item_typed`，另有 40 项
- 直接静态依赖：`__future__`, `pathlib`, `time`, `pytest`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/tests/test_tasking_aux_model_calls.py -->
### `python/winpilot_home/tests/test_tasking_aux_model_calls.py`

[打开文件](<../../python/winpilot_home/tests/test_tasking_aux_model_calls.py>)

- 分类/语言：`test` / Python
- 规模：111 行，3,761 bytes
- SHA-256：`625cac23070eb24523e0fda5b7a4317443e3a9060b771b12cebff68afdbb3c7a`
- 源码事实摘要：验证 test tasking aux model calls 对应的行为、回归或合同。
- 顶层符号/结构：`_ConfiguredApi`, `_FakeClient`, `_capture_boundary`, `test_conversation_uses_one_runtime_owned_model_attempt`, `test_message_draft_uses_one_runtime_owned_model_attempt`, `test_message_parse_failure_is_not_a_transport_retry`
- 直接静态依赖：`__future__`, `threading`, `types`, `pytest`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/tests/test_tool_schemas.py -->
### `python/winpilot_home/tests/test_tool_schemas.py`

[打开文件](<../../python/winpilot_home/tests/test_tool_schemas.py>)

- 分类/语言：`test` / Python
- 规模：929 行，35,846 bytes
- SHA-256：`d97b19a627c99c9adb2d17fdeaf30fa1b9dc2006de6b6e765c13f75e7ce745f3`
- 源码事实摘要：验证 test tool schemas 对应的行为、回归或合同。
- 顶层符号/结构：`_names`, `test_execute_subgoal_is_prioritized_before_low_level_gui_mutations`, `test_open_app_is_prioritized_before_manual_app_discovery_loop`, `test_open_conversation_can_discover_an_unresolved_channel`, `test_execute_subgoal_schema_states_only_structural_preconditions`, `test_execute_subgoal_schema_describes_fields_without_ui_tactics`, `test_execute_subgoal_schema_keeps_generic_spatial_creation_primitives`, `test_schema_exposes_agent_schedules_without_shell`, `test_schema_exposes_pluggable_web_search_without_shell`, `test_schema_exposes_coordinate_calibration_tools_without_shell`, `test_schema_exposes_per_run_source_bound_working_set`, `test_multi_source_research_task_detection_requires_research_and_multiple_sources`, `test_search_files_schema_exposes_fast_local_filters`, `test_strict_gui_schema_hides_file_tools_without_hiding_gui_tools`，另有 27 项
- 直接静态依赖：—
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/tests/test_trace_evidence.py -->
### `python/winpilot_home/tests/test_trace_evidence.py`

[打开文件](<../../python/winpilot_home/tests/test_trace_evidence.py>)

- 分类/语言：`test` / Python
- 规模：179 行，6,095 bytes
- SHA-256：`54dfad753c87107d6183457c56b433729f3ec1edfa178b1da9afe4bee1b3667c`
- 源码事实摘要：验证 test trace evidence 对应的行为、回归或合同。
- 顶层符号/结构：`test_llm_trace_redacts_frame_without_mutating_api_request`, `test_jsonl_trace_event_redacts_inline_frame_in_worker`, `test_trace_stop_waits_for_an_accepted_callback_before_integrity`, `test_trace_recorder_persists_exact_evidence_frame`
- 直接静态依赖：`numpy`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/tests/test_uia_perception.py -->
### `python/winpilot_home/tests/test_uia_perception.py`

[打开文件](<../../python/winpilot_home/tests/test_uia_perception.py>)

- 分类/语言：`test` / Python
- 规模：48 行，1,689 bytes
- SHA-256：`b8c09665e29eeabb351dc88f44ed45118e3c8d8b577f8faa286f6b79660c1aef`
- 源码事实摘要：验证 test uia perception 对应的行为、回归或合同。
- 顶层符号/结构：`test_chromium_legacy_root_is_skipped_only_when_modern_tree_exists`, `test_exact_uia_query_rejects_direct_legacy_renderer_match`
- 直接静态依赖：—
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/tests/test_unified_coding_runtime.py -->
### `python/winpilot_home/tests/test_unified_coding_runtime.py`

[打开文件](<../../python/winpilot_home/tests/test_unified_coding_runtime.py>)

- 分类/语言：`test` / Python
- 规模：34 行，1,528 bytes
- SHA-256：`fe9687ec6d7927b5e4b97aded690cb7af6432e8da5aa2dd2eb958b684617ccc1`
- 源码事实摘要：验证 test unified coding runtime 对应的行为、回归或合同。
- 顶层符号/结构：`test_tool_schemas_add_coding_runtime_tools_only_when_enabled`, `test_coding_runtime_search_text_respects_path`
- 直接静态依赖：`pathlib`, `sys`, `types`, `winpilot.agent.coding_tools`, `winpilot.agent.tool_schemas`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/tests/test_vision_priority.py -->
### `python/winpilot_home/tests/test_vision_priority.py`

[打开文件](<../../python/winpilot_home/tests/test_vision_priority.py>)

- 分类/语言：`test` / Python
- 规模：396 行，14,159 bytes
- SHA-256：`1ee268004a72c6c478cadf2a385130031d9b8d1ec71f37795763e5a4a8ed4ffc`
- 源码事实摘要：Tests for perception_mode / vision_priority_task (backward-compat).
- 顶层符号/结构：`test_raw_task_and_window_identity_default_to_normal`, `test_structured_visual_operation_selects_visual_mode`, `test_structured_active_interaction_selects_visual_mode`, `test_nonvisual_structured_operation_stays_normal`, `test_explicit_runtime_mode_is_preserved`, `test_vision_priority_uses_structured_intent_only`, `_fake_observe_env`, `test_ocr_skipped_when_uia_rich_and_normal_mode`, `test_toolbar_only_uia_count_does_not_suppress_ocr`, `test_ocr_forced_when_visual_mode`, `test_vlm_assist_runs_in_visual_mode_below_threshold`, `test_vlm_still_skipped_in_normal_mode_when_uia_rich`, `test_structured_ui_mode_skips_full_ocr_vlm_and_empty_rescue`, `test_empty_rescue_never_runs_disabled_ocr_or_vlm`，另有 7 项
- 直接静态依赖：`pytest`, `winpilot.perception`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/tests/test_visual_appearance.py -->
### `python/winpilot_home/tests/test_visual_appearance.py`

[打开文件](<../../python/winpilot_home/tests/test_visual_appearance.py>)

- 分类/语言：`test` / Python
- 规模：69 行，2,402 bytes
- SHA-256：`44474385e58ae27ce993168eede291ddfdf1e5bdacf5424ccbf4074d4c681b68`
- 源码事实摘要：Current-pixel appearance evidence stays geometric and role-safe.
- 顶层符号/结构：`_shared_surface`, `test_inventory_reports_current_colour_without_claiming_ownership`, `test_explicit_role_colours_match_components_but_do_not_infer_actor`
- 直接静态依赖：`__future__`, `cv2`, `numpy`, `winpilot.tasking.visual_appearance`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/tests/test_visual_capture_scope.py -->
### `python/winpilot_home/tests/test_visual_capture_scope.py`

[打开文件](<../../python/winpilot_home/tests/test_visual_capture_scope.py>)

- 分类/语言：`test` / Python
- 规模：536 行，22,259 bytes
- SHA-256：`d2e3b056cc99c9c6e4aa086d9ce72130b7601fab5ef2335a9036abc2783d9df6`
- 源码事实摘要：验证 test visual capture scope 对应的行为、回归或合同。
- 顶层符号/结构：`test_credible_work_surface_bounds_use_authoritative_frame_coordinates`, `test_work_surface_is_clipped_to_window_client_authority`, `test_region_frame_bounds_maps_physical_client_into_window_frame`, `test_credible_work_surface_bounds_preserve_ambiguous_failure`, `test_capture_scope_toggle_off_forces_full_desktop`, `test_capture_scope_auto_crops_only_verified_foreground_target`, `test_capture_scope_auto_falls_back_when_foreground_is_ambiguous`, `test_capture_scope_uses_runtime_window_state_not_global_task_words`, `test_capture_scope_does_not_classify_cross_application_word_orders`, `test_capture_scope_does_not_classify_comparison_or_side_by_side_words`, `test_capture_scope_does_not_expand_single_window_move_or_paste_tasks`, `test_vlm_describe_toggle_off_uses_full_desktop_and_returns_scope`, `test_vlm_verify_enabled_uses_only_verified_foreground_target`, `test_vlm_verify_ambiguous_foreground_falls_back_to_full_desktop`，另有 3 项
- 直接静态依赖：`__future__`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/tests/test_visual_closed_loop.py -->
### `python/winpilot_home/tests/test_visual_closed_loop.py`

[打开文件](<../../python/winpilot_home/tests/test_visual_closed_loop.py>)

- 分类/语言：`test` / Python
- 规模：4066 行，153,359 bytes
- SHA-256：`bf61e03f3d4d1c971ed070c4164a7155e3e432e3c0127200ef7b4d75e5267676`
- 源码事实摘要：验证 test visual closed loop 对应的行为、回归或合同。
- 顶层符号/结构：`semantic_grounding_enabled`, `authoritative_native_vision`, `_tool_call`, `test_native_point_scope_guard_blocks_taskbar_hit`, `test_native_point_scope_guard_does_not_reinterpret_full_desktop_selection`, `test_native_point_scope_guard_requires_fresh_crop_after_target_rebound`, `test_native_visual_observe_skips_lossy_structured_stack`, `test_visual_frame_marks_current_screen_epoch`, `test_every_native_spatial_action_gets_same_frame_magnified_detail`, `test_visual_frame_rebinds_verified_application_successor_before_binding`, `test_preflight_rebound_target_crop_returns_fresh_authoritative_frame`, `test_preflight_rebound_target_identity_blocks_full_desktop_keyboard_action`, `test_full_desktop_frame_stays_grounded_when_crop_becomes_available`, `test_initial_prebind_frame_settle_requires_a_bounded_quiet_period`，另有 89 项
- 直接静态依赖：`__future__`, `json`, `types`, `pytest`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/tests/test_visual_effects.py -->
### `python/winpilot_home/tests/test_visual_effects.py`

[打开文件](<../../python/winpilot_home/tests/test_visual_effects.py>)

- 分类/语言：`test` / Python
- 规模：459 行，16,101 bytes
- SHA-256：`8b21e7c616658bfea38c8ee41666aa63d123ad99550d737dad29448125ec2065`
- 源码事实摘要：验证 test visual effects 对应的行为、回归或合同。
- 顶层符号/结构：`_decision`, `_write_pair`, `test_pointer_gesture_realization_proves_all_declared_paths`, `test_pointer_gesture_realization_rejects_partial_or_missing_path`, `test_typed_freehand_turn_completion_proves_local_turn_without_terminal_claim`, `test_typed_freehand_turn_completion_rejects_wrong_requested_colour`, `test_pointer_path_pixels_reject_cursor_sized_endpoint_delta`, `test_pointer_path_pixels_reject_unrelated_large_roi_change`, `test_pointer_path_pixels_requires_every_path_even_when_one_is_complete`, `test_pointer_path_pixels_accepts_all_paths_amid_broad_transient_delta`, `test_pointer_path_pixels_rejects_full_area_repaint_despite_path_coverage`, `_direct_drag_decision`, `_write_direct_transition`, `test_direct_manipulation_transition_proves_appearance_transfer`，另有 3 项
- 直接静态依赖：`__future__`, `cv2`, `numpy`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/tests/test_visual_geometry.py -->
### `python/winpilot_home/tests/test_visual_geometry.py`

[打开文件](<../../python/winpilot_home/tests/test_visual_geometry.py>)

- 分类/语言：`test` / Python
- 规模：1123 行，40,347 bytes
- SHA-256：`73b9e327ebbfc1635db87b0ba96a02d25bef2111f254006742eac01bf7df562e`
- 源码事实摘要：验证 test visual geometry 对应的行为、回归或合同。
- 顶层符号/结构：`_decision`, `_write`, `test_local_geometry_corrects_coarse_drag_boxes_from_current_pixels`, `test_unique_multi_family_pixels_correct_client_chrome_coordinate_offset`, `test_explicit_target_effect_binding_follows_refined_geometry`, `test_drag_target_anchor_is_projected_into_refined_target`, `test_point_placement_uses_refined_semantic_anchor_as_drag_endpoint`, `test_object_anchored_click_is_regrounded_from_current_pixels`, `test_anchored_point_uses_target_semantics_without_losing_action_label`, `test_target_circle_prefers_complete_enclosing_boundary_over_nearby_arc`, `test_enclosing_target_accepts_substantial_partial_coarse_overlap`, `test_anchored_point_projects_semantic_anchor_with_refined_region`, `test_free_path_returns_explicit_typed_policy_proof`, `test_target_relative_free_path_records_coordinate_grounding`，另有 18 项
- 直接静态依赖：`__future__`, `pathlib`, `cv2`, `numpy`, `pytest`, `winpilot.tasking.interaction`, `winpilot.tasking.visual_geometry`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/tests/test_visual_interaction_server.py -->
### `python/winpilot_home/tests/test_visual_interaction_server.py`

[打开文件](<../../python/winpilot_home/tests/test_visual_interaction_server.py>)

- 分类/语言：`test` / Python
- 规模：293 行，9,300 bytes
- SHA-256：`7aa70cd79f681d111122279e0ea6f08351869b2526f02c7682b81df6dbf08b16`
- 源码事实摘要：验证 test visual interaction server 对应的行为、回归或合同。
- 顶层符号/结构：`_event`, `_dynamic_event`, `_corridor_event`, `_quiz_answers`, `_quiz_event`, `test_quiz_spec_is_seeded_mixed_and_does_not_publish_answer_key`, `test_quiz_evaluation_is_server_owned_and_omits_submitted_answers`, `test_browser_quiz_claim_is_replaced_by_server_evaluation`, `test_quiz_success_requires_trusted_server_verified_submit_event`, `test_corridor_success_requires_a_real_origin_to_goal_route`, `test_case_scoped_state_accepts_only_verified_quiz_completion`, `test_case_scoped_state_cannot_inherit_another_cases_success`, `test_case_scoped_state_reports_only_its_own_trusted_success`, `test_dynamic_success_requires_preceding_trusted_in_goal_pointerup`
- 直接静态依赖：`__future__`, `copy`, `eval_targets`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/tests/test_visual_path.py -->
### `python/winpilot_home/tests/test_visual_path.py`

[打开文件](<../../python/winpilot_home/tests/test_visual_path.py>)

- 分类/语言：`test` / Python
- 规模：361 行，12,956 bytes
- SHA-256：`65eed7466133f88bf391d3c741db972e852f0faecdca6e5c2b7f59ca585fecc1`
- 源码事实摘要：验证 test visual path 对应的行为、回归或合同。
- 顶层符号/结构：`_normalized`, `_distance_to_segment`, `_orient`, `test_guided_path_snaps_coarse_bends_to_source_safe_visible_centerline`, `test_guided_path_rejects_uniform_unbounded_background`, `test_route_surface_sampling_trims_one_transitive_colour_outlier`, `test_grounded_source_surface_overrides_off_route_semantic_waypoints`, `test_guided_path_retries_authoritative_resolution_when_coarse_grid_disconnects`, `test_guided_path_does_not_treat_destination_decoration_as_obstacle`, `test_drag_path_mode_is_typed_and_cannot_contradict_waypoints`
- 直接静态依赖：`__future__`, `math`, `cv2`, `numpy`, `pytest`, `winpilot.tasking.interaction`, `winpilot.tasking.visual_path`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/tests/test_visual_regions.py -->
### `python/winpilot_home/tests/test_visual_regions.py`

[打开文件](<../../python/winpilot_home/tests/test_visual_regions.py>)

- 分类/语言：`test` / Python
- 规模：887 行，32,862 bytes
- SHA-256：`39c6c9aaf8c5beda438e1723d837133cf2151a31e83258f550e71642e13b0e96`
- 源码事实摘要：验证 test visual regions 对应的行为、回归或合同。
- 顶层符号/结构：`_normalized`, `_grid`, `_open_partition`, `test_visible_region_inventory_exposes_repeated_current_pixel_cells`, `test_visible_region_inventory_merges_inner_and_outer_outline_contours`, `test_visible_region_inventory_recovers_fragmented_four_sided_boundary`, `test_open_partition_inventory_exposes_typed_cells_without_domain_rules`, `test_closed_repeated_faces_do_not_become_shifted_open_partitions`, `test_closed_child_cannot_seed_nearby_open_partition_inventory`, `test_circular_radial_faces_remain_general_closed_region_candidates`, `test_visible_bounded_region_randomized_grid_cells_are_contained`, `test_visible_bounded_region_does_not_invent_boundary_on_blank_canvas`, `test_anchored_subregion_selects_child_not_coarse_container`, `test_anchored_subregion_recovers_unique_nearby_overlapping_boundary`，另有 10 项
- 直接静态依赖：`__future__`, `random`, `cv2`, `numpy`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/tests/test_visual_servo.py -->
### `python/winpilot_home/tests/test_visual_servo.py`

[打开文件](<../../python/winpilot_home/tests/test_visual_servo.py>)

- 分类/语言：`test` / Python
- 规模：172 行，5,657 bytes
- SHA-256：`89cf56ea74c5a6f41970c828781fae7447ffdb6cbb451e4efbd2bfff800150c0`
- 源码事实摘要：验证 test visual servo 对应的行为、回归或合同。
- 顶层符号/结构：`_moving_scene`, `_initial_element`, `test_visual_servo_tracks_one_hover_move_and_confirms_before_click`, `test_visual_servo_fails_closed_when_target_disappears`, `test_visual_servo_waits_through_stale_frame_after_hover_trigger`, `test_visual_servo_does_not_converge_before_hover_guard_expires`, `test_servo_config_can_force_semantic_ocr_cross_check`
- 直接静态依赖：`time`, `cv2`, `numpy`, `winpilot.fast.visual_servo`, `winpilot.perception.model`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/tests/test_visual_state_summary.py -->
### `python/winpilot_home/tests/test_visual_state_summary.py`

[打开文件](<../../python/winpilot_home/tests/test_visual_state_summary.py>)

- 分类/语言：`test` / Python
- 规模：69 行，2,955 bytes
- SHA-256：`49baf4b5667e63b10a3dd3009106503fb3d0350e62d4646fd6e53db21d3032dd`
- 源码事实摘要：验证 test visual state summary 对应的行为、回归或合同。
- 顶层符号/结构：`test_visual_state_summary_does_not_inject_task_specific_pixel_rules`, `test_loop_surfaces_visual_advisory`, `test_loop_deduplicates_unchanged_world_advisories`, `test_native_visual_loop_keeps_world_and_playbook_tactics_out_of_context`
- 直接静态依赖：—
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/tests/test_vlm_guard.py -->
### `python/winpilot_home/tests/test_vlm_guard.py`

[打开文件](<../../python/winpilot_home/tests/test_vlm_guard.py>)

- 分类/语言：`test` / Python
- 规模：127 行，4,456 bytes
- SHA-256：`36cae9f347d486b88fe083fa695f08cf1a40806f0e6385627093b4640cf5ae1f`
- 源码事实摘要：验证 test vlm guard 对应的行为、回归或合同。
- 顶层符号/结构：`test_deepseek_v4_is_reported_as_text_only`, `test_deepseek_chat_is_reported_as_text_only`, `test_non_deepseek_multimodal_endpoint_stays_enabled`, `test_mimo_v25_reuses_main_model_credentials_for_vision`, `test_semantic_visual_verifier_returns_strict_structured_evidence`, `test_tool_vlm_verify_keeps_original_user_goal_in_narrow_checklist`
- 直接静态依赖：`winpilot.perception`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/tests/test_vnext_action_effect.py -->
### `python/winpilot_home/tests/test_vnext_action_effect.py`

[打开文件](<../../python/winpilot_home/tests/test_vnext_action_effect.py>)

- 分类/语言：`test` / Python
- 规模：481 行，15,899 bytes
- SHA-256：`3691fbef378a58885ee131c79b0d3e6a8e6b8cb77922beb2fc75f1dd6a0971f5`
- 源码事实摘要：验证 test vnext action effect 对应的行为、回归或合同。
- 顶层符号/结构：`_Frame`, `_image`, `_frame`, `_request`, `_summary`, `test_exact_authoritative_pixels_are_unchanged_and_evidence_bound`, `test_action_effect_request_does_not_silently_normalize_text`, `test_any_aligned_pixel_delta_is_reported_as_changed`, `test_different_observation_domains_are_unknown`, `test_decode_and_decoded_size_failures_remain_unknown`, `test_spoofed_image_hash_cannot_claim_exact_payload`, `test_malformed_frame_contract_fails_closed`, `test_noncanonical_or_non_ascii_frame_payload_fails_closed`, `test_request_snapshots_mutable_structural_frames`，另有 9 项
- 直接静态依赖：`__future__`, `base64`, `dataclasses`, `hashlib`, `types`, `cv2`, `numpy`, `pytest`, `winpilot.vnext.verification.action_effect`, `winpilot.vnext.mission`, `winpilot.vnext.json_contract`, `winpilot.vnext.verification`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/tests/test_vnext_actor_contracts.py -->
### `python/winpilot_home/tests/test_vnext_actor_contracts.py`

[打开文件](<../../python/winpilot_home/tests/test_vnext_actor_contracts.py>)

- 分类/语言：`test` / Python
- 规模：1694 行，52,576 bytes
- SHA-256：`dd3fce15594642fa1504999a44b0fb3f6c3707e6d2ce6a805b46801e175b874d`
- 源码事实摘要：Identity-free Actor proposal and Runtime-owned envelope tests.
- 顶层符号/结构：`_module_name`, `_imports`, `_matches_prefix`, `_nested_json`, `_schema`, `_point_schema`, `_drag_schema`, `_tool`, `_catalog`, `_catalog_with_drag`, `_mission_reducer`, `_mission`, `_profile`, `_state`，另有 45 项
- 直接静态依赖：`__future__`, `ast`, `dataclasses`, `json`, `math`, `pathlib`, `random`, `types`, `pytest`, `pydantic`, `winpilot.visual_runtime.frame_authority`, `winpilot.vnext.actor`, `winpilot.vnext.capabilities`, `winpilot.vnext.json_contract`，另有 2 项
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/tests/test_vnext_atomic_desktop_adapter.py -->
### `python/winpilot_home/tests/test_vnext_atomic_desktop_adapter.py`

[打开文件](<../../python/winpilot_home/tests/test_vnext_atomic_desktop_adapter.py>)

- 分类/语言：`test` / Python
- 规模：3008 行，97,564 bytes
- SHA-256：`492afa2157d2b299a0ad8b4f4f324e2ed8afdfdc95c7d0827e4533068040473b`
- 源码事实摘要：Focused fake-port tests for the default-unreachable atomic vNext bridge.
- 顶层符号/结构：`_jpeg`, `_map_submitted_test_point`, `_Probe`, `_Trace`, `_RawHarness`, `_Harness`, `_AttemptResult`, `_proposal`, `_projection_id`, `_envelope`, `_replace_envelope`, `_replace_action_args`, `_broker_projection`, `_harness`，另有 67 项
- 直接静态依赖：`__future__`, `ast`, `base64`, `dataclasses`, `hashlib`, `json`, `pathlib`, `threading`, `typing`, `cv2`, `numpy`, `pytest`, `winpilot.action.raw_device`, `winpilot.action.desktop_queue`，另有 9 项
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/tests/test_vnext_canary_campaign_state.py -->
### `python/winpilot_home/tests/test_vnext_canary_campaign_state.py`

[打开文件](<../../python/winpilot_home/tests/test_vnext_canary_campaign_state.py>)

- 分类/语言：`test` / Python
- 规模：3018 行，99,106 bytes
- SHA-256：`3fd6ad89320a848abc2a1ffd66968233bb47e8aa433737d30824a9361f9b90a8`
- 源码事实摘要：验证 test vnext canary campaign state 对应的行为、回归或合同。
- 顶层符号/结构：`_load_campaign_module`, `_matrix`, `_manifest_kwargs`, `manifest_kwargs`, `manifest`, `sealed_campaign`, `_invalid_data`, `_failed_data`, `_break_stack`, `_break_source`, `_break_build`, `_break_model`, `_break_c2_binding`, `test_manifest_is_canonical_self_hashed_exclusive_and_round_trips`，另有 79 项
- 直接静态依赖：`__future__`, `copy`, `hashlib`, `importlib.util`, `json`, `sys`, `pathlib`, `typing`, `pytest`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/tests/test_vnext_capability_broker.py -->
### `python/winpilot_home/tests/test_vnext_capability_broker.py`

[打开文件](<../../python/winpilot_home/tests/test_vnext_capability_broker.py>)

- 分类/语言：`test` / Python
- 规模：1939 行，61,784 bytes
- SHA-256：`ef8d6abb570c37523adcc6f9d36a84a9c493f873a3c2ea374668f97c60b19cd8`
- 源码事实摘要：Pure ToolCatalog, grant, state, and Capability Broker tests.
- 顶层符号/结构：`_schema`, `_tool`, `_catalog`, `_catalog_with_drag`, `_mission`, `_mission_with_artifact_binding`, `_profile`, `_state`, `_provider_offer`, `_wire_samples`, `test_every_capability_wire_entry_requires_plain_exact_complete_objects`, `test_wire_array_fields_require_literal_lists`, `test_wire_scalars_never_coerce_or_normalize`, `_bounded_tool_wire_cases`，另有 43 项
- 直接静态依赖：`__future__`, `ast`, `dataclasses`, `pathlib`, `random`, `types`, `pytest`, `vnext_import_boundary_ast`, `winpilot.vnext.capabilities`, `winpilot.vnext.capabilities.state`, `winpilot.vnext.json_contract`, `winpilot.vnext.mission`, `winpilot.vnext.profiling`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/tests/test_vnext_desktop_workspace.py -->
### `python/winpilot_home/tests/test_vnext_desktop_workspace.py`

[打开文件](<../../python/winpilot_home/tests/test_vnext_desktop_workspace.py>)

- 分类/语言：`test` / Python
- 规模：476 行，13,606 bytes
- SHA-256：`624b762e1edb3f8f10d80d11b74af8043f037eef6f16e9b85c3c62bde4f1f4b8`
- 源码事实摘要：验证 test vnext desktop workspace 对应的行为、回归或合同。
- 顶层符号/结构：`_surface`, `_binding`, `_Topology`, `_workspace`, `_shell_workspace`, `test_desktop_shell_flag_retires_when_a_real_window_is_adopted`, `test_unrelated_foreground_is_observed_but_never_adopted`, `test_completed_click_adopts_stable_foreground_on_next_observation`, `test_discarded_full_desktop_successor_is_never_adopted`, `test_two_discarded_successors_exhaust_bounded_handoff`, `test_successor_that_loses_foreground_fails_validation`, `test_not_started_input_never_creates_causal_transition`, `test_any_completed_action_adopts_only_a_real_foreground_change`, `test_disappearing_pending_candidate_is_discarded`，另有 11 项
- 直接静态依赖：`__future__`, `dataclasses`, `pytest`, `winpilot.vnext.desktop_adapter`, `winpilot.vnext.mission`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/tests/test_vnext_evidence_registry.py -->
### `python/winpilot_home/tests/test_vnext_evidence_registry.py`

[打开文件](<../../python/winpilot_home/tests/test_vnext_evidence_registry.py>)

- 分类/语言：`test` / Python
- 规模：166 行，5,573 bytes
- SHA-256：`2011b9a1ae7c841cb7ce3201b114c88b880a438cd80301ac031a0574b971a17b`
- 源码事实摘要：验证 test vnext evidence registry 对应的行为、回归或合同。
- 顶层符号/结构：`_ref`, `test_identical_id_is_idempotent_but_conflicting_content_fails`, `test_scope_and_value_type_fail_closed_without_writes`, `test_append_many_is_atomic_for_registry_and_batch_conflicts`, `test_snapshot_is_sorted_immutable_and_evidence_scope_compatible`, `test_concurrent_appends_preserve_all_unique_refs_and_one_identity`, `test_reducer_reads_registry_dynamically_on_each_apply`, `test_close_rejects_all_writes_but_keeps_reads_available`
- 直接静态依赖：`__future__`, `concurrent.futures`, `dataclasses`, `pytest`, `winpilot.vnext.evidence`, `winpilot.vnext.mission`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/tests/test_vnext_execution_profile.py -->
### `python/winpilot_home/tests/test_vnext_execution_profile.py`

[打开文件](<../../python/winpilot_home/tests/test_vnext_execution_profile.py>)

- 分类/语言：`test` / Python
- 规模：697 行，26,324 bytes
- SHA-256：`efa443db7646e65cc21d4104ecc8257794db771f32a7618b528cd28ea00e9e54`
- 源码事实摘要：Pure ExecutionProfile contract and deterministic policy tests.
- 顶层符号/结构：`_proposal`, `test_contracts_are_strict_versioned_and_round_trip_canonically`, `test_all_profile_from_dict_entrypoints_require_plain_complete_objects`, `test_profile_wire_rejects_legacy_aliases_and_nonliteral_field_types`, `test_profile_wire_strings_round_trip_without_silent_normalization`, `test_profile_wire_arrays_are_literal_lists_and_triggers_are_objects`, `test_profile_wire_uses_shared_bounded_json_limits`, `test_execution_policy_exposes_only_canonical_entrypoints`, `test_proposal_rejects_runtime_gui_plan_and_identity_authority`, `test_nested_forbidden_authority_is_rejected_before_policy`, `test_policy_safe_standard_fallback_is_audited`, `test_unknown_or_noncanonical_capabilities_fail_closed`, `test_missing_runtime_capability_facts_fail_closed`, `test_policy_snapshots_can_only_be_tightened`，另有 13 项
- 直接静态依赖：`__future__`, `ast`, `collections`, `dataclasses`, `math`, `pathlib`, `random`, `pytest`, `winpilot.vnext.capability_names`, `winpilot.vnext.json_contract`, `winpilot.vnext.profiling`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/tests/test_vnext_kernel_boundaries.py -->
### `python/winpilot_home/tests/test_vnext_kernel_boundaries.py`

[打开文件](<../../python/winpilot_home/tests/test_vnext_kernel_boundaries.py>)

- 分类/语言：`test` / Python
- 规模：886 行，28,252 bytes
- SHA-256：`1a3c86f085f3f6d05a4987e1f108f3aed3ea9c3f1a039a27ef53e22913e9ed47`
- 源码事实摘要：Static boundaries for the pure, default-unreachable vNext kernel.
- 顶层符号/结构：`_source_files`, `_module_name`, `_imports`, `_matches_prefix`, `_analyze_path`, `_sorted_root_bindings`, `_root_static_imports_are_exact`, `_dynamic_boundary_calls`, `test_kernel_imports_only_stdlib_or_its_own_pure_packages`, `test_vnext_root_has_no_eager_or_lazy_imports`, `test_only_one_composition_root_can_reach_live_vnext_by_import`, `test_live_composition_dependency_closure_cannot_reach_legacy_agent`, `test_retired_electron_agent_subprocess_cannot_return`, `test_ast_import_analyzer_rejects_dynamic_vnext_bypasses`，另有 15 项
- 直接静态依赖：`__future__`, `ast`, `re`, `pathlib`, `pytest`, `vnext_import_boundary_ast`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/tests/test_vnext_live_canary_e2e_harness.py -->
### `python/winpilot_home/tests/test_vnext_live_canary_e2e_harness.py`

[打开文件](<../../python/winpilot_home/tests/test_vnext_live_canary_e2e_harness.py>)

- 分类/语言：`test` / Python
- 规模：3206 行，100,663 bytes
- SHA-256：`2563bafca74d71420c902caa3d44ac6f5beb8dddc0b8ee29f0c9b79b0efe7320`
- 源码事实摘要：验证 test vnext live canary e2e harness 对应的行为、回归或合同。
- 顶层符号/结构：`_assert_exact_json_value`, `_complete_campaign_config`, `_write_config_fixture`, `_config_contract_sha256`, `_load`, `_client_harness`, `_canary_harness`, `test_target_handoff_watcher_waits_for_electron_minimize`, `_oracle`, `_input_hook`, `_stack`, `_model_config`, `_manifest`, `test_visible_client_selector_is_exact_and_read_back`，另有 49 项
- 直接静态依赖：`__future__`, `copy`, `hashlib`, `importlib.util`, `inspect`, `json`, `sys`, `time`, `types`, `uuid`, `pathlib`, `typing`, `pytest`, `winpilot.vnext.actor`，另有 1 项
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/tests/test_vnext_live_model_roles.py -->
### `python/winpilot_home/tests/test_vnext_live_model_roles.py`

[打开文件](<../../python/winpilot_home/tests/test_vnext_live_model_roles.py>)

- 分类/语言：`test` / Python
- 规模：4171 行，131,144 bytes
- SHA-256：`244fa3878ae14a4d917876f7acb4243fbc4d4b807c77ca4adac784b28e4c3288`
- 源码事实摘要：Focused fake-provider tests for the vNext live visual actor.
- 顶层符号/结构：`_point_schema`, `_actor_turn`, `_tool_response`, `_connector_response`, `_actor_shape_response`, `_schema_keywords`, `_RecordingClient`, `_RecordingConnectorGateway`, `_build_clients`, `test_actor_uses_zero_retry_client_and_strict_visual_context`, `test_visual_actor_can_request_a_fresh_desktop_without_input`, `test_visual_actor_defers_refresh_shape_to_the_single_runtime_parse`, `test_initial_actor_turn_replies_with_native_history_and_short_system_contract`, `test_initial_observe_can_seed_remaining_work_for_first_visual_action`，另有 78 项
- 直接静态依赖：`__future__`, `base64`, `json`, `threading`, `dataclasses`, `pathlib`, `types`, `typing`, `pytest`, `winpilot.modeling`, `winpilot.vnext.actor`, `winpilot.vnext.model_roles`, `winpilot.vnext.runtime`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/tests/test_vnext_mechanical_core_contract.py -->
### `python/winpilot_home/tests/test_vnext_mechanical_core_contract.py`

[打开文件](<../../python/winpilot_home/tests/test_vnext_mechanical_core_contract.py>)

- 分类/语言：`test` / Python
- 规模：226 行，7,663 bytes
- SHA-256：`adeff863dcc0862e1a167fba9cb6ff6e6bc4154f7e3d7138e870cc3a40a0a1f4`
- 源码事实摘要：Ownership contract for the shared mechanical core the vNext line runs on.
- 顶层符号/结构：`_module_name`, `_module_paths`, `_imports`, `_matches_prefix`, `_reachable_from_product_line`, `test_mechanical_core_modules_exist`, `test_every_mechanical_core_module_is_reachable_from_the_product_line`, `test_mechanical_core_modules_never_import_legacy_or_planning_layers`, `test_mechanical_core_perception_leaks_are_pinned_shrink_only`, `test_product_line_legacy_verification_reach_is_pinned_shrink_only`, `const:PACKAGE_ROOT`, `const:LIVE_COMPOSITION_MODULE`, `const:MECHANICAL_CORE_MODULES`, `const:CORE_FORBIDDEN_IMPORT_PREFIXES`，另有 4 项
- 直接静态依赖：`__future__`, `ast`, `pathlib`, `vnext_import_boundary_ast`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/tests/test_vnext_mission.py -->
### `python/winpilot_home/tests/test_vnext_mission.py`

[打开文件](<../../python/winpilot_home/tests/test_vnext_mission.py>)

- 分类/语言：`test` / Python
- 规模：1908 行，71,608 bytes
- SHA-256：`6fe80d9fa5e119cf26fce2a5b240fc91603929c038d65a79ba0998f34178a971`
- 源码事实摘要：P0/P1 sidecar tests; none of these tests start a model or touch a desktop.
- 顶层符号/结构：`_action_effect_material`, `_transition_with_effect`, `_evidence`, `_mission_evidence`, `_definition`, `_verify_event`, `_required_verification_identity`, `_mission_verification_payload`, `_mission_verify_event`, `_raw_event`, `_verified_two_obligation_reducer`, `test_definition_falls_back_to_one_lossless_obligation`, `test_definition_and_event_canonical_wire_are_deterministic`, `test_contracts_are_strict_versioned_json_and_canonical_on_wire`，另有 51 项
- 直接静态依赖：`__future__`, `hashlib`, `random`, `dataclasses`, `pytest`, `winpilot.vnext.mission`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/tests/test_vnext_mission_completion_verifier.py -->
### `python/winpilot_home/tests/test_vnext_mission_completion_verifier.py`

[打开文件](<../../python/winpilot_home/tests/test_vnext_mission_completion_verifier.py>)

- 分类/语言：`test` / Python
- 规模：420 行，15,385 bytes
- SHA-256：`5e0f0fcba24cdd3e1af74915a45508525dcae3259414811a0920f3d4fe06e4f0`
- 源码事实摘要：P5b deterministic Mission completion verifier contracts and handoff.
- 顶层符号/结构：`_evidence`, `_accepted`, `_request`, `test_request_is_strict_versioned_replayable_and_exactly_scoped`, `test_accepted_verification_rejects_missing_or_extra_evidence`, `test_actor_or_telemetry_evidence_is_rejected_by_actual_trust`, `test_claim_kind_cannot_hide_behind_deterministic_trust`, `test_independent_visual_evidence_is_allowed_but_unknown_trust_fails_closed`, `test_evidence_scope_and_obligation_association_are_checked`, `test_request_rejects_empty_and_duplicate_obligation_identities`, `test_mission_level_evidence_can_cover_multiple_obligations_once`, `test_verifier_assessment_is_positive_and_contains_no_runtime_identity`, `test_verifier_revalidates_forged_constructed_request`, `test_runtime_binding_is_stable_self_contained_and_hashes_full_evidence`，另有 3 项
- 直接静态依赖：`__future__`, `concurrent.futures`, `dataclasses`, `hashlib`, `json`, `pytest`, `pydantic`, `winpilot.vnext.evidence`, `winpilot.vnext.mission`, `winpilot.vnext.verification`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/tests/test_vnext_mission_runtime.py -->
### `python/winpilot_home/tests/test_vnext_mission_runtime.py`

[打开文件](<../../python/winpilot_home/tests/test_vnext_mission_runtime.py>)

- 分类/语言：`test` / Python
- 规模：1922 行，63,547 bytes
- SHA-256：`0f7a9e89fd73145653415c1ec4724b0eb28b6a6c21154781f3185150132cc716`
- 源码事实摘要：验证 test vnext mission runtime 对应的行为、回归或合同。
- 顶层符号/结构：`_empty_schema`, `_point_schema`, `_drag_schema`, `_scroll_schema`, `_text_schema`, `_hotkey_schema`, `_move_schema`, `_sequence_schema`, `_tool`, `_catalog`, `_action`, `_claim`, `_observe`, `_blocked`，另有 61 项
- 直接静态依赖：`__future__`, `dataclasses`, `hashlib`, `json`, `threading`, `typing`, `pytest`, `winpilot.vnext.actor`, `winpilot.vnext.capabilities`, `winpilot.vnext.evidence`, `winpilot.vnext.mission`, `winpilot.vnext.profiling`, `winpilot.vnext.runtime`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/tests/test_vnext_verification_contracts.py -->
### `python/winpilot_home/tests/test_vnext_verification_contracts.py`

[打开文件](<../../python/winpilot_home/tests/test_vnext_verification_contracts.py>)

- 分类/语言：`test` / Python
- 规模：620 行，22,634 bytes
- SHA-256：`f4bca86e9a5bb467c5e7ccb77bbf85bf9c96cac7eb3ee700ed921b701b92e386`
- 源码事实摘要：Pure P5a verifier contracts; no MissionRuntime or model integration.
- 顶层符号/结构：`_evidence`, `_request`, `ScriptedEvaluator`, `_partial`, `test_assessment_is_versioned_identity_free_and_strict_json`, `test_wire_object_requires_plain_dict_and_every_canonical_field`, `test_wire_strings_round_trip_without_silent_normalization`, `test_wire_confidence_requires_an_exact_float`, `test_wire_object_rejects_cycles_depth_and_oversized_values`, `test_wire_json_recursion_and_non_finite_float_fail_as_schema_errors`, `test_assessment_constructor_requires_literal_boolean`, `test_request_round_trip_binds_scope_and_ignores_untrusted_evidence`, `test_runtime_binding_accepts_real_visual_trust_as_policy_gated_evidence`, `test_runtime_binding_is_stable_and_requires_full_coverage_and_trusted_evidence`，另有 7 项
- 直接静态依赖：`__future__`, `ast`, `dataclasses`, `importlib.util`, `json`, `pathlib`, `threading`, `typing`, `pytest`, `winpilot.vnext.json_contract`, `winpilot.vnext.mission`, `winpilot.vnext.verification`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/tests/test_vnext_wire_contracts.py -->
### `python/winpilot_home/tests/test_vnext_wire_contracts.py`

[打开文件](<../../python/winpilot_home/tests/test_vnext_wire_contracts.py>)

- 分类/语言：`test` / Python
- 规模：922 行，28,372 bytes
- SHA-256：`403a43a2ebbb29fe45feca5da1c6647a8378de9db9f43536707e48d09a2b5b30`
- 源码事实摘要：Negative coverage for the canonical vNext Mission wire boundary.
- 顶层符号/结构：`_definition`, `_event`, `_evidence`, `_artifact`, `_target`, `_fact`, `test_all_wire_parsers_round_trip_one_exact_schema`, `test_all_wire_parsers_require_plain_dict_and_exact_complete_fields`, `test_legacy_wire_aliases_are_not_kernel_inputs`, `test_legacy_read_and_constructor_aliases_are_absent`, `test_boolean_fields_reject_truthiness_coercion`, `test_integer_fields_reject_numeric_coercion_and_bool_as_int`, `test_float_fields_require_finite_literal_float`, `test_required_string_fields_reject_none_and_string_coercion`，另有 24 项
- 直接静态依赖：`__future__`, `copy`, `math`, `types`, `typing`, `pytest`, `winpilot.vnext.json_contract`, `winpilot.vnext.mission`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/tests/test_web_search.py -->
### `python/winpilot_home/tests/test_web_search.py`

[打开文件](<../../python/winpilot_home/tests/test_web_search.py>)

- 分类/语言：`test` / Python
- 规模：53 行，1,930 bytes
- SHA-256：`53087f543a146aa0befaa8ba90f7d176d1554439fd78279b38e76f6894fbbdae`
- 源码事实摘要：验证 test web search 对应的行为、回归或合同。
- 顶层符号/结构：`_Response`, `test_duckduckgo_search_returns_compact_structured_results`, `test_configured_serper_failure_falls_back_to_bing`
- 直接静态依赖：—
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/tests/test_win32_window_match.py -->
### `python/winpilot_home/tests/test_win32_window_match.py`

[打开文件](<../../python/winpilot_home/tests/test_win32_window_match.py>)

- 分类/语言：`test` / Python
- 规模：7 行，467 bytes
- SHA-256：`89c8a152348cf517bfe508416a8b8e8b422d1f803bde9571b70e6f010f88045a`
- 源码事实摘要：验证 test win32 window match 对应的行为、回归或合同。
- 顶层符号/结构：`test_title_contains_normalizes_underscores_spaces_and_case`
- 直接静态依赖：—
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/tests/test_window_tools.py -->
### `python/winpilot_home/tests/test_window_tools.py`

[打开文件](<../../python/winpilot_home/tests/test_window_tools.py>)

- 分类/语言：`test` / Python
- 规模：117 行，3,881 bytes
- SHA-256：`2342c106ae079861c32c17be1f37d308eb7e5b9a312ea19cfcddbf5b58888eda`
- 源码事实摘要：验证 test window tools 对应的行为、回归或合同。
- 顶层符号/结构：`_WindowSession`, `test_focus_window_resolves_visible_title`, `test_focus_window_returns_structured_error_for_missing_target`, `test_focus_window_does_not_scan_or_activate_background_browser_tabs`, `test_focus_window_can_bind_explicit_browser_hwnd_without_tab_inference`
- 直接静态依赖：`json`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/tests/test_windows_task_contract_e2e.py -->
### `python/winpilot_home/tests/test_windows_task_contract_e2e.py`

[打开文件](<../../python/winpilot_home/tests/test_windows_task_contract_e2e.py>)

- 分类/语言：`test` / Python
- 规模：164 行，7,513 bytes
- SHA-256：`158f3ebd44023f241b3512a64355bb655f0f4768d78d336043e242db93383fa3`
- 源码事实摘要：Real Windows Paint -> managed artifact -> QQ delivery tests.
- 顶层符号/结构：`_post`, `_get`, `_run_graph`, `_assert_runtime_is_current`, `test_real_frontend_path_paint_capture_qq_delivery`, `const:ROOT`, `const:REPORT_ROOT`, `const:TASKS`
- 直接静态依赖：`__future__`, `json`, `os`, `subprocess`, `time`, `pathlib`, `urllib.request`, `pytest`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/tests/test_windows_visual_interaction_e2e.py -->
### `python/winpilot_home/tests/test_windows_visual_interaction_e2e.py`

[打开文件](<../../python/winpilot_home/tests/test_windows_visual_interaction_e2e.py>)

- 分类/语言：`test` / Python
- 规模：755 行，32,770 bytes
- SHA-256：`0e7ba899efe134e15edd9ac252d32ec4e17ce1b2c2cc55f8385e189d9ef78f1f`
- 源码事实摘要：Opt-in real-client Windows E2E matrix for general visual interactions.
- 顶层符号/结构：`_headers`, `_request`, `_free_port`, `_wait_json`, `_browser_executable`, `_find_window`, `_close_window`, `_poll_run`, `_assert_runtime_is_current`, `_trace_action_types`, `_assert_model_owned_visual_evidence`, `_assert_external_scenario`, `test_real_frontend_api_visual_scenario`, `const:ROOT`，另有 5 项
- 直接静态依赖：`__future__`, `ctypes`, `json`, `os`, `shutil`, `socket`, `subprocess`, `sys`, `tempfile`, `time`, `uuid`, `pathlib`, `typing`, `urllib.error`，另有 2 项
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/tests/test_world_bulletin.py -->
### `python/winpilot_home/tests/test_world_bulletin.py`

[打开文件](<../../python/winpilot_home/tests/test_world_bulletin.py>)

- 分类/语言：`test` / Python
- 规模：1361 行，47,023 bytes
- SHA-256：`0db0771cb426b2b134aa48af984ec0a75b22ca20ddc4565dcff6258810019266`
- 源码事实摘要：验证 test world bulletin 对应的行为、回归或合同。
- 顶层符号/结构：`_synthetic_pointer_tests_bypass_live_hwnd_scope`, `_status`, `test_world_model_screen_bulletin_advisory_is_edge_triggered`, `test_world_model_tracks_no_pixel_change_before_waiting_for_ocr_delta`, `test_world_model_no_effect_advisory_is_not_drawing_specific`, `test_world_model_retains_source_bound_facts_per_run`, `test_record_facts_tool_updates_run_working_set`, `test_record_facts_duplicate_subject_is_update_not_new_source`, `test_record_facts_rejects_new_subject_bound_to_existing_source_url`, `test_record_facts_uses_visible_address_bar_url_over_model_claim`, `test_record_facts_normalizes_source_fact_to_visible_address_bar`, `test_verified_subgoal_repetition_is_dispatched_for_shared_progress_owner`, `test_identical_no_progress_subgoal_result_is_not_replaced_by_session_policy`, `test_research_history_does_not_block_later_gui_action`，另有 29 项
- 直接静态依赖：`types`, `pytest`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/tests/test_wps_v2_compat.py -->
### `python/winpilot_home/tests/test_wps_v2_compat.py`

[打开文件](<../../python/winpilot_home/tests/test_wps_v2_compat.py>)

- 分类/语言：`test` / Python
- 规模：1122 行，39,499 bytes
- SHA-256：`e6d2232022c7ab777d56e300f324a5de60d732bebd768b4a80df60f6496c2637`
- 源码事实摘要：wps/2 Replayer integration + v1 backward-compatibility.
- 顶层符号/结构：`SpyReplayer`, `_write`, `_stub_window_inventory`, `test_v1_flat_script_runs_each_leaf_in_order`, `test_v1_leaf_failure_stops_and_reports`, `test_v1_no_vars_means_no_interpolation`, `test_v2_if_true_runs_then_with_interpolation`, `test_v2_loop_count_paths_indices`, `test_v2_ext_delay_records_result`, `test_v2_ext_delay_cancellation_is_prompt_and_not_success`, `test_v2_var_set_then_used_by_later_leaf`, `test_v2_unknown_ext_step_fails_gracefully`, `test_replayer_runs_draw_strokes_leaf`, `test_replayer_runs_detached_leaf`，另有 36 项
- 直接静态依赖：`json`, `types`, `numpy`, `winpilot.recorder`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/tests/vnext_import_boundary_ast.py -->
### `python/winpilot_home/tests/vnext_import_boundary_ast.py`

[打开文件](<../../python/winpilot_home/tests/vnext_import_boundary_ast.py>)

- 分类/语言：`test` / Python
- 规模：1163 行，40,233 bytes
- SHA-256：`913a0f1b45dfb3347944a0d248f236281c982be7725038d4d6cd649a44072396`
- 源码事实摘要：Small AST data-flow helper for the live-vNext import boundary tests.
- 顶层符号/结构：`_matches_prefix`, `StaticImport`, `_resolved_from_base`, `_collect_static_imports`, `DynamicImportCall`, `ImportAnalysis`, `_Value`, `_constant`, `_merge_values`, `_function_value`, `_merge_environments`, `_bound_names`, `_FunctionLocalCollector`, `_argument_names`，另有 13 项
- 直接静态依赖：`__future__`, `ast`, `dataclasses`, `pathlib`, `typing`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/webui/app.js -->
### `python/winpilot_home/webui/app.js`

[打开文件](<../../python/winpilot_home/webui/app.js>)

- 分类/语言：`supporting-code` / JavaScript
- 规模：710 行，30,313 bytes
- SHA-256：`7d642bac8ca9cd70fb6f54741a5cdf9d3b39ca2c56c916f477d04df39a96429e`
- 源码事实摘要：WinPilot front-end: WebSocket event stream + chat + settings panel. */
- 顶层符号/结构：`$`, `chatList`, `logList`, `config`, `running`, `runMetrics`, `metricsTimer`, `el`, `fmtTs`, `api`, `patchConfig`, `usageCostText`, `resetRunMetrics`, `finishRunMetrics`，另有 26 项
- 直接静态依赖：—
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/webui/index.html -->
### `python/winpilot_home/webui/index.html`

[打开文件](<../../python/winpilot_home/webui/index.html>)

- 分类/语言：`supporting-code` / HTML
- 规模：164 行，8,052 bytes
- SHA-256：`40620fe29c37f8f8f1b6a6905480d351f68d72fa557144edacb1a56d4951ce57`
- 源码事实摘要：WinPilot 电脑操控 Agent
- 顶层符号/结构：—
- 直接静态依赖：—
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/webui/style.css -->
### `python/winpilot_home/webui/style.css`

[打开文件](<../../python/winpilot_home/webui/style.css>)

- 分类/语言：`supporting-code` / CSS
- 规模：267 行，10,501 bytes
- SHA-256：`4456e8562e63a02b89fd6342ae3cfbcbbcc78d5b0605bc5b1f0dd3893195c5db`
- 源码事实摘要：WinPilot UI — adapts to the host app's light / dark theme.
- 顶层符号/结构：—
- 直接静态依赖：—
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/__init__.py -->
### `python/winpilot_home/winpilot/__init__.py`

[打开文件](<../../python/winpilot_home/winpilot/__init__.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：5 行，133 bytes
- SHA-256：`39b03fcbeff761b05c29fbac8edcc942cef26971efef8c65afe72f913e59c2e1`
- 源码事实摘要：Python 包 python.winpilot_home.winpilot 的初始化与公开边界。
- 顶层符号/结构：—
- 直接静态依赖：`.utils.dpi`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/action/__init__.py -->
### `python/winpilot_home/winpilot/action/__init__.py`

[打开文件](<../../python/winpilot_home/winpilot/action/__init__.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：0 行，0 bytes
- SHA-256：`e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`
- 源码事实摘要：Python 包 python.winpilot_home.winpilot.action 的初始化与公开边界。
- 顶层符号/结构：—
- 直接静态依赖：—
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/action/clipboard_ops.py -->
### `python/winpilot_home/winpilot/action/clipboard_ops.py`

[打开文件](<../../python/winpilot_home/winpilot/action/clipboard_ops.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：300 行，10,170 bytes
- SHA-256：`8f273339d30c0a26625af2c3e0de589ccaa147f41bd221ad29ebfd481b8cbce3`
- 源码事实摘要：vNext-owned physical capture / window / clipboard backends.
- 顶层符号/结构：`_process_name`, `capture_screen_backend`, `list_windows_backend`, `_image_file_to_dib`, `set_clipboard_dib`, `paste_image_backend`, `const:CAPTURE_FORMAT`, `const:WINDOWS_FORMAT`, `const:PASTE_FORMAT`, `const:_CAPTURE_EXTENSIONS`
- 直接静态依赖：`__future__`, `ctypes`, `ntpath`, `time`, `io`, `pathlib`, `typing`, `..utils.log`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/action/desktop_queue.py -->
### `python/winpilot_home/winpilot/action/desktop_queue.py`

[打开文件](<../../python/winpilot_home/winpilot/action/desktop_queue.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：560 行，21,644 bytes
- SHA-256：`1ff4a18da885cfd886af728c6d0c4de78f3cfa340b1dae2d8b43101e5beb3430`
- 源码事实摘要：Single-writer desktop action queue.
- 顶层符号/结构：`DesktopActionCanceled`, `_ResourceWaiter`, `_resource_conflict`, `_resource_sets_conflict`, `_QueuedDesktopJob`, `DesktopActionQueue`, `queued_desktop_action`, `const:T`, `const:DESKTOP_ACTION_QUEUE`
- 直接静态依赖：`__future__`, `functools`, `inspect`, `itertools`, `threading`, `time`, `collections.abc`, `dataclasses`, `typing`, `..utils.log`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/action/executor.py -->
### `python/winpilot_home/winpilot/action/executor.py`

[打开文件](<../../python/winpilot_home/winpilot/action/executor.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：3574 行，124,934 bytes
- SHA-256：`dc4c49f0857f720031ff5a6d28beed70ec5c3af95b4e2aff4c5b17728168a8e8`
- 源码事实摘要：Action executor: SendInput-based mouse/keyboard plus window management.
- 顶层符号/结构：`_valid_stop_signal`, `_stop_requested`, `install_press_probe`, `restore_press_probe`, `_sample_pressed_state`, `_settle_after_release`, `_KeyBdInput`, `_MouseInput`, `_InputUnion`, `_Input`, `_send_inputs`, `_key_input`, `_hardware_key_input`, `_mouse_input`，另有 97 项
- 直接静态依赖：`__future__`, `base64`, `ctypes`, `inspect`, `math`, `os`, `re`, `string`, `subprocess`, `threading`, `time`, `io`, `pathlib`, `typing`，另有 8 项
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/action/raw_device.py -->
### `python/winpilot_home/winpilot/action/raw_device.py`

[打开文件](<../../python/winpilot_home/winpilot/action/raw_device.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：1228 行，46,544 bytes
- SHA-256：`d6c36d27832c03837cc64e21273419e25c9878c888871b3714ac1a3fa5704e3a`
- 源码事实摘要：Typed execution of already-admitted physical input.
- 顶层符号/结构：`_canonical_drag_points`, `_canonical_held_keys`, `MouseButton`, `InputEventKind`, `StopSignal`, `_valid_stop_signal`, `PhysicalInputEvent`, `ClickCount`, `ClickMechanism`, `FinalPointerGuardDecision`, `FinalKeyboardGuardDecision`, `_scalar_facts`, `RawActionContext`, `RawBackendResult`，另有 16 项
- 直接静态依赖：`__future__`, `collections.abc`, `dataclasses`, `enum`, `math`, `threading`, `types`, `typing`, `..visual_runtime.keyboard_contracts`, `.replay_policy`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/action/replay_policy.py -->
### `python/winpilot_home/winpilot/action/replay_policy.py`

[打开文件](<../../python/winpilot_home/winpilot/action/replay_policy.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：68 行，2,179 bytes
- SHA-256：`4f56f5552f229cbc21b7f13472121f7416e393579ff25944ea1b62e56cc736d6`
- 源码事实摘要：Fail-closed policy for retrying a desktop mutation.
- 顶层符号/结构：`MutationOutcome`, `ReplayDisposition`, `ActionReplayDecision`, `decide_action_replay`
- 直接静态依赖：`__future__`, `dataclasses`, `enum`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/action/safety.py -->
### `python/winpilot_home/winpilot/action/safety.py`

[打开文件](<../../python/winpilot_home/winpilot/action/safety.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：59 行，2,923 bytes
- SHA-256：`c6c18e0a1adea0dc06205c0660dd83dc872587f179511c4b91d174a09820c95c`
- 源码事实摘要：Irreversible-command guard for shell execution.
- 顶层符号/结构：`evaluate`, `guard`, `const:_CATASTROPHIC`
- 直接静态依赖：`__future__`, `re`, `..utils.log`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/action/shell.py -->
### `python/winpilot_home/winpilot/action/shell.py`

[打开文件](<../../python/winpilot_home/winpilot/action/shell.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：735 行，27,903 bytes
- SHA-256：`6229b8fbc198a39836b5423c2b94f246f20398c6d259cc5a152fed9589f997ea`
- 源码事实摘要：Shell execution: run cmd/PowerShell commands and capture their output.
- 顶层符号/结构：`_unwrap_redundant_shell_command`, `_runtime_host_termination_guard`, `shell_enabled`, `_truncate`, `_truncate_tail`, `_save_full_output`, `_shape_result`, `_powershell_command`, `_decode`, `_kill_process_tree`, `_run_legacy_subprocess`, `run`, `write_file`, `_foreground_window_fact`，另有 9 项
- 直接静态依赖：`__future__`, `os`, `re`, `subprocess`, `sys`, `tempfile`, `time`, `uuid`, `pathlib`, `..config`, `..utils.log`, `.`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/action/verify.py -->
### `python/winpilot_home/winpilot/action/verify.py`

[打开文件](<../../python/winpilot_home/winpilot/action/verify.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：901 行，35,825 bytes
- SHA-256：`068ef033ea0c0b3f4f4b603b848f2a1b54169f207dfc4b916152c8c281c6bc36`
- 源码事实摘要：Action→Verify→Retry engine.
- 顶层符号/结构：`_canonical_expect`, `_norm_visible_text`, `_match_visible_text_precise`, `_match_visible_text`, `VerifyOutcome`, `_AsyncTextProbe`, `_AsyncGroundTruthProbe`, `_related_window`, `_target_hwnd`, `_full_screen_region`, `_find_text`, `_ocr_enabled`, `_uia_enabled`, `_check_window`，另有 15 项
- 直接静态依赖：`__future__`, `re`, `threading`, `time`, `dataclasses`, `typing`, `win32gui`, `..config`, `..perception`, `..utils.log`, `..utils.screenshot`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/agent/__init__.py -->
### `python/winpilot_home/winpilot/agent/__init__.py`

[打开文件](<../../python/winpilot_home/winpilot/agent/__init__.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：0 行，0 bytes
- SHA-256：`e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`
- 源码事实摘要：Python 包 python.winpilot_home.winpilot.agent 的初始化与公开边界。
- 顶层符号/结构：—
- 直接静态依赖：—
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/agent/coding.py -->
### `python/winpilot_home/winpilot/agent/coding.py`

[打开文件](<../../python/winpilot_home/winpilot/agent/coding.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：32 行，1,110 bytes
- SHA-256：`bad243de8dfbdb6a96d226575bb2ff8954b2ce1eb1e786b2ea87fa4833c44f31`
- 源码事实摘要：Compatibility shim for the removed Coding sub-agent.
- 顶层符号/结构：`workspace_dir`, `CodingRunner`
- 直接静态依赖：`__future__`, `pathlib`, `.loop`, `.runtime_profile`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/agent/coding_tools.py -->
### `python/winpilot_home/winpilot/agent/coding_tools.py`

[打开文件](<../../python/winpilot_home/winpilot/agent/coding_tools.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：418 行，20,132 bytes
- SHA-256：`8399e7f89ff5672170daac281ff3c05d686af03d017feeaeee6ca5f8cab576a7`
- 源码事实摘要：Coding Runtime tools: deterministic filesystem / repo operations.
- 顶层符号/结构：`_tool`, `coding_tool_schemas`, `CodingResult`, `coding_runtime_tool_names`, `CodingSession`, `const:_MAX_READ_CHARS`, `const:_SKIP_DIRS`, `const:_TEXT_EXT`
- 直接静态依赖：`__future__`, `json`, `re`, `urllib.parse`, `urllib.request`, `pathlib`, `typing`, `..utils.log`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/agent/context_guidance.py -->
### `python/winpilot_home/winpilot/agent/context_guidance.py`

[打开文件](<../../python/winpilot_home/winpilot/agent/context_guidance.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：214 行，9,317 bytes
- SHA-256：`06a290db12c84d25fa00e17944e31e68b87daf70a0bd64ffce0602182d097593`
- 源码事实摘要：Planning, world-model, and playbook guidance for AgentRunner.
- 顶层符号/结构：`ContextGuidanceMixin`
- 直接静态依赖：`__future__`, `..config`, `..utils.log`, `.`, `.gui_plan_helpers`, `.planner`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/agent/desktop_tools.py -->
### `python/winpilot_home/winpilot/agent/desktop_tools.py`

[打开文件](<../../python/winpilot_home/winpilot/agent/desktop_tools.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：1275 行，58,163 bytes
- SHA-256：`019bfc78f13ae5943667860be31f5c3681bcdae12629d1c2e8671d67ac73f2bb`
- 源码事实摘要：General desktop utility tool handlers for ToolSession.
- 顶层符号/结构：`DesktopToolMixin`
- 直接静态依赖：`__future__`, `base64`, `ctypes`, `threading`, `io`, `pathlib`, `..action`, `..action.verify`, `..config`, `..perception`, `..utils.log`, `..utils.screenshot`, `.interaction_tools`, `.tool_result`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/agent/done_verifier.py -->
### `python/winpilot_home/winpilot/agent/done_verifier.py`

[打开文件](<../../python/winpilot_home/winpilot/agent/done_verifier.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：2331 行，110,341 bytes
- SHA-256：`1b5535287cc3b19a22e67987750edf9101ce490ea238aee0a7f3e79ab37c6add`
- 源码事实摘要：Done-gate verification helpers for AgentRunner.
- 顶层符号/结构：`_word_attr`, `_docx_document_evidence`, `_docx_task_format_errors`, `_xlsx_column_number`, `_xlsx_column_label`, `_xlsx_workbook_evidence`, `_xlsx_task_errors`, `_loop_attr`, `_config`, `_configured_vision_judge_model`, `_explicit_saved_paths`, `DoneVerifierMixin`, `const:_JUDGE_MAX_TOKENS`, `const:_NATIVE_JUDGE_NONVISUAL_TOOLS`，另有 6 项
- 直接静态依赖：`__future__`, `hashlib`, `json`, `re`, `sys`, `time`, `datetime`, `pathlib`, `xml.etree`, `zipfile`, `..config`, `..model_adapter`, `..modeling`, `..perception`，另有 3 项
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/agent/failure_classifier.py -->
### `python/winpilot_home/winpilot/agent/failure_classifier.py`

[打开文件](<../../python/winpilot_home/winpilot/agent/failure_classifier.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：605 行，22,877 bytes
- SHA-256：`de59eb0bfe57ea2e0f5261b89fcefcc271f5bc152b6559be4d9c8aa6d56ec582`
- 源码事实摘要：Factual classification of desktop tool failures.
- 顶层符号/结构：`FailureType`, `FailureClassification`, `parse_tool_json`, `_json_dict`, `_iter_dicts`, `_values_for_key`, `_first_dict`, `_child_payloads`, `_truthy_bool`, `_compact_reason`, `_dedupe`, `_has_failed_signal`, `_candidate_labels`, `_missing_texts`，另有 6 项
- 直接静态依赖：`__future__`, `json`, `re`, `dataclasses`, `enum`, `typing`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/agent/failure_recovery.py -->
### `python/winpilot_home/winpilot/agent/failure_recovery.py`

[打开文件](<../../python/winpilot_home/winpilot/agent/failure_recovery.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：132 行，4,142 bytes
- SHA-256：`5010f3f83cc187f71b9318740773772000ad1a4f092e60ea9493dc763a39b80b`
- 源码事实摘要：Deterministic recovery routing for classified desktop-agent failures.
- 顶层符号/结构：`FailureRecoveryStatus`, `FailureRecoveryResult`, `_root_result_is_proven_success`, `_root_result_is_nonmutating_negative_observation`, `FailureRecoveryLayer`, `attach_recovery_metadata`
- 直接静态依赖：`__future__`, `json`, `dataclasses`, `enum`, `typing`, `.failure_classifier`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/agent/fast_tools.py -->
### `python/winpilot_home/winpilot/agent/fast_tools.py`

[打开文件](<../../python/winpilot_home/winpilot/agent/fast_tools.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：425 行，18,478 bytes
- SHA-256：`569e575232c2352efdb03bea6900b4cb113160ad08af24cdfc67befa5c9f353c`
- 源码事实摘要：Fast-controller tool handlers for ToolSession.
- 顶层符号/结构：`_has_execute_subgoal_routine`, `_needs_resolved_target_window`, `_blocked_unresolved_target_payload`, `_blocked_nonblocking_notice_click`, `_batch_open_app_target`, `FastToolMixin`
- 直接静态依赖：`__future__`, `json`, `re`, `typing`, `..fast.action_contract`, `..fast.controller`, `..fast.perception_daemon`, `..fast.subgoal`, `..perception`, `..perception.reducer`, `.tool_result`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/agent/file_tools.py -->
### `python/winpilot_home/winpilot/agent/file_tools.py`

[打开文件](<../../python/winpilot_home/winpilot/agent/file_tools.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：149 行，6,121 bytes
- SHA-256：`1615f7a474f3a098ce3fb322cff9a09f2935a098fdbf50d4fb2ec2a325895924`
- 源码事实摘要：File and repository tool handlers for the unified Agent.
- 顶层符号/结构：`FileToolMixin`, `const:_SKIP_DIRS`
- 直接静态依赖：`__future__`, `re`, `pathlib`, `..utils.log`, `.tool_result`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/agent/graphical_affordances.py -->
### `python/winpilot_home/winpilot/agent/graphical_affordances.py`

[打开文件](<../../python/winpilot_home/winpilot/agent/graphical_affordances.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：254 行，9,568 bytes
- SHA-256：`6bd798b6ca441f0d91dcca6a24bb47ea2cc69b5639d2430e33e901e40d935b09`
- 源码事实摘要：Pixel-native hypotheses for small graphical GUI affordances.
- 顶层符号/结构：`_decode`, `_normalized`, `_descriptor`, `_appearance_similarity`, `detect_graphical_affordances`
- 直接静态依赖：`__future__`, `base64`, `typing`, `cv2`, `numpy`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/agent/gui_plan_helpers.py -->
### `python/winpilot_home/winpilot/agent/gui_plan_helpers.py`

[打开文件](<../../python/winpilot_home/winpilot/agent/gui_plan_helpers.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：110 行，4,418 bytes
- SHA-256：`5486395efe0def879c470949a9fa18af03814b8ec0b3136c5f87fd8aab3c509e`
- 源码事实摘要：Small, domain-neutral GUI planning helpers.
- 顶层符号/结构：`_looks_like_native_gui_operation_task`, `_schema_profile_for_task`, `_looks_like_pure_open_plan_goal`, `_pure_open_goal_matches_bound_target`, `const:_OPEN_PLAN_TERMS`, `const:_OPEN_PLAN_MIXED_ACTION_TERMS`
- 直接静态依赖：`__future__`, `re`, `..config`, `..perception.app_aliases`, `.task_rules`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/agent/interaction_tools.py -->
### `python/winpilot_home/winpilot/agent/interaction_tools.py`

[打开文件](<../../python/winpilot_home/winpilot/agent/interaction_tools.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：1594 行，71,167 bytes
- SHA-256：`9f5dad0415b4e286608e48e55170e9476f3c7ceedbf3856dc644fe74fbe91d67`
- 源码事实摘要：Input and visible-action tool handlers for ToolSession.
- 顶层符号/结构：`_call_with_optional_hwnd`, `_clipboard_sequence_number`, `_looks_like_input_element`, `_actionable_semantic_hit`, `_prefer_physical_text_input`, `_normalize_draw_strokes`, `_validate_normalized_draw_strokes`, `_tools_attr`, `_expect_timeout`, `_printable_text_sequence`, `InteractionToolMixin`, `const:_INPUT_ROLES`, `const:_ACTIONABLE_HIT_ROLES`
- 直接静态依赖：`__future__`, `json`, `hashlib`, `inspect`, `math`, `sys`, `time`, `typing`, `win32gui`, `..action`, `..config`, `..perception`, `..utils.log`, `..utils.screenshot`，另有 1 项
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/agent/loop.py -->
### `python/winpilot_home/winpilot/agent/loop.py`

[打开文件](<../../python/winpilot_home/winpilot/agent/loop.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：7336 行，339,526 bytes
- SHA-256：`d60f8bc6c551da91406d0161316caf4dcaf94c3f5b32710886b65e220105f51d`
- 源码事实摘要：Unified WinPilot Agent loop.
- 顶层符号/结构：`NativeFrameCoordinateError`, `_normalize_stringified_frame_strokes`, `_normalize_frame_stroke_list_structure`, `_result_explicitly_blocked_before_execution`, `_unexecuted_constraint_category`, `_client`, `_main_model_has_vision`, `_main_agent_api_settings`, `_sanitize_assistant`, `AgentRunner`, `const:_KEEP_FULL_OBSERVATIONS`, `const:_API_RETRIES`, `const:_RATE_LIMIT_BACKOFF_S`, `const:_GUI_TOOL_RECOVERY_MAX_TOKENS`，另有 4 项
- 直接静态依赖：`__future__`, `copy`, `hashlib`, `json`, `re`, `threading`, `time`, `uuid`, `typing`, `win32gui`, `..config`, `..memory`, `..model_adapter`, `..modeling`，另有 24 项
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/agent/mcp_tools.py -->
### `python/winpilot_home/winpilot/agent/mcp_tools.py`

[打开文件](<../../python/winpilot_home/winpilot/agent/mcp_tools.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：33 行，1,295 bytes
- SHA-256：`580410e4bfff3813552b9f33b83282538889fb41e810bc7722beff89afd0943a`
- 源码事实摘要：ToolSession wrappers for optional outbound MCP providers.
- 顶层符号/结构：`McpToolMixin`
- 直接静态依赖：`__future__`, `.tool_result`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/agent/memory_tools.py -->
### `python/winpilot_home/winpilot/agent/memory_tools.py`

[打开文件](<../../python/winpilot_home/winpilot/agent/memory_tools.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：232 行，10,346 bytes
- SHA-256：`2d2f9d1393104bdbc24688edf9bc61a835515cdae43e3558064771b11103612e`
- 源码事实摘要：Memory graph tool handlers for ToolSession.
- 顶层符号/结构：`_fmt`, `_compact_node`, `MemoryToolMixin`
- 直接静态依赖：`__future__`, `json`, `typing`, `..memory`, `..utils.log`, `.tool_result`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/agent/perception_tools.py -->
### `python/winpilot_home/winpilot/agent/perception_tools.py`

[打开文件](<../../python/winpilot_home/winpilot/agent/perception_tools.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：1250 行，55,387 bytes
- SHA-256：`ce82103a5105e812ea4470ff48b1c9aeca71d198f4bd497cd9a2d27c84158d73`
- 源码事实摘要：Perception and visual-inspection tool handlers for ToolSession.
- 顶层符号/结构：`_tools_attr`, `_suppress_nonblocking_notice_lines`, `PerceptionToolMixin`
- 直接静态依赖：`__future__`, `json`, `sys`, `time`, `..config`, `..perception.model`, `..perception`, `..perception.ocr_lines`, `..utils.image_capture`, `..utils.log`, `..utils.screenshot`, `.task_rules`, `.tool_result`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/agent/plan_tools.py -->
### `python/winpilot_home/winpilot/agent/plan_tools.py`

[打开文件](<../../python/winpilot_home/winpilot/agent/plan_tools.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：588 行，24,712 bytes
- SHA-256：`499b45a0afc6d58cdc89ca41731ac054626e012faa31356e5a1bb24762e3bf0d`
- 源码事实摘要：Planning and terminal state tool handlers for ToolSession.
- 顶层符号/结构：`_tools_attr`, `_research_goal_matches_record`, `_research_source_completion_error`, `_is_coding_runtime`, `_looks_like_form_or_quiz_context`, `_looks_like_submit_or_save_goal`, `_latest_completion_rate`, `_file_check_paths`, `_spurious_agent_schedule_file_check`, `_normalize_visible_text`, `_accepts_historical_visible_state`, `_visible_check_values`, `_color_keys_in_text`, `PlanToolMixin`，另有 8 项
- 直接静态依赖：`__future__`, `re`, `sys`, `..action`, `..utils.log`, `.`, `.task_rules`, `.tool_result`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/agent/planner.py -->
### `python/winpilot_home/winpilot/agent/planner.py`

[打开文件](<../../python/winpilot_home/winpilot/agent/planner.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：673 行，29,061 bytes
- SHA-256：`1565936f75944ce32f313b3ae8818480f2d9365596ee5f22ac9b15cdba218507`
- 源码事实摘要：Planning / todo layer: optional task decomposition for long-horizon tasks.
- 顶层符号/结构：`_valid_check`, `_compact_text`, `_looks_like_state_token`, `_occurrence_looks_like_button_label`, `_value_has_result_context`, `_check_echoes_button_label`, `_sanitize_check_for_goal`, `_is_gui_action_goal`, `_visible_text_from_verify_step`, `_merge_adjacent_visible_verification_steps`, `planning_enabled`, `_planner_intent_field`, `should_plan_upfront`, `_parse_steps`，另有 13 项
- 直接静态依赖：`__future__`, `json`, `re`, `uuid`, `collections.abc`, `copy`, `pathlib`, `typing`, `..config`, `..modeling`, `..utils.log`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/agent/playbooks.py -->
### `python/winpilot_home/winpilot/agent/playbooks.py`

[打开文件](<../../python/winpilot_home/winpilot/agent/playbooks.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：194 行，7,410 bytes
- SHA-256：`66575bd300c1e24ed7b4d971913f683a7b9f6c846f0b3ab33220d968e45fe95c`
- 源码事实摘要：Playbook layer: app/situation-specific tactics as data, not prompt code.
- 顶层符号/结构：`Playbook`, `_render`, `_tuple_of_strings`, `_default_conditions`, `_default_evidence`, `load_all`, `enabled`, `for_task`, `for_window_title`, `render_section`, `render_playbook`, `const:PLAYBOOKS_DIR`, `const:_HOME`, `const:_PATH_VARS`
- 直接静态依赖：`__future__`, `json`, `dataclasses`, `pathlib`, `..config`, `..utils.log`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/agent/prompts.py -->
### `python/winpilot_home/winpilot/agent/prompts.py`

[打开文件](<../../python/winpilot_home/winpilot/agent/prompts.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：132 行，5,763 bytes
- SHA-256：`017867e51a62e19351cb9083c2633e1048bccb26b8fedbded066d331740656dc`
- 源码事实摘要：System prompt for the WinPilot agent.
- 顶层符号/结构：`system_prompt`, `const:_COMPACT_SYSTEM_TEMPLATE`, `const:_NATIVE_EXECUTION_CONTRACT`, `const:_TEXT_EXECUTION_CONTRACT`, `const:_VLM_SECTION`, `const:_PLANNING_SECTION`, `const:_EXPERIENCE_HEADER`, `const:_VISION_NATIVE`, `const:_VISION_TEXT_ONLY`
- 直接静态依赖：`__future__`, `datetime`, `pathlib`, `..utils.screenshot`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/agent/reflection.py -->
### `python/winpilot_home/winpilot/agent/reflection.py`

[打开文件](<../../python/winpilot_home/winpilot/agent/reflection.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：72 行，3,213 bytes
- SHA-256：`d41ba07a82189af398a8a71e94f8633a70d657a72ee0b17979df5d174f2fe346`
- 源码事实摘要：Reflection checkpoint helpers for AgentRunner.
- 顶层符号/结构：`ReflectionMixin`
- 直接静态依赖：`__future__`, `..config`, `..utils.log`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/agent/router.py -->
### `python/winpilot_home/winpilot/agent/router.py`

[打开文件](<../../python/winpilot_home/winpilot/agent/router.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：47 行，1,742 bytes
- SHA-256：`7b32568ca7bb1322146286ec88779e6c9509d624f15f9886b09bbd6d54b7fba1`
- 源码事实摘要：Runner construction for the single WinPilot Agent.
- 顶层符号/结构：`build_runner`
- 直接静态依赖：`__future__`, `threading`, `typing`, `..utils.log`, `.loop`, `.runtime_profile`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/agent/run_state.py -->
### `python/winpilot_home/winpilot/agent/run_state.py`

[打开文件](<../../python/winpilot_home/winpilot/agent/run_state.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：400 行，14,718 bytes
- SHA-256：`220c5023cd9e1be16b6d7d9279e25dd50e95de5e5368f83b96f174c250d0ae14`
- 源码事实摘要：Single task-progress ledger for the unified Agent loop.
- 顶层符号/结构：`_now_ms`, `_json_dict`, `_batch_last_action`, `_batch_has_semantic_observe`, `_batch_has_mutation`, `_batch_has_expect`, `_passive_observe_verifies_pending_actions`, `RunStateMachine`, `const:ACTION_TOOLS`, `const:OBSERVE_TOOLS`, `const:PASSIVE_PIXEL_OBSERVE_TOOLS`
- 直接静态依赖：`__future__`, `json`, `time`, `dataclasses`, `typing`, `..fast.action_contract`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/agent/runtime_profile.py -->
### `python/winpilot_home/winpilot/agent/runtime_profile.py`

[打开文件](<../../python/winpilot_home/winpilot/agent/runtime_profile.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：283 行，12,132 bytes
- SHA-256：`be45f0adc1d514c9f251d2ce181b949640e48643f0992f3d73b3425840faf985`
- 源码事实摘要：Runtime capability profile for the single WinPilot Agent.
- 顶层符号/结构：`coding_workspace_dir`, `RuntimeProfile`, `build_runtime_profile`
- 直接静态依赖：`__future__`, `dataclasses`, `pathlib`, `typing`, `..config`, `.task_rules`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/agent/scene_coverage.py -->
### `python/winpilot_home/winpilot/agent/scene_coverage.py`

[打开文件](<../../python/winpilot_home/winpilot/agent/scene_coverage.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：744 行，31,133 bytes
- SHA-256：`a6f1f25d7767e3e7a52b38ef5b4591671dcc68b98f844d2a20134146894345eb`
- 源码事实摘要：Bounded visual scene coverage and local-loop detection.
- 顶层符号/结构：`SceneRecord`, `SceneCoverageLedger`, `const:_SCENE_CONTENT_ROI`
- 直接静态依赖：`__future__`, `base64`, `hashlib`, `dataclasses`, `typing`, `cv2`, `numpy`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/agent/scope_guard.py -->
### `python/winpilot_home/winpilot/agent/scope_guard.py`

[打开文件](<../../python/winpilot_home/winpilot/agent/scope_guard.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：480 行，18,506 bytes
- SHA-256：`cb2913f5166e3e7c137a1dd2c1de13050d71557a8f73962a400fef11ed1a63c2`
- 源码事实摘要：Generic foreground/target-window guard for GUI mutations.
- 顶层符号/结构：`_batch_establishes_target_before_spatial`, `_requires_live_target`, `_missing_target_result`, `_title`, `_class_name`, `_window_alive`, `_same_or_related`, `_screen_point`, `_point_hits_target`, `_record_focus_transition`, `_context`, `take_scope_recovery`, `ensure_scope`, `const:GUI_SCOPE_MUTATIONS`，另有 2 项
- 直接静态依赖：`__future__`, `json`, `typing`, `win32gui`, `..action`, `..config`, `..fast.action_contract`, `..perception`, `.tool_result`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/agent/session.py -->
### `python/winpilot_home/winpilot/agent/session.py`

[打开文件](<../../python/winpilot_home/winpilot/agent/session.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：143 行，4,972 bytes
- SHA-256：`60120af52eddd5e29b67c7602585164be420b147b6da46455aaef32b52cab5b9`
- 源码事实摘要：Session memory: cross-task continuity within one server lifetime.
- 顶层符号/结构：`SessionEntry`, `SessionMemory`, `render_context`, `const:_MAX_ENTRIES`, `const:_RECENT_FOR_PROMPT`, `const:_SUMMARY_CHARS`, `const:SESSION`
- 直接静态依赖：`__future__`, `threading`, `dataclasses`, `typing`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/agent/system_tools.py -->
### `python/winpilot_home/winpilot/agent/system_tools.py`

[打开文件](<../../python/winpilot_home/winpilot/agent/system_tools.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：1258 行，54,888 bytes
- SHA-256：`5ce891331dac4cfc76e633b7046472b2696951b39e542197f5a0f8f2604287c3`
- 源码事实摘要：System, launch, tray, shell, and undo tool handlers for ToolSession.
- 顶层符号/结构：`_tools_attr`, `SystemToolMixin`
- 直接静态依赖：`__future__`, `json`, `re`, `shutil`, `sys`, `time`, `pathlib`, `urllib.parse`, `win32con`, `win32gui`, `..action`, `..`, `..config`, `..perception`，另有 3 项
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/agent/task_rules.py -->
### `python/winpilot_home/winpilot/agent/task_rules.py`

[打开文件](<../../python/winpilot_home/winpilot/agent/task_rules.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：376 行，15,623 bytes
- SHA-256：`5f6e623507b95d806476c86fc15531ab755cefebf3c6587e5bc29cb9316bd2af`
- 源码事实摘要：Task classification and guard rules for the WinPilot GUI runner.
- 顶层符号/结构：`_looks_like_contact_list`, `_has_explicit_gui_only_marker`, `_is_gui_only_task`, `_is_multi_source_research_task`, `_gui_only_shell_allowed`, `_gui_only_forbids_shell`, `_gui_only_launch_uses_shell`, `_is_coding_task`, `_structured_task_intent`, `_is_communication_task`, `_communication_mode`, `_quoted_payloads`, `_required_visible_payloads`, `_dedupe_targets`，另有 11 项
- 直接静态依赖：`__future__`, `re`, `typing`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/agent/tool_result.py -->
### `python/winpilot_home/winpilot/agent/tool_result.py`

[打开文件](<../../python/winpilot_home/winpilot/agent/tool_result.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：22 行，809 bytes
- SHA-256：`513b3a8c1e334221a92ad70e4ca3555ececd1b8981356fd31601b11e81536259`
- 源码事实摘要：Shared return object for WinPilot agent tools.
- 顶层符号/结构：`ToolResult`
- 直接静态依赖：`__future__`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/agent/tool_schemas.py -->
### `python/winpilot_home/winpilot/agent/tool_schemas.py`

[打开文件](<../../python/winpilot_home/winpilot/agent/tool_schemas.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：1812 行，87,773 bytes
- SHA-256：`164cf3cc941f59a2cef32bb8aa54b20ad4c104c6f2249870671dd00513554c3d`
- 源码事实摘要：OpenAI function-calling schemas for the unified WinPilot Agent.
- 顶层符号/结构：`_batch_region_schema`, `_batch_frame_region_schema`, `_frame_constraint_bounds_schema`, `_image_constraint_bounds_schema`, `_batch_image_region_schema`, `_batch_action_schema`, `_tool`, `_prop`, `_expectable`, `_tool_name`, `_promote_tools`, `_strip_nested_descriptions`, `_compact_tool_descriptions`, `_apply_tool_profile`，另有 9 项
- 直接静态依赖：`__future__`, `copy`, `..action`, `..fast.action_contract`, `..memory`, `..perception`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/agent/tools.py -->
### `python/winpilot_home/winpilot/agent/tools.py`

[打开文件](<../../python/winpilot_home/winpilot/agent/tools.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：1237 行，56,149 bytes
- SHA-256：`dbaa396d2ae1e02337aa41bbf5e8394bd08d4c3dde8f62a027cb7c942a2c715c`
- 源码事实摘要：Agent tool dispatcher and per-run execution state.
- 顶层符号/结构：`ToolSession`, `const:_GA_ROOT`, `const:_MUTATING_TOOLS`, `const:_ACTION_SCREEN_DELTA_TOOLS`, `const:_CONTROLLER_COMPOSITE_TOOLS`
- 直接静态依赖：`__future__`, `json`, `threading`, `time`, `typing`, `win32gui`, `win32process`, `..action`, `..config`, `..fast.vision`, `..perception`, `..perception.model`, `..perception.app_aliases`, `..utils.log`，另有 17 项
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/agent/window_tools.py -->
### `python/winpilot_home/winpilot/agent/window_tools.py`

[打开文件](<../../python/winpilot_home/winpilot/agent/window_tools.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：267 行，12,070 bytes
- SHA-256：`7ab0ae7cd6c021e54279ff319f2a8791871481c2c80fc5006a5949bf5e05345a`
- 源码事实摘要：Window listing, scanning, and focus tool handlers for ToolSession.
- 顶层符号/结构：`WindowToolMixin`
- 直接静态依赖：`__future__`, `time`, `win32con`, `win32gui`, `..action`, `..config`, `..perception`, `.tool_result`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/agent/working_set_tools.py -->
### `python/winpilot_home/winpilot/agent/working_set_tools.py`

[打开文件](<../../python/winpilot_home/winpilot/agent/working_set_tools.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：121 行，5,523 bytes
- SHA-256：`670a94685a19fa4fa7d877032c45f1606f2fc23188a693f5d6d3ac4a670799a2`
- 源码事实摘要：Per-run structured fact collection for cross-application workflows.
- 顶层符号/结构：`WorkingSetToolMixin`
- 直接静态依赖：`__future__`, `json`, `re`, `..utils.log`, `.tool_result`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/automation_ir.py -->
### `python/winpilot_home/winpilot/automation_ir.py`

[打开文件](<../../python/winpilot_home/winpilot/automation_ir.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：375 行，14,086 bytes
- SHA-256：`fb73d0e611977288239be6f4ca9afee54b3d47a58ee168c3981049b3754208fe`
- 源码事实摘要：Automation IR v1: canonical handoff for replay, FlowScript, and skills.
- 顶层符号/结构：`_clone`, `_nonempty_str`, `_created_at`, `_target_window_from_wps`, `_walk_steps`, `_default_safety`, `automation_ir_from_wps`, `wps_from_automation_ir`, `validate_automation_ir`, `automation_runtime_kind`, `automation_projection_fingerprint`, `_legacy_projection_fingerprint`, `arp_graph_from_automation_ir`, `validate_automation_runtime`，另有 3 项
- 直接静态依赖：`__future__`, `copy`, `hashlib`, `json`, `time`, `uuid`, `typing`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/bin/ES-LICENSE.txt -->
### `python/winpilot_home/winpilot/bin/ES-LICENSE.txt`

[打开文件](<../../python/winpilot_home/winpilot/bin/ES-LICENSE.txt>)

- 分类/语言：`winpilot-product` / Text
- 规模：24 行，1,162 bytes
- SHA-256：`4a6d27e4dcd8d7ed890ed16a61d2d18f74bc460a4e55d99bb22831d3580b3ee9`
- 源码事实摘要：ES - Everything Command Line Interface
- 顶层符号/结构：—
- 直接静态依赖：—
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/bin/es.exe -->
### `python/winpilot_home/winpilot/bin/es.exe`

[打开文件](<../../python/winpilot_home/winpilot/bin/es.exe>)

- 分类/语言：`asset-or-binary` / Binary or extensionless project file
- 规模：binary 行，154,736 bytes
- SHA-256：`9a9b851f9da14a29626126d9b5f8ef71b569b3cf7e3e70bfbf57f4f00a9b9383`
- 源码事实摘要：项目使用的 Binary or extensionless project file 资产。
- 顶层符号/结构：—
- 直接静态依赖：—
- API/IPC 入口：—
- 解析说明：binary

<!-- PROJECT-FILE: python/winpilot_home/winpilot/config.py -->
### `python/winpilot_home/winpilot/config.py`

[打开文件](<../../python/winpilot_home/winpilot/config.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：272 行，11,044 bytes
- SHA-256：`3b144ffa147755fc308cb0827a7b17f6a572badda07246cb59ef4d29e1e9bf2f`
- 源码事实摘要：WinPilot configuration: load/save config.json + runtime toggle state.
- 顶层符号/结构：`_merge`, `_canonicalize_live_agent_config`, `Config`, `const:ROOT_DIR`, `const:PROJECT_ROOT`, `const:CONFIG_PATH`, `const:TRACES_DIR`, `const:SCRIPTS_DIR`, `const:TEMPLATES_IMG_DIR`, `const:BIN_DIR`, `const:MEMORY_PATH`
- 直接静态依赖：`__future__`, `json`, `threading`, `copy`, `pathlib`, `typing`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/eval/__init__.py -->
### `python/winpilot_home/winpilot/eval/__init__.py`

[打开文件](<../../python/winpilot_home/winpilot/eval/__init__.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：1 行，48 bytes
- SHA-256：`710d232149575061c4a7ed6cee73ed4fdfcdc3ed483a06d21af5e39d43cdb57f`
- 源码事实摘要：WinPilot agent-level evaluation harness.
- 顶层符号/结构：—
- 直接静态依赖：—
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/eval/__main__.py -->
### `python/winpilot_home/winpilot/eval/__main__.py`

[打开文件](<../../python/winpilot_home/winpilot/eval/__main__.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：33 行，942 bytes
- SHA-256：`47639aae231a81e3c6fd4b4288c19785e59d3498e03b315e7aacbcb3435eea7d`
- 源码事实摘要：CLI: python -m winpilot.eval [smoke|full] [--tasks id,id] [--save-baseline]
- 顶层符号/结构：`main`
- 直接静态依赖：`__future__`, `sys`, `.`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/eval/baseline.json -->
### `python/winpilot_home/winpilot/eval/baseline.json`

[打开文件](<../../python/winpilot_home/winpilot/eval/baseline.json>)

- 分类/语言：`configuration` / JSON
- 规模：170 行，4,021 bytes
- SHA-256：`d1053d23800fab06d5b96c3fe94d663831436aa772e5e72c70f7ecd06752d078`
- 源码事实摘要：配置 baseline 对应的构建、运行、依赖或测试行为。
- 顶层符号/结构：`key:total`, `key:passed`, `key:success_rate`, `key:avg_steps`, `key:total_cost_cny`, `key:total_elapsed_s`, `key:results`, `key:calc_compute,file_write,git_clone,launch_calc,notepad_write,shell_file,timer`
- 直接静态依赖：—
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/eval/runner.py -->
### `python/winpilot_home/winpilot/eval/runner.py`

[打开文件](<../../python/winpilot_home/winpilot/eval/runner.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：170 行，6,606 bytes
- SHA-256：`a9be604efcc5db9268f01be664320085184eca0778afc898aa0e4fec5fdd3d77`
- 源码事实摘要：Eval runner: run tasks against the live in-process agent, score with
- 顶层符号/结构：`_run_once`, `run_task`, `_scorecard`, `_fmt_table`, `_signature`, `_load_baselines`, `_compare_baseline`, `run`, `const:RESULTS_DIR`, `const:BASELINE_PATH`
- 直接静态依赖：`__future__`, `json`, `time`, `pathlib`, `typing`, `..agent.loop`, `..config`, `..recorder`, `.tasks`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/eval/summary.py -->
### `python/winpilot_home/winpilot/eval/summary.py`

[打开文件](<../../python/winpilot_home/winpilot/eval/summary.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：850 行，35,540 bytes
- SHA-256：`b855d33ef171f755324f61c684c0bd372f50b098b129ab1c1fa07203f97cb5fa`
- 源码事实摘要：Trace/eval summarization utilities.
- 顶层符号/结构：`_read_jsonl`, `_percentile`, `_median_ms`, `_latency_summary`, `_perception_dim_called`, `_screen_watch_status`, `_low_latency_metrics`, `_summarize_fast_gui`, `summarize_events`, `summarize_trace`, `summarize_eval_report`, `list_eval_reports`, `aggregate_reports`, `const:EVALS_DIR`，另有 1 项
- 直接静态依赖：`__future__`, `json`, `math`, `statistics`, `collections`, `pathlib`, `typing`, `..config`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/eval/tasks.py -->
### `python/winpilot_home/winpilot/eval/tasks.py`

[打开文件](<../../python/winpilot_home/winpilot/eval/tasks.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：146 行，5,172 bytes
- SHA-256：`c5d0bb9921aa21a91b510e677f0789791275042a375767b4c36e415a6ce727e7`
- 源码事实摘要：Eval task suite: real tasks with PROGRAMMATIC ground-truth checks.
- 顶层符号/结构：`_noop`, `_rm`, `_kill`, `EvalTask`, `_check_dir`, `_check_txt`, `_check_note`, `_check_calc`, `_check_timer`, `_check_clone`, `_check_compute`, `suite`, `by_ids`, `const:DESKTOP`，另有 6 项
- 直接静态依赖：`__future__`, `subprocess`, `time`, `dataclasses`, `pathlib`, `typing`, `..perception`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/fast/__init__.py -->
### `python/winpilot_home/winpilot/fast/__init__.py`

[打开文件](<../../python/winpilot_home/winpilot/fast/__init__.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：7 行，291 bytes
- SHA-256：`a4552b7448710501eff944fd5450fb952ab3a777259eed1c2e18f692cea6dbe3`
- 源码事实摘要：Low-latency GUI control primitives.
- 顶层符号/结构：—
- 直接静态依赖：—
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/fast/action_contract.py -->
### `python/winpilot_home/winpilot/fast/action_contract.py`

[打开文件](<../../python/winpilot_home/winpilot/fast/action_contract.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：540 行，19,450 bytes
- SHA-256：`b967ba67cfe84fda9712c75c94aaed6da2b85fabea2887a907d8a3b1c0bfcfad`
- 源码事实摘要：Shared contract for actions accepted by the low-latency controller.
- 顶层符号/结构：`ActionTarget`, `ActionPolicy`, `ActionEnvelope`, `AtomicAction`, `_region_dict`, `action_reference_system`, `action_source_reference_system`, `build_action_envelope`, `supported_actions`, `_action_name`, `parse_atomic_action`, `canonicalize_batch_actions`, `iter_atomic_actions`, `batch_action_name`，另有 8 项
- 直接静态依赖：`__future__`, `dataclasses`, `typing`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/fast/controller.py -->
### `python/winpilot_home/winpilot/fast/controller.py`

[打开文件](<../../python/winpilot_home/winpilot/fast/controller.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：3754 行，169,076 bytes
- SHA-256：`596b6e606aa8842f874805bdef49eaa866454ea2b8d179c5f8efa38622d99474`
- 源码事实摘要：Fast GUI controller: execute short grounded action batches.
- 顶层符号/结构：`_now_ms`, `_same_window_process`, `_json_dict`, `_is_json_object`, `_grounded_query_matches`, `_truthy`, `_norm_text`, `_short_control_terms`, `_dialog_context_candidate`, `_needs_replace_guard`, `_is_input_element`, `_detect_prefixed_glob_text`, `_input_presence_error`, `_should_move_to_end_before_typing`，另有 13 项
- 直接静态依赖：`__future__`, `json`, `re`, `time`, `unicodedata`, `typing`, `cv2`, `numpy`, `win32gui`, `win32process`, `..agent.tool_result`, `..action`, `..action.desktop_queue`, `..config`，另有 11 项
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/fast/element_cache.py -->
### `python/winpilot_home/winpilot/fast/element_cache.py`

[打开文件](<../../python/winpilot_home/winpilot/fast/element_cache.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：448 行，16,882 bytes
- SHA-256：`f014809b70aa3ea0e600e617495deafe1aaab94ba6dfd04f63f35dd997c0a164`
- 源码事实摘要：Visual anchor cache and locator for fast repeated GUI actions.
- 顶层符号/结构：`ElementCacheEntry`, `LocateResult`, `_region_match`, `_terms`, `_looks_like_browser_address_text`, `_role_family`, `_role_penalty`, `_score`, `_rel_center`, `_rel_distance`, `_text_compatible`, `_role_compatible`, `_candidate_trace`, `_same_physical_target`，另有 5 项
- 直接静态依赖：`__future__`, `math`, `re`, `time`, `dataclasses`, `typing`, `..perception.fusion`, `..perception.model`, `..utils.screenshot`, `.types`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/fast/input_locator.py -->
### `python/winpilot_home/winpilot/fast/input_locator.py`

[打开文件](<../../python/winpilot_home/winpilot/fast/input_locator.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：668 行，26,660 bytes
- SHA-256：`9d3962ef1ca71bcbfd542a8085014390c9bba95ec3d222e1e217afc031797e0f`
- 源码事实摘要：Generic input-field locator for UIA-off and custom-rendered surfaces.
- 顶层符号/结构：`InputCandidate`, `InputLocateResult`, `_norm`, `_role_has`, `_is_input_role`, `_is_button_role`, `_is_compact_property_editor`, `_is_caret_like`, `_looks_like_field_label`, `_looks_search_semantic`, `_wants_search`, `_wants_browser_location`, `_wants_filename`, `_looks_like_filename_value`，另有 19 项
- 直接静态依赖：`__future__`, `re`, `dataclasses`, `typing`, `..perception.fusion`, `..perception.model`, `..utils.screenshot`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/fast/input_mode.py -->
### `python/winpilot_home/winpilot/fast/input_mode.py`

[打开文件](<../../python/winpilot_home/winpilot/fast/input_mode.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：200 行，7,400 bytes
- SHA-256：`dbe39abd4177aec509d1d8f5cfb0fa1d1597a930279fc3fed6254589bcf7cec7`
- 源码事实摘要：Application-neutral input-mode inspection for deterministic GUI actions.
- 顶层符号/结构：`_normalise`, `InputModeSpec`, `InputModeRegistry`, `_matches_alias`, `_summary`, `inspect_input_mode`, `const:_ACTIONABLE_ROLE_TERMS`, `const:_ACTIVE_STATES`, `const:_ACTIVE_TEXT_MARKERS`, `const:_NON_MODE_SUFFIXES`
- 直接静态依赖：`__future__`, `dataclasses`, `re`, `typing`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/fast/legacy_session_actions.py -->
### `python/winpilot_home/winpilot/fast/legacy_session_actions.py`

[打开文件](<../../python/winpilot_home/winpilot/fast/legacy_session_actions.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：43 行，1,507 bytes
- SHA-256：`a21b7dc94d38dce56d74be426bde5a121aeacc519e3db03f6de2276023fc2991`
- 源码事实摘要：Legacy ToolSession reflection retained only for FastGuiController migration.
- 顶层符号/结构：`LegacyToolSessionActionAdapter`, `const:LEGACY_SESSION_ACTIONS`
- 直接静态依赖：`__future__`, `time`, `typing`, `..agent.tool_result`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/fast/perception_daemon.py -->
### `python/winpilot_home/winpilot/fast/perception_daemon.py`

[打开文件](<../../python/winpilot_home/winpilot/fast/perception_daemon.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：619 行，24,478 bytes
- SHA-256：`62f5760aea13dba569a7a2c7ca4608491feccdcb8fc769e5621c98de95925023`
- 源码事实摘要：Shared change-triggered perception service keyed by target hwnd.
- 顶层符号/结构：`_now_ms`, `_PendingChange`, `_IncrementalRecord`, `_Entry`, `PerceptionLease`, `PerceptionDaemon`, `const:PERCEPTION_DAEMON`
- 直接静态依赖：`__future__`, `copy`, `threading`, `time`, `collections`, `collections.abc`, `dataclasses`, `typing`, `..config`, `..perception.fusion`, `..perception.incremental`, `..perception.model`, `..utils.log`, `..utils.screenshot`，另有 3 项
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/fast/subgoal.py -->
### `python/winpilot_home/winpilot/fast/subgoal.py`

[打开文件](<../../python/winpilot_home/winpilot/fast/subgoal.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：3584 行，149,586 bytes
- SHA-256：`d20e602bbf6abacf1b581b2f6018b18f3d30fcdfbdc3129e2b6efa3d5326e6de`
- 源码事实摘要：Controller-first subgoal executor.
- 顶层符号/结构：`SubgoalRequest`, `_as_dict`, `_tool_result_data`, `_as_text_list`, `_unique`, `_compact_key`, `_looks_like_file_path`, `_strip_stale_status_lines`, `_file_dialog_enter_submit`, `_file_dialog_navigation_clicks`, `_app_launch_target`, `_initial_task_app_target`, `_click_label_to_printable_key`, `_click_label_to_hotkey`，另有 76 项
- 直接静态依赖：`__future__`, `json`, `math`, `re`, `time`, `unicodedata`, `dataclasses`, `typing`, `numpy`, `win32gui`, `..action.verify`, `..config`, `..agent.task_rules`, `..agent.tool_result`，另有 5 项
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/fast/types.py -->
### `python/winpilot_home/winpilot/fast/types.py`

[打开文件](<../../python/winpilot_home/winpilot/fast/types.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：96 行，3,523 bytes
- SHA-256：`19ea1c4a359708f13610fbea7e79222a64f610a0608c95acf821ca4f8b9713f1`
- 源码事实摘要：Shared dataclasses for the low-latency GUI controller.
- 顶层符号/结构：`ScreenDelta`, `VisualAnchor`, `BatchMetrics`
- 直接静态依赖：`__future__`, `dataclasses`, `typing`, `..utils.screenshot`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/fast/vision.py -->
### `python/winpilot_home/winpilot/fast/vision.py`

[打开文件](<../../python/winpilot_home/winpilot/fast/vision.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：111 行，4,266 bytes
- SHA-256：`d1a5592f07e4dafc786525c33650ed1a345821c23f70a97c2b452dca535d5253`
- 源码事实摘要：Cheap screen-delta and ROI helpers for the fast GUI loop.
- 顶层符号/结构：`roi_point_to_screen`, `screen_point_to_roi`, `inflate_region`, `_merge_regions`, `changed_regions`, `capture_delta`
- 直接静态依赖：`__future__`, `time`, `cv2`, `numpy`, `..utils.screenshot`, `.types`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/fast/visual_servo.py -->
### `python/winpilot_home/winpilot/fast/visual_servo.py`

[打开文件](<../../python/winpilot_home/winpilot/fast/visual_servo.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：475 行，18,155 bytes
- SHA-256：`b8ed54ed1aaf4c487e3752d45be4cca5662c3619b38acf6bc742b7ffbdb12a13`
- 源码事实摘要：Low-latency visual servo tracking for moving GUI targets.
- 顶层符号/结构：`ServoConfig`, `_now_ms`, `_norm_text`, `_text_matches`, `_element_region`, `_search_region`, `_template_estimate`, `_ocr_estimate`, `_estimate`, `track_visual_target`
- 直接静态依赖：`__future__`, `time`, `dataclasses`, `difflib`, `typing`, `cv2`, `numpy`, `..perception.model`, `..utils.screenshot`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/fast/watcher.py -->
### `python/winpilot_home/winpilot/fast/watcher.py`

[打开文件](<../../python/winpilot_home/winpilot/fast/watcher.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：544 行，21,754 bytes
- SHA-256：`7b545ddd3148a33c479b2d1563342021d446671b0f211e503bfc36f940edf0fb`
- 源码事实摘要：Low-cost screen watcher and change bulletin board.
- 顶层符号/结构：`ScreenBulletin`, `_now_ms`, `ScreenWatcher`
- 直接静态依赖：`__future__`, `threading`, `time`, `dataclasses`, `typing`, `numpy`, `..utils.screenshot`, `..utils.log`, `.types`, `.vision`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/integrations/__init__.py -->
### `python/winpilot_home/winpilot/integrations/__init__.py`

[打开文件](<../../python/winpilot_home/winpilot/integrations/__init__.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：2 行，59 bytes
- SHA-256：`cabee08d0616eba8ddfbb5cbca369facf66842d0ae637cccb3f00c131f78b196`
- 源码事实摘要：External control-plane integrations for WinPilot.
- 顶层符号/结构：—
- 直接静态依赖：—
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/integrations/chatgpt_mcp.py -->
### `python/winpilot_home/winpilot/integrations/chatgpt_mcp.py`

[打开文件](<../../python/winpilot_home/winpilot/integrations/chatgpt_mcp.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：1667 行，73,101 bytes
- SHA-256：`fc0ec7899112bdc48095a22a55b441f5b47ba8c0faab015dffcb1c5a4a3384b9`
- 源码事实摘要：MCP bridge that lets a logged-in ChatGPT/Codex client drive WinPilot.
- 顶层符号/结构：`_frame_invalidating_actions`, `_region_payload`, `_region_contains`, `ChatGPTBridgeHooks`, `_json_object`, `_compact_element`, `_compact_screen_delta`, `_compact_controller_item`, `_compact_controller_payload`, `ChatGPTDesktopBridge`, `build_chatgpt_mcp`
- 直接静态依赖：`__future__`, `base64`, `hashlib`, `json`, `threading`, `time`, `uuid`, `collections.abc`, `dataclasses`, `typing`, `win32gui`, `..action`, `..config`, `..orchestrator.contracts`，另有 5 项
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/integrations/connector_gateway.py -->
### `python/winpilot_home/winpilot/integrations/connector_gateway.py`

[打开文件](<../../python/winpilot_home/winpilot/integrations/connector_gateway.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：312 行，11,900 bytes
- SHA-256：`dcec8b8147214bfe671b0985ad59b9292fbe931854577355ccb27398de376b9f`
- 源码事实摘要：Bounded, task-agnostic access to explicitly configured MCP connectors.
- 顶层符号/结构：`ConnectorGatewayError`, `_dump`, `_name`, `_read_only`, `_descriptor`, `_decode_ref`, `_unique_object`, `_arguments`, `_bounded`, `_response`, `ConnectorGateway`, `const:TOOL_REF_PREFIX`, `const:RECEIPT_FORMAT`, `const:MAX_ARGUMENT_BYTES`，另有 5 项
- 直接静态依赖：`__future__`, `base64`, `hashlib`, `json`, `re`, `time`, `uuid`, `typing`, `.`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/integrations/mcp_client.py -->
### `python/winpilot_home/winpilot/integrations/mcp_client.py`

[打开文件](<../../python/winpilot_home/winpilot/integrations/mcp_client.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：416 行，14,741 bytes
- SHA-256：`972f8f59f26b9edf817ce69393a16dc1986512c99237a774c4ec390810872e7b`
- 源码事实摘要：Optional outbound MCP capability discovery for the unified agent.
- 顶层符号/结构：`_config_value`, `_configured_paths`, `configured_servers`, `_server_cache_key`, `_resolved_command`, `_connection`, `_tool_descriptor`, `_list_server_tools`, `_bounded`, `_run`, `discover`, `_call_server_tool`, `call`, `const:_CACHE_TTL_S`，另有 1 项
- 直接静态依赖：`__future__`, `asyncio`, `hashlib`, `json`, `os`, `re`, `shutil`, `threading`, `time`, `contextlib`, `pathlib`, `typing`, `..config`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/memory/__init__.py -->
### `python/winpilot_home/winpilot/memory/__init__.py`

[打开文件](<../../python/winpilot_home/winpilot/memory/__init__.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：0 行，0 bytes
- SHA-256：`e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`
- 源码事实摘要：Python 包 python.winpilot_home.winpilot.memory 的初始化与公开边界。
- 顶层符号/结构：—
- 直接静态依赖：—
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/memory/applicability.py -->
### `python/winpilot_home/winpilot/memory/applicability.py`

[打开文件](<../../python/winpilot_home/winpilot/memory/applicability.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：223 行，8,087 bytes
- SHA-256：`fbc1aced4793c1e8d8350be86a0822aea43442f8d3500942b0fda484dd0a4ec7`
- 源码事实摘要：Typed applicability contracts for reusable action memory.
- 顶层符号/结构：`_mapping`, `_value`, `_normal`, `_bigrams`, `task_intent_matches`, `_same_context`, `_clean_parameters`, `build_applicability`, `runtime_context`, `ApplicabilityDecision`, `evaluate`, `const:_IGNORED_PARAMETERS`
- 直接静态依赖：`__future__`, `re`, `dataclasses`, `typing`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/memory/compiler.py -->
### `python/winpilot_home/winpilot/memory/compiler.py`

[打开文件](<../../python/winpilot_home/winpilot/memory/compiler.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：241 行，9,972 bytes
- SHA-256：`deaaa53735d73e4bcef6897928439ff4116f24538ec31bfe31ff34d973f960bc`
- 源码事实摘要：Compile completed agent runs into Memory Graph nodes.
- 顶层符号/结构：`_enabled`, `_clean_steps`, `_conditions`, `_evidence`, `_verified_run`, `ingest_run`, `const:_JUNK_STEP`, `const:_OLD_FORMAT`, `const:_COORD_HEAVY`
- 直接静态依赖：`__future__`, `re`, `typing`, `..`, `..utils.log`, `.applicability`, `.experience`, `.migration`, `.retriever`, `.store`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/memory/experience.py -->
### `python/winpilot_home/winpilot/memory/experience.py`

[打开文件](<../../python/winpilot_home/winpilot/memory/experience.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：890 行，32,753 bytes
- SHA-256：`402318a2219a6b1596d047f5ef9804316e14156d6c9b4efea33a4238546577ff`
- 源码事实摘要：Compatibility layer over the SQLite Memory Graph.
- 顶层符号/结构：`_effective_db_path`, `_store`, `_string_list`, `_default_conditions`, `_default_evidence`, `_clean_steps`, `_script`, `_recipe_from_node`, `_node_from_recipe_id`, `enabled`, `memory_store_present`, `_script_path`, `script_sha256`, `_script_integrity_ok`，另有 27 项
- 直接静态依赖：`__future__`, `hashlib`, `json`, `re`, `time`, `pathlib`, `typing`, `..`, `.applicability`, `.`, `.migration`, `.retriever`, `.runtime`, `.store`，另有 1 项
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/memory/feedback.py -->
### `python/winpilot_home/winpilot/memory/feedback.py`

[打开文件](<../../python/winpilot_home/winpilot/memory/feedback.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：97 行，3,255 bytes
- SHA-256：`5a50b02622d67fb6645d2d813f4cf5447d96aafa1866b110c1ce69f5372166b4`
- 源码事实摘要：Feedback rules for Memory Graph.
- 顶层符号/结构：`_payload_scores`, `apply_feedback_to_ids`, `apply_run_feedback`, `const:EVICT_MIN_USES`
- 直接静态依赖：`__future__`, `typing`, `.store`, `.types`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/memory/migration.py -->
### `python/winpilot_home/winpilot/memory/migration.py`

[打开文件](<../../python/winpilot_home/winpilot/memory/migration.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：172 行，6,422 bytes
- SHA-256：`c2af05d59fbf325911aed8fcfc887b71207ce271afacc14713134bfd754e0aff`
- 源码事实摘要：Migration from legacy experience_memory.json into Memory Graph.
- 顶层符号/结构：`_as_list`, `_default_conditions`, `_default_evidence`, `_kind_for`, `_anchors`, `migrate_json_if_needed`, `const:MIGRATION_KEY`
- 直接静态依赖：`__future__`, `json`, `time`, `pathlib`, `typing`, `..`, `..utils.log`, `.store`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/memory/packer.py -->
### `python/winpilot_home/winpilot/memory/packer.py`

[打开文件](<../../python/winpilot_home/winpilot/memory/packer.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：162 行，6,450 bytes
- SHA-256：`8e63da178173e82e9585d36f84eb1e21e21dff904d0be56d7f600bf93ba06047`
- 源码事实摘要：Memory Pack rendering for LLM context.
- 顶层符号/结构：`_steps`, `_script`, `_bullets`, `_render_steps`, `_warning_nodes`, `pack`, `const:MAX_PACK_CHARS`, `const:MAX_STEPS`
- 直接静态依赖：`__future__`, `typing`, `.store`, `.types`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/memory/replay_anchors.py -->
### `python/winpilot_home/winpilot/memory/replay_anchors.py`

[打开文件](<../../python/winpilot_home/winpilot/memory/replay_anchors.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：132 行，4,628 bytes
- SHA-256：`5b292a99059abca248b099aecfb0f44358e22f9e7a6fab7aaed060ba878b5db7`
- 源码事实摘要：Persistent, local visual anchors for deterministic replay.
- 顶层符号/结构：`script_profile`, `template_name`, `_meta_key`, `_number_list`, `_normalize_anchor`, `load_profile`, `save_profile`, `const:_VERSION`, `const:_META_PREFIX`, `const:_MAX_ANCHORS`
- 直接静态依赖：`__future__`, `hashlib`, `json`, `time`, `pathlib`, `typing`, `.`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/memory/retriever.py -->
### `python/winpilot_home/winpilot/memory/retriever.py`

[打开文件](<../../python/winpilot_home/winpilot/memory/retriever.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：261 行，8,783 bytes
- SHA-256：`214598c552f9a5196029eac095629ef603fb27b4fea1324212017f180602b38c`
- 源码事实摘要：Recall and graph expansion for Memory Graph.
- 顶层符号/结构：`norm`, `is_volatile_state_task`, `words_for`, `bigrams`, `strong_match`, `parse_task_frame`, `_is_active`, `passes_hard_filters`, `_score_node`, `recall`, `expand_graph`, `top_reusable_skill`, `const:_VOLATILE_CN`, `const:_STATE_CN`，另有 1 项
- 直接静态依赖：`__future__`, `re`, `time`, `typing`, `.applicability`, `.store`, `.types`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/memory/runtime.py -->
### `python/winpilot_home/winpilot/memory/runtime.py`

[打开文件](<../../python/winpilot_home/winpilot/memory/runtime.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：45 行，1,523 bytes
- SHA-256：`ae50cc678318cba9def9cb9b910d15b2fc60624766fe079aa93804d106a7e5e1`
- 源码事实摘要：Runtime entry point for preparing Memory Packs.
- 顶层符号/结构：`prepare_run_memory`
- 直接静态依赖：`__future__`, `typing`, `..`, `.migration`, `.packer`, `.retriever`, `.store`, `.types`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/memory/safety.py -->
### `python/winpilot_home/winpilot/memory/safety.py`

[打开文件](<../../python/winpilot_home/winpilot/memory/safety.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：62 行，2,160 bytes
- SHA-256：`70a76b36caa55d1be7c6a30aad981c2769192b68929b3c1b3b4876c33cffe668`
- 源码事实摘要：Safety helpers for memory persistence.
- 顶层符号/结构：`_known_secret_values`, `sanitize_memory_text`, `sanitize_payload`, `const:_SECRET_ASSIGNMENT`, `const:_OPENAI_STYLE_KEY`, `const:_BEARER_TOKEN`
- 直接静态依赖：`__future__`, `re`, `typing`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/memory/store.py -->
### `python/winpilot_home/winpilot/memory/store.py`

[打开文件](<../../python/winpilot_home/winpilot/memory/store.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：874 行，34,297 bytes
- SHA-256：`5f738710c34fd8dbcbc6049dde3c129b873b9bfbbb3aa5850b51edf5efe7e6d4`
- 源码事实摘要：Thread-safe SQLite store for WinPilot Memory Graph.
- 顶层符号/结构：`_now`, `new_id`, `_json_loads`, `_json_dumps`, `_clean_list`, `MemoryStore`
- 直接静态依赖：`__future__`, `json`, `sqlite3`, `threading`, `time`, `uuid`, `pathlib`, `typing`, `..`, `..utils.log`, `.safety`, `.types`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/memory/types.py -->
### `python/winpilot_home/winpilot/memory/types.py`

[打开文件](<../../python/winpilot_home/winpilot/memory/types.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：67 行，1,555 bytes
- SHA-256：`62fa886d84a3c625baadaf3ba64e8ba20a275787b5b74f9d0fa772f6608b3c5a`
- 源码事实摘要：Shared types for the WinPilot memory graph.
- 顶层符号/结构：`TaskFrame`, `MemoryCandidate`, `MemoryPack`, `const:NODE_KINDS`, `const:EDGE_KINDS`, `const:ACTIVE_STATUSES`, `const:ACTION_KINDS`, `const:FACTUAL_KINDS`, `const:PACKABLE_KINDS`
- 直接静态依赖：`__future__`, `dataclasses`, `typing`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/model_adapter.py -->
### `python/winpilot_home/winpilot/model_adapter.py`

[打开文件](<../../python/winpilot_home/winpilot/model_adapter.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：522 行，18,017 bytes
- SHA-256：`45ed6cef16e510a15be7f2ee6c01d95ba9f23d97dd628d984f7be94c0c9aa143`
- 源码事实摘要：Thin model/provider adapter for WinPilot's OpenAI-compatible chat path.
- 顶层符号/结构：`_lower`, `_get`, `_nested`, `_to_int`, `_to_float`, `_first_int`, `_api_dict`, `infer_provider`, `native_provider_protocol`, `model_supports_vision`, `ModelProfile`, `model_profile_from_config`, `request_extra_body`, `_contains_visual_input`，另有 16 项
- 直接静态依赖：`__future__`, `re`, `dataclasses`, `types`, `typing`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/modeling/__init__.py -->
### `python/winpilot_home/winpilot/modeling/__init__.py`

[打开文件](<../../python/winpilot_home/winpilot/modeling/__init__.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：39 行，1,003 bytes
- SHA-256：`d7760bf3cc0f1e55e88b01e4b98d4086a725af8fce00134748b3c1533b6916fc`
- 源码事实摘要：Single owner for provider transport calls and request retry semantics.
- 顶层符号/结构：—
- 直接静态依赖：`.contracts`, `.openai_transport`, `.provider_protocols`, `.runtime`, `.chat`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/modeling/chat.py -->
### `python/winpilot_home/winpilot/modeling/chat.py`

[打开文件](<../../python/winpilot_home/winpilot/modeling/chat.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：68 行，2,390 bytes
- SHA-256：`b7588436968cf79a96db1c7291ae5545217389cc95b623ff2f14ee786d9b5122`
- 源码事实摘要：One OpenAI-compatible chat-completion transport boundary.
- 顶层符号/结构：`invoke_chat_completion`
- 直接静态依赖：`__future__`, `threading`, `inspect`, `typing`, `.contracts`, `.runtime`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/modeling/contracts.py -->
### `python/winpilot_home/winpilot/modeling/contracts.py`

[打开文件](<../../python/winpilot_home/winpilot/modeling/contracts.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：315 行，11,906 bytes
- SHA-256：`cd52b803267d517a69810391cfdb6c769cb5dcca4f5f175e85264b56672bded3`
- 源码事实摘要：Versioned contracts for model requests, retries, polls, and redecisions.
- 顶层符号/结构：`InvalidModelRequest`, `RetryKind`, `FollowupKind`, `_freeze_json`, `_thaw_json`, `_update_hash`, `_fingerprint`, `_text`, `ModelRetryPolicy`, `ModelRequestEnvelope`, `ModelAttempt`, `classify_followup`, `const:MODEL_REQUEST_FORMAT`, `const:_ROLE_PATTERN`
- 直接静态依赖：`__future__`, `hashlib`, `math`, `re`, `dataclasses`, `enum`, `typing`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/modeling/openai_transport.py -->
### `python/winpilot_home/winpilot/modeling/openai_transport.py`

[打开文件](<../../python/winpilot_home/winpilot/modeling/openai_transport.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：91 行，3,143 bytes
- SHA-256：`9ee5f0f9d21e9dfe2d0ce9c353d5fbc7a69ca77c72909229acd24c454b45a625`
- 源码事实摘要：OpenAI-compatible transport construction with SDK retries disabled.
- 顶层符号/结构：`_proxy_env`, `_proxy_from_config`, `create_openai_client`, `const:_DEFAULT_USER_AGENT`
- 直接静态依赖：`__future__`, `os`, `typing`, `logging`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/modeling/provider_protocols.py -->
### `python/winpilot_home/winpilot/modeling/provider_protocols.py`

[打开文件](<../../python/winpilot_home/winpilot/modeling/provider_protocols.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：1218 行，48,498 bytes
- SHA-256：`9698294a0481d4ceef51ec04ba35b77b2f96367a1491581c28fee9866e8c4bf3`
- 源码事实摘要：Clean-room wire adapters for non-Chat-Completions model APIs.
- 顶层符号/结构：`ProviderProtocolError`, `ProviderHTTPError`, `RequestsJSONTransport`, `normalize_provider_family`, `provider_endpoint`, `provider_headers`, `_message_list`, `_content_parts`, `_data_image`, `_call_parts`, `_canonical_tools`, `_tool_result_text`, `_chosen_function`, `_responses_request`，另有 20 项
- 直接静态依赖：`__future__`, `base64`, `json`, `math`, `threading`, `uuid`, `collections`, `collections.abc`, `copy`, `typing`, `urllib.parse`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/modeling/runtime.py -->
### `python/winpilot_home/winpilot/modeling/runtime.py`

[打开文件](<../../python/winpilot_home/winpilot/modeling/runtime.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：325 行，12,144 bytes
- SHA-256：`5a2f9f296bf3de622cf2b38434da6d331ca9def7958e83fa016b0740d960f331`
- 源码事实摘要：The sole transport retry loop for model requests.
- 顶层符号/结构：`ModelCallOutcome`, `ModelCallError`, `_status_code`, `_is_transient_multimodal_rejection`, `_retryable_transport_error`, `_notify_attempt`, `_ModelResponseAbandoned`, `_send_before_deadline`, `ModelCallRuntime`, `const:_TRANSIENT_MULTIMODAL_REJECTION_MESSAGES`
- 直接静态依赖：`__future__`, `math`, `threading`, `time`, `collections.abc`, `dataclasses`, `typing`, `.contracts`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/orchestrator/__init__.py -->
### `python/winpilot_home/winpilot/orchestrator/__init__.py`

[打开文件](<../../python/winpilot_home/winpilot/orchestrator/__init__.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：15 行，364 bytes
- SHA-256：`ba7f79fdc27a667559a7c657c823eabfcb0ed946a0bf7dec7b70872fc2d1a0f9`
- 源码事实摘要：Unified Agent Runtime Platform (ARP) orchestration primitives.
- 顶层符号/结构：—
- 直接静态依赖：`.contracts`, `.runtime`, `.projection`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/orchestrator/adapters.py -->
### `python/winpilot_home/winpilot/orchestrator/adapters.py`

[打开文件](<../../python/winpilot_home/winpilot/orchestrator/adapters.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：543 行，22,791 bytes
- SHA-256：`76e0a05e87e4038458074f8b27d8382d7d300bdc4f084c5b318048cfe30482c5`
- 源码事实摘要：Adapters from ARP node kinds to the existing WinPilot runtimes.
- 顶层符号/结构：`_publish_anchor_metrics`, `_json_result`, `_agent_handler`, `_batch_handler`, `_walk_replay_tools`, `_replay_step_handler`, `_skill_handler`, `default_handlers`, `finalize_memory_feedback`, `const:_REPLAY_SHELL_TOOLS`, `const:_REPLAY_DESKTOP_TOOLS`, `const:_ACTION_BATCH_SESSION_KEY`
- 直接静态依赖：`__future__`, `json`, `pathlib`, `typing`, `..agent.router`, `..agent.runtime_profile`, `..agent.tools`, `..config`, `..memory`, `..recorder`, `..utils.log`, `.contracts`, `.runtime`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/orchestrator/agent_threads.py -->
### `python/winpilot_home/winpilot/orchestrator/agent_threads.py`

[打开文件](<../../python/winpilot_home/winpilot/orchestrator/agent_threads.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：435 行，16,484 bytes
- SHA-256：`727493f42517d994365a3beb1611b64ba547a07029e5a2736f465220b746c2f9`
- 源码事实摘要：Small server-owned Thread/Turn history for the primary Agent surface.
- 顶层符号/结构：`_Turn`, `_Thread`, `AgentThreadError`, `AgentThreadStore`, `const:_MAX_THREADS`, `const:_MAX_TURNS`, `const:_MAX_HISTORY_MESSAGES`, `const:_MAX_HISTORY_CHARS`, `const:_ACTIVE_STATUSES`, `const:_SUCCESS_STATUSES`, `const:_CHECKPOINT_FORMAT`, `const:_MAX_CHECKPOINT_BYTES`
- 直接静态依赖：`__future__`, `json`, `math`, `os`, `threading`, `time`, `uuid`, `dataclasses`, `pathlib`, `typing`, `..utils.log`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/orchestrator/catalog.py -->
### `python/winpilot_home/winpilot/orchestrator/catalog.py`

[打开文件](<../../python/winpilot_home/winpilot/orchestrator/catalog.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：135 行，5,217 bytes
- SHA-256：`e43d9a2969078edcefe2cf77e373268c54820b5eeca53d0313833077b4280c7a`
- 源码事实摘要：Capability catalog exposed to ARP Studio and external clients.
- 顶层符号/结构：`capability_catalog`
- 直接静态依赖：`__future__`, `.contracts`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/orchestrator/contracts.py -->
### `python/winpilot_home/winpilot/orchestrator/contracts.py`

[打开文件](<../../python/winpilot_home/winpilot/orchestrator/contracts.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：383 行，15,200 bytes
- SHA-256：`e4f698c134e540b61a656a21fc31c8d7171224d325d2dae9dc746db5ac0eb690`
- 源码事实摘要：Versioned contracts shared by the Agent, ARP runtime, and cockpit UI.
- 顶层符号/结构：`ArpNodeKind`, `ArpRunStatus`, `ArpRuntimeVersion`, `ArpRetryPolicy`, `ArpNode`, `ArpEdge`, `ArpPolicy`, `ArpGraph`, `GraphValidation`, `CapabilitySpec`, `EvidenceRecord`, `EvidenceRequirement`, `ArpRunRequest`, `_validate_node_config`，另有 5 项
- 直接静态依赖：`__future__`, `enum`, `time`, `typing`, `uuid`, `pydantic`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/orchestrator/evidence.py -->
### `python/winpilot_home/winpilot/orchestrator/evidence.py`

[打开文件](<../../python/winpilot_home/winpilot/orchestrator/evidence.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：104 行，3,936 bytes
- SHA-256：`f1de1b363d61cbf4237295cd34b4448345005f2058272b9b0efcdb8d982bc488`
- 源码事实摘要：Typed evidence normalization and deterministic gate evaluation.
- 顶层符号/结构：`normalize_evidence`, `parse_requirement`, `evidence_matches`, `has_verified_evidence`
- 直接静态依赖：`__future__`, `time`, `typing`, `uuid`, `.contracts`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/orchestrator/goal_admission.py -->
### `python/winpilot_home/winpilot/orchestrator/goal_admission.py`

[打开文件](<../../python/winpilot_home/winpilot/orchestrator/goal_admission.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：152 行，4,596 bytes
- SHA-256：`109c90b55ce0af1a2c6332f2ebe798073293276e60a29fb83551282b8b0514c8`
- 源码事实摘要：Server-owned admission contract for one natural-language product goal.
- 顶层符号/结构：`GoalAdmissionError`, `GoalAdmissionUnavailable`, `GoalExecutionRoute`, `GoalRunRequest`, `DesktopLeaseRequest`, `DesktopLeaseFailureRequest`, `GoalAdmissionDecision`, `admit_goal`, `bind_client_execution_context`, `const:GOAL_ADMISSION_FORMAT`, `const:_CLIENT_EXECUTION_CONTEXT_FIELDS`
- 直接静态依赖：`__future__`, `copy`, `dataclasses`, `enum`, `typing`, `pydantic`, `.contracts`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/orchestrator/graph_compiler.py -->
### `python/winpilot_home/winpilot/orchestrator/graph_compiler.py`

[打开文件](<../../python/winpilot_home/winpilot/orchestrator/graph_compiler.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：375 行，16,007 bytes
- SHA-256：`3f919ad20326f04918df9991b129c5f78f42d81c499723770dd76f3926a78060`
- 源码事实摘要：Projection of typed task contracts into executable ARP graphs.
- 顶层符号/结构：`task_contract_to_arp`, `legacy_goal_graph_task`
- 直接静态依赖：`__future__`, `..tasking.contracts`, `.contracts`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/orchestrator/live_agent.py -->
### `python/winpilot_home/winpilot/orchestrator/live_agent.py`

[打开文件](<../../python/winpilot_home/winpilot/orchestrator/live_agent.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：4379 行，155,785 bytes
- SHA-256：`c969e20534dcfdf166b761648ec6f78a4f4eff51bebb5f4526722f6ffcf9125d`
- 源码事实摘要：Product composition root for the minimal live vNext desktop loop.
- 顶层符号/结构：`_Budget`, `_ModelConfig`, `_TargetFacts`, `_SessionPort`, `_CompositionDependencies`, `_FrozenTarget`, `DesktopLeaseError`, `DesktopLeaseSlot`, `_SessionBuild`, `_plain_dict`, `_positive_int`, `_positive_number`, `_fnv1a_utf16`, `build_exact_live_vnext_graph`，另有 69 项
- 直接静态依赖：`__future__`, `base64`, `collections.abc`, `ctypes`, `dataclasses`, `copy`, `hashlib`, `json`, `os`, `pathlib`, `threading`, `time`, `typing`, `uuid`，另有 23 项
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/orchestrator/projection.py -->
### `python/winpilot_home/winpilot/orchestrator/projection.py`

[打开文件](<../../python/winpilot_home/winpilot/orchestrator/projection.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：1097 行，55,661 bytes
- SHA-256：`df734aa56939cc6e9838315386e4e053cb624af417b718f782354b201f442774`
- 源码事实摘要：Read-optimized projections of Agent, controller, replay, and ARP events.
- 顶层符号/结构：`_compact`, `_nonnegative_latency_ms`, `_percentile`, `RunProjectionStore`, `const:_TERMINAL`, `const:RUN_PROJECTIONS`
- 直接静态依赖：`__future__`, `copy`, `json`, `math`, `threading`, `time`, `collections`, `pathlib`, `typing`, `..utils.log`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/orchestrator/runtime.py -->
### `python/winpilot_home/winpilot/orchestrator/runtime.py`

[打开文件](<../../python/winpilot_home/winpilot/orchestrator/runtime.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：1059 行，44,632 bytes
- SHA-256：`1aa0365b2826f40ec29ccb1cdb9be8af411ebe5223c9663a2189013b5710c209`
- 源码事实摘要：Executable ARP graph runtime with retries, cancellation, and human gates.
- 顶层符号/结构：`NodeResult`, `PauseController`, `_CombinedStopEvent`, `_json_preview`, `_interaction_state_preview`, `_typed_snapshot_list`, `ArpExecution`, `ArpRunManager`, `const:ARP_RUNS`
- 直接静态依赖：`__future__`, `json`, `threading`, `time`, `uuid`, `dataclasses`, `typing`, `..utils.log`, `.contracts`, `.evidence`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/perception/__init__.py -->
### `python/winpilot_home/winpilot/perception/__init__.py`

[打开文件](<../../python/winpilot_home/winpilot/perception/__init__.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：0 行，0 bytes
- SHA-256：`e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`
- 源码事实摘要：Python 包 python.winpilot_home.winpilot.perception 的初始化与公开边界。
- 顶层符号/结构：—
- 直接静态依赖：—
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/perception/app_aliases.py -->
### `python/winpilot_home/winpilot/perception/app_aliases.py`

[打开文件](<../../python/winpilot_home/winpilot/perception/app_aliases.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：10 行，364 bytes
- SHA-256：`442c7428a674978a9d5fd46c90f2e389be134958ca5101ac37f1641bc8ed64c7`
- 源码事实摘要：Canonicalize literal application names without guessing product identity.
- 顶层符号/结构：`expand`
- 直接静态依赖：`__future__`, `unicodedata`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/perception/apps.py -->
### `python/winpilot_home/winpilot/perception/apps.py`

[打开文件](<../../python/winpilot_home/winpilot/perception/apps.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：1024 行，36,292 bytes
- SHA-256：`80b3d27c7a08180b698946e8164713aabc8ca9d6b6b007a058347bcc1c348beb`
- 源码事实摘要：Resolve installed application display names to discovered launch targets.
- 顶层符号/结构：`_unique_text`, `_start_app_entry`, `_parse_start_apps_output`, `_merge_entry_metadata`, `_start_menu_entries`, `_uwp_entries`, `_app_paths_entries`, `_index`, `_normalized`, `_score`, `_entry_terms`, `_score_entry`, `resolve`, `launch_target`，另有 19 项
- 直接静态依赖：`__future__`, `json`, `os`, `re`, `shutil`, `subprocess`, `threading`, `time`, `unicodedata`, `difflib`, `pathlib`, `..utils.log`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/perception/fusion.py -->
### `python/winpilot_home/winpilot/perception/fusion.py`

[打开文件](<../../python/winpilot_home/winpilot/perception/fusion.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：768 行，33,689 bytes
- SHA-256：`90762bc03f94e8aa55e26cfe6200c5f2dfea0c230e57adbbc8ec931a6df63688`
- 源码事实摘要：Perception fusion: run enabled dimensions in priority order, merge,
- 顶层符号/结构：`_uia_content_count`, `_uia_is_rich`, `_rect_overlap_ratio`, `_foreground_modal_over_target`, `Snapshot`, `_merge`, `_has_readable`, `_escalate_empty`, `_run_uia`, `_run_ocr`, `_bounded_parallel_jobs`, `_uia_suppressed`, `_suppress_uia`, `observe`，另有 5 项
- 直接静态依赖：`__future__`, `re`, `threading`, `time`, `dataclasses`, `win32gui`, `..config`, `..utils.log`, `..utils.screenshot`, `.`, `.model`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/perception/groundtruth.py -->
### `python/winpilot_home/winpilot/perception/groundtruth.py`

[打开文件](<../../python/winpilot_home/winpilot/perception/groundtruth.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：169 行，6,873 bytes
- SHA-256：`dae0a6a2c1e38d93e78516f20607d61bb1802268e1c26c2917f0eb3ff6ff5de9`
- 源码事实摘要：Deterministic ground-truth checks — the Codex-grade verification primitive.
- 顶层符号/结构：`_expand`, `file_exists`, `file_format`, `file_contains`, `_process_names`, `process_running`, `process_gone`, `shell_true`, `check`, `const:_IMAGE_SIGNATURES`, `const:GROUNDTRUTH_KEYS`
- 直接静态依赖：`__future__`, `os`, `subprocess`, `pathlib`, `..utils.log`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/perception/incremental.py -->
### `python/winpilot_home/winpilot/perception/incremental.py`

[打开文件](<../../python/winpilot_home/winpilot/perception/incremental.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：143 行，5,082 bytes
- SHA-256：`02a519991fded44e23ecc092b8f67923d874450e862f2741e44937bbb65c33f2`
- 源码事实摘要：Cheap semantic analysis for screen-delta ROI crops.
- 顶层符号/结构：`regions_intersect`, `element_region`, `union_regions`, `dedupe_elements`, `analyze_changed_regions`
- 直接静态依赖：`__future__`, `time`, `typing`, `numpy`, `..config`, `..utils.log`, `..utils.screenshot`, `.`, `.fusion`, `.model`, `.ocr_lines`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/perception/intent.py -->
### `python/winpilot_home/winpilot/perception/intent.py`

[打开文件](<../../python/winpilot_home/winpilot/perception/intent.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：62 行，2,279 bytes
- SHA-256：`a1707c736e7e415f4847fb6a50ef8248b8146c4071b7161e410d22241cfc792e`
- 源码事实摘要：Select perception strategy from runtime intent, never product vocabulary.
- 顶层符号/结构：`_structured_intent`, `perception_mode`, `vision_priority_task`, `const:_RUNTIME_MODES`, `const:_VISUAL_OPERATIONS`
- 直接静态依赖：`__future__`, `typing`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/perception/model.py -->
### `python/winpilot_home/winpilot/perception/model.py`

[打开文件](<../../python/winpilot_home/winpilot/perception/model.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：70 行，2,702 bytes
- SHA-256：`1e8e9144b7953ab14576f1c5ccdbc848cfc9b90568a5b6b50ed657fd99cbe212`
- 源码事实摘要：Shared perception data model: UIElement, the unit all dimensions emit.
- 顶层符号/结构：`UIElement`
- 直接静态依赖：`__future__`, `dataclasses`, `typing`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/perception/ocr.py -->
### `python/winpilot_home/winpilot/perception/ocr.py`

[打开文件](<../../python/winpilot_home/winpilot/perception/ocr.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：541 行，20,383 bytes
- SHA-256：`a6f64c44743e822d54b6842804b4553c904b0645ec8a10625aaa3b5bef0f513e`
- 源码事实摘要：OCR dimension: RapidOCR (PP-OCR v3) with DirectML GPU acceleration.
- 顶层符号/结构：`foreground_priority`, `inference_scope`, `_inference_slot`, `_match_text`, `_rebuild_with_dml`, `get_engine`, `active_provider`, `warm_async`, `recognize`, `_control_key`, `_runs`, `_control_glyph_crop`, `locate_control_text`, `collect`，另有 1 项
- 直接静态依赖：`__future__`, `contextlib`, `re`, `threading`, `time`, `unicodedata`, `difflib`, `cv2`, `numpy`, `onnxruntime`, `..config`, `..utils.log`, `..utils.screenshot`, `.model`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/perception/ocr_lines.py -->
### `python/winpilot_home/winpilot/perception/ocr_lines.py`

[打开文件](<../../python/winpilot_home/winpilot/perception/ocr_lines.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：59 行，2,431 bytes
- SHA-256：`2902a2340246b5b221fb0348729425bc260d1b57c8626264f79345062b4f8dac`
- 源码事实摘要：Shared OCR line reconstruction for incremental and on-demand perception.
- 顶层符号/结构：`merge_line_elements`
- 直接静态依赖：`__future__`, `.model`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/perception/reducer.py -->
### `python/winpilot_home/winpilot/perception/reducer.py`

[打开文件](<../../python/winpilot_home/winpilot/perception/reducer.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：507 行，21,833 bytes
- SHA-256：`9e7d8a4bb710db05319f8195ddfc3c49242f0aac0d6a34d85631671495e9d6ba`
- 源码事实摘要：Compact perception summaries for the LLM hot path.
- 顶层符号/结构：`CandidateScore`, `_norm`, `_role_contains`, `_region_dict`, `_extract_terms`, `_stage`, `_chat_context`, `_non_blocking_notice_context`, `_score_element`, `reduce_snapshot`, `render_reduced_snapshot`, `const:_ACTION_WORDS`, `const:_IMPORTANT_ROLES`, `const:_INPUT_ROLES`，另有 3 项
- 直接静态依赖：`__future__`, `re`, `dataclasses`, `typing`, `..utils.screenshot`, `.fusion`, `.model`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/perception/search.py -->
### `python/winpilot_home/winpilot/perception/search.py`

[打开文件](<../../python/winpilot_home/winpilot/perception/search.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：394 行，13,205 bytes
- SHA-256：`fa54590b10c849214e8efa3863ddf409ebcd773e6e80b7e4faf99e230babb2b6`
- 源码事实摘要：Fast local filename search with Everything and a bounded native fallback.
- 顶层符号/结构：`find_es`, `_find_everything_exe`, `_ensure_everything_client`, `_normalized_extensions`, `_run_es`, `_search_everything`, `warm_async`, `_search_scandir`, `search`, `const:_DEFAULT_MAX`, `const:_DEFAULT_FALLBACK_TIMEOUT_S`, `const:_EVERYTHING_IPC_TIMEOUT_MS`, `const:_EVERYTHING_PROCESS_TIMEOUT_S`, `const:_SORT_NAMES`
- 直接静态依赖：`__future__`, `fnmatch`, `os`, `re`, `shutil`, `subprocess`, `threading`, `time`, `pathlib`, `typing`, `..config`, `..utils.log`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/perception/surface_binding.py -->
### `python/winpilot_home/winpilot/perception/surface_binding.py`

[打开文件](<../../python/winpilot_home/winpilot/perception/surface_binding.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：432 行，15,765 bytes
- SHA-256：`81f853f92fcbb3276df267733f8b8a465040d8b9993e83ec36ce852b11ccba5d`
- 源码事实摘要：Stable geometry contracts for visual interaction.
- 顶层符号/结构：`_bounds`, `InteractionRegion`, `SurfaceBindingResult`, `SurfaceBinding`
- 直接静态依赖：`__future__`, `dataclasses`, `typing`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/perception/tray.py -->
### `python/winpilot_home/winpilot/perception/tray.py`

[打开文件](<../../python/winpilot_home/winpilot/perception/tray.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：183 行，6,481 bytes
- SHA-256：`5b597e67bc9896d13c2f21dd30c1af4be3652a5ddfd6af87db374dc47ee7efaa`
- 源码事实摘要：System-tray dimension: enumerate and click notification-area icons.
- 顶层符号/结构：`_window_at_point`, `_walk_buttons`, `_taskbar_icons`, `_find_overflow_window`, `_open_overflow`, `list_tray_icons`, `click_tray_icon`, `const:_OVERFLOW_TITLES`, `const:_CHEVRON_NAMES`, `const:_MAX_DEPTH`
- 直接静态依赖：`__future__`, `time`, `uiautomation`, `win32gui`, `..utils.log`, `.model`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/perception/uia.py -->
### `python/winpilot_home/winpilot/perception/uia.py`

[打开文件](<../../python/winpilot_home/winpilot/perception/uia.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：348 行，11,318 bytes
- SHA-256：`384429c88e86b959009e4c8a28e21bfb8a56ee7e8ff7df7ae5932394c5e45066`
- 源码事实摘要：UIA dimension: walk the UI Automation tree of a window.
- 顶层符号/结构：`_is_legacy_chromium_root_child`, `_control_class_name`, `_has_modern_chromium_tree`, `_is_legacy_chromium_match`, `_ensure_thread_initialized`, `_states_of`, `_value_of`, `_to_element`, `collect`, `find_by_text`, `find_text`, `_query_text`, `find_by_automation_id`, `const:_INTERESTING`，另有 4 项
- 直接静态依赖：`__future__`, `threading`, `time`, `uiautomation`, `uiautomation.uiautomation`, `..utils.log`, `.model`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/perception/vision_cv.py -->
### `python/winpilot_home/winpilot/perception/vision_cv.py`

[打开文件](<../../python/winpilot_home/winpilot/perception/vision_cv.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：806 行，33,496 bytes
- SHA-256：`ba97bf55f03bc3beabb71decdcf58b3bc6d94b4bbbfc0eddf69bcfb222236277`
- 源码事实摘要：OpenCV dimension: what OCR cannot see.
- 顶层符号/结构：`_detect_control_candidates_from_image`, `detect_control_candidates`, `detect_icon_candidates`, `list_templates`, `save_template`, `save_template_image`, `match_template`, `color_probe`, `region_mean_color`, `diff_ratio`, `wait_screen_stable`, `is_animating`, `image_ink_stats_from_array`, `image_ink_stats`，另有 9 项
- 直接静态依赖：`__future__`, `base64`, `time`, `pathlib`, `cv2`, `numpy`, `..config`, `..utils.log`, `..utils.screenshot`, `.model`, `.surface_binding`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/perception/vlm.py -->
### `python/winpilot_home/winpilot/perception/vlm.py`

[打开文件](<../../python/winpilot_home/winpilot/perception/vlm.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：585 行，23,269 bytes
- SHA-256：`73bdc85cc99891839ce670eeb7bf4224ab690586a02e878776e534a0c5ca6397`
- 源码事实摘要：Optional VLM dimension: when the user configures a multimodal model
- 顶层符号/结构：`_settings`, `configuration_issue`, `is_configured`, `_client`, `_encode_region`, `_extra_body`, `_invoke_model`, `describe`, `_structured_json_from_array`, `structured_json_from_array`, `structured_json_from_image`, `verify`, `_validated_grounding_item`, `_decode_box`，另有 5 项
- 直接静态依赖：`__future__`, `base64`, `json`, `math`, `re`, `uuid`, `pathlib`, `typing`, `cv2`, `..config`, `..model_adapter`, `..modeling`, `..utils.log`, `..utils.screenshot`，另有 1 项
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/perception/web_search.py -->
### `python/winpilot_home/winpilot/perception/web_search.py`

[打开文件](<../../python/winpilot_home/winpilot/perception/web_search.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：191 行，7,782 bytes
- SHA-256：`420982115c65ffb34061682bfefdb996fe0d54c523d466297e8da357c71bf2a5`
- 源码事实摘要：Small pluggable web-search capability for the unified Agent.
- 顶层符号/结构：`_plain`, `_serper`, `_duckduckgo_result_url`, `_DuckDuckGoLiteParser`, `_duckduckgo_lite`, `_bing_rss`, `search`, `const:_USER_AGENT`
- 直接静态依赖：`__future__`, `html`, `html.parser`, `json`, `os`, `re`, `time`, `urllib.parse`, `urllib.request`, `xml.etree.ElementTree`, `typing`, `..config`, `..utils.log`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/perception/win32.py -->
### `python/winpilot_home/winpilot/perception/win32.py`

[打开文件](<../../python/winpilot_home/winpilot/perception/win32.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：304 行，10,687 bytes
- SHA-256：`f6f63789c89f789385abd21e426054b3289a096e8bf5ea987125a3a3d80f36c6`
- 源码事实摘要：Win32 dimension: window enumeration, classic-control text, Explorer items.
- 顶层符号/结构：`_window_text`, `_send_wm_gettext`, `list_windows`, `_title_key`, `title_contains`, `find_window`, `_process_name`, `find_app`, `collect`, `explorer_items`, `const:_CLASS_ROLE`
- 直接静态依赖：`__future__`, `ctypes`, `ctypes.wintypes`, `re`, `win32con`, `win32gui`, `win32process`, `..utils.log`, `..utils.screenshot`, `.model`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/perception/world.py -->
### `python/winpilot_home/winpilot/perception/world.py`

[打开文件](<../../python/winpilot_home/winpilot/perception/world.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：334 行，15,375 bytes
- SHA-256：`071db8da1f41088bc5b5248522e1147521e0593decced846d46effe77e3a0e81`
- 源码事实摘要：Lightweight world model: minimal cross-step state for stuck detection and
- 顶层符号/结构：`_args_hash`, `_ObserveState`, `WorldModel`, `const:_ACTION_TOOLS`, `const:_STUCK_REPEAT`, `const:_STUCK_NO_CHANGE`, `const:_MAX_HISTORY`, `const:_MAX_DELTA_ITEMS`
- 直接静态依赖：`__future__`, `hashlib`, `json`, `dataclasses`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/recorder/__init__.py -->
### `python/winpilot_home/winpilot/recorder/__init__.py`

[打开文件](<../../python/winpilot_home/winpilot/recorder/__init__.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：0 行，0 bytes
- SHA-256：`e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`
- 源码事实摘要：Python 包 python.winpilot_home.winpilot.recorder 的初始化与公开边界。
- 顶层符号/结构：—
- 直接静态依赖：—
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/recorder/apprentice.py -->
### `python/winpilot_home/winpilot/recorder/apprentice.py`

[打开文件](<../../python/winpilot_home/winpilot/recorder/apprentice.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：146 行，5,832 bytes
- SHA-256：`fb4b62eaf77a53de8bc1982c0d8aa03520ba2303e6ffb1f25ed212fa595b4b45`
- 源码事实摘要：Apprentice-mode lifecycle built on deterministic human recording.
- 顶层符号/结构：`ApprenticeManager`, `const:APPRENTICE`
- 直接静态依赖：`__future__`, `json`, `threading`, `time`, `uuid`, `pathlib`, `typing`, `..config`, `..memory`, `.`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/recorder/human.py -->
### `python/winpilot_home/winpilot/recorder/human.py`

[打开文件](<../../python/winpilot_home/winpilot/recorder/human.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：544 行，22,030 bytes
- SHA-256：`74ae9e55f84bf92a8212ea1f8ae22e36c9dcdcaf92c8e6c8f7df1e45834bf6d4`
- 源码事实摘要：Human-demonstration recorder → .wps.json with semantic selectors.
- 顶层符号/结构：`_normalize_key_char`, `_dist`, `StepBuilder`, `_key_name`, `HumanRecorder`, `const:_DRAG_THRESHOLD`, `const:_MOVE_SAMPLE`, `const:_MODIFIERS`, `const:_SPECIAL_KEYS`, `const:RECORDER`
- 直接静态依赖：`__future__`, `threading`, `time`, `pathlib`, `queue`, `typing`, `..config`, `..utils.log`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/recorder/replay_control.py -->
### `python/winpilot_home/winpilot/recorder/replay_control.py`

[打开文件](<../../python/winpilot_home/winpilot/recorder/replay_control.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：187 行，7,292 bytes
- SHA-256：`227457abaee7821e5be453f6123330a2cdc337f2db77b75470ad159a91d6fc12`
- 源码事实摘要：Offline replay control flow + variable interpolation.
- 顶层符号/结构：`interpolate`, `interpolate_step`, `_max_iters`, `exec_if`, `exec_loop`, `count_leaves`, `const:NESTED_STEP_KEYS`, `const:_VAR_RE`, `const:_DEFAULT_MAX_ITERS`
- 直接静态依赖：`__future__`, `logging`, `re`, `typing`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/recorder/trace.py -->
### `python/winpilot_home/winpilot/recorder/trace.py`

[打开文件](<../../python/winpilot_home/winpilot/recorder/trace.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：3780 行，157,874 bytes
- SHA-256：`e3b169e4f188d695a1df86980d68877c33cabf2cfd59f59a76e9ac84aee704c9`
- 源码事实摘要：Trace recording, script export, and offline (0-token) replay.
- 顶层符号/结构：`clean_selector_text`, `TraceRecorder`, `list_traces`, `_read_trace_records`, `read_trace`, `_distill_steps`, `_semantic_step_key`, `_step_semantic_text`, `_event_target_window`, `_extract_replay_steps`, `_legacy_event_requires_replay`, `_stable_target_window_hint`, `_target_window_from_result_meta`, `_target_window_from_args`，另有 39 项
- 直接静态依赖：`__future__`, `base64`, `hashlib`, `json`, `inspect`, `os`, `queue`, `re`, `threading`, `time`, `collections.abc`, `pathlib`, `typing`, `cv2`，另有 12 项
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/recorder/wps_validate.py -->
### `python/winpilot_home/winpilot/recorder/wps_validate.py`

[打开文件](<../../python/winpilot_home/winpilot/recorder/wps_validate.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：228 行，9,211 bytes
- SHA-256：`16c0f8a89f330a0f1ed853502c8b4aa407322a078f89b37eae519ac941d5163f`
- 源码事实摘要：Structural validation for the ``.wps.json`` offline replay script format.
- 顶层符号/结构：`_nonempty_str`, `_is_number`, `_is_point`, `_target_window_hint_from`, `_target_window_hint`, `_target_window_spec_present`, `_has_click_target`, `_valid_strokes`, `_validate_leaf_step`, `_validate_steps`, `validate_script`, `const:LEAF_TOOLS`, `const:CTRL_TOOLS`, `const:EXT_TOOLS`，另有 1 项
- 直接静态依赖：`__future__`, `typing`, `.replay_control`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/runtime_info.py -->
### `python/winpilot_home/winpilot/runtime_info.py`

[打开文件](<../../python/winpilot_home/winpilot/runtime_info.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：203 行，6,969 bytes
- SHA-256：`db3f43b57b894c82168aa804c5aaaeb7355008cfc48976435c4e0638cc42c7fe`
- 源码事实摘要：Runtime fingerprint helpers for debugging the live WinPilot service.
- 顶层符号/结构：`repo_root`, `_git`, `_git_dirty`, `_has_module`, `_source_stamp`, `_cached_source_state`, `_read_git_state`, `_cached_git_state`, `_reset_runtime_caches_for_tests`, `runtime_source_drifted`, `runtime_fingerprint`, `const:RUNTIME_CONTRACT_VERSION`, `const:_SOURCE_STAMP_TTL_S`, `const:_GIT_STATE_TTL_S`，另有 5 项
- 直接静态依赖：`__future__`, `importlib.util`, `hashlib`, `os`, `subprocess`, `sys`, `threading`, `time`, `pathlib`, `typing`, `.config`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/runtime_jobs.py -->
### `python/winpilot_home/winpilot/runtime_jobs.py`

[打开文件](<../../python/winpilot_home/winpilot/runtime_jobs.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：234 行，8,660 bytes
- SHA-256：`66e805086d941a8242529fdedc2d91e31b0c5653c2d30311e3091db3090b26bf`
- 源码事实摘要：Shared asynchronous job runtime with explicit resource leases.
- 顶层符号/结构：`RuntimeJob`, `RuntimeJobManager`, `const:RUNTIME_JOBS`
- 直接静态依赖：`__future__`, `threading`, `time`, `uuid`, `dataclasses`, `typing`, `.utils.log`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/scheduler.py -->
### `python/winpilot_home/winpilot/scheduler.py`

[打开文件](<../../python/winpilot_home/winpilot/scheduler.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：332 行，12,368 bytes
- SHA-256：`13308277c1d2f3bca5f9945f41499fe892dff9f52356486ecb743a4793ad7cc7`
- 源码事实摘要：Persistent scheduled WinPilot Agent tasks.
- 顶层符号/结构：`_task_key`, `reminder_message`, `_now`, `_iso`, `_parse_time`, `_read_unlocked`, `_write_unlocked`, `list_tasks`, `create_task`, `update_task`, `cancel_task`, `due_tasks`, `mark_started`, `mark_skipped`，另有 4 项
- 直接静态依赖：`__future__`, `json`, `os`, `re`, `threading`, `time`, `uuid`, `datetime`, `pathlib`, `typing`, `.config`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/server.py -->
### `python/winpilot_home/winpilot/server.py`

[打开文件](<../../python/winpilot_home/winpilot/server.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：2607 行，94,242 bytes
- SHA-256：`81ddca5fb7787de7069f4df5f4b35d8791f611f9a5a30ff5cff2ded4dfd17fbb`
- 源码事实摘要：FastAPI server: REST control surface + WebSocket event stream + static UI.
- 顶层符号/结构：`_legacy_build_runner`, `_legacy_session`, `FlowFragmentProposalRequest`, `_started_by_direct_uvicorn`, `_warm_perception_dimensions`, `_lifespan`, `_request_token`, `require_control_token`, `api_ping`, `_ensure_inside`, `_public_trace_leaf`, `_public_trace_run_id`, `_is_admin`, `_RunState`，另有 112 项
- 直接静态依赖：`__future__`, `asyncio`, `ctypes`, `json`, `os`, `queue`, `re`, `secrets`, `sys`, `threading`, `time`, `uuid`, `contextlib`, `pathlib`，另有 28 项
- API/IPC 入口：`GET /api/ping`, `POST /api/chat`, `POST /api/stop`, `GET /api/status`, `GET /api/v2/platform`, `GET /api/v2/runs`, `GET /api/v2/jobs`, `GET /api/v2/desktop-context`, `GET /api/v2/jobs/{job_id}`, `POST /api/v2/jobs/{job_id}/cancel`, `GET /api/v2/runs/current`, `GET /api/v2/runs/{run_id}`, `POST /api/v2/runs/{run_id}/cancel`, `POST /api/v2/runs/{run_id}/pause`，另有 55 项

<!-- PROJECT-FILE: python/winpilot_home/winpilot/tasking/__init__.py -->
### `python/winpilot_home/winpilot/tasking/__init__.py`

[打开文件](<../../python/winpilot_home/winpilot/tasking/__init__.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：19 行，608 bytes
- SHA-256：`87b3e3f6d5422e048bed3a460e34e1af4652e6b4cdd39d0348e75c7f77dc96f6`
- 源码事实摘要：Typed task contracts and the deterministic stage runtime.
- 顶层符号/结构：—
- 直接静态依赖：`.compiler`, `.contracts`, `.intent`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/tasking/adapters.py -->
### `python/winpilot_home/winpilot/tasking/adapters.py`

[打开文件](<../../python/winpilot_home/winpilot/tasking/adapters.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：273 行，9,367 bytes
- SHA-256：`2ec5806c1a38dbf8e31dce80becb03f2cebcb55b6805c99e7dd5e83bb5dcc0cc`
- 源码事实摘要：Plugin boundary for optional communication providers.
- 顶层符号/结构：`CommunicationAdapter`, `DomainAdapterRegistry`
- 直接静态依赖：`__future__`, `abc`, `typing`, `.artifacts`, `.evidence`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/tasking/artifacts.py -->
### `python/winpilot_home/winpilot/tasking/artifacts.py`

[打开文件](<../../python/winpilot_home/winpilot/tasking/artifacts.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：845 行，38,526 bytes
- SHA-256：`28a35160ff76cdd24f75435e1ea5ec56695c3e4a0fe119c2d1af0ebffbe03ab5`
- 源码事实摘要：Managed, run-scoped artifacts distinct from transient perception frames.
- 顶层符号/结构：`Artifact`, `_mime_for`, `_window_title`, `ArtifactStore`
- 直接静态依赖：`__future__`, `hashlib`, `io`, `json`, `pathlib`, `shutil`, `threading`, `time`, `typing`, `uuid`, `pydantic`, `..config`, `..perception`, `..utils.screenshot`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/tasking/capability.py -->
### `python/winpilot_home/winpilot/tasking/capability.py`

[打开文件](<../../python/winpilot_home/winpilot/tasking/capability.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：661 行，29,872 bytes
- SHA-256：`7362531c9d3783e364390e7a6e7eb79ae7c2d75875a12ccf5a3b08219e914cd8`
- 源码事实摘要：Capability selection and preflight satisfiability for typed task stages.
- 顶层符号/结构：`CapabilityUnsatisfiedError`, `ComponentChoice`, `CapabilityPlanner`, `tools_for_capabilities`, `const:CAPABILITY_TO_TOOLS`, `const:_EXECUTE_DESKTOP_MODEL_TOOLS`, `const:_COMPONENT_CAPABILITIES`, `const:_COMMUNICATION_ADAPTER_CAPABILITIES`, `const:_STAGE_BASE`
- 直接静态依赖：`__future__`, `dataclasses`, `os`, `typing`, `.contracts`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/tasking/compiler.py -->
### `python/winpilot_home/winpilot/tasking/compiler.py`

[打开文件](<../../python/winpilot_home/winpilot/tasking/compiler.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：643 行，29,009 bytes
- SHA-256：`1b1b667d72b64b3f87c2ed2880ef41ead9f3f98b9379a70f579b1c1996259dd3`
- 源码事实摘要：Compile natural-language tasks to typed contracts and executable ARP graphs.
- 顶层符号/结构：`_provider`, `TaskContractCompiler`
- 直接静态依赖：`__future__`, `re`, `uuid`, `.capability`, `.contracts`, `.intent`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/tasking/completion.py -->
### `python/winpilot_home/winpilot/tasking/completion.py`

[打开文件](<../../python/winpilot_home/winpilot/tasking/completion.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：314 行，14,005 bytes
- SHA-256：`96f18cb2e2746525a67ff65b74290c8cbbea98fabb16ef8c9b047c66e03ff161`
- 源码事实摘要：Fail-closed completion verification against task-contract obligations.
- 顶层符号/结构：`CompletionContractVerifier`
- 直接静态依赖：`__future__`, `collections`, `typing`, `.artifacts`, `.contracts`, `.evidence`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/tasking/contracts.py -->
### `python/winpilot_home/winpilot/tasking/contracts.py`

[打开文件](<../../python/winpilot_home/winpilot/tasking/contracts.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：298 行，11,639 bytes
- SHA-256：`1085b6e21688515d26b5b29fd69afe56aa85d87d13a6f647f4a9625fa579b5ad`
- 源码事实摘要：Versioned contracts shared by compilation, runtime, tools, and verification.
- 顶层符号/结构：`StageType`, `InteractionActor`, `InteractionIntent`, `InteractionSessionContract`, `TaskIntent`, `TaskRequirement`, `RequirementCoverage`, `StateBinding`, `ArtifactBinding`, `EvidenceProviderSpec`, `CompletionObligation`, `CapabilitySelection`, `TaskStage`, `TaskContract`，另有 3 项
- 直接静态依赖：`__future__`, `enum`, `typing`, `pydantic`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/tasking/conversation.py -->
### `python/winpilot_home/winpilot/tasking/conversation.py`

[打开文件](<../../python/winpilot_home/winpilot/tasking/conversation.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：98 行，3,531 bytes
- SHA-256：`21874d65a39e38480bef20b86534f8eaec5ecb1aa6cd2e04268b146f8af4c8c1`
- 源码事实摘要：Single-turn conversational responses that must not enter the GUI loop.
- 顶层符号/结构：`ConversationResponseError`, `ConversationResponse`, `generate_conversation_response`
- 直接静态依赖：`__future__`, `threading`, `time`, `uuid`, `dataclasses`, `typing`, `..config`, `..model_adapter`, `..modeling`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/tasking/evidence.py -->
### `python/winpilot_home/winpilot/tasking/evidence.py`

[打开文件](<../../python/winpilot_home/winpilot/tasking/evidence.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：75 行，2,541 bytes
- SHA-256：`c93b184f4a4cc8a8fa46acdc9b72389df8cdbc2c1f9f5f1e3bc4ce689115491b`
- 源码事实摘要：Run-scoped evidence registry used by tools, adapters, and completion gates.
- 顶层符号/结构：`EvidenceRegistry`
- 直接静态依赖：`__future__`, `threading`, `time`, `typing`, `uuid`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/tasking/execution_context.py -->
### `python/winpilot_home/winpilot/tasking/execution_context.py`

[打开文件](<../../python/winpilot_home/winpilot/tasking/execution_context.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：368 行，13,845 bytes
- SHA-256：`535686990960f68bec8125b70d20f2d951c9538e358fdb198e6815e39dd89780`
- 源码事实摘要：Validated client-to-runtime context for desktop foreground handoff.
- 顶层符号/结构：`_normalized_rect`, `normalize_execution_context`, `_window_title`, `_window_class_name`, `windows_shell_desktop_context`, `capture_desktop_workspace_context`, `window_process_id`, `capture_foreground_window_context`, `validated_handoff_target`, `foreground_handoff_conflict`
- 直接静态依赖：`__future__`, `ctypes`, `time`, `typing`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/tasking/intent.py -->
### `python/winpilot_home/winpilot/tasking/intent.py`

[打开文件](<../../python/winpilot_home/winpilot/tasking/intent.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：1404 行，65,080 bytes
- SHA-256：`422624245106aaad63bb9dca432949fded296dafce0dc4a71ec16a4a9a477f02`
- 源码事实摘要：Low-latency structured intent extraction.
- 顶层符号/结构：`_is_deictic_surface_source`, `_application_mentions`, `_communication_application`, `_strip_recipient`, `_quoted_message_segments`, `_explicit_message_text`, `_execution_domain`, `_recipient_after_channel`, `_recipient_from_relation`, `_drawing_subject`, `_application_launch_parts`, `_application_launch_target`, `_actor_assignments`, `_primary_action_modality`，另有 11 项
- 直接静态依赖：`__future__`, `re`, `collections.abc`, `typing`, `.contracts`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/tasking/interaction.py -->
### `python/winpilot_home/winpilot/tasking/interaction.py`

[打开文件](<../../python/winpilot_home/winpilot/tasking/interaction.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：3530 行，164,217 bytes
- SHA-256：`d440e3b9f40906d42219bef24f2f2550ffb27026f53a37465589416141fbb506`
- 源码事实摘要：Typed, target-bound visual interaction planning and execution.
- 顶层符号/结构：`InteractionPlanOutputError`, `_validation_error_details`, `_coord`, `_region`, `_point_in_region`, `_translated_source_inside_target`, `InteractionStroke`, `InteractionPartitionLocator`, `InteractionAction`, `ExpectedInteractionEffect`, `InteractionResolutionRequirement`, `InteractionDecision`, `_planning_prompt`, `_mapping_region`，另有 21 项
- 直接静态依赖：`__future__`, `json`, `math`, `pathlib`, `typing`, `unicodedata`, `pydantic`, `..perception`, `..perception.surface_binding`, `.visual_appearance`, `.visual_regions`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/tasking/interaction_controller.py -->
### `python/winpilot_home/winpilot/tasking/interaction_controller.py`

[打开文件](<../../python/winpilot_home/winpilot/tasking/interaction_controller.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：304 行，13,534 bytes
- SHA-256：`f859f801493525d8607342c61626c2ccdb94562296a76aedee8fa9da07b32a34`
- 源码事实摘要：Deterministic mapping from typed visual plans to Controller actions.
- 顶层符号/结构：`_map_point`, `_map_region`, `_inside`, `controller_preparation_actions`, `controller_actions`, `const:LOCAL_PATH_INSET_FRACTION`
- 直接静态依赖：`__future__`, `typing`, `..perception.surface_binding`, `.interaction`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/tasking/interaction_stage_runtime.py -->
### `python/winpilot_home/winpilot/tasking/interaction_stage_runtime.py`

[打开文件](<../../python/winpilot_home/winpilot/tasking/interaction_stage_runtime.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：3941 行，180,576 bytes
- SHA-256：`9345b1aec36892ab161ac9f459571d856887caa61248493c849de5b5f6cb7e10`
- 源码事实摘要：Deterministic runtime for typed visual-interaction stages.
- 顶层符号/结构：`_pointer_session_key`, `_registered_pointer_session`, `_register_pointer_session`, `_discard_registered_pointer_session`, `_local_causal_change_ratio`, `_controller_visual_no_effect`, `_local_resolution_requirement_proofs`, `_mutation_signature`, `_failed_visible_mutation`, `_failed_no_effect_evidence`, `_failed_no_effect_repeats_frame`, `_prove_artifact_frame_lineage`, `_json`, `_failure`，另有 20 项
- 直接静态依赖：`__future__`, `base64`, `ctypes`, `hashlib`, `json`, `pathlib`, `threading`, `time`, `typing`, `cv2`, `numpy`, `..agent.runtime_profile`, `..agent.tools`, `..fast.watcher`，另有 14 项
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/tasking/message_content.py -->
### `python/winpilot_home/winpilot/tasking/message_content.py`

[打开文件](<../../python/winpilot_home/winpilot/tasking/message_content.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：191 行，7,468 bytes
- SHA-256：`370f2d99837b5b17bd3130e659483ca2fc6cba74333add78eceb4243cb42abf3`
- 源码事实摘要：Run-scoped outbound message drafting and deterministic authorization.
- 顶层符号/结构：`MessageContentGenerationError`, `MessageDraft`, `_json_object`, `generate_message_content`, `authorize_message_content`
- 直接静态依赖：`__future__`, `json`, `threading`, `time`, `uuid`, `typing`, `pydantic`, `..config`, `..model_adapter`, `..modeling`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/tasking/recovery.py -->
### `python/winpilot_home/winpilot/tasking/recovery.py`

[打开文件](<../../python/winpilot_home/winpilot/tasking/recovery.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：62 行，2,773 bytes
- SHA-256：`b001e91eb80e9dd2c36894c3e439f8bd4b1a4f2c9a8542bcc2c48deae717fce2`
- 源码事实摘要：Failure-class recovery decisions for typed stages.
- 顶层符号/结构：`RecoveryDecision`, `RecoveryPolicy`
- 直接静态依赖：`__future__`, `dataclasses`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/tasking/stage_runtime.py -->
### `python/winpilot_home/winpilot/tasking/stage_runtime.py`

[打开文件](<../../python/winpilot_home/winpilot/tasking/stage_runtime.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：1296 行，57,924 bytes
- SHA-256：`b769a13b698ca151134cfef09bf04962caba5305105795eeb53d117656219a9a`
- 源码事实摘要：Deterministic state machine for one typed task stage.
- 顶层符号/结构：`_json`, `_raise_stage_failure`, `StageRuntime`, `task_stage_handler`, `const:SEMANTIC_EXECUTION_STAGE_TYPES`, `const:STAGE_RUNTIME`
- 直接静态依赖：`__future__`, `ctypes`, `json`, `time`, `typing`, `..agent.router`, `..agent.runtime_profile`, `..agent.tools`, `..orchestrator.contracts`, `..orchestrator.runtime`, `..perception`, `..perception.app_aliases`, `..utils.log`, `.adapters`，另有 10 项
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/tasking/visual_appearance.py -->
### `python/winpilot_home/winpilot/tasking/visual_appearance.py`

[打开文件](<../../python/winpilot_home/winpilot/tasking/visual_appearance.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：283 行，9,979 bytes
- SHA-256：`991ca98cc1f794bc9ed77ff45ade28447adb6e746330149cefe6ab738abe7f0a`
- 源码事实摘要：Deterministic current-frame appearance evidence for semantic planning.
- 顶层符号/结构：`_colour_family`, `_pixel_box`, `_frame_bounds`, `_component_topology`, `_components`, `inventory_frame_appearance`, `match_role_colours`, `const:_ROLE_COLOUR_TERMS`
- 直接静态依赖：`__future__`, `base64`, `json`, `pathlib`, `typing`, `cv2`, `numpy`, `..perception`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/tasking/visual_effects.py -->
### `python/winpilot_home/winpilot/tasking/visual_effects.py`

[打开文件](<../../python/winpilot_home/winpilot/tasking/visual_effects.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：838 行，32,942 bytes
- SHA-256：`cd1d2ebd914690c8e14b9033cdbf65405a7f5fe46bb776899b03d6cb503d9a9a`
- 源码事实摘要：Local causal proofs for pointer-authored visual gestures.
- 顶层符号/结构：`_point`, `_region_mask`, `verify_pointer_paths_in_frames`, `_pixel_box`, `_colour_histogram`, `_placement_geometry_valid`, `verify_direct_manipulation_transition`, `verify_typed_direct_manipulation_completion`, `_canonical_colour`, `_requested_colour_near_paths`, `verify_pointer_gesture_realization`, `verify_typed_freehand_turn_completion`, `const:_COLOUR_ALIASES`
- 直接静态依赖：`__future__`, `pathlib`, `typing`, `cv2`, `numpy`, `..perception`, `.interaction`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/tasking/visual_geometry.py -->
### `python/winpilot_home/winpilot/tasking/visual_geometry.py`

[打开文件](<../../python/winpilot_home/winpilot/tasking/visual_geometry.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：1585 行，69,693 bytes
- SHA-256：`dbf936666f5fc17dad0437b3275131d288d5f7a4bd2028be79ac2a7b5367974b`
- 源码事实摘要：Local pixel grounding for typed spatial interaction plans.
- 顶层符号/结构：`VisualGeometryError`, `_pixel_box`, `_normalized_box`, `_intersection_area`, `_iou`, `_intersection_over_smaller`, `_nested_same_center`, `_odd`, `_candidate_masks`, `_anchored_circle_masks`, `_circle_boundary_support`, `_resolve_region`, `_project_point`, `_typed_spatial_policy_proof`，另有 9 项
- 直接静态依赖：`__future__`, `math`, `pathlib`, `typing`, `cv2`, `numpy`, `.interaction`, `.visual_path`, `.visual_regions`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/tasking/visual_path.py -->
### `python/winpilot_home/winpilot/tasking/visual_path.py`

[打开文件](<../../python/winpilot_home/winpilot/tasking/visual_path.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：658 行，26,502 bytes
- SHA-256：`5a6919a1dc8479f74ac83b23d1332e839d6c1408359363cb5cb5bb079e7330cf`
- 源码事实摘要：Current-frame path control for constrained pointer drags.
- 顶层符号/结构：`GuidedPathError`, `_pixel_point`, `_pixel_box`, `_deduplicate`, `_patch_median`, `_route_surface_colour`, `infer_guided_surface_colour`, `infer_surrounding_surface_colour`, `_nearest_enabled`, `_line_is_clear`, `_astar`, `_simplify_safe_path`, `_polyline_length`, `_polyline_minimum_clearance`，另有 1 项
- 直接静态依赖：`__future__`, `heapq`, `math`, `typing`, `cv2`, `numpy`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/tasking/visual_regions.py -->
### `python/winpilot_home/winpilot/tasking/visual_regions.py`

[打开文件](<../../python/winpilot_home/winpilot/tasking/visual_regions.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：1911 行，74,917 bytes
- SHA-256：`892a88aa165df94dd9d811aa9dc03e85ae7189af0b3db6f86965ea7488687984`
- 源码事实摘要：Application-neutral grounding for visibly bounded interaction regions.
- 顶层符号/结构：`inventory_visible_regions`, `_pixel_box`, `_normalized_box`, `_intersection_area`, `_iou`, `_same_outline_object`, `_line_coverage`, `_fragmented_rectangular_boundaries`, `resolve_visible_bounded_region`, `_cluster_partition_lines`, `_coalesce_nearby_partition_fragments`, `_partition_axis_hypotheses`, `_partition_intersection_support`, `_single_partition_line_cross_support`，另有 7 项
- 直接静态依赖：`__future__`, `math`, `pathlib`, `typing`, `cv2`, `numpy`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/utils/__init__.py -->
### `python/winpilot_home/winpilot/utils/__init__.py`

[打开文件](<../../python/winpilot_home/winpilot/utils/__init__.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：0 行，0 bytes
- SHA-256：`e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`
- 源码事实摘要：Python 包 python.winpilot_home.winpilot.utils 的初始化与公开边界。
- 顶层符号/结构：—
- 直接静态依赖：—
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/utils/dpi.py -->
### `python/winpilot_home/winpilot/utils/dpi.py`

[打开文件](<../../python/winpilot_home/winpilot/utils/dpi.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：34 行，1,096 bytes
- SHA-256：`91679fbe3f3aaaeb4879080216462cbfec4968967bfef526f4ea52816499a21a`
- 源码事实摘要：Process-wide Windows DPI coordinate initialization.
- 顶层符号/结构：`ensure_process_dpi_awareness`, `const:_LOCK`, `const:_INITIALIZED`
- 直接静态依赖：`__future__`, `ctypes`, `threading`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/utils/image_capture.py -->
### `python/winpilot_home/winpilot/utils/image_capture.py`

[打开文件](<../../python/winpilot_home/winpilot/utils/image_capture.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：443 行，18,069 bytes
- SHA-256：`0ba3671388376179cb0f229a87dfa734737fb2f4eb62bb61047b0d0b322b08bf`
- 源码事实摘要：Small image capture helpers shared by agent tools and the loop.
- 顶层符号/结构：`VisualCaptureScope`, `window_client_region`, `region_frame_bounds`, `select_visual_capture_scope`, `capture_window_jpeg_b64`, `normalized_detail_crop_jpeg_b64`, `jpeg_diff_ratio`, `jpeg_diff_ratio_in_normalized_roi`, `jpeg_color_activation_in_normalized_roi`, `jpeg_diff_ratios_in_normalized_rois`
- 直接静态依赖：`__future__`, `base64`, `dataclasses`, `typing`, `cv2`, `numpy`, `win32gui`, `..config`, `.`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/utils/log.py -->
### `python/winpilot_home/winpilot/utils/log.py`

[打开文件](<../../python/winpilot_home/winpilot/utils/log.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：75 行，2,513 bytes
- SHA-256：`86f046d5bbbc13b2342633918eb993750089e46355154ab7f9cc518257229dcb`
- 源码事实摘要：Central event bus + logging: every agent/perception/action step publishes here.
- 顶层符号/结构：`EventBus`, `const:BUS`
- 直接静态依赖：`__future__`, `logging`, `queue`, `sys`, `threading`, `time`, `typing`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/utils/screenshot.py -->
### `python/winpilot_home/winpilot/utils/screenshot.py`

[打开文件](<../../python/winpilot_home/winpilot/utils/screenshot.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：170 行，5,506 bytes
- SHA-256：`4d996a417e192865db81a6824efafec96bc72e95bc2c8fdb43df7b62bc28b01b`
- 源码事实摘要：Screen capture utilities: full-screen / window-region capture via mss.
- 顶层符号/结构：`_get_mss`, `Region`, `DesktopGeometry`, `_virtual_metrics`, `virtual_screen_region`, `monitor_regions`, `primary_screen_region`, `desktop_geometry`, `screen_size`, `capture`, `window_region`
- 直接静态依赖：`__future__`, `ctypes.wintypes`, `threading`, `dataclasses`, `mss`, `numpy`, `.dpi`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/utils/trace_payload.py -->
### `python/winpilot_home/winpilot/utils/trace_payload.py`

[打开文件](<../../python/winpilot_home/winpilot/utils/trace_payload.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：135 行，4,616 bytes
- SHA-256：`e328abaafba121e44b9b8da727e01d92d1db5bda4aafadd85e080c52567d1f24`
- 源码事实摘要：Compact, non-mutating payloads before diagnostic trace persistence.
- 顶层符号/结构：`_frame_id_in_text`, `_frame_metadata`, `_list_frame_id`, `sanitize_trace_payload`, `const:_FRAME_ID_RE`, `const:_RAW_FRAME_KEYS`, `const:_PRIVATE_TEXT_KEYS`
- 直接静态依赖：`__future__`, `base64`, `binascii`, `hashlib`, `re`, `typing`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/visual_runtime/__init__.py -->
### `python/winpilot_home/winpilot/visual_runtime/__init__.py`

[打开文件](<../../python/winpilot_home/winpilot/visual_runtime/__init__.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：12 行，417 bytes
- SHA-256：`b7a57604206c8dbbddeeaa8f86ef38aadb73ef25859da5bc0896b302f4895173`
- 源码事实摘要：Shared mechanical owners for legacy and vNext visual runtimes.
- 顶层符号/结构：—
- 直接静态依赖：`.frame_observer`, `.normalized_frame_coordinates`, `.submitted_image_coordinates`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/visual_runtime/frame_authority.py -->
### `python/winpilot_home/winpilot/visual_runtime/frame_authority.py`

[打开文件](<../../python/winpilot_home/winpilot/visual_runtime/frame_authority.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：199 行，6,748 bytes
- SHA-256：`bb4d33f7bdd4055208838903b024e9a0485b6e0ed243f40d3435be5cbedec448`
- 源码事实摘要：Shared mechanical owner for the pixels that ground the next GUI action.
- 顶层符号/结构：`FrameIdentityLike`, `FrameState`, `FrameAuthority`
- 直接静态依赖：`__future__`, `dataclasses`, `hashlib`, `threading`, `time`, `typing`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/visual_runtime/frame_observer.py -->
### `python/winpilot_home/winpilot/visual_runtime/frame_observer.py`

[打开文件](<../../python/winpilot_home/winpilot/visual_runtime/frame_observer.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：189 行，6,816 bytes
- SHA-256：`56bfd369bd3fbbef04a46370a100c49e871309871665bbe8e771b68ddf352797`
- 源码事实摘要：Capture and publish the single frame allowed to ground GUI actions.
- 顶层符号/结构：`FrameCapture`, `FrameObserver`
- 直接静态依赖：`__future__`, `collections.abc`, `dataclasses`, `time`, `typing`, `.frame_authority`, `..utils.image_capture`, `..utils.screenshot`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/visual_runtime/input_sequence.py -->
### `python/winpilot_home/winpilot/visual_runtime/input_sequence.py`

[打开文件](<../../python/winpilot_home/winpilot/visual_runtime/input_sequence.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：125 行，5,130 bytes
- SHA-256：`055f97fe1dc9f1d52511da1353858266e36806dc3aabaa6bd976a7ac84093c2a`
- 源码事实摘要：Minimal model-facing grammar for a physical input timeline.
- 顶层符号/结构：`InputSequenceStep`, `_fields`, `_milliseconds`, `_point`, `_pointer`, `parse_input_sequence`
- 直接静态依赖：`__future__`, `dataclasses`, `re`, `.keyboard_contracts`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/visual_runtime/keyboard_contracts.py -->
### `python/winpilot_home/winpilot/visual_runtime/keyboard_contracts.py`

[打开文件](<../../python/winpilot_home/winpilot/visual_runtime/keyboard_contracts.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：168 行，5,854 bytes
- SHA-256：`496f81f05c38aebac869101141c02bff8f416d33edf7fd81fa5ecc833f3b04e4`
- 源码事实摘要：Pure, owner-neutral contracts shared by keyboard schema and execution.
- 顶层符号/结构：`normalize_hotkey_args`, `canonical_hotkey_args`, `HotkeyChord`, `const:MAX_TYPE_TEXT_CODEPOINTS`, `const:CANONICAL_HOTKEY_MODIFIERS`, `const:CANONICAL_HOTKEY_KEYS`, `const:_KEY_ALIASES`
- 直接静态依赖：`__future__`, `json`, `dataclasses`, `typing`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/visual_runtime/normalized_frame_coordinates.py -->
### `python/winpilot_home/winpilot/visual_runtime/normalized_frame_coordinates.py`

[打开文件](<../../python/winpilot_home/winpilot/visual_runtime/normalized_frame_coordinates.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：73 行，2,600 bytes
- SHA-256：`f9ce441f9c2d2e18361cda121dd910fe96ed52aba5a4ef9dddc87d9ff2209380`
- 源码事实摘要：Pure normalized-frame to physical-screen point mapping.
- 顶层符号/结构：`NormalizedFrameCoordinateMapper`
- 直接静态依赖：`__future__`, `dataclasses`, `math`, `typing`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/visual_runtime/submitted_image_coordinates.py -->
### `python/winpilot_home/winpilot/visual_runtime/submitted_image_coordinates.py`

[打开文件](<../../python/winpilot_home/winpilot/visual_runtime/submitted_image_coordinates.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：273 行，11,326 bytes
- SHA-256：`37d67db43bce8d0e177f4cb61ff33db60b6784965f3028a269c4b61ca901093b`
- 源码事实摘要：Shared pure submitted-image to normalized-frame coordinate mapping.
- 顶层符号/结构：`SubmittedImageCoordinateMapper`
- 直接静态依赖：`__future__`, `dataclasses`, `math`, `typing`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/vnext/__init__.py -->
### `python/winpilot_home/winpilot/vnext/__init__.py`

[打开文件](<../../python/winpilot_home/winpilot/vnext/__init__.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：3 行，86 bytes
- SHA-256：`7cbf82c2545e15cb577c670edd306cfd925012f53f7bf63276c2ebcccfb36e38`
- 源码事实摘要：Pure, product-unwired vNext kernel namespace.
- 顶层符号/结构：—
- 直接静态依赖：—
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/vnext/actor/__init__.py -->
### `python/winpilot_home/winpilot/vnext/actor/__init__.py`

[打开文件](<../../python/winpilot_home/winpilot/vnext/actor/__init__.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：8 行，278 bytes
- SHA-256：`db9b7fc7b6993560e50218250a3d17c3aa8554150c7e830398d3686a37bc5d54`
- 源码事实摘要：Pure Visual Actor proposal and Runtime-envelope contracts.
- 顶层符号/结构：—
- 直接静态依赖：`.envelope`, `.proposals`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/vnext/actor/envelope.py -->
### `python/winpilot_home/winpilot/vnext/actor/envelope.py`

[打开文件](<../../python/winpilot_home/winpilot/vnext/actor/envelope.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：652 行，22,009 bytes
- SHA-256：`d43b320f23c12b9d840219ec7d0ad9e411038bdf3994c4d529eafdc5c8e65aa2`
- 源码事实摘要：Runtime-owned identity envelope for one admitted Actor proposal.
- 顶层符号/结构：`InvalidActorEnvelope`, `InvalidActorAdmission`, `_identity`, `_identities`, `_ordered_identities`, `_sha256`, `_bounded_integer`, `_frame_region`, `_submitted_size`, `_submitted_coordinate`, `_validate_image_region`, `proposal_tool_name`, `_decision_id`, `_mission_projection_sha256`，另有 9 项
- 直接静态依赖：`__future__`, `hashlib`, `json`, `math`, `re`, `typing`, `pydantic`, `..capabilities`, `..mission`, `..json_contract`, `..profiling`, `.proposals`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/vnext/actor/proposals.py -->
### `python/winpilot_home/winpilot/vnext/actor/proposals.py`

[打开文件](<../../python/winpilot_home/winpilot/vnext/actor/proposals.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：337 行，10,455 bytes
- SHA-256：`edc436d2a8c3314ea4bef5af77442990e50f169ac57e8f2699e951e99da407e5`
- 源码事实摘要：Strict, identity-free P4a Visual Actor proposal contracts.
- 顶层符号/结构：`ActorContractError`, `InvalidActorProposal`, `_text`, `_unique_text`, `_freeze`, `_thaw`, `_ActorModel`, `ImageRegion`, `DesktopActionProposal`, `ObserveProposal`, `CapabilityRequest`, `InspectionRequest`, `ClaimObligationDone`, `BlockedReport`，另有 9 项
- 直接静态依赖：`__future__`, `json`, `math`, `re`, `types`, `typing`, `pydantic`, `..capability_names`, `..json_contract`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/vnext/capabilities/__init__.py -->
### `python/winpilot_home/winpilot/vnext/capabilities/__init__.py`

[打开文件](<../../python/winpilot_home/winpilot/vnext/capabilities/__init__.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：13 行，342 bytes
- SHA-256：`f83ad7b72578b2277f5cae016621a61c77014175672a80bd97eaa9faaac9fccc`
- 源码事实摘要：Pure capability contracts and deterministic Broker projection.
- 顶层符号/结构：—
- 直接静态依赖：`.contracts`, `.state`, `.broker`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/vnext/capabilities/broker.py -->
### `python/winpilot_home/winpilot/vnext/capabilities/broker.py`

[打开文件](<../../python/winpilot_home/winpilot/vnext/capabilities/broker.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：555 行，19,944 bytes
- SHA-256：`0ebbd9178f8eda12ab568f774b274eba458a23bf97164e4a308a79d522982907`
- 源码事实摘要：Pure deterministic Capability Broker; it never inspects GUI semantics.
- 顶层符号/结构：`CapabilityBroker`
- 直接静态依赖：`__future__`, `dataclasses`, `typing`, `..capability_names`, `..mission`, `..profiling`, `.contracts`, `.state`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/vnext/capabilities/contracts.py -->
### `python/winpilot_home/winpilot/vnext/capabilities/contracts.py`

[打开文件](<../../python/winpilot_home/winpilot/vnext/capabilities/contracts.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：546 行，20,498 bytes
- SHA-256：`d625b9ecae50cccd037963b647d3c592206d4b7717a6fe394ddbb9998f8a463e`
- 源码事实摘要：Strict P3 contracts for catalog, grants, runtime state, and projection.
- 顶层符号/结构：`CapabilityContractError`, `ToolRoute`, `ExecutionRoute`, `ExecutionMedium`, `MutationKind`, `PointerDisposition`, `_text`, `_identity`, `_strings`, `_capability`, `_capabilities`, `_enum_value`, `_wire_array`, `_thaw_frozen_json`，另有 13 项
- 直接静态依赖：`__future__`, `hashlib`, `json`, `re`, `dataclasses`, `enum`, `types`, `typing`, `..capability_names`, `..json_contract`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/vnext/capabilities/state.py -->
### `python/winpilot_home/winpilot/vnext/capabilities/state.py`

[打开文件](<../../python/winpilot_home/winpilot/vnext/capabilities/state.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：663 行，26,773 bytes
- SHA-256：`cf7f947a688eb83cedc081ed0be67f724fd13017a0b39398a4911212b6a38902`
- 源码事实摘要：Immutable runtime-state and projection contracts for the P3 Broker.
- 顶层符号/结构：`TargetState`, `AuthoritativeFrameState`, `PointerLeaseState`, `ArtifactState`, `ExecutorOffer`, `BrokerRuntimeState`, `_broker_projection_id`, `BrokerProjection`
- 直接静态依赖：`__future__`, `dataclasses`, `hashlib`, `json`, `typing`, `.contracts`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/vnext/capability_names.py -->
### `python/winpilot_home/winpilot/vnext/capability_names.py`

[打开文件](<../../python/winpilot_home/winpilot/vnext/capability_names.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：57 行，1,683 bytes
- SHA-256：`62242a420cc8dbd4385eabf9246f6b587a1cf7bc694a89b29c7eb240222facad`
- 源码事实摘要：Single dependency-light owner for broad vNext capability names.
- 顶层符号/结构：`InvalidCapability`, `canonical_capability`, `canonical_capabilities`, `const:CAPABILITY_PATTERN`, `const:CANONICAL_CAPABILITIES`
- 直接静态依赖：`__future__`, `re`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/vnext/desktop_adapter/__init__.py -->
### `python/winpilot_home/winpilot/vnext/desktop_adapter/__init__.py`

[打开文件](<../../python/winpilot_home/winpilot/vnext/desktop_adapter/__init__.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：25 行，577 bytes
- SHA-256：`df671ae25e12947c1a2d8085aaf871444724f407ad3871a0d81ff3c4e9f074fe`
- 源码事实摘要：Default-unreachable atomic desktop bridge for the minimal vNext Runtime.
- 顶层符号/结构：—
- 直接静态依赖：`.atomic`, `.workspace`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/vnext/desktop_adapter/atomic.py -->
### `python/winpilot_home/winpilot/vnext/desktop_adapter/atomic.py`

[打开文件](<../../python/winpilot_home/winpilot/vnext/desktop_adapter/atomic.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：2026 行，75,813 bytes
- SHA-256：`15404aff9c47e1cb6927124cbde9c3c80677cdcc88e6aae8fe2ae257b94ae552`
- 源码事实摘要：One atomic bridge from an Actor decision to admitted physical input.
- 顶层符号/结构：`LiveTargetSnapshot`, `DesktopTargetProbe`, `DesktopWorkspacePort`, `RawReceiptTraceSink`, `_PreparedAction`, `_PinnedExecution`, `_HeldPointerLease`, `AtomicDesktopAdapter`, `const:_ACTIONS`, `const:_POST_FRAME_KIND`, `const:_JPEG_MEDIA_TYPE`, `const:_MAX_SCROLL_AMOUNT`, `const:_MAX_LAUNCH_EFFECT_CHARS`, `const:_DEFAULT_HELD_POINTER_TIMEOUT_S`
- 直接静态依赖：`__future__`, `base64`, `collections.abc`, `dataclasses`, `hashlib`, `json`, `math`, `ntpath`, `threading`, `time`, `types`, `typing`, `...action.desktop_queue`, `...action.raw_device`，另有 11 项
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/vnext/desktop_adapter/workspace.py -->
### `python/winpilot_home/winpilot/vnext/desktop_adapter/workspace.py`

[打开文件](<../../python/winpilot_home/winpilot/vnext/desktop_adapter/workspace.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：421 行，15,636 bytes
- SHA-256：`559a2b86592da43ab067fc04c9f1d561e20a255a4677f9306380cb6bc3d7b91b`
- 源码事实摘要：One mutable desktop workspace above per-action atomic input.
- 顶层符号/结构：`WorkspaceSurface`, `WorkspaceObservation`, `_PendingTransition`, `DesktopWorkspace`
- 直接静态依赖：`__future__`, `collections.abc`, `dataclasses`, `threading`, `time`, `..mission`, `.atomic`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/vnext/desktop_ops.py -->
### `python/winpilot_home/winpilot/vnext/desktop_ops.py`

[打开文件](<../../python/winpilot_home/winpilot/vnext/desktop_ops.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：116 行，3,522 bytes
- SHA-256：`e1b8bb559707c498afbb51267bb85939a5cacc3f895979e5a6d0b8f3e26e2e4d`
- 源码事实摘要：vNext-owned capture / window-inventory / image-paste operation contracts.
- 顶层符号/结构：`_bounded_text`, `capture_screen`, `list_windows`, `paste_image`, `const:CAPTURE_FORMAT`, `const:WINDOWS_FORMAT`, `const:PASTE_FORMAT`, `const:_CAPTURE_OUTPUT_MAX_CHARS`
- 直接静态依赖：`__future__`, `typing`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/vnext/evidence/__init__.py -->
### `python/winpilot_home/winpilot/vnext/evidence/__init__.py`

[打开文件](<../../python/winpilot_home/winpilot/vnext/evidence/__init__.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：13 行，262 bytes
- SHA-256：`b768f6e3dcd2057edaf70f82c7be1544df74f6d5809f881f86c31f5802248fc6`
- 源码事实摘要：Run-scoped evidence ownership for vNext.
- 顶层符号/结构：—
- 直接静态依赖：`.registry`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/vnext/evidence/registry.py -->
### `python/winpilot_home/winpilot/vnext/evidence/registry.py`

[打开文件](<../../python/winpilot_home/winpilot/vnext/evidence/registry.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：110 行，4,060 bytes
- SHA-256：`6717590e096f715f83f26742b4cad92281a7257c62a081b421f035421e40da49`
- 源码事实摘要：Canonical run-scoped storage for already-constructed vNext evidence.
- 顶层符号/结构：`EvidenceRegistryError`, `EvidenceRegistryClosed`, `EvidenceRegistry`
- 直接静态依赖：`__future__`, `collections.abc`, `threading`, `..mission.contracts`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/vnext/json_contract.py -->
### `python/winpilot_home/winpilot/vnext/json_contract.py`

[打开文件](<../../python/winpilot_home/winpilot/vnext/json_contract.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：178 行，6,200 bytes
- SHA-256：`f7023ef9b98d3614dac6afeb59eafc955550defcb771ece1d32e58400534b2f8`
- 源码事实摘要：Shared bounded validation for canonical vNext JSON values.
- 顶层符号/结构：`BoundedJSONError`, `_display_path`, `bounded_json_copy`, `freeze_bounded_json`, `const:MAX_JSON_DEPTH`, `const:MAX_JSON_NODES`, `const:MAX_JSON_STRING_LENGTH`, `const:MAX_JSON_ARRAY_ITEMS`, `const:MAX_JSON_KEY_LENGTH`, `const:MAX_JSON_UTF8_BYTES`, `const:MIN_SIGNED_64`, `const:MAX_SIGNED_64`
- 直接静态依赖：`__future__`, `math`, `types`, `typing`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/vnext/mission/__init__.py -->
### `python/winpilot_home/winpilot/vnext/mission/__init__.py`

[打开文件](<../../python/winpilot_home/winpilot/vnext/mission/__init__.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：12 行，314 bytes
- SHA-256：`d4f4324375541315804b36969a792f596ed747d894a5ab173039354f41591263`
- 源码事实摘要：Pure mission contracts and deterministic event replay.
- 顶层符号/结构：—
- 直接静态依赖：`.contracts`, `.reducer`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/vnext/mission/contracts.py -->
### `python/winpilot_home/winpilot/vnext/mission/contracts.py`

[打开文件](<../../python/winpilot_home/winpilot/vnext/mission/contracts.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：2923 行，103,228 bytes
- SHA-256：`4afc814aee40354072a20b3b156298976328d2e20b8cee2dbaad1e5830c10857`
- 源码事实摘要：Pure, versioned state contracts for the vNext mission shadow.
- 顶层符号/结构：`thaw`, `_mapping`, `_json_mapping`, `MissionError`, `InvalidMissionDefinition`, `InvalidMissionEvent`, `StaleRevisionError`, `DuplicateEventError`, `EvidenceScopeError`, `IllegalTransitionError`, `_contract_text`, `_contract_bool`, `_contract_int`, `_contract_float`，另有 37 项
- 直接静态依赖：`__future__`, `hashlib`, `json`, `math`, `re`, `dataclasses`, `enum`, `types`, `typing`, `..json_contract`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/vnext/mission/reducer.py -->
### `python/winpilot_home/winpilot/vnext/mission/reducer.py`

[打开文件](<../../python/winpilot_home/winpilot/vnext/mission/reducer.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：1219 行，50,641 bytes
- SHA-256：`308e108d0f78308cb9c85e66f5823d22201f0bc79b878f859fe3079b75287e9f`
- 源码事实摘要：Pure MissionReducer and deterministic event replay.
- 顶层符号/结构：`_payload_text`, `_payload_string_tuple`, `_payload_items`, `_initial_projection`, `_definition_for`, `_state_map`, `_dependencies_verified`, `_ready`, `_first_ready`, `_required_verified`, `_recompute`, `_evidence_refs`, `_merge_evidence`, `_parse_artifact`，另有 19 项
- 直接静态依赖：`__future__`, `hashlib`, `json`, `dataclasses`, `typing`, `.contracts`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/vnext/model_roles/__init__.py -->
### `python/winpilot_home/winpilot/vnext/model_roles/__init__.py`

[打开文件](<../../python/winpilot_home/winpilot/vnext/model_roles/__init__.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：30 行，824 bytes
- SHA-256：`158cf08c1a5b6625bec51faa64cad8783de635cbf69720d3673c9474f985298e`
- 源码事实摘要：Live Visual Actor role.
- 顶层符号/结构：—
- 直接静态依赖：`.common`, `.visual_actor`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/vnext/model_roles/common.py -->
### `python/winpilot_home/winpilot/vnext/model_roles/common.py`

[打开文件](<../../python/winpilot_home/winpilot/vnext/model_roles/common.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：190 行，6,205 bytes
- SHA-256：`eea6f12ac49e853273f3614b9fc72a24f15f9a91feec646d2975b0dc2961f447`
- 源码事实摘要：Shared fail-closed helpers for the two isolated live model roles.
- 顶层符号/结构：`LiveModelRoleError`, `require_text`, `require_positive_int`, `require_positive_timeout`, `require_jpeg`, `member`, `strict_json_object`, `response_message`, `require_empty_content`, `narration_content`, `const:MAX_LIVE_MODEL_TOTAL_TIMEOUT_S`, `const:MAX_LIVE_MODEL_OUTPUT_TOKENS`, `const:MAX_LIVE_MODEL_JSON_RESPONSE_CHARS`, `const:MAX_LIVE_MODEL_NAME_CHARS`，另有 1 项
- 直接静态依赖：`__future__`, `base64`, `json`, `math`, `collections.abc`, `typing`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/vnext/model_roles/visual_actor.py -->
### `python/winpilot_home/winpilot/vnext/model_roles/visual_actor.py`

[打开文件](<../../python/winpilot_home/winpilot/vnext/model_roles/visual_actor.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：3641 行，141,074 bytes
- SHA-256：`d8c23217358189a4dc1b66a6691a9f546598f85c9ff1a28af5b37d230a57f3cc`
- 源码事实摘要：One semantic Visual Actor backed by the shared model call Runtime.
- 顶层符号/结构：`_InitialExchange`, `_VisualExchange`, `VisualActorTurnAdmission`, `_canonical_json`, `_repeated_tool_result`, `_project_tool_result`, `_projected_tool_message`, `_finish_reason`, `_truncation_messages`, `_actionable_tool_result`, `_actionable_tool_error`, `_tool_function_name`, `_decision_id`, `_initial_decision_id`，另有 51 项
- 直接静态依赖：`__future__`, `hashlib`, `json`, `re`, `threading`, `concurrent.futures`, `copy`, `collections.abc`, `dataclasses`, `pathlib`, `typing`, `...modeling`, `..actor`, `..runtime.contracts`，另有 1 项
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/vnext/profiling/__init__.py -->
### `python/winpilot_home/winpilot/vnext/profiling/__init__.py`

[打开文件](<../../python/winpilot_home/winpilot/vnext/profiling/__init__.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：32 行，841 bytes
- SHA-256：`21fbb700288c686b270e62e96db957c806ed72c7edfbb35a5edb4c21e373f614`
- 源码事实摘要：Pure ExecutionProfile contracts and deterministic acceptance policy.
- 顶层符号/结构：—
- 直接静态依赖：`.contracts`, `.policy`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/vnext/profiling/contracts.py -->
### `python/winpilot_home/winpilot/vnext/profiling/contracts.py`

[打开文件](<../../python/winpilot_home/winpilot/vnext/profiling/contracts.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：547 行，22,839 bytes
- SHA-256：`5131b9c6a4fa04dfdeb1b08859423df3c4c157e1f887e41e03e78af6a39bf886`
- 源码事实摘要：Strict, versioned P2a profiling contracts.
- 顶层符号/结构：`ExecutionProfileError`, `InvalidProfileSchema`, `UnsupportedCapability`, `ExecutionDepth`, `_bounded_object`, `_strict_keys`, `_text`, `_string_tuple`, `_finite_confidence`, `_bool`, `_depth`, `TaskDimensions`, `CognitiveServices`, `ProfileTrigger`，另有 14 项
- 直接静态依赖：`__future__`, `hashlib`, `json`, `math`, `dataclasses`, `enum`, `typing`, `..capability_names`, `..json_contract`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/vnext/profiling/policy.py -->
### `python/winpilot_home/winpilot/vnext/profiling/policy.py`

[打开文件](<../../python/winpilot_home/winpilot/vnext/profiling/policy.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：390 行，16,343 bytes
- SHA-256：`37dd56ff1f9048aa9f9dce9b93e84181e65585a7279926555f5f6466c960c2eb`
- 源码事实摘要：Deterministic P2a execution policy.
- 顶层符号/结构：`_normalize_available`, `_validate_mission_identity`, `ExecutionPolicy`, `const:DEFAULT_CAPABILITY_CATALOG`, `const:_SERVICE_NAMES`, `const:_SERVICE_FIELDS`
- 直接静态依赖：`__future__`, `dataclasses`, `typing`, `..capability_names`, `..json_contract`, `.contracts`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/vnext/runtime/__init__.py -->
### `python/winpilot_home/winpilot/vnext/runtime/__init__.py`

[打开文件](<../../python/winpilot_home/winpilot/vnext/runtime/__init__.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：14 行，374 bytes
- SHA-256：`2e61992a804023e43f2c50bcf1c572f6137939ec2f1b25e3407bec5b23dd2473`
- 源码事实摘要：Default-unreachable minimal vNext Mission Runtime.
- 顶层符号/结构：—
- 直接静态依赖：`.contracts`, `.mission_runtime`, `.ports`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/vnext/runtime/contracts.py -->
### `python/winpilot_home/winpilot/vnext/runtime/contracts.py`

[打开文件](<../../python/winpilot_home/winpilot/vnext/runtime/contracts.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：1343 行，47,554 bytes
- SHA-256：`9fd732b75f7c4f5e783155c3b50215c883d53ced05034d8434bb6db71bcde6d8`
- 源码事实摘要：Typed, default-unreachable contracts for the minimal vNext live Runtime.
- 顶层符号/结构：`RuntimeContractError`, `_identity`, `_text`, `_ordered_identities`, `_strings`, `_is_safe_action_summary`, `_action_summary_matches`, `_freeze_json`, `_thaw_json`, `_bounded_mapping`, `_canonical_json`, `RuntimeFrameIdentity`, `RuntimeTargetIdentity`, `RuntimeCommitIdentity`，另有 23 项
- 直接静态依赖：`__future__`, `dataclasses`, `enum`, `hashlib`, `json`, `re`, `types`, `typing`, `..capabilities`, `..json_contract`, `..mission`, `.task_state`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/vnext/runtime/mission_runtime.py -->
### `python/winpilot_home/winpilot/vnext/runtime/mission_runtime.py`

[打开文件](<../../python/winpilot_home/winpilot/vnext/runtime/mission_runtime.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：1049 行，40,211 bytes
- SHA-256：`c14ba14a17a053c4347604cdb66c02e7bfc498ab55e5f296cca6b4f88cc55e9f`
- 源码事实摘要：One-turn, event-sourced minimal Mission Runtime.
- 顶层符号/结构：`MissionRuntimeConfigurationError`, `MissionRuntime`, `const:_MAX_NARRATION_CHARS`
- 直接静态依赖：`__future__`, `json`, `threading`, `typing`, `...utils.log`, `..actor`, `..capabilities`, `..evidence`, `..mission`, `..profiling`, `.contracts`, `.ports`, `.task_state`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/vnext/runtime/ports.py -->
### `python/winpilot_home/winpilot/vnext/runtime/ports.py`

[打开文件](<../../python/winpilot_home/winpilot/vnext/runtime/ports.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：64 行，1,542 bytes
- SHA-256：`b5f6511e37300b784fad5892c0ffa532e1d486d454280fdd0c8214ec2c10ae32`
- 源码事实摘要：Injected ports for one minimal Mission Runtime turn.
- 顶层符号/结构：`ActorPort`, `RuntimeStatePort`, `ControllerPort`
- 直接静态依赖：`__future__`, `threading`, `typing`, `..actor`, `..mission`, `.contracts`, `.task_state`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/vnext/runtime/task_state.py -->
### `python/winpilot_home/winpilot/vnext/runtime/task_state.py`

[打开文件](<../../python/winpilot_home/winpilot/vnext/runtime/task_state.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：212 行，6,729 bytes
- SHA-256：`7516a7a891cc9481869c61b75f0a83acfc9f50c4d87daf815b9ad2c04c78483c`
- 源码事实摘要：Small in-memory working state for the single Visual Actor loop.
- 顶层符号/结构：`ActorTaskStateError`, `_scratchpad`, `_remaining_items`, `ActorTaskStateUpdate`, `ActorTaskState`, `ActorTurnDecision`, `const:_SCRATCHPAD_CHARS`, `const:_MAX_REMAINING_ITEMS`, `const:_REMAINING_ITEM_CHARS`, `const:TASK_STATE_UPDATE_SCHEMA`
- 直接静态依赖：`__future__`, `dataclasses`, `types`, `typing`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/vnext/verification/__init__.py -->
### `python/winpilot_home/winpilot/vnext/verification/__init__.py`

[打开文件](<../../python/winpilot_home/winpilot/vnext/verification/__init__.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：19 行，639 bytes
- SHA-256：`eb2140663d99cc37bd6462fc250184bd1689d3cce3983127cc38061af2f9a1d2`
- 源码事实摘要：Pure read-only verification contracts and deterministic evaluators.
- 顶层符号/结构：—
- 直接静态依赖：`.action_effect`, `.contracts`, `.dispatcher`, `.mission_completion`, `.ports`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/vnext/verification/action_effect.py -->
### `python/winpilot_home/winpilot/vnext/verification/action_effect.py`

[打开文件](<../../python/winpilot_home/winpilot/vnext/verification/action_effect.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：420 行，15,429 bytes
- SHA-256：`36acab807aa2a8e572995a6656cfaecd615a36054d24994ceec6dd4cd140367d`
- 源码事实摘要：Deterministic pre/post frame comparison for one accepted GUI action.
- 顶层符号/结构：`_RuntimeFramePort`, `_FrozenFrame`, `ActionEffectVerificationError`, `_text`, `_validate_frame`, `ActionEffectVerificationRequest`, `ActionEffectVerificationResult`, `_decode`, `_comparable`, `_evidence_id`, `DeterministicActionEffectVerifier`, `const:PIXEL_CHANNEL_DELTA_THRESHOLD`, `const:_IDENTITY`, `const:_SHA256`
- 直接静态依赖：`__future__`, `base64`, `dataclasses`, `hashlib`, `json`, `re`, `typing`, `cv2`, `numpy`, `..mission`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/vnext/verification/contracts.py -->
### `python/winpilot_home/winpilot/vnext/verification/contracts.py`

[打开文件](<../../python/winpilot_home/winpilot/vnext/verification/contracts.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：494 行，19,004 bytes
- SHA-256：`16f0f17189a221fa995a3292b78e53478488487f3b28e0229a6579a3c07a2fd0`
- 源码事实摘要：Strict P5a contracts for read-only obligation verification.
- 顶层符号/结构：`VerificationContractError`, `InvalidVerificationSchema`, `_text`, `_integer`, `_literal_bool`, `_confidence`, `_strings`, `_array`, `_wire_text`, `_wire_bool`, `_wire_integer`, `_wire_strings`, `_condition_key`, `conditions_cover`，另有 14 项
- 直接静态依赖：`__future__`, `hashlib`, `json`, `math`, `typing`, `pydantic`, `..json_contract`, `..mission`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/vnext/verification/dispatcher.py -->
### `python/winpilot_home/winpilot/vnext/verification/dispatcher.py`

[打开文件](<../../python/winpilot_home/winpilot/vnext/verification/dispatcher.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：221 行，8,452 bytes
- SHA-256：`221ea67814e80ac03aef5bfe5a9536474bb55402ba72ab0e4c57c70cfa194b80`
- 源码事实摘要：Deterministic-first dispatcher for injected read-only evidence evaluators.
- 顶层符号/结构：`EvidenceEvaluator`, `_RegisteredEvaluator`, `_missing`, `_inconclusive`, `DeterministicFirstVerifier`
- 直接静态依赖：`__future__`, `dataclasses`, `threading`, `typing`, `.contracts`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/vnext/verification/mission_completion.py -->
### `python/winpilot_home/winpilot/vnext/verification/mission_completion.py`

[打开文件](<../../python/winpilot_home/winpilot/vnext/verification/mission_completion.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：349 行，15,344 bytes
- SHA-256：`f9b1878c09bea93192c79f1644102bdb5819a57f81a64fadb50f39b573e1f12a`
- 源码事实摘要：Read-only deterministic P5b Mission-completion verification.
- 顶层符号/结构：`_canonical_json`, `_sha256`, `AcceptedObligationVerification`, `_accepted`, `MissionCompletionVerificationRequest`, `DeterministicMissionCompletionVerifier`, `MissionCompletionVerificationResult`, `const:ACCEPTED_OBLIGATION_VERIFICATION_FORMAT`, `const:MISSION_COMPLETION_REQUEST_FORMAT`, `const:MISSION_COMPLETION_RESULT_FORMAT`, `const:MISSION_COMPLETION_AGGREGATE_FORMAT`, `const:MISSION_COMPLETION_SCOPE`, `const:MISSION_COMPLETION_TRUST`, `const:_DISALLOWED_TRUST`，另有 1 项
- 直接静态依赖：`__future__`, `hashlib`, `json`, `typing`, `pydantic`, `..json_contract`, `..mission`, `.contracts`
- API/IPC 入口：—

<!-- PROJECT-FILE: python/winpilot_home/winpilot/vnext/verification/ports.py -->
### `python/winpilot_home/winpilot/vnext/verification/ports.py`

[打开文件](<../../python/winpilot_home/winpilot/vnext/verification/ports.py>)

- 分类/语言：`winpilot-product` / Python
- 规模：21 行，552 bytes
- SHA-256：`f12c300631bab762f92757d356b0a3087d48cf341a4531ad99acc1ba4360388c`
- 源码事实摘要：Read-only P5a verification ports.
- 顶层符号/结构：`ObligationVerifierPort`
- 直接静态依赖：`__future__`, `threading`, `typing`, `.contracts`
- API/IPC 入口：—

## `requirements.txt`

<!-- PROJECT-FILE: requirements.txt -->
### `requirements.txt`

[打开文件](<../../requirements.txt>)

- 分类/语言：`configuration` / Python requirements
- 规模：43 行，1,565 bytes
- SHA-256：`8c38e8033aac5c516f11722f56f0976308bdee7c9f3a2ca428a2b65ee32f3fff`
- 源码事实摘要：-*- coding: utf-8 -*-
- 顶层符号/结构：—
- 直接静态依赖：—
- API/IPC 入口：—

## `run_mimo_demo.py`

<!-- PROJECT-FILE: run_mimo_demo.py -->
### `run_mimo_demo.py`

[打开文件](<../../run_mimo_demo.py>)

- 分类/语言：`supporting-code` / Python
- 规模：127 行，4,155 bytes
- SHA-256：`fd69a9ea0e2a57e300be2ebcd8398747fd1d1608134fbc1fadcbb8f0ffc4572d`
- 源码事实摘要：MIMO Agent 完整实操：视觉操控 + 每步截屏 + 提取小图
- 顶层符号/结构：`save_screenshot`, `crop_templates`, `event_cb`, `const:SAVE_DIR`
- 直接静态依赖：`sys`, `os`, `json`, `time`, `base64`, `cv2`, `perception.vision`, `agent.mimo_agent`
- API/IPC 入口：—

## `scripts`

<!-- PROJECT-FILE: scripts/clean-artifacts.cjs -->
### `scripts/clean-artifacts.cjs`

[打开文件](<../../scripts/clean-artifacts.cjs>)

- 分类/语言：`developer-script` / CommonJS
- 规模：95 行，2,763 bytes
- SHA-256：`c874c45c20f3bb7f00298b036c2a4f8a1531af96b0406834ba423b1995b339aa`
- 源码事实摘要：!/usr/bin/env node
- 顶层符号/结构：`fs`, `path`, `root`, `includeRuntime`, `rootEntries`, `pycacheRoots`, `insideRoot`, `dirSize`, `removeEntry`, `collectPycache`, `freed`
- 直接静态依赖：`node:fs`, `node:path`
- API/IPC 入口：—

<!-- PROJECT-FILE: scripts/client_architecture_e2e_run.py -->
### `scripts/client_architecture_e2e_run.py`

[打开文件](<../../scripts/client_architecture_e2e_run.py>)

- 分类/语言：`developer-script` / Python
- 规模：154 行，4,406 bytes
- SHA-256：`216320134107cf0842ce7ef99a92a73f87bb1de936e3db51a92f81a0dcf7e75d`
- 源码事实摘要：Visible Electron E2E for the 2026-08-03 architecture gaps.
- 顶层符号/结构：`notepad_window_oracle`, `notepad_process_oracle`, `run_ad_hoc`, `main`, `const:WORKSPACE`, `const:SCRIPT_DIR`, `const:OUTPUT_ROOT`, `const:NOTEPAD_EXECUTABLES`, `const:NOTEPAD_CLASSES`
- 直接静态依赖：`__future__`, `ctypes`, `json`, `os`, `subprocess`, `sys`, `time`, `datetime`, `pathlib`, `psutil`, `win32gui`, `client_e2e_run`
- API/IPC 入口：—

<!-- PROJECT-FILE: scripts/client_cat_qq_3turn_e2e_run.py -->
### `scripts/client_cat_qq_3turn_e2e_run.py`

[打开文件](<../../scripts/client_cat_qq_3turn_e2e_run.py>)

- 分类/语言：`developer-script` / Python
- 规模：609 行，22,309 bytes
- SHA-256：`d088a6feaa3beebe9b43bd3ebde5c0d8a3c13219af957cf5ff30ceb3318755e3`
- 源码事实摘要：Run the exact user-requested 3-turn desktop sequence through the visible client.
- 顶层符号/结构：`_json_safe`, `_write_json`, `_scan_new_images`, `_qq_process_alive`, `_read_claim_texts`, `_read_run_user_result`, `_analyze_trace`, `_count_model_calls`, `_copy_bound_traces`, `run_three_turn_sequence`, `main`, `const:WORKSPACE`, `const:SCRIPT_DIR`, `const:WINPILOT_HOME`，另有 5 项
- 直接静态依赖：`__future__`, `json`, `os`, `shutil`, `sys`, `time`, `collections`, `datetime`, `pathlib`, `typing`, `winpilot.utils.dpi`, `psutil`, `win32gui`, `win32process`，另有 1 项
- API/IPC 入口：—

<!-- PROJECT-FILE: scripts/client_complex_orchestration_e2e_run.py -->
### `scripts/client_complex_orchestration_e2e_run.py`

[打开文件](<../../scripts/client_complex_orchestration_e2e_run.py>)

- 分类/语言：`developer-script` / Python
- 规模：523 行，27,053 bytes
- SHA-256：`5e4436f9797c5a2ff43bf0a0967e6c8c378fd8c3c5801bb692d7f99149a7b58f`
- 源码事实摘要：Run one real long task that forces exploration, recovery, and replanning.
- 顶层符号/结构：`_write`, `_json`, `_jsonl`, `_sha256`, `prepare_fixture`, `verify_fixture`, `observe_live_ui`, `_short`, `_invokes_python_script`, `orchestration_evidence`, `parse_args`, `main`, `const:WORKSPACE`, `const:SCRIPT_DIR`，另有 2 项
- 直接静态依赖：`__future__`, `argparse`, `hashlib`, `json`, `os`, `re`, `shutil`, `subprocess`, `sys`, `time`, `datetime`, `pathlib`, `typing`, `client_e2e_run`
- API/IPC 入口：—

<!-- PROJECT-FILE: scripts/client_e2e_run.py -->
### `scripts/client_e2e_run.py`

[打开文件](<../../scripts/client_e2e_run.py>)

- 分类/语言：`developer-script` / Python
- 规模：2370 行，92,970 bytes
- SHA-256：`2855274e105e5bb630571d030d855af38846408dbd9587f2cd386ff6873de4e9`
- 源码事实摘要：Drive the real Electron client with native mouse and keyboard input.
- 顶层符号/结构：`ClientIdentity`, `_client_identity_audit`, `_slug`, `_visible_descendants`, `_first_visible_descendant`, `_control_value`, `_document_contains_text`, `_type_control_text`, `_toggle_state`, `_toggle`, `_runtime_version_from_label`, `_selected_combobox_label`, `_select_runtime_version`, `_admitted_run_id`，另有 27 项
- 直接静态依赖：`__future__`, `argparse`, `json`, `os`, `re`, `sys`, `time`, `urllib.error`, `urllib.parse`, `urllib.request`, `dataclasses`, `datetime`, `pathlib`, `typing`，另有 6 项
- API/IPC 入口：—

<!-- PROJECT-FILE: scripts/client_hard_tasks_e2e_run.py -->
### `scripts/client_hard_tasks_e2e_run.py`

[打开文件](<../../scripts/client_hard_tasks_e2e_run.py>)

- 分类/语言：`developer-script` / Python
- 规模：1858 行，71,259 bytes
- SHA-256：`bac20c71f432828d5a4569ec8433c0ce8e46a789c2121f0589f9a17db1c6fe32`
- 源码事实摘要：Run the six high-difficulty real desktop tasks through the visible client.
- 顶层符号/结构：`_taskkill`, `_find_window`, `_find_process_window`, `_focus_window`, `_capture_window`, `_capture_rect`, `_launch_app`, `_read_claim_texts`, `_copy_bound_traces`, `_scan_new_images`, `_claim_contains`, `_prepare_quiz`, `_analyze_trace`, `_strategy_hint`，另有 32 项
- 直接静态依赖：`__future__`, `argparse`, `json`, `os`, `random`, `shutil`, `subprocess`, `sys`, `time`, `collections`, `datetime`, `pathlib`, `typing`, `ctypes`，另有 11 项
- API/IPC 入口：—

<!-- PROJECT-FILE: scripts/client_netease_music_e2e_run.py -->
### `scripts/client_netease_music_e2e_run.py`

[打开文件](<../../scripts/client_netease_music_e2e_run.py>)

- 分类/语言：`developer-script` / Python
- 规模：1143 行，38,716 bytes
- SHA-256：`7de53b69c65179d801d68e95ac673b30f6f73ede8f4126123b82883ce3577e99`
- 源码事实摘要：Run one real NetEase Cloud Music task through the visible Electron client.
- 顶层符号/结构：`_json_safe`, `_write_json`, `_normal`, `_root_window`, `_window_identity`, `_focus_window`, `_start_target_handoff_watcher`, `_finish_target_handoff_watcher`, `_new_admitted_run`, `_hold_target_until_admitted`, `IMMDevice`, `IMMDeviceEnumerator`, `IAudioEndpointVolume`, `_default_audio_endpoint`，另有 24 项
- 直接静态依赖：`__future__`, `argparse`, `hashlib`, `json`, `math`, `re`, `shutil`, `sys`, `threading`, `time`, `ctypes`, `ctypes.wintypes`, `datetime`, `pathlib`，另有 10 项
- API/IPC 入口：—

<!-- PROJECT-FILE: scripts/client_paint_game_e2e_run.py -->
### `scripts/client_paint_game_e2e_run.py`

[打开文件](<../../scripts/client_paint_game_e2e_run.py>)

- 分类/语言：`developer-script` / Python
- 规模：892 行，32,799 bytes
- SHA-256：`6a66a4f2dad4f2ec5e12e1837f0fc0d5e490f37a464be4b6e739b9b8620dbee1`
- 源码事实摘要：Run one native Microsoft Paint tic-tac-toe turn through the visible client.
- 顶层符号/结构：`_json_safe`, `_write_json`, `_rgb_array`, `_red_mask`, `_neutral_dark_mask`, `cell_bounds`, `_inset`, `baseline_contract`, `evaluate_board_frame`, `BoardOracle`, `_root_window`, `_window_dpi`, `_scale_logical_pixel`, `logical_relative_bbox_to_physical`，另有 16 项
- 直接静态依赖：`__future__`, `argparse`, `ctypes`, `ctypes.wintypes`, `hashlib`, `json`, `shutil`, `sys`, `time`, `datetime`, `pathlib`, `typing`, `winpilot.utils.dpi`, `cv2`，另有 8 项
- API/IPC 入口：—

<!-- PROJECT-FILE: scripts/client_qq_groups_e2e_run.py -->
### `scripts/client_qq_groups_e2e_run.py`

[打开文件](<../../scripts/client_qq_groups_e2e_run.py>)

- 分类/语言：`developer-script` / Python
- 规模：1630 行，58,007 bytes
- SHA-256：`5e082dff3a79efcb671d80677bd19fe6bc2de93b5c9df234c24eec89ce655a5f`
- 源码事实摘要：Run the real QQ group-summary goal through the visible Electron client.
- 顶层符号/结构：`_json_safe`, `_write_json`, `_normal`, `_clean_label`, `_opaque`, `_root_window`, `_window_identity`, `_focus_window`, `_new_admitted_run`, `_hold_target_until_admitted`, `_rect_list`, `_selection_state`, `_scroll_state`, `snapshot_accessible_state`，另有 30 项
- 直接静态依赖：`__future__`, `argparse`, `contextlib`, `hashlib`, `hmac`, `io`, `json`, `re`, `secrets`, `sys`, `time`, `datetime`, `pathlib`, `typing`，另有 8 项
- API/IPC 入口：—

<!-- PROJECT-FILE: scripts/client_random_tasks_e2e_run.py -->
### `scripts/client_random_tasks_e2e_run.py`

[打开文件](<../../scripts/client_random_tasks_e2e_run.py>)

- 分类/语言：`developer-script` / Python
- 规模：776 行，29,403 bytes
- SHA-256：`0ca3ff70abcef61858dcc58d217719817c10494f9f499677d0d67404c450db6a`
- 源码事实摘要：Run random real desktop tasks through the visible Electron client.
- 顶层符号/结构：`_taskkill`, `_find_window`, `_focus_window`, `_capture_window`, `_uia_visible_texts`, `_launch_app`, `_notepad_file_case`, `_calculator_case`, `_chat_then_task_case`, `_impossible_case`, `_paint_case`, `generate_case`, `prepare_case`, `_target_hwnd_for`，另有 17 项
- 直接静态依赖：`__future__`, `argparse`, `json`, `os`, `random`, `re`, `shutil`, `subprocess`, `sys`, `time`, `winreg`, `datetime`, `pathlib`, `typing`，另有 6 项
- API/IPC 入口：—

<!-- PROJECT-FILE: scripts/client_thread_connector_e2e_run.py -->
### `scripts/client_thread_connector_e2e_run.py`

[打开文件](<../../scripts/client_thread_connector_e2e_run.py>)

- 分类/语言：`developer-script` / Python
- 规模：2886 行，111,589 bytes
- SHA-256：`27de9dff257819394e938f34b601d114531139583fb5de70cc02f1600ec45152`
- 源码事实摘要：Exercise one real Electron Thread with chat, a connector, and Notepad.
- 顶层符号/结构：`_sha256_text`, `_history_projection_sha256`, `connector_read_task`, `connector_to_notepad_task`, `connector_to_current_notepad_task`, `_write_json`, `FixtureEnvironment`, `build_fixture_environment`, `read_invocation_evidence`, `_read_connector_probe_events`, `read_connector_probe_evidence`, `read_history_projection_evidence`, `history_text_only_evidence`, `_runtime_json`，另有 46 项
- 直接静态依赖：`__future__`, `argparse`, `contextlib`, `hashlib`, `io`, `json`, `os`, `random`, `secrets`, `shutil`, `subprocess`, `sys`, `tempfile`, `time`，另有 12 项
- API/IPC 入口：—

<!-- PROJECT-FILE: scripts/client_visual_e2e_run.py -->
### `scripts/client_visual_e2e_run.py`

[打开文件](<../../scripts/client_visual_e2e_run.py>)

- 分类/语言：`developer-script` / Python
- 规模：1136 行，45,416 bytes
- SHA-256：`51b89d65ea2e609bcfa4992f95a46390cf24d129f40ca19f1db15bf2699a5fa6`
- 源码事实摘要：Run generic visual gestures through the visible Electron client.
- 顶层符号/结构：`_free_port`, `_json_get`, `_wait_json`, `_browser_executable`, `_find_window`, `_focus_window`, `_capture_window`, `_terminate_tree`, `_calibration_summary`, `external_verdict`, `_client_case_spec`, `run_visual_case`, `parse_args`, `main`，另有 8 项
- 直接静态依赖：`__future__`, `argparse`, `ctypes`, `json`, `os`, `shutil`, `socket`, `subprocess`, `sys`, `tempfile`, `time`, `urllib.request`, `uuid`, `datetime`，另有 8 项
- API/IPC 入口：—

<!-- PROJECT-FILE: scripts/client_vnext_canary_e2e_run.py -->
### `scripts/client_vnext_canary_e2e_run.py`

[打开文件](<../../scripts/client_vnext_canary_e2e_run.py>)

- 分类/语言：`developer-script` / Python
- 规模：4421 行，166,625 bytes
- SHA-256：`f807a61caf771ff0129c8b7dd5542ea69cb9279292675c16e03d276c4262f904`
- 源码事实摘要：Run the immutable paired legacy/vNext canary through the visible Electron UI.
- 顶层符号/结构：`DesktopSuiteMutex`, `_utc_now`, `_run_text`, `_run_bytes`, `_git`, `_gh_json`, `_repo_slug`, `_remote_ref`, `_pr_identity`, `_c4_pr_identity`, `_workspace_electron_processes`, `_write_config_bytes`, `_fixed_config`, `_restore_config_bytes`，另有 76 项
- 直接静态依赖：`__future__`, `argparse`, `copy`, `ctypes`, `hashlib`, `json`, `math`, `os`, `re`, `shutil`, `socket`, `subprocess`, `sys`, `tempfile`，另有 16 项
- API/IPC 入口：—

<!-- PROJECT-FILE: scripts/connector_e2e_fixture.py -->
### `scripts/connector_e2e_fixture.py`

[打开文件](<../../scripts/connector_e2e_fixture.py>)

- 分类/语言：`developer-script` / Python
- 规模：112 行，3,083 bytes
- SHA-256：`262166774c04efe6e0dd923debe046425920f0284dea0c966ad961a14e14ec79`
- 源码事实摘要：One-tool stdio MCP fixture for the generic connector Electron E2E.
- 顶层符号/结构：`_required_env`, `_append_oracle`, `read_value`, `main`, `const:TOKEN_ENV`, `const:ORACLE_ENV`, `const:FIXTURE_KEY`
- 直接静态依赖：`__future__`, `hashlib`, `json`, `os`, `time`, `pathlib`, `typing`, `mcp.server.fastmcp`, `mcp.types`
- API/IPC 入口：—

<!-- PROJECT-FILE: scripts/connector_e2e_sitecustomize.py -->
### `scripts/connector_e2e_sitecustomize.py`

[打开文件](<../../scripts/connector_e2e_sitecustomize.py>)

- 分类/语言：`developer-script` / Python
- 规模：274 行，10,141 bytes
- SHA-256：`157a65412a63a8a164bd97d22578731d342337decbc5c978b2c2633750e790fd`
- 源码事实摘要：Test-only connector trace sidecar loaded as ``sitecustomize``.
- 顶层符号/结构：`_member`, `_sha256`, `_append`, `_install`, `const:PROBE_ENV`, `const:PROBE_FORMAT`, `const:_LOCK`
- 直接静态依赖：`__future__`, `hashlib`, `json`, `os`, `sys`, `threading`, `time`, `pathlib`, `typing`
- API/IPC 入口：—

<!-- PROJECT-FILE: scripts/eval_summary.py -->
### `scripts/eval_summary.py`

[打开文件](<../../scripts/eval_summary.py>)

- 分类/语言：`developer-script` / Python
- 规模：92 行，3,592 bytes
- SHA-256：`18644385d98b2d8b0e5198f7f41f0154747f0ec5edf55fc74ee4eb24035f85ce`
- 源码事实摘要：Print a compact summary of recent WinPilot GUI eval reports.
- 顶层符号/结构：`_fmt_cost`, `_usage`, `main`, `const:REPO_ROOT`, `const:WINPILOT_PY`
- 直接静态依赖：`__future__`, `argparse`, `json`, `sys`, `pathlib`, `typing`, `winpilot.eval`
- API/IPC 入口：—

<!-- PROJECT-FILE: scripts/export_run_log.py -->
### `scripts/export_run_log.py`

[打开文件](<../../scripts/export_run_log.py>)

- 分类/语言：`developer-script` / Python
- 规模：354 行，13,185 bytes
- SHA-256：`2eeb4aba9250204f3ab8cb005dabfc7cc03d09e4a3744fb13ee0039effff2ce3`
- 源码事实摘要：Export one Agent run trace as a readable troubleshooting report.
- 顶层符号/结构：`_load_trace`, `_fmt_ts`, `_find_runs`, `_safe_text`, `render_report`, `export_e2e_suite`, `main`, `const:WORKSPACE`, `const:TRACE_ROOT`, `const:DEFAULT_OUTPUT`, `const:E2E_ROOT`
- 直接静态依赖：`__future__`, `argparse`, `glob`, `json`, `os`, `shutil`, `datetime`, `pathlib`, `typing`
- API/IPC 入口：—

<!-- PROJECT-FILE: scripts/generate_project_guide.py -->
### `scripts/generate_project_guide.py`

[打开文件](<../../scripts/generate_project_guide.py>)

- 分类/语言：`developer-script` / Python
- 规模：555 行，19,596 bytes
- SHA-256：`6e8b9e5f72bfff5b95229137cbf1d8bf25f4f949342298974ab53407329b2ca3`
- 源码事实摘要：Generate and verify the exhaustive, source-backed project file inventory.
- 顶层符号/结构：`FileFacts`, `_git_paths`, `_is_code_or_config`, `_category`, `_language`, `_decode_text`, `_first_nonempty_line`, `_python_facts`, `_render_python_route`, `_json_facts`, `_ts_facts`, `_png_dimensions`, `_fallback_summary`, `_facts`，另有 12 项
- 直接静态依赖：`__future__`, `argparse`, `ast`, `collections`, `dataclasses`, `hashlib`, `json`, `pathlib`, `re`, `struct`, `subprocess`, `sys`, `typing`
- API/IPC 入口：—

<!-- PROJECT-FILE: scripts/gui_eval_run.py -->
### `scripts/gui_eval_run.py`

[打开文件](<../../scripts/gui_eval_run.py>)

- 分类/语言：`developer-script` / Python
- 规模：2226 行，97,777 bytes
- SHA-256：`484ce8ab42211d91554f12d2a5534b5360d4dca4d228437336415a3388042a1a`
- 源码事实摘要：Run one real WinPilot GUI task and write a compact evidence report.
- 顶层符号/结构：`_pick`, `_endpoint_fingerprint`, `_canonical_model_name`, `_model_identity`, `_model_metadata`, `_git_metadata`, `_display_metadata`, `_collect_provenance`, `_latest_target_hwnd`, `_explicit_region`, `_resolve_visual_region`, `_report_file_path`, `_capture_visual_judge_frame`, `_visual_judge_settings`，另有 35 项
- 直接静态依赖：`__future__`, `argparse`, `base64`, `ctypes`, `hashlib`, `json`, `math`, `os`, `platform`, `re`, `subprocess`, `sys`, `time`, `urllib.error`，另有 4 项
- API/IPC 入口：—

<!-- PROJECT-FILE: scripts/probe_runtime.py -->
### `scripts/probe_runtime.py`

[打开文件](<../../scripts/probe_runtime.py>)

- 分类/语言：`developer-script` / Python
- 规模：31 行，844 bytes
- SHA-256：`8295e39bcccfabefaa1d400e39cdd0902c5613307407c5ce99a2ada18ec73fc9`
- 源码事实摘要：Probe the running Electron client and managed runtime state.
- 顶层符号/结构：`main`, `const:WORKSPACE`, `const:SCRIPT_DIR`
- 直接静态依赖：`__future__`, `json`, `sys`, `pathlib`, `winpilot.utils.dpi`, `client_e2e_run`
- API/IPC 入口：—

<!-- PROJECT-FILE: scripts/setup-claude-code.js -->
### `scripts/setup-claude-code.js`

[打开文件](<../../scripts/setup-claude-code.js>)

- 分类/语言：`developer-script` / JavaScript
- 规模：140 行，5,776 bytes
- SHA-256：`3f5ed20d686a5d5ff87da10b1fe3ba55a159310a994e55e84c949709ff197deb`
- 源码事实摘要：!/usr/bin/env node
- 顶层符号/结构：`path`, `fs`, `PROJECT_ROOT`, `SERVER_SCRIPT`, `getMCPConfig`, `getProjectMCPConfig`
- 直接静态依赖：`path`, `fs`, `os`
- API/IPC 入口：—

<!-- PROJECT-FILE: scripts/vnext_canary_campaign.py -->
### `scripts/vnext_canary_campaign.py`

[打开文件](<../../scripts/vnext_canary_campaign.py>)

- 分类/语言：`developer-script` / Python
- 规模：4963 行，184,818 bytes
- SHA-256：`bcbee216ca3c3721e3370883ce48cdf6af86e0362753aa8f6abc61c7521cb89f`
- 源码事实摘要：Fail-closed state and evidence primitives for the vNext live canary.
- 顶层符号/结构：`CampaignStateError`, `CampaignIntegrityError`, `CampaignTransitionError`, `CampaignAlreadyExistsError`, `ImmutableWriteResult`, `utc_now`, `_json_value`, `canonical_json_bytes`, `content_sha256`, `with_content_hash`, `validate_content_hash`, `sha256_file`, `tree_identity`, `sha256_tree`，另有 95 项
- 直接静态依赖：`__future__`, `collections.abc`, `copy`, `dataclasses`, `datetime`, `hashlib`, `json`, `math`, `os`, `pathlib`, `re`, `threading`, `typing`
- API/IPC 入口：—

<!-- PROJECT-FILE: scripts/vnext_canary_input_hook.py -->
### `scripts/vnext_canary_input_hook.py`

[打开文件](<../../scripts/vnext_canary_input_hook.py>)

- 分类/语言：`developer-script` / Python
- 规模：766 行，25,948 bytes
- SHA-256：`cd53da97b2b6c57e45bc22c1f585d5f33445fc790797fc0556ffb35ea2d3f62f`
- 源码事实摘要：Passive, in-memory Windows input telemetry for the vNext canary.
- 顶层符号/结构：`_MSLLHOOKSTRUCT`, `_KBDLLHOOKSTRUCT`, `_strict_int`, `_timeout`, `_signed_high_word`, `_unsigned_high_word`, `mouse_event_from_raw`, `keyboard_event_from_raw`, `_WindowsHookApi`, `PassiveInputHook`, `const:WH_KEYBOARD_LL`, `const:WH_MOUSE_LL`, `const:HC_ACTION`, `const:WM_QUIT`，另有 4 项
- 直接静态依赖：`__future__`, `collections`, `copy`, `ctypes`, `math`, `os`, `threading`, `time`, `typing`
- API/IPC 入口：—

## `src`

<!-- PROJECT-FILE: src/ai-interface/index.ts -->
### `src/ai-interface/index.ts`

[打开文件](<../../src/ai-interface/index.ts>)

- 分类/语言：`electron-or-typescript-product` / TypeScript
- 规模：88 行，3,115 bytes
- SHA-256：`c541b28c1c9f00b6c336f5dd01fd6f79bdbdf21712daee4913a783e1bc3dfb9c`
- 源码事实摘要：灵动脚本 AI Interface — 标准工具接口入口
- 顶层符号/结构：`parseArgs`, `main`
- 直接静态依赖：`./tools/registry`, `./tools/run-flow`, `./tools/list-flows`, `./tools/create-flow`, `./tools/ask-ai`, `./tools/screen-capture`, `./server/mcp`, `./server/http`, `./tools/types`
- API/IPC 入口：—

<!-- PROJECT-FILE: src/ai-interface/prompt/index.ts -->
### `src/ai-interface/prompt/index.ts`

[打开文件](<../../src/ai-interface/prompt/index.ts>)

- 分类/语言：`electron-or-typescript-product` / TypeScript
- 规模：137 行，4,906 bytes
- SHA-256：`c669ee88d2c1e46ee5f87cb0196f3b3edebb59fba7b8dc073c79f296d987afe8`
- 源码事实摘要：灵动脚本 AI Prompt 系统
- 顶层符号/结构：`PromptLevel`, `PRIORITY_RULES`, `getSystemPrompt`, `getToolDefinitionsJSON`, `getMCPToolList`, `generateMCPConfig`
- 直接静态依赖：`../tools/registry`
- API/IPC 入口：—

<!-- PROJECT-FILE: src/ai-interface/server/http.ts -->
### `src/ai-interface/server/http.ts`

[打开文件](<../../src/ai-interface/server/http.ts>)

- 分类/语言：`electron-or-typescript-product` / TypeScript
- 规模：267 行，9,275 bytes
- SHA-256：`3a23816ea86525531382b3cdfa21a3561f33a056d7641f33c79692f287cb70e4`
- 源码事实摘要：HTTP API Server — 通用 REST API（任何 AI / HTTP 客户端都可以调用）
- 顶层符号/结构：`currentPromptLevel`, `jsonResponse`, `parseBody`, `startHTTPServer`
- 直接静态依赖：`http`, `crypto`, `../tools/registry`, `../prompt`, `../tools/types`
- API/IPC 入口：—

<!-- PROJECT-FILE: src/ai-interface/server/mcp.ts -->
### `src/ai-interface/server/mcp.ts`

[打开文件](<../../src/ai-interface/server/mcp.ts>)

- 分类/语言：`electron-or-typescript-product` / TypeScript
- 规模：130 行，3,366 bytes
- SHA-256：`e293739f0a62854304de7c5d715813d2e0057d1611c10a1e343022a7fc1e0023`
- 源码事实摘要：MCP stdio Server — Model Context Protocol 实现
- 顶层符号/结构：`startMCPServer`, `sendResponse`, `sendError`
- 直接静态依赖：`readline`, `../tools/registry`, `../tools/types`
- API/IPC 入口：—

<!-- PROJECT-FILE: src/ai-interface/tools/ask-ai.ts -->
### `src/ai-interface/tools/ask-ai.ts`

[打开文件](<../../src/ai-interface/tools/ask-ai.ts>)

- 分类/语言：`electron-or-typescript-product` / TypeScript
- 规模：178 行，5,375 bytes
- SHA-256：`4175ea660e435e39b918defea6a69b10ad326d10f7a4409238c1b5fc0a1fc649`
- 源码事实摘要：ask-ai Tool — 用自然语言描述需求，AI 自动生成并执行脚本
- 顶层符号/结构：`API_BASE`, `MODEL`, `API_PATH`, `SYSTEM_PROMPT`, `callDeepSeek`, `askAITool`
- 直接静态依赖：`./registry`, `./run-flow`
- API/IPC 入口：—

<!-- PROJECT-FILE: src/ai-interface/tools/create-flow.ts -->
### `src/ai-interface/tools/create-flow.ts`

[打开文件](<../../src/ai-interface/tools/create-flow.ts>)

- 分类/语言：`electron-or-typescript-product` / TypeScript
- 规模：80 行，2,902 bytes
- SHA-256：`ad6d247f960dd86821f60c1784500260d1a16ca3a5a278e6a8d35bc22f005416`
- 源码事实摘要：create-flow Tool — 创建新的 .flow 脚本
- 顶层符号/结构：`createFlowTool`
- 直接静态依赖：`fs`, `path`, `./registry`, `../../renderer/utils/pseudoParser`
- API/IPC 入口：—

<!-- PROJECT-FILE: src/ai-interface/tools/list-flows.ts -->
### `src/ai-interface/tools/list-flows.ts`

[打开文件](<../../src/ai-interface/tools/list-flows.ts>)

- 分类/语言：`electron-or-typescript-product` / TypeScript
- 规模：71 行，2,508 bytes
- SHA-256：`eb16b965e0fa31e83ffb331b7c564cc05cd0e20db4dbc5b79402fb7d86868dd6`
- 源码事实摘要：list-flows Tool — 列出可用脚本
- 顶层符号/结构：`listFlowsTool`
- 直接静态依赖：`fs`, `path`, `./registry`, `../../renderer/utils/pseudoParser`
- API/IPC 入口：—

<!-- PROJECT-FILE: src/ai-interface/tools/registry.ts -->
### `src/ai-interface/tools/registry.ts`

[打开文件](<../../src/ai-interface/tools/registry.ts>)

- 分类/语言：`electron-or-typescript-product` / TypeScript
- 规模：101 行，2,991 bytes
- SHA-256：`27a5d0b878f71b19cc9899b936a0ab883797af46d885b38f091346a5aa66fbed`
- 源码事实摘要：灵动脚本 Tool Registry — 所有工具的注册中心
- 顶层符号/结构：`ToolRegistry`, `toolRegistry`, `createTool`
- 直接静态依赖：`./types`
- API/IPC 入口：—

<!-- PROJECT-FILE: src/ai-interface/tools/run-flow.ts -->
### `src/ai-interface/tools/run-flow.ts`

[打开文件](<../../src/ai-interface/tools/run-flow.ts>)

- 分类/语言：`electron-or-typescript-product` / TypeScript
- 规模：142 行，5,176 bytes
- SHA-256：`b235bbfcc7a907942f563963b62fafeddf611f8181d2223da1e90534c6cf6637`
- 源码事实摘要：run-flow Tool — 执行 .flow 脚本或内联伪代码
- 顶层符号/结构：`findEngine`, `parsePseudoToFlow`, `executeScript`, `runFlowTool`
- 直接静态依赖：`fs`, `path`, `child_process`, `./registry`, `../../renderer/utils/pseudoParser`
- API/IPC 入口：—

<!-- PROJECT-FILE: src/ai-interface/tools/screen-capture.ts -->
### `src/ai-interface/tools/screen-capture.ts`

[打开文件](<../../src/ai-interface/tools/screen-capture.ts>)

- 分类/语言：`electron-or-typescript-product` / TypeScript
- 规模：284 行，10,438 bytes
- SHA-256：`dedd4fa2f17ef2f46e27b0dd4ef568074e9ffb1e35d8cb35ab4496cd0eb80a2c`
- 源码事实摘要：screen_capture Tool — 截图选区工具
- 顶层符号/结构：`getElectronPath`, `getMainJSPath`, `spawnSelectionOverlay`, `path`, `fs`, `RESULT_FILE`, `c`, `img`, `sx`, `init`, `draw`, `confirm`, `screenCaptureTool`
- 直接静态依赖：`fs`, `path`, `child_process`, `./registry`, `os`, `electron`
- API/IPC 入口：`IPC ss:cancel`, `IPC ss:done`, `IPC ss:cropped`

<!-- PROJECT-FILE: src/ai-interface/tools/types.ts -->
### `src/ai-interface/tools/types.ts`

[打开文件](<../../src/ai-interface/tools/types.ts>)

- 分类/语言：`electron-or-typescript-product` / TypeScript
- 规模：87 行，2,215 bytes
- SHA-256：`8817f1aa1173ef9c6eef2f27f258f1a45c9fde97cd4bcb14c955e3b6572af217`
- 源码事实摘要：灵动脚本 AI Interface — 核心类型定义
- 顶层符号/结构：`ToolParameterProperty`, `ToolInputSchema`, `ToolDefinition`, `ToolResult`, `ToolHandler`, `Tool`, `MCPRequest`, `MCPResponse`, `APIResponse`, `ServerMode`
- 直接静态依赖：—
- API/IPC 入口：—

<!-- PROJECT-FILE: src/cli/commands/ask.ts -->
### `src/cli/commands/ask.ts`

[打开文件](<../../src/cli/commands/ask.ts>)

- 分类/语言：`electron-or-typescript-product` / TypeScript
- 规模：30 行，838 bytes
- SHA-256：`56392a5165f87132a04414bd3c63c5dd93b332205bdb0b73c136a39963c59e02`
- 源码事实摘要：实现或支撑 ask 模块；精确公开面见下列静态符号与依赖。
- 顶层符号/结构：`AskOptions`, `askCommand`
- 直接静态依赖：`../deepseek`, `./run`
- API/IPC 入口：—

<!-- PROJECT-FILE: src/cli/commands/create.ts -->
### `src/cli/commands/create.ts`

[打开文件](<../../src/cli/commands/create.ts>)

- 分类/语言：`electron-or-typescript-product` / TypeScript
- 规模：60 行，1,772 bytes
- SHA-256：`5a4d635628a164131375a4a033b79cb40c0719df51471243969d050b711a9fef`
- 源码事实摘要：实现或支撑 create 模块；精确公开面见下列静态符号与依赖。
- 顶层符号/结构：`createCommand`, `readStdin`
- 直接静态依赖：`fs`, `path`, `../../renderer/utils/pseudoParser`
- API/IPC 入口：—

<!-- PROJECT-FILE: src/cli/commands/list.ts -->
### `src/cli/commands/list.ts`

[打开文件](<../../src/cli/commands/list.ts>)

- 分类/语言：`electron-or-typescript-product` / TypeScript
- 规模：82 行，2,556 bytes
- SHA-256：`1b9976fd852b34aaa820b2745e47e18e467f5a36d1057fb9832d6ebbb3feea27`
- 源码事实摘要：实现或支撑 list 模块；精确公开面见下列静态符号与依赖。
- 顶层符号/结构：`ListOptions`, `ScriptInfo`, `listCommand`
- 直接静态依赖：`fs`, `path`, `../../renderer/utils/pseudoParser`
- API/IPC 入口：—

<!-- PROJECT-FILE: src/cli/commands/run.ts -->
### `src/cli/commands/run.ts`

[打开文件](<../../src/cli/commands/run.ts>)

- 分类/语言：`electron-or-typescript-product` / TypeScript
- 规模：105 行，3,333 bytes
- SHA-256：`e8e0c1209ee02c300253e991be6d0f1a49d0403b7dc945f31bb1c53a8d4c3fe3`
- 源码事实摘要：实现或支撑 run 模块；精确公开面见下列静态符号与依赖。
- 顶层符号/结构：`PSEUDO_EXTS`, `runCommand`, `runCommandInline`, `parsePseudoToFlow`, `spawnEngine`, `findEngine`
- 直接静态依赖：`fs`, `path`, `child_process`, `../../renderer/utils/pseudoParser`
- API/IPC 入口：—

<!-- PROJECT-FILE: src/cli/commands/serve.ts -->
### `src/cli/commands/serve.ts`

[打开文件](<../../src/cli/commands/serve.ts>)

- 分类/语言：`electron-or-typescript-product` / TypeScript
- 规模：177 行，6,835 bytes
- SHA-256：`c937909ecd99767bf3110c3e20116bf074490450c1d02f52369800049b837c51`
- 源码事实摘要：实现或支撑 serve 模块；精确公开面见下列静态符号与依赖。
- 顶层符号/结构：`serveCommand`, `readBody`, `findEngine`
- 直接静态依赖：`http`, `child_process`, `path`, `fs`, `crypto`, `../../renderer/utils/pseudoParser`
- API/IPC 入口：—

<!-- PROJECT-FILE: src/cli/deepseek.ts -->
### `src/cli/deepseek.ts`

[打开文件](<../../src/cli/deepseek.ts>)

- 分类/语言：`electron-or-typescript-product` / TypeScript
- 规模：133 行，4,004 bytes
- SHA-256：`d2ba2bb473a4cf62e9b6927829e52872ba0d2b4ba2338e3510e35a6e4a2a3a73`
- 源码事实摘要：通过环境变量配置，支持任意 OpenAI 兼容 API
- 顶层符号/结构：`API_BASE`, `MODEL`, `API_PATH`, `SYSTEM_PROMPT`, `DeepSeekResult`, `callDeepSeek`
- 直接静态依赖：—
- API/IPC 入口：—

<!-- PROJECT-FILE: src/cli/index.ts -->
### `src/cli/index.ts`

[打开文件](<../../src/cli/index.ts>)

- 分类/语言：`electron-or-typescript-product` / TypeScript
- 规模：133 行，5,152 bytes
- SHA-256：`05f0c20e77858e9ef37ef90ff93c3d7c97dcdda3e1a468a8736a655b4a5d6c40`
- 源码事实摘要：!/usr/bin/env node
- 顶层符号/结构：`args`, `pretty`, `help`, `cmd`, `main`
- 直接静态依赖：`./commands/run`, `./commands/list`, `./commands/serve`, `./commands/create`, `./commands/ask`
- API/IPC 入口：—

<!-- PROJECT-FILE: src/main/api.ts -->
### `src/main/api.ts`

[打开文件](<../../src/main/api.ts>)

- 分类/语言：`electron-or-typescript-product` / TypeScript
- 规模：573 行，27,182 bytes
- SHA-256：`e9c8b58a938f125b8294386760014223a1c825120ef66079c8621d0296029279`
- 源码事实摘要：HTTP API 服务器（提取自 main.ts）
- 顶层符号/结构：`ApiDeps`, `API_PORT`, `startApiServer`
- 直接静态依赖：`http`, `path`, `fs`, `crypto`, `child_process`, `./engine`, `./env`, `./featureCallBridge`, `./mcp-handler`, `./macroPaths`
- API/IPC 入口：—

<!-- PROJECT-FILE: src/main/cronMatcher.ts -->
### `src/main/cronMatcher.ts`

[打开文件](<../../src/main/cronMatcher.ts>)

- 分类/语言：`electron-or-typescript-product` / TypeScript
- 规模：66 行，2,209 bytes
- SHA-256：`ec5bb4c612c644bcec160c7e5e86ff9f3541a1e6037310fe5ca2fc8f9ed4c82b`
- 源码事实摘要：简易 cron 表达式匹配器
- 顶层符号/结构：`matchField`, `matchCron`
- 直接静态依赖：—
- API/IPC 入口：—

<!-- PROJECT-FILE: src/main/engine.ts -->
### `src/main/engine.ts`

[打开文件](<../../src/main/engine.ts>)

- 分类/语言：`electron-or-typescript-product` / TypeScript
- 规模：193 行，6,521 bytes
- SHA-256：`48497239d151160fa5ff50593578911f3ad661e25619d434e16beb301c1308f6`
- 源码事实摘要：Python 引擎管理 — spawn、DSL 执行、LLM 调用
- 顶层符号/结构：`getPythonPath`, `_vlmBackend`, `setVlmBackend`, `getVlmBackend`, `spawnPython`, `MAX_CONCURRENT`, `_activeOneShot`, `_oneShotQueue`, `_drainOneShot`, `spawnPythonOneShot`, `runPythonCollect`, `_engineLineBuffer`, `startPythonEngine`, `stopPythonEngine`，另有 1 项
- 直接静态依赖：`child_process`, `path`, `fs`, `http`, `https`, `./state`, `./env`
- API/IPC 入口：—

<!-- PROJECT-FILE: src/main/env.ts -->
### `src/main/env.ts`

[打开文件](<../../src/main/env.ts>)

- 分类/语言：`electron-or-typescript-product` / TypeScript
- 规模：13 行，462 bytes
- SHA-256：`85657a4c363caa8e904e0a1e7f21aad66bde3beb64d91a4bd7693c2fe80ff3ff`
- 源码事实摘要：环境配置 — isDev / getBaseDir
- 顶层符号/结构：`isDev`, `getBaseDir`
- 直接静态依赖：`path`, `electron`
- API/IPC 入口：—

<!-- PROJECT-FILE: src/main/featureCallBridge.ts -->
### `src/main/featureCallBridge.ts`

[打开文件](<../../src/main/featureCallBridge.ts>)

- 分类/语言：`electron-or-typescript-product` / TypeScript
- 规模：122 行，3,413 bytes
- SHA-256：`3d6368c513483dd708ead03da0d4a328103cdfab08687b7a2e5f75dffdc9466c`
- 源码事实摘要：持久化 feature_call.py 子进程管理。
- 顶层符号/结构：`_process`, `_stdoutBuffer`, `_pending`, `_ready`, `_pendingLines`, `_processStdout`, `_ensureProcess`, `callFeature`, `isFeatureProcessReady`, `stopFeatureProcess`
- 直接静态依赖：`child_process`, `path`, `./engine`, `./env`
- API/IPC 入口：—

<!-- PROJECT-FILE: src/main/macroPaths.ts -->
### `src/main/macroPaths.ts`

[打开文件](<../../src/main/macroPaths.ts>)

- 分类/语言：`electron-or-typescript-product` / TypeScript
- 规模：17 行，822 bytes
- SHA-256：`98645f35d4dfb3b549d88204930198fa2d063768960197ccfabad5dda3543415`
- 源码事实摘要：实现或支撑 macroPaths 模块；精确公开面见下列静态符号与依赖。
- 顶层符号/结构：`resolveMacroPath`
- 直接静态依赖：`path`
- API/IPC 入口：—

<!-- PROJECT-FILE: src/main/main.ts -->
### `src/main/main.ts`

[打开文件](<../../src/main/main.ts>)

- 分类/语言：`electron-or-typescript-product` / TypeScript
- 规模：1611 行，61,489 bytes
- SHA-256：`af24bd3c9930504692ca6d18d6b8a258f6e6aaff124ac7c4247fbb54243f8376`
- 源码事实摘要：﻿import { app, BrowserWindow, globalShortcut, ipcMain, dialog, desktopCapturer, Menu, Notification } from 'electron';
- 顶层符号/结构：`fsp`, `_cachedRendererState`, `apiServer`, `LOCAL_CONTROL_TOKEN`, `_taskbarNotifiedRuns`, `_desktopHandoffPending`, `_desktopHandoffRunId`, `_floatingWindowNormalBounds`, `_screenRegionSelectionActive`, `TaskbarNotificationKind`, `releaseDesktopHandoff`, `notifyTaskbarAttention`, `getTemplatesDir`, `loadTemplateContent`，另有 39 项
- 直接静态依赖：`electron`, `path`, `fs`, `os`, `crypto`, `child_process`, `http`, `https`, `./schedulerBridge`, `./engine`, `./env`, `./api`, `./macroPaths`, `./featureCallBridge`，另有 2 项
- API/IPC 入口：`IPC user-activity`, `IPC state:response`, `IPC window:set-titlebar-theme`, `IPC window:minimize`, `IPC window:toggle-maximize`, `IPC window:close`, `IPC window:prepare-desktop-handoff`, `IPC window:bind-desktop-handoff`, `IPC window:release-desktop-handoff`, `IPC dialog:openFile`, `IPC dialog:saveFile`, `IPC file:read`, `IPC file:write`, `IPC engine:start`，另有 34 项

<!-- PROJECT-FILE: src/main/mcp-handler.ts -->
### `src/main/mcp-handler.ts`

[打开文件](<../../src/main/mcp-handler.ts>)

- 分类/语言：`electron-or-typescript-product` / TypeScript
- 规模：207 行，7,577 bytes
- SHA-256：`6088b85f6e9101cb9038a99da648bce53e9dd2636d6a102f0a4b42c69c6e4616`
- 源码事实摘要：MCP over HTTP — 使 :5877 API 服务器兼作 MCP HTTP 端点
- 顶层符号/结构：`MCPTool`, `MCPResult`, `_toolsCache`, `_pythonAvailable`, `checkPython`, `fetchTools`, `callTool`, `rpc`, `maybeSse`, `handleMCPRequest`, `clearToolsCache`
- 直接静态依赖：`path`, `fs`, `./engine`, `./env`
- API/IPC 入口：—

<!-- PROJECT-FILE: src/main/preload.ts -->
### `src/main/preload.ts`

[打开文件](<../../src/main/preload.ts>)

- 分类/语言：`electron-or-typescript-product` / TypeScript
- 规模：168 行，7,071 bytes
- SHA-256：`b2bff06bf5cf56ebe5ecddc8f45f6f3bb98fe81b4b4599e0efdd8c9373a73945`
- 源码事实摘要：实现或支撑 preload 模块；精确公开面见下列静态符号与依赖。
- 顶层符号/结构：—
- 直接静态依赖：`electron`
- API/IPC 入口：`IPC dialog:openFile`, `IPC dialog:saveFile`, `IPC file:read`, `IPC file:write`, `IPC engine:start`, `IPC engine:stop`, `IPC engine:pause`, `IPC engine:resume`, `IPC engine:status`, `IPC engine-message`, `IPC engine-log`, `IPC winpilot:url`, `IPC winpilot:connection`, `IPC winpilot:ensure`，另有 38 项

<!-- PROJECT-FILE: src/main/schedulerBridge.ts -->
### `src/main/schedulerBridge.ts`

[打开文件](<../../src/main/schedulerBridge.ts>)

- 分类/语言：`electron-or-typescript-product` / TypeScript
- 规模：209 行，6,440 bytes
- SHA-256：`50abd30ca198f99a5099243189a60f99dadd980d6e793e784d2ffa1420f578d4`
- 源码事实摘要：调度器守护进程桥接 — 管理持久化 Python 调度器进程
- 顶层符号/结构：`SchedulerEvent`, `SchedulerBridge`, `getSchedulerScriptPath`, `getPythonCommand`
- 直接静态依赖：`child_process`, `path`, `fs`, `events`, `./env`
- API/IPC 入口：—

<!-- PROJECT-FILE: src/main/state.ts -->
### `src/main/state.ts`

[打开文件](<../../src/main/state.ts>)

- 分类/语言：`electron-or-typescript-product` / TypeScript
- 规模：29 行，1,135 bytes
- SHA-256：`522ba34af221b07b709bb1daaa4f2fddf8a436b9edf15b871805b62fae1611fa`
- 源码事实摘要：共享全局状态（替代 main.ts 中的 module-level let 变量）
- 顶层符号/结构：`mainWindow`, `setMainWindow`, `pythonProcess`, `setPythonProcess`, `engineRunning`, `setEngineRunning`, `lastScript`, `setLastScript`, `voiceServerProcess`, `setVoiceServerProcess`, `voiceServerPort`, `setVoiceServerPort`
- 直接静态依赖：`electron`, `child_process`
- API/IPC 入口：—

<!-- PROJECT-FILE: src/main/winpilotServer.ts -->
### `src/main/winpilotServer.ts`

[打开文件](<../../src/main/winpilotServer.ts>)

- 分类/语言：`electron-or-typescript-product` / TypeScript
- 规模：354 行，12,720 bytes
- SHA-256：`ec70a0451eba650e8ab2a784ead4ae951618ef10a11db99ab99aa91e017c5c20`
- 源码事实摘要：WinPilot server lifecycle — spawns the WinPilot FastAPI/uvicorn server as a
- 顶层符号/结构：`_proc`, `_port`, `_starting`, `_lastError`, `_lifecycleGeneration`, `_stderrTail`, `HOST`, `RUNTIME_CONTRACT_VERSION`, `MIN_RENDERER_SAFE_PORT`, `CONTROL_TOKEN`, `isRendererSafePort`, `runtimeCompatibilityNotice`, `pushStderr`, `requestPort`，另有 12 项
- 直接静态依赖：`path`, `http`, `net`, `crypto`, `child_process`, `./engine`, `./env`
- API/IPC 入口：—

<!-- PROJECT-FILE: src/renderer/App.tsx -->
### `src/renderer/App.tsx`

[打开文件](<../../src/renderer/App.tsx>)

- 分类/语言：`electron-or-typescript-product` / React TypeScript
- 规模：107 行，5,198 bytes
- SHA-256：`7131749ec9b3078664400a7c087226a02980064fc965820a5ea693ac7fc66ea4`
- 源码事实摘要：实现或支撑 App 模块；精确公开面见下列静态符号与依赖。
- 顶层符号/结构：`App`
- 直接静态依赖：`react`, `./components/platform/PlatformWorkbench`, `./components/common/AppErrorBoundary`, `./store`, `./utils/automationIR`, `./utils/editorDrafts`, `./styles/globals.css`, `./styles/platform.css`, `./styles/floating.css`
- API/IPC 入口：—

<!-- PROJECT-FILE: src/renderer/components/Canvas/CanvasControls.tsx -->
### `src/renderer/components/Canvas/CanvasControls.tsx`

[打开文件](<../../src/renderer/components/Canvas/CanvasControls.tsx>)

- 分类/语言：`electron-or-typescript-product` / React TypeScript
- 规模：255 行，8,935 bytes
- SHA-256：`333d775f9ed03b0d32fc0955104aa1057e95ea8dd9c71aa3fcba84408d532fa2`
- 源码事实摘要：实现或支撑 CanvasControls 模块；精确公开面见下列静态符号与依赖。
- 顶层符号/结构：`CONTEXT_ADD_NODE_DEFINITIONS`, `CanvasControls`, `MiniMap`, `ContextMenuProps`, `ContextMenu`
- 直接静态依赖：`react`, `lucide-react`, `../../store`, `../../utils/constants`, `../../utils/nodeDefinitions`, `../../platform/flowToArpGraph`
- API/IPC 入口：—

<!-- PROJECT-FILE: src/renderer/components/Canvas/EdgeLayer.tsx -->
### `src/renderer/components/Canvas/EdgeLayer.tsx`

[打开文件](<../../src/renderer/components/Canvas/EdgeLayer.tsx>)

- 分类/语言：`electron-or-typescript-product` / React TypeScript
- 规模：185 行，6,096 bytes
- SHA-256：`c12206b1c6c388f98b2931483539c83ff12266e4d2025e78ea40db48cacd6f00`
- 源码事实摘要：实现或支撑 EdgeLayer 模块；精确公开面见下列静态符号与依赖。
- 顶层符号/结构：`getPortNodePos`, `bezierPath`, `EDGE_COLORS`, `EdgeLayer`
- 直接静态依赖：`react`, `../../store`, `../../types`, `../../utils/nodeDefinitions`
- API/IPC 入口：—

<!-- PROJECT-FILE: src/renderer/components/Canvas/FlowCanvas.tsx -->
### `src/renderer/components/Canvas/FlowCanvas.tsx`

[打开文件](<../../src/renderer/components/Canvas/FlowCanvas.tsx>)

- 分类/语言：`electron-or-typescript-product` / React TypeScript
- 规模：438 行，16,627 bytes
- SHA-256：`8083ec7477896b75b28b64627876caf3fed12fbb43516aa4f282251a19697cc8`
- 源码事实摘要：实现或支撑 FlowCanvas 模块；精确公开面见下列静态符号与依赖。
- 顶层符号/结构：`FlowCanvas`
- 直接静态依赖：`react`, `../../store`, `./FlowNode`, `./EdgeLayer`, `./CanvasControls`, `../../utils/nodeDefinitions`, `../../utils/constants`, `../../types`, `./SubFlowBreadcrumb`
- API/IPC 入口：—

<!-- PROJECT-FILE: src/renderer/components/Canvas/FlowNode.tsx -->
### `src/renderer/components/Canvas/FlowNode.tsx`

[打开文件](<../../src/renderer/components/Canvas/FlowNode.tsx>)

- 分类/语言：`electron-or-typescript-product` / React TypeScript
- 规模：476 行，21,995 bytes
- SHA-256：`fc9c3c485c505effdff6ef8c6373307a74128b60de266c2561c1d6c32761bf3f`
- 源码事实摘要：实现或支撑 FlowNode 模块；精确公开面见下列静态符号与依赖。
- 顶层符号/结构：`NodeIcon`, `getNodeSummary`, `conditionLabel`, `STATUS_COLORS`, `Props`, `FlowNode`
- 直接静态依赖：`react`, `lucide-react`, `../../types`, `../../utils/nodeDefinitions`, `../../utils/constants`, `../../utils/nodeIconMap`, `../../store`
- API/IPC 入口：—

<!-- PROJECT-FILE: src/renderer/components/Canvas/SubFlowBreadcrumb.tsx -->
### `src/renderer/components/Canvas/SubFlowBreadcrumb.tsx`

[打开文件](<../../src/renderer/components/Canvas/SubFlowBreadcrumb.tsx>)

- 分类/语言：`electron-or-typescript-product` / React TypeScript
- 规模：45 行，1,632 bytes
- SHA-256：`30cff2e69148a269d0ab07abdc78a5dad370adbf3315f86b46980d399be35da5`
- 源码事实摘要：实现或支撑 SubFlowBreadcrumb 模块；精确公开面见下列静态符号与依赖。
- 顶层符号/结构：`SubFlowBreadcrumb`
- 直接静态依赖：`react`, `lucide-react`, `../../store`
- API/IPC 入口：—

<!-- PROJECT-FILE: src/renderer/components/FlowFragment/FlowFragmentComposer.tsx -->
### `src/renderer/components/FlowFragment/FlowFragmentComposer.tsx`

[打开文件](<../../src/renderer/components/FlowFragment/FlowFragmentComposer.tsx>)

- 分类/语言：`electron-or-typescript-product` / React TypeScript
- 规模：176 行，7,094 bytes
- SHA-256：`c94c5243cce6de3b5d263c07ee2361d66278abf944034d020727523e3a273f44`
- 源码事实摘要：实现或支撑 FlowFragmentComposer 模块；精确公开面见下列静态符号与依赖。
- 顶层符号/结构：`FlowFragmentComposer`
- 直接静态依赖：`react`, `lucide-react`, `../../store`, `../../utils/editorDrafts`, `../../utils/flowFragmentProposal`
- API/IPC 入口：—

<!-- PROJECT-FILE: src/renderer/components/NodeLibrary/NodeLibrary.tsx -->
### `src/renderer/components/NodeLibrary/NodeLibrary.tsx`

[打开文件](<../../src/renderer/components/NodeLibrary/NodeLibrary.tsx>)

- 分类/语言：`electron-or-typescript-product` / React TypeScript
- 规模：484 行，23,410 bytes
- SHA-256：`d24607a1972c6bd93138fb28672f3401aaeb178a13444503d7336e2463231919`
- 源码事实摘要：实现或支撑 NodeLibrary 模块；精确公开面见下列静态符号与依赖。
- 顶层符号/结构：`RECENT_NODE_TYPES`, `NodeIcon`, `NodeLibrary`
- 直接静态依赖：`react`, `lucide-react`, `../../types`, `../../utils/nodeDefinitions`, `../../store`, `../../utils/unifiedSearch`, `../../utils/itemMarker`, `../../utils/nodeIconMap`, `../../utils/displayLabels`, `../../platform/flowToArpGraph`, `../../platform/contracts`
- API/IPC 入口：—

<!-- PROJECT-FILE: src/renderer/components/PropertyPanel/PropertyPanel.tsx -->
### `src/renderer/components/PropertyPanel/PropertyPanel.tsx`

[打开文件](<../../src/renderer/components/PropertyPanel/PropertyPanel.tsx>)

- 分类/语言：`electron-or-typescript-product` / React TypeScript
- 规模：898 行，48,048 bytes
- SHA-256：`8fdfd43e4a9513b40b07522b5b8c52d09665b7038b1a597b986f760a28128224`
- 源码事实摘要：实现或支撑 PropertyPanel 模块；精确公开面见下列静态符号与依赖。
- 顶层符号/结构：`NodeIcon`, `PropertyPanel`, `NodeParamEditor`
- 直接静态依赖：`react`, `lucide-react`, `../../store`, `../../utils/nodeDefinitions`, `../../types`, `./params/ClickParams`, `./params/ImageClickParams`, `./params/ClickTextParams`, `./params/WaitParams`, `./params/InputTextParams`, `./params/KeyboardParams`, `./params/SwipeParams`, `./params/ConditionParams`, `./params/LoopParams`，另有 15 项
- API/IPC 入口：—

<!-- PROJECT-FILE: src/renderer/components/PropertyPanel/controls/WheelSelectControl.tsx -->
### `src/renderer/components/PropertyPanel/controls/WheelSelectControl.tsx`

[打开文件](<../../src/renderer/components/PropertyPanel/controls/WheelSelectControl.tsx>)

- 分类/语言：`electron-or-typescript-product` / React TypeScript
- 规模：74 行，2,072 bytes
- SHA-256：`4d9a6084d7064f8aa7b2d228b934a456bc4304c979dfe0ae1edbe9184898407e`
- 源码事实摘要：实现或支撑 WheelSelectControl 模块；精确公开面见下列静态符号与依赖。
- 顶层符号/结构：`Option`, `Props`, `WheelSelectControl`
- 直接静态依赖：`react`, `lucide-react`
- API/IPC 入口：—

<!-- PROJECT-FILE: src/renderer/components/PropertyPanel/params/ArpNodeParams.tsx -->
### `src/renderer/components/PropertyPanel/params/ArpNodeParams.tsx`

[打开文件](<../../src/renderer/components/PropertyPanel/params/ArpNodeParams.tsx>)

- 分类/语言：`electron-or-typescript-product` / React TypeScript
- 规模：201 行，10,762 bytes
- SHA-256：`275e2ccb06cc0c7d5f64a6fabd0529f00d3146c883a54205cda03499d181d256`
- 源码事实摘要：实现或支撑 ArpNodeParams 模块；精确公开面见下列静态符号与依赖。
- 顶层符号/结构：`Props`, `Field`, `ArpNodeParams`
- 直接静态依赖：`react`, `../../../types`, `../../../utils/editorDrafts`
- API/IPC 入口：—

<!-- PROJECT-FILE: src/renderer/components/PropertyPanel/params/CameraCaptureParams.tsx -->
### `src/renderer/components/PropertyPanel/params/CameraCaptureParams.tsx`

[打开文件](<../../src/renderer/components/PropertyPanel/params/CameraCaptureParams.tsx>)

- 分类/语言：`electron-or-typescript-product` / React TypeScript
- 规模：93 行，3,311 bytes
- SHA-256：`99d1b3213997fb1bc709656e076d772bc034582b696ea0a33d219fa07f1424f8`
- 源码事实摘要：实现或支撑 CameraCaptureParams 模块；精确公开面见下列静态符号与依赖。
- 顶层符号/结构：`CameraCaptureParams`, `Props`
- 直接静态依赖：—
- API/IPC 入口：—

<!-- PROJECT-FILE: src/renderer/components/PropertyPanel/params/CameraRecordParams.tsx -->
### `src/renderer/components/PropertyPanel/params/CameraRecordParams.tsx`

[打开文件](<../../src/renderer/components/PropertyPanel/params/CameraRecordParams.tsx>)

- 分类/语言：`electron-or-typescript-product` / React TypeScript
- 规模：93 行，3,062 bytes
- SHA-256：`2b512a03a4bedec3aa0075af4e84ecde0c718e5d6569d16b975d2c0843dc3e32`
- 源码事实摘要：实现或支撑 CameraRecordParams 模块；精确公开面见下列静态符号与依赖。
- 顶层符号/结构：`CameraRecordParams`, `Props`
- 直接静态依赖：—
- API/IPC 入口：—

<!-- PROJECT-FILE: src/renderer/components/PropertyPanel/params/ClickParams.tsx -->
### `src/renderer/components/PropertyPanel/params/ClickParams.tsx`

[打开文件](<../../src/renderer/components/PropertyPanel/params/ClickParams.tsx>)

- 分类/语言：`electron-or-typescript-product` / React TypeScript
- 规模：74 行，3,193 bytes
- SHA-256：`2ca4101670903daa02edd4383933a3f219f4cade6ff5cb672e65e78d8d757300`
- 源码事实摘要：实现或支撑 ClickParams 模块；精确公开面见下列静态符号与依赖。
- 顶层符号/结构：`Props`, `ClickParams`
- 直接静态依赖：`react`, `../../../types`, `lucide-react`
- API/IPC 入口：—

<!-- PROJECT-FILE: src/renderer/components/PropertyPanel/params/ClickTextParams.tsx -->
### `src/renderer/components/PropertyPanel/params/ClickTextParams.tsx`

[打开文件](<../../src/renderer/components/PropertyPanel/params/ClickTextParams.tsx>)

- 分类/语言：`electron-or-typescript-product` / React TypeScript
- 规模：139 行，5,318 bytes
- SHA-256：`24f6d9ed343c36b7c6ffcc25a50ecb7d12b02ecd321e4e9675ae9281f71a2391`
- 源码事实摘要：实现或支撑 ClickTextParams 模块；精确公开面见下列静态符号与依赖。
- 顶层符号/结构：`Props`, `ClickTextParams`
- 直接静态依赖：`react`, `../../../types`, `lucide-react`
- API/IPC 入口：—

<!-- PROJECT-FILE: src/renderer/components/PropertyPanel/params/ColorCheckParams.tsx -->
### `src/renderer/components/PropertyPanel/params/ColorCheckParams.tsx`

[打开文件](<../../src/renderer/components/PropertyPanel/params/ColorCheckParams.tsx>)

- 分类/语言：`electron-or-typescript-product` / React TypeScript
- 规模：78 行，3,629 bytes
- SHA-256：`039cd4c3cb3fcb15932ddeda783d572c3b4ccaeb8b49dbc72e78b4d5c6773a50`
- 源码事实摘要：实现或支撑 ColorCheckParams 模块；精确公开面见下列静态符号与依赖。
- 顶层符号/结构：`Props`, `ColorCheckParams`
- 直接静态依赖：`react`, `../../../types`, `lucide-react`
- API/IPC 入口：—

<!-- PROJECT-FILE: src/renderer/components/PropertyPanel/params/CommonNodeSettings.tsx -->
### `src/renderer/components/PropertyPanel/params/CommonNodeSettings.tsx`

[打开文件](<../../src/renderer/components/PropertyPanel/params/CommonNodeSettings.tsx>)

- 分类/语言：`electron-or-typescript-product` / React TypeScript
- 规模：48 行，1,516 bytes
- SHA-256：`c9d998e4caee8deb40ce2fd12c9f9cfd16f6f4ffd0defadf9cd02cbdf851e539`
- 源码事实摘要：实现或支撑 CommonNodeSettings 模块；精确公开面见下列静态符号与依赖。
- 顶层符号/结构：`Props`, `CommonNodeSettings`
- 直接静态依赖：`../../../types`
- API/IPC 入口：—

<!-- PROJECT-FILE: src/renderer/components/PropertyPanel/params/ConditionParams.tsx -->
### `src/renderer/components/PropertyPanel/params/ConditionParams.tsx`

[打开文件](<../../src/renderer/components/PropertyPanel/params/ConditionParams.tsx>)

- 分类/语言：`electron-or-typescript-product` / React TypeScript
- 规模：77 行，2,719 bytes
- SHA-256：`cd39477d2e065ac011bb31326e35435370a8d251f7c6b6f92fe33f47c24a60dd`
- 源码事实摘要：实现或支撑 ConditionParams 模块；精确公开面见下列静态符号与依赖。
- 顶层符号/结构：`Props`, `CONDITION_OPTIONS`, `ConditionParams`
- 直接静态依赖：`../../../types`
- API/IPC 入口：—

<!-- PROJECT-FILE: src/renderer/components/PropertyPanel/params/ImageClickParams.tsx -->
### `src/renderer/components/PropertyPanel/params/ImageClickParams.tsx`

[打开文件](<../../src/renderer/components/PropertyPanel/params/ImageClickParams.tsx>)

- 分类/语言：`electron-or-typescript-product` / React TypeScript
- 规模：290 行，12,299 bytes
- SHA-256：`40abb95757e8c7393c3e1ddc643033099cb08d1811607bd33c1b85e0337d5aa8`
- 源码事实摘要：实现或支撑 ImageClickParams 模块；精确公开面见下列静态符号与依赖。
- 顶层符号/结构：`Props`, `ImageClickParams`
- 直接静态依赖：`react`, `../../../types`, `lucide-react`
- API/IPC 入口：—

<!-- PROJECT-FILE: src/renderer/components/PropertyPanel/params/InputTextParams.tsx -->
### `src/renderer/components/PropertyPanel/params/InputTextParams.tsx`

[打开文件](<../../src/renderer/components/PropertyPanel/params/InputTextParams.tsx>)

- 分类/语言：`electron-or-typescript-product` / React TypeScript
- 规模：85 行，3,494 bytes
- SHA-256：`4d0956b7d74122f0e7664c1b4a2909947baa57e52aef458426fd80247c5b7874`
- 源码事实摘要：实现或支撑 InputTextParams 模块；精确公开面见下列静态符号与依赖。
- 顶层符号/结构：`Props`, `InputTextParams`
- 直接静态依赖：`react`, `../../../types`, `lucide-react`
- API/IPC 入口：—

<!-- PROJECT-FILE: src/renderer/components/PropertyPanel/params/KeyboardParams.tsx -->
### `src/renderer/components/PropertyPanel/params/KeyboardParams.tsx`

[打开文件](<../../src/renderer/components/PropertyPanel/params/KeyboardParams.tsx>)

- 分类/语言：`electron-or-typescript-product` / React TypeScript
- 规模：136 行，4,847 bytes
- SHA-256：`783e47f1ce6ea97557058b8aa6c7096dfee5bf3dcbf9858e260752b48f94285f`
- 源码事实摘要：实现或支撑 KeyboardParams 模块；精确公开面见下列静态符号与依赖。
- 顶层符号/结构：`Props`, `COMMON_KEYS`, `KeyboardParams`
- 直接静态依赖：`react`, `../../../types`
- API/IPC 入口：—

<!-- PROJECT-FILE: src/renderer/components/PropertyPanel/params/LoopParams.tsx -->
### `src/renderer/components/PropertyPanel/params/LoopParams.tsx`

[打开文件](<../../src/renderer/components/PropertyPanel/params/LoopParams.tsx>)

- 分类/语言：`electron-or-typescript-product` / React TypeScript
- 规模：45 行，1,871 bytes
- SHA-256：`9d6f71d1f803e5549bd7249045f029146a7e2ba34d3ebc5ca9d401163f8a36be`
- 源码事实摘要：实现或支撑 LoopParams 模块；精确公开面见下列静态符号与依赖。
- 顶层符号/结构：`Props`, `LoopParams`
- 直接静态依赖：`../../../types`
- API/IPC 入口：—

<!-- PROJECT-FILE: src/renderer/components/PropertyPanel/params/PopupCloseParams.tsx -->
### `src/renderer/components/PropertyPanel/params/PopupCloseParams.tsx`

[打开文件](<../../src/renderer/components/PropertyPanel/params/PopupCloseParams.tsx>)

- 分类/语言：`electron-or-typescript-product` / React TypeScript
- 规模：47 行，1,965 bytes
- SHA-256：`279fbbaf0fb438bd84386d5dd0eaa2f89d4c65e50192e694b7a49c953ad0b966`
- 源码事实摘要：实现或支撑 PopupCloseParams 模块；精确公开面见下列静态符号与依赖。
- 顶层符号/结构：`Props`, `STRATEGY_OPTIONS`, `PopupCloseParams`
- 直接静态依赖：`../../../types`, `../controls/WheelSelectControl`
- API/IPC 入口：—

<!-- PROJECT-FILE: src/renderer/components/PropertyPanel/params/ProgressWaitParams.tsx -->
### `src/renderer/components/PropertyPanel/params/ProgressWaitParams.tsx`

[打开文件](<../../src/renderer/components/PropertyPanel/params/ProgressWaitParams.tsx>)

- 分类/语言：`electron-or-typescript-product` / React TypeScript
- 规模：86 行，4,227 bytes
- SHA-256：`e832bbfb84556251251bb3c3b60267077bdecf7b77ea9c5cf1f94c85aa09e0bc`
- 源码事实摘要：实现或支撑 ProgressWaitParams 模块；精确公开面见下列静态符号与依赖。
- 顶层符号/结构：`Props`, `ProgressWaitParams`
- 直接静态依赖：`react`, `../../../types`, `lucide-react`
- API/IPC 入口：—

<!-- PROJECT-FILE: src/renderer/components/PropertyPanel/params/RegionWaitParams.tsx -->
### `src/renderer/components/PropertyPanel/params/RegionWaitParams.tsx`

[打开文件](<../../src/renderer/components/PropertyPanel/params/RegionWaitParams.tsx>)

- 分类/语言：`electron-or-typescript-product` / React TypeScript
- 规模：85 行，3,989 bytes
- SHA-256：`8e905a0b2b21acb7aa4dc5e13ae55664782c1c26481de20b6796a755ba1d8832`
- 源码事实摘要：实现或支撑 RegionWaitParams 模块；精确公开面见下列静态符号与依赖。
- 顶层符号/结构：`Props`, `RegionWaitParams`
- 直接静态依赖：`react`, `../../../types`, `lucide-react`
- API/IPC 入口：—

<!-- PROJECT-FILE: src/renderer/components/PropertyPanel/params/SceneConditionParams.tsx -->
### `src/renderer/components/PropertyPanel/params/SceneConditionParams.tsx`

[打开文件](<../../src/renderer/components/PropertyPanel/params/SceneConditionParams.tsx>)

- 分类/语言：`electron-or-typescript-product` / React TypeScript
- 规模：31 行，1,407 bytes
- SHA-256：`2b3d5b678d40e0058a5b64060ca2526891f698904562be17be6e5b7bde10b009`
- 源码事实摘要：实现或支撑 SceneConditionParams 模块；精确公开面见下列静态符号与依赖。
- 顶层符号/结构：`Props`, `SceneConditionParams`
- 直接静态依赖：`../../../types`
- API/IPC 入口：—

<!-- PROJECT-FILE: src/renderer/components/PropertyPanel/params/ScreenshotParams.tsx -->
### `src/renderer/components/PropertyPanel/params/ScreenshotParams.tsx`

[打开文件](<../../src/renderer/components/PropertyPanel/params/ScreenshotParams.tsx>)

- 分类/语言：`electron-or-typescript-product` / React TypeScript
- 规模：121 行，4,293 bytes
- SHA-256：`678d04a9868a85e95da73465f61e0ff4cdc96966e59717339adbd56366d646bd`
- 源码事实摘要：实现或支撑 ScreenshotParams 模块；精确公开面见下列静态符号与依赖。
- 顶层符号/结构：`Props`, `isRegion`, `ScreenshotParams`
- 直接静态依赖：`react`, `lucide-react`, `../../../types`
- API/IPC 入口：—

<!-- PROJECT-FILE: src/renderer/components/PropertyPanel/params/SubScriptParams.tsx -->
### `src/renderer/components/PropertyPanel/params/SubScriptParams.tsx`

[打开文件](<../../src/renderer/components/PropertyPanel/params/SubScriptParams.tsx>)

- 分类/语言：`electron-or-typescript-product` / React TypeScript
- 规模：69 行，2,387 bytes
- SHA-256：`d481c597885bb5b232d6244196c8e625fc82bb7e3a351c97f27ba1be7047e305`
- 源码事实摘要：实现或支撑 SubScriptParams 模块；精确公开面见下列静态符号与依赖。
- 顶层符号/结构：`Props`, `SubScriptParams`
- 直接静态依赖：`react`, `lucide-react`, `../../../types`, `../../common/ScriptBrowser`, `../../../store`
- API/IPC 入口：—

<!-- PROJECT-FILE: src/renderer/components/PropertyPanel/params/SwipeParams.tsx -->
### `src/renderer/components/PropertyPanel/params/SwipeParams.tsx`

[打开文件](<../../src/renderer/components/PropertyPanel/params/SwipeParams.tsx>)

- 分类/语言：`electron-or-typescript-product` / React TypeScript
- 规模：109 行，4,492 bytes
- SHA-256：`599ba8e067024bfd0deabccf9e10a7a7ed30ad8b3e2bf88372f3ac369f7cb318`
- 源码事实摘要：实现或支撑 SwipeParams 模块；精确公开面见下列静态符号与依赖。
- 顶层符号/结构：`Props`, `SwipeParams`
- 直接静态依赖：`react`, `../../../types`, `lucide-react`
- API/IPC 入口：—

<!-- PROJECT-FILE: src/renderer/components/PropertyPanel/params/ToolCallParams.tsx -->
### `src/renderer/components/PropertyPanel/params/ToolCallParams.tsx`

[打开文件](<../../src/renderer/components/PropertyPanel/params/ToolCallParams.tsx>)

- 分类/语言：`electron-or-typescript-product` / React TypeScript
- 规模：184 行，6,979 bytes
- SHA-256：`e9dbbbb3d1a6fbefab259bf01ddf5a0b363215502ceff9de92763b2830772a4f`
- 源码事实摘要：实现或支撑 ToolCallParams 模块；精确公开面见下列静态符号与依赖。
- 顶层符号/结构：`Props`, `CAT_LABELS`, `CAT_ORDER`, `ToolCallParams`
- 直接静态依赖：`react`, `../../../store`, `../../../types`
- API/IPC 入口：—

<!-- PROJECT-FILE: src/renderer/components/PropertyPanel/params/VariableParams.tsx -->
### `src/renderer/components/PropertyPanel/params/VariableParams.tsx`

[打开文件](<../../src/renderer/components/PropertyPanel/params/VariableParams.tsx>)

- 分类/语言：`electron-or-typescript-product` / React TypeScript
- 规模：50 行，1,645 bytes
- SHA-256：`b7fd35a14d6630179f48929a3489cbab2ad8bcd11200471d63f757ad484e65fd`
- 源码事实摘要：实现或支撑 VariableParams 模块；精确公开面见下列静态符号与依赖。
- 顶层符号/结构：`Props`, `VariableParams`
- 直接静态依赖：`../../../types`
- API/IPC 入口：—

<!-- PROJECT-FILE: src/renderer/components/PropertyPanel/params/WaitParams.tsx -->
### `src/renderer/components/PropertyPanel/params/WaitParams.tsx`

[打开文件](<../../src/renderer/components/PropertyPanel/params/WaitParams.tsx>)

- 分类/语言：`electron-or-typescript-product` / React TypeScript
- 规模：86 行，3,209 bytes
- SHA-256：`650d0746e3fe4f22d497b5734883db25eb78534369895868e7c78055589501df`
- 源码事实摘要：实现或支撑 WaitParams 模块；精确公开面见下列静态符号与依赖。
- 顶层符号/结构：`Props`, `WaitParams`
- 直接静态依赖：`../../../types`
- API/IPC 入口：—

<!-- PROJECT-FILE: src/renderer/components/PropertyPanel/params/WebBrowserParams.tsx -->
### `src/renderer/components/PropertyPanel/params/WebBrowserParams.tsx`

[打开文件](<../../src/renderer/components/PropertyPanel/params/WebBrowserParams.tsx>)

- 分类/语言：`electron-or-typescript-product` / React TypeScript
- 规模：92 行，4,220 bytes
- SHA-256：`90a86cdc2f2b4832ae39239516d6a6544f1875420318f2c7d860cd60b5d744ff`
- 源码事实摘要：实现或支撑 WebBrowserParams 模块；精确公开面见下列静态符号与依赖。
- 顶层符号/结构：`Props`, `ACTIONS`, `WebBrowserParams`
- 直接静态依赖：`../../../types`
- API/IPC 入口：—

<!-- PROJECT-FILE: src/renderer/components/common/AppErrorBoundary.tsx -->
### `src/renderer/components/common/AppErrorBoundary.tsx`

[打开文件](<../../src/renderer/components/common/AppErrorBoundary.tsx>)

- 分类/语言：`electron-or-typescript-product` / React TypeScript
- 规模：38 行，1,288 bytes
- SHA-256：`ba821b85325e4c30bf7e06ce64529e22a8d2204ade00329ce726434cf02252d1`
- 源码事实摘要：实现或支撑 AppErrorBoundary 模块；精确公开面见下列静态符号与依赖。
- 顶层符号/结构：`AppErrorBoundaryProps`, `AppErrorBoundaryState`, `AppErrorBoundary`
- 直接静态依赖：`react`, `lucide-react`
- API/IPC 入口：—

<!-- PROJECT-FILE: src/renderer/components/common/ScriptBrowser.tsx -->
### `src/renderer/components/common/ScriptBrowser.tsx`

[打开文件](<../../src/renderer/components/common/ScriptBrowser.tsx>)

- 分类/语言：`electron-or-typescript-product` / React TypeScript
- 规模：135 行，5,361 bytes
- SHA-256：`dba28df4d580a1e84c8ba10f06f9b057d3f34ce3c56ba32c4f9968fb333f4451`
- 源码事实摘要：实现或支撑 ScriptBrowser 模块；精确公开面见下列静态符号与依赖。
- 顶层符号/结构：`ScriptBrowserProps`, `ScriptBrowser`
- 直接静态依赖：`react`, `lucide-react`, `../../store`, `../../utils/templateRegistry`, `../../utils/unifiedSearch`, `../../utils/displayLabels`, `./UnifiedSearchDialog`
- API/IPC 入口：—

<!-- PROJECT-FILE: src/renderer/components/common/UnifiedSearchDialog.tsx -->
### `src/renderer/components/common/UnifiedSearchDialog.tsx`

[打开文件](<../../src/renderer/components/common/UnifiedSearchDialog.tsx>)

- 分类/语言：`electron-or-typescript-product` / React TypeScript
- 规模：385 行，15,794 bytes
- SHA-256：`1f90eb81972790a7c7fb6fb3a513c4b8cc4fee5fbc91cc557520be0e1f70cdf9`
- 源码事实摘要：实现或支撑 UnifiedSearchDialog 模块；精确公开面见下列静态符号与依赖。
- 顶层符号/结构：`FilterTab`, `UnifiedSearchDialogProps`, `OVERLAY_STYLE`, `DIALOG_STYLE`, `FILTER_TABS`, `EXAMPLE_QUERIES`, `UnifiedSearchDialog`, `getScoreColor`, `getTypeBadgeStyle`
- 直接静态依赖：`react`, `../../store`, `../../utils/unifiedSearch`, `../../utils/itemMarker`, `lucide-react`, `../../utils/displayLabels`
- API/IPC 入口：—

<!-- PROJECT-FILE: src/renderer/components/platform/PlatformWorkbench.tsx -->
### `src/renderer/components/platform/PlatformWorkbench.tsx`

[打开文件](<../../src/renderer/components/platform/PlatformWorkbench.tsx>)

- 分类/语言：`electron-or-typescript-product` / React TypeScript
- 规模：3274 行，167,080 bytes
- SHA-256：`3ec734f2cdc03e9c5f783ba65e3f6982c894e3ac61cb024b3647798b21458a85`
- 源码事实摘要：实现或支撑 PlatformWorkbench 模块；精确公开面见下列静态符号与依赖。
- 顶层符号/结构：`PlatformView`, `Plane`, `isActiveRunStatus`, `UNSUCCESSFUL_STATUSES`, `PHASES`, `PHASE_LABELS`, `STATUS_LABELS`, `RUN_KIND_LABELS`, `EVENT_LABELS`, `TOOL_LABELS`, `LIVE_ACTIVITY_LABELS`, `viewFromStore`, `formatTime`, `formatDuration`，另有 63 项
- 直接静态依赖：`react`, `lucide-react`, `../Canvas/FlowCanvas`, `../NodeLibrary/NodeLibrary`, `../PropertyPanel/PropertyPanel`, `../FlowFragment/FlowFragmentComposer`, `../../store`, `../../types`, `../../platform/useControlPlane`, `../../platform/contracts`, `../../utils/wpsToFlowScript`, `../../utils/automationIR`, `../../utils/flowFragmentProposal`, `../../../shared/taskOutcome`，另有 1 项
- API/IPC 入口：—

<!-- PROJECT-FILE: src/renderer/icon.png -->
### `src/renderer/icon.png`

[打开文件](<../../src/renderer/icon.png>)

- 分类/语言：`asset-or-binary` / PNG image
- 规模：binary 行，89,789 bytes
- SHA-256：`8ab86a926198e67725680075d79605aa00ac15e1d80cd33078c24f7cbe51ce10`
- 源码事实摘要：PNG 256x256
- 顶层符号/结构：—
- 直接静态依赖：—
- API/IPC 入口：—
- 解析说明：binary

<!-- PROJECT-FILE: src/renderer/index.html -->
### `src/renderer/index.html`

[打开文件](<../../src/renderer/index.html>)

- 分类/语言：`electron-or-typescript-product` / HTML
- 规模：14 行，438 bytes
- SHA-256：`b671042db897c651923b19e9d0ab0dcd8a071ff8647f42c3cc41d7a7d5602681`
- 源码事实摘要：灵动脚本
- 顶层符号/结构：—
- 直接静态依赖：—
- API/IPC 入口：—

<!-- PROJECT-FILE: src/renderer/main.tsx -->
### `src/renderer/main.tsx`

[打开文件](<../../src/renderer/main.tsx>)

- 分类/语言：`electron-or-typescript-product` / React TypeScript
- 规模：10 行，245 bytes
- SHA-256：`1908c4f8cf7f16047ff4ee360bda3b0f913021ebc66133eec291f436e5300c9d`
- 源码事实摘要：实现或支撑 main 模块；精确公开面见下列静态符号与依赖。
- 顶层符号/结构：—
- 直接静态依赖：`react`, `react-dom/client`, `./App`, `./styles/globals.css`
- API/IPC 入口：—

<!-- PROJECT-FILE: src/renderer/platform/client.ts -->
### `src/renderer/platform/client.ts`

[打开文件](<../../src/renderer/platform/client.ts>)

- 分类/语言：`electron-or-typescript-product` / TypeScript
- 规模：560 行，18,992 bytes
- SHA-256：`52661b7b6da2c6a21628b7872534f1fa7a4b97fadaa1b035ca3bab6a7a740f3a`
- 源码事实摘要：实现或支撑 client 模块；精确公开面见下列静态符号与依赖。
- 顶层符号/结构：`FALLBACK_URL`, `ELECTRON_SERVER_READY_TIMEOUT_MS`, `ELECTRON_SERVER_POLL_MS`, `EXPECTED_RUNTIME_CONTRACT_VERSION`, `runtimeCompatibilityNotice`, `normalizeBaseUrl`, `ControlPlaneConnection`, `websocketUrl`, `resolveControlPlaneConnection`, `resolveControlPlaneUrl`, `readJson`, `ControlPlaneClient`
- 直接静态依赖：`./contracts`, `../utils/flowFragmentProposal`, `../utils/automationIR`
- API/IPC 入口：—

<!-- PROJECT-FILE: src/renderer/platform/contracts.ts -->
### `src/renderer/platform/contracts.ts`

[打开文件](<../../src/renderer/platform/contracts.ts>)

- 分类/语言：`electron-or-typescript-product` / TypeScript
- 规模：829 行，21,766 bytes
- SHA-256：`f7c4e5a35cc778b01d27bb3b4ac1974553835d1ac4205400dccbc5d21067c704`
- 源码事实摘要：实现或支撑 contracts 模块；精确公开面见下列静态符号与依赖。
- 顶层符号/结构：`ARP_FORMAT`, `RuntimeVersion`, `GoalAdmissionAudit`, `DesktopLeaseSnapshot`, `GoalRunStart`, `ArpNodeKind`, `ArpRetryPolicy`, `ArpNode`, `ArpEdge`, `ArpGraph`, `GraphValidation`, `CapabilitySpec`, `RunTimelineEvent`, `RunMetrics`，另有 48 项
- 直接静态依赖：—
- API/IPC 入口：—

<!-- PROJECT-FILE: src/renderer/platform/executionContext.ts -->
### `src/renderer/platform/executionContext.ts`

[打开文件](<../../src/renderer/platform/executionContext.ts>)

- 分类/语言：`electron-or-typescript-product` / TypeScript
- 规模：47 行，1,750 bytes
- SHA-256：`6b3b493c969d7cc942e8e3db4c9d9d9aa7676120d02284e6a1f84b94d981480d`
- 源码事实摘要：实现或支撑 executionContext 模块；精确公开面见下列静态符号与依赖。
- 顶层符号/结构：`DesktopHandoffResult`, `buildClientExecutionContext`
- 直接静态依赖：`./contracts`
- API/IPC 入口：—

<!-- PROJECT-FILE: src/renderer/platform/flowToArpGraph.ts -->
### `src/renderer/platform/flowToArpGraph.ts`

[打开文件](<../../src/renderer/platform/flowToArpGraph.ts>)

- 分类/语言：`electron-or-typescript-product` / TypeScript
- 规模：337 行，12,812 bytes
- SHA-256：`b696117551d1019cc268f2ca4bee0c79d0d087528f087308f136de9a4cef3854`
- 源码事实摘要：实现或支撑 flowToArpGraph 模块；精确公开面见下列静态符号与依赖。
- 顶层符号/结构：`NATIVE_KINDS`, `RUNNABLE_FLOW_NODE_TYPES`, `isRunnableFlowNodeType`, `retryOf`, `withoutPolicyFields`, `conditionConfig`, `list`, `withDelay`, `clickAction`, `coordinateClickActions`, `controllerActions`, `nodeToArp`, `edgeBranch`, `flowToArpGraph`
- 直接静态依赖：`../types`, `./contracts`
- API/IPC 入口：—

<!-- PROJECT-FILE: src/renderer/platform/taskOutcome.ts -->
### `src/renderer/platform/taskOutcome.ts`

[打开文件](<../../src/renderer/platform/taskOutcome.ts>)

- 分类/语言：`electron-or-typescript-product` / TypeScript
- 规模：20 行，529 bytes
- SHA-256：`c48c24568955a6875b08da595f319bf350be426efd474381132d1799a2d0ad5d`
- 源码事实摘要：实现或支撑 taskOutcome 模块；精确公开面见下列静态符号与依赖。
- 顶层符号/结构：`taskOutcomeForRun`
- 直接静态依赖：`../../shared/taskOutcome`, `./contracts`
- API/IPC 入口：—

<!-- PROJECT-FILE: src/renderer/platform/taskbarNotification.ts -->
### `src/renderer/platform/taskbarNotification.ts`

[打开文件](<../../src/renderer/platform/taskbarNotification.ts>)

- 分类/语言：`electron-or-typescript-product` / TypeScript
- 规模：62 行，2,001 bytes
- SHA-256：`1203d0013e513e3a81e74946618d555f2fc6452b35bfb14385540f2037eede9c`
- 源码事实摘要：实现或支撑 taskbarNotification 模块；精确公开面见下列静态符号与依赖。
- 顶层符号/结构：`TaskbarNotificationKind`, `TaskbarRunState`, `TaskbarNotificationRequest`, `ACTIVE_RUN_STATUSES`, `taskbarNotificationForTransition`
- 直接静态依赖：`./contracts`, `../../shared/taskOutcome`, `./taskOutcome`
- API/IPC 入口：—

<!-- PROJECT-FILE: src/renderer/platform/useControlPlane.ts -->
### `src/renderer/platform/useControlPlane.ts`

[打开文件](<../../src/renderer/platform/useControlPlane.ts>)

- 分类/语言：`electron-or-typescript-product` / TypeScript
- 规模：514 行，16,563 bytes
- SHA-256：`c253b7eb655d30900438f3985740b227f61b621d6f8babddcadc89eaf8c632e3`
- 源码事实摘要：实现或支撑 useControlPlane 模块；精确公开面见下列静态符号与依赖。
- 顶层符号/结构：`ConnectionState`, `CONTROL_PLANE_FALLBACK_REFRESH_MS`, `CONTROL_PLANE_EVENT_REFRESH_MS`, `CONTROL_PLANE_RECONNECT_MS`, `DesktopLeaseClient`, `releaseDesktopHandoffBestEffort`, `fulfillDesktopLease`, `useControlPlane`
- 直接静态依赖：`react`, `./client`, `./executionContext`, `./contracts`, `../utils/automationIR`, `../utils/flowFragmentProposal`, `./taskbarNotification`
- API/IPC 入口：—

<!-- PROJECT-FILE: src/renderer/store/defaults.ts -->
### `src/renderer/store/defaults.ts`

[打开文件](<../../src/renderer/store/defaults.ts>)

- 分类/语言：`electron-or-typescript-product` / TypeScript
- 规模：194 行，6,890 bytes
- SHA-256：`7b4062e068f8dcf910b247ee0333e23bd5281c2c223a3ed74f9f94bfee122c40`
- 源码事实摘要：Store default values and executable preset templates. */
- 顶层符号/结构：`DEFAULT_SETTINGS`, `DEFAULT_UI_STATE`, `MAX_HISTORY`, `STORAGE_KEY`, `RECENT_KEY`, `CUSTOM_TEMPLATES_KEY`, `AGENT_HISTORY_KEY`, `TEMPLATE_SHORTCUTS_KEY`, `ScriptTemplate`, `meta`, `PRESET_TEMPLATES`, `PRESET_TEMPLATE_META`
- 直接静态依赖：`nanoid`, `../types`
- API/IPC 入口：—

<!-- PROJECT-FILE: src/renderer/store/executor.ts -->
### `src/renderer/store/executor.ts`

[打开文件](<../../src/renderer/store/executor.ts>)

- 分类/语言：`electron-or-typescript-product` / TypeScript
- 规模：1085 行，47,867 bytes
- SHA-256：`35e0855ba2d1ec34f9cc898f609556d7b9e94f09ea5a90e09611a8950a429717`
- 源码事实摘要：实现或支撑 executor 模块；精确公开面见下列静态符号与依赖。
- 顶层符号/结构：`ExecutionController`, `ExecCallbacks`, `createFlowExecutor`
- 直接静态依赖：`../types`
- API/IPC 入口：—

<!-- PROJECT-FILE: src/renderer/store/index.ts -->
### `src/renderer/store/index.ts`

[打开文件](<../../src/renderer/store/index.ts>)

- 分类/语言：`electron-or-typescript-product` / TypeScript
- 规模：1647 行，62,088 bytes
- SHA-256：`73d3d971c124c20f2ea99e81bcb01002361a8c472ae429486290ff43aa38246a`
- 源码事实摘要：实现或支撑 index 模块；精确公开面见下列静态符号与依赖。
- 顶层符号/结构：`ScriptSaveResult`, `createTemplateScript`, `AppStore`, `history`, `historyIndex`, `_buildParamPromptQueue`, `pushHistory`, `initialScript`, `useStore`, `confirmDocumentReplacement`, `_autoSaveTestFlow`, `_api`, `_listenerCleanups`, `_registerListeners`
- 直接静态依赖：`zustand/traditional`, `zustand/middleware/immer`, `nanoid`, `../types`, `../utils/nodeDefinitions`, `../utils/constants`, `../utils/templateRegistry`, `../utils/templateMatcher`, `./executor`, `../utils/automationIR`, `../utils/editorDrafts`, `../utils/flowFragmentProposal`, `../platform/flowToArpGraph`, `./defaults`，另有 2 项
- API/IPC 入口：—

<!-- PROJECT-FILE: src/renderer/store/persistence.ts -->
### `src/renderer/store/persistence.ts`

[打开文件](<../../src/renderer/store/persistence.ts>)

- 分类/语言：`electron-or-typescript-product` / TypeScript
- 规模：64 行，1,809 bytes
- SHA-256：`5a1b2924f65aa672ccbc8797b5a6704dba9d0bc3da14dcee8c03f7e9501dda71`
- 源码事实摘要：localStorage persistence helpers */
- 顶层符号/结构：`CustomTemplate`, `loadTemplateShortcuts`, `saveTemplateShortcut`, `deleteTemplateShortcut`, `loadCustomTemplates`, `saveCustomTemplates`, `loadAgentHistory`, `saveAgentHistory`
- 直接静态依赖：`../types`, `./defaults`
- API/IPC 入口：—

<!-- PROJECT-FILE: src/renderer/styles/floating.css -->
### `src/renderer/styles/floating.css`

[打开文件](<../../src/renderer/styles/floating.css>)

- 分类/语言：`electron-or-typescript-product` / CSS
- 规模：3117 行，71,114 bytes
- SHA-256：`532f9805e3c98eccb2298e24443ddb6a01aac00777aa598d6f181135431d4bc4`
- 源码事实摘要：:root {
- 顶层符号/结构：—
- 直接静态依赖：—
- API/IPC 入口：—

<!-- PROJECT-FILE: src/renderer/styles/globals.css -->
### `src/renderer/styles/globals.css`

[打开文件](<../../src/renderer/styles/globals.css>)

- 分类/语言：`electron-or-typescript-product` / CSS
- 规模：3343 行，73,130 bytes
- SHA-256：`d9da92b9c93b25732183e9ac965f8a6df58f13a483d161babdb86ade07f7a0bb`
- 源码事实摘要：@import url('https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap');
- 顶层符号/结构：—
- 直接静态依赖：—
- API/IPC 入口：—

<!-- PROJECT-FILE: src/renderer/styles/platform.css -->
### `src/renderer/styles/platform.css`

[打开文件](<../../src/renderer/styles/platform.css>)

- 分类/语言：`electron-or-typescript-product` / CSS
- 规模：718 行，53,408 bytes
- SHA-256：`fb905f44c8ce35eb2b000ac0f64a5831d81a768d8becfda9f6059d1284a8f8a7`
- 源码事实摘要：:root {
- 顶层符号/结构：—
- 直接静态依赖：—
- API/IPC 入口：—

<!-- PROJECT-FILE: src/renderer/test/appErrorBoundary.test.tsx -->
### `src/renderer/test/appErrorBoundary.test.tsx`

[打开文件](<../../src/renderer/test/appErrorBoundary.test.tsx>)

- 分类/语言：`test` / React TypeScript
- 规模：18 行，832 bytes
- SHA-256：`611f9755a399fdb8e833fc33bc1d00446dbb040f2b55d0aa953eb92ea758d2a4`
- 源码事实摘要：验证 appErrorBoundary.test 对应的行为、回归或合同。
- 顶层符号/结构：`BrokenView`
- 直接静态依赖：`@testing-library/react`, `vitest`, `../components/common/AppErrorBoundary`
- API/IPC 入口：—

<!-- PROJECT-FILE: src/renderer/test/appFileOpen.test.tsx -->
### `src/renderer/test/appFileOpen.test.tsx`

[打开文件](<../../src/renderer/test/appFileOpen.test.tsx>)

- 分类/语言：`test` / React TypeScript
- 规模：166 行，5,483 bytes
- SHA-256：`55f2955525d665c1b5417d46f435d1b32c2c797d9b1cd75bb50c296fd1f715d0`
- 源码事实摘要：验证 appFileOpen.test 对应的行为、回归或合同。
- 顶层符号/结构：`harness`, `validFlow`, `electronApi`
- 直接静态依赖：`@testing-library/react`, `vitest`, `../App`, `../utils/editorDrafts`
- API/IPC 入口：—

<!-- PROJECT-FILE: src/renderer/test/apprenticeUx.test.tsx -->
### `src/renderer/test/apprenticeUx.test.tsx`

[打开文件](<../../src/renderer/test/apprenticeUx.test.tsx>)

- 分类/语言：`test` / React TypeScript
- 规模：233 行，9,976 bytes
- SHA-256：`23b0f7cdf78270b9ead576630d6d36c3152ae19d90b41731ec343319d649b435`
- 源码事实摘要：验证 apprenticeUx.test 对应的行为、回归或合同。
- 顶层符号/结构：`controlPlane`, `createPlane`
- 直接静态依赖：`@testing-library/react`, `@testing-library/user-event`, `vitest`, `../components/platform/PlatformWorkbench`, `../store`, `../styles/platform.css?raw`
- API/IPC 入口：—

<!-- PROJECT-FILE: src/renderer/test/automationIR.test.ts -->
### `src/renderer/test/automationIR.test.ts`

[打开文件](<../../src/renderer/test/automationIR.test.ts>)

- 分类/语言：`test` / TypeScript
- 规模：492 行，19,561 bytes
- SHA-256：`c3cc3eafd48ba24d4df3c66cdf2259d634e5b059f219d61b08074d418f7ea27b`
- 源码事实摘要：验证 automationIR.test 对应的行为、回归或合同。
- 顶层符号/结构：—
- 直接静态依赖：`vitest`, `../utils/automationIR`, `../types`, `../platform/flowToArpGraph`
- API/IPC 入口：—

<!-- PROJECT-FILE: src/renderer/test/canvasControlsNodeBoundary.test.tsx -->
### `src/renderer/test/canvasControlsNodeBoundary.test.tsx`

[打开文件](<../../src/renderer/test/canvasControlsNodeBoundary.test.tsx>)

- 分类/语言：`test` / React TypeScript
- 规模：43 行，1,954 bytes
- SHA-256：`34a49bb02ac11a7dbf5c1f3ec68ca2f411a3c236ae96ddb8cb1951d66ba8ee71`
- 源码事实摘要：验证 canvasControlsNodeBoundary.test 对应的行为、回归或合同。
- 顶层符号/结构：—
- 直接静态依赖：`@testing-library/react`, `@testing-library/user-event`, `vitest`, `../components/Canvas/CanvasControls`, `../platform/flowToArpGraph`, `../store`, `../utils/nodeDefinitions`
- API/IPC 入口：—

<!-- PROJECT-FILE: src/renderer/test/constants.test.ts -->
### `src/renderer/test/constants.test.ts`

[打开文件](<../../src/renderer/test/constants.test.ts>)

- 分类/语言：`test` / TypeScript
- 规模：23 行，940 bytes
- SHA-256：`e5a2eca61c9a6b4e8948b2339a82d0ec02d2fc8f5eef3507346a8b791c86ed72`
- 源码事实摘要：验证 constants.test 对应的行为、回归或合同。
- 顶层符号/结构：—
- 直接静态依赖：`vitest`, `../utils/constants`
- API/IPC 入口：—

<!-- PROJECT-FILE: src/renderer/test/executionContext.test.ts -->
### `src/renderer/test/executionContext.test.ts`

[打开文件](<../../src/renderer/test/executionContext.test.ts>)

- 分类/语言：`test` / TypeScript
- 规模：147 行，4,749 bytes
- SHA-256：`705cbbcb8489f7c237536cb9fb78824ef5bfd4ca35fc9f3d3e698417be3c9860`
- 源码事实摘要：验证 executionContext.test 对应的行为、回归或合同。
- 顶层符号/结构：—
- 直接静态依赖：`vitest`, `../platform/executionContext`
- API/IPC 入口：—

<!-- PROJECT-FILE: src/renderer/test/executor.test.ts -->
### `src/renderer/test/executor.test.ts`

[打开文件](<../../src/renderer/test/executor.test.ts>)

- 分类/语言：`test` / TypeScript
- 规模：775 行，23,184 bytes
- SHA-256：`2497eb581d583ae3a82e7b3897d47e3db024aa8bbdbb9e28f9d2d24b40741cf9`
- 源码事实摘要：验证 executor.test 对应的行为、回归或合同。
- 顶层符号/结构：`flowWith`, `flowSequence`, `runFlow`
- 直接静态依赖：`vitest`, `../store/executor`, `../types`
- API/IPC 入口：—

<!-- PROJECT-FILE: src/renderer/test/flowFragmentComposer.test.tsx -->
### `src/renderer/test/flowFragmentComposer.test.tsx`

[打开文件](<../../src/renderer/test/flowFragmentComposer.test.tsx>)

- 分类/语言：`test` / React TypeScript
- 规模：78 行，3,056 bytes
- SHA-256：`7ab7afb2036dc78bf80e191178c29b4db0f0a5cfe83879e9dcad421fa7837c0b`
- 源码事实摘要：验证 flowFragmentComposer.test 对应的行为、回归或合同。
- 顶层符号/结构：—
- 直接静态依赖：`@testing-library/react`, `@testing-library/user-event`, `vitest`, `../components/FlowFragment/FlowFragmentComposer`, `../store`, `../utils/flowFragmentProposal`
- API/IPC 入口：—

<!-- PROJECT-FILE: src/renderer/test/flowFragmentProposal.test.ts -->
### `src/renderer/test/flowFragmentProposal.test.ts`

[打开文件](<../../src/renderer/test/flowFragmentProposal.test.ts>)

- 分类/语言：`test` / TypeScript
- 规模：166 行，6,990 bytes
- SHA-256：`73bf818120e0ad8bf668365a31b09218862b5f8643715e38915e2d0223d492e8`
- 源码事实摘要：验证 flowFragmentProposal.test 对应的行为、回归或合同。
- 顶层符号/结构：—
- 直接静态依赖：`vitest`, `../store`, `../utils/flowFragmentProposal`
- API/IPC 入口：—

<!-- PROJECT-FILE: src/renderer/test/flowNodeDrag.test.tsx -->
### `src/renderer/test/flowNodeDrag.test.tsx`

[打开文件](<../../src/renderer/test/flowNodeDrag.test.tsx>)

- 分类/语言：`test` / React TypeScript
- 规模：55 行，2,695 bytes
- SHA-256：`3d76b1bc31aa33744077642e3598a8d1a7971d62ae23cf39dd61de559f141bb4`
- 源码事实摘要：验证 flowNodeDrag.test 对应的行为、回归或合同。
- 顶层符号/结构：—
- 直接静态依赖：`@testing-library/react`, `vitest`, `../components/Canvas/FlowNode`, `../store`
- API/IPC 入口：—

<!-- PROJECT-FILE: src/renderer/test/goalRunEntry.test.tsx -->
### `src/renderer/test/goalRunEntry.test.tsx`

[打开文件](<../../src/renderer/test/goalRunEntry.test.tsx>)

- 分类/语言：`test` / React TypeScript
- 规模：1157 行，39,879 bytes
- SHA-256：`2df4fd4329e88f6e396adb87c971ad7ccf1106760769282d27c6d874430a8baa`
- 源码事实摘要：验证 goalRunEntry.test 对应的行为、回归或合同。
- 顶层符号/结构：`target`, `snapshot`, `goalRunStart`
- 直接静态依赖：`@testing-library/react`, `@testing-library/user-event`, `vitest`, `../components/platform/PlatformWorkbench`, `../platform/client`, `../platform/contracts`, `../platform/useControlPlane`
- API/IPC 入口：—

<!-- PROJECT-FILE: src/renderer/test/interactionRuntimeView.test.tsx -->
### `src/renderer/test/interactionRuntimeView.test.tsx`

[打开文件](<../../src/renderer/test/interactionRuntimeView.test.tsx>)

- 分类/语言：`test` / React TypeScript
- 规模：394 行，17,335 bytes
- SHA-256：`41e094fe9f51ea4b7b12d74248b78010c51eb24aa9fcfdfe7934b013e454baa0`
- 源码事实摘要：验证 interactionRuntimeView.test 对应的行为、回归或合同。
- 顶层符号/结构：`interactiveContract`, `snapshot`
- 直接静态依赖：`@testing-library/react`, `vitest`, `../components/platform/PlatformWorkbench`, `../platform/contracts`
- API/IPC 入口：—

<!-- PROJECT-FILE: src/renderer/test/macroConverter.test.ts -->
### `src/renderer/test/macroConverter.test.ts`

[打开文件](<../../src/renderer/test/macroConverter.test.ts>)

- 分类/语言：`test` / TypeScript
- 规模：86 行，3,363 bytes
- SHA-256：`e6d7e29c8e0e9878879d1d941763238547b036cf9447347c0f6b75a16970a6fc`
- 源码事实摘要：验证 macroConverter.test 对应的行为、回归或合同。
- 顶层符号/结构：`evt`
- 直接静态依赖：`vitest`, `../utils/macroConverter`, `../types`
- API/IPC 入口：—

<!-- PROJECT-FILE: src/renderer/test/macroPaths.test.ts -->
### `src/renderer/test/macroPaths.test.ts`

[打开文件](<../../src/renderer/test/macroPaths.test.ts>)

- 分类/语言：`test` / TypeScript
- 规模：18 行，690 bytes
- SHA-256：`3ff27a69f67012b26b92adb81a1aad6645bebf5c3393054bf83ef648ffeaec5e`
- 源码事实摘要：验证 macroPaths.test 对应的行为、回归或合同。
- 顶层符号/结构：—
- 直接静态依赖：`vitest`, `path`, `../../main/macroPaths`
- API/IPC 入口：—

<!-- PROJECT-FILE: src/renderer/test/nodeDefinitions.test.ts -->
### `src/renderer/test/nodeDefinitions.test.ts`

[打开文件](<../../src/renderer/test/nodeDefinitions.test.ts>)

- 分类/语言：`test` / TypeScript
- 规模：123 行，4,236 bytes
- SHA-256：`e1b92cccc890475daa545582585776e6f50278296e47cffc7dc25d0a76bbbcb8`
- 源码事实摘要：@vitest-environment jsdom
- 顶层符号/结构：—
- 直接静态依赖：`vitest`, `../utils/nodeDefinitions`
- API/IPC 入口：—

<!-- PROJECT-FILE: src/renderer/test/nodeLibraryCapabilities.test.tsx -->
### `src/renderer/test/nodeLibraryCapabilities.test.tsx`

[打开文件](<../../src/renderer/test/nodeLibraryCapabilities.test.tsx>)

- 分类/语言：`test` / React TypeScript
- 规模：68 行，3,066 bytes
- SHA-256：`a578cb3eaf63775e92bd8b53d53251fbb184a1c2795a5216b269538abe7ec6de`
- 源码事实摘要：验证 nodeLibraryCapabilities.test 对应的行为、回归或合同。
- 顶层符号/结构：`capability`, `modelCapability`
- 直接静态依赖：`@testing-library/react`, `vitest`, `../components/NodeLibrary/NodeLibrary`, `../platform/contracts`, `../platform/flowToArpGraph`, `../store`
- API/IPC 入口：—

<!-- PROJECT-FILE: src/renderer/test/platformContracts.test.ts -->
### `src/renderer/test/platformContracts.test.ts`

[打开文件](<../../src/renderer/test/platformContracts.test.ts>)

- 分类/语言：`test` / TypeScript
- 规模：712 行，32,250 bytes
- SHA-256：`7c24072977877001f02374d13b36a27866a0748d09bf831b07c8c4459ccb4cfc`
- 源码事实摘要：验证 platformContracts.test 对应的行为、回归或合同。
- 顶层符号/结构：`scriptFixture`
- 直接静态依赖：`vitest`, `../types`, `../platform/client`, `../platform/flowToArpGraph`, `../utils/automationIR`, `../utils/nodeDefinitions`, `../store/defaults`
- API/IPC 入口：—

<!-- PROJECT-FILE: src/renderer/test/propertyPanelHistory.test.tsx -->
### `src/renderer/test/propertyPanelHistory.test.tsx`

[打开文件](<../../src/renderer/test/propertyPanelHistory.test.tsx>)

- 分类/语言：`test` / React TypeScript
- 规模：182 行，8,767 bytes
- SHA-256：`735bae172df89fb3e558dfa64ab3012ad75e0e515f2e964001f279a9b87d0bab`
- 源码事实摘要：验证 propertyPanelHistory.test 对应的行为、回归或合同。
- 顶层符号/结构：—
- 直接静态依赖：`@testing-library/react`, `vitest`, `../components/PropertyPanel/PropertyPanel`, `../store`
- API/IPC 入口：—

<!-- PROJECT-FILE: src/renderer/test/pseudoParser.test.ts -->
### `src/renderer/test/pseudoParser.test.ts`

[打开文件](<../../src/renderer/test/pseudoParser.test.ts>)

- 分类/语言：`test` / TypeScript
- 规模：237 行，9,236 bytes
- SHA-256：`62b2e17aaa80dbf484cabf19fbf35b8dd7a5b96266b2fd811f352555f0aa4478`
- 源码事实摘要：验证 pseudoParser.test 对应的行为、回归或合同。
- 顶层符号/结构：—
- 直接静态依赖：`vitest`, `../utils/pseudoParser`
- API/IPC 入口：—

<!-- PROJECT-FILE: src/renderer/test/runsView.test.tsx -->
### `src/renderer/test/runsView.test.tsx`

[打开文件](<../../src/renderer/test/runsView.test.tsx>)

- 分类/语言：`test` / React TypeScript
- 规模：233 行，8,352 bytes
- SHA-256：`c31daf148529a5f75f9dc64245b8cdc07021f9f4f04181cd3b22958e2dd63fef`
- 源码事实摘要：验证 runsView.test 对应的行为、回归或合同。
- 顶层符号/结构：`historicalRun`, `historicalContract`
- 直接静态依赖：`@testing-library/react`, `vitest`, `../components/platform/PlatformWorkbench`, `../platform/contracts`
- API/IPC 入口：—

<!-- PROJECT-FILE: src/renderer/test/setup.ts -->
### `src/renderer/test/setup.ts`

[打开文件](<../../src/renderer/test/setup.ts>)

- 分类/语言：`test` / TypeScript
- 规模：1 行，36 bytes
- SHA-256：`a46d66851af2c056e805fdd574bf5ec3adb1181c43c5e41f0a1c592e338afe64`
- 源码事实摘要：验证 setup 对应的行为、回归或合同。
- 顶层符号/结构：—
- 直接静态依赖：`@testing-library/jest-dom`
- API/IPC 入口：—

<!-- PROJECT-FILE: src/renderer/test/store.test.ts -->
### `src/renderer/test/store.test.ts`

[打开文件](<../../src/renderer/test/store.test.ts>)

- 分类/语言：`test` / TypeScript
- 规模：486 行，21,857 bytes
- SHA-256：`8bffbd51472b1452d9e0b729fd8b238421e5745203cfd7146673c75fd11de0e4`
- 源码事实摘要：验证 store.test 对应的行为、回归或合同。
- 顶层符号/结构：`mockCleanup`, `mockElectronAPI`
- 直接静态依赖：`vitest`, `../store/defaults`, `../store`, `../platform/flowToArpGraph`
- API/IPC 入口：—

<!-- PROJECT-FILE: src/renderer/test/studioResponsive.test.ts -->
### `src/renderer/test/studioResponsive.test.ts`

[打开文件](<../../src/renderer/test/studioResponsive.test.ts>)

- 分类/语言：`test` / TypeScript
- 规模：27 行，1,363 bytes
- SHA-256：`a05c28e8788e29e17e859a69ebd343c31f6e4c0393e1b2977c0bd7a6d7ebdb06`
- 源码事实摘要：验证 studioResponsive.test 对应的行为、回归或合同。
- 顶层符号/结构：—
- 直接静态依赖：`vitest`, `../styles/platform.css?raw`
- API/IPC 入口：—

<!-- PROJECT-FILE: src/renderer/test/taskbarNotification.test.ts -->
### `src/renderer/test/taskbarNotification.test.ts`

[打开文件](<../../src/renderer/test/taskbarNotification.test.ts>)

- 分类/语言：`test` / TypeScript
- 规模：151 行，4,819 bytes
- SHA-256：`af6d3aad914ee77fe22b28ed7acf9e9e14ea1bf125c12aa44b003831586e50c1`
- 源码事实摘要：验证 taskbarNotification.test 对应的行为、回归或合同。
- 顶层符号/结构：`run`
- 直接静态依赖：`vitest`, `../platform/contracts`, `../platform/taskbarNotification`
- API/IPC 入口：—

<!-- PROJECT-FILE: src/renderer/test/templateMatcher.test.ts -->
### `src/renderer/test/templateMatcher.test.ts`

[打开文件](<../../src/renderer/test/templateMatcher.test.ts>)

- 分类/语言：`test` / TypeScript
- 规模：78 行，2,486 bytes
- SHA-256：`88a8cbd211d5b64b2dff55c0afbb8700440eb983846949f7a8646f1bc6fcc44a`
- 源码事实摘要：验证 templateMatcher.test 对应的行为、回归或合同。
- 顶层符号/结构：—
- 直接静态依赖：`vitest`, `../utils/templateMatcher`, `../utils/templateRegistry`
- API/IPC 入口：—

<!-- PROJECT-FILE: src/renderer/test/templateRegistry.test.ts -->
### `src/renderer/test/templateRegistry.test.ts`

[打开文件](<../../src/renderer/test/templateRegistry.test.ts>)

- 分类/语言：`test` / TypeScript
- 规模：124 行，5,015 bytes
- SHA-256：`c1bff2587b74dad93b563d4629733531e9a9318a3f42863a2ba836087ae70dc8`
- 源码事实摘要：验证 templateRegistry.test 对应的行为、回归或合同。
- 顶层符号/结构：`makeEntry`
- 直接静态依赖：`vitest`, `../utils/templateRegistry`
- API/IPC 入口：—

<!-- PROJECT-FILE: src/renderer/test/textGenerator.test.ts -->
### `src/renderer/test/textGenerator.test.ts`

[打开文件](<../../src/renderer/test/textGenerator.test.ts>)

- 分类/语言：`test` / TypeScript
- 规模：559 行，18,213 bytes
- SHA-256：`bb44ae32146998ae7e2c9f380b766efd712512fc515c3c1eedd1cc6fcd1f811b`
- 源码事实摘要：验证 textGenerator.test 对应的行为、回归或合同。
- 顶层符号/结构：`makeFlow`
- 直接静态依赖：`vitest`, `../utils/textGenerator`, `../types`
- API/IPC 入口：—

<!-- PROJECT-FILE: src/renderer/test/visualInteractionTarget.test.ts -->
### `src/renderer/test/visualInteractionTarget.test.ts`

[打开文件](<../../src/renderer/test/visualInteractionTarget.test.ts>)

- 分类/语言：`test` / TypeScript
- 规模：65 行，2,204 bytes
- SHA-256：`f43843ef91d7d1168366e2a661ebe98c51df8613782b6b52b40e417f65068101`
- 源码事实摘要：验证 visualInteractionTarget.test 对应的行为、回归或合同。
- 顶层符号/结构：`targetHtml`
- 直接静态依赖：`node:fs`, `node:path`, `jsdom`, `vitest`
- API/IPC 入口：—

<!-- PROJECT-FILE: src/renderer/test/winpilotServer.test.ts -->
### `src/renderer/test/winpilotServer.test.ts`

[打开文件](<../../src/renderer/test/winpilotServer.test.ts>)

- 分类/语言：`test` / TypeScript
- 规模：128 行，4,644 bytes
- SHA-256：`d480533016f3082bd945dc10e52aede2200f8af6c88b11daf0b73e4ee2c8bf78`
- 源码事实摘要：验证 winpilotServer.test 对应的行为、回归或合同。
- 顶层符号/结构：`compatibleRuntime`
- 直接静态依赖：`vitest`, `http`, `net`, `../../main/winpilotServer`
- API/IPC 入口：—

<!-- PROJECT-FILE: src/renderer/test/wpsToFlowScript.test.ts -->
### `src/renderer/test/wpsToFlowScript.test.ts`

[打开文件](<../../src/renderer/test/wpsToFlowScript.test.ts>)

- 分类/语言：`test` / TypeScript
- 规模：113 行，4,504 bytes
- SHA-256：`768df18434a45d558df4f2c72279da2b03bd8e9a86537783aab587f462169f65`
- 源码事实摘要：验证 wpsToFlowScript.test 对应的行为、回归或合同。
- 顶层符号/结构：`replayNodes`
- 直接静态依赖：`vitest`, `../types`, `../utils/wpsToFlowScript`, `, () => {
  it(`
- API/IPC 入口：—

<!-- PROJECT-FILE: src/renderer/types/electron.d.ts -->
### `src/renderer/types/electron.d.ts`

[打开文件](<../../src/renderer/types/electron.d.ts>)

- 分类/语言：`electron-or-typescript-product` / TypeScript
- 规模：142 行，5,427 bytes
- SHA-256：`7ddbd4941cacb6db2aa132ea0bd926d97e42bb436d50c8ce876adcd18be6084f`
- 源码事实摘要：Electron preload API 类型声明 */
- 顶层符号/结构：`ElectronAPI`
- 直接静态依赖：—
- API/IPC 入口：—

<!-- PROJECT-FILE: src/renderer/types/index.ts -->
### `src/renderer/types/index.ts`

[打开文件](<../../src/renderer/types/index.ts>)

- 分类/语言：`electron-or-typescript-product` / TypeScript
- 规模：953 行，21,650 bytes
- SHA-256：`dda2e673cf91b41dce600246b2ee57884c3e0aaca4b56f1963ada064a8af65bb`
- 源码事实摘要：============================================================
- 顶层符号/结构：`NodeType`, `ConditionType`, `ClickMethod`, `WaitType`, `LoopType`, `KeyModifier`, `StartNodeParams`, `EndNodeParams`, `ClickNodeParams`, `ImageTarget`, `ImageClickNodeParams`, `WaitNodeParams`, `InputTextNodeParams`, `KeyboardNodeParams`，另有 86 项
- 直接静态依赖：—
- API/IPC 入口：—

<!-- PROJECT-FILE: src/renderer/utils/automationIR.ts -->
### `src/renderer/utils/automationIR.ts`

[打开文件](<../../src/renderer/utils/automationIR.ts>)

- 分类/语言：`electron-or-typescript-product` / TypeScript
- 规模：849 行，36,886 bytes
- SHA-256：`ac6018c75b0002f0a637e603d6d62b0848796ea8e9339edd5ceb4363e6c51fbc`
- 源码事实摘要：实现或支撑 automationIR 模块；精确公开面见下列静态符号与依赖。
- 顶层符号/结构：`AutomationRuntime`, `AutomationIR`, `DESKTOP_MUTATION_TOOLS`, `SHELL_TOOLS`, `clone`, `nowSeconds`, `created`, `str`, `record`, `preservedWpsMetadata`, `canonicalEvidence`, `walkWpsSteps`, `defaultSafetyForWps`, `targetWindowFromWps`，另有 22 项
- 直接静态依赖：`nanoid`, `../types`, `../platform/flowToArpGraph`, `../platform/contracts`, `./nodeDefinitions`, `./wpsToFlowScript`
- API/IPC 入口：—

<!-- PROJECT-FILE: src/renderer/utils/constants.ts -->
### `src/renderer/utils/constants.ts`

[打开文件](<../../src/renderer/utils/constants.ts>)

- 分类/语言：`electron-or-typescript-product` / TypeScript
- 规模：71 行，2,109 bytes
- SHA-256：`2a3020da335c6eea5366edbc5d28d3d80768ce1abc7d01ab77061f68db28f8d8`
- 源码事实摘要："use client";
- 顶层符号/结构：`NODE_DIMENSIONS`, `getNodeDimensions`, `ViewMode`, `ExecutionStatus`, `DEFAULT_FLOW_NAME`, `DEFAULT_WAIT_TIMEOUT`, `DEFAULT_SIMILARITY`, `DEFAULT_LLM_API_BASE`, `DEFAULT_LLM_MODEL`, `STORAGE_KEY_API_KEY`, `STORAGE_KEY_API_BASE`, `STORAGE_KEY_MODEL`, `CANVAS_BACKGROUND_COLOR`, `SNAP_TO_GRID`，另有 6 项
- 直接静态依赖：—
- API/IPC 入口：—

<!-- PROJECT-FILE: src/renderer/utils/displayLabels.ts -->
### `src/renderer/utils/displayLabels.ts`

[打开文件](<../../src/renderer/utils/displayLabels.ts>)

- 分类/语言：`electron-or-typescript-product` / TypeScript
- 规模：58 行，1,487 bytes
- SHA-256：`41c70c270ee7816f6c1bdad9120b5e2a96282f0dfce649f7cd2cef353660e0aa`
- 源码事实摘要：实现或支撑 displayLabels 模块；精确公开面见下列静态符号与依赖。
- 顶层符号/结构：`SEARCH_TOKEN_LABELS`, `SOURCE_LABELS`, `displaySearchToken`, `displaySourceLabel`, `displaySearchReason`
- 直接静态依赖：—
- API/IPC 入口：—

<!-- PROJECT-FILE: src/renderer/utils/editorDrafts.ts -->
### `src/renderer/utils/editorDrafts.ts`

[打开文件](<../../src/renderer/utils/editorDrafts.ts>)

- 分类/语言：`electron-or-typescript-product` / TypeScript
- 规模：20 行，579 bytes
- SHA-256：`f53d17f14ad641782e49e89b620f85a4b84ee6f99ddce4c5b5fa3598b12a2197`
- 源码事实摘要：实现或支撑 editorDrafts 模块；精确公开面见下列静态符号与依赖。
- 顶层符号/结构：`EditorDraftCommitter`, `committers`, `registerEditorDraftCommitter`, `commitEditorDrafts`
- 直接静态依赖：—
- API/IPC 入口：—

<!-- PROJECT-FILE: src/renderer/utils/flowFragmentProposal.ts -->
### `src/renderer/utils/flowFragmentProposal.ts`

[打开文件](<../../src/renderer/utils/flowFragmentProposal.ts>)

- 分类/语言：`electron-or-typescript-product` / TypeScript
- 规模：334 行，13,729 bytes
- SHA-256：`d69d2a0ba6978abb31bc6e7fbdc09f00bf00982ce0c534038feac6aebd95d84e`
- 源码事实摘要：实现或支撑 flowFragmentProposal 模块；精确公开面见下列静态符号与依赖。
- 顶层符号/结构：`FLOW_FRAGMENT_PROPOSAL_FORMAT`, `FlowFragmentNodeDraft`, `FlowFragmentEdgeDraft`, `FlowFragmentProposal`, `FlowFragmentRequestContext`, `FLOW_FRAGMENT_PARAM_KEYS`, `INSERTABLE_NODE_TYPES`, `INSERTABLE_NODE_TYPE_SET`, `modelParams`, `record`, `text`, `flowDocumentFingerprint`, `buildFlowFragmentRequestContext`, `normalizeFlowFragmentProposal`，另有 3 项
- 直接静态依赖：`../types`, `../platform/flowToArpGraph`, `./automationIR`, `./nodeDefinitions`
- API/IPC 入口：—

<!-- PROJECT-FILE: src/renderer/utils/itemMarker.ts -->
### `src/renderer/utils/itemMarker.ts`

[打开文件](<../../src/renderer/utils/itemMarker.ts>)

- 分类/语言：`electron-or-typescript-product` / TypeScript
- 规模：86 行，2,023 bytes
- SHA-256：`f523891b9dd485b9d1273c90c36ce42e8bd483b8c080f2ac2bc64e8ada2d2894`
- 源码事实摘要：实现或支撑 itemMarker 模块；精确公开面见下列静态符号与依赖。
- 顶层符号/结构：`MarkRecord`, `STORAGE_KEY`, `ItemMarker`, `itemMarker`
- 直接静态依赖：—
- API/IPC 入口：—

<!-- PROJECT-FILE: src/renderer/utils/macroConverter.ts -->
### `src/renderer/utils/macroConverter.ts`

[打开文件](<../../src/renderer/utils/macroConverter.ts>)

- 分类/语言：`electron-or-typescript-product` / TypeScript
- 规模：192 行，5,764 bytes
- SHA-256：`d1b8d591d9729a59c0c7d27fe7c1399c05ca1ce17912959f22d42afe4fe3b2e8`
- 源码事实摘要：实现或支撑 macroConverter 模块；精确公开面见下列静态符号与依赖。
- 顶层符号/结构：`macroEventsToFlow`
- 直接静态依赖：`nanoid`, `../types`
- API/IPC 入口：—

<!-- PROJECT-FILE: src/renderer/utils/nodeDefinitions.ts -->
### `src/renderer/utils/nodeDefinitions.ts`

[打开文件](<../../src/renderer/utils/nodeDefinitions.ts>)

- 分类/语言：`electron-or-typescript-product` / TypeScript
- 规模：907 行，26,911 bytes
- SHA-256：`4f752fadac8496a5a4b5a35dcd96d734217579cd8eca5228c2e1f1792626cd50`
- 源码事实摘要：实现或支撑 nodeDefinitions 模块；精确公开面见下列静态符号与依赖。
- 顶层符号/结构：`NODE_DEFINITIONS`, `NODE_CATEGORIES`, `getNodeDefinition`, `getNodesByCategory`
- 直接静态依赖：`../types`
- API/IPC 入口：—

<!-- PROJECT-FILE: src/renderer/utils/nodeIconMap.ts -->
### `src/renderer/utils/nodeIconMap.ts`

[打开文件](<../../src/renderer/utils/nodeIconMap.ts>)

- 分类/语言：`electron-or-typescript-product` / TypeScript
- 规模：24 行，1,255 bytes
- SHA-256：`16ea76656c7fdf825390c2ef98abe430ea602f8a79cc4dfa13709f64ceb4a773`
- 源码事实摘要：实现或支撑 nodeIconMap 模块；精确公开面见下列静态符号与依赖。
- 顶层符号/结构：`NODE_ICONS`, `getNodeIcon`
- 直接静态依赖：`lucide-react`
- API/IPC 入口：—

<!-- PROJECT-FILE: src/renderer/utils/pseudoParser.ts -->
### `src/renderer/utils/pseudoParser.ts`

[打开文件](<../../src/renderer/utils/pseudoParser.ts>)

- 分类/语言：`electron-or-typescript-product` / TypeScript
- 规模：1179 行，45,417 bytes
- SHA-256：`6e4dd8f79988eb7c810f45caa319ba2b932f9f4c2d04543c30f1c5292a772b79`
- 源码事实摘要：﻿import { FlowScript, FlowNode, FlowEdge, NodeType, NodeParams } from '../types';
- 顶层符号/结构：`ParseLine`, `parseIndentation`, `ParsedStmt`, `parseStmt`, `parseConditionExpr`, `buildTree`, `DEFAULT_SETTINGS`, `getDefaultParams`, `buildFlowScript`, `parsePseudoToFlow`
- 直接静态依赖：`../types`, `nanoid`
- API/IPC 入口：—

<!-- PROJECT-FILE: src/renderer/utils/templateMatcher.ts -->
### `src/renderer/utils/templateMatcher.ts`

[打开文件](<../../src/renderer/utils/templateMatcher.ts>)

- 分类/语言：`electron-or-typescript-product` / TypeScript
- 规模：46 行，1,702 bytes
- SHA-256：`ac773a1a647062ef36b612cd675c20c209ad894c9c73fa5ea0fcc8850242e157`
- 源码事实摘要：实现或支撑 templateMatcher 模块；精确公开面见下列静态符号与依赖。
- 顶层符号/结构：`ExactMatchResult`, `stripChineseVerbs`, `matchExact`
- 直接静态依赖：`./templateRegistry`
- API/IPC 入口：—

<!-- PROJECT-FILE: src/renderer/utils/templateRegistry.ts -->
### `src/renderer/utils/templateRegistry.ts`

[打开文件](<../../src/renderer/utils/templateRegistry.ts>)

- 分类/语言：`electron-or-typescript-product` / TypeScript
- 规模：184 行，5,971 bytes
- SHA-256：`f457c5026ae72e2c0ecbbc22e5240157d9fb78a45c7fc522ea00bbb46a9594af`
- 源码事实摘要：实现或支撑 templateRegistry 模块；精确公开面见下列静态符号与依赖。
- 顶层符号/结构：`TemplateEntry`, `ScoredResult`, `TemplateRegistry`, `templateRegistry`
- 直接静态依赖：`../types`
- API/IPC 入口：—

<!-- PROJECT-FILE: src/renderer/utils/textGenerator.ts -->
### `src/renderer/utils/textGenerator.ts`

[打开文件](<../../src/renderer/utils/textGenerator.ts>)

- 分类/语言：`electron-or-typescript-product` / TypeScript
- 规模：938 行，38,916 bytes
- SHA-256：`087cd9b8159c2ddf6ce134bb9fa1fab04f0b85d7161dd970c82c9e7cee099cd7`
- 源码事实摘要：Unified Text Generator
- 顶层符号/结构：`TextMode`, `GenState`, `emit`, `getEdge`, `collectReachable`, `findConvergence`, `dslGenClick`, `dslGenWait`, `dslGenType`, `dslGenKeyboard`, `dslGenSwipe`, `dslGenImageClick`, `dslGenScreenshot`, `dslGenVariableSet`，另有 37 项
- 直接静态依赖：`../types`
- API/IPC 入口：—

<!-- PROJECT-FILE: src/renderer/utils/unifiedSearch.ts -->
### `src/renderer/utils/unifiedSearch.ts`

[打开文件](<../../src/renderer/utils/unifiedSearch.ts>)

- 分类/语言：`electron-or-typescript-product` / TypeScript
- 规模：244 行，7,603 bytes
- SHA-256：`0bb501012251f03e9cf2bbf673fefee9495661cc0dfde8af6e096d3933bc276b`
- 源码事实摘要：实现或支撑 unifiedSearch 模块；精确公开面见下列静态符号与依赖。
- 顶层符号/结构：`SearchItemType`, `UnifiedSearchItem`, `UnifiedSearchResult`, `UnifiedSearchOptions`, `VERB_PREFIXES`, `normalizeQuery`, `UnifiedSearchEngine`, `unifiedSearch`
- 直接静态依赖：`../types`, `./templateRegistry`, `./itemMarker`, `./nodeDefinitions`, `../platform/flowToArpGraph`
- API/IPC 入口：—

<!-- PROJECT-FILE: src/renderer/utils/wpsToFlowScript.ts -->
### `src/renderer/utils/wpsToFlowScript.ts`

[打开文件](<../../src/renderer/utils/wpsToFlowScript.ts>)

- 分类/语言：`electron-or-typescript-product` / TypeScript
- 规模：153 行，4,738 bytes
- SHA-256：`023406e1a019716171c371e9c3c17dc11b1acdafb33a6ab1c543482dece2cf7a`
- 源码事实摘要：实现或支撑 wpsToFlowScript 模块；精确公开面见下列静态符号与依赖。
- 顶层符号/结构：`WpsStep`, `WpsScript`, `X`, `Y_STEP`, `clone`, `sourceDocument`, `replayTarget`, `replayLabel`, `wpsToFlowScript`
- 直接静态依赖：`nanoid`, `../types`, `, `
- API/IPC 入口：—

<!-- PROJECT-FILE: src/renderer/vite-env.d.ts -->
### `src/renderer/vite-env.d.ts`

[打开文件](<../../src/renderer/vite-env.d.ts>)

- 分类/语言：`electron-or-typescript-product` / TypeScript
- 规模：1 行，38 bytes
- SHA-256：`65996936fbb042915f7b74a200fcdde7e410f32a669b1ab9597cfaa4b0faddb5`
- 源码事实摘要：<reference types="vite/client" />
- 顶层符号/结构：—
- 直接静态依赖：—
- API/IPC 入口：—

<!-- PROJECT-FILE: src/shared/taskOutcome.ts -->
### `src/shared/taskOutcome.ts`

[打开文件](<../../src/shared/taskOutcome.ts>)

- 分类/语言：`electron-or-typescript-product` / TypeScript
- 规模：163 行，5,322 bytes
- SHA-256：`3567c30f4af600e96373ee56b53b2bfb9e9f82ddc41a2f5a0a790daf06d6b153`
- 源码事实摘要：实现或支撑 taskOutcome 模块；精确公开面见下列静态符号与依赖。
- 顶层符号/结构：`TaskOutcomeKind`, `UNCONDITIONAL_SUCCESS_STATUSES`, `STOPPED_STATUSES`, `FAILURE_STATUSES`, `TERMINAL_STATUSES`, `FAILURE_TRUE_KEYS`, `FAILURE_FALSE_KEYS`, `FAILURE_VALUE_KEYS`, `MAX_INSPECTION_DEPTH`, `MAX_INSPECTION_NODES`, `parsedJson`, `hasFailureValue`, `inspectFailureSignal`, `hasExplicitTaskFailure`，另有 7 项
- 直接静态依赖：—
- API/IPC 入口：—

## `start-dev.ps1`

<!-- PROJECT-FILE: start-dev.ps1 -->
### `start-dev.ps1`

[打开文件](<../../start-dev.ps1>)

- 分类/语言：`developer-script` / PowerShell
- 规模：21 行，972 bytes
- SHA-256：`af9aa270b61b8d906b4313d66e4d32a4d4c48e8029eacf5a6e49866a1687431b`
- 源码事实摘要：Kill existing processes
- 顶层符号/结构：—
- 直接静态依赖：—
- API/IPC 入口：—

## `templates`

<!-- PROJECT-FILE: templates/1.png -->
### `templates/1.png`

[打开文件](<../../templates/1.png>)

- 分类/语言：`asset-or-binary` / PNG image
- 规模：binary 行，557 bytes
- SHA-256：`713e2aaeabc9600bf5199aea835c0c0c49ba788d78dd7e8cf47cf3bf8152f8b3`
- 源码事实摘要：PNG 67x41
- 顶层符号/结构：—
- 直接静态依赖：—
- API/IPC 入口：—
- 解析说明：binary

<!-- PROJECT-FILE: templates/login-workflow.dsl -->
### `templates/login-workflow.dsl`

[打开文件](<../../templates/login-workflow.dsl>)

- 分类/语言：`fixture-or-template` / Flow DSL
- 规模：31 行，644 bytes
- SHA-256：`b14f3d8601c0f9f8e8aa0b7ce918380a95ba3bcde1511680141f65098d6f747b`
- 源码事实摘要：---
- 顶层符号/结构：—
- 直接静态依赖：—
- API/IPC 入口：—

<!-- PROJECT-FILE: templates/monitor-workflow.dsl -->
### `templates/monitor-workflow.dsl`

[打开文件](<../../templates/monitor-workflow.dsl>)

- 分类/语言：`fixture-or-template` / Flow DSL
- 规模：25 行，553 bytes
- SHA-256：`8025640bb6c6493679c14aa6842c111dc89d88d1c03a4b648bec3459c4769901`
- 源码事实摘要：---
- 顶层符号/结构：—
- 直接静态依赖：—
- API/IPC 入口：—

<!-- PROJECT-FILE: templates/paint-drawing.dsl -->
### `templates/paint-drawing.dsl`

[打开文件](<../../templates/paint-drawing.dsl>)

- 分类/语言：`fixture-or-template` / Flow DSL
- 规模：22 行，569 bytes
- SHA-256：`e2aaee858bbcff62252b46bd9e86b0827f37b73e906cbb7c52988c35f6bfd808`
- 源码事实摘要：---
- 顶层符号/结构：—
- 直接静态依赖：—
- API/IPC 入口：—

<!-- PROJECT-FILE: templates/screenshot-workflow.dsl -->
### `templates/screenshot-workflow.dsl`

[打开文件](<../../templates/screenshot-workflow.dsl>)

- 分类/语言：`fixture-or-template` / Flow DSL
- 规模：21 行，547 bytes
- SHA-256：`55afe4d65b4889e49c413aaf3c901417cde7a774109fc92394be0fd11c05f779`
- 源码事实摘要：---
- 顶层符号/结构：—
- 直接静态依赖：—
- API/IPC 入口：—

<!-- PROJECT-FILE: templates/skill_tree.json -->
### `templates/skill_tree.json`

[打开文件](<../../templates/skill_tree.json>)

- 分类/语言：`fixture-or-template` / JSON
- 规模：22 行，508 bytes
- SHA-256：`b3b3867868b387e793337198972d95578e390d98a49c07ad2f481c71ae2b7d00`
- 源码事实摘要：skill tree 的流程、模板或测试 fixture 数据。
- 顶层符号/结构：`key:version`, `key:categories`
- 直接静态依赖：—
- API/IPC 入口：—

<!-- PROJECT-FILE: templates/保存文件对话框.flow -->
### `templates/保存文件对话框.flow`

[打开文件](<../../templates/保存文件对话框.flow>)

- 分类/语言：`fixture-or-template` / FlowScript JSON
- 规模：74 行，1,926 bytes
- SHA-256：`a808c90fde00bec772957bb4c0c853dfa4bc1fc31e739310f653b8f31b41d9c5`
- 源码事实摘要：保存文件对话框 的流程、模板或测试 fixture 数据。
- 顶层符号/结构：`key:nodes`, `key:edges`, `key:meta`, `key:selection`, `key:viewMode`, `key:ui`, `node:start×1`, `node:keyboard×1`, `node:tool_call×1`, `node:end×1`
- 直接静态依赖：—
- API/IPC 入口：—

<!-- PROJECT-FILE: templates/打开文件对话框.flow -->
### `templates/打开文件对话框.flow`

[打开文件](<../../templates/打开文件对话框.flow>)

- 分类/语言：`fixture-or-template` / FlowScript JSON
- 规模：73 行，1,897 bytes
- SHA-256：`134b521017fd326bc59703a17ab3e691c157f04733d3d18dfa4b06525c19389f`
- 源码事实摘要：打开文件对话框 的流程、模板或测试 fixture 数据。
- 顶层符号/结构：`key:nodes`, `key:edges`, `key:meta`, `key:selection`, `key:viewMode`, `key:ui`, `node:start×1`, `node:keyboard×1`, `node:tool_call×1`, `node:end×1`
- 直接静态依赖：—
- API/IPC 入口：—

<!-- PROJECT-FILE: templates/测试模板生成.flow -->
### `templates/测试模板生成.flow`

[打开文件](<../../templates/测试模板生成.flow>)

- 分类/语言：`fixture-or-template` / FlowScript JSON
- 规模：163 行，3,280 bytes
- SHA-256：`5172d835c53f7875951cfdff0d1a71778bbc08b587d3e6ee88d4c692d9194f03`
- 源码事实摘要：测试模板生成 的流程、模板或测试 fixture 数据。
- 顶层符号/结构：`key:nodes`, `key:edges`, `key:meta`, `key:selection`, `key:viewMode`, `key:ui`, `node:start×1`, `node:click×1`, `node:wait×1`, `node:input_text×1`, `node:screenshot×1`, `node:end×1`
- 直接静态依赖：—
- API/IPC 入口：—

<!-- PROJECT-FILE: templates/测试结晶.dsl -->
### `templates/测试结晶.dsl`

[打开文件](<../../templates/测试结晶.dsl>)

- 分类/语言：`fixture-or-template` / Flow DSL
- 规模：1 行，8 bytes
- SHA-256：`fce445a81cb36067b0d7a53d73bdb00ea8e0878d1f5a26b11aff521dfafda4e6`
- 源码事实摘要：WAIT 0.5
- 顶层符号/结构：—
- 直接静态依赖：—
- API/IPC 入口：—

<!-- PROJECT-FILE: templates/测试结晶.flow -->
### `templates/测试结晶.flow`

[打开文件](<../../templates/测试结晶.flow>)

- 分类/语言：`fixture-or-template` / FlowScript JSON
- 规模：51 行，929 bytes
- SHA-256：`1ed7407b1927acecdf35c51328ea5f1755a228e5f89d40aeb1540851a9425285`
- 源码事实摘要：测试结晶 的流程、模板或测试 fixture 数据。
- 顶层符号/结构：`key:meta`, `key:nodes`, `key:edges`, `key:variables`, `key:settings`, `node:start×1`, `node:wait×1`, `node:end×1`
- 直接静态依赖：—
- API/IPC 入口：—

## `test_complex.flow`

<!-- PROJECT-FILE: test_complex.flow -->
### `test_complex.flow`

[打开文件](<../../test_complex.flow>)

- 分类/语言：`test` / FlowScript JSON
- 规模：6 行，83 bytes
- SHA-256：`e3c08b21cb7b950ce6889289a45f7ff5c7cb8d45529afa7c64f44bfec69a7fcd`
- 源码事实摘要：验证 test complex 对应的行为、回归或合同。
- 顶层符号/结构：—
- 直接静态依赖：—
- API/IPC 入口：—
- 解析说明：JSON parse failed at line 1: Expecting value

## `tests`

<!-- PROJECT-FILE: tests/conftest.py -->
### `tests/conftest.py`

[打开文件](<../../tests/conftest.py>)

- 分类/语言：`test` / Python
- 规模：16 行，620 bytes
- SHA-256：`8657e4925992d2a01b09ad870b4ecc9ad8e0ffbe355eeb3fc6e4cf74744c3fb2`
- 源码事实摘要：验证 conftest 对应的行为、回归或合同。
- 顶层符号/结构：`pytest_addoption`, `pytest_configure`, `pytest_collection_modifyitems`
- 直接静态依赖：`pytest`
- API/IPC 入口：—

<!-- PROJECT-FILE: tests/test_browser_features.py -->
### `tests/test_browser_features.py`

[打开文件](<../../tests/test_browser_features.py>)

- 分类/语言：`test` / Python
- 规模：53 行，1,705 bytes
- SHA-256：`ea13ef9075779a136f32ebc2de16c16f72f4ecd5f61466b797c0c865d5930abe`
- 源码事实摘要：验证 test browser features 对应的行为、回归或合同。
- 顶层符号/结构：`test_set_value_passes_text_as_playwright_argument`, `test_storage_helpers_pass_keys_and_values_as_arguments`
- 直接静态依赖：`sys`, `pathlib`, `features`
- API/IPC 入口：—

<!-- PROJECT-FILE: tests/test_browser_real_ops.py -->
### `tests/test_browser_real_ops.py`

[打开文件](<../../tests/test_browser_real_ops.py>)

- 分类/语言：`test` / Python
- 规模：274 行，11,535 bytes
- SHA-256：`6bd2a72f92f6b43fa5e2179166345c3393efe6e210b6b0b7d2597d115712ee73`
- 源码事实摘要：浏览器真实操作测试 — 在真实网站上测试浏览器的全部交互能力。
- 顶层符号/结构：`run`, `run_browser_tests`
- 直接静态依赖：`sys`, `os`, `json`, `time`, `features._all`, `features.registry`
- API/IPC 入口：—

<!-- PROJECT-FILE: tests/test_compare_som_vs_direct.py -->
### `tests/test_compare_som_vs_direct.py`

[打开文件](<../../tests/test_compare_som_vs_direct.py>)

- 分类/语言：`test` / Python
- 规模：177 行，5,935 bytes
- SHA-256：`f97965d771bf8d741885a3290affeff7a9ff71db97c55bf3a6e8fdc59b56ba98`
- 源码事实摘要：SoM (text-only) vs Direct (full screenshot) 对比测试。
- 顶层符号/结构：`run_test`, `main`, `const:_PATH`, `const:API_KEY`, `const:API_BASE`, `const:MODEL`, `const:GOAL`
- 直接静态依赖：`json`, `logging`, `os`, `sys`, `time`, `argparse`, `dotenv`
- API/IPC 入口：—

<!-- PROJECT-FILE: tests/test_dialog_features.py -->
### `tests/test_dialog_features.py`

[打开文件](<../../tests/test_dialog_features.py>)

- 分类/语言：`test` / Python
- 规模：378 行，16,124 bytes
- SHA-256：`263442c8267a2c5e4b1e2f64dfbe5cc66fb88f4373520d09adbac7c108b4619d`
- 源码事实摘要：dialog_features @feature 注册表测试。
- 顶层符号/结构：`TestRegistryMetadata`, `TestHandleDialogRouting`, `TestDialogListRouting`, `TestDialogTakeActionRouting`, `TestRegistryIntegration`, `TestExecutorIntegration`, `_make_mock_dlg`
- 直接静态依赖：`__future__`, `sys`, `pathlib`, `unittest.mock`, `pytest`
- API/IPC 入口：—

<!-- PROJECT-FILE: tests/test_dialog_handler.py -->
### `tests/test_dialog_handler.py`

[打开文件](<../../tests/test_dialog_handler.py>)

- 分类/语言：`test` / Python
- 规模：1370 行，54,380 bytes
- SHA-256：`29ebffa7e6a82725a168a89044d7772edbe6bb869f87e9bf9d1c584793b817fb`
- 源码事实摘要：dialog_handler 全面测试套件。
- 顶层符号/结构：`FakeHWND`, `make_button`, `TestDialogInfoScanButtons`, `TestDialogInfoClassify`, `TestButtonFinding`, `TestHasEditField`, `mock_dialog_info`, `TestHandleAnyDialog`, `TestHandlerFunctions`, `TestHelperFunctions`, `TestFindDialogs`, `TestDumpAllDialogs`, `TestEdgeCases`, `TestHandlerFunctionsAdvanced`，另有 1 项
- 直接静态依赖：`__future__`, `json`, `logging`, `os`, `sys`, `time`, `pathlib`, `typing`, `unittest.mock`, `pytest`
- API/IPC 入口：—

<!-- PROJECT-FILE: tests/test_dsl_executor_replay_nodes.py -->
### `tests/test_dsl_executor_replay_nodes.py`

[打开文件](<../../tests/test_dsl_executor_replay_nodes.py>)

- 分类/语言：`test` / Python
- 规模：56 行，1,530 bytes
- SHA-256：`271049be2ec1ffad2f433507ee0cab60c6ff7851110a963f79038878b04f9762`
- 源码事实摘要：验证 test dsl executor replay nodes 对应的行为、回归或合同。
- 顶层符号/结构：`run_with_spy`, `test_input_text_clicks_coordinates_then_types`, `test_click_text_dispatches_to_click_action`, `test_click_image_dispatches_to_image_click_action`
- 直接静态依赖：`os`, `sys`, `engine.executor`
- API/IPC 入口：—

<!-- PROJECT-FILE: tests/test_features_comprehensive.py -->
### `tests/test_features_comprehensive.py`

[打开文件](<../../tests/test_features_comprehensive.py>)

- 分类/语言：`test` / Python
- 规模：305 行，13,557 bytes
- SHA-256：`3887edf8911970149a222e741ccf32671aaca076ae667db91802508282b6e238`
- 源码事实摘要：Comprehensive @feature library test — validates all 178 features:
- 顶层符号/结构：`_all_features`, `_try_exec`, `TestFeatureMetadata`, `TestFeatureInvocation`, `TestConsistency`, `const:DESTRUCTIVE`, `const:GUI_REQUIRED`, `const:AI_API_REQUIRED`, `const:PARAMS_REQUIRED`, `const:ALL_SKIP`
- 直接静态依赖：`sys`, `os`, `json`, `subprocess`, `pytest`, `features._all`, `features.registry`
- API/IPC 入口：—

<!-- PROJECT-FILE: tests/test_flow_converter.py -->
### `tests/test_flow_converter.py`

[打开文件](<../../tests/test_flow_converter.py>)

- 分类/语言：`test` / Python
- 规模：299 行，12,308 bytes
- SHA-256：`4b0f7f04d77a214e6c06d6c36bae6180fe9d5233898d05eed32bdb61ff417a34`
- 源码事实摘要：DSL ↔ FlowScript 双向转换器测试
- 顶层符号/结构：`TestDSLtoFlow`, `TestFlowtoDSL`, `TestRegistryFlowTypes`
- 直接静态依赖：`json`, `sys`, `os`, `pytest`, `engine.dsl_flow_converter`
- API/IPC 入口：—

<!-- PROJECT-FILE: tests/test_flow_full.json -->
### `tests/test_flow_full.json`

[打开文件](<../../tests/test_flow_full.json>)

- 分类/语言：`test` / JSON
- 规模：20 行，1,194 bytes
- SHA-256：`9a0d193887b3acdc64f539469fa134c9e536fdc39bcf09f23595d378c17a51ae`
- 源码事实摘要：验证 test flow full 对应的行为、回归或合同。
- 顶层符号/结构：`key:script`
- 直接静态依赖：—
- API/IPC 入口：—

<!-- PROJECT-FILE: tests/test_flow_utf8.json -->
### `tests/test_flow_utf8.json`

[打开文件](<../../tests/test_flow_utf8.json>)

- 分类/语言：`test` / JSON
- 规模：20 行，1,194 bytes
- SHA-256：`9a0d193887b3acdc64f539469fa134c9e536fdc39bcf09f23595d378c17a51ae`
- 源码事实摘要：验证 test flow utf8 对应的行为、回归或合同。
- 顶层符号/结构：`key:script`
- 直接静态依赖：—
- API/IPC 入口：—

<!-- PROJECT-FILE: tests/test_init_output.txt -->
### `tests/test_init_output.txt`

[打开文件](<../../tests/test_init_output.txt>)

- 分类/语言：`test` / Text
- 规模：9 行，324 bytes
- SHA-256：`2a7765b0d0750faa1cbb63aba6e83a9d9ffaa1e7f6a065f9133175784f901579`
- 源码事实摘要：历史测试输出快照，不代表当前测试结果。
- 顶层符号/结构：—
- 直接静态依赖：—
- API/IPC 入口：—

<!-- PROJECT-FILE: tests/test_interfaces.py -->
### `tests/test_interfaces.py`

[打开文件](<../../tests/test_interfaces.py>)

- 分类/语言：`test` / Python
- 规模：352 行，11,881 bytes
- SHA-256：`f977af894f2cbc4f34a4d0b853bcd9883bea6b406f48577c7e2841d424ca2483`
- 源码事实摘要：Core interface tests — ToolResult, Observation, StepRecord, TaskState, EventBus, StructuredLogger.
- 顶层符号/结构：`TestToolResult`, `TestObservation`, `TestStepRecord`, `TestTaskState`, `TestEventBus`, `TestEvent`, `TestStructuredLogger`
- 直接静态依赖：`json`, `os`, `sys`, `tempfile`, `time`, `pytest`, `core.interfaces`, `core.events`, `core.logging`
- API/IPC 入口：—

<!-- PROJECT-FILE: tests/test_legacy_executor_fail_closed.py -->
### `tests/test_legacy_executor_fail_closed.py`

[打开文件](<../../tests/test_legacy_executor_fail_closed.py>)

- 分类/语言：`test` / Python
- 规模：297 行，8,482 bytes
- SHA-256：`5163aeb91b11db92ee283434a93b3dfd096404afa1d2eba43fc66f0136ea9c63`
- 源码事实摘要：验证 test legacy executor fail closed 对应的行为、回归或合同。
- 顶层符号/结构：`_run`, `test_explicit_failure_results_fail_non_idempotent_action_once`, `test_nested_registry_failure_result_is_rejected`, `test_nested_explicit_negative_result_is_rejected`, `test_double_wrapped_registry_negative_result_is_rejected`, `test_ambiguous_or_error_results_fail_closed`, `test_explicit_status_envelopes_require_literal_true`, `test_explicit_status_envelopes_accept_literal_true`, `test_condition_negative_allows_only_well_formed_success_wrappers`, `test_none_result_fails_action_instead_of_continuing`, `test_false_condition_takes_else_branch_without_retry`, `test_allow_negative_result_does_not_hide_explicit_error`, `test_unknown_registry_keyword_fails_closed`, `test_parallel_branch_failure_propagates_to_flow_result`，另有 3 项
- 直接静态依赖：`io`, `json`, `os`, `sys`, `pytest`, `engine`, `engine.executor`, `engine.executor_types`, `engine.healing`
- API/IPC 入口：—

<!-- PROJECT-FILE: tests/test_macro_path_security.py -->
### `tests/test_macro_path_security.py`

[打开文件](<../../tests/test_macro_path_security.py>)

- 分类/语言：`test` / Python
- 规模：28 行，953 bytes
- SHA-256：`92583f80a0a1060dde70bf027180dbe3404caa40b779c4df43eff3dd50f0444a`
- 源码事实摘要：验证 test macro path security 对应的行为、回归或合同。
- 顶层符号/结构：`test_macro_path_stays_inside_macro_directory`, `test_macro_path_rejects_traversal_and_absolute_paths`
- 直接静态依赖：`pathlib`, `sys`
- API/IPC 入口：—

<!-- PROJECT-FILE: tests/test_nlp_schedule_parser.py -->
### `tests/test_nlp_schedule_parser.py`

[打开文件](<../../tests/test_nlp_schedule_parser.py>)

- 分类/语言：`test` / Python
- 规模：389 行，15,893 bytes
- SHA-256：`35f70a3dfcebfafbb06e95802fc154fe6df547d8a8e9b3ecce8aac8a5a20fa8c`
- 源码事实摘要：自然语言调度解析器测试
- 顶层符号/结构：`TestIsScheduleIntent`, `TestParseTime`, `TestParseActions`, `TestRiskLevel`
- 直接静态依赖：`sys`, `os`, `datetime`, `engine.nlp_schedule_parser`
- API/IPC 入口：—

<!-- PROJECT-FILE: tests/test_param_prompt.ts -->
### `tests/test_param_prompt.ts`

[打开文件](<../../tests/test_param_prompt.ts>)

- 分类/语言：`test` / TypeScript
- 规模：170 行，7,448 bytes
- SHA-256：`52a853d03526aa10a09206c8e96b867c92e50d78824a1bd056d3311c3fd1035b`
- 源码事实摘要：Quick verification of the ParamPrompt logic:
- 顶层符号/结构：`ParamPromptItem`, `REQUIRED_PARAMS_MAP`, `isParamMissing`, `buildParamPromptQueue`, `passed`, `failed`, `assert`, `node1`, `node2`, `q2`, `node3`, `q3`, `node4`, `q4`，另有 8 项
- 直接静态依赖：—
- API/IPC 入口：—

<!-- PROJECT-FILE: tests/test_providers.py -->
### `tests/test_providers.py`

[打开文件](<../../tests/test_providers.py>)

- 分类/语言：`test` / Python
- 规模：228 行，8,196 bytes
- SHA-256：`4e78ab5d1ad66a75fb6df1d7fdf75523f2da9fdde6bc5d647e27bcfc7f92fed0`
- 源码事实摘要：LLM provider tests — DeepSeekProvider and OpenAIProvider with mocked HTTP.
- 顶层符号/结构：`_fake_response`, `TestDeepSeekProvider`, `TestOpenAIProvider`, `TestLLMProviderProtocol`
- 直接静态依赖：`os`, `sys`, `unittest.mock`, `pytest`, `providers.base`, `providers.deepseek`, `providers.openai`
- API/IPC 入口：—

<!-- PROJECT-FILE: tests/test_secure_store.py -->
### `tests/test_secure_store.py`

[打开文件](<../../tests/test_secure_store.py>)

- 分类/语言：`test` / Python
- 规模：42 行，1,450 bytes
- SHA-256：`d6d177fe315f4911157eab4b01179ade1ce55707241a040752a1d61abb5bec5c`
- 源码事实摘要：验证 test secure store 对应的行为、回归或合同。
- 顶层符号/结构：`test_execution_store_encrypts_and_migrates_vault_values`
- 直接静态依赖：`__future__`, `os`, `sqlite3`, `sys`, `pathlib`, `pytest`
- API/IPC 入口：—

<!-- PROJECT-FILE: tests/test_vision.py -->
### `tests/test_vision.py`

[打开文件](<../../tests/test_vision.py>)

- 分类/语言：`test` / Python
- 规模：120 行，4,322 bytes
- SHA-256：`6e81e2a2c83a025633ea383337bef5e4a846ddba3266cf5dc83b19d2016dc672`
- 源码事实摘要：Tests for vision modules (color, progress, change detection).
- 顶层符号/结构：`TestImageUtils`, `TestColorDetector`, `TestProgressDetector`, `TestChangeDetector`
- 直接静态依赖：`sys`, `os`, `time`, `unittest`, `numpy`, `core.screenshot`, `vision.color`, `vision.progress`, `vision.change`, `utils.image`
- API/IPC 入口：—

## `tools`

<!-- PROJECT-FILE: tools/diag_windows.py -->
### `tools/diag_windows.py`

[打开文件](<../../tools/diag_windows.py>)

- 分类/语言：`supporting-code` / Python
- 规模：146 行，4,419 bytes
- SHA-256：`85c10ec67835ee138d1424d2fa84a2c672324e3ce6ed4255f3d834b2683528f8`
- 源码事实摘要：诊断工具: 枚举所有可见窗口的类名和标题。用于识别保存对话框的窗口类。
- 顶层符号/结构：`safe`, `say`, `flush`, `enum_all`, `check_alt_methods`, `check_focus`
- 直接静态依赖：`sys`, `win32gui`, `win32process`, `psutil`, `ctypes`
- API/IPC 入口：—

<!-- PROJECT-FILE: tools/dsl_to_flow.py -->
### `tools/dsl_to_flow.py`

[打开文件](<../../tools/dsl_to_flow.py>)

- 分类/语言：`supporting-code` / Python
- 规模：45 行，1,412 bytes
- SHA-256：`ea49112e9199cb91deed5f448ddd623e73a1c00967a9502aa6ba9012c845e064`
- 源码事实摘要：DSL -> FlowScript 转换桥接器（被 Electron IPC 调用）
- 顶层符号/结构：`main`
- 直接静态依赖：`sys`, `os`, `json`, `engine.dsl_flow_converter`
- API/IPC 入口：—

## `tsconfig.ai.json`

<!-- PROJECT-FILE: tsconfig.ai.json -->
### `tsconfig.ai.json`

[打开文件](<../../tsconfig.ai.json>)

- 分类/语言：`configuration` / JSON
- 规模：22 行，510 bytes
- SHA-256：`06b79f7f6de5f22dec0148b26c6b81269b3b0667994c4364c329fff95b976f41`
- 源码事实摘要：配置 tsconfig.ai 对应的构建、运行、依赖或测试行为。
- 顶层符号/结构：`key:compilerOptions`, `key:include`
- 直接静态依赖：—
- API/IPC 入口：—

## `tsconfig.cli.json`

<!-- PROJECT-FILE: tsconfig.cli.json -->
### `tsconfig.cli.json`

[打开文件](<../../tsconfig.cli.json>)

- 分类/语言：`configuration` / JSON
- 规模：22 行，486 bytes
- SHA-256：`5382d14b0b5122fb9ef55fbffc81cca777b8e193e4d9d433f0b4b7205ecedeae`
- 源码事实摘要：配置 tsconfig.cli 对应的构建、运行、依赖或测试行为。
- 顶层符号/结构：`key:compilerOptions`, `key:include`
- 直接静态依赖：—
- API/IPC 入口：—

## `tsconfig.json`

<!-- PROJECT-FILE: tsconfig.json -->
### `tsconfig.json`

[打开文件](<../../tsconfig.json>)

- 分类/语言：`configuration` / JSON
- 规模：21 行，565 bytes
- SHA-256：`af103dc6721f8c8cfac5c67dd4d42942ab55ead5c86942b5d576e764d922ffc7`
- 源码事实摘要：配置 tsconfig 对应的构建、运行、依赖或测试行为。
- 顶层符号/结构：`key:compilerOptions`, `key:include`
- 直接静态依赖：—
- API/IPC 入口：—

## `tsconfig.main.json`

<!-- PROJECT-FILE: tsconfig.main.json -->
### `tsconfig.main.json`

[打开文件](<../../tsconfig.main.json>)

- 分类/语言：`configuration` / JSON
- 规模：19 行，449 bytes
- SHA-256：`75b83678f66c8ff40fbcae27a0e70a88be0e63bef9476e52e6203f157bb88db4`
- 源码事实摘要：配置 tsconfig.main 对应的构建、运行、依赖或测试行为。
- 顶层符号/结构：`key:compilerOptions`, `key:include`
- 直接静态依赖：—
- API/IPC 入口：—

## `utils`

<!-- PROJECT-FILE: utils/__init__.py -->
### `utils/__init__.py`

[打开文件](<../../utils/__init__.py>)

- 分类/语言：`supporting-code` / Python
- 规模：2 行，64 bytes
- SHA-256：`931ce2c8822d8a371b9e99520620d5c59d8d70d9860f65d51b854f8b52ce8cfe`
- 源码事实摘要：Python 包 utils 的初始化与公开边界。
- 顶层符号/结构：—
- 直接静态依赖：`.bezier`, `.image`
- API/IPC 入口：—

<!-- PROJECT-FILE: utils/bezier.py -->
### `utils/bezier.py`

[打开文件](<../../utils/bezier.py>)

- 分类/语言：`supporting-code` / Python
- 规模：140 行，5,433 bytes
- SHA-256：`bec338f4cd00a1fdaac424f7e2523215443ca89674f16debca85a1300d09023e`
- 源码事实摘要：Bezier curve generation for human-like mouse movement.
- 顶层符号/结构：`BezierCurve`
- 直接静态依赖：`math`, `random`, `typing`, `numpy`
- API/IPC 入口：—

<!-- PROJECT-FILE: utils/image.py -->
### `utils/image.py`

[打开文件](<../../utils/image.py>)

- 分类/语言：`supporting-code` / Python
- 规模：178 行，7,000 bytes
- SHA-256：`caf2b96d9be3aa26ae9e1ac04332aebe30b58c666585e73188b1e11235c63250`
- 源码事实摘要：Common image processing utility functions.
- 顶层符号/结构：`ImageUtils`
- 直接静态依赖：`cv2`, `numpy`
- API/IPC 入口：—

## `vision`

<!-- PROJECT-FILE: vision/__init__.py -->
### `vision/__init__.py`

[打开文件](<../../vision/__init__.py>)

- 分类/语言：`supporting-code` / Python
- 规模：4 行，146 bytes
- SHA-256：`a6d6aa77e4da1903b77fcedfac3343bb0e5ff887c0b3f8711e58cdd0f01b303d`
- 源码事实摘要：Python 包 vision 的初始化与公开边界。
- 顶层符号/结构：—
- 直接静态依赖：`.color`, `.progress`, `.change`, `.scene`
- API/IPC 入口：—

<!-- PROJECT-FILE: vision/change.py -->
### `vision/change.py`

[打开文件](<../../vision/change.py>)

- 分类/语言：`supporting-code` / Python
- 规模：227 行，9,094 bytes
- SHA-256：`a321e148e7bc7f013405bb7bd0018010e49df19404522c93aaa382a025b1c287`
- 源码事实摘要：Region change detection — detect when screen regions change or stabilize.
- 顶层符号/结构：`ChangeDetector`
- 直接静态依赖：`time`, `collections`, `typing`, `cv2`, `numpy`, `core.screenshot`, `utils.image`
- API/IPC 入口：—

<!-- PROJECT-FILE: vision/color.py -->
### `vision/color.py`

[打开文件](<../../vision/color.py>)

- 分类/语言：`supporting-code` / Python
- 规模：141 行，5,287 bytes
- SHA-256：`772b455a25f9df7eb2d45aaf27979945d0c56854cda3994ee568f81caa92a5cf`
- 源码事实摘要：Color detection module — detect colors in screen regions using HSV distance.
- 顶层符号/结构：`ColorDetector`
- 直接静态依赖：`typing`, `cv2`, `numpy`, `core.screenshot`, `math`
- API/IPC 入口：—

<!-- PROJECT-FILE: vision/progress.py -->
### `vision/progress.py`

[打开文件](<../../vision/progress.py>)

- 分类/语言：`supporting-code` / Python
- 规模：175 行，6,665 bytes
- SHA-256：`017b38d59512bbb71328d8a39c14ce0bf15df48c41b98fd8dcbbf763487a4b6a`
- 源码事实摘要：Progress bar detection module — detect fill percentage of progress bars.
- 顶层符号/结构：`ProgressDetector`
- 直接静态依赖：`typing`, `cv2`, `numpy`, `core.screenshot`, `utils.image`
- API/IPC 入口：—

<!-- PROJECT-FILE: vision/scene.py -->
### `vision/scene.py`

[打开文件](<../../vision/scene.py>)

- 分类/语言：`supporting-code` / Python
- 规模：162 行，6,334 bytes
- SHA-256：`867816c48724ca33bca59c7002df331e6cf8a3679be822ec1a38a1507dec3c49`
- 源码事实摘要：Scene recognition — classify screen content using lightweight models.
- 顶层符号/结构：`SceneClassifier`
- 直接静态依赖：`typing`, `numpy`, `core.screenshot`, `utils.image`, `cv2`
- API/IPC 入口：—

## `vite.config.mts`

<!-- PROJECT-FILE: vite.config.mts -->
### `vite.config.mts`

[打开文件](<../../vite.config.mts>)

- 分类/语言：`supporting-code` / TypeScript module
- 规模：52 行，1,394 bytes
- SHA-256：`c0d6c776f321b740747aa0d67b7c9c4d006b24c090dc004065e2cf5c1c57ef98`
- 源码事实摘要：<reference types="vitest" />
- 顶层符号/结构：`projectRoot`, `removeCrossOrigin`
- 直接静态依赖：`node:url`, `node:path`, `vite`, `@vitejs/plugin-react`
- API/IPC 入口：—

## `桌面图标监控.flow`

<!-- PROJECT-FILE: 桌面图标监控.flow -->
### `桌面图标监控.flow`

[打开文件](<../../桌面图标监控.flow>)

- 分类/语言：`fixture-or-template` / FlowScript JSON
- 规模：6 行，218 bytes
- SHA-256：`2dd8341a973ecc0b25bf3c9908e0065de99c807667fa3922ad923daa3b9f82bf`
- 源码事实摘要：桌面图标监控 的流程、模板或测试 fixture 数据。
- 顶层符号/结构：—
- 直接静态依赖：—
- API/IPC 入口：—
- 解析说明：JSON parse failed at line 1: Expecting value

## `桌面图标监控.json`

<!-- PROJECT-FILE: 桌面图标监控.json -->
### `桌面图标监控.json`

[打开文件](<../../桌面图标监控.json>)

- 分类/语言：`configuration` / JSON
- 规模：131 行，3,018 bytes
- SHA-256：`1a60936dbef86c2b9b26f350d5598b4fc945402e38e8ef57cf94160d8effb7fd`
- 源码事实摘要：配置 桌面图标监控 对应的构建、运行、依赖或测试行为。
- 顶层符号/结构：`key:meta`, `key:nodes`, `key:edges`, `key:variables`, `key:settings`, `node:start×1`, `node:loop×1`, `node:image_click×3`
- 直接静态依赖：—
- API/IPC 入口：—
