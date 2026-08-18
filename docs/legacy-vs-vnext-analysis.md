# 新旧架构关系分析报告（Legacy vs vNext）

> 分支：`analysis/new-vs-old-architecture`（从 `master` 创建，2026-08-17）
> 方法：对 `python/winpilot_home/winpilot`、`python/` 旧引擎目录、`src/`、`scripts/`、
> 两层测试做 AST 级 import 正反向扫描，与 `test_vnext_mechanical_core_contract.py`、
> `docs/vnext-migration.md`、`docs/architecture.md` 交叉核对。

## 0. 执行状态（本分支已实施，方案 B）

两批已实际执行并通过全量回归（WinPilot `2377 passed, 13 skipped`、根 `tests/`
`2508 passed`、renderer `325 passed`、TS typecheck 通过）：

1. 零消费者死面：`src/ai-interface/`、`python/lobster_mcp/`、npm `ai*`/`build:ai`、
   `tsconfig.ai.json`、`webui/`、`/api/chat`、`/api/stop`、`/mcp` ChatGPT MCP、
   `integrations/chatgpt_mcp.py`、`winpilot/eval/`、`scripts/eval_summary.py`。
2. 服务端定时任务整功能（Renderer“代理计划”面板 + `/api/agent-schedules` +
   `winpilot/scheduler.py` + 调度线程）。
3. Studio `agent_goal` 节点：节点库摘除、历史文档编译为 `legacy_flow` fail closed、
   预设模板与属性编辑器删除。
4. 任务契约编译链：`/api/v2/task-contracts/compile`、`gui_eval_run.py`、npm `eval:*`、
   `tasking/` 缩到 `artifacts/execution_context/evidence` 三模块、
   `orchestrator/graph_compiler.py`、adapters TASK_STAGE handler、contracts 的
   TaskStage pin（改内联 fail-closed）。
5. AgentRunner 半边：`agent/` 35→21 文件（删 loop/router/session/done_verifier/
   failure_classifier/failure_recovery/scene_coverage/run_state/reflection/playbooks/
   prompts/context_guidance/graphical_affordances/gui_plan_helpers/coding）；
   `/api/session` 路由与 Renderer 会话记忆视图；`playbooks/*.json`。
6. 关联测试：删除 47 个旧包测试文件，修正 5 个共享件测试。

**执行中发现的修正**：
- `orchestrator/adapters.py` 不能整体删除——它的 ACTION_BATCH/TASK_STAGE/REPLAY_STEP/
  SKILL_REPLAY 是 Studio 图执行 handler 注册表；本批只删了 AGENT_GOAL/TASK_STAGE。
- Studio ACTION_BATCH 实际跑在 `ToolSession → FastGuiController → session.dispatch`
  上（fast/controller.py:1816），`agent/` 剩余 21 文件与 `fast/` 因此保留为 Studio
  确定性执行器；只有先做 ToolSession→fast.controller 直连迁移（方案 A 工作量）才能删完。
- `tasking/visual_effects.verify_pointer_paths_in_frames` 被 port 进
  `agent/interaction_tools.py`；`_main_model_has_vision` 内联进 `perception_tools`。

## 1. 结论摘要

1. **新架构已经与旧 `winpilot/agent` 完全解耦**：产品组合根 `orchestrator/live_agent.py`
   的静态闭包不含 `winpilot.agent`、`fast`、`tasking`、`recorder`；vNext 只依赖
   19 个共享机械模块（`action/{desktop_queue,executor,raw_device,replay_policy,safety,shell}`、
   `utils/{dpi,image_capture,log,screenshot}`、`visual_runtime/*`、`perception/{apps,model,uia}`）
   与 `vnext/`、`modeling/`、`integrations/connector_gateway`。
2. **旧架构不是一块，而是三个互相独立的旧集群**：
   - **旧 Agent 线**（`winpilot/agent` + `fast` + `tasking` + `memory` + `eval` +
     `recorder` 部分 + `integrations/chatgpt_mcp`）：约 94 个 py 文件、2.8 MB；
   - **旧 F9/DSL 引擎线**（`python/engine` + `features` + `tools` + `agent`(dialog/menu)
     + `core` + `interaction` + `perception` + `providers` + `rpa_keywords` +
     `lobster_mcp` + Electron main 六个旧桥 + renderer 兼容执行器）：约 100+ 文件；
   - **零消费者死面**（`src/ai-interface` 等）：可立即删除。
