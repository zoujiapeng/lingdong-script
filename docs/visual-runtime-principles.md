# Visual runtime principles

This project treats the model's current pixels as the primary source of
meaning. The runtime owns only the mechanics that every GUI task needs: frame
identity and coordinate mapping, one canonical input transaction, fresh
post-action evidence, and bounded recovery. It must not encode the rules of a
particular application, game, drawing exercise, or test sentence.

The vNext design keeps exactly one model role with desktop mutation proposal
authority: the Visual Actor. A low-frequency, read-only Task Profiler may
propose execution depth and cognitive services, but deterministic Execution
Policy decides what is accepted. Mission progress is a replayable projection
of mission definition, accepted events, and immutable run-scoped evidence;
tool exposure comes from a deterministic capability broker; verifier, planner,
and inspector roles are read-only and event-triggered. See
[`vnext/README.md`](vnext/README.md). This is currently a design target, not an
implemented runtime claim.

Models submit semantic proposals, not runtime identity. Mission revision,
active obligation, authoritative frame, evidence scope, and broker projection
are bound by the runtime envelope immediately before validation and dispatch.
This keeps mechanical identity work out of model reasoning and prevents stale
or cross-obligation writes.

## Reasoning and latency

Native visual reasoning is adaptive. The first frame, unchanged pixels, failed
verification, a blocked action, a stale frame, and a rejected completion claim
receive the provider's deeper reasoning mode. A routine turn following a
visibly verified mutation may use the provider's lightweight mode. This
decision uses live frame/action evidence only; it never matches task keywords.
An explicit `api.thinking=true` setting remains authoritative.

Every provider call has both a per-call SDK timeout and a runner-owned total
deadline. A timed-out response is abandoned before tool dispatch. A bounded
retry may retry the same decision with lightweight reasoning; it cannot replay
an already executed GUI action.

## Visual observation

The ordinary frame remains the model's authority. When the model needs more
detail, `observe` accepts an optional `image_region` in the current frame's
pixel coordinates. The runtime maps that crop back to the physical capture
region, captures it atomically, and makes the resulting crop the next
authoritative frame. Actions then use the crop's own image coordinates. This is
a generic zoom primitive, not a board/canvas/browser heuristic.

UIA, OCR, CV, and MCP are secondary probes. They are used only when the model
or an accepted capability contract explicitly asks for structured evidence;
they never silently replace pixels or choose a target selected by the model.

## External turns

`wait(until_change=true, persistent=true)` arms a generic watcher for a settled
change in the bound window or an explicit ROI. It publishes
`interaction_wait_external_change` while armed and returns whether a stable
external transition was observed. Typed interaction stages may use the same
watcher through their persistent `WAIT_FOR_EXTERNAL_CHANGE` stage. No app- or
conversation-specific waiting rule belongs in the Agent prompt.

## Evaluation

End-to-end evaluation must submit the original natural-language task through
the visible Electron client, with memory disabled/cold start when testing hard
skills. The 11 examples are probes, not production rules. Add unknown tasks and
mixed multi-turn tasks; record the TaskProfile/ExecutionProfile when vNext is
evaluated, frame/action trace, model wait, controller time, recovery reason,
and an independent application oracle. A unit test or a direct API call is not
a client E2E pass.
