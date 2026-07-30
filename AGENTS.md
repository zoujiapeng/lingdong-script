# Repository agent instructions

Before changing this repository, read `CLAUDE.md`. It is the current handoff
and points to the authoritative architecture, migration, and E2E documents.

Non-negotiable rules:

- Optimize for real uncertain desktop-task completion, not code volume,
  internal `done`, model prose, or benchmark-specific success.
- Keep one original goal, one semantic Visual Actor, one visual execution
  line, and a small generic action surface.
- Do not add app recipes, task classifiers, target snapping, coordinate
  offsets, answer correction, prompt teaching, or extra model roles to
  compensate for the current model.
- Diagnose failures along
  `model arguments -> canonical arguments -> physical_attempt -> external
  application state`, and change only the first layer that diverges.
- Prefer deletion and existing infrastructure. Admit a new owner only after
  external-oracle E2E on at least two different task families.
- Spoken GUI requirements remain in the user's goal; do not restore frontend
  execution-policy controls or keyword routing.
- Work on `master`, the GitHub default and only active long-term branch. Treat
  all remaining named branches as read-only recovery/audit inputs; do not
  create another parallel product branch.
- Never stage, modify, rename, or delete the local untracked `CON`.
- Use explicit file paths when staging. Never use `git add .` in this mixed
  worktree.
- Runtime changes require visible Electron E2E with independent outcome
  evidence. Unit and integration tests are necessary but not a user-task pass.
