"""Golden-corpus contract tests. No model calls belong in this suite."""

import json
from dataclasses import replace
from pathlib import Path

import pytest

from postmortem.evaluation import (
    ACTIONABLE_OPERATIONS,
    REQUIRED_SCENARIO_TAGS,
    evaluate_case,
    evaluate_snapshot,
    load_corpus,
    validate_corpus,
)
from postmortem.schemas import DiagnosisResult


CORPUS_PATH = Path(__file__).parent / "fixtures" / "diagnoses" / "corpus.json"


def _representative_result(**updates):
    data = {
        "schema_version": 1,
        "finding": {
            "summary": "The measured peak leaves very little headroom.",
            "probable_cause": "The track output level is close to clipping.",
            "confidence": "high",
            "confidence_reason": "The isolated capture has a measured peak.",
            "evidence_refs": [{"path": "audio.sample_peak_db"}],
        },
        "proposal": {
            "operation": "none",
            "reason": "The finding is useful even without a previewable move.",
            "expected_direction": [],
        },
    }
    for key, value in updates.items():
        section, field = key.split("__", 1)
        data[section][field] = value
    return DiagnosisResult.model_validate(data)


def test_corpus_has_at_least_twenty_complete_deidentified_cases():
    cases = load_corpus(CORPUS_PATH)

    assert len(cases) >= 20
    assert validate_corpus(cases) == []


def test_corpus_payloads_use_the_production_track_check_shape():
    cases = load_corpus(CORPUS_PATH)

    assert all(
        set(case.payload)
        == {"project", "track", "fx_chain", "routing", "capture", "audio"}
        for case in cases
    )
    assert all("sample_rate" not in case.payload["project"] for case in cases)
    assert all("channels" not in case.payload["track"] for case in cases)
    assert all("duration_seconds" not in case.payload["capture"] for case in cases)
    assert all("duration_seconds" in case.payload["audio"] for case in cases)
    assert all("spectrum_note" in case.payload["audio"] for case in cases)

    parent_case = next(case for case in cases if "parent_bus" in case.tags)
    assert parent_case.payload["track"]["parent_track"] == "Guitar Bus"
    assert "parent_track" not in parent_case.payload["routing"]


def test_corpus_covers_every_required_scenario_family():
    cases = load_corpus(CORPUS_PATH)
    covered = {tag for case in cases for tag in case.tags}

    assert REQUIRED_SCENARIO_TAGS <= covered


def test_corpus_has_none_cases_and_positive_and_negative_operation_cases():
    cases = load_corpus(CORPUS_PATH)
    none_cases = [case for case in cases if case.assertions["require_none"]]

    assert len(none_cases) >= 4
    for operation in ACTIONABLE_OPERATIONS:
        assert any(
            operation in case.assertions["allowed_operations"] for case in cases
        )
        assert any(
            operation in case.assertions["forbidden_operations"] for case in cases
        )


def test_single_track_masking_language_is_forbidden_in_every_case():
    cases = load_corpus(CORPUS_PATH)

    assert all(
        "masking" in case.assertions["forbidden_claims"] for case in cases
    )


def test_deidentification_gate_scans_fixture_metadata_and_payload_fields():
    case = load_corpus(CORPUS_PATH)[0]
    private = replace(case, description="Captured from /Users/example/session")
    raw_payload = dict(case.payload)
    raw_payload["audio"] = {**raw_payload["audio"], "raw_audio": "bytes"}
    raw = replace(case, case_id="raw-audio-copy", payload=raw_payload)

    failures = validate_corpus([private, raw] + load_corpus(CORPUS_PATH)[1:])

    assert any("private or client-identifying" in failure for failure in failures)
    assert any("raw audio fields" in failure for failure in failures)


def test_result_evaluator_accepts_concepts_evidence_operation_and_confidence():
    case = load_corpus(CORPUS_PATH)[0]

    assert evaluate_case(case, _representative_result()) == []


def test_result_evaluator_reports_forbidden_claims_and_contract_mismatches():
    case = load_corpus(CORPUS_PATH)[0]
    result = _representative_result(
        finding__summary="The kick is masking the bass.",
        finding__confidence="high",
        finding__evidence_refs=[{"path": "routing.sends[0]"}],
    )

    failures = evaluate_case(case, result)

    assert any("forbidden claim" in failure for failure in failures)
    assert any("required evidence category" in failure for failure in failures)


def test_none_required_case_rejects_an_actionable_result():
    case = next(
        case for case in load_corpus(CORPUS_PATH) if case.assertions["require_none"]
    )
    actionable = {
        "operation": "set_track_volume",
        "reason": "Preview a conservative level reduction.",
        "target": {"track_guid": case.payload["track"]["guid"]},
        "current_value": {"value": 0.0, "unit": "db"},
        "proposed_value": {"value": -1.0, "unit": "db"},
        "goal": "sample_peak_db",
        "expected_direction": [
            {"metric": "sample_peak_db", "direction": "decrease"}
        ],
    }
    data = _representative_result().model_dump()
    data["proposal"] = actionable
    result = DiagnosisResult.model_validate(data)

    failures = evaluate_case(case, result)

    assert any("requires operation none" in failure for failure in failures)


def test_result_evaluator_runs_deterministic_proposal_validation():
    case = load_corpus(CORPUS_PATH)[0]
    data = _representative_result().model_dump()
    data["proposal"] = {
        "operation": "set_track_volume",
        "reason": "Preview a conservative level reduction.",
        "target": {"track_guid": "{WRONG-TRACK}"},
        "current_value": {"value": 0.0, "unit": "db"},
        "proposed_value": {"value": -1.0, "unit": "db"},
        "goal": "sample_peak_db",
        "expected_direction": [
            {"metric": "sample_peak_db", "direction": "decrease"}
        ],
    }

    failures = evaluate_case(case, DiagnosisResult.model_validate(data))

    assert any("deterministic validation" in failure for failure in failures)


def test_offline_snapshot_evaluator_uses_pinned_captured_results(tmp_path):
    case = load_corpus(CORPUS_PATH)[0]
    manifest = {
        "provider": "fixture-provider",
        "model": "economical-model",
        "model_revision": "economical-model-2026-07-11",
        "captured_at": "2026-07-11T00:00:00Z",
        "case_ids": [case.case_id],
    }
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (tmp_path / f"{case.case_id}.json").write_text(
        _representative_result().model_dump_json(), encoding="utf-8"
    )

    summary = evaluate_snapshot([case], tmp_path)

    assert summary["model_revision"] == manifest["model_revision"]
    assert summary["passed"] == 1
    assert summary["failed"] == 0


def test_offline_snapshot_requires_a_pinned_model_revision(tmp_path):
    case = load_corpus(CORPUS_PATH)[0]
    manifest = {
        "provider": "fixture-provider",
        "model": "economical-model",
        "captured_at": "2026-07-11T00:00:00Z",
        "case_ids": [case.case_id],
    }
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="model_revision"):
        evaluate_snapshot([case], tmp_path)
