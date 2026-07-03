# Changelog

## Unreleased

### Refactoring

- Split WinPilot GUI tool schema construction from runtime dispatch:
  - `agent/tool_schemas.py` now owns compact OpenAI function schemas.
  - `agent/tool_result.py` now owns the shared `ToolResult` object.
  - `agent/window_tools.py` now owns window listing/scanning/focus/app-discovery tools.
  - `agent/perception_tools.py` now owns observe/read_text/VLM/template/color perception tools.
  - `agent/interaction_tools.py` now owns click/type/hotkey/scroll/drag/draw/wait tools.
  - `agent/system_tools.py` now owns run_shell/run_detached/launch/tray/search/undo tools.
  - `agent/plan_tools.py` now owns revise_plan/complete_subgoal/done/fail tools.
  - `agent/file_tools.py` now owns generic file tools used by `ToolSession`.
  - `agent/memory_tools.py` now owns memory/replay tools used by `ToolSession`.
  - `agent/desktop_tools.py` now owns clipboard, mouse fine-control, screenshot, display, and app-list tools.
  - `agent/tools.py` remains the runtime `ToolSession`, dispatcher, and target-window state surface.
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
- Added a concise runtime/state-machine map for the current GUI path: `/api/chat`, server `_RunState`, runner routing, `AgentRunner.status`, `ToolSession.dispatch()`, BUS events, trace files, and done verification.
- Documented the forensic GUI debugging workflow: inspect trace jsonl events, before/after screenshots, and per-step LLM request/response files to verify every tool call against the real screen.
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
- Documented the low-latency GUI controller path: batch subgoals, automatic screen watcher, recent/action ROI, anchor-cache revalidation, `low_latency`/`fast_gui` summary metrics, and the current limitations.

### Low-latency GUI agent

- Added automatic `screen_watch` startup/shutdown around target windows and visible actions.
- Added `run_action_batch(subgoal=...)` so traces can connect a controller batch to the high-level intent.
- Added `click_element` anchor evidence (`anchor_locator`, `resolved_element`) and made `ElementCache` revalidate cached anchors against the current snapshot instead of reusing stale `elem_id` values across observes.
- Added independent before/after frame diffing for visible action `screen_delta`; the watcher now records action-attributed ROIs in `action_changed_regions`.
- Made `observe(changed_only=true)` prefer fresh action ROIs before falling back to regular recent watcher ROIs.
- Extended trace summaries with `low_latency` and `fast_gui` metrics for screen capture, perception, LLM decision, action execution, post-action verification, batch subgoals, controller phases, and anchor click/cache/failure counts.
- Verified the webpage GUI target through the real `/api/chat` path with UIA disabled: run `20260703-025138-dcf2c6` reached `LOCAL_COUNTER_1` via `run_action_batch` + `observe(changed_only=true)` and passed fresh-screen done verification.
- Verified the merged state with `python -m pytest python/winpilot_home/tests -q`, `npx vitest run`, and `npm run build`.

### Agent hardening already present on this branch

- Treat plain MiMo chat models such as `mimo-v2.5-pro` as text-only unless the
  model name explicitly indicates multimodal/vision support, preventing
  unsupported `image_url` requests on text-only endpoints.
- Prefer a matching foreground top-level window over a stale remembered hwnd
  when resolving the current target window, reducing false success with
  duplicate same-title windows.
- GUI tasks route to `AgentRunner`; coding/repository tasks route to `CodingRunner`.
- `CodingRunner` includes `github_search` and run-gate logic for clone/install/run tasks.
- GUI-only tasks restrict shell/file shortcuts that would bypass real frontend operation.
- Done verification includes stronger guards for drawing, communication, GUI search, upload/publish, and run-gated coding tasks.
- Perception fusion combines UIA, Win32, OCR, CV, and optional VLM, with modal-window UIA skip and visual summaries for canvas/drawing/color tasks.
- `agent.llm_trace` records forensic LLM inputs/outputs for debugging model mistakes.
