# 接手入口

本仓库有两个互不相关的项目。**按你要做的事选一行读，不要都读。**

| 你要做的 | 读这个 | 不要读 |
| --- | --- | --- |
| DSH 插件（`packages/dsh-plugin-lingdong`、`dsh-client-ui-lingdong`） | `packages/dsh-plugin-lingdong/AGENTS.md` | 下面的主产品文档 |
| 主产品「灵动脚本 / WinPilot」桌面 Agent | `AGENTS.md`（根，原则）→ `docs/handover/main-product-history.md`（决策与历史） | — |

## 为什么拆开

主产品那份交接文档原有 1235 行 / 105 KB，超过工作区指令预算（65536 B）本身，每次会话都会被截断，
并且把预算吃光。它已**原样**移到 `docs/handover/main-product-history.md`（内容一字未删，Git 历史可追）。
本文件只做路由。

主产品的权威文档依次是：

1. `AGENTS.md`（根）— 最高原则与不可协商规则
2. `docs/handover/main-product-history.md` — 决策、实验结论、精确 run SHA 与回归数字
3. `docs/project-guide/README.md` — 全项目架构与逐文件索引
4. `docs/architecture.md` · `docs/testing.md` · `docs/vnext-migration.md`
5. `docs/GUI_AGENT_ARCHITECTURE_RESET_2026-07-29.md`

主产品唯一长期分支是 `master`；接手时必须现场核对 `git rev-parse HEAD`、`origin/master`
与该精确 SHA 的 Actions。DSH 插件子项目在分支 `feat/dsh-plugin`，改动只允许落在 `packages/` 下。