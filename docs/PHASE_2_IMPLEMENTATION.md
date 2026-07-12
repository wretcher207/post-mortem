# Phase 2 Implementation Backlog: Verified Fix Preview

**Status:** Ready for implementation
**Date:** 2026-07-11
**Target:** CLI-only proof of the hero promise (PRODUCT_PLAN §17)
**Depends on:** Phase 1 complete (model gate closed 2026-07-11, MiniMax M3);
Reaper Daemon `v3.8.3` or later

## 1. Outcome

Phase 2 turns a validated `DiagnosisResult.proposal` into a heard and measured
before/after comparison, without ever leaving the user's project in a mutated
state. At the end of this phase, this loop works in the terminal:

```text
postmortem "Kick" --format json > diagnosis.json
postmortem preview diagnosis.json     # snapshot → baseline capture → temp
                                      # change → candidate capture → restore →
                                      # before/after proof report
postmortem commit diagnosis.json      # explicit, creates ONE named undo point
```

(Design note, settled during P2-004: `preview` always restores and deletes the
bridge preview state, so `commit` takes the diagnosis file, re-verifies
identities and current values fresh, and runs `preview_change` +
`commit_preview` back-to-back. A token from a cancelled preview has nothing
left to commit.)

`preview` always ends with the project back in its original state. `commit` is
the only operation that leaves a change behind, and it must be explicit.

Relationship to SPEC.md: the v1 freeze ("Automatic fix application" is not in
v1) is intact — v1 shipped diagnosis-only. Phase 2 is the sanctioned successor
line from `docs/PRODUCT_PLAN.md` (locked 2026-07-10), not a reopening of v1.

## 2. Scope boundary

### Included

- Daemon preview lifecycle: snapshot, temporary apply, restore, commit, cancel.
- Crash and restart recovery: an interrupted preview restores the baseline.
- Fresh-scan revalidation: a stale diagnosis refuses to preview or apply.
- Baseline/candidate capture using the existing `capture_track_audio`.
- Deterministic goal/guardrail evaluator (`verification.py`).
- CLI `preview` and `commit` commands and the proof report.

### Not included

- ReaImGui panel, A/B transport controls, loudness-match monitoring (Phase 3;
  the CLI report states measured deltas instead).
- New capture source modes (`track_post_fx_pre_parent`, `folder_or_bus_output`,
  `via_master`) and true isolation for item-based tracks — separate work, not
  a Phase 2 gate.
- Mix Check, session graph, history/SQLite, licensing, hosted credits.
- Automation writing, plug-in add/remove, routing changes, item edits.

### Supported preview operations (exactly the Phase 1 proposal set)

- `set_track_volume`
- `set_track_pan`
- `set_fx_param`
- `set_fx_bypass`

## 3. Repository ownership

| Workstream | Repository | Primary files |
|---|---|---|
| Track state snapshot/restore | `reaper-daemon` | `bridge/reaper_agent_bridge.lua`, `bridge/command_schema.md`, `tests/` |
| Preview lifecycle commands | `reaper-daemon` | same |
| Crash/restart recovery | `reaper-daemon` | same (defer-loop startup re-queue) |
| Guardrail evaluator | `post-mortem` | `postmortem/verification.py`, `tests/test_verification.py` |
| Preview orchestration | `post-mortem` | `postmortem/preview.py`, `postmortem/bridge.py`, `tests/` |
| CLI commands | `post-mortem` | `postmortem/cli.py`, `tests/test_cli.py` |
| Docs | both | README, command schema, this backlog |

## 4. Delivery sequence

### P2-001 — Track state snapshot and restore commands

**Repository:** `reaper-daemon`
**Priority:** Blocking
**Status:** Not started

Add `snapshot_track_state` and `restore_track_state`. The snapshot covers only
what the four supported operations can mutate: track volume, track pan, per-FX
enabled state, and individual FX parameter values (by FX GUID + parameter
index).

Rules:

1. The snapshot is written to a state file on disk BEFORE any mutation happens
   (same pattern as the render-settings crash mitigation in SPEC.md Risks).
2. The snapshot records track GUID, FX GUIDs, parameter indices and values,
   and a `created_at` timestamp.
