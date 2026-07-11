# Captured model snapshots

This directory is separate from `tests/fixtures/diagnoses/corpus.json` on
purpose. Normal unit tests validate the corpus and evaluator without making
network or model calls.

For an intentional model evaluation, create a snapshot directory containing:

- `manifest.json` with `provider`, `model`, pinned `model_revision`,
  `captured_at`, and the corpus `case_ids` in order.
- One validated `DiagnosisResult` JSON file per case, named `<case_id>.json`.
- An optional `capture_errors.json` sidecar containing sanitized failed-attempt
  metadata. It is operational evidence and is not evaluated as a diagnosis.

Then run:

```bash
python -m postmortem.evaluation \
  tests/fixtures/diagnoses/corpus.json \
  evaluations/results/<snapshot-name>
```

The evaluator reads existing JSON only. It has no provider integration and
cannot spend credits. Snapshot output should be reviewed for de-identification
before it is committed.

The first complete benchmark is recorded in
`2026-07-11-model-benchmark.md`, with captured snapshots for DeepSeek V4 Flash,
DeepSeek V4 Pro, and MiniMax M3. It is a failed selection gate: none reached the
required usefulness threshold, so the results do not authorize a default-model
change.

The hardened provider-contract rerun is recorded in
`2026-07-11-model-contract-v2-benchmark.md`. MiniMax M3 improved to 19/25 useful
findings and 15/25 full-contract passes without an operational retry, but remains
one useful finding short of the selection gate. The baseline and v2 snapshots
are both retained for direct comparison.
