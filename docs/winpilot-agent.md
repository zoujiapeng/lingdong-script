# WinPilot Agent 旧实现退役说明

> `AgentRunner`、`ToolSession`、Task Contract、StageRuntime 和旧 `/api/chat` 已于
> 2026-08-19 从当前树删除。本文不再保存一份会漂移的旧实现手册。

当前产品主链是：

```text
Electron「智能代理」
  -> POST /api/v2/goals/runs
  -> one LiveVisualActor
  -> MissionRuntime
  -> AtomicDesktopAdapter
  -> RawDevice / SendInput
  -> new authoritative screen
  -> same model continues, claims, or reports blocked
```

Studio `ACTION_BATCH` 不再创建 `ToolSession`，而是在 run 内复用
`fast.controller.FastGuiController` 执行 canonical action。Studio/Replay 是显式确定性兼容面，
不是产品 Agent 的 fallback。

维护规则：

- 不要恢复 `agent/`、Task Contract、StageRuntime、第二 planner、独立 verifier 或 evidence/hash
  chain；
- 主 Agent 只有一个原始 goal、一个语义 Actor 和一条视觉执行线；
- Runtime 只保留模型守不住的机械边界：桌面输入单写入、按键/指针释放、凭据不泄漏和
  不可逆外部动作同意；
- 失败按 `model arguments → canonical arguments → physical_attempt → external state` 找首个分歧；
- 需要研究旧实现时使用 Git 历史，例如 `git show 50ce370^:docs/winpilot-agent.md`，不要把历史
  文本复制回当前架构说明。

当前事实见 [架构说明](architecture.md)、[vNext 迁移](vnext-migration.md)、
[测试规范](testing.md) 与仓库根目录 [CLAUDE.md](../CLAUDE.md)。
