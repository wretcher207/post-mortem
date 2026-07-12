"""Preview orchestration (Phase 2, P2-004).

Turns a validated DiagnosisResult proposal into a heard, measured
before/after comparison without ever leaving the project mutated:

    fresh scan -> revalidate -> snapshot+apply (bridge preview_change)
    -> candidate capture -> restore (bridge cancel_preview, ALWAYS)
    -> deterministic verification report

Commit is a separate explicit action: it re-validates identities and current
values against a fresh scan, then runs preview_change + commit_preview
back-to-back, producing exactly one named undo point.
"""

import os

from . import bridge
from .analysis import analyze_wav
from .diagnose import build_payload
from .proposals import validate_proposal
from .schemas import DiagnosisResult
from .verification import evaluate

_DB_DRIFT_TOLERANCE = 0.1
_NORMALIZED_DRIFT_TOLERANCE = 0.001


class PreviewRefused(Exception):
    """A refusal with a stable machine-readable code and a human message."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _actionable_proposal(result: DiagnosisResult):
    proposal = result.proposal
    if proposal.operation == "none":
        raise PreviewRefused(
            "not_actionable",
            "the diagnosis carries no previewable proposal (operation: none"
            + (f", rejection: {proposal.rejection_reason}" if proposal.rejection_reason else "")
            + ").",
        )
    if proposal.rejection_reason is not None:
        raise PreviewRefused(
            "not_actionable",
            f"the proposal was rejected by validation ({proposal.rejection_reason}).",
        )
    if proposal.target is None or proposal.target.track_guid is None:
        raise PreviewRefused(
            "not_actionable", "the proposal has no track identity to act on."
        )
    return proposal


def _preview_change_payload(proposal):
    target = proposal.target
    payload_target = {"track_guid": target.track_guid}
    for field in ("track_name", "fx_guid", "fx_index", "fx_scope", "fx_name",
                  "parameter_index", "parameter_name"):
        value = getattr(target, field, None)
        if value is not None:
            payload_target[field] = value
    return {
        "operation": proposal.operation,
        "target": payload_target,
        "proposed_value": proposal.proposed_value.value,
    }


def _fresh_single_track_state(track_name):
    """One fresh look at the live project for the target track."""
    context = bridge.get_context()
    track_scan = bridge.scan_fx(track_name)
    routing = bridge.get_track_routing(track_name)
    return context, track_scan, routing


def _capture_audio_block(context, track_scan, routing, track_name, seconds):
    """Capture + analyze one stem and return (payload, wav_path)."""
    capture_data, wav_path = bridge.capture_track_audio(
        track_name, duration_seconds=seconds
    )
    stats = analyze_wav(wav_path)
    payload = build_payload(
        context, track_scan, routing, capture_data, stats, target_name=track_name
    )
    return payload, wav_path


def _resolved_track_name(proposal):
    name = proposal.target.track_name
    if not name:
        raise PreviewRefused(
            "not_actionable",
            "the proposal target carries no track name; re-run the diagnosis.",
        )
    return name


def _check_value_drift(proposal, routing, track_scan):
    """Refuse when the live current value no longer matches the diagnosis.

    The bridge re-verifies identities (STALE_IDENTITY); this is the value
    half: a knob that moved since the diagnosis makes proposed_value a
    different-sized move than the model reasoned about.
    """
    operation = proposal.operation
    current = proposal.current_value.value if proposal.current_value else None
    if current is None:
        return
    if operation == "set_track_volume":
        live = routing.get("volume_db")
        if live is None or abs(float(live) - float(current)) > _DB_DRIFT_TOLERANCE:
            raise PreviewRefused(
                "current_value_drift",
                f"track volume is now {live} dB; the diagnosis said {current} dB. "
                "Re-run the diagnosis.",
            )
    elif operation == "set_track_pan":
        live = routing.get("pan")
        if live is None or abs(float(live) - float(current)) > _NORMALIZED_DRIFT_TOLERANCE:
            raise PreviewRefused(
                "current_value_drift",
                f"track pan is now {live}; the diagnosis said {current}. "
                "Re-run the diagnosis.",
            )
    elif operation == "set_fx_bypass":
        entry = _scan_fx_entry(track_scan, proposal.target)
        if entry is None:
            raise PreviewRefused(
                "stale_identity", "the proposal's FX is no longer in the chain."
            )
        live_bypassed = entry.get("enabled") is False
        if live_bypassed != bool(current):
            raise PreviewRefused(
                "current_value_drift",
                "the FX bypass state changed since the diagnosis. Re-run it.",
            )
    elif operation == "set_fx_param":
        live = _live_parameter_value(proposal.target)
        if live is None or abs(float(live) - float(current)) > _NORMALIZED_DRIFT_TOLERANCE:
            raise PreviewRefused(
                "current_value_drift",
                f"the parameter is now {live}; the diagnosis said {current}. "
                "Re-run the diagnosis.",
            )


def _scan_fx_entry(track_scan, target):
    tracks = track_scan.get("tracks") or [{}]
    for entry in tracks[0].get("fx", []) or []:
        if entry.get("guid") == target.fx_guid:
            return entry
    return None


def _live_parameter_value(target):
    data = bridge.cmd(
        "get_fx_parameters",
        {
            "target_track_guid": target.track_guid,
            "fx_index": target.fx_index,
            "fx_scope": target.fx_scope,
            "include_empty": True,
            "limit": 2000,
        },
    )
    for param in data.get("parameters", []) or []:
        if param.get("index") == target.parameter_index:
            return param.get("normalized_value")
    return None


def load_diagnosis(text):
    try:
        return DiagnosisResult.model_validate_json(text)
    except Exception as error:
        raise PreviewRefused(
            "bad_diagnosis", f"not a valid DiagnosisResult JSON document: {error}"
        ) from None


def run_preview(result: DiagnosisResult, seconds, keep_wav=False):
    """The safe A/B loop. Returns a JSON-ready report dict.

    The project is ALWAYS restored before this returns — the bridge preview is
    cancelled in a finally block, and the report is only assembled after the
    restore succeeded. Temp stems are deleted on every path unless keep_wav.
    """
    proposal = _actionable_proposal(result)
    track_name = _resolved_track_name(proposal)

    wav_paths = []
    try:
        context, track_scan, routing = _fresh_single_track_state(track_name)
        baseline_payload, baseline_wav = _capture_audio_block(
            context, track_scan, routing, track_name, seconds
        )
        wav_paths.append(baseline_wav)

        # Full Phase 1 revalidation against the FRESH payload: stale
        # identities, drifted current values, missing evidence, and unsafe
        # move sizes all refuse here, before any mutation, with the Phase 1
        # rejection vocabulary.
        revalidated = validate_proposal(result, baseline_payload)
        if revalidated.proposal.operation == "none":
            raise PreviewRefused(
                revalidated.proposal.rejection_reason or "revalidation_failed",
                "the live project no longer supports this proposal "
                f"({revalidated.proposal.rejection_reason}). Re-run the diagnosis.",
            )

        preview = bridge.cmd("preview_change", _preview_change_payload(proposal))
        token = preview.get("preview_token")
        try:
            candidate_payload, candidate_wav = _capture_audio_block(
                context, track_scan, routing, track_name, seconds
            )
            wav_paths.append(candidate_wav)
        finally:
            # Restore ALWAYS — an exception between apply and restore must
            # not leave the preview value in the project. Cancel failure is
            # loud: the bridge's startup recovery is the backstop, but we do
            # not bury it.
            bridge.cmd("cancel_preview", {"preview_token": token})

        verification = evaluate(
            baseline_payload["audio"],
            candidate_payload["audio"],
            goal=proposal.goal,
            expected_direction=proposal.expected_direction,
        )
        return {
            "schema_version": 1,
            "preview_token": token,
            "restored": True,
            "operation": proposal.operation,
            "track": track_name,
            "proposal_reason": proposal.reason,
            "current_value": proposal.current_value.model_dump() if proposal.current_value else None,
            "proposed_value": proposal.proposed_value.model_dump() if proposal.proposed_value else None,
            "verification": verification.model_dump(),
            "wav_paths": {"baseline": baseline_wav, "candidate": candidate_wav}
            if keep_wav
            else None,
        }
    finally:
        if not keep_wav:
            for path in wav_paths:
                try:
                    os.unlink(path)
                except OSError:
                    pass


def run_commit(result: DiagnosisResult):
    """Explicit apply: fresh identity + value verification, then
    preview_change + commit_preview back-to-back. Exactly one named undo
    point (the bridge commit's), nothing else in undo history."""
    proposal = _actionable_proposal(result)
    track_name = _resolved_track_name(proposal)

    _, track_scan, routing = _fresh_single_track_state(track_name)
    _check_value_drift(proposal, routing, track_scan)

    preview = bridge.cmd("preview_change", _preview_change_payload(proposal))
    token = preview.get("preview_token")
    try:
        committed = bridge.cmd("commit_preview", {"preview_token": token})
    except bridge.BridgeError:
        # A failed commit must not leave the temporary value applied.
        bridge.cmd("cancel_preview", {"preview_token": token})
        raise
    return {
        "schema_version": 1,
        "preview_token": token,
        "operation": proposal.operation,
        "track": track_name,
        "committed": committed.get("committed"),
        "undo_point": committed.get("undo_point"),
    }


def render_preview_text(report):
    """Human report from the same structured dict --format json prints."""
    verification = report["verification"]
    lines = [
        f"PREVIEW: {report['operation']} on {report['track']}",
        f"  {report['proposal_reason']}",
        "  The project was restored to its baseline; nothing was kept.",
        "",
        f"OUTCOME: {verification['outcome_sentence']}",
    ]
    goal = verification.get("goal_metric")
    if goal:
        lines.append(f"GOAL: {goal} — {verification['goal_outcome'].replace('_', ' ')}")
    lines.append("GUARDRAILS:")
    for guardrail in verification["guardrails"]:
        lines.append(
            f"  [{guardrail['status']}] {guardrail['name']}: {guardrail['detail']}"
        )
    interesting = {goal} | {e["metric"] for e in verification["expected"]}
    deltas = [d for d in verification["deltas"] if d["metric"] in interesting]
    if deltas:
        lines.append("MEASURED:")
        for delta in deltas:
            if delta.get("delta") is None:
                lines.append(f"  {delta['metric']}: not measured in both captures")
                continue
            band = f" @ {delta['band_hz']:g} Hz" if delta.get("band_hz") else ""
            lines.append(
                f"  {delta['metric']}{band}: {delta['baseline']:+.2f} -> "
                f"{delta['candidate']:+.2f} (delta {delta['delta']:+.2f})"
            )
    lines.append("")
    lines.append(
        "COMMIT: postmortem commit <diagnosis.json> applies this move with one "
        "undo point. Your ear decides; the numbers above only frame the A/B."
    )
    return "\n".join(lines)


def render_commit_text(report):
    committed = report.get("committed") or {}
    after = committed.get("after")
    return "\n".join(
        [
            f"COMMITTED: {report['operation']} on {report['track']}"
            + (f" -> {after}" if after is not None else ""),
            f"  Undo point: {report.get('undo_point')}",
            "  One Ctrl+Z returns the project to its pre-preview state.",
        ]
    )
