# Visual runtime principles

The current pixels seen by the model are the primary source of GUI meaning.
The runtime owns only mechanics shared by arbitrary desktop tasks: frame and
window identity, one coordinate mapping, one canonical input transaction,
fresh post-action observation, physical-input cleanup, and bounded failure.
It must not encode an application's workflow, a game's rules, a test answer,
or a correction for the current model.

## Current primary loop

The live vNext path is:

```text
original goal + authoritative JPEG + bounded task state
  + at most one previous native tool exchange
-> one Visual Actor tool call
-> one physical transaction
-> a new authoritative JPEG
```

The previous native exchange is exactly the most recent user image, assistant
tool call, and physical-result summary. It is replaced after the next tool
call. There is no unbounded conversation history, second planner, Grounder,
Verifier, task classifier, or application prompt.

## Reasoning and latency

The validated baseline uses `api.thinking=false`,
`temperature=0.3`, a maximum submitted-image side of 1120, and one provider
attempt per Visual Actor decision. Enabling thinking made a corridor run
slower without fixing it; increasing the image side to 1600 and removing
temperature zero also regressed tests. Those experiments were reverted.

The primary vNext runtime must not automatically toggle provider reasoning
from task keywords or action outcomes. A future model profile may use its own
documented settings, but it must be compared on the same real-task matrix. If
a stronger model fixes arithmetic or geometry directly, no runtime
rule should accompany the model upgrade.

Every provider call still has a total deadline. A response that does not
produce one admitted tool call is never repaired by executing guessed prose,
and an already executed GUI action is never replayed by a model retry.

## Visual observation

Each normal turn receives the complete virtual desktop by default
(screen-first frames). A window crop is an explicit opt-in used only when a
verified foreground target makes it material; it is never the default. The
submitted image declares its exact pixel dimensions. Model coordinates use
those pixels and are mapped exactly once into the authoritative physical
region.

The current minimal live tool catalog does not expose the Legacy
`observe(image_region=...)` crop tool. Legacy observe/UIA/OCR/CV code remains
as compatibility debt, not as an automatic fallback for vNext failures.
Adding a model-selected crop or another observation primitive requires
cross-task completion evidence that the current observation is structurally
insufficient; it must not be introduced to repair one benchmark.

UIA, OCR, CV, and MCP are secondary facts. They may be used only when their
source, target window, frame, and expected value are explicit. They never
silently replace pixels, choose a target for the model, or turn a model error
into an infrastructure success.

## Actions and external turns

The minimal live surface is click, double click, drag/path/held-pointer,
scroll, literal text entry, one key chord, claim, and blocked. It contains no
save, draw, answer, chat, music, or application macro.

Dynamic interfaces that reveal a target after mouse-down use the same drag
lifecycle across new authoritative frames. Timeout, lost focus, cancellation,
or failure must release the physical button. Generic Legacy wait/watch
capabilities still exist, but they are not currently a model-facing vNext
primitive and must not be documented as one.

## Evaluation and change admission

The model verifies task completion on the real screen it sees; no separate
verification pass, evidence chain, or outcome oracle is built or required for
completion in this repository (independent-outcome oracle rigs belong to
another project). Unit and integration tests guard the mechanical layer only.

Diagnose every failure in this order:

```text
model arguments
-> canonical arguments
-> physical_attempt
-> operating-system/application state
```

Change only the first layer that diverges. Matching submitted and physical
coordinates rule out a coordinate-transport bug; matching submitted and
physical text rules out a keyboard-transport bug. A unit test or tool `ok`
is not a user-task pass.