3. **三个"名字是旧包、实际是共享基础设施"的模块不能随旧包删除**：
   `recorder/trace.py`（vNext run 的 trace 写入器）、`tasking/artifacts.py`
   （vNext 截图工件存储）、`model_adapter.py` + `modeling/`（新旧共享的模型 transport）。
4. 删除的最大障碍不是代码依赖而是**消费者面**：server 57 条路由中约 45 条仍属
   兼容/Studio/离线面；140 个 WinPilot 测试文件里 79 个 import 旧包；根 `tests/`
   16 个文件全为旧引擎回归。

## 2. 新架构（当前产品主链）

```text
Electron 智能代理页 (src/renderer/platform/)
  -> POST /api/v2/goals/runs                     [server.py:1459]
  -> GoalAdmission (orchestrator/goal_admission.py)
  -> LiveVisualActor (orchestrator/live_agent.py, 155 KB)
  -> vnext/ (30 文件: actor/capabilities/desktop_adapter/evidence/mission/
             model_roles/profiling/runtime)
  -> 共享机械核心: action/{desktop_queue,executor,raw_device,...}
                  visual_runtime/*  utils/*  perception/{apps,uia,model}
  -> modeling/* (provider wire) + integrations/connector_gateway (MCP)
  -> AgentThreadStore (orchestrator/agent_threads.py) 有界 Thread checkpoint
```

| 包 | 角色 | 状态 |
| --- | --- | --- |
| `winpilot/vnext/` (30 文件, 640 KB) | live 内核：Actor/Broker/MissionRuntime/AtomicAdapter | 新，继续演进 |
| `winpilot/orchestrator/live_agent.py` | 产品组合根 | 新 |
| `orchestrator/{goal_admission,agent_threads,runtime,projection}` | Goal 入口/Thread/ARP 生命周期/投影 | 新（`runtime.py` 的 ArpExecution 与 Studio 共用） |
| `orchestrator/{contracts,graph_compiler}` | Studio 图校验 | 新，但 pin 了 3 个旧模块（见 §4.2） |
| `orchestrator/adapters.py` | `/api/chat`、legacy ARP 的旧 Runner 桥 | 旧桥，待删 |
| `modeling/` (6 文件) | provider 协议 adapter | 共享（旧线也在用） |
| `integrations/{connector_gateway,mcp_client}` | 显式只读 MCP | 新 |
| 机械核心 19 模块 | 物理输入/帧/启动 | 共享，由 `test_vnext_mechanical_core_contract.py` 钉住 |
| `runtime_jobs.py` / `runtime_info.py` | Job 管理器 / 运行时信息 | 新/共享 |

## 3. 旧架构全貌（按集群）

### 3.1 旧 Agent 执行线（`python/winpilot_home/winpilot/` 内）

