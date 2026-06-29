# Changelog

## Unreleased

### Refactoring

- Split WinPilot GUI tool schema construction from runtime dispatch:
  - `agent/tool_schemas.py` now owns compact OpenAI function schemas.
  - `agent/tool_result.py` now owns the shared `ToolResult` object.
  - `agent/window_tools.py` now owns window listing/scanning/focus/app-discovery tools.
  - `agent/perception_tools.py` now owns observe/read_text/VLM/template/color perception tools.
  - `agent/file_tools.py` now owns generic file tools used by `ToolSession`.
  - `agent/memory_tools.py` now owns memory/replay tools used by `ToolSession`.
  - `agent/desktop_tools.py` now owns clipboard, mouse fine-control, screenshot, display, and app-list tools.
  - `agent/tools.py` remains the runtime `ToolSession` and core input/action handler surface.
- Split pure GUI runner rules out of `agent/loop.py`:
  - `agent/task_rules.py` now owns GUI task classification and guard helpers.
  - `agent/done_verifier.py` now owns done-gate verification helpers.
  - `agent/context_guidance.py` now owns planning, world advisory, recovery, and playbook injection helpers.
  - `agent/reflection.py` now owns reflection checkpoint helpers.
- Split legacy Flow/DSL executor shared types into `python/engine/executor_types.py`.

### Size

- Added `npm run clean` for generated build outputs, caches, browser snapshots, and Python `__pycache__`.
- Added `npm run clean:runtime` for additionally removing ignored `python-runtime/`.
- Added `scripts/gui_eval_run.py` for frontend-path GUI eval evidence reports without committing local traces/screenshots.
- Removed tracked `.playwright-mcp` browser snapshots.
- Tightened Electron Builder resources to exclude Python caches, tests, dist-info/egg-info, local WinPilot runtime data, and extra Electron locales.
- Compressed GUI tool schema descriptions to reduce code size and LLM tool-context noise.

### Documentation

- Rewrote the project documentation from current code facts instead of stale historical Agent notes.
- Clarified the active architecture:
  - WinPilot lives in `python/winpilot_home/winpilot/`.
  - `python/agent/__main__.py` is an IPC adapter.
  - `python/features/` serves the flow editor/MCP/HTTP/DSL stack, not the WinPilot GUI agent.
  - `.wps.json` is the bridge from WinPilot trace/human recording to zero-token replay and FlowScript import.
- Removed stale documentation that described deleted or non-tracked current modules as active functionality, including old visual macro, `visual_locator`, `experience_db`, and old LingdongAgent internals.
- Documented the actual WinPilot REST endpoints from `server.py`.
- Documented the actual Electron HTTP endpoints from `src/main/api.ts`.
- Documented current WinPilot defaults from `config.py`, including `llm_trace`, `verify_done`, `prompt_style`, `world_model`, VLM toggles, and coding sub-agent config.
- Updated testing guidance to distinguish current WinPilot tests from root-level historical/legacy tests.

### Agent hardening already present on this branch

- GUI tasks route to `AgentRunner`; coding/repository tasks route to `CodingRunner`.
- `CodingRunner` includes `github_search` and run-gate logic for clone/install/run tasks.
- GUI-only tasks restrict shell/file shortcuts that would bypass real frontend operation.
- Done verification includes stronger guards for drawing, communication, GUI search, upload/publish, and run-gated coding tasks.
- Perception fusion combines UIA, Win32, OCR, CV, and optional VLM, with modal-window UIA skip and visual summaries for canvas/drawing/color tasks.
- `agent.llm_trace` records forensic LLM inputs/outputs for debugging model mistakes.
