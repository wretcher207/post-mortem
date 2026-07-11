# Model contract v2 benchmark — 2026-07-11

This iteration reran DeepSeek V4 Flash, DeepSeek V4 Pro, and MiniMax M3 against
the same 25-case de-identified corpus used by the Phase 1 baseline. The original
snapshots remain unchanged. New requests used the stricter
`ProviderDiagnosisResult` schema and the hardened deterministic validator.

The provider-facing contract now excludes validator-owned `rejection_reason`,
enumerates supported goal and metric names, documents exact non-null evidence
leaf paths, and explicitly caps silent or unverified captures. Runtime
validation independently enforces those capture/confidence rules and removes
single-track cross-track claims. An empty repair after an invalid structured
response is classified as `invalid_structured_response` and fails closed.

The manifests record the provider model IDs used. None of the providers exposed
an immutable serving-infrastructure revision, so the snapshots are fixed
evidence but future reruns may differ.

## Result

| Model | Useful finding baseline | Useful finding v2 | Full contract baseline | Full contract v2 |
|---|---:|---:|---:|---:|
| MiniMax M3 | 12/25 (48%) | 19/25 (76%) | 3/25 (12%) | 15/25 (60%) |
| DeepSeek V4 Pro | 8/25 (32%) | 12/25 (48%) | 0/25 (0%) | 8/25 (32%) |
| DeepSeek V4 Flash | 9/25 (36%) | 11/25 (44%) | 2/25 (8%) | 8/25 (32%) |

All three models improved. MiniMax M3 remains the strongest candidate and
completed all 25 cases without the capture retries required by its baseline
run. It is still one useful finding short of the 20/25 (80%) selection gate, so
this result does not authorize a default-model change.

## Remaining failures

Counts below are failure occurrences; one case can contribute more than one.

| Failure category | Flash | Pro | MiniMax M3 |
|---|---:|---:|---:|
| Missing required concepts | 9 | 10 | 4 |
| Missing required evidence categories | 7 | 11 | 4 |
| Confidence above case ceiling | 8 | 7 | 2 |
| Deterministic proposal rejection | 8 | 6 | 7 |
| Invalid structured result after repair | 2 | 2 | 2 |
| Cross-track claim removed at runtime | 0 | 1 | 1 |

MiniMax's six remaining finding failures are concentrated in four areas:
reduced-dynamics/FX evidence, two confidence ceilings, receive-routing context,
unknown FX display mapping, and an explicit no-supported-move conclusion. Its
full-contract failures additionally include two over-limit moves, two invalid
evidence paths, and two invalid structured results.

## Decision

- Keep the configured default model unchanged because no candidate reached the
  80% useful-finding gate.
- Continue contract hardening with the MiniMax failure set as the primary
  target, while preserving the same corpus and baseline snapshots.
- The next iteration should improve routing/FX evidence selection, encode
  context-specific confidence ceilings, and make `operation: none` evidence
  cleanup deterministic without weakening actionable proposal validation.