| 包 | 规模 | 内容 | 当前消费者 |
| --- | --- | --- | --- |
| `agent/` | 35 文件, 1.1 MB | AgentRunner 生态：`loop.py`(340 KB)、`done_verifier.py`(110 KB)、`tool_schemas.py`(88 KB)、`interaction_tools.py`、`desktop_tools.py`、`perception_tools.py`、`planner.py`、`plan_tools.py`、`router.py`、`session.py`、`scope_guard.py`、`scene_coverage.py`、`failure_classifier/recovery`、`task_rules.py`、`playbooks.py`、`prompts.py`、coding 工具、`mcp_tools.py` 等 | server `/api/chat`(lazy)、`/api/v2/arp/runs` LEGACY 分支(lazy)、`orchestrator/adapters.py`、`integrations/chatgpt_mcp.py`、`eval/runner.py` |
| `tasking/` | 22 文件, 858 KB | Task Contract 链：`interaction.py`(164 KB)、`interaction_stage_runtime.py`(181 KB)、`stage_runtime.py`(58 KB)、`intent.py`(65 KB)、visual_* 五个文件(175 KB)、`compiler.py`、`capability.py`、`artifacts.py`、`completion.py`、`execution_context.py` | server(`compiler/capability/artifacts/contracts/execution_context` 顶层 import)、`orchestrator/contracts.py`+`graph_compiler.py`(Studio 校验)、`orchestrator/adapters.py`、`integrations/chatgpt_mcp.py` |
| `fast/` | 13 文件, 463 KB | FastGuiController：`controller.py`(169 KB)、`subgoal.py`(150 KB)、`watcher.py`、`visual_servo.py`、`perception_daemon.py`、`element_cache.py`、`input_locator.py`、`action_contract.py` | `agent/`(fast_tools/loop/tool_schemas/run_state/scope_guard)、`orchestrator/contracts.py`(action_contract Studio pin)、`integrations/chatgpt_mcp.py` |
| `memory/` | 13 文件, 120 KB | Memory Graph：store/compiler/retriever/feedback/migration | server `/api/memory*`(9 条路由)、`orchestrator/adapters.py`(finalize_memory_feedback)、**`orchestrator/live_agent.py:3500`(只读 store_present 状态查询)**、`recorder/*`、`agent/*`、`tasking/stage_runtime.py` |
| `recorder/` | 6 文件, 204 KB | trace/human/apprentice/replay_control/wps_validate | **`trace.py` 是 vNext run 的 trace 写入器（server.py:1187-1225 的 vNext 分支调用 `RECORDER.start/owns/stop`）**；`wps_validate.py` 被 `orchestrator/contracts.py` pin；human/apprentice 服务离线示教/回放 |
| `eval/` | 5 文件, 48 KB | 旧 Agent 级评测 runner/summary | server `/api/trace/{run_id}/summary`、`eval/runner.py` import `agent/loop.py` |
| `integrations/chatgpt_mcp.py` | 100 KB | 旧 ToolSession（ChatGPT Desktop 外部会话） | server `_install_chatgpt_mcp`(lazy) |
| `action/verify.py` | — | 旧动作验证器 | `agent/{desktop_tools,planner}`、`fast/subgoal` |
| `utils/trace_payload.py` | — | 旧 trace 载荷 | `agent/loop.py`、`recorder/trace.py` |
| `perception/` 旧子集 | fusion/ocr/ocr_lines/reducer/surface_binding/vision_cv/vlm/win32/world/incremental/intent/groundtruth/web_search | 旧感知栈 | 仅旧线（产品线闭包对其引用已按合同钉为 0） |
| `scheduler.py` | — | agent-schedules 持久化 | server `/api/agent-schedules`(3 条路由) |
| `webui/` | 3 文件, 49 KB | 旧 Web UI | server `GET /`(仍在服务) |

### 3.2 旧 F9/DSL 引擎线（`python/` 包外 + Electron main）

| 位置 | 规模 | 内容 | 当前消费者 |
| --- | --- | --- | --- |
| `python/engine/` | 22 文件 | F9 引擎：`engine.py`(IPC)、`dsl_parser.py`、`executor.py`、`planner.py`、`healing.py`、`optimizer.py`、`robot_converter/runner`、`triggers.py`、`scheduler_daemon.py`、`nlp_schedule_parser.py`、`secure_store.py`、`store.py`、`crystallizer.py`、`dsl_flow_converter.py`、`unified_entry.py` | `src/main/engine.ts`(`engine:start` IPC → `startPythonEngine`)、`schedulerBridge.ts`(scheduler_daemon)、`tools/run_dsl.py` |
| `python/features/` | 49 文件 | `@feature` 注册表：browser/camera/dialog/file/input/window/fusion/perception/vlm/... | `tools/feature_list.py`+`feature_call.py` ← `src/main/{featureCallBridge,mcp-handler}.ts` 与 7880 `/api/features/*` |
| `python/tools/` | 11 文件 | `run_dsl.py`、`feature_list/call.py`、`macro_recorder.py`、`macro_play_cli.py`、`ahk_manager.py`、`template_market.py` | Electron main 多处 spawn |
| `python/agent/` | 2 文件 | dialog_handler / menu_handler | `engine/executor.py`、`features/{dialog,filedialog,menu}_features.py`、`rpa_keywords` |
| `python/{core,interaction,perception,utils,providers,rpa_keywords,lobster_mcp,macros}` | 约 20+ 文件 | 旧引擎支撑、轮选器、旧 provider、MCP wrapper、宏数据 | 同上旧线 |
| `src/main/{engine,api,featureCallBridge,mcp-handler,schedulerBridge,macroPaths}.ts` | 6 文件 | 7880 本地 API、F9 桥、feature 桥、宏桥、调度桥 | 旧消费者与外部集成 |
| `src/main/main.ts` 旧 IPC | ~10 handler | `engine:*`、`dsl:toFlow`、`features:list`、`feature:call`、`macro:*`、`screenshot:capture`、`ocr:recognizeRegion`、`interaction:wheelSelect`、`.claude.json` 自动注册(main.ts:1529) | renderer 旧参数控件 |
| renderer 兼容面 | — | `store/index.ts` 的 `startExecution`(F9 兼容执行器，仅 "历史流程")、`fetchFeatures/callFeatureDirect`、`dslToFlow`、PropertyPanel 的 `ClickTextParams`(OCR)、`ImageClickParams`(截图)、`WheelSelectControl`(轮选) | Studio 旧参数控件 |

