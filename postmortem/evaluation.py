"""Offline evaluation for the versioned single-track diagnosis corpus.

This module reads payload fixtures and already-captured DiagnosisResult JSON.
It never imports a provider SDK or performs a model call.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

from .proposals import validate_proposal
from .schemas import DiagnosisResult


ACTIONABLE_OPERATIONS = frozenset(
    {"set_track_volume", "set_track_pan", "set_fx_param", "set_fx_bypass"}
)
REQUIRED_SCENARIO_TAGS = frozenset(
    {
        "kick",
        "snare",
        "drum_bus",
        "bass_guitar",
        "synth_bass",
        "clean_guitar",
        "distorted_guitar",
        "lead_vocal",
        "backing_vocal",
        "synth",
        "pad",
        "mono",
        "stereo",
        "silent",
        "mostly_silent",
        "clipping",
        "near_clipping",
        "overcompression",
        "phase",
        "stereo_imbalance",
        "parent_bus",
        "sends",
        "receives",
        "gain_staging",
        "no_problem",
        "insufficient_evidence",
    }
)
_CONFIDENCE_ORDER = {"low": 0, "medium": 1, "high": 2}
_FINDING_FAILURE_PREFIXES = (
    "missing required concept:",
    "contains forbidden claim:",
    "missing required evidence category:",
    "confidence ",
)
_SENSITIVE_FRAGMENTS = (
    "/users/",
    "c:\\users\\",
    "api_key",
    "authorization",
    "client_name",
)
_RAW_AUDIO_KEYS = frozenset(
    {"samples", "waveform", "pcm", "audio_bytes", "raw_audio", "file_path"}
)


@dataclass(frozen=True)
class CorpusCase:
    case_id: str
    description: str
    tags: tuple[str, ...]
    payload: dict[str, Any]
    assertions: dict[str, Any]


def _deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = deepcopy(value)
    return merged


def load_corpus(path: str | Path) -> list[CorpusCase]:
    """Load and expand a versioned corpus JSON file without model calls."""
    document = json.loads(Path(path).read_text(encoding="utf-8"))
    if document.get("schema_version") != 1:
        raise ValueError("unsupported diagnosis corpus schema_version")
    defaults = document.get("payload_defaults")
    assertion_defaults = document.get("assertion_defaults", {})
    raw_cases = document.get("cases")
    if (
        not isinstance(defaults, dict)
        or not isinstance(assertion_defaults, dict)
        or not isinstance(raw_cases, list)
    ):
        raise ValueError("corpus requires payload_defaults and cases")
    cases = []
    for raw in raw_cases:
        cases.append(
            CorpusCase(
                case_id=raw["id"],
                description=raw["description"],
                tags=tuple(raw["tags"]),
                payload=_deep_merge(defaults, raw.get("payload", {})),
                assertions=_deep_merge(assertion_defaults, raw["assertions"]),
            )
        )
    return cases


def _walk_keys(value: Any):
    if isinstance(value, dict):
        for key, child in value.items():
            yield str(key).lower()
            yield from _walk_keys(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_keys(child)


def validate_corpus(cases: list[CorpusCase]) -> list[str]:
    """Return corpus-shape, coverage, and de-identification failures."""
    failures: list[str] = []
    if len(cases) < 20:
        failures.append("corpus must contain at least 20 cases")
    ids = [case.case_id for case in cases]
    if len(ids) != len(set(ids)):
        failures.append("corpus case ids must be unique")

    covered_tags = {tag for case in cases for tag in case.tags}
    missing_tags = sorted(REQUIRED_SCENARIO_TAGS - covered_tags)
    if missing_tags:
        failures.append(f"missing scenario tags: {', '.join(missing_tags)}")

    none_count = 0
    positive = set()
    negative = set()
    for case in cases:
        prefix = f"{case.case_id}: "
        assertions = case.assertions
        required_fields = {
            "required_concepts",
            "forbidden_claims",
            "allowed_operations",
            "forbidden_operations",
            "required_evidence_categories",
            "max_confidence",
            "require_none",
        }
        missing = sorted(required_fields - assertions.keys())
        if missing:
            failures.append(prefix + f"missing assertions: {', '.join(missing)}")
            continue
        if "masking" not in assertions["forbidden_claims"]:
            failures.append(prefix + "must forbid single-track masking claims")
        if assertions["require_none"]:
            none_count += 1
        positive.update(
            ACTIONABLE_OPERATIONS.intersection(assertions["allowed_operations"])
        )
        negative.update(
            ACTIONABLE_OPERATIONS.intersection(assertions["forbidden_operations"])
        )
        if assertions["max_confidence"] not in _CONFIDENCE_ORDER:
            failures.append(prefix + "has invalid max_confidence")
        expected_payload_keys = {
            "project",
            "track",
            "fx_chain",
            "routing",
            "capture",
            "audio",
        }
        if set(case.payload) != expected_payload_keys:
            failures.append(prefix + "must match the production Track Check payload")
        capture = case.payload.get("capture", {})
        if not (
            capture.get("scope") == "isolated_track"
            and capture.get("isolation_verified") is True
        ):
            failures.append(prefix + "must use verified isolated capture provenance")
        serialized = json.dumps(
            {
                "id": case.case_id,
                "description": case.description,
                "tags": case.tags,
                "payload": case.payload,
                "assertions": case.assertions,
            },
            sort_keys=True,
        ).lower()
        if any(fragment in serialized for fragment in _SENSITIVE_FRAGMENTS):
            failures.append(prefix + "contains private or client-identifying data")
        raw_keys = _RAW_AUDIO_KEYS.intersection(_walk_keys(case.payload))
        if raw_keys:
            failures.append(prefix + f"contains raw audio fields: {sorted(raw_keys)}")

    if none_count < 4:
        failures.append("at least four cases must require operation none")
    for operation in sorted(ACTIONABLE_OPERATIONS - positive):
        failures.append(f"missing positive case for {operation}")
    for operation in sorted(ACTIONABLE_OPERATIONS - negative):
        failures.append(f"missing negative case for {operation}")
    return failures


def _evidence_category(path: str) -> str | None:
    normalized = path.removeprefix("$.")
    if normalized == "track.parent_track":
        return "routing"
    if normalized.startswith("fx_chain"):
        return "fx"
    if normalized.startswith("routing"):
        return "routing"
    if normalized.startswith("capture"):
        return "capture"
    if normalized.startswith("audio.spectrum_third_octave"):
        return "spectrum"
    if normalized.startswith("audio.stereo"):
        return "stereo"
    if normalized == "audio.silence_fraction":
        return "silence"
    if normalized in {
        "audio.crest_factor_db",
        "audio.loudness_range_lu",
        "audio.lufs_momentary_max",
        "audio.lufs_short_term_max",
    }:
        return "dynamics"
    if normalized.startswith("audio."):
        return "level"
    return None


def evaluate_case(case: CorpusCase, result: DiagnosisResult | dict) -> list[str]:
    """Evaluate one validated result against assertion-based expectations."""
    diagnosis = DiagnosisResult.model_validate(result)
    validated = validate_proposal(diagnosis, case.payload)
    assertions = case.assertions
    failures: list[str] = []
    if validated.proposal.rejection_reason is not None:
        failures.append(
            "proposal failed deterministic validation: "
            + validated.proposal.rejection_reason
        )
    text = " ".join(
        (
            diagnosis.finding.summary,
            diagnosis.finding.probable_cause,
            diagnosis.finding.confidence_reason,
            diagnosis.proposal.reason,
        )
    ).lower()
    for concept in assertions["required_concepts"]:
        aliases = [alias.lower() for alias in concept["aliases"]]
        if not any(alias in text for alias in aliases):
            failures.append(f"missing required concept: {concept['name']}")
    for claim in assertions["forbidden_claims"]:
        if claim.lower() in text:
            failures.append(f"contains forbidden claim: {claim}")

    operation = diagnosis.proposal.operation
    if assertions["require_none"] and operation != "none":
        failures.append(f"requires operation none, received {operation}")
    if operation not in assertions["allowed_operations"]:
        failures.append(f"operation not allowed: {operation}")
    if operation in assertions["forbidden_operations"]:
        failures.append(f"operation explicitly forbidden: {operation}")

    evidence_categories = {
        category
        for ref in diagnosis.finding.evidence_refs
        if (category := _evidence_category(ref.path)) is not None
    }
    for required in assertions["required_evidence_categories"]:
        if required not in evidence_categories:
            failures.append(f"missing required evidence category: {required}")

    maximum = assertions["max_confidence"]
    if _CONFIDENCE_ORDER[diagnosis.finding.confidence] > _CONFIDENCE_ORDER[maximum]:
        failures.append(
            f"confidence {diagnosis.finding.confidence} exceeds maximum {maximum}"
        )
    return failures


def evaluate_snapshot(
    cases: list[CorpusCase], snapshot_dir: str | Path
) -> dict[str, Any]:
    """Score captured results from one explicitly pinned model snapshot."""
    directory = Path(snapshot_dir)
    manifest = json.loads(
        (directory / "manifest.json").read_text(encoding="utf-8")
    )
    required_metadata = {"provider", "model", "model_revision", "captured_at"}
    missing_metadata = sorted(required_metadata - manifest.keys())
    if missing_metadata:
        raise ValueError(
            "snapshot manifest missing: " + ", ".join(missing_metadata)
        )
    if not str(manifest["model_revision"]).strip():
        raise ValueError("snapshot model_revision must be pinned")

    expected_ids = [case.case_id for case in cases]
    if manifest.get("case_ids") != expected_ids:
        raise ValueError("snapshot case_ids must exactly match the evaluated corpus")

    case_results = []
    passed = 0
    finding_passed = 0
    for case in cases:
        result_path = directory / f"{case.case_id}.json"
        diagnosis = DiagnosisResult.model_validate_json(
            result_path.read_text(encoding="utf-8")
        )
        failures = evaluate_case(case, diagnosis)
        finding_failures = [
            failure
            for failure in failures
            if failure.startswith(_FINDING_FAILURE_PREFIXES)
        ]
        if not failures:
            passed += 1
        if not finding_failures:
            finding_passed += 1
        case_results.append(
            {
                "case_id": case.case_id,
                "finding_failures": finding_failures,
                "failures": failures,
            }
        )
    return {
        "provider": manifest["provider"],
        "model": manifest["model"],
        "model_revision": manifest["model_revision"],
        "captured_at": manifest["captured_at"],
        "total": len(cases),
        "finding_passed": finding_passed,
        "finding_failed": len(cases) - finding_passed,
        "passed": passed,
        "failed": len(cases) - passed,
        "cases": case_results,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate captured diagnosis JSON without calling a model."
    )
    parser.add_argument("corpus", type=Path)
    parser.add_argument("snapshot_dir", type=Path)
    args = parser.parse_args(argv)
    cases = load_corpus(args.corpus)
    corpus_failures = validate_corpus(cases)
    if corpus_failures:
        print(json.dumps({"corpus_failures": corpus_failures}, indent=2))
        return 2
    summary = evaluate_snapshot(cases, args.snapshot_dir)
    print(json.dumps(summary, indent=2))
    return 1 if summary["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
