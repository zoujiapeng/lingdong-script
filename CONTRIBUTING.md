# 贡献指南

感谢参与灵动脚本。这个项目会真实操控 Windows 桌面；贡献的首要目标是提高
通用 GUI 任务的真实成功率、速度和可验证性，而不是为某个应用或某条测试指令
增加特判。

## 开发环境

当前 CI 使用：

- Windows
- Node.js 20
- Python 3.11

推荐从干净环境安装依赖：

```powershell
npm ci
python -m pip install --upgrade pip
python -m pip install pytest -r requirements.txt
```

本地启动：

```powershell
npm run dev
```

不要直接运行 `uvicorn winpilot.server:app`。Electron 通过
`python/winpilot_home/serve.py` 管理 WinPilot 子进程、动态端口和进程级
bearer token。

## 修改原则

- 用户只面对一个 WinPilot Agent。新能力优先作为 Runtime、Capability、Skill
  或 Controller 能力接入同一 runner。
- 不为画猫、画狗、某个软件或某条评测指令增加生产专属规则。连续指针、输入、
  拖拽、窗口切换和验证都应保持通用。
- 真实画面优先。UIA、OCR、CV 和规则是提示，不能覆盖相反的截图事实。
- 没有新鲜、范围匹配的画面或确定性证据时必须 fail closed；不能让模型在
  看不清界面时自信继续。
- 物理鼠标和键盘保持全局 single-writer。只有已经证明不会抢焦点、不会移动
  鼠标的后台窗口动作才可并发。
- `done` 不是模型自述。需要用新鲜截图、文件、进程、窗口、trace 或其他与
  目标匹配的 evidence 核验。
- Task Contract 只描述阶段、依赖、工件和完成义务。到达未知界面后才能知道的
  信息留给视觉 Agent，不要在 compiler 中预写点击剧本。
- 新增模块、ledger、guard、adapter 或状态字段前，必须先说明现有唯一 owner
  为什么不能承载，并列出将删除或归并的旧路径。
- 新通用能力至少在两个不同任务族验证。11 个原始任务的文本、题号和 oracle
  只能存在于测试代码或报告中。
- 保留用户已有改动，避免把无关重构混入同一提交。

单轮 GUI runtime 改动应保持可审查：优先净删代码；若生产代码明显净增，PR 必须
列出新增行解决的通用失败模式、删除计划和第二任务族证据。禁止用一次上万行修改
同时引入多套 planner、executor 或状态账本。

### vNext 迁移切片

`codex/vnext-agent-core` 上的 Agent 核心旁路工作以 `docs/vnext/` 为合同。每个实现
切片只能引入一个主要 owner，并必须说明：Mission Reducer、Capability Broker、
Visual Actor、Controller、Verifier、Planner 或 Inspector 中谁拥有该职责；哪些
legacy 字段/路径将在何种证据下删除；为何问题不是基础工具或调度骨架缺陷。

Visual Actor 是唯一可提出 GUI mutation 的模型，Mission Reducer 是唯一可写
MissionState 的组件。Planner、Verifier 和 Inspector 必须静态限制为只读。不得因
某应用、测试题号或 prompt 关键词投影工具，也不得让 legacy/vNext 在同一 run 中
交叉操作桌面。完整阶段、A/B 和删除门槛见
[`docs/vnext/migration-plan.md`](docs/vnext/migration-plan.md)。

## 文档与配置

代码、配置和文档必须在同一变更中保持一致：

- 默认值变化时同步 `config.py`、`config.example.json`、设置界面和相关文档。
- API 或事件契约变化时同步 `docs/apis.md` 和测试。
- 原生前端入口、刷新、历史 run、节点可达性或兼容层变化时同步 `docs/frontend.md`；run/job 集合接口保持摘要，完整审计按 id 加载。
- 架构、模型能力、运行边界变化时同步 README、`CLAUDE.md` 和 `docs/`。
- GUI 感知、动作、上下文或完成口径变化时同步 `docs/architecture.md`、
  `docs/testing.md` 和 `docs/GUI_AGENT_ARCHITECTURE_AUDIT.md`。
- 面向用户的前端文字使用中文；API、MCP、VLM、URL、坐标名和协议标识可保留
  其技术原名。
- 不提交本机 `python/winpilot_home/config.json`、`.env`、API key、trace、截图、memory DB、
  eval 报告、缓存或构建产物。

## 验证

与 GitHub Actions 对齐的基础验证是：

```powershell
npm test
npm run typecheck
npm run build
python -m pytest python/winpilot_home/tests -q
python -m pytest tests -q
```

只改文档时至少运行 `git diff --check`，并检查所有相对链接和命令仍然有效。

GUI、Controller、感知、坐标、截图范围、上下文或 done gate 发生变化时，单元测试
通过还不够。必须从可见 Electron 客户端运行至少一个受影响的非破坏性任务；
`/api/chat` 和直接 `/api/v2` 调用只能补充集成证据。核对：

- 模型实际收到的截图范围和新鲜度；
- 每个工具调用是否与当时画面一致；
- 动作后的真实截图；
- trace 中的 request/response、tool result 和 done evidence；
- 失败时是否停止或换策略，而不是盲目重试。
- outer/child run 与 trace 是否准确绑定；
- 是否有独立 external oracle，而不是 runtime 自报成功。

画布任务只是连续指针压力测试之一；不要用单个靶场或一次粗糙成图代表通用成功率。
完整口径见 `docs/testing.md`。

## 提交流程

1. 先查看 `git status -sb`，明确本次范围。
2. 做小而可复核的修改，并补充相应测试。
3. 更新所有受影响文档和 `CHANGELOG.md`。
4. 运行与风险相称的验证，记录真实结果，不把模型自报 done 当成功。
5. 提交前运行 `git diff --check` 并复核没有密钥、运行数据或无关文件。

`master` 是唯一最终长期主分支。用户明确授权的 `codex/vnext-agent-core` 是大整改
期间的迁移集成分支，不是第二条产品主线；达到默认切换和 legacy 删除门槛后必须
合并回 `master` 并删除。其他临时开发分支应短期存在，合并后删除；提交信息使用
清晰的命令式摘要，例如 `fix: fail closed on stale visual frames`。
