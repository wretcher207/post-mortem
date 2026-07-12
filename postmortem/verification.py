"""Deterministic before/after goal and guardrail evaluation (Phase 2, P2-003).

Pure functions over two payload-style audio blocks plus the validated
proposal's goal and expected directions. No bridge calls, no model calls.

Honesty rules (hard):
- Never the word "better". The result reports what moved relative to the
  stated goal and what risks appeared, from measured values only.
- A metric missing from either capture is not_measured, never inferred.
- Null means not measured, never zero.
"""

from collections.abc import Mapping

from .schemas import (
    DirectionOutcome,
    Guardrail,
    MetricDelta,
    VerificationResult,
)

# Initial conservative thresholds, expected to be tuned alongside the Phase 1
# move limits once real preview sessions exist.
NOISE_FLOOR_DB = 0.5
NOISE_FLOOR_LU = 0.5
NOISE_FLOOR_CORRELATION = 0.05
NOISE_FLOOR_FRACTION = 0.05
NOISE_FLOOR_BAND_DB = 1.0
CLIPPING_CEILING_DB = -0.3
LOUDNESS_SHIFT_WARN_LU = 3.0
CORRELATION_DROP_WARN = 0.25
BALANCE_SHIFT_WARN_DB = 3.0
SILENCE_UNAVAILABLE_FRACTION = 0.75

# The locked outcome sentences (PRODUCT_PLAN §6.3) plus the unavailable case.
OUTCOME_INTENDED = "The candidate moved in the intended direction."
OUTCOME_UNPROVEN = "The measurement changed, but not enough to prove improvement."
OUTCOME_NEW_RISK = "This introduced a new risk; keeping the original is safer."
OUTCOME_UNAVAILABLE = (
    "Verification is unavailable: the candidate capture is mostly silence."
)

_METRIC_FLOORS = {
    "sample_peak_db": NOISE_FLOOR_DB,
    "true_peak_db": NOISE_FLOOR_DB,
    "rms_db": NOISE_FLOOR_DB,
    "crest_factor_db": NOISE_FLOOR_DB,
    "integrated_lufs": NOISE_FLOOR_DB,
    "loudness_range_lu": NOISE_FLOOR_LU,
    "lufs_momentary_max": NOISE_FLOOR_DB,
    "lufs_short_term_max": NOISE_FLOOR_DB,
    "silence_fraction": NOISE_FLOOR_FRACTION,
    "stereo_correlation": NOISE_FLOOR_CORRELATION,
    "stereo_balance_db": NOISE_FLOOR_DB,
    "mid_rms_db": NOISE_FLOOR_DB,
    "side_rms_db": NOISE_FLOOR_DB,
    "spectrum_third_octave": NOISE_FLOOR_BAND_DB,
}
_STEREO_KEYS = {
    "stereo_correlation": "correlation",
    "stereo_balance_db": "balance_db",
    "mid_rms_db": "mid_rms_db",
    "side_rms_db": "side_rms_db",
}


def _finite_number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def _metric_value(audio, metric):
    """Read one supported scalar metric out of a payload-style audio block.
    Returns None for missing, null, or non-numeric values."""
    if not isinstance(audio, Mapping):
        return None
    stereo_key = _STEREO_KEYS.get(metric)
    if stereo_key is not None:
        stereo = audio.get("stereo")
        if not isinstance(stereo, Mapping):
            return None
        return _finite_number(stereo.get(stereo_key))
    return _finite_number(audio.get(metric))


def _spectrum_bands(audio):
    bands = {}
    if not isinstance(audio, Mapping):
        return bands
    for entry in audio.get("spectrum_third_octave") or []:
        if not isinstance(entry, Mapping):
            continue
        freq = _finite_number(entry.get("freq_hz"))
        level = _finite_number(entry.get("level_db"))
        if freq is not None and level is not None:
            bands[freq] = level
    return bands


def _spectrum_delta(baseline_audio, candidate_audio):
    """The largest-magnitude band change across bands present in BOTH
    captures. Returns a MetricDelta or None when no band is comparable."""
    baseline = _spectrum_bands(baseline_audio)
    candidate = _spectrum_bands(candidate_audio)
    best = None
    for freq in sorted(set(baseline) & set(candidate)):
        delta = candidate[freq] - baseline[freq]
        if best is None or abs(delta) > abs(best.delta):
            best = MetricDelta(
                metric="spectrum_third_octave",
                baseline=baseline[freq],
                candidate=candidate[freq],
                delta=delta,
                band_hz=freq,
            )
    return best


