"""Behavioral tests for deterministic proposal validation."""

import pytest

from postmortem.proposals import validate_proposal
from postmortem.diagnose import render_diagnosis_text
from postmortem.schemas import DiagnosisResult, Proposal


def _payload():
    return {
        "track": {
            "guid": "{TRACK-KICK}",
            "name": "Kick",
            "volume_db": -3.0,
            "pan": 0.0,
        },
        "fx_chain": [
            {
                "guid": "{FX-EQ}",
                "index": 2,
                "scope": "track",
                "name": "VST3: Pro-Q 4",
                "enabled": True,
                "parameters": [
                    {
                        "index": 17,
                        "name": "Band 3 Gain",
                        "normalized_value": 0.5,
                        "formatted_value": "0.0 dB",
                    }
                ],
            }
        ],
        "audio": {
            "sample_peak_db": -1.0,
            "true_peak_db": -0.7,
            "crest_factor_db": 8.0,
            "stereo": {"balance_db": 2.0},
            "spectrum_third_octave": [
                {"freq_hz": 400, "level_db": -12.0}
            ],
        },
    }


def _track_volume_result():
    return DiagnosisResult.model_validate(
        {
            "schema_version": 1,
            "finding": {
                "summary": "The track is close to clipping.",
                "probable_cause": "The track output is too hot.",
                "confidence": "high",
                "confidence_reason": "The measured sample peak is -1 dBFS.",
                "evidence_refs": [
                    {
                        "path": "audio.sample_peak_db",
                        "description": "Measured sample peak.",
                    }
                ],
            },
            "proposal": {
                "operation": "set_track_volume",
                "reason": "Preview a 2 dB reduction to create headroom.",
                "target": {
                    "track_guid": "{TRACK-KICK}",
                    "track_name": "Kick",
                },
                "current_value": {"value": -3.0, "unit": "db"},
                "proposed_value": {"value": -5.0, "unit": "db"},
                "goal": "sample_peak_db",
                "expected_direction": [
                    {"metric": "sample_peak_db", "direction": "decrease"}
                ],
            },
        }
    )


def _track_pan_result():
    result = _track_volume_result().model_copy(deep=True)
    result.finding.evidence_refs[0].path = "audio.stereo.balance_db"
    result.proposal.operation = "set_track_pan"
    result.proposal.current_value.value = 0.0
    result.proposal.current_value.unit = "normalized_pan"
    result.proposal.proposed_value.value = -0.15
    result.proposal.proposed_value.unit = "normalized_pan"
    result.proposal.goal = "stereo_balance_db"
    result.proposal.expected_direction[0].metric = "stereo_balance_db"
    return DiagnosisResult.model_validate(result.model_dump())


def _fx_parameter_result():
    result = _track_volume_result().model_copy(deep=True)
    result.finding.evidence_refs[0].path = (
        "audio.spectrum_third_octave[0].level_db"
    )
    result.proposal.operation = "set_fx_param"
    result.proposal.target.fx_guid = "{FX-EQ}"
    result.proposal.target.fx_index = 2
    result.proposal.target.fx_scope = "track"
    result.proposal.target.fx_name = "VST3: Pro-Q 4"
    result.proposal.target.parameter_index = 17
    result.proposal.target.parameter_name = "Band 3 Gain"
    result.proposal.current_value.value = 0.5
    result.proposal.current_value.unit = "normalized"
    result.proposal.proposed_value.value = 0.42
    result.proposal.proposed_value.unit = "normalized"
    result.proposal.goal = "spectrum_third_octave"
    result.proposal.expected_direction[0].metric = "spectrum_third_octave"
    return DiagnosisResult.model_validate(result.model_dump())


def _fx_bypass_result():
    result = _fx_parameter_result().model_copy(deep=True)
    result.finding.evidence_refs[0].path = "fx_chain[0].enabled"
    result.proposal.operation = "set_fx_bypass"
    result.proposal.reason = (
        "Preview bypassing this FX; this does not remove or delete the plugin."
    )
    result.proposal.target.parameter_index = None
    result.proposal.target.parameter_name = None
    result.proposal.current_value.value = False
    result.proposal.current_value.unit = "boolean"
    result.proposal.proposed_value.value = True
    result.proposal.proposed_value.unit = "boolean"
    result.proposal.goal = "sample_peak_db"
    result.proposal.expected_direction[0].metric = "sample_peak_db"
    return DiagnosisResult.model_validate(result.model_dump())


