# Sidecar Protocol (Phase 3, P3-001)

**Status:** v1, shipped with `postmortem/service.py`
**Consumers:** the ReaImGui panel (primary), any future companion window

The sidecar is a local process that executes engine calls on behalf of a thin
UI client. Transport is atomic JSON files in a jobs folder — the same
no-socket architecture as the Reaper Daemon bridge. The client writes job
files; the sidecar writes progress and result files; nobody opens a port.

This document is the UI-agnostic contract. A client that speaks it needs no
knowledge of Python, the bridge, or the model provider.

## App-data root

Platform-appropriate, never the repository folder:

| Platform | Root |
|---|---|
| macOS | `~/Library/Application Support/PostMortem` |
| Windows | `%APPDATA%\PostMortem` |
| Linux | `$XDG_DATA_HOME/postmortem` (fallback `~/.local/share/postmortem`) |

`POSTMORTEM_DATA_DIR` (environment or `~/.config/postmortem/config`) overrides.

Layout:

```text
PostMortem/
  jobs/inbox/        client writes job files here
  jobs/processing/   sidecar-owned, in-flight job lives here
  jobs/outbox/       results and progress files
  captures/          reserved for panel-owned stems (keep_wav previews)
  logs/service.log   internal error detail (never surfaced raw to results)
  feedback.jsonl     record_feedback stub (Phase 5 reads this)
  heartbeat.json     liveness: pid, service_version, updated_at, in_flight_job
  lock.json          single-instance lock
```

## File discipline

- **Atomic writes only.** Write `<name>.json.tmp`, then rename. The sidecar
  does the same for everything it writes. `.tmp` files are ignored.
- **One job per file** in `jobs/inbox/`, filename `<id>.json`. Use a
  timestamp-prefixed id (`pm-20260712T163000Z-a1b2c3.json`) so lexical order
  is arrival order.
- **The reply filename is derived from the inbox filename**, never from the
  job's `id` field. A malformed or hostile id cannot choose where its reply
  lands.
- Jobs are processed strictly one at a time, oldest filename first.

## Liveness