def _direction_outcome(direction, delta, floor):
    """Map a measured delta onto one goal outcome for one expected direction."""
    if delta is None:
        return "not_measured"
    if direction == "increase":
        if delta > floor:
            return "moved_as_intended"
        if delta < -floor:
            return "moved_against"
        return "moved_insufficiently"
    if direction == "decrease":
        if delta < -floor:
            return "moved_as_intended"
        if delta > floor:
            return "moved_against"
        return "moved_insufficiently"
    if direction == "not_increase":
        return "moved_against" if delta > floor else "moved_as_intended"
    if direction == "not_decrease":
        return "moved_against" if delta < -floor else "moved_as_intended"
    # "unchanged"
    return "moved_as_intended" if abs(delta) <= floor else "moved_against"


def _delta_for_metric(metric, baseline_audio, candidate_audio):
    if metric == "spectrum_third_octave":
        return _spectrum_delta(baseline_audio, candidate_audio)
    baseline = _metric_value(baseline_audio, metric)
    candidate = _metric_value(candidate_audio, metric)
    delta = None
    if baseline is not None and candidate is not None:
        delta = candidate - baseline
    if baseline is None and candidate is None:
        return None
    return MetricDelta(
        metric=metric, baseline=baseline, candidate=candidate, delta=delta
    )


def _guardrail_new_clipping(baseline_audio, candidate_audio):
    for metric in ("true_peak_db", "sample_peak_db"):
        baseline = _metric_value(baseline_audio, metric)
        candidate = _metric_value(candidate_audio, metric)
        if baseline is None or candidate is None:
            continue
        if candidate > CLIPPING_CEILING_DB and baseline <= CLIPPING_CEILING_DB:
            return Guardrail(
                name="new_clipping",
                status="fail",
                detail=(
                    f"{metric} rose to {candidate:.2f} dBFS from "
                    f"{baseline:.2f}, above the {CLIPPING_CEILING_DB} ceiling."
                ),
            )
        return Guardrail(
            name="new_clipping",
            status="pass",
            detail=f"{metric} {candidate:.2f} dBFS introduces no new clipping risk.",
        )
    return Guardrail(
        name="new_clipping", status="pass", detail="Peak level not measured in both captures."
    )


def _guardrail_loudness_shift(baseline_audio, candidate_audio):
    for metric in ("integrated_lufs", "rms_db"):
        baseline = _metric_value(baseline_audio, metric)
        candidate = _metric_value(candidate_audio, metric)
        if baseline is None or candidate is None:
            continue
        delta = candidate - baseline
        if abs(delta) > LOUDNESS_SHIFT_WARN_LU:
            return Guardrail(
                name="loudness_shift",
                status="warn",
                detail=(
                    f"{metric} shifted {delta:+.1f}; a level change this large "
                    "makes the A/B comparison unfair (louder usually reads as "
                    "preferable)."
                ),
            )
        return Guardrail(
            name="loudness_shift",
            status="pass",
            detail=f"{metric} shifted {delta:+.1f}, within the comparison window.",
        )
    return Guardrail(
        name="loudness_shift", status="pass", detail="Loudness not measured in both captures."
    )


def _guardrail_phase(baseline_audio, candidate_audio):
    baseline = _metric_value(baseline_audio, "stereo_correlation")
    candidate = _metric_value(candidate_audio, "stereo_correlation")
    if baseline is None or candidate is None:
        return Guardrail(
            name="phase", status="pass", detail="Phase correlation not measured in both captures."
        )
    drop = baseline - candidate
    if drop > CORRELATION_DROP_WARN or (baseline > 0 and candidate < 0):
        return Guardrail(
            name="phase",
            status="warn",
            detail=(
                f"Stereo correlation moved from {baseline:.2f} to "
                f"{candidate:.2f}; mono compatibility may be at risk."
            ),
        )
    return Guardrail(
        name="phase",
        status="pass",
        detail=f"Stereo correlation {candidate:.2f} holds against baseline {baseline:.2f}.",
    )