3. `restore_track_state` verifies GUIDs still resolve before writing values;
   a missing track/FX returns a typed error and restores whatever still
   resolves, reporting exactly what it could not restore.
4. Restore never touches state the snapshot does not contain.

Acceptance criteria:

- Snapshot/restore round-trips volume, pan, FX enabled, and one FX parameter.
- A snapshot for a deleted track fails closed with a typed error.
- State file survives bridge restart and is discoverable by the startup scan.
- Existing command semantics unchanged; additive only.

### P2-002 — Preview lifecycle commands

**Repository:** `reaper-daemon`
**Priority:** Blocking
**Depends on:** P2-001
**Status:** Not started

Add `preview_change`, `commit_preview`, `cancel_preview`.

Rules:

1. `preview_change` takes the proposal target (track GUID, optional FX GUID +
   index + scope + name, optional parameter index + name), the proposed value,
   and returns a random `preview_token`. It snapshots first (P2-001), then
   applies the temporary change.
2. Identity verification happens inside the command: every supplied GUID,
   index, and name must still describe the same object at apply time, or the
   command refuses with a typed `STALE_IDENTITY` error and mutates nothing.
3. Only one active preview may exist per project. A second `preview_change`
   refuses until the first is committed or cancelled.
4. `cancel_preview` restores the snapshot and deletes the preview state.
5. `commit_preview` wraps the final value in exactly one named undo point
   (`Undo_BeginBlock2` / `Undo_EndBlock2`, name carries the track and change),
   then deletes the preview state. The user's undo history gains ONE entry.
6. Preview state (token, snapshot path, created_at) persists to disk. On
   bridge startup, a leftover preview is restored (cancel semantics) and the
   event is reported in the startup status. Previews expire after 30 minutes;
   an expired preview restores on the next defer pass.
7. No preview operation can delete, add, or reorder anything.

Acceptance criteria:

- Kill REAPER (or the bridge) mid-preview; on restart the baseline is restored
  and status reports the recovery.
- Commit produces exactly one undo point; REAPER's undo list shows one named
  entry and undoing it restores the pre-preview value.
- A preview against a renamed/moved/removed FX refuses with `STALE_IDENTITY`.
- Double-preview refuses; cancel after commit fails typed; token reuse fails.
- Lua tests cover command validation; Python fake-bridge tests cover every
  state transition.

### P2-003 — Deterministic goal and guardrail evaluator

**Repository:** `post-mortem`
**Priority:** Blocking (parallel with P2-001/002)
**Status:** Not started

Create `postmortem/verification.py`. Pure functions over two `TrackStats` plus
the validated proposal; no bridge calls, no model calls.

Inputs: baseline stats, candidate stats, `proposal.goal`,
`proposal.expected_direction[]`.

Outputs a `VerificationResult` (extend `schemas.py`) containing:

- Per-metric deltas for every supported metric present in both captures.
- Goal evaluation: `moved_as_intended | moved_insufficiently | moved_against |
  not_measured`, with a noise floor below which change is "not proven"
  (initial: 0.5 dB for dB metrics, 0.05 for correlation, 0.5 LU for LU).
- Guardrails (each `pass | warn | fail`):
  - New clipping: candidate sample peak or true peak rises above `-0.3 dBFS`
    when the baseline was below it → fail.
  - Loudness shift: |Δ integrated LUFS| > 3 LU → warn (comparison is unfair;
    note that louder usually sounds better).
  - Phase: stereo correlation drops by more than 0.25, or goes negative when
    the baseline was positive → warn.
  - Stereo balance: |Δ balance_db| > 3 dB → warn.
  - Silence: candidate silence_fraction ≥ 0.75 → verification unavailable.
- One of the three locked outcome sentences from PRODUCT_PLAN §6.3, chosen
  deterministically from the above.

These thresholds are initial conservative values, expected to be tuned; they
live in one constants block with the Phase 1 move limits.

Honesty rules (hard):

- Never the word "better". The report says what moved, relative to the stated
  goal, and what risks appeared.
- Metrics not present in both captures are `not_measured`, never inferred.
- A null in either capture is "not measured", never zero.

Acceptance criteria:

- Every guardrail has positive and negative unit tests.
- Goal evaluation covers intended / insufficient / against / unmeasured.
- The outcome sentence is a pure function of the structured result.
- No code path can claim improvement without the goal metric moving past the
  noise floor in the intended direction.