### 3.3 零消费者死面（可立即删除）

| 位置 | 规模 | 证据 |
| --- | --- | --- |
| `src/ai-interface/` | 11 文件 | 全仓零 import；只有 `package.json` 的 `ai`/`ai:mcp`/`ai:http` scripts 与 `tsconfig.ai.json`、`build:ai` 引用；CLAUDE.md 已明令"不得接回"（第二模型 + DSL 生成 + 无确认突变） |
| `python/winpilot_home/winpilot/agent/mcp_tools.py` | 1.3 KB | 绕过 Gateway 的 Legacy 重复 owner（architecture.md 债务 #9） |
| `dist/`、`output/`、`release/`、`python/browser_snapshots/` | 构建/运行产物 | 数据目录 |

## 4. 新旧之间的关键边（删除时必须先处理的点）

### 4.1 产品线到旧包仅剩的 3 条边

1. `orchestrator/live_agent.py:3500` → `memory.experience.memory_store_present()`
   （`get_memory_status` 状态工具；只读、try/except 包着）。**可一行改掉**（读配置 + store
   路径存在性），删掉后产品线闭包对 `memory` 的边归零。
2. `orchestrator/contracts.py` → `fast.action_contract`、`recorder.wps_validate`、
   `tasking.contracts`（Studio 图校验 pin，见 vnext-migration.md "Phase D 清单"）。
   这是 Studio 兼容面，不是产品主链；随 Studio 退役链第 4/5 步删除。
3. server 顶层 import `tasking.{capability,artifacts,compiler,contracts}` +
   `recorder.{trace,human,apprentice}` + `eval.summary` + `scheduler`：
   - `recorder.trace` 与 `tasking.artifacts` 是 vNext 也在用的共享件（见下）；
   - 其余是旧路由的服务者。

### 4.2 "名字旧、实际共享"的三个模块（不可随旧包删除）

| 模块 | 共享用途 | 证据 |
| --- | --- | --- |
| `recorder/trace.py` | **vNext run 的 durable trace 写入器** | server.py:1187-1225 vNext 分支调用 `RECORDER.start/owns/stop`；trace 导出(`/api/export/*`、`scripts/export_run_log.py`)也读它 |
| `tasking/artifacts.py` | **vNext 截图/工件存储** | `/api/v2/runs/{id}/artifacts/{aid}`(server.py:979) 为 vNext run 提供 hash 校验工件 |
| `model_adapter.py` + `modeling/` | 新旧共享模型 transport/wire | `live_agent.py:43` 与 `agent/loop`、`tasking/{conversation,message_content}`、`perception/vlm` 同时 import |

删除建议：为这三个模块先做"共享部分提取/改名归属"（如 Phase D 对 `wps_validate.py`
的做法），再删其旧专用部分。

## 5. 删除建议

### 5.1 可立即删除（零消费者/已被判死刑）

1. `src/ai-interface/` 全目录 + `tsconfig.ai.json` + `build:ai` + `package.json`
   `ai`/`ai:mcp`/`ai:http` scripts。
