# Phase 1 model benchmark — 2026-07-11

This benchmark ran every model against the same 25-case de-identified corpus in
`tests/fixtures/diagnoses/corpus.json`. Requests used adaptive reasoning and the
runtime `max_tokens` value of 16,384. Every per-case result file is a validated
`DiagnosisResult`; no raw audio or private project data was sent. The MiniMax
directory also records two sanitized capture-retry errors in a sidecar file.

The manifests record the exact provider model IDs used. The providers did not
return an immutable serving-infrastructure revision hash, so the captured
outputs are fixed evidence but a future rerun may not be byte-for-byte identical.

## Result

| Model | Useful primary finding | Full contract pass | Operational notes |
|---|---:|---:|---|
| MiniMax M3 | 12/25 (48%) | 3/25 (12%) | Two first attempts needed a retry after an overlong `rejection_reason` and empty repair response. |
| DeepSeek V4 Flash | 9/25 (36%) | 2/25 (8%) | Completed all cases without transport failure. |
| DeepSeek V4 Pro | 8/25 (32%) | 0/25 (0%) | Completed all cases without transport failure. |

The primary-finding score checks required concepts, forbidden claims, evidence
categories, and the confidence ceiling while ignoring proposal-only failures.
The full-contract score also requires the proposal assertions and deterministic
validation to pass. No model meets the product plan's 80% primary-finding gate,
and no default-model change is justified by this run.

MiniMax passed `bass_guitar_hot_output`, `stereo_pad_left_heavy`, and
`drum_bus_near_clipping`. DeepSeek V4 Flash passed `lead_vocal_send_context` and
`pan_change_not_supported`. DeepSeek V4 Pro passed no complete case.

## Failure patterns

| Failure category | Flash | Pro | MiniMax M3 |
|---|---:|---:|---:|
| Missing required evidence categories | 14 | 15 | 12 |
| Missing required concepts | 10 | 11 | 8 |
| Unsupported proposal goal | 12 | 10 | 5 |
| Invalid structured result after repair | 4 | 5 | 8 |
| Confidence above the case maximum | 6 | 7 | 3 |
| Model-supplied `rejection_reason` | 2 | 1 | 3 |
| Evidence path missing or null | 0 | 2 | 4 |
| Conservative move limit exceeded | 0 | 0 | 2 |
| Forbidden single-track masking claim | 0 | 0 | 1 |

The schema and deterministic validator failed closed on structured values.
Unsupported, stale, over-large, or unverifiable structured proposals were
converted to `operation: "none"`. The baseline also exposed a prose bypass: one
MiniMax pan proposal used an allowed structured value but recommended a larger
second move in `proposal.reason`. Post-benchmark validation now replaces
accepted actionable reasons with deterministic text for track volume, pan, FX
parameters, and bypass. The original snapshot remains unchanged as evidence of
the defect.

## Decision

- Do not select DeepSeek V4 Pro as the quality profile; it underperformed Flash
  on this contract.
- Do not select MiniMax M3 yet despite its leading score. Its 48% primary-finding
  rate is far below the gate, it made one forbidden masking claim, and it
  required two capture retries.
- Keep the current model choice unchanged until a model-facing contract
  iteration is evaluated against new snapshots.

The next model-facing iteration should reserve `rejection_reason` for deterministic code,
make supported metric names harder to ignore, give exact evidence-path examples,
and state corpus-aligned confidence ceilings for silent or evidence-poor
captures. The original snapshots must remain unchanged as the comparison
baseline.