### P2-004 — Preview orchestration and CLI

**Repository:** `post-mortem`
**Priority:** High
**Depends on:** P2-001, P2-002, P2-003
**Status:** Not started

Create `postmortem/preview.py` orchestrating the PRODUCT_PLAN §6.3 sequence:

1. Load the `DiagnosisResult` (from `--format json` output or a fresh run).
2. Refuse anything but an actionable validated proposal.
3. Run a FRESH `scan_fx` + `get_track_routing`; re-run `validate_proposal`
   against the fresh payload. Any mismatch refuses with the Phase 1 rejection
   vocabulary (`track_identity_mismatch`, `fx_identity_mismatch`, ...).
4. Capture baseline (existing `capture_track_audio` + WAV verification: mtime
   newer than the command's `created_at`, nonzero size — the Phase 1 rule).
5. `preview_change`, capture candidate with the same verification, then
   `cancel_preview` ALWAYS — including on exceptions (try/finally). The
   preview token is printed only after the restore succeeded.
6. Evaluate with `verification.py`, print the proof report (text and
   `--format json` from one structured result, same pattern as Phase 1).
7. `postmortem commit <diagnosis.json>` re-verifies identities and current
   values against a fresh scan, then runs `preview_change` + `commit_preview`
   back-to-back. Nothing else ever commits.

Acceptance criteria:

- A thrown exception anywhere between apply and restore still restores (fake
  bridge test kills each step).
- The report renders from the structured `VerificationResult`; text and JSON
  agree.
- Stale diagnosis (edited FX chain between diagnose and preview) refuses
  before any mutation.
- `--payload-only`, existing diagnose flags, and exit codes unchanged.

### P2-005 — Live REAPER verification protocol

**Repository:** both
**Priority:** High
**Depends on:** P2-001 through P2-004
**Status:** Not started

Scripted live pass on a real project (the Phase 1 A/B rig: ReaEQ boost is
proven detectable):

1. Preview a volume change on a real track; hear/measure both captures; verify
   auto-restore via `get_track_routing` equality.
2. Kill the bridge mid-preview; reload; verify baseline restoration and
   startup report.
3. Commit; verify exactly one undo point and its name in REAPER's undo list.
4. Rename the target FX between diagnose and preview; verify refusal.

Record results in `HANDOFF.md` and the bridge repo's verification notes.
Remember the repo rule: a capture is verified only by checking the WAV file
(mtime + nonzero size), and Lua edits need a REAPER reload before live claims.

### P2-006 — Documentation

**Repository:** both
**Priority:** Medium
**Depends on:** all prior
**Status:** Not started

- README preview/commit examples with honest language (preview restores;
  commit is explicit; one undo point).
- Command schema entries for the five new bridge commands.
- STRUCTURED_RESULTS.md gains the `VerificationResult` contract.
- PRODUCT_PLAN Phase 2 exit criteria checked off with evidence links.

## 5. Definition of done

Phase 2 is complete only when:

1. All existing tests pass in both repositories; new state-transition and
   guardrail tests pass across the Phase 1 CI matrix.
2. Killing the bridge or REAPER during a preview restores the baseline in
   live recovery testing (not just unit tests).
3. Apply creates exactly one named undo point, verified in REAPER's undo list.
4. A stale diagnosis refuses to preview or apply after any FX-chain change.
5. No preview operation supports destructive actions (nothing added, removed,
   or reordered by any code path).
6. The proof report never claims improvement; it reports goal movement and
   guardrails from measured values only.
7. The whole loop runs in the terminal against a real project.

## 6. Recommended pull request sequence

1. **Reaper Daemon:** snapshot/restore commands + state file + tests.
2. **Reaper Daemon:** preview lifecycle + crash recovery + tests (tag + bump
   `@version` + `index.xml` in lockstep per that repo's ship rules).
3. **Post Mortem:** `verification.py` + schemas + unit tests.
4. **Post Mortem:** preview orchestration + CLI + fake-bridge tests.
5. **Both:** live verification notes + docs.

Keep the daemon PRs small; the bridge is in daily use and a broken defer loop
takes the whole toolchain down with it.
