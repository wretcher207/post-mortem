"""Deterministic validation for model-proposed Track Check changes."""

from collections.abc import Mapping
import math
import re

from .schemas import DiagnosisResult, Proposal


_MISSING = object()
_PATH_SEGMENT = re.compile(r"([A-Za-z_][A-Za-z0-9_-]*)(?:\[(\d+)\])?")
_DB_CURRENT_TOLERANCE = 0.1
_NORMALIZED_CURRENT_TOLERANCE = 0.001
SUPPORTED_METRICS = frozenset(
    {
        "sample_peak_db",
        "true_peak_db",
        "rms_db",
        "crest_factor_db",
        "integrated_lufs",
        "loudness_range_lu",
        "lufs_momentary_max",
        "lufs_short_term_max",
        "silence_fraction",
        "stereo_correlation",
        "stereo_balance_db",
        "mid_rms_db",
        "side_rms_db",
        "spectrum_third_octave",
    }
)


def _resolve_payload_path(payload, path):
    current = payload
    normalized = path[2:] if path.startswith("$.") else path
    for raw_segment in normalized.split("."):
        match = _PATH_SEGMENT.fullmatch(raw_segment)
        if not match or not isinstance(current, Mapping):
            return _MISSING
        key, index = match.groups()
        if key not in current:
            return _MISSING
        current = current[key]
        if index is not None:
            if not isinstance(current, list) or int(index) >= len(current):
                return _MISSING
            current = current[int(index)]
    return current


def _reject(result, reason):
    proposal = Proposal(
        operation="none",
        reason=result.proposal.reason,
        expected_direction=[],
        rejection_reason=reason,
    )
    return result.model_copy(update={"proposal": proposal})


def _matching_fx(payload, target):
    chain = payload.get("fx_chain")
    if not isinstance(chain, list):
        return None
    for fx in chain:
        if not isinstance(fx, Mapping):
            continue
        if (
            fx.get("guid") == target.fx_guid
            and fx.get("index") == target.fx_index
            and fx.get("scope") == target.fx_scope
            and fx.get("name") == target.fx_name
        ):
            return fx
    return None


def _matching_parameter(fx, target):
    parameters = fx.get("parameters")
    if not isinstance(parameters, list):
        return None
    for parameter in parameters:
        if not isinstance(parameter, Mapping):
            continue
        if (
            parameter.get("index") == target.parameter_index
            and parameter.get("name") == target.parameter_name
        ):
            return parameter
    return None


def _evidence_cites_fx(result, payload, fx):
    chain = payload.get("fx_chain")
    position = next((i for i, entry in enumerate(chain) if entry is fx), None)
    if position is None:
        return False
    prefix = f"fx_chain[{position}]"
    for evidence in result.finding.evidence_refs:
        path = evidence.path[2:] if evidence.path.startswith("$.") else evidence.path
        if path == prefix or path.startswith(prefix + "."):
            return True
    return False


def _states_preview_not_deletion(reason):
    text = " ".join(reason.lower().split())
    non_destructive = any(
        phrase in text
        for phrase in (
            "not delete",
            "not a deletion",
            "not remove",
            "does not delete",
            "does not remove",
            "without deleting",
            "without removing",
        )
    )
    return "preview" in text and non_destructive


def validate_proposal(
    result: DiagnosisResult,
    payload: Mapping,
) -> DiagnosisResult:
    """Return the result when its proposal is safe for later preview."""
    actionable = result.proposal.operation != "none"
    if not result.finding.evidence_refs:
        return _reject(result, "evidence_missing") if actionable else result
    for evidence in result.finding.evidence_refs:
        value = _resolve_payload_path(payload, evidence.path)
        if value is _MISSING:
            return _reject(result, "evidence_path_missing")
        if value is None:
            return _reject(result, "evidence_value_null")
    if not actionable:
        return result
    if result.proposal.goal not in SUPPORTED_METRICS:
        return _reject(result, "unsupported_goal")
    if any(
        expected.metric not in SUPPORTED_METRICS
        for expected in result.proposal.expected_direction
    ):
        return _reject(result, "unsupported_metric")
    target = result.proposal.target
    track = payload.get("track")
    if (
        target is None
        or not isinstance(track, Mapping)
        or target.track_guid != track.get("guid")
    ):
        return _reject(result, "track_identity_mismatch")
    fx = None
    parameter = None
    if result.proposal.operation in {"set_fx_param", "set_fx_bypass"}:
        fx = _matching_fx(payload, target)
        if fx is None:
            return _reject(result, "fx_identity_mismatch")
    if result.proposal.operation == "set_fx_param":
        parameter = _matching_parameter(fx, target)
        if parameter is None:
            return _reject(result, "parameter_identity_mismatch")
        actual = parameter.get("normalized_value")
        current = result.proposal.current_value
        if (
            not isinstance(actual, (int, float))
            or current is None
            or not math.isclose(
                float(current.value),
                float(actual),
                rel_tol=0.0,
                abs_tol=_NORMALIZED_CURRENT_TOLERANCE,
            )
        ):
            return _reject(result, "current_value_mismatch")
        display_known = bool(str(parameter.get("formatted_value") or "").strip())
        move_limit = 0.20 if display_known else 0.10
        proposed = result.proposal.proposed_value
        if proposed is None or abs(
            float(proposed.value) - float(current.value)
        ) > move_limit:
            return _reject(result, "move_limit_exceeded")
    if result.proposal.operation == "set_fx_bypass":
        enabled = fx.get("enabled")
        current = result.proposal.current_value
        if (
            not isinstance(enabled, bool)
            or current is None
            or current.value is not (not enabled)
        ):
            return _reject(result, "current_value_mismatch")
        if not _evidence_cites_fx(result, payload, fx):
            return _reject(result, "fx_bypass_evidence_missing")
        if not _states_preview_not_deletion(result.proposal.reason):
            return _reject(result, "fx_bypass_not_preview")
    if result.proposal.operation == "set_track_volume":
        actual = track.get("volume_db")
        current = result.proposal.current_value
        if (
            not isinstance(actual, (int, float))
            or current is None
            or not math.isclose(
                float(current.value),
                float(actual),
                rel_tol=0.0,
                abs_tol=_DB_CURRENT_TOLERANCE,
            )
        ):
            return _reject(result, "current_value_mismatch")
        proposed = result.proposal.proposed_value
        if proposed is None or abs(
            float(proposed.value) - float(current.value)
        ) > 3.0:
            return _reject(result, "move_limit_exceeded")
    if result.proposal.operation == "set_track_pan":
        actual = track.get("pan")
        current = result.proposal.current_value
        if (
            not isinstance(actual, (int, float))
            or current is None
            or not math.isclose(
                float(current.value),
                float(actual),
                rel_tol=0.0,
                abs_tol=_NORMALIZED_CURRENT_TOLERANCE,
            )
        ):
            return _reject(result, "current_value_mismatch")
        proposed = result.proposal.proposed_value
        if proposed is None or abs(
            float(proposed.value) - float(current.value)
        ) > 0.20:
            return _reject(result, "move_limit_exceeded")
    return result
