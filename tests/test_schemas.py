"""Behavioral contract tests for structured diagnosis results."""

import json

import pytest
from pydantic import ValidationError

from postmortem.schemas import DiagnosisResult


def _advice_only_result():
    return {
        "schema_version": 1,
        "finding": {
            "summary": "The capture is too quiet to support a safe move.",
            "probable_cause": "The cursor may be parked outside the performance.",
            "confidence": "low",
            "confidence_reason": "Most of the measured section is silent.",
            "evidence_refs": [{"path": "audio.silence_fraction"}],
        },
        "proposal": {
            "operation": "none",
            "reason": "There is not enough measured signal for a safe proposal.",
            "target": None,
            "current_value": None,
            "proposed_value": None,
            "goal": None,
            "expected_direction": [],
        },
    }


def _track_volume_result():
    payload = _advice_only_result()
    payload["proposal"] = {
        "operation": "set_track_volume",
        "reason": "Reduce the track's hot output before the next stage.",
        "target": {"track_guid": "{TRACK-KICK}", "track_name": "Kick"},
        "current_value": {"value": 0.0, "unit": "db", "display": "0.0 dB"},
        "proposed_value": {"value": -2.0, "unit": "db", "display": "-2.0 dB"},
        "goal": "reduce_peak_level",
        "expected_direction": [
            {"metric": "sample_peak_db", "direction": "decrease"}
        ],
    }
    return payload


def _fx_parameter_result():
    payload = _track_volume_result()
    payload["proposal"].update(
        {
            "operation": "set_fx_param",
            "target": {
                "track_guid": "{TRACK-KICK}",
                "track_name": "Kick",
                "fx_guid": "{FX-EQ}",
                "fx_index": 2,
                "fx_scope": "track",
                "fx_name": "VST3: Pro-Q 4",
                "parameter_index": 17,
                "parameter_name": "Band 3 Gain",
            },
            "current_value": {
                "value": 0.50,
                "unit": "normalized",
                "display": "0.0 dB",
            },
            "proposed_value": {
                "value": 0.45,
                "unit": "normalized",
                "display": "-1.5 dB",
            },
            "goal": "reduce_low_mid_buildup",
            "expected_direction": [
                {"metric": "spectrum.400_hz", "direction": "decrease"}
            ],
        }
    )
    return payload


def test_advice_only_result_round_trips_deterministically():
    result = DiagnosisResult.model_validate(_advice_only_result())

    first = result.model_dump_json(exclude_none=True)
    second = result.model_dump_json(exclude_none=True)

    assert first == second
    assert DiagnosisResult.model_validate(json.loads(first)) == result


def test_none_proposal_rejects_action_fields():
    payload = _advice_only_result()
    payload["proposal"]["target"] = {
        "track_guid": "{TRACK-KICK}",
        "track_name": "Kick",
    }
    payload["proposal"]["proposed_value"] = {
        "value": -2.0,
        "unit": "db",
    }

    with pytest.raises(ValidationError, match="none proposal"):
        DiagnosisResult.model_validate(payload)


def test_actionable_proposal_requires_complete_action_fields():
    payload = _advice_only_result()
    payload["proposal"]["operation"] = "set_track_volume"

    with pytest.raises(ValidationError, match="actionable proposal requires"):
        DiagnosisResult.model_validate(payload)


def test_track_volume_uses_explicit_db_values():
    payload = _track_volume_result()
    payload["proposal"]["proposed_value"]["unit"] = "normalized"
    payload["proposal"]["proposed_value"]["value"] = 0.5

    with pytest.raises(ValidationError, match="set_track_volume values must use db"):
        DiagnosisResult.model_validate(payload)


def test_fx_operation_requires_stable_verified_fx_identity():
    payload = _fx_parameter_result()
    payload["proposal"]["target"].pop("fx_guid")

    with pytest.raises(ValidationError, match="FX operations require"):
        DiagnosisResult.model_validate(payload)


