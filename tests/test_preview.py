"""Fake-bridge tests for the preview orchestration (P2-004).

Every state transition is exercised without REAPER: the bridge module's
functions are monkeypatched with a call-recording fake, and analyze_wav
returns synthetic TrackStats.
"""

import json
import os
import tempfile

import pytest

from postmortem import bridge, preview
from postmortem.analysis import TrackStats
from postmortem.schemas import DiagnosisResult

TRACK_GUID = "{TRACK-KICK}"


def _diagnosis(**proposal_overrides):
    proposal = {
        "operation": "set_track_volume",
        "reason": "Preview a 2 dB reduction to create headroom.",
        "target": {"track_guid": TRACK_GUID, "track_name": "Kick"},
        "current_value": {"value": -3.0, "unit": "db"},
        "proposed_value": {"value": -5.0, "unit": "db"},
        "goal": "sample_peak_db",
        "expected_direction": [
            {"metric": "sample_peak_db", "direction": "decrease"}
        ],
    }
    proposal.update(proposal_overrides)
    return DiagnosisResult.model_validate(
        {
            "schema_version": 1,
            "finding": {
                "summary": "The track is close to clipping.",
                "probable_cause": "The track output is too hot.",
                "confidence": "high",
                "confidence_reason": "The measured sample peak is -1 dBFS.",
                "evidence_refs": [
                    {"path": "audio.sample_peak_db", "description": "Measured peak."}
                ],
            },
            "proposal": proposal,
        }
    )


class FakeBridge:
    """Records every bridge call; per-test hooks inject failures."""

    def __init__(self):
        self.calls = []
        self.capture_count = 0
        self.fail_capture_at = None
        self.fail_commit = False
        self.scan_guid = TRACK_GUID
        self.routing_volume_db = -3.0
        # Candidate captures read a quieter peak so the goal moves as intended.
        self.peaks = [-1.0, -3.0, -3.0]

    def status(self):
        return "bridge alive"

    def get_context(self):
        self.calls.append("get_context")
        return {"project_name": "test", "tempo": 120, "tracks": [{"name": "Kick"}]}

    def scan_fx(self, track_name):
        self.calls.append("scan_fx")
        return {
            "tracks": [
                {"name": "Kick", "index": 1, "guid": self.scan_guid, "fx": []}
            ]
        }

    def get_track_routing(self, track_name):
        self.calls.append("get_track_routing")
        return {
            "volume_db": self.routing_volume_db,
            "pan": 0.0,
            "sends": [],
            "receives": [],
            "parent_track": None,
            "phase_inverted": False,
            "automation_mode": "trim",
            "track": {"guid": self.scan_guid},
        }

    def capture_track_audio(self, track_name, duration_seconds):
        self.capture_count += 1
        self.calls.append(f"capture_{self.capture_count}")
        if self.fail_capture_at == self.capture_count:
            raise bridge.BridgeError("render died mid-capture")
        fd, path = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        return (
            {
                "capture_scope": "isolated_track",
                "isolation_verified": True,
                "note": None,
                "track": {"guid": self.scan_guid},
            },
            path,
        )

    def cmd(self, cmd_type, payload, timeout_ms=10000):
        self.calls.append(cmd_type)
        if cmd_type == "preview_change":
            return {"preview_token": "pv-1", "snapshot_id": "snap-1"}
        if cmd_type == "cancel_preview":
            return {"preview_token": payload["preview_token"], "restored": True}
        if cmd_type == "commit_preview":
            if self.fail_commit:
                raise bridge.BridgeError("STALE_IDENTITY: FX changed mid-commit")
            return {
                "preview_token": payload["preview_token"],
                "committed": {"before": -3.0, "after": -5.0, "unit": "db"},
                "undo_point": "Post Mortem: set_track_volume on Kick",
            }
        raise AssertionError(f"unexpected bridge command {cmd_type}")


@pytest.fixture
def fake(monkeypatch):
    fake_bridge = FakeBridge()
    for name in ("status", "get_context", "scan_fx", "get_track_routing",
                 "capture_track_audio", "cmd"):
        monkeypatch.setattr(bridge, name, getattr(fake_bridge, name))

    def fake_analyze(path):
        peak = fake_bridge.peaks[min(fake_bridge.capture_count - 1,
                                     len(fake_bridge.peaks) - 1)]
        return TrackStats(
            duration_seconds=10.0,
            sample_rate=48000,
            channels=1,
            sample_peak_db=peak,
            rms_db=-18.0,
            crest_factor_db=12.0,
            spectrum_third_octave=[{"freq_hz": 100, "level_db": -18.0}],
            silence_fraction=0.05,
            stereo=None,
        )

    monkeypatch.setattr(preview, "analyze_wav", fake_analyze)
    return fake_bridge


