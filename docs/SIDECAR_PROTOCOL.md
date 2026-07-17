# Sidecar Protocol v1

**Status:** frozen and shipped with Post Mortem `0.1.0`
**Implementation:** `postmortem/service.py`
**Consumers:** the ReaImGui panel (primary), any future companion window

Protocol v1 is the shipped compatibility boundary. A v1 client must reject a
newer result schema it does not understand. Additive result fields may appear
inside a v1 job type, but job names, stable error codes, file ownership, and
restore behavior cannot change without a protocol-version update.

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
  logs/service.log   internal error detail (never surfaced raw to results)
  feedback.jsonl     record_feedback stub (Phase 5 reads this)
  heartbeat.json     liveness: pid, service_version, updated_at, in_flight_job
  lock.d/            single-instance lock (atomic mkdir, pid-liveness checked)
  mcp-receipt.json   one-time measured Track Check receipt
  mcp-handoff.json   sidecar-owned diagnosis returned by an MCP client
```

After `analyze_track` passes Post Mortem's isolation gate, Reaper Daemon submits
`record_mcp_measurement` with an unguessable receipt id. For first-run
onboarding, the client model calls `complete_postmortem_onboarding` with its
diagnosis. The sidecar alone verifies that the one-time receipt is fresh,
single-track, 10 seconds, and matches the diagnosis track before atomically
writing `mcp-handoff.json`. The panel receives only the sidecar's `get_status`
readiness verdict. A missing, stale, reused, comparison, or non-10-second
receipt cannot complete onboarding.

## File discipline

- **Atomic writes only.** Write `<name>.json.tmp`, then rename. The sidecar
  does the same for everything it writes. `.tmp` files are ignored.
- **Exception:** `feedback.jsonl` is an append-only log written with
  `O_APPEND`. Each entry is written as a single `os.write()` syscall,
  which prevents interleaving from concurrent appends. A crash or power
  loss may still leave a partial final line; readers must skip lines
  that fail JSON parsing.
- **One job per file** in `jobs/inbox/`, filename `<id>.json`. Use a
  timestamp-prefixed id (`pm-20260712T163000Z-a1b2c3.json`) so lexical order
  is arrival order.
- **The reply filename is derived from the inbox filename**, never from the
  job's `id` field. A malformed or hostile id cannot choose where its reply
  lands.
- Jobs are processed strictly one at a time, oldest filename first.

## Liveness

`heartbeat.json` is rewritten at least every ~2 seconds while the sidecar
runs, immediately when a job starts or finishes, and at every stage boundary
(`started`, `reading_track`, `capturing`, `measuring`, `diagnosing`, etc.).
The `last_progress_at` field records when the in-flight job last advanced
stage. Client rule: heartbeat stale by more than ~10 seconds AND no
`in_flight_job` → sidecar is dead, offer to relaunch. A heartbeat with
`in_flight_job` set can legitimately go quiet longer, as captures block on
REAPER's render, but only up to a bounded window: if the heartbeat is stale
past `max(120, active_job_seconds + 120)` seconds, the sidecar is dead even
if `in_flight_job` is set. The client should relaunch it; the next startup's
`sweep_interrupted()` will produce an `interrupted` result for the stranded
job.

`lock.d/` (atomic `os.mkdir` with a `pid` file inside) enforces a single
instance. A lock whose pid is dead is reclaimed automatically at startup.

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
- `validate_provider`: `started` → `validating_access`
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
machine code, the engine detail, and an engine-owned recovery object:

```json
{
  "code": "silence_gate",
  "message": "capture is essentially silent (...)",
  "recovery": {
    "explanation": "The capture came back essentially silent.",
    "action": "Move the edit cursor to a section where the track is playing, then check it again.",
    "copy_diagnostics": false
  }
}
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
message and recovery fields verbatim, hedges intact. Every stable error code
has a specific recovery. Unknown codes keep the typed code and set
`copy_diagnostics: true` rather than inventing an explanation.

## Job types

### `get_status`

Payload: none. Result:

```json
{
  "service_version": "0.1.0",
  "data_root": "/Users/x/Library/Application Support/PostMortem",
  "bridge_ok": true,
  "bridge_status": "bridge alive (...)",
  "capture_preflight_detail": null,
  "capture_preflight": {
    "capture_allowed": true,
    "blockers": [],
    "warnings": [],
    "risk_gate": {
      "allow_risk_level_3": true,
      "requires_restart_to_change": true
    },
    "sws_installed": true,
    "render_autoclose": true,
    "target": null
  },
  "provider_configured": true,
  "provider": "Anthropic",
  "setup": {
    "ready": true,
    "provider_configured": true,
    "checks": {
      "bridge_running": true,
      "capture_enabled": true,
      "panel_registered": true
    },
    "recovery": null,
    "detail": null
  },
  "model": "MiniMax-M3",
  "mcp_handoff": { "ready": false }
}
```

`capture_preflight` is the Reaper Daemon `get_capture_preflight` reply. It is
`null` when the bridge is unavailable or that command fails.
`capture_preflight_detail` is normally `null`; when bridge liveness succeeds
but preflight fails, it carries that typed bridge error while `bridge_ok`
remains true. `provider_configured` means the local endpoint, key, and model
can be constructed; it does not replace the live validation required during
first-run onboarding.
`provider` is `Anthropic` for the official endpoint and the configured hostname
for a compatible third-party endpoint. It never contains a key, path, or URL
query.
`setup` is the engine-owned onboarding verdict. When `ready` is false,
`recovery` carries `{code, message, action, primary_action}` for `bridge_dead`,
`preflight_missing`, `preflight_invalid`, `capture_gated`, `render_hang_risk`,
`capture_blocked`, or `panel_not_registered`. The panel supplies only its
directly observed "Found REAPER" and "panel registered" checks; it does not
recreate bridge logic.
`primary_action` is an engine-owned `{label, job_type, payload}` descriptor;
`manual_steps` is present when the client should render a manual checklist.
The panel renders and dispatches these fields generically.
`mcp_handoff` always includes `ready`. The client supplies
`payload.mcp_started_at` while polling. Only a structurally valid handoff newer
than that timestamp returns `ready: true` with
`{tracks, diagnosis_summary, delivered_at}`.

### `enable_capture`

Payload: none. Atomically sets only `allow_risk_level_3: true` in Reaper
Daemon's existing `bridge_config.json`, preserving every other setting.
Result:

```json
{
  "enabled": true,
  "restart_required": true,
  "config_path": "/path/to/reaper-daemon/bridge/bridge_config.json"
}
```

The bridge reads this flag once at REAPER startup. Clients must instruct the
user to restart REAPER and test again; there is no reload operation.

### `validate_provider`

Payload: `{ "api_key": "..." }`, or `{}` to validate an already configured
key. Executes one live provider call with `max_tokens: 1`. A supplied key is
written atomically to the matching provider config field only after that call
succeeds. The key is never included in the result or service log. Result:

```json
{ "validated": true, "model": "MiniMax-M3" }
```

Provider failures use the existing `provider_<category>` error codes. The
processed job file is deleted whether validation succeeds or fails.

### `record_mcp_measurement`

Payload: `{ "receipt_id": "64-random-hex-characters", "tracks": ["Kick"],
"seconds": 10 }`. Reaper Daemon submits this only after verified isolated
measurements reach the MCP client. The sidecar stores it as a short-lived,
one-time receipt.

### `record_mcp_handoff`

Payload:

```json
{
  "receipt_id": "matching-measurement-receipt",
  "tracks": ["Kick"],
  "diagnosis_summary": "The kick has a measured buildup around 200 Hz."
}
```

This is submitted by Reaper Daemon's `complete_postmortem_onboarding` MCP tool,
not by the panel. It requires exactly one non-empty track name and a diagnosis
summary of at least 20 characters. The sidecar consumes a fresh matching
`mcp-receipt.json`; a caller-controlled duration is not accepted as proof. It
atomically stores the result and returns
`{tracks, diagnosis_summary, delivered_at}`. The panel polls
`get_status`; it never reads or validates a second file protocol itself.

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

Payload: `{ "diagnosis": {...}, "seconds": N }`. The optional
`proposed_value` uses the same engine-side clamping as `preview_fix`, so an
adjusted preview can be applied without the client rewriting the diagnosis.
`seconds` controls the baseline capture used for full `validate_proposal`
revalidation (same gate as `preview_fix`). Explicit apply uses fresh
re-verification and exactly one named undo point.
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
