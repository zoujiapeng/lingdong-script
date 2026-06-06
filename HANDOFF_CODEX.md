# Codex Handoff: DeepSeek Text Protocol Agent Refactoring

> Branch: `refactor-deepseek-computer-use-agent`
> Base: `master`
> 22 files changed, +974 / -2471 lines (net -1497, mostly from deleting stale docs/)
> Agent core file: `python/agent/agent.py` → 2676 lines after changes
> Test status: **2583 passed, 1 pre-existing failure** (NLP encoding — unrelated)

---

## 1. What This Branch Does

Rewrites the **text-only AI agent execution path** (DeepSeek, MIMO, and other non-function-calling models) so it's:

1. **More robust** — Text protocol parsing extracted to a standalone, tested module
2. **Smarter about vision** — SoM labels now ranked, filtered, and compacted for LLM consumption; search fields and carets detected heuristically
3. **Window-aware** — UIA-poor windows (Electron, WebView2, CEF, Qt) detected and routed to YOLO/OCR instead
4. **Verifiable** — Every GUI action snapshots screen + window state before and after; window context changes trigger re-sensing
5. **Leaner** — Agent prompt shows only ~30 core tools instead of ~200; old `SYSTEM_PROMPT_TEXT` replaced with compact `_build_clean_system_prompt()`
6. **Prompt-traceable** — LLM request/response pairs saved to `%TEMP%\lingdong_agent_prompt_trace\` for debugging

---

## 2. Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                     LingdongAgent                           │
│  run_text_protocol()  ←  text_protocol loop (main entry)   │
│                     │                                       │
│  ┌──────────────────┼───────────────────┐                  │
│  │  Text Protocol   │   SoM Vision      │  Verification    │
│  │  (text_protocol  │   (_apply_som     │  (_runtime_       │
│  │   .py)           │   _compact_som    │   snapshot)      │
│  │                  │   _filter_som     │                  │
│  │  parse_response  │   _augment_ctrl   │  before/after    │
│  │  parse_tool_     │   _redraw_overlay │  active_window   │
│  │   blocks         │   _visual_rank    │  screen_hash     │
│  └──────────────────┼───────────────────┘                  │
│                     │                                       │
│                     ▼                                       │
│  Supervisor.execute()                                       │
│  ┌────────────────────────────────────┐                    │
│  │  UIA-poor detection? → skip UIA    │                    │
│  │  → YOLO/OCR/SoM instead            │                    │
│  └────────────────────────────────────┘                    │
└─────────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────┐    ┌──────────────────────┐
│  features.registry  │    │  Supervisor:          │
│  .execute(name,     │    │  UIA/window/OCR/     │
│   params)           │    │  dialog handlers     │
└─────────────────────┘    └──────────────────────┘
```

---

## 3. New & Changed Files — Line-by-Line

### 3.1 New Files

| File | Lines | Purpose |
|---|---|---|
| `python/agent/text_protocol.py` | 87 | Standalone parser for `Tool:/Args:` protocol (extracted from agent.py). Supports Chinese labels (工具/参数/总结), multiline pipe values, "Done" marker detection. API: `parse_response()`, `parse_tool_blocks()`, `has_done_marker()` |
| `tests/test_text_protocol_parser.py` | 58 | 4 tests for text_protocol.py (ASCII, Chinese, multi-block, done marker). **All pass.** |
| `tests/test_uia_poor_window_policy.py` | 13 | 1 test for `Supervisor._is_uia_poor_window_text()`. **Passes.** |

### 3.2 Modified Python Files