`heartbeat.json` is rewritten at least every ~2 seconds while the sidecar
runs, and immediately when a job starts or finishes (`in_flight_job` carries
the running job's id). Client rule: heartbeat stale by more than ~10 seconds
AND no `in_flight_job` → sidecar is dead, offer to relaunch. A heartbeat with
`in_flight_job` set can legitimately go quiet longer — captures block on
REAPER's render.

`lock.json` (`{pid, created_at}`) enforces a single instance. A lock whose
pid is dead is reclaimed automatically at startup.

## Job file

```json
{
  "id": "pm-20260712T163000Z-a1b2c3",
  "type": "track_check",
  "created_at": "2026-07-12T16:30:00Z",
  "payload": { "track": "Kick", "seconds": 10 }
}
```

`id` must match `[A-Za-z0-9._-]+`; anything else is replaced by the filename
stem. `type` must be one of the job types below.

## Progress file

While a job runs, `jobs/outbox/<stem>.progress.json` is atomically replaced
at each stage:

```json
{ "id": "pm-...", "stage": "capturing", "updated_at": "..." }
```

Stages by job type (the panel renders these as PRODUCT_PLAN §6.2's human
steps):

- `track_check`: `started` → `reading_track` → `capturing` → `measuring` →
  `diagnosing`
- `preview_fix`: `started` → `previewing`
- `commit_fix`: `started` → `committing`
- others: `started` only

The progress file is deleted when the final result is written. Result-file
existence, not progress absence, means done.

## Result file

`jobs/outbox/<stem>.json`, written exactly once per job:

```json
{
  "id": "pm-...",
  "ok": true,
  "result": { "...": "job-type specific, see below" },
  "error": null,
  "finished_at": "2026-07-12T16:30:41Z"
}
```

On failure `ok` is `false`, `result` is `null`, and `error` carries a stable
machine code plus a human message the panel can show verbatim:

```json
{ "code": "silence_gate", "message": "capture is essentially silent (...)" }
```

### Error codes

| Code | Meaning |
|---|---|
| `bad_job` | malformed job file or payload |
| `bad_adjustment` | requested preview/apply value is not numeric or the proposal cannot be adjusted |
| `unknown_job_type` | `type` not in the supported set |
| `track_not_resolved` | track name matched zero or several tracks |
| `isolation_gate` | capture is not verified isolated-track evidence |
| `silence_gate` | capture is dead air (payload `force: true` bypasses) |
| `not_actionable`, `bad_diagnosis`, `current_value_drift`, `stale_identity`, `track_identity_mismatch`, `current_value_mismatch`, ... | Phase 1/2 refusal vocabulary, passed through unchanged |
| `bridge_error` | Reaper Daemon call failed (message says why) |
| `provider_<category>` | model provider failure |
| `cancelled` | cancelled at a safe stage boundary |
| `interrupted` | sidecar restarted mid-job; the job was NOT re-executed |
| `nothing_to_cancel` | cancel target not queued or running |
| `internal_error` | unexpected failure; detail in `logs/service.log` |

Refusals are product behavior, not failures to hide: the panel renders the
message, hedges intact.

## Job types

### `get_status`

Payload: none. Result:

```json
{
  "service_version": "0.1.0",
  "data_root": "/Users/x/Library/Application Support/PostMortem",
  "bridge_ok": true,
  "bridge_status": "bridge alive (...)",
  "model": "MiniMax-M3"
}
```

### `track_check`

Payload: `{ "track": "Kick", "seconds": 10, "force": false }` (`seconds`
1-600, default 10; `force` bypasses only the silence gate, never the
isolation gate). Result:

```json
{ "track": "Kick", "diagnosis": { "schema_version": 1, "finding": {...}, "proposal": {...} }, "payload": {...} }
```

`diagnosis` is a complete `DiagnosisResult` (see STRUCTURED_RESULTS.md) —
the panel feeds it back verbatim as `payload.diagnosis` for preview/commit.

`payload` is the exact measured Track Check document the provider saw
(project/track/fx_chain/routing/capture/audio). It exists so a thin client
can render measured evidence — `finding.evidence_refs[].path` values such as
`audio.sample_peak_db` resolve against this document — without re-deriving
any measurement. Clients must treat it as display data, never as something
to recompute or mutate.

### `preview_fix`

Payload: `{ "diagnosis": {...}, "seconds": 10, "keep_wav": false }`.
`proposed_value` is optional. When present, the
sidecar copies the diagnosis, clamps the requested numeric value to the
engine-owned adjustment bounds, and then runs a fresh preview.
Runs the Phase 2 preview loop: fresh revalidation, snapshot, temporary
apply, candidate capture, ALWAYS restore. Result is the preview report
(`restored`, `verification`, deltas, optional `wav_paths` when `keep_wav`).
Numeric proposals also carry `adjustment` with engine-owned `minimum`,
`maximum`, `step`, `value`, and `unit` fields. Thin clients must use these
bounds for adjustment controls instead of duplicating validator move limits.
Boolean FX-bypass proposals carry `adjustment: null`.

### `commit_fix`

Payload: `{ "diagnosis": {...} }`. The optional
`proposed_value` uses the same engine-side clamping as `preview_fix`, so an
adjusted preview can be applied without the client rewriting the diagnosis.
Explicit apply uses fresh re-verification and exactly one named undo point.
Result carries `committed` and `undo_point`.

### `cancel_job`

Payload: `{ "target_id": "pm-..." }`. Works two ways:

- Target still queued in the inbox → removed, its result written as
  `cancelled`.
- Target currently running → the sidecar consumes the cancel at the next
  safe stage boundary. Boundaries exist between every `track_check` stage
  up to the model call, and before `preview_fix`/`commit_fix` touch the
  bridge. Once a preview's temporary change is applied, cancellation is
  ignored until the automatic restore has completed — restore-always
  outranks responsiveness.

If the target already finished, the cancel resolves `nothing_to_cancel`.

### `record_feedback`

Payload: any non-empty object (the panel sends
`{ "kind": "not_helpful", "track": "...", "diagnosis_summary": "..." }`).
Appended verbatim with a timestamp to `feedback.jsonl`. Phase 5's history
work consumes this file; nothing else reads it yet.

## Crash behavior

- A job found in `jobs/processing/` at startup gets an `interrupted` error
  result and is NEVER re-executed — `preview_fix` may have mutated the
  project, and the bridge's own startup recovery is the restore authority.
- Killing the sidecar mid-preview leaves the bridge-side preview state; the
  bridge restores it (cancel semantics) on its next startup pass, exactly as
  live-verified in Phase 2 (P2-005).

## Running it

```text
.venv/bin/python -m postmortem.service            # serve (lock + loop)
.venv/bin/python -m postmortem.service --once     # drain pending jobs, exit
.venv/bin/python -m postmortem.service --data-dir /tmp/pm-test --once
```

The packaged build (P3-007) ships this as the `postmortem-sidecar` binary;
the panel launches it on demand when the heartbeat is stale (P3-003).
