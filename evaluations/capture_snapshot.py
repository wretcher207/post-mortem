"""Capture a pinned model snapshot for the diagnosis corpus.

Usage: capture_snapshot.py <corpus.json> <snapshot_dir> <contract_iteration>

Env must already point at the target provider (ANTHROPIC_BASE_URL,
ANTHROPIC_API_KEY or the built-in secrets fallback, POSTMORTEM_MODEL).
Writes one validated DiagnosisResult JSON per case plus manifest.json.
Skips cases whose result file already exists, so a crashed run resumes.
"""

import datetime
import json
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from postmortem import config, diagnose
from postmortem.evaluation import load_corpus
from postmortem.providers.anthropic_provider import AnthropicProvider


def main():
    if len(sys.argv) < 4:
        print(
            "usage: capture_snapshot.py <corpus.json> <snapshot_dir> "
            "<contract_iteration>\n"
            "Env must point at the target provider first "
            "(ANTHROPIC_BASE_URL, POSTMORTEM_MODEL).",
            file=sys.stderr,
        )
        return 2
    corpus_path, snapshot_dir, iteration = sys.argv[1:4]
    out = pathlib.Path(snapshot_dir)
    out.mkdir(parents=True, exist_ok=True)
    cases = load_corpus(corpus_path)

    provider, profile = AnthropicProvider.from_config()
    print(f"model={profile.model} thinking={profile.thinking}", flush=True)

    for case in cases:
        result_path = out / f"{case.case_id}.json"
        if result_path.exists():
            print(f"skip {case.case_id} (exists)", flush=True)
            continue
        started = time.monotonic()
        try:
            result = diagnose.diagnose_track(
                case.payload, provider=provider, profile=profile
            )
        except Exception as error:  # capture must not die mid-corpus
            print(f"FAIL {case.case_id}: {error!r}", flush=True)
            continue
        result_path.write_text(
            result.model_dump_json(indent=2), encoding="utf-8"
        )
        elapsed = time.monotonic() - started
        print(
            f"done {case.case_id} in {elapsed:.0f}s "
            f"op={result.proposal.operation} "
            f"conf={result.finding.confidence} "
            f"rej={result.proposal.rejection_reason}",
            flush=True,
        )

    manifest = {
        "schema_version": 1,
        "provider": "minimax",
        "model": profile.model,
        "model_revision": f"provider-id-{profile.model}-no-immutable-revision",
        "captured_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "contract_iteration": iteration,
        "case_ids": [case.case_id for case in cases],
    }
    (out / "manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    missing = [
        case.case_id
        for case in cases
        if not (out / f"{case.case_id}.json").exists()
    ]
    print(f"manifest written; missing={missing or 'none'}", flush=True)
    return 1 if missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
