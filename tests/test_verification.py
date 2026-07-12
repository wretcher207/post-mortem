"""Behavioral tests for the deterministic before/after evaluator (P2-003)."""

import pytest

from postmortem import verification
from postmortem.verification import (
    OUTCOME_INTENDED,
    OUTCOME_NEW_RISK,
    OUTCOME_UNAVAILABLE,
    OUTCOME_UNPROVEN,
    evaluate,
)


def _audio(**overrides):
    base = {
        "sample_peak_db": -6.0,
        "true_peak_db": -5.5,
        "rms_db": -18.0,
        "crest_factor_db": 12.0,
        "integrated_lufs": -20.0,
        "silence_fraction": 0.05,
        "stereo": {"correlation": 0.8, "balance_db": 0.2,
                   "mid_rms_db": -20.0, "side_rms_db": -30.0},
        "spectrum_third_octave": [
            {"freq_hz": 100, "level_db": -18.0},
            {"freq_hz": 1000, "level_db": -24.0},
        ],
    }
    base.update(overrides)
    return base


def _guardrail(result, name):
    return next(g for g in result.guardrails if g.name == name)


def test_goal_moved_as_intended_produces_the_locked_intended_sentence():
    result = evaluate(
        _audio(sample_peak_db=-1.0),
        _audio(sample_peak_db=-3.0),
        goal="sample_peak_db",
        expected_direction=[{"metric": "sample_peak_db", "direction": "decrease"}],
    )

    assert result.available is True
    assert result.goal_outcome == "moved_as_intended"
    assert result.outcome_sentence == OUTCOME_INTENDED


def test_change_below_noise_floor_is_insufficient_not_proof():
    result = evaluate(
        _audio(sample_peak_db=-6.0),
        _audio(sample_peak_db=-6.3),
        goal="sample_peak_db",
        expected_direction=[{"metric": "sample_peak_db", "direction": "decrease"}],
    )

    assert result.goal_outcome == "moved_insufficiently"
    assert result.outcome_sentence == OUTCOME_UNPROVEN


def test_movement_against_the_stated_goal_is_reported():
    result = evaluate(
        _audio(sample_peak_db=-6.0),
        _audio(sample_peak_db=-4.0),
        goal="sample_peak_db",
        expected_direction=[{"metric": "sample_peak_db", "direction": "decrease"}],
    )

    assert result.goal_outcome == "moved_against"
    assert result.outcome_sentence == OUTCOME_UNPROVEN


@pytest.mark.parametrize(
    ("direction", "delta", "outcome"),
    [
        ("not_increase", -2.0, "moved_as_intended"),
        ("not_increase", 2.0, "moved_against"),
        ("not_decrease", 2.0, "moved_as_intended"),
        ("not_decrease", -2.0, "moved_against"),
        ("unchanged", 0.1, "moved_as_intended"),
        ("unchanged", 2.0, "moved_against"),
    ],
)
def test_hold_style_directions(direction, delta, outcome):
    result = evaluate(
        _audio(rms_db=-18.0),
        _audio(rms_db=-18.0 + delta),
        goal="rms_db",
        expected_direction=[{"metric": "rms_db", "direction": direction}],
    )

    assert result.goal_outcome == outcome


def test_goal_metric_missing_from_candidate_is_not_measured():
    candidate = _audio()
    candidate.pop("true_peak_db")
    result = evaluate(
        _audio(),
        candidate,
        goal="true_peak_db",
        expected_direction=[{"metric": "true_peak_db", "direction": "decrease"}],
    )

    assert result.goal_outcome == "not_measured"
    assert result.outcome_sentence == OUTCOME_UNPROVEN


def test_null_metric_is_not_measured_never_zero():
    result = evaluate(
        _audio(integrated_lufs=None),
        _audio(integrated_lufs=None),
        goal="integrated_lufs",
        expected_direction=[{"metric": "integrated_lufs", "direction": "increase"}],
    )

    assert result.goal_outcome == "not_measured"


def test_new_clipping_fails_and_selects_the_risk_sentence():
    result = evaluate(
        _audio(true_peak_db=-2.0, sample_peak_db=-2.5),
        _audio(true_peak_db=-0.1, sample_peak_db=-0.2),
        goal="crest_factor_db",
        expected_direction=[{"metric": "crest_factor_db", "direction": "increase"}],
    )

    assert _guardrail(result, "new_clipping").status == "fail"
    assert result.outcome_sentence == OUTCOME_NEW_RISK


def test_preexisting_clipping_is_not_reported_as_new():
    result = evaluate(
        _audio(true_peak_db=-0.1),
        _audio(true_peak_db=-0.1),
    )

    assert _guardrail(result, "new_clipping").status == "pass"


def test_loudness_shift_beyond_window_warns_and_blocks_the_intended_sentence():
    result = evaluate(
        _audio(integrated_lufs=-20.0, sample_peak_db=-6.0),
        _audio(integrated_lufs=-14.0, sample_peak_db=-8.0),
        goal="sample_peak_db",
        expected_direction=[{"metric": "sample_peak_db", "direction": "decrease"}],
    )

    assert _guardrail(result, "loudness_shift").status == "warn"
    assert result.goal_outcome == "moved_as_intended"
    assert result.outcome_sentence == OUTCOME_UNPROVEN


