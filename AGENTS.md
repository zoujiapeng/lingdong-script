# Repository agent instructions

Before changing this repository, read `CLAUDE.md`. It is the current handoff
and points to the authoritative architecture, migration, and E2E documents.

## Highest principle: Model-first, machinery-minimal

The model is the system. It understands the goal, sees the real screen, picks
targets and coordinates, verifies its own results, decides when it is done,
and explains itself in natural language. The runtime exists only to give it
the real desktop and to perform the physical actions it chooses. Everything
that can reasonably be left to the model must be left to the model.

- Do not wrap the model in layers. No extra safety contracts, privacy gates,
  evidence chains, verification passes, capability checks, or permission
  handshakes whose only effect is to reject or delay what the model already
  decided correctly. "Sounds well-architected" is not a reason to add a layer.
- When a task fails, first assume the model is right and the machine lied:
  the screenshot was stale, the click was blocked, the window it saw was not
  the window it operated. Only fix the mechanical layer when the mechanical
  layer demonstrably diverged. Most failures are one of those two, not the
  model's reasoning.
- A small model may be weaker than a big one, but it is never improved by
  hard-coded rules. When the model is the bottleneck, change the model or
  accept the result; never add app recipes, classifiers, prompts, offsets,
  snapping, second verifiers, or extra roles to compensate.

Non-negotiable rules:

- Optimize for real uncertain desktop-task completion, not code volume,
  internal `done`, model prose, or benchmark-specific success.
- Keep one original goal, one semantic Visual Actor, one visual execution
  line, and a small generic action surface.
- The model sees the whole real screen by default (screen-first frames). A
  window crop is an explicit opt-in, never the default.
- Physical input goes where the model points on the screen, whatever window
  that is. Do not force the bound window to the foreground and do not reject
  a click because a guard thinks a different window "should" own it.
- Delete redundant machinery: permits, guards, evidence/audit chains, and
  verification passes that only reject or record without protecting a real
  physical boundary. When in doubt, delete.
- Keep only the mechanical boundaries the model cannot hold itself:
  single-writer physical input, key/pointer release cleanup, credential
  non-leakage, and irreversible external-action consent.
- Do not add app recipes, task classifiers, target snapping, coordinate
  offsets, answer correction, prompt teaching, or extra model roles to
  compensate for the current model.
- Diagnose failures along
  `model arguments -> canonical arguments -> physical_attempt -> external
  application state`, and change only the first layer that diverges.
- Prefer deletion and existing infrastructure. Admit a new owner only after
  real end-to-end completion on at least two different task families;
  independent-outcome oracle rigs are owned by another project, not this one.
- Spoken GUI requirements remain in the user's goal; do not restore frontend
  execution-policy controls or keyword routing.
- Work on `master`, the GitHub default and only active long-term branch. Treat
  all remaining named branches as read-only recovery/audit inputs; do not
  create another parallel product branch.
- Never stage, modify, rename, or delete the local untracked `CON`.
- Use explicit file paths when staging. Never use `git add .` in this mixed
  worktree.
- The model verifies completion on the real screen it sees. Do not build or
  require separate verification passes, evidence chains, or outcome oracles
  for task completion; unit and integration tests guard the mechanical layer
  only.