def test_valid_conservative_track_volume_proposal_survives_unchanged():
    result = _track_volume_result()

    validated = validate_proposal(result, _payload())

    assert validated == result
    assert validated.proposal.operation == "set_track_volume"


@pytest.mark.parametrize(
    "factory",
    [_track_pan_result, _fx_parameter_result, _fx_bypass_result],
)
def test_each_supported_conservative_operation_can_remain_previewable(factory):
    result = factory()

    validated = validate_proposal(result, _payload())

    assert validated == result
    assert validated.proposal.operation != "none"


def test_missing_evidence_path_rejects_only_the_proposal():
    result = _track_volume_result().model_copy(deep=True)
    original_finding = result.finding.model_copy(deep=True)
    result.finding.evidence_refs[0].path = "audio.not_measured"

    validated = validate_proposal(result, _payload())

    assert validated.finding == result.finding
    assert validated.finding.summary == original_finding.summary
    assert validated.proposal.operation == "none"
    assert validated.proposal.rejection_reason == "evidence_path_missing"


def test_null_evidence_value_is_not_treated_as_measurement():
    payload = _payload()
    payload["audio"]["true_peak_db"] = None
    result = _track_volume_result().model_copy(deep=True)
    result.finding.evidence_refs[0].path = "audio.true_peak_db"

    validated = validate_proposal(result, payload)

    assert validated.proposal.operation == "none"
    assert validated.proposal.rejection_reason == "evidence_value_null"


def test_actionable_proposal_requires_at_least_one_evidence_reference():
    result = _track_volume_result().model_copy(deep=True)
    result.finding.evidence_refs = []

    validated = validate_proposal(result, _payload())

    assert validated.proposal.operation == "none"
    assert validated.proposal.rejection_reason == "evidence_missing"


def test_stale_track_guid_rejects_the_proposal():
    result = _track_volume_result().model_copy(deep=True)
    result.proposal.target.track_guid = "{TRACK-STALE}"

    validated = validate_proposal(result, _payload())

    assert validated.proposal.operation == "none"
    assert validated.proposal.rejection_reason == "track_identity_mismatch"


def test_track_volume_current_value_must_match_payload_within_tolerance():
    result = _track_volume_result().model_copy(deep=True)
    result.proposal.current_value.value = -2.0

    validated = validate_proposal(result, _payload())

    assert validated.proposal.operation == "none"
    assert validated.proposal.rejection_reason == "current_value_mismatch"


def test_track_volume_move_over_three_db_is_rejected():
    result = _track_volume_result().model_copy(deep=True)
    result.proposal.proposed_value.value = -6.1

    validated = validate_proposal(result, _payload())

    assert validated.proposal.operation == "none"
    assert validated.proposal.rejection_reason == "move_limit_exceeded"


def test_track_pan_current_value_must_match_payload():
    result = _track_pan_result().model_copy(deep=True)
    result.proposal.current_value.value = 0.1

    validated = validate_proposal(result, _payload())

    assert validated.proposal.operation == "none"
    assert validated.proposal.rejection_reason == "current_value_mismatch"


def test_track_pan_move_over_point_two_is_rejected():
    result = _track_pan_result().model_copy(deep=True)
    result.proposal.proposed_value.value = -0.21

    validated = validate_proposal(result, _payload())

    assert validated.proposal.operation == "none"
    assert validated.proposal.rejection_reason == "move_limit_exceeded"


@pytest.mark.parametrize(
    ("field", "stale_value"),
    [
        ("fx_guid", "{FX-STALE}"),
        ("fx_index", 3),
        ("fx_scope", "input"),
        ("fx_name", "VST3: Different EQ"),
    ],
)
def test_fx_identity_fields_must_describe_one_payload_fx(field, stale_value):
    result = _fx_parameter_result().model_copy(deep=True)
    setattr(result.proposal.target, field, stale_value)

    validated = validate_proposal(result, _payload())

    assert validated.proposal.operation == "none"
    assert validated.proposal.rejection_reason == "fx_identity_mismatch"


@pytest.mark.parametrize(
    ("field", "stale_value"),
    [("parameter_index", 18), ("parameter_name", "Band 4 Gain")],
)
def test_parameter_identity_fields_must_describe_one_payload_parameter(
    field, stale_value
):
    result = _fx_parameter_result().model_copy(deep=True)
    setattr(result.proposal.target, field, stale_value)

    validated = validate_proposal(result, _payload())

    assert validated.proposal.operation == "none"
    assert validated.proposal.rejection_reason == "parameter_identity_mismatch"


