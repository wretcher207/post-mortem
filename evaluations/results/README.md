# Captured model snapshots

This directory is separate from `tests/fixtures/diagnoses/corpus.json` on
purpose. Normal unit tests validate the corpus and evaluator without making
network or model calls.

For an intentional model evaluation, create a snapshot directory containing:

- `manifest.json` with `provider`, `model`, pinned `model_revision`,
  `captured_at`, and the corpus `case_ids` in order.
- One validated `DiagnosisResult` JSON file per case, named `<case_id>.json`.

Then run:

```bash
python -m postmortem.evaluation \
  tests/fixtures/diagnoses/corpus.json \
  evaluations/results/<snapshot-name>
```

The evaluator reads existing JSON only. It has no provider integration and
cannot spend credits. Snapshot output should be reviewed for de-identification
before it is committed.
