# Model contract v3 benchmark — 2026-07-11

This iteration reran MiniMax M3 against the same 25-case de-identified corpus
used by the Phase 1 baseline and the contract v2 rerun. The v2 snapshots remain
unchanged. The DeepSeek models were not rerun: the selection gate needs one
passing candidate, and MiniMax M3 was the only model within reach after v2.

## Contract changes in v3

The v2 failure analysis found that several MiniMax failures were not diagnosis
failures but contract-communication gaps:

- **Vocabulary false positives.** The runtime cross-track claim detector
  matches bare relationship verbs (dominate, bury, cover, smother, ...). v2
  responses used them metaphorically ("a buffer dominated by silence", "the
  100 Hz band dominates the spectrum") and were replaced with safe
  low-confidence findings. v3 bans the vocabulary outright in the shared
  honesty contract. The validator was not loosened.
- **Unstated schema limits.** Two v2 cases failed twice on structured-output
  validation. The model was never told the schema's string length limits; v3
  states them and demands concision.
- **Unstated confidence ceilings.** v3 adds enforced ceilings: an FX-caused
  finding without reported parameter values caps at medium; unmeasured
  receive/parent-bus context caps at low and must cite routing evidence.
- **Unstated evidence and move rules.** v3 requires dynamics + fx evidence for
  overcompression findings, top-level-key evidence paths, explicit
  "no clear problem / no safe move" language for benign `operation: none`
  results, unverified-display-mapping handling, and states the numeric move
  limits the validator enforces.

## Result

| Model | Useful finding v2 | Useful finding v3 | Full contract v2 | Full contract v3 |
|---|---:|---:|---:|---:|
| MiniMax M3 | 19/25 (76%) | **20/25 (80%)** | 15/25 (60%) | **19/25 (76%)** |

**The 20/25 (80%) useful-finding selection gate is met.** This is exactly at
the threshold, not above it; treat the margin as zero. All 25 cases completed
in one pass with no capture retries.

## Remaining failures (v3)

| Case | Failure |
|---|---|
| snare_healthy_transient | confidence high exceeds maximum medium |
| backing_vocal_healthy_stereo | confidence high exceeds maximum medium |
| synth_chorus_bypass_candidate | confidence high exceeds ceiling; missing fx evidence category |
| backing_vocal_mostly_silent | cross-track vocabulary false positive removed the finding |
| lead_vocal_send_context | one evidence path missing from payload (full-contract only) |
| volume_change_not_supported | cross-track vocabulary false positive (full-contract only) |

Two failure themes remain: the model still reaches `high` confidence on
healthy-track findings a 10-second capture cannot prove to that level, and it
still occasionally uses banned relationship verbs in silence-related prose.
Both are contract-iteration targets for v4; neither produced an unsafe result,
because runtime validation capped or replaced every one of them.

## Decision

- MiniMax M3 passes the Phase 1 selection gate on the pinned
  `minimax-m3-2026-07-11-contract-v3` snapshot and becomes the configured
  default model.
- The DeepSeek models remain available via config; neither is the default.
- v4 contract work (healthy-case confidence ceilings, silence-prose
  vocabulary) should buy margin above the gate before Phase 2 relies on it.
