# AI / 开发者接手速览

先读 `README.md`、`docs/architecture.md`、`docs/testing.md` 和 `docs/GUI_AGENT_ARCHITECTURE_AUDIT.md`。本文件只列最容易踩错的事实。

## 最终目标

让同一个 GUI Agent 从真实 Electron 客户端完成未知 Windows GUI 任务。11 个真实例子是黑盒探针，不是产品规格；不能为 QQ、画板、音乐、短视频、笔记、抓包或游戏逐项增加关键词、固定坐标、prompt 和 adapter 剧本。

## vNext 旁路（`codex/vnext-agent-core`）

该分支正在设计“单一 Visual Actor + 外置 Mission Ledger + 确定性 Capability Broker + 独立只读 Verifier + 按事件 Planner/Inspector”。先读 [`docs/vnext/README.md`](docs/vnext/README.md)。

当前 vNext 状态仅为文档，尚未实现代码。后续工作必须遵守：

- 只有 Visual Actor 可以提出 GUI mutation；
- 只有确定性 Mission Reducer 可以修改 MissionState；
- Planner、Verifier、Inspector 没有桌面写权限；
- Obligation 只描述结果、验收、依赖和禁止效果，不包含点击步骤；
- Broker 只根据 obligation capability、窗口、pointer lease、artifact 和 adapter 真实状态投影小工具面；
- 基础工具、Frame/Controller、应用发现和调度骨架未经证明前，不通过 prompt 或新增模型角色修单例；
- vNext 共用当前 Electron 入口和 E2E，不能形成第二套产品链；
- 每个迁移切片必须列出未来删除的旧职责，最终合并到 `master` 并删除迁移分支。

## 唯一产品链

```text
Electron「智能代理」
  -> /api/v2/task-contracts/compile
  -> /api/v2/arp/runs
  -> StageRuntime
  -> AgentRunner / FastGuiController / deterministic capability
  -> evidence / completion gate / trace
```

`/api/chat`、MCP、CLI、scheduler、FlowScript 和直接 API runner 是开发、集成或兼容入口，不得作为客户端 E2E 的替代品。

## 关键文件

| 文件/目录 | 作用 |
| --- | --- |
| `python/winpilot_home/winpilot/server.py` | FastAPI control plane、run/job、trace、MCP、replay、memory |
| `python/winpilot_home/serve.py` | 支持的 WinPilot 启动入口 |
| `python/winpilot_home/winpilot/tasking/` | Task Contract、stage、artifact、interaction |
| `python/winpilot_home/winpilot/orchestrator/` | ARP graph 和运行投影 |
| `python/winpilot_home/winpilot/agent/loop.py` | 统一 AgentRunner；当前过重，是收敛重点 |
| `python/winpilot_home/winpilot/agent/tools.py` | ToolSession 和 dispatch |
| `python/winpilot_home/winpilot/fast/` | Controller、subgoal、watcher、输入定位 |
| `python/winpilot_home/winpilot/action/` | 原子输入、Desktop Action Queue 和验证 |
| `python/winpilot_home/winpilot/perception/` | 权威截图、UIA/Win32/OCR/CV/VLM |
| `python/winpilot_home/winpilot/memory/` | Memory Graph、适用性、反馈和编译 |
| `python/winpilot_home/winpilot/recorder/trace.py` | trace、截图、IR 导出和 replay |
| `src/main/winpilotServer.ts` | Electron 托管 Python runtime |
| `src/renderer/components/platform/PlatformWorkbench.tsx` | 原生客户端工作台 |
| `scripts/client_e2e_run.py` | 真实任务客户端 E2E |
| `scripts/client_visual_e2e_run.py` | 受控视觉目标客户端 E2E |

## 不要做

- 不要直接运行 `uvicorn winpilot.server:app`；使用 Electron 托管或 `python/winpilot_home/serve.py`。
- 不要把 `python/features/` 当作 WinPilot 工具池；它属于 legacy Flow/DSL 体系。
- 不要用 UIA/OCR 的“有结果”代替视觉理解。结构化信号必须与当前 hwnd/frame/pixel 一致。
- 不要把 `drawing_gui`、DrawArtifact 或 Paint 当成连续指针能力的 owner。拖拽、按住、游戏式操控和笔画共用底层事务。
- 不要让 compiler 把未知界面提前翻译成固定点击步骤；到达界面后应由模型看图决定。
- 不要把工具 `ok`、像素变化、runtime `done` 或模型总结当完成证据。
- 不要用直接 API/integration 成功冒充 Electron 客户端成功。
- 不要新增重复 ledger、guard 或 action parser；先找到唯一 owner 并删除旧路径。
- 不要提交配置、trace、eval、output、缓存、构建产物或带本机绝对路径的交接文档。

## 感知与动作原则

- 当前权威帧是可见状态的最终依据。
- 原生视觉模型普通回合直接看当前 JPEG；UIA/OCR/CV 是按需辅助。
- 所有空间动作绑定窗口、capture region、frame id、epoch 和 0..1000 坐标。
- 每个模型视觉回合通常只执行一笔事务，然后重新观察。
- 跨帧 held-pointer 只允许继续移动/释放；超时、失焦和异常必须清理 `mouse_up`。
- 物理鼠标键盘通过 Desktop Action Queue 全局单写。
- 用户目标看不清或结果不确定时应重新观察或 `blocked`，不能猜。

## Context / Memory

- session context 用于同一场景内的“刚刚”“继续”等引用；它不证明当前 GUI 状态。
- cold start 禁用 Skill/Memory 等经验能力，但同一 multi-turn scenario 的显式 artifact dependency 仍需保留。
- Memory 只能加速；冷启动失败说明硬能力仍有缺口。
- typed applicability、脚本 hash 和真实 postflight evidence 都通过后，replay 才可成为 verified Skill。

## Legacy 边界

- `automation-ir/1` 是新 artifact 的规范中间层。
- `.wps.json`、FlowScript、DSL、旧 feature bridge 和浏览器 executor 只做兼容。
- 新 GUI 能力只在 WinPilot 主 runtime 实现一次，不向 legacy 系统复制。
- 兼容执行器不能模拟真实桌面成功。

## 测试

常规：

```bash
python -m pytest python/winpilot_home/tests -q
python -m pytest tests -q
npm test
npm run typecheck
npm run build
npm run build:all
```

客户端 E2E（先 `npm run dev`）：

```bash
npm run e2e:client:visual
npm run e2e:client:original
npm run e2e:client:mixed
```

通过定义、冷启动、context、external oracle 和干扰处理全部以 `docs/testing.md` 为准。

## 发布与分支

`master` 仍是唯一最终长期分支。`codex/vnext-agent-core` 是用户明确授权的迁移集成分支，可以在大整改期间持续存在，但不得形成第二套产品入口或永久双栈；达到切换门槛后合并回 `master`，删除 legacy 职责和该分支。其他临时分支完成审查、CI 和合并后删除。