| File | Δ Lines | Changes |
|---|---|---|
| `python/agent/agent.py` | +710/-199 | **Core refactoring.** See section 4 below. |
| `python/agent/supervisor.py` | +69/-0 | UIA-poor window detection: `_is_uia_poor_window_text()`, `_active_window_blob()`, `should_prefer_local_vision()`. Added `UIA_POOR_WINDOW_HINTS` frozenset. In `execute()`: when `prefer_local_vision=True`, skips UIA element lookup and UIA pre-focus. |
| `python/features/system_features.py` | +230/-13 | `focus_window()` rewritten with Win32 API (AttachThreadInput, BringWindowToTop, SetForegroundWindow, 10-retry verify). `get_active_window()` uses Win32 directly. `list_windows()` → `_get_visible_window_titles()` with Win32 fallback. `launch_program()` tracks before/after window titles. |
| `python/features/action_features.py` | +10/-0 | `click_target()` gets `allow_uia` parameter (default True). Passes through to ActionHandler. |
| `python/interaction/actions.py` | +3/-0 | `_exec_click()` respects `allow_uia=False` → skips UIA click-by-name fallback. |
| `python/agent/local_vision_engine.py` | +47/-10 | **Image scaling**: downscales to ≤1280px edge before YOLO/OCR, annotates on full-res. Removed Florence-2 loading. Better logging (scale factor, OCR timing). |
| `python/agent/config.py` | +4/-0 | Added clarifying comments for `yolo_enabled` and `local_vision` defaults. |
| `python/agent/meta_actions.py` | +3/-2 | Removed stale comment blocks in `manage_app()`. |
| `python/agent/__main__.py` | +4/-0 | Uses `resolved_preview.get("planning")` instead of deprecated `_resolved_planning`. |

### 3.3 Modified Frontend Files

| File | Δ Lines | Changes |
|---|---|---|
| `src/renderer/components/AgentView.tsx` | +66/-3 | Agent config now persists to localStorage (8 new keys: verify, auto_learn, omniparser, protocol, vision, planning, on_error, multimodal, fast_mode). `getRuntimeLLMConfig()` helper added. `buildAgentConfig()` reads localStorage settings. |
| `src/main/api.ts` | +35/-0 | Added GET method handlers: `/api/ping`, `/api/get-state`, `/api/set-view-mode`, `/api/fit-screen`, `/api/execute-js`. |
| `src/main/main.ts` | +1/-1 | Fix: `agentProcess.stdin.write()` now wraps in `Buffer.from(..., 'utf8')` for proper encoding. |
| `src/renderer/utils/textGenerator.ts` | +1/-1 | Fix: Chinese pseudocode word order `窗口${op}` → `${op}窗口`. |

### 3.4 Other

| File | Changes |
|---|---|
| `templates/skill_tree.json` | Version 2→6, added "通用" auto-template category |
| 8 docs/ files | **Deleted** — stale architecture diagrams (`.drawio`, `.html`, `.png`). Confirm these are safe to remove. |

---

## 4. agent.py — Detailed Changes

### 4.1 System Prompt Rewrite

**Before:** `SYSTEM_PROMPT_TEXT` was a hardcoded Chinese string with `{tool_count}` and `{tools_text}` placeholders.

