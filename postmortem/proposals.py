"""Deterministic validation for model-proposed Track Check changes."""

from collections.abc import Mapping
import math
import re
from typing import get_args

from .schemas import DiagnosisResult, Proposal, SupportedMetric


_MISSING = object()
_PATH_SEGMENT = re.compile(r"([A-Za-z_][A-Za-z0-9_-]*)(?:\[(\d+)\])?")
_DB_CURRENT_TOLERANCE = 0.1
_NORMALIZED_CURRENT_TOLERANCE = 0.001
SUPPORTED_METRICS = frozenset(get_args(SupportedMetric))
_CROSS_TRACK_DISCLAIMER_PATTERNS = (
    re.compile(
        r"\b(?:cannot|can't|could not|unable to|do not|don't)\s+"
        r"(?:infer|diagnose|establish|verify|conclude)\b[^.!?;]{0,60}"
        r"\b(?:mask(?:s|ed|ing)?|cross-track)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:cannot|can't|could not|unable to|impossible to|not possible to)\b"
        r"[^.!?;]{0,40}\b(?:determine|assess|know|tell)\s+"
        r"(?:whether|if)\b[^.!?;]{0,60}"
        r"\b(?:mask(?:s|ed|ing)?|cross-track)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:no|insufficient|not enough)\s+"
        r"(?:evidence|data|context|information)\s+"
        r"(?:of|for|to\s+support|that\s+supports)\b[^.!?;]{0,40}"
        r"\b(?:mask(?:s|ed|ing)?|cross-track)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:mask(?:s|ed|ing)?|cross-track\s+(?:relationship|claim))\b"
        r"[^.!?;]{0,60}\b(?:(?:is|are)\s+not\s+"
        r"(?:supported|established|verified|knowable)|"
        r"(?:cannot|can't)\s+be\s+"
        r"(?:inferred|determined|diagnosed|established|verified))\b",
        re.IGNORECASE,
    ),
)
_CROSS_TRACK_CLAIM_PATTERNS = (
    re.compile(r"\bmask(?:s|ed|ing)?\b", re.IGNORECASE),
    re.compile(
        r"\b(?:clash(?:es|ed|ing)?|compet(?:e|es|ed|ing)|"
        r"fight(?:s|ing)?|conflict(?:s|ed|ing)?|collid(?:e|es|ed|ing)|"
        r"interfer(?:e|es|ed|ing)|overlap(?:s|ped|ping)?)\s+with\b",
        re.IGNORECASE,
    ),
    re.compile(r"\bbur(?:y|ies|ied)\s+(?:beneath|under)\b", re.IGNORECASE),
    re.compile(
        r"\b(?:bur(?:y|ies|ied)|obscur(?:e|es|ed|ing)|cover(?:s|ed|ing)?|"
        r"drown(?:s|ed|ing)?|"
        r"overpower(?:s|ed|ing)?|dominat(?:e|es|ed|ing)|"
        r"smother(?:s|ed|ing)?)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:crowds?\s+out|steps?\s+on|sits?\s+against|"
        r"gets?\s+lost\s+(?:behind|under))\b",
        re.IGNORECASE,
    ),
)
_CLAUSE_BOUNDARY = re.compile(
    r"[.!?;,]|\b(?:and|but|however|yet|although|nevertheless|or)\b",
    re.IGNORECASE,
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


def _reject(result, reason, proposal_reason=None):
    proposal_reason = proposal_reason or result.proposal.reason
    if result.proposal.operation == "set_fx_param":
        proposal_reason = (
            "The proposed normalized FX parameter change was rejected by "
            "deterministic validation."
        )
    proposal = Proposal(
        operation="none",
        reason=proposal_reason,
        expected_direction=[],
        rejection_reason=reason,
    )
    return result.model_copy(update={"proposal": proposal})


def _with_low_confidence(result, reason):
    finding = result.finding.model_copy(
        update={"confidence": "low", "confidence_reason": reason}
    )
    return result.model_copy(update={"finding": finding})


def _contains_cross_track_claim(result):
    model_fields = filter(
        None,
        (
            result.finding.summary,
            result.finding.probable_cause,
            result.finding.confidence_reason,
            result.proposal.reason,
            *(reference.description for reference in result.finding.evidence_refs),
        ),
    )
    for model_field in model_fields:
        for clause in _CLAUSE_BOUNDARY.split(model_field):
            claim_matches = [
                match
                for pattern in _CROSS_TRACK_CLAIM_PATTERNS
                if (match := pattern.search(clause)) is not None
            ]
            for claim in claim_matches:
                disclaimer_matches = (
                    match
                    for pattern in _CROSS_TRACK_DISCLAIMER_PATTERNS
                    if (match := pattern.search(clause)) is not None
                )
                if any(
                    disclaimer.start() <= claim.start()
                    and disclaimer.end() >= claim.end()
                    for disclaimer in disclaimer_matches
                ):
                    continue
                return True
    return False


def has_verified_isolated_capture(payload: Mapping) -> bool:
    """Return whether payload provenance can support a track diagnosis."""
    capture = payload.get("capture")
    return bool(
        isinstance(capture, Mapping)
        and capture.get("scope") == "isolated_track"
        and capture.get("isolation_verified") is True
    )


def _reject_unverified_capture(result):
    finding = result.finding.model_copy(
        update={
            "summary": "No track diagnosis is available for this capture.",
            "probable_cause": (
                "The payload does not prove that the audio came from one "
                "isolated track."
            ),
            "confidence": "low",
            "confidence_reason": (
                "Isolated-track capture provenance is required before diagnosis."
            ),
            "evidence_refs": [],
        }
    )
    proposal = Proposal(
        operation="none",
        reason="Unverified capture provenance cannot support a safe change.",
        expected_direction=[],
        rejection_reason="capture_not_isolated",
    )
    return result.model_copy(update={"finding": finding, "proposal": proposal})


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


def _expected_metric_reason(proposal):
    expected = next(
        (
            item
            for item in proposal.expected_direction
            if item.metric == proposal.goal
        ),
        proposal.expected_direction[0],
    )
    predicate = {
        "increase": "increases",
        "decrease": "decreases",
        "not_increase": "does not increase",
        "not_decrease": "does not decrease",
        "unchanged": "stays unchanged",
    }[expected.direction]
    return f"to test whether {expected.metric} {predicate}"


def validate_proposal(
    result: DiagnosisResult,
    payload: Mapping,
) -> DiagnosisResult:
    """Return the result when its proposal is safe for later preview."""
    actionable = result.proposal.operation != "none"
    if not has_verified_isolated_capture(payload):
        return _reject_unverified_capture(result)
    if _contains_cross_track_claim(result):
        finding = result.finding.model_copy(
            update={
                "summary": (
                    "The provider response exceeded the single-track evidence boundary."
                ),
                "probable_cause": (
                    "A single isolated track cannot establish cross-track relationships."
                ),
                "confidence": "low",
                "confidence_reason": (
                    "The unsupported cross-track claim was removed by validation."
                ),
                "evidence_refs": [],
            }
        )
        result = result.model_copy(update={"finding": finding})
        return _reject(
            result,
            "cross_track_claim",
            proposal_reason=(
                "A single-track capture cannot support a cross-track change."
            ),
        )
    audio = payload.get("audio")
    silence_fraction = (
        audio.get("silence_fraction") if isinstance(audio, Mapping) else None
    )
    if (
        isinstance(silence_fraction, (int, float))
        and not isinstance(silence_fraction, bool)
        and math.isfinite(float(silence_fraction))
        and silence_fraction >= 0.75
    ):
        result = _with_low_confidence(
            result,
            "Confidence is capped at low because audio.silence_fraction is "
            f"{float(silence_fraction):.3f}.",
        )
        if actionable:
            return _reject(result, "insufficient_signal")
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
        # A current formatted value is only one point, not a verified mapping
        # between normalized and displayed values. The payload has no mapping
        # metadata yet, so all Phase 1 FX parameter moves use the strict default.
        move_limit = 0.10
        proposed = result.proposal.proposed_value
        if proposed is None or abs(
            float(proposed.value) - float(current.value)
        ) > move_limit:
            return _reject(result, "move_limit_exceeded")
        sanitized = result.proposal.model_copy(deep=True)
        payload_display = str(parameter.get("formatted_value") or "").strip()
        if sanitized.current_value.display != payload_display:
            sanitized.current_value.display = None
        sanitized.proposed_value.display = None
        sanitized.reason = (
            f"Preview changing {target.fx_name} / {target.parameter_name} from "
            f"normalized {float(current.value):.3f} to "
            f"{float(proposed.value):.3f} {_expected_metric_reason(sanitized)}."
        )
        result = result.model_copy(update={"proposal": sanitized})
    if result.proposal.operation == "set_fx_bypass":
        enabled = fx.get("enabled")
        current = result.proposal.current_value
        proposed = result.proposal.proposed_value
        if (
            not isinstance(enabled, bool)
            or current is None
            or current.value is not (not enabled)
        ):
            return _reject(result, "current_value_mismatch")
        if proposed is None or proposed.value is current.value:
            return _reject(result, "proposed_value_unchanged")
        if not _evidence_cites_fx(result, payload, fx):
            return _reject(result, "fx_bypass_evidence_missing")
        if not _states_preview_not_deletion(result.proposal.reason):
            return _reject(result, "fx_bypass_not_preview")
        sanitized = result.proposal.model_copy(deep=True)
        action = "bypassing" if proposed.value else "enabling"
        sanitized.reason = (
            f"Preview {action} {target.fx_name}; this does not remove or delete "
            f"the plugin; {_expected_metric_reason(sanitized)}."
        )
        result = result.model_copy(update={"proposal": sanitized})
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
        sanitized = result.proposal.model_copy(deep=True)
        sanitized.reason = (
            "Preview changing track volume from "
            f"{float(current.value):.3f} dB to {float(proposed.value):.3f} dB "
            f"{_expected_metric_reason(sanitized)}."
        )
        result = result.model_copy(update={"proposal": sanitized})
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
        sanitized = result.proposal.model_copy(deep=True)
        sanitized.reason = (
            "Preview changing track pan from "
            f"{float(current.value):.3f} to {float(proposed.value):.3f} "
            f"{_expected_metric_reason(sanitized)}."
        )
        result = result.model_copy(update={"proposal": sanitized})
    return result