def _guardrail_stereo_balance(baseline_audio, candidate_audio):
    baseline = _metric_value(baseline_audio, "stereo_balance_db")
    candidate = _metric_value(candidate_audio, "stereo_balance_db")
    if baseline is None or candidate is None:
        return Guardrail(
            name="stereo_balance",
            status="pass",
            detail="Stereo balance not measured in both captures.",
        )
    delta = candidate - baseline
    if abs(delta) > BALANCE_SHIFT_WARN_DB:
        return Guardrail(
            name="stereo_balance",
            status="warn",
            detail=f"L/R balance shifted {delta:+.1f} dB against the baseline.",
        )
    return Guardrail(
        name="stereo_balance",
        status="pass",
        detail=f"L/R balance shifted {delta:+.1f} dB, within the window.",
    )


def _guardrail_silence(candidate_audio):
    fraction = _metric_value(candidate_audio, "silence_fraction")
    if fraction is not None and fraction >= SILENCE_UNAVAILABLE_FRACTION:
        return Guardrail(
            name="silence",
            status="fail",
            detail=(
                f"Candidate capture is {fraction:.0%} silence; its measurements "
                "cannot support a comparison."
            ),
        )
    detail = (
        "Candidate silence not measured."
        if fraction is None
        else f"Candidate capture is {fraction:.0%} silence."
    )
    return Guardrail(name="silence", status="pass", detail=detail)


def _direction_entry(entry):
    if isinstance(entry, Mapping):
        return entry.get("metric"), entry.get("direction")
    return entry.metric, entry.direction


def _outcome_sentence(available, goal_outcome, guardrails):
    if not available:
        return OUTCOME_UNAVAILABLE
    by_name = {guardrail.name: guardrail for guardrail in guardrails}
    if by_name["new_clipping"].status == "fail" \
       or by_name["phase"].status == "warn" \
       or by_name["stereo_balance"].status == "warn":
        return OUTCOME_NEW_RISK
    if goal_outcome == "moved_as_intended" \
       and by_name["loudness_shift"].status == "pass":
        return OUTCOME_INTENDED
    return OUTCOME_UNPROVEN


def evaluate(baseline_audio, candidate_audio, goal=None, expected_direction=()):
    """Score a candidate capture against its baseline for one proposal.

    baseline_audio / candidate_audio are payload-style audio blocks
    (diagnose.build_payload shape). goal is a SupportedMetric name or None;
    expected_direction is the proposal's list (models or dicts).
    """
    guardrails = [
        _guardrail_new_clipping(baseline_audio, candidate_audio),
        _guardrail_loudness_shift(baseline_audio, candidate_audio),
        _guardrail_phase(baseline_audio, candidate_audio),
        _guardrail_stereo_balance(baseline_audio, candidate_audio),
        _guardrail_silence(candidate_audio),
    ]
    silence_guardrail = guardrails[-1]
    available = silence_guardrail.status != "fail"

    deltas = []
    for metric in _METRIC_FLOORS:
        delta = _delta_for_metric(metric, baseline_audio, candidate_audio)
        if delta is not None:
            deltas.append(delta)

    delta_by_metric = {delta.metric: delta for delta in deltas}

    expected = []
    for entry in expected_direction:
        metric, direction = _direction_entry(entry)
        measured = delta_by_metric.get(metric)
        outcome = _direction_outcome(
            direction,
            measured.delta if measured is not None else None,
            _METRIC_FLOORS.get(metric, NOISE_FLOOR_DB),
        )
        expected.append(
            DirectionOutcome(metric=metric, direction=direction, outcome=outcome)
        )

    if goal is None:
        goal_outcome = "not_measured"
    else:
        goal_entries = [entry for entry in expected if entry.metric == goal]
        if goal_entries:
            outcomes = [entry.outcome for entry in goal_entries]
            if "moved_against" in outcomes:
                goal_outcome = "moved_against"
            elif "not_measured" in outcomes:
                goal_outcome = "not_measured"
            elif "moved_insufficiently" in outcomes:
                goal_outcome = "moved_insufficiently"
            else:
                goal_outcome = "moved_as_intended"
        else:
            # A goal with no stated direction can only report its delta.
            goal_outcome = "not_measured"

    if not available:
        goal_outcome = "not_measured"

    return VerificationResult(
        schema_version=1,
        available=available,
        goal_metric=goal,
        goal_outcome=goal_outcome,
        deltas=deltas,
        expected=expected,
        guardrails=guardrails,
        outcome_sentence=_outcome_sentence(available, goal_outcome, guardrails),
    )
