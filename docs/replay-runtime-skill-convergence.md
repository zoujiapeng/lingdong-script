# Replay / ARP / FlowScript 收敛方向

本文只记录当前设计边界，避免把 replay、流程图和 skill 误解成多个用户可见 agent。

## 当前边界

- 用户仍面对一个 WinPilot Agent。
- `.wps.json` 是 WinPilot 语义 replay 格式，来源是真实 trace。
- FlowScript 是前端流程图格式，来源是 `src/renderer/`。
- `python/engine/` 是 legacy DSL/Flow 执行器，不是 WinPilot 低延迟 controller。
- `reuse_skill` 只是强匹配 `.wps.json` 的 0-token replay；replay 成功后仍要用当前屏幕/文件/进程/文本证据通过 done gate。

## 收敛目标

未来应该把录制、replay、ARP、FlowScript 看成同一个 Automation Runtime 的不同入口：

```text
Trace / Human recording / Flow editor
  -> Automation IR
  -> validate safety + applicability
  -> replay by WinPilot controller or legacy executor
  -> collect current evidence
  -> done gate
```

这不是拆新 agent，而是给同一个 AgentRunner 增加可复用 skill/runtime 能力。

## 最小共享字段

共享 IR 至少需要：

- `id`
- `name`
- `source`
- `conditions`
- `evidence`
- `steps`
- `variables`
- `safety`
- `fallback`

`.wps.json` 中 WinPilot-only 的动作可以保留原始 payload。导入 FlowScript 时，如果当前 editor/executor 没有等价节点，应导入为 `tool_call` 占位并标记需要 WinPilot replay，而不是丢失语义。

## Prompt policy

- 主 prompt 不应塞流程图内部细节。
- LLM 只需要知道是否存在适用 skill、适用条件、证据要求和失败恢复方式。
- 常规 replay 由本地 runtime 执行；LLM 只处理不适用、失败、证据不足或需要重新规划的情况。

## 当前还没完成

- 统一 Automation IR 尚未落地为唯一格式。
- FlowScript executor 和 WinPilot Replayer 仍是两套执行路径。
- Skill 复用率、失败归因和 replay 后证据采集还需要继续工程化。
