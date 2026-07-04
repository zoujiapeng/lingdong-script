# 前端重设计资产包

本目录用于承接“灵动脚本”前端独立重设计。目标是让 Codex 后续直接读取 GitHub 资料，并结合 Figma 继续实现高端、人性化、动感的新版前端。

## 文件结构

- `lingdong_redesign_spec.md`：产品定位、信息架构、交互规则、组件拆分建议。
- `CODEX_IMPLEMENTATION_PROMPT.md`：交给 Codex 的实现提示词。
- `lingdong_redesign_prototype.html`：本地 HTML 原型。
- `lingdong_redesign_workflow.mmd`：Mermaid 用户流程图。
- `*.svg`：可直接拖入 Figma 的四张设计画布。
- `*.png`：四张设计预览图。
- `lingdong_frontend_redesign_pack.zip`：完整离线设计包。

## 建议工作流

1. Codex 先阅读 `lingdong_redesign_spec.md` 和 `CODEX_IMPLEMENTATION_PROMPT.md`。
2. Figma 中导入 4 个 SVG，拆成设计参考或重绘成正式组件。
3. 前端先做壳层重构：`AppShell`、`CommandDock`、左侧模式栏、页面容器。
4. 再重构三大页：`AgentCockpit`、`FlowStudioLayout`、`ReplayLab`。
5. 最后统一设计 tokens、动效、空状态、错误提示、证据面板和流程图自动布局。

## 不要做的事

- 不要把 WinPilot Agent、FlowScript、WPS Replay 三套语义混成一个内部格式。
- 不要让所有节点默认摊开给用户。
- 不要把错误提示只写成异常栈。
- 不要让不可等价转换的 WPS 步骤假装变成普通 FlowScript 节点。
