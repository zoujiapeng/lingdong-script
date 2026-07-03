# Replay / ARP / FlowScript convergence

This note keeps replay and automation as a Runtime/Skill inside the unified AgentRunner. It should not become another user-visible agent or another planning loop.

## Boundary

The main AgentRunner owns task intent, tool exposure, run state, evidence, and done verification. Replay code should register actions and skills through the same ToolSession/Runtime boundary used by GUI, Coding, Shell, File, and Browser capabilities.

The replay runtime should consume Trace and ActionResult records. It should not ask the model to reason about internal flowchart details unless a replay step fails, is ambiguous, or needs user-facing explanation.

## Shared IR

Use one replay node record for WPS trace, replay, ARP/action replay, and FlowScript:

```text
AutomationNode
  id
  kind                 # gui_action | code_action | shell_action | browser_action | condition | wait | verify | skill_call
  label
  inputs
  outputs
  preconditions
  action_schema_ref
  verification
  retry_policy
  timeout
  error_handler
  trace_refs
  ui_metadata          # optional flow editor layout only
```

Flow editor nodes and ARP nodes should be views over this record, not separate executable concepts. UI metadata must not affect execution semantics.

## Replay modes

1. Fully offline replay: Trace -> AutomationNode graph -> editable FlowScript/ARP view -> run through the same ToolSession dispatcher.
2. Semi-offline replay skill: Trace -> parameterized AutomationSkill -> controller observes current state -> runs known routine -> repairs only failed or ambiguous nodes.
3. Apprentice mode: demo/user/agent execution -> Trace -> summarize stable intent and variable inputs -> save AutomationSkill with tests/evidence requirements.

## Controller-first replay

GUI replay should prefer controller-first routines for stable low-level sequences:

```text
subgoal -> execute_subgoal -> fast controller actions -> evidence_packet
```

Only failed `execute_subgoal` results should expand into LLM planning. The evidence packet should feed both done verification and future skill extraction.

## Prompt policy

Do not put flowchart internals into the main agent prompt. The prompt may mention `execute_subgoal`, Replay Runtime, and AutomationSkill at a high level; node editing details belong in the FlowScript/ARP editor and runtime docs.