def test_correlation_drop_warns_with_the_risk_sentence():
    candidate = _audio()
    candidate["stereo"] = dict(candidate["stereo"], correlation=0.4)
    result = evaluate(_audio(), candidate)

    assert _guardrail(result, "phase").status == "warn"
    assert result.outcome_sentence == OUTCOME_NEW_RISK


def test_sign_flip_to_negative_correlation_warns_even_within_drop_window():
    baseline = _audio()
    baseline["stereo"] = dict(baseline["stereo"], correlation=0.1)
    candidate = _audio()
    candidate["stereo"] = dict(candidate["stereo"], correlation=-0.1)
    result = evaluate(baseline, candidate)

    assert _guardrail(result, "phase").status == "warn"


def test_mono_capture_evaluates_without_phase_or_balance_claims():
    baseline = _audio(stereo=None)
    candidate = _audio(stereo=None)
    result = evaluate(baseline, candidate)

    assert _guardrail(result, "phase").status == "pass"
    assert "not measured" in _guardrail(result, "phase").detail
    assert _guardrail(result, "stereo_balance").status == "pass"


def test_balance_shift_warns():
    candidate = _audio()
    candidate["stereo"] = dict(candidate["stereo"], balance_db=4.0)
    result = evaluate(_audio(), candidate)

    assert _guardrail(result, "stereo_balance").status == "warn"
    assert result.outcome_sentence == OUTCOME_NEW_RISK


def test_mostly_silent_candidate_makes_verification_unavailable():
    result = evaluate(
        _audio(),
        _audio(silence_fraction=0.9),
        goal="sample_peak_db",
        expected_direction=[{"metric": "sample_peak_db", "direction": "decrease"}],
    )

    assert result.available is False
    assert _guardrail(result, "silence").status == "fail"
    assert result.goal_outcome == "not_measured"
    assert result.outcome_sentence == OUTCOME_UNAVAILABLE


def test_spectrum_goal_reports_the_largest_common_band_change():
    baseline = _audio()
    candidate = _audio(spectrum_third_octave=[
        {"freq_hz": 100, "level_db": -18.5},
        {"freq_hz": 1000, "level_db": -28.0},
    ])
    result = evaluate(
        baseline,
        candidate,
        goal="spectrum_third_octave",
        expected_direction=[
            {"metric": "spectrum_third_octave", "direction": "decrease"}
        ],
    )

    spectrum = next(d for d in result.deltas if d.metric == "spectrum_third_octave")
    assert spectrum.band_hz == 1000
    assert spectrum.delta == -4.0
    assert result.goal_outcome == "moved_as_intended"


def test_spectrum_with_no_common_bands_is_not_measured():
    candidate = _audio(spectrum_third_octave=[{"freq_hz": 4000, "level_db": -30.0}])
    result = evaluate(
        _audio(),
        candidate,
        goal="spectrum_third_octave",
        expected_direction=[
            {"metric": "spectrum_third_octave", "direction": "decrease"}
        ],
    )

    assert result.goal_outcome == "not_measured"


def test_no_code_path_claims_improvement_without_goal_movement():
    result = evaluate(
        _audio(),
        _audio(),
        goal="sample_peak_db",
        expected_direction=[{"metric": "sample_peak_db", "direction": "decrease"}],
    )

    assert result.outcome_sentence != OUTCOME_INTENDED


def test_report_never_uses_the_word_better():
    result = evaluate(
        _audio(sample_peak_db=-1.0),
        _audio(sample_peak_db=-3.0),
        goal="sample_peak_db",
        expected_direction=[{"metric": "sample_peak_db", "direction": "decrease"}],
    )

    serialized = result.model_dump_json().lower()
    assert "better" not in serialized
    for sentence in (OUTCOME_INTENDED, OUTCOME_UNPROVEN, OUTCOME_NEW_RISK,
                     OUTCOME_UNAVAILABLE):
        assert "better" not in sentence.lower()


def test_result_round_trips_through_the_schema():
    result = evaluate(
        _audio(),
        _audio(sample_peak_db=-8.0),
        goal="sample_peak_db",
        expected_direction=[{"metric": "sample_peak_db", "direction": "decrease"}],
    )

    from postmortem.schemas import VerificationResult

    rebuilt = VerificationResult.model_validate_json(result.model_dump_json())
    assert rebuilt == result


def test_expected_direction_accepts_models_and_dicts():
    from postmortem.schemas import ExpectedMetricDirection

    model_entry = ExpectedMetricDirection(
        metric="sample_peak_db", direction="decrease"
    )
    result = evaluate(
        _audio(sample_peak_db=-1.0),
        _audio(sample_peak_db=-3.0),
        goal="sample_peak_db",
        expected_direction=[model_entry],
    )

    assert result.expected[0].outcome == "moved_as_intended"