def test_preview_runs_the_full_loop_and_always_restores(fake):
    report = preview.run_preview(_diagnosis(), seconds=10)

    assert fake.calls.index("preview_change") < fake.calls.index("capture_2")
    assert fake.calls.index("capture_2") < fake.calls.index("cancel_preview")
    assert report["restored"] is True
    assert report["verification"]["goal_outcome"] == "moved_as_intended"
    assert report["verification"]["outcome_sentence"] == (
        "The candidate moved in the intended direction."
    )
    assert report["adjustment"] == {
        "minimum": -6.0,
        "maximum": 0.0,
        "step": 0.1,
        "value": -5.0,
        "unit": "db",
    }


def test_candidate_capture_failure_still_cancels_the_preview(fake):
    fake.fail_capture_at = 2

    with pytest.raises(bridge.BridgeError):
        preview.run_preview(_diagnosis(), seconds=10)

    assert "preview_change" in fake.calls
    assert "cancel_preview" in fake.calls, "restore must run even when capture dies"


def test_stale_track_identity_refuses_before_any_mutation(fake):
    fake.scan_guid = "{TRACK-OTHER}"

    with pytest.raises(preview.PreviewRefused) as refusal:
        preview.run_preview(_diagnosis(), seconds=10)

    assert refusal.value.code == "track_identity_mismatch"
    assert "preview_change" not in fake.calls
    assert "cancel_preview" not in fake.calls


def test_drifted_current_value_refuses_before_any_mutation(fake):
    fake.routing_volume_db = -1.0

    with pytest.raises(preview.PreviewRefused) as refusal:
        preview.run_preview(_diagnosis(), seconds=10)

    assert refusal.value.code == "current_value_mismatch"
    assert "preview_change" not in fake.calls


def test_non_actionable_diagnosis_refuses_without_touching_the_bridge(fake):
    result = _diagnosis().model_copy(deep=True)
    result.proposal.operation = "none"
    result.proposal.target = None
    result.proposal.current_value = None
    result.proposal.proposed_value = None
    result.proposal.goal = None
    result.proposal.expected_direction = []
    result = DiagnosisResult.model_validate(result.model_dump())

    with pytest.raises(preview.PreviewRefused) as refusal:
        preview.run_preview(result, seconds=10)

    assert refusal.value.code == "not_actionable"
    assert fake.calls == []


def test_temp_stems_are_deleted_unless_kept(fake, monkeypatch):
    created = []
    original = fake.capture_track_audio

    def tracking_capture(track_name, duration_seconds):
        data, path = original(track_name, duration_seconds)
        created.append(path)
        return data, path

    monkeypatch.setattr(bridge, "capture_track_audio", tracking_capture)

    preview.run_preview(_diagnosis(), seconds=10)
    assert created and all(not os.path.exists(p) for p in created)

    created.clear()
    report = preview.run_preview(_diagnosis(), seconds=10, keep_wav=True)
    try:
        assert created and all(os.path.exists(p) for p in created)
        assert report["wav_paths"]["baseline"] in created
    finally:
        for p in created:
            try:
                os.unlink(p)
            except OSError:
                pass


def test_text_and_json_reports_agree(fake):
    report = preview.run_preview(_diagnosis(), seconds=10)
    text = preview.render_preview_text(report)

    assert report["verification"]["outcome_sentence"] in text
    assert "restored to its baseline" in text
    assert "sample_peak_db" in text
    assert json.dumps(report)  # JSON-serializable as printed by --format json
    assert "better" not in text.lower()


def test_commit_runs_preview_then_commit_and_reports_the_undo_point(fake):
    report = preview.run_commit(_diagnosis())

    assert fake.calls.index("preview_change") < fake.calls.index("commit_preview")
    assert "cancel_preview" not in fake.calls
    assert report["undo_point"] == "Post Mortem: set_track_volume on Kick"
    # Commit now captures a baseline for full validate_proposal revalidation,
    # mirroring run_preview's gate.
    assert "capture_1" in fake.calls, "commit must capture for revalidation"
    text = preview.render_commit_text(report)
    assert "Undo point" in text


def test_commit_failure_cancels_the_temporary_change(fake):
    fake.fail_commit = True

    with pytest.raises(bridge.BridgeError):
        preview.run_commit(_diagnosis())

    assert "cancel_preview" in fake.calls
def test_commit_refuses_on_drifted_volume(fake):
    fake.routing_volume_db = -1.0

    with pytest.raises(preview.PreviewRefused) as refusal:
        preview.run_commit(_diagnosis())

    # validate_proposal uses "current_value_mismatch" (not "current_value_drift"
    # from the old _check_value_drift), matching run_preview's behavior.
    assert refusal.value.code == "current_value_mismatch"
    assert "preview_change" not in fake.calls


def test_bad_diagnosis_json_is_a_typed_refusal():
    with pytest.raises(preview.PreviewRefused) as refusal:
        preview.load_diagnosis("{not json")

    assert refusal.value.code == "bad_diagnosis"
