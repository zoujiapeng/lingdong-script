# Changelog

## Unreleased

### Documentation and branch consolidation (2026-07-18)

- Re-audited the repository documentation against the current Electron entrypoint, Task Contract/ARP runtime, native-vision loop, Controller primitives, memory policy, MCP boundary, legacy Flow/Browser compatibility code, and known architectural debt.
- Replaced volatile run-by-run prose with durable architecture invariants: authoritative pixels, one client path, one action transaction, evidence-gated completion, generic pointer primitives, compiler limits, and memory as acceleration rather than capability.
- Added `docs/testing.md` as the canonical definition of real Electron-client E2E, cold-start isolation, multi-turn context, the 11 black-box probes, mixed long tasks, external oracles, focus interference, reporting, and release gates.
- Updated the architecture audit to distinguish reusable generic mechanisms from remaining duplication and application-specific debt. The old browser-based visual guide is intentionally not merged because it is a parallel presentation artifact rather than a runtime fact source.
- Re-ran the current WinPilot suite after replacing the client harness's undeclared `pywinauto` dependency with the repository-supported `uiautomation + Win32`: `1985 passed, 13 skipped`. The visible Electron cold-start `dynamic-hold` rerun passed trusted external pointer ground truth, and both GitHub Actions jobs passed. These are mechanism checks, not a claim that all 11 real tasks pass.

### Cross-frame pointer control and native-client stress matrix (2026-07-18)

- Generalized continuous pointer execution beyond drawing. Typed direct manipulation now supports a Controller-owned `pointer_hold_before/after` session across authoritative frames, allowing a target or route to be revealed only after the button is already down. Held sessions accept only movement and the matching release, have bounded timeout/cleanup, and are released on cancellation, mismatch, failure, or terminal transitions.
- Made a fresh frame a hard spatial-plan boundary while a pointer is held. Deferred coordinates authored against the old frame are discarded and replanned from current pixels; destination actions no longer inherit stale source regions, and candidate/anchor mapping uses current frame geometry.
- Preserved authored subtarget coordinates inside broad plain-OCR boxes, added one bounded current-frame UIA/Win32 semantic recovery for first no-progress clicks, and hardened exact-window focus with isolated Windows fallbacks plus transient `Windows.UI.Core.CoreWindow` dismissal.
- Added real Electron-client E2E harnesses: `client_visual_e2e_run.py` covers ordinary drag, slider, dynamic target reveal, constrained corridors, and game-like dragging with trusted external pointer ground truth; `client_e2e_run.py` contains the exact 11 user cases plus mixed and all-11 long tasks, resets session context by default, and verifies cold-start experience isolation.
- The 2026-07-18 visible-client visual suite passed `7/7` with `experience_policy=disabled`; the dynamic case used one trusted pointerdown, revealed its goal only after the checkpoint, read a new frame, and released inside the goal. WinPilot regression was `1929 passed, 13 skipped, 2 warnings`, and all four TypeScript typecheck targets passed.
- Kept the real-software result explicit: the latest QQ Space case exhausted 80 steps after a Windows Shell Experience focus takeover and is still failed; its trace is `arp-20260718-075223-92a997`. The subsequent generic focus fix has unit and manual reproduction evidence but the QQ case was not rerun after it, so this changelog does not claim it passed.

### General visual interaction reachability and control loop (2026-07-17)

- Added a renderer-to-runtime execution-context handoff so a task submitted from the native client preserves the intended foreground window instead of losing it when Electron focuses its own UI.
- Made mutation authorization fail closed: model actions never imply a missing `agent_should_act` decision. Harmless provider-shape drift is canonicalized only when it preserves explicitly supplied geometry; semantic uncertainty still requires a bounded correction or fails before input.
- Separated input-mode preparation, colour selection, and spatial execution into fresh-frame Controller transactions. UIA silence no longer proves a single-mode surface, and an old frame cannot authorize both a mode switch and subsequent drawing coordinates.
- Generalized current-pixel region, repeated-partition, complete-object placement, source-footprint, surrounding-surface, guided-path and A* clearance proofs. The implementation contains no object template, application name, recipient alias, or fixed screen coordinate branch.
- Added explicit autonomous and external-participant session semantics. Only contracts that require a participant may enter non-terminal `waiting_external`; an absent participant is classified separately from an Agent action failure and never counts as task completion.
- Expanded task intent and stage compilation for non-GUI domains and generated message content, retained current-run ArtifactStore provenance, and kept capability/verifier satisfiability as a pre-execution invariant.
- Updated the native run view to show execution context, current stage capabilities, artifact/evidence lineage, `waiting_external`, exact failure classification, and model/Controller/capture timing.
- Final local verification: WinPilot `1827 passed, 13 skipped, 1 warning`, root Python `2508 passed`, Vitest `209 passed`, and renderer/main/CLI/AI typechecks and builds all passed. The opt-in autonomous Windows visual matrix completed `8 passed, 1 deselected in 155.79s` with trusted browser pointer ground truth, 10 total model calls, and no replans or recoveries. The deselected shared-turn case requires a live participant and is reported only as an Agent-turn checkpoint.

### Native frontend audit, reachability, and performance (2026-07-16)

- Rebuilt the Run History view around `GET /api/v2/runs/{run_id}`. Selecting a run now shows its typed stages, required/verifier/exposed/missing capabilities, model and Controller tools, artifacts, evidence providers, Completion Contract, timing, retries, and exact failure stage instead of only a flattened timeline.
- Persisted bounded ARP audit snapshots and original start-time Task Contracts, reconstructed older stage/evidence/completion data from JSONL trace events after restart, and restored ArtifactStore manifests without reparsing old natural-language tasks. Older traces that predate contract snapshots are explicitly labeled as trace projections.
- Added a hash-verified, run-scoped artifact endpoint for live and historical records. Manifest paths are constrained to the managed run directory; file size, SHA-256, and image dimensions are revalidated before serving. The UI distinguishes sendable artifacts from observation frames and renders image thumbnails.
- Added one generic Agent/ARP cancel endpoint and wired the frontend stop action to it, removing run-kind guessing from the renderer.
- Reduced control-plane payload and request churn: run/job collection endpoints no longer embed full results/evidence, WebSocket events refresh only the current run in an 80 ms merge window, terminal events refresh full lists, `ping` causes no REST request, and fallback polling is five seconds.
- Added typed runtime identity/job/desktop-queue/perception contracts and a System view health panel for active jobs, resource waiters, asynchronous desktop actions, daemon leases/targets, duration, and errors.
- Made every Studio creation surface—main library, unified search, and recent items—use the same frontend-compiler/backend-catalog intersection. Removed the never-populated dynamic-feature search branch while retaining legacy `tool_call` schema editing.
- Removed unreachable renderer `RegionPickerControl` and `featureRegistry` modules, unused Electron template/schedule context generation, a duplicate legacy engine branch, and unused imports/state/parameters. Enabled `noUnusedLocals` and `noUnusedParameters` for renderer, Electron main, CLI, and AI TypeScript builds.
- Added frontend component tests, projection/API/artifact persistence tests, and browser visual verification against a real persisted Paint-to-QQ run. Browser verification restored five stages, four image artifacts, ten contract evidence records, and the delivery Completion Contract with no console errors; it did not resend the artifact or claim a new communication E2E.
- Final local verification: WinPilot `1731 passed, 13 skipped, 1 warning`, root Python `2508 passed`, Vitest `201 passed`, strict TypeScript checks for renderer/main/CLI/AI, and production renderer/Electron plus CLI/AI builds all completed successfully.

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
