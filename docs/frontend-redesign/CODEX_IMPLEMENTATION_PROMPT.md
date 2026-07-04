# Codex 实现提示词：灵动脚本前端重设计

请基于当前仓库真实代码实现一版前端重设计，不要只做静态 demo。先阅读 `README.md`、`docs/flow-editor.md`、`docs/replay-recording.md`、`src/renderer/App.tsx`、`src/renderer/components/AgentView.tsx`、`src/renderer/components/Canvas/FlowCanvas.tsx`、`src/renderer/components/NodeLibrary/NodeLibrary.tsx`、`src/renderer/components/ReplayEditorView.tsx`、`src/renderer/components/SettingsPanel.tsx`。再阅读本目录的 `lingdong_redesign_spec.md` 和 SVG/HTML 参考稿。

目标：把前端从“多个工具页并列切换”重构成一个高端、人性化、动感的自动化工作台。核心不是换皮，而是降低用户理解成本，让用户清楚知道 Agent 正在观察什么、执行什么、用什么证据证明成功，以及成功后如何沉淀为可复用流程。

请按以下方向实现：

1. 新增或重构 `AppShell`：左侧模式栏 + 顶部 `CommandDock` + 主内容区。保留现有 viewMode，但 UI 表达改为“总览 / Agent / 流程 / Replay / 模板 / 设置”。顶部始终保留全局搜索、运行、停止、验证、保存、状态芯片。
2. 新增 `AgentCockpit` 包裹现有 `AgentView`，不要破坏 iframe 常驻逻辑。外层增加任务输入区、执行时间线、证据面板、安全护栏、成功后操作入口：保存模板、导出 WPS、导入流程图。
3. 重构流程页为 `FlowStudioLayout`：左侧节点库改成搜索优先和推荐组合，中央画布保留现有 `FlowCanvas`，右侧属性面板改成先解释节点意图再展示参数，并对高风险动作显示验证建议。
4. 重构 `ReplayEditorView` 为 `ReplayLab`：保留 trace、WPS 列表、导入流程图、原样重放能力；新增 WPS step → FlowScript 节点映射说明、不可等价步骤提示、原始截图/证据入口和校准工作台。
5. 新增统一 `EvidencePanel`：用于展示 before/after/diff、文件存在、窗口状态、OCR、done check、trace summary。先允许 mock/占位，但接口位置必须清晰，后续可接 `/api/trace/{run_id}/summary`。
6. 新增流程图排版规则：主路径自上而下，失败恢复向右展开，高风险动作后必须可见验证节点，重试最多 1–3 次，不要形成难读的大环。可以先实现“整理布局”按钮或纯函数工具，不要求一次做到完美。
7. 统一视觉 tokens：深色优先、玻璃质感、Indigo/Cyan 渐变、状态色明确、轻量动效。不要到处写 inline style，优先把样式收敛到 CSS tokens 和组件 class。
8. 保持项目边界：WinPilot Agent、FlowScript、WPS Replay 仍然各自独立；导入/编辑只是桥，不要把 Agent 直接改成读写流程图内部结构。

验收标准：`npm run build` 通过；原有 Agent iframe 能正常打开并跨页面切换保活；流程图编辑器仍可拖拽/连线/运行；Replay 原样重放和导入流程图入口仍存在；新 UI 有清晰空状态、错误提示和用户下一步建议。
