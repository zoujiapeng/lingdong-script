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
- 保留用户已有改动，避免把无关重构混入同一提交。

## 文档与配置

代码、配置和文档必须在同一变更中保持一致：

- 默认值变化时同步 `config.py`、`config.example.json`、设置界面和相关文档。
- API 或事件契约变化时同步 `docs/apis.md` 和测试。
- 原生前端入口、刷新、历史 run、节点可达性或兼容层变化时同步 `docs/frontend.md`；run/job 集合接口保持摘要，完整审计按 id 加载。
- 架构、模型能力、运行边界变化时同步 README、`CLAUDE.md` 和 `docs/`。
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

GUI、Controller、感知、坐标、截图范围或 done gate 发生变化时，单元测试通过
还不够。还应通过真实前端或 `/api/chat` 跑至少一个受影响的非破坏性任务，
人工核对：

- 模型实际收到的截图范围和新鲜度；
- 每个工具调用是否与当时画面一致；
- 动作后的真实截图；
- trace 中的 request/response、tool result 和 done evidence；
- 失败时是否停止或换策略，而不是盲目重试。

画布任务只是连续指针压力测试之一；不要用单个靶场或一次粗糙成图代表通用成功率。

## 提交流程

1. 先查看 `git status -sb`，明确本次范围。
2. 做小而可复核的修改，并补充相应测试。
3. 更新所有受影响文档和 `CHANGELOG.md`。
4. 运行与风险相称的验证，记录真实结果，不把模型自报 done 当成功。
5. 提交前运行 `git diff --check` 并复核没有密钥、运行数据或无关文件。

`master` 是唯一长期主分支。临时开发分支应短期存在，合并后删除；提交信息使用
清晰的命令式摘要，例如 `fix: fail closed on stale visual frames`。
