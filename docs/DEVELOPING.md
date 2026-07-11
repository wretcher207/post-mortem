# Developing Post Mortem

Use Python 3.10 or newer. The CI matrix runs 3.10, 3.12, and the latest
supported stable Python on Windows, macOS, and Ubuntu.

## Install and test

macOS/Linux:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install . pytest build
python -m pytest -q
python -m build
```

Windows PowerShell:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install . pytest build
python -m pytest -q
python -m build
```

The normal suite makes no model calls. The golden corpus at
`tests/fixtures/diagnoses/corpus.json` contains de-identified Track Check
payloads and assertion-based expectations.

## Produce a fixture-backed JSON diagnosis

The checked-in example is a validated `DiagnosisResult` for the corpus case
`kick_near_clipping`. This command loads the real corpus payload, validates the
example, evaluates it against that case, and prints the same JSON shape exposed
by `postmortem --format json`:

```bash
python -c "from pathlib import Path; from postmortem.evaluation import load_corpus,evaluate_case; from postmortem.schemas import DiagnosisResult; c=load_corpus('tests/fixtures/diagnoses/corpus.json')[0]; d=DiagnosisResult.model_validate_json(Path('docs/examples/kick_near_clipping.result.json').read_text()); assert evaluate_case(c,d)==[]; print(d.model_dump_json(indent=2))"
```

This is deterministic and offline. It proves the fixture, structured schema,
and evaluation contract work together; it does not claim that a model generated
the example.

Intentional model evaluations are stored separately from unit fixtures. Create
`evaluations/results/<snapshot>/manifest.json` with `provider`, `model`, a
pinned `model_revision`, `captured_at`, and every corpus case ID in order, then
add one `<case_id>.json` result per case and run:

```bash
python -m postmortem.evaluation \
  tests/fixtures/diagnoses/corpus.json \
  evaluations/results/<snapshot>
```

The evaluator only reads captured files and cannot spend provider credits.
Review snapshots for de-identification before committing them.

Phase 1 closeout requires two intentional snapshots against the same corpus: a
pinned economical model and the current quality baseline. The offline fixture
example above does not satisfy that external model-evaluation gate.

## Contract changes

Update schema tests, proposal tests, the golden corpus assertions, and
`docs/STRUCTURED_RESULTS.md` together. A breaking JSON change requires a new
`schema_version`; do not reinterpret version 1 fields in place. Provider work
must follow `docs/PROVIDER_ADAPTERS.md`, including typed errors and same-source
credential handling.