2. `python/lobster_mcp/`（MCP wrapper spike，全仓零 import）。
3. `winpilot/agent/mcp_tools.py`（Gateway 的 Legacy 重复 owner；被 `agent/tools.py`
   import，随 `agent/` 整包删除，无需单独处理）。
4. `winpilot_home` 下未跟踪的调试截图/ps1（非代码，本次工作树已有，可顺手清理或忽略）。

### 5.2 消费者明确后可整簇删除（按依赖顺序）

> 注：顺序 1 已部分执行（见 §0）；`winpilot/agent` 整包删除的前置条件比初版多出
> scheduler 与 Studio StageRuntime/ToolSession 两条消费者，必须按 §7 修订顺序执行。

| 顺序 | 删除对象 | 前置条件（先删消费者） |
| --- | --- | --- |
| 1 | `winpilot/agent/` 整包 + `action/verify.py` + `utils/trace_payload.py` | ①已删：`/api/chat`、legacy ARP goal 分支、chatgpt_mcp、eval、adapters `_agent_handler`；②待办：scheduler `_try_start_scheduled` 的 agent 执行分支迁移/删除；③待办：Studio `tasking/{stage_runtime,interaction_stage_runtime}.py` 与 `adapters._batch_handler` 不再依赖 `agent.{router,tools,runtime_profile}`；④同步删除相关测试文件 |
| 2 | `winpilot/tasking/` 主体 | ①`artifacts.py` 工件存储部分先提取到共享位置；②`orchestrator/contracts.py` 的 `tasking.contracts` pin 迁移；③删除 `/api/v2/task-contracts/compile` 与其前端消费者；④删除 StageRuntime/interaction_stage_runtime 的剩余消费者 |
| 3 | `winpilot/fast/` | ①`action_contract.py` Studio pin 迁移；②随 agent 删除自动清场 |
| 4 | `winpilot/memory/` | ①`live_agent._status_provider` 改为不 import memory；②删除 `/api/memory*` 9 条路由；③删除 `finalize_memory_feedback` 调用 |
| 5 | `winpilot/scheduler.py` + `/api/agent-schedules` + `src/main/schedulerBridge.ts` + `python/engine/scheduler_daemon.py` | 确认 scheduler 无真实用户（当前是"仍保留的兼容功能"） |
| 6 | 旧 F9 线：`python/engine` + `features` + `tools` + `agent(dialog/menu)` + `core` + `rpa_keywords` + `providers` + `lobster_mcp` + `interaction` + 旧 `perception`/`utils` | ①删除 `src/main/engine.ts` 的 F9 启动、`featureCallBridge.ts`、`mcp-handler.ts`、`macroPaths.ts`、`api.ts` 7880 旧路由；②删除 renderer `store/index.ts` 的 `startExecution`(F9) 与 `fetchFeatures/callFeatureDirect`；③迁移/删除 `ClickTextParams`(OCR)、`ImageClickParams`(截图)、`WheelSelectControl`(轮选) 三个参数控件；④删除 preload 旧 API 与 main.ts 旧 IPC handler；⑤删除 `main.ts:1529` 的 `.claude.json` 自动写入；⑥删除根 `tests/` 16 个旧引擎回归 |
| 7 | `webui/` 3 文件 | 确认 `/` 无外部消费者后删 |

### 5.3 建议保留（不是旧架构，或仍是共享件）

- `recorder/trace.py` + `wps_validate.py` + `replay_control.py`（trace 写入 + Studio 校验）
- `tasking/artifacts.py` 的工件存储（vNext 截图工件）— 先提取归属
- `model_adapter.py`、`modeling/`（共享 transport；`modeling/` 已是新 owner 名单）
- `integrations/connector_gateway.py` + `mcp_client.py`（新 MCP 面）
- `winpilotServer.ts`（serve.py 托管，产品基础设施）
- Electron 桌面交接 IPC（`window:prepare-desktop-handoff`、`bind-desktop-handoff` 等）
- `recorder/human.py` + `apprentice.py` + `/api/replay*`：离线示教/回放是**用户明确保留的能力**
  （CLAUDE.md "Replay/离线流程现状" 整节），删除前必须有用户确认