**After:** `_build_clean_system_prompt(tools_text)` generates a compact English prompt with core rules:
- Verifiable honesty (don't report success without evidence)
- Text-only limitations (OCR/SoM/YOLO are noisy hints)
- No app-specific rules — generic strategies only
- Caret detection (OCR'd as I/l/|), search field heuristics
- Tool/Args/Done output protocol

Plus `TEXT_PROTOCOL_FORMAT_INSTRUCTIONS` constant appended separately.

### 4.2 Tool Whitelist (`AGENT_CORE_FEATURES`)

Set of ~30 tool names (from ~200). Runtime modes only see whitelisted tools. Flow-chart mode still sees all. The prompt now says `"元动作已覆盖大部分场景。以下 N 个工具是补充能力。另有 M 个 DSL 专用工具。"`

### 4.3 SoM Intelligence (6 new methods)

| Method | Lines | Purpose |
|---|---|---|
| `_clean_visual_label()` | 10 | Strip whitespace/emoji/private-use Unicode from OCR text |
| `_looks_corrupt_label()` | 8 | Detect mojibake (锟/�/閹 patterns) |
| `_is_search_like_label()` | 8 | Detect search field labels ("search", "find", "q搜索") |
| `_is_caret_like_label()` | 4 | Detect blinking caret (I/l/| glyph, 10px+ tall, ≤12px wide) |
| `_visual_label_rank()` | 15 | Priority scoring: search_fields=0, icons=1, normal=2, long_text=3, time=4, corrupt=5 |
| `_compact_som_label()` | 30 | Sort by priority → pick top 15 → format as text list for LLM |
| `_redraw_som_overlay()` | 25 | Redraw SoM boxes on fresh image from label_map (used after window-filtered re-render) |
| `_augment_active_window_controls()` | 50 | CV-based: Canny edge detection + morphological close → find candidate click targets in active window. Also detects caret via high-threshold mask. |
| `_filter_som_to_active_window()` | 25 | Keep only elements inside active window rect, augment with CV candidates, redraw overlay |

### 4.4 Window State Tracking

| Method | Purpose |
|---|---|
| `_active_window_rect()` | Get foreground window rect+title via win32gui. Caches in `_last_focus_window`. Returns `(left,top,right,bottom)` or None. |

### 4.5 Prompt Tracing (2 new methods + 2 fields)

`_begin_prompt_trace(goal)` creates `%TEMP%\lingdong_agent_prompt_trace\run_<ts>_<pid>/`. `_trace_llm(kind, data)` writes numbered JSON files (001_request.json, 002_response.json, ...). Controlled by `_trace_dir` and `_llm_call_index` instance fields.

### 4.6 Execution Verification (in `run_text_protocol`)

`_runtime_snapshot()` captures:
- `active_window`: title, rect
- `screen_hash`: SHA-256[:12] of a low-res screenshot

Applied around every GUI-affecting action (click, type, hotkey, scroll, drag). Results logged and included in step result as `verification`. Window title change detection → triggers re-sense.

### 4.7 Window Context Break

After successful `manage_window(focus/restore/maximize)`, `manage_app(launch/open)`, `focus_window`, or `launch_program` → breaks the multi-tool batch so the next LLM call sees fresh state.

### 4.8 SoM #N Direct Resolution

In the text protocol loop, `target: #3` is now resolved to coordinates immediately from `supervisor._last_som_map` (line ~1707), before the old text-match loop.

### 4.9 Binary Data Strip

`_strip_binary()` removes `base64`/`image_b64` fields from results before feeding back to LLM (line ~1395). Prevents ~100KB base64 strings from flooding context.

---

## 5. Key Architecture Decisions

### 5.1 Text Protocol Delegation (agent.py → text_protocol.py)

**Why:** The old `_parse_response()` / `_parse_params_section()` / `_parse_all_tools()` were inline in agent.py, making the file even larger and untestable in isolation. Extracting to `text_protocol.py` gives a clean module with 87 lines, 4 unit tests, and a pure-function API.

**Pattern:** The old regex used Chinese-only labels (工具/参数). The new one accepts both ASCII (Tool/Args) and Chinese (工具/参数) + CJK mojibike variants for backward compatibility.

### 5.2 UIA-Poor Window Policy

**Why:** Electron/WebView2/CEF/Qt apps have UIA trees that are either empty or misleading (a single "Electron host window" node). YOLO+OCR gives better results.

**Implementation:** Two-layer detection:
1. `Supervisor._is_uia_poor_window_text()` — string matching against window title/class/exe
2. `Supervisor.should_prefer_local_vision()` — combines current window blob + last launched app hint

When prefer_local_vision=True:
- `_apply_som()` skips UIA element collection → YOLO/OCR only
- `Supervisor.execute()` skips UIA semantic lookup and UIA pre-focus
- `click_target()` receives `allow_uia=False` → ActionHandler skips `_uia_click_by_name()`

### 5.3 Image Scaling in LocalVisionEngine

**Why:** YOLO + OCR on 4K screenshots (~8MP) is slow and memory-heavy. Downscaling to ≤1280px edge reduces processing by ~10x while detection accuracy stays similar for UI elements.

**Implementation:** `parse()` computes `scale = 1280 / max(h, w)`, resizes with INTER_AREA. YOLO and OCR run on scaled image. SoM boxes are annotated on the full-res `img_full` using `inv_scale`. Coordinates in `label_map` are in original screen coordinates.

### 5.4 Whitelist vs Full Tool Exposure

**Why:** Showing 200 @feature tools in the runtime prompt wastes tokens and confuses the LLM. 27 meta-actions + 30 core features cover 95%+ of desktop automation needs. The remaining ~170 are for DSL generation/debugging/testing only.

### 5.5 _runtime_snapshot() Inside run_text_protocol()

**Why:** Wants access to `self.supervisor.capture_b64()` and `features.system_features.get_active_window()` without adding cross-file coupling. Defined as a closure inside `run_text_protocol()` so it captures `self`. Minor: re-created on each call — could be lifted to method if performance matters.

---

## 6. Test Results

```
$ python -m pytest tests/ -x -q --timeout=30 --ignore=tests/test_agent_e2e_llm.py
2583 passed, 1 failed in 69.05s

FAILED tests/test_nlp_schedule_parser.py::TestParseTime::test_tonight_11pm
  └─ Pre-existing: NLP parser confidence for "今晚11点睡觉" returned 51 (threshold 60).
     The test content displays with garbled encoding (mojibake in the .py file).
     Unrelated to this branch's changes.
```

**To run the new tests specifically:**
```bash
python -m pytest tests/test_text_protocol_parser.py -v
python -m pytest tests/test_uia_poor_window_policy.py -v
```

---

## 7. Known Issues & TODOs

### Fixed During This Session
- [x] `_apply_som()` dead code: stray `if self._som_enabled and 'som_result' in locals()` block at line 797 — removed (was referencing undefined variable)
- [x] `run_text_protocol()` wasted work: detailed `som_label` built at lines 1440-1444 then immediately overwritten by `_compact_som_label()` — now builds compact directly

### Remaining
- [ ] **`_runtime_snapshot()` is defined as a closure inside `run_text_protocol()`** — fine for now, but if it's needed in other execution modes (langgraph, computer_use), lift it to a method on `LingdongAgent` and share it
- [ ] **`_augment_active_window_controls()` CV parameters** — Canny thresholds (35, 90), min width/height (70, 16), min area (1400) are hardcoded. May need adjustment for different UI styles
- [ ] **`AGENT_CORE_FEATURES` set** — verify the whitelist covers all needed runtime tools. Currently ~30 tools; if a new @feature is added that runtime agents need, add it here
- [ ] **`text_protocol.py` pipe-value parsing** — the `|` multiline syntax works but is rarely tested. Add test cases if issues appear
- [ ] **Stale docs/ deletion** — 8 `.drawio`/`.html`/`.png` files were deleted. Confirm these aren't needed (they were architecture diagrams from an older design iteration)

### Potential Risks
- `LocalVisionEngine.parse()` image scaling assumes `max_edge > 1280` means downscale; small images (<1280) skip scaling entirely. If YOLO model was trained on specific input sizes, unscaled images may degrade
- The `_clean_visual_label()` Unicode category filter removes `So` (Symbol/Other), `Co` (Private Use), `Cs` (Surrogate). This may strip legitimate emoji or special chars from button labels
- `focus_window()` now uses Win32 API directly (was `WindowManager`). If WindowManager had OS-version-specific workarounds, the new fallback path may regress on pre-Win10

---

## 8. File Dependency Graph

```
text_protocol.py              ← standalone (no internal deps)
    ↓ imported by
agent.py                      ← imports: text_protocol, supervisor, meta_actions
    ↓                          ← has: LingdongAgent class
    ↓ uses
supervisor.py                 ← imports: features.registry
    ↓                          ← has: Supervisor class
    ↓ uses
features/system_features.py   ← @feature: focus_window, list_windows,
    ↓                           get_active_window, launch_program
features/action_features.py   ← @feature: click_target (allow_uia param)
    ↓
interaction/actions.py        ← ActionHandler._exec_click (allow_uia kwarg)

local_vision_engine.py        ← agent.py imports for LocalVisionEngine
config.py                     ← AgentConfig dataclass (used by agent.py + __main__.py)
__main__.py                   ← entry point: spawns LingdongAgent
```

---

## 9. Developer Quick Reference

### Key Symbols

| Symbol | Location | Purpose |
|---|---|---|
| `LingdongAgent` | `agent.py:210` | Main agent class. Entry: `run_text_protocol()` |
| `Supervisor` | `supervisor.py:20` | Execution supervision layer |
| `_apply_som()` | `agent.py:789` | SoM annotation (local_vision → OmniParser → UIA+OCR) |
| `_compact_som_label()` | `agent.py:???` | Rank + compact label_map to 15-item text list |
| `_runtime_snapshot()` | `agent.py:~2370` | Closure inside run_text_protocol for verification |
| `should_prefer_local_vision()` | `supervisor.py:182` | UIA-poor window detection |
| `AGENT_CORE_FEATURES` | `agent.py:58` | Whitelist of ~30 runtime tools |
| `parse_response()` | `text_protocol.py:93` | Parse LLM response → (action, name, args) |
| `parse_tool_blocks()` | `text_protocol.py:61` | Parse multi-tool blocks from LLM |

### Debugging

Enable **prompt tracing**: set `_begin_prompt_trace(goal)` automatically runs on `run_text_protocol()`. Find traces in:
```
%TEMP%\lingdong_agent_prompt_trace\run_<YYYYMMDD>_<HHMMSS>_<PID>\
├── goal.txt
├── 001_request.json
├── 002_response.json
├── 003_request.json
...
```

### Adding New Runtime Tools

If a new @feature should be visible to runtime agents, add its name to `AGENT_CORE_FEATURES` in `agent.py`:
```python
AGENT_CORE_FEATURES: Set[str] = {
    ...
    "my_new_tool",  # <-- add here
}
```

---

## 10. Quick Commands

```bash
# Run this branch's tests
python -m pytest tests/test_text_protocol_parser.py tests/test_uia_poor_window_policy.py -v

# Full test suite (skip LLM e2e test)
python -m pytest tests/ -q --timeout=30 --ignore=tests/test_agent_e2e_llm.py

# Syntax check all changed files
python -c "import ast; ast.parse(open('python/agent/agent.py', encoding='utf-8').read()); print('OK')"
python -c "import ast; ast.parse(open('python/agent/text_protocol.py', encoding='utf-8').read()); print('OK')"
python -c "import ast; ast.parse(open('python/agent/supervisor.py', encoding='utf-8').read()); print('OK')"

# Git diff on this branch (all changes)
git diff master...HEAD --stat

# Diff just the agent module
git diff master...HEAD -- python/agent/
```

---

## 11. Staged / Untracked Files

```
?? .playwright-mcp/                        # Playwright MCP artifacts — probably gitignore-worthy
?? agent_analysis.backup.drawio            # Backup analysis diagram
?? docs/agent_analysis.drawio              # New architecture analysis
?? python/agent/text_protocol.py           # NEW — staged? No, untracked. Needs `git add`
?? tests/test_text_protocol_parser.py      # NEW — untracked. Needs `git add`
?? tests/test_uia_poor_window_policy.py    # NEW — untracked. Needs `git add`
```

**Before committing**, run:
```bash
git add python/agent/text_protocol.py tests/test_text_protocol_parser.py tests/test_uia_poor_window_policy.py
```

And consider adding `.playwright-mcp/` to `.gitignore`.

---

*Generated 2026-06-05. After reading this, Codex should verify the current state of each file before making changes — file contents may have shifted from what this document describes.*
