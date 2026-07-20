# Codex repository entrypoint

Read these before changing the WinPilot GUI Agent:

1. `README.md`
2. `CLAUDE.md`
3. `docs/visual-runtime-principles.md`
4. `docs/GUI_AGENT_ARCHITECTURE_AUDIT.md`
5. `docs/testing.md`
6. `docs/vnext/README.md` when working on the vNext migration branch

## Non-negotiable architecture

- There is exactly one model role allowed to propose GUI mutation: the Visual Actor.
- There is exactly one desktop execution chain: `FrameAuthority -> Controller -> RawDevice/DesktopActionQueue`.
- Mission completion truth must be rebuildable from accepted mission events and immutable run-scoped evidence; do not create a second mutable completion ledger.
- A low-frequency, read-only Task Profiler may propose execution depth and cognitive services. It must not output GUI steps, tools, coordinates, application workflows, or a dedicated architecture for a task label.
- Runtime identities such as mission revision, active obligation, frame id, evidence ids, and broker projection ids are bound by Runtime envelopes. Do not require the model to copy or invent them.
- Task contracts and obligations describe desired states, acceptance criteria, dependencies, artifacts, broad capabilities, and forbidden effects. They do not describe unseen GUI click sequences.
- Capability Broker decisions come from accepted capabilities and current runtime state, not application names, benchmark identities, task phrases, or remembered coordinates.
- Controller code may validate, reject, map, execute, and report facts. It must not replace the model-selected semantic target or choose recovery tactics.
- Planner, Verifier, Task Profiler, and Visual Inspector are read-only roles with separate contexts and schemas. They never receive GUI mutation tools.
- The same underlying model may serve different roles, but each role must use an isolated request context, system contract, output schema, tool allowlist, and budget.
- Do not add application-, benchmark-, phrase-, layout-, or coordinate-specific GUI rules.
- Do not create duplicate frame owners, pointer owners, action parsers, ledgers, planners, recovery authorities, or completion authorities.

## Change discipline

- Inspect existing owners and call sites before editing.
- Prefer behavior-preserving shadow seams before enforcement.
- Keep architecture migration, broad renames, and legacy deletion in separate changes.
- Every migration slice must state the old responsibility it will eventually remove and the evidence required for deletion.
- Do not rewrite `FastGuiController`, `FrameAuthority`, `RawDeviceExecutor`, or `DesktopActionQueue` unless the task explicitly targets a proven defect in that owner.
- `codex/vnext-agent-core` is a temporary integration branch. `master` remains the canonical long-term product branch; use tags for durable backups and remove or archive migration branches after merge or abandonment.

## Verification

Run the smallest relevant tests first, then the repository-standard checks:

```bash
python -m pytest python/winpilot_home/tests -q
python -m pytest tests -q
npm test
npm run typecheck
npm run build
```

Documentation-only changes must at least pass `git diff --check` and link review. Client E2E must follow `docs/testing.md`; do not claim a pass without the required independent oracle.