- `scripts/gui_eval_run.py` 的 `--entrypoint chat` legacy 模式：删除 `/api/chat` 前先删该模式
- Studio 的 `/api/v2/arp/*`、`/api/v2/automation-ir/*`、`/api/script*`、`/api/export*`：
  Studio/自动化编辑是活跃产品面，不可随旧 Agent 删除

## 6. 影响面清单（删除时会被波及的东西）

1. **测试**：`python/winpilot_home/tests` 140 个文件中 79 个 import 旧包（agent/fast/tasking/
   memory 等），随删随清；根 `tests/` 16 个文件全部绑定旧引擎/feature。
2. **server 路由**：57 条路由中约 45 条属兼容面——`/api/chat`、`/api/stop`、
   `/api/v2/task-contracts/compile`、`/api/v2/arp/*`、`/api/automation-ir/*`、
   `/api/trace*`、`/api/export*`、`/api/replay*`、`/api/memory*`、`/api/session`、
   `/api/agent-schedules`、`/api/script*`、`/api/v2/flow-fragments/proposals`(Studio 助手，
   新线，保留)。
3. **Electron main**：6 个旧桥文件 + main.ts 约 10 个旧 IPC + `.claude.json` 自动写入。
4. **Renderer**：`store/index.ts` 兼容执行器与 feature 调用、3 个 PropertyPanel 参数控件、
   preload 旧 API 面。
5. **Scripts**：`gui_eval_run.py`(chat 模式)、`export_run_log.py`(trace)、部分 `client_*_e2e`
   harness、`eval_summary.py`。
6. **npm scripts**：`eval:*`、`build:cli`、`build:ai`、`ai*`、`cli`。
7. **文档**：`docs/architecture.md` 债务节、`docs/vnext-migration.md` 退役链、
   `docs/project-guide/` 三卷逐文件索引（删除后必须重跑
   `python scripts/generate_project_guide.py`）。
8. **数据兼容**：历史 `legacy_flow` JSON、旧 trace、memory recipe、宏与 `.wps.json`
   在删除前必须确认只读迁移器或明确弃档（vnext-migration.md 最终退出门第 4 条）。

## 7. 建议执行顺序（消费者先行，一次性小步）

1. ✅ 已执行：删 `src/ai-interface/`、`python/lobster_mcp/` 与零消费者死面（第 5.1 节）。
2. ✅ 已执行：删 `/api/chat` + webui + `/api/stop` + chatgpt_mcp + eval + legacy ARP goal
   分支 + adapters `_agent_handler`，并斩断 `live_agent` → `memory` 边（见 §0）。
3. 下一批（未执行）：迁移 scheduler 的 agent 执行分支与 Studio TASK_STAGE/ACTION_BATCH 的
   `build_runner`/`ToolSession` 依赖，然后才能删 `winpilot/agent/` 整包与配套测试。
4. 提取 `tasking/artifacts.py` 共享部分，然后按 Studio 退役链删 `tasking/`、`fast/`、
   `memory/`。
5. 最后动 F9/DSL 引擎线（涉及 Electron main 与 renderer 参数控件，前端改动最大，
   放最后，且每步需重跑 typecheck + renderer 测试）。
6. 每批删除后跑：`python -m pytest python/winpilot_home/tests -q`、
   `npm run typecheck`、`npm test`、重跑 `generate_project_guide.py --check`。

## 8. 不建议现在做的

- 一次性整体删除 `python/winpilot_home/winpilot/agent/`：scheduler
  （`_try_start_scheduled`）与 Studio StageRuntime/`ToolSession` 会立刻 ImportError。
- 删除 `orchestrator/adapters.py` 整文件：它的 ACTION_BATCH/TASK_STAGE/REPLAY_STEP/
  SKILL_REPLAY handler 仍是 Studio 图执行的 handler 注册表（只删了 `_agent_handler`）。
- 删除 `recorder/`、`tasking/artifacts.py`、`modeling/`：会打断 vNext 的 trace、工件与模型调用。
- 删除 `/api/replay*` 与 human/apprentice 录制：离线示教/回放是用户明确要保留的能力。
- 删除 `/api/v2/arp/*` 与 `automation-ir`：Studio 仍依赖。
- 删除 `/api/session`：Renderer 会话记忆面板仍在消费。
- 合并两个 pytest 层或删根 `tests/`：旧引擎删除前它们仍是唯一回归面。