def test_fx_parameter_operation_requires_verified_parameter_identity():
    payload = _fx_parameter_result()
    payload["proposal"]["target"].pop("parameter_name")

    with pytest.raises(ValidationError, match="set_fx_param requires parameter"):
        DiagnosisResult.model_validate(payload)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -0.01, 1.01])
def test_fx_normalized_values_are_finite_and_bounded(value):
    payload = _fx_parameter_result()
    payload["proposal"]["proposed_value"]["value"] = value

    with pytest.raises(ValidationError):
        DiagnosisResult.model_validate(payload)


def test_valid_fx_parameter_result_preserves_stable_identity():
    result = DiagnosisResult.model_validate(_fx_parameter_result())

    assert result.proposal.target.track_guid == "{TRACK-KICK}"
    assert result.proposal.target.fx_guid == "{FX-EQ}"
    assert result.proposal.target.fx_index == 2
    assert result.proposal.target.parameter_index == 17


@pytest.mark.parametrize(
    ("field", "value"),
    [("schema_version", 2), ("operation", "delete_track")],
)
def test_unknown_schema_versions_and_operations_fail_closed(field, value):
    payload = _advice_only_result()
    if field == "operation":
        payload["proposal"][field] = value
    else:
        payload[field] = value

    with pytest.raises(ValidationError):
        DiagnosisResult.model_validate(payload)


def test_unknown_contract_fields_fail_closed():
    payload = _advice_only_result()
    payload["proposal"]["authorization"] = "apply_without_confirmation"

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        DiagnosisResult.model_validate(payload)


def test_user_facing_strings_are_bounded():
    payload = _advice_only_result()
    payload["finding"]["summary"] = "x" * 1_001

    with pytest.raises(ValidationError, match="String should have at most 1000 characters"):
        DiagnosisResult.model_validate(payload)


def test_fx_bypass_requires_boolean_values():
    payload = _fx_parameter_result()
    proposal = payload["proposal"]
    proposal["operation"] = "set_fx_bypass"
    proposal["target"].pop("parameter_index")
    proposal["target"].pop("parameter_name")
    proposal["current_value"] = {"value": False, "unit": "boolean", "display": "On"}
    proposal["proposed_value"] = {"value": 1, "unit": "boolean", "display": "Bypassed"}

    with pytest.raises(ValidationError, match="boolean values must be true or false"):
        DiagnosisResult.model_validate(payload)


@pytest.mark.parametrize(
    ("field", "value"),
    [("schema_version", True), ("fx_index", "2"), ("parameter_index", True)],
)
def test_versions_and_identity_indices_are_strict_integers(field, value):
    payload = _fx_parameter_result()
    if field == "schema_version":
        payload[field] = value
    else:
        payload["proposal"]["target"][field] = value

    with pytest.raises(ValidationError):
        DiagnosisResult.model_validate(payload)


def test_stable_track_guid_is_sufficient_without_track_name():
    payload = _fx_parameter_result()
    payload["proposal"]["target"].pop("track_name")

    result = DiagnosisResult.model_validate(payload)

    assert result.proposal.target.track_guid == "{TRACK-KICK}"
    assert result.proposal.target.track_name is None


def test_none_proposal_may_keep_explanation_only_metadata():
    payload = _advice_only_result()
    proposal = payload["proposal"]
    proposal["current_value"] = {"value": -1.0, "unit": "db"}
    proposal["goal"] = "avoid_clipping"
    proposal["expected_direction"] = [
        {"metric": "true_peak_db", "direction": "not_increase"}
    ]

    result = DiagnosisResult.model_validate(payload)

    assert result.proposal.operation == "none"
    assert result.proposal.goal == "avoid_clipping"


def test_numeric_proposal_values_reject_strings():
    payload = _fx_parameter_result()
    payload["proposal"]["proposed_value"]["value"] = "0.45"

    with pytest.raises(ValidationError):
        DiagnosisResult.model_validate(payload)