def test_fx_parameter_current_value_must_match_payload():
    result = _fx_parameter_result().model_copy(deep=True)
    result.proposal.current_value.value = 0.6

    validated = validate_proposal(result, _payload())

    assert validated.proposal.operation == "none"
    assert validated.proposal.rejection_reason == "current_value_mismatch"


def test_formatted_value_does_not_count_as_a_verified_display_mapping():
    result = _fx_parameter_result().model_copy(deep=True)
    result.proposal.proposed_value.value = 0.61

    validated = validate_proposal(result, _payload())

    assert validated.proposal.operation == "none"
    assert validated.proposal.rejection_reason == "move_limit_exceeded"


def test_fx_parameter_move_over_point_one_is_rejected_without_display_mapping():
    payload = _payload()
    payload["fx_chain"][0]["parameters"][0]["formatted_value"] = ""
    result = _fx_parameter_result().model_copy(deep=True)
    result.proposal.proposed_value.value = 0.61

    validated = validate_proposal(result, payload)

    assert validated.proposal.operation == "none"
    assert validated.proposal.rejection_reason == "move_limit_exceeded"


def test_fx_bypass_current_state_must_match_payload_enabled_state():
    result = _fx_bypass_result().model_copy(deep=True)
    result.proposal.current_value.value = True

    validated = validate_proposal(result, _payload())

    assert validated.proposal.operation == "none"
    assert validated.proposal.rejection_reason == "current_value_mismatch"


def test_fx_bypass_must_change_the_current_bypass_state():
    result = _fx_bypass_result().model_copy(deep=True)
    result.proposal.proposed_value.value = False

    validated = validate_proposal(result, _payload())

    assert validated.proposal.operation == "none"
    assert validated.proposal.rejection_reason == "proposed_value_unchanged"


def test_fx_bypass_requires_evidence_that_directly_cites_the_fx():
    result = _fx_bypass_result().model_copy(deep=True)
    result.finding.evidence_refs[0].path = "audio.sample_peak_db"

    validated = validate_proposal(result, _payload())

    assert validated.proposal.operation == "none"
    assert validated.proposal.rejection_reason == "fx_bypass_evidence_missing"


def test_fx_bypass_must_explicitly_be_a_preview_not_a_deletion():
    result = _fx_bypass_result().model_copy(deep=True)
    result.proposal.reason = "Remove this plugin because it looks unnecessary."

    validated = validate_proposal(result, _payload())

    assert validated.proposal.operation == "none"
    assert validated.proposal.rejection_reason == "fx_bypass_not_preview"


def test_unknown_goal_metric_rejects_the_proposal():
    result = _track_volume_result().model_copy(deep=True)
    result.proposal.goal = "make_it_better"

    validated = validate_proposal(result, _payload())

    assert validated.proposal.operation == "none"
    assert validated.proposal.rejection_reason == "unsupported_goal"


def test_unknown_expected_metric_rejects_the_proposal():
    result = _track_volume_result().model_copy(deep=True)
    result.proposal.expected_direction[0].metric = "vibes"

    validated = validate_proposal(result, _payload())

    assert validated.proposal.operation == "none"
    assert validated.proposal.rejection_reason == "unsupported_metric"


def test_text_renderer_distinguishes_previewable_moves_from_advice_only():
    previewable = render_diagnosis_text(
        validate_proposal(_track_volume_result(), _payload())
    )
    rejected_result = _track_volume_result().model_copy(deep=True)
    rejected_result.proposal.goal = "make_it_better"
    advice_only = render_diagnosis_text(
        validate_proposal(rejected_result, _payload())
    )

    assert "SUGGESTED MOVE: Previewable move." in previewable
    assert "SUGGESTED MOVE: Advice only." in advice_only
    assert "Preview a 2 dB reduction to create headroom." in advice_only
    assert "unsupported_goal" not in advice_only


def test_advice_only_result_marks_hallucinated_evidence_without_becoming_actionable():
    result = _track_volume_result().model_copy(deep=True)
    result.finding.evidence_refs[0].path = "audio.imaginary_metric"
    result.proposal = Proposal(
        operation="none",
        reason="No safe change was proposed.",
        expected_direction=[],
    )

    validated = validate_proposal(result, _payload())

    assert validated.proposal.operation == "none"
    assert validated.proposal.rejection_reason == "evidence_path_missing"
