# Changelog

## Unreleased

### Native visual GUI closed loop and frontend parity (2026-07)

- Made `mimo-v2.5` an explicitly supported native-image main model and kept `mimo-v2.5-pro` on the text-only route; unsupported screenshot requests are no longer inferred from a provider family name. MiMo/Reasonix tool decisions disable provider thinking by default, while optional continuous-spatial reasoning remains an explicit experiment.
- Replaced the old “always send the whole desktop” assumption with conservative capture selection. `controller.target_window_capture_enabled` uses a verified foreground target-window crop only when it is safe and materially smaller; multi-window, cross-app, global-desktop, invalid, hidden, off-screen, tiny, or ambiguous targets fall back to the complete virtual desktop. Every frame reports its scope and physical region.
- Tightened the native-vision transaction loop: the model gets an initial authoritative frame and a fresh post-action frame, one visual mutation is accepted per turn, stale/missing frames fail closed, preflight checks use a grounded target ROI when possible, and final semantic completion is judged only from a newly captured goal-conditioned frame or deterministic evidence.
- Hardened generic continuous-pointer execution without adding cat/dog/Paint-specific production rules. Tool arguments repair flat or stringified stroke arrays; paths are fit coherently to one detected work surface instead of clipping points independently; truncated/partial model output cannot trigger destructive recovery; visible partial work is repaired forward rather than entering repeated clear/undo loops.
- Reduced the system prompt to one compact, vision-first contract. Same-frame UIA/OCR/VLM decomposition is skipped for ordinary native-vision observations; structured perception remains available for explicit queries, text-only models, grounding, verification, and recovery. Domain playbooks remain opt-in.
- Made the live todo plan model-editable at any time. `revise_plan` can replace or clear the list, or atomically add, update, remove, move, and reopen items while retaining unrelated verified progress.
- Added experimental human/Agent input arbitration behind `controller.multi_input_cooperation=false`. It prefers background-safe window/UIA actions, never steals keyboard focus from an unrelated foreground window, lets human cursor movement win during long physical input, and blocks when target ownership cannot be proved. Windows physical input remains single-writer.
- Added runtime contract v2 and a process-loaded source fingerprint. Electron and the renderer reject incompatible, source-drifted, or different-commit Python controllers; new frontend ARP runs also fail closed until the managed runtime is restarted.
- Localized the native workbench to Chinese while preserving technical identifiers such as API, MCP, VLM, URL, model names, and coordinate fields.
- Verified the real frontend path from a blank Paint process for `用画板画一只猫`: ARP skill miss -> `agent_goal` -> one continuous stroke transaction -> fresh-frame completion in about 55 seconds. The result remains visually rough, so this is evidence for the end-to-end generic control path, not a claim of stable artistic quality or a statistical success rate.

### Documentation and repository metadata (2026-07)

- Synchronized README, architecture, Agent, provider, API, replay, flow-editor, memory, desktop-bridge, interview, contribution, and security documentation with the current code and explicit maturity limits.
- Synchronized `config.example.json` with current defaults, including native-vision direct mode, capture scope, visual closed-loop/preflight/servo controls, cooperative input, incremental perception timing, and ChatGPT desktop integration.
- Refreshed the DeepSeek V4-Flash fallback estimate to the official 2026-07-14 CNY rates: 1 yuan/M uncached input, 0.02 yuan/M cached input, and 2 yuan/M output.
- Added GitHub contribution and security guidance; the repository still has no declared project license pending an explicit owner decision.
- Local verification baseline on 2026-07-14: WinPilot `1342 passed`, root Python `2508 passed`, Vitest `194 passed`, and the production build completed. GitHub publication status is determined separately by Actions for the pushed commit.

### Unified runtime hardening

- Added authenticated loopback control planes with strict renderer origins, WebSocket authentication, atomic WinPilot configuration writes, and DPAPI-backed legacy execution-vault storage/migration.
- Added `RuntimeJobManager` for queued Agent, ARP, Replay, and scheduled jobs. Interactive desktop jobs declare the shared `interactive_runtime` resource instead of failing with a server-wide busy response.
- Added a raw desktop device adapter and per-target desktop action queue. Fast controller batches no longer recurse through the full outer `ToolSession` lifecycle for every child action.
- Added a shared per-window perception daemon with screen-delta and element-cache leases, plus action-id based asynchronous trace screenshots.
- Made `automation-ir/1` the canonical persisted automation artifact. Trace export, apprentice lessons, memory skills, replay, Flow import/export, and ARP execution now cross checked adapters around that IR; `.wps.json` remains a compatibility format.
- Made completion verification fail closed: model narrative and repeated done attempts cannot force acceptance without current typed evidence.
- Added virtual-desktop/DPI/ROI coordinate diagnostics and actual cursor-position checks for negative-origin and multi-monitor layouts.
- Replaced the iframe-era renderer with the native `PlatformWorkbench`, added schedule controls and taskbar completion/failure notifications, and removed unreachable HTN/Node-RED/legacy panel code and dependencies.
- Reduced the production renderer bundle by replacing whole-package Lucide imports and deleting dead styles/components.

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

### Earlier agent hardening

- Treat `mimo-v2.5-pro` and other unverified chat models as text-only; the later explicit `mimo-v2.5` capability entry above is the only marker-free MiMo image route.
- Prefer a matching foreground top-level window over a stale remembered hwnd
  when resolving the current target window, reducing false success with
  duplicate same-title windows.
- GUI and coding/repository tasks route to the same `AgentRunner`; Coding is enabled as a runtime capability.
- The unified runner includes `github_search` and run-gate logic for clone/install/run tasks; `CodingRunner` remains only as an import-compatibility shim.
- GUI-only tasks restrict shell/file shortcuts that would bypass real frontend operation.
- Done verification includes stronger guards for drawing, communication, GUI search, upload/publish, and run-gated coding tasks.
- Perception fusion combines UIA, Win32, OCR, CV, and optional VLM, with modal-window UIA skip and visual summaries for canvas/drawing/color tasks.
- `agent.llm_trace` records forensic LLM inputs/outputs for debugging model mistakes.
