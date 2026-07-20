# 贡献指南

感谢参与灵动脚本。这个项目会真实操控 Windows 桌面；贡献的首要目标是提高通用 GUI 任务的真实成功率、速度和可验证性，而不是为某个应用或某条测试指令增加特判。

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

不要直接运行 `uvicorn winpilot.server:app`。Electron 通过 `python/winpilot_home/serve.py` 管理 WinPilot 子进程、动态端口和进程级 bearer token。

## 修改原则

- 用户只面对一个 WinPilot 产品入口。新能力优先作为 Runtime、Capability、Skill 或 Controller 能力接入主链。
- 不为画猫、画狗、某个软件或某条评测指令增加生产专属规则。连续指针、输入、拖拽、窗口切换和验证都应通用。
- 真实画面优先。UIA、OCR、CV 和规则是按需证据，不能覆盖相反截图事实。
- 没有新鲜、范围匹配的画面或确定性证据时 fail closed。
- 物理鼠标键盘保持全局 single-writer。只有已证明不抢焦点、不移动鼠标的后台动作才可并发。
- `done` 不是模型自述。需要新鲜截图、文件、进程、窗口、trace、receipt 或外部 oracle。
- Task Contract/Obligation 只描述结果、依赖、工件、broad capability 和完成义务；不预写未知 GUI 剧本。
- 新增模块、ledger、guard、adapter 或状态字段前，必须说明现有唯一 owner 为什么不能承载，并列出将删除/归并的旧路径。
- 新通用能力至少在两个不同任务族验证。11 个原始任务文本、题号和 oracle 只能存在于测试或报告。
- 保留用户已有改动，避免无关重构混入同一提交。

单轮 GUI runtime 改动应可审查：优先净删；若生产代码明显净增，提交必须列出通用失败模式、删除计划和第二任务族证据。禁止一次引入多套 planner、executor 或状态账本。

## vNext 迁移切片

`codex/vnext-agent-core` 上的旁路工作以 `docs/vnext/` 为合同。每个实现切片只能引入一个主要 owner，并说明：

- Goal Compiler、Task Profiler、Execution Policy、Mission Reducer、Capability Broker、Visual Actor、Controller、Verifier、Planner 或 Inspector 中谁拥有该职责；
- 哪些 legacy 字段/路径将在什么证据下删除；
- 为什么问题不是基础工具、身份绑定或调度骨架缺陷。

硬边界：

- Visual Actor 是唯一可提出 GUI mutation 的模型；
- Controller/RawDevice 是唯一桌面执行链；
- Mission Reducer 是唯一可接受 MissionEvent 并产生 MissionProjection 的组件；
- MissionProjection 必须由 MissionDefinition、accepted events 和 immutable evidence 重放，不建立第二份完成账本；
- Task Profiler 只提出多维画像、执行深度和服务建议，Execution Policy 才能接受；
- Profiler 不生成 GUI 步骤、工具、坐标、应用专属架构或 runtime version；
- ObligationSpec 不含 runtime status；lifecycle只有一个权威 map；
- 模型 proposal 不携带 mission/revision/obligation/frame/evidence ids；Runtime envelope绑定身份；
- Planner、Profiler、Verifier、Inspector 静态限制为只读；
- Capability Broker 不因应用、题号、窗口标题或prompt关键词投影工作流；
- Actor可请求broad capability，但Runtime/Broker才有授权权；
- legacy/vNext不在同一run交叉操作桌面。

完整阶段、A/B 和删除门槛见 [`docs/vnext/migration-plan.md`](docs/vnext/migration-plan.md)。

## 文档与配置

代码、配置和文档必须保持一致：

- 默认值变化时同步 `config.py`、`config.example.json`、设置界面和文档。
- API或事件契约变化时同步 `docs/apis.md` 和测试。
- 原生前端入口、历史run、节点可达性或兼容层变化时同步 `docs/frontend.md`。
- 架构、模型能力、运行边界变化时同步 README、`AGENTS.md`、`CLAUDE.md` 和 `docs/`。
- GUI感知、动作、上下文或完成口径变化时同步 `docs/architecture.md`、`docs/testing.md` 和 `docs/GUI_AGENT_ARCHITECTURE_AUDIT.md`。
- vNext profile、typed contract、迁移或门槛变化时同步 `docs/vnext/`。
- 面向用户的前端文字使用中文；协议标识和技术名可保留英文。
- 不提交 `python/winpilot_home/config.json`、`.env`、API key、trace、截图、memory DB、eval报告、缓存或构建产物。

## 验证

基础验证：

```powershell
npm test
npm run typecheck
npm run build
python -m pytest python/winpilot_home/tests -q
python -m pytest tests -q
```

只改文档时至少运行 `git diff --check`，并检查相对链接和命令。

GUI、Controller、感知、坐标、截图范围、上下文、Profiler/Broker 或 done gate 变化时，单元测试不够。必须从可见 Electron 客户端运行至少一个受影响的非破坏性任务；`/api/chat` 和直接 `/api/v2` 只能补充集成证据。核对：

- 模型实际收到的截图范围和新鲜度；
- TaskProfileProposal、ExecutionProfile 和policy override；
- 每回合Broker实际工具；
-模型proposal和Runtime envelope；
-动作后的真实截图；
- trace中的request/response、tool result和evidence；
-失败时是否停止/换策略而不是盲重试；
-outer/child run与trace绑定；
-独立external oracle。

画布任务只是连续指针压力之一；不要用一个靶场或一次成图代表通用成功率。

## 提交流程

1. 查看 `git status -sb`，明确范围。
2. 做小而可复核的修改并补测试。
3. 更新受影响文档和 `CHANGELOG.md`。
4. 运行与风险相称的验证，记录真实结果。
5. 提交前运行 `git diff --check`，复核无密钥、运行数据和无关文件。

`master` 是唯一长期 canonical 产品分支。工作、迁移和实验分支在活跃期间应推送到 GitHub 以便协作与恢复；合并或放弃后由维护者归档/删除。`codex/vnext-agent-core` 可以在迁移期持续存在，但不能永久形成第二条产品事实线。耐久备份使用 tag。提交信息使用清晰命令式摘要，例如 `fix: fail closed on stale visual frames`。
