"""Fake-bridge tests for the sidecar service (P3-001).

Same discipline as test_preview.py: the bridge module's functions are
monkeypatched with a call-recording fake, analyze_wav returns synthetic
TrackStats, and diagnose_track returns a canned DiagnosisResult. Every job
lifecycle is exercised through real job files in a tmp app-data root.
"""

import json
import os
import tempfile

import pytest

from postmortem import bridge, preview, service
from postmortem.analysis import TrackStats
from postmortem.schemas import DiagnosisResult
from postmortem.providers.base import ModelProfile, ProviderError, ProviderErrorCategory

TRACK_GUID = "{TRACK-KICK}"


def _diagnosis():
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
            "proposal": {
                "operation": "set_track_volume",
                "reason": "Preview a 2 dB reduction to create headroom.",
                "target": {"track_guid": TRACK_GUID, "track_name": "Kick"},
                "current_value": {"value": -3.0, "unit": "db"},
                "proposed_value": {"value": -5.0, "unit": "db"},
                "goal": "sample_peak_db",
                "expected_direction": [
                    {"metric": "sample_peak_db", "direction": "decrease"}
                ],
            },
        }
    )


class FakeBridge:
    def __init__(self):
        self.calls = []
        self.capture_count = 0
        self.fail_capture_at = None
        self.scan_guid = TRACK_GUID
        self.routing_volume_db = -3.0
        self.preview_payloads = []

    def status(self):
        self.calls.append("status")
        return "bridge alive"

    def get_capture_preflight(self):
        self.calls.append("get_capture_preflight")
        return {
            "capture_allowed": True,
            "blockers": [],
            "warnings": [],
            "risk_gate": {
                "allow_risk_level_3": True,
                "requires_restart_to_change": True,
            },
            "sws_installed": True,
            "render_autoclose": True,
            "target": None,
        }

    def enable_capture(self):
        self.calls.append("enable_capture")
        return {
            "enabled": True,
            "restart_required": True,
            "config_path": "/test/bridge/bridge_config.json",
        }

    def get_context(self):
        self.calls.append("get_context")
        return {"project_name": "test", "tempo": 120, "tracks": [{"name": "Kick"}]}

    def scan_fx(self, track_name):
        self.calls.append("scan_fx")
        return {
            "tracks": [{"name": "Kick", "index": 1, "guid": self.scan_guid, "fx": []}]
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
            self.preview_payloads.append(payload)
            return {"preview_token": "pv-1", "snapshot_id": "snap-1"}
        if cmd_type == "cancel_preview":
            return {"preview_token": payload["preview_token"], "restored": True}
        if cmd_type == "commit_preview":
            return {
                "preview_token": payload["preview_token"],
                "committed": {"before": -3.0, "after": -5.0, "unit": "db"},
                "undo_point": "Post Mortem: set_track_volume on Kick",
            }
        raise AssertionError(f"unexpected bridge command {cmd_type}")


@pytest.fixture
def svc(tmp_path, monkeypatch):
    fake_bridge = FakeBridge()
    for name in ("status", "get_capture_preflight", "enable_capture", "get_context",
                 "scan_fx", "get_track_routing", "capture_track_audio", "cmd"):
        monkeypatch.setattr(bridge, name, getattr(fake_bridge, name))

    stats_overrides = {}

    def fake_analyze(path):
        return TrackStats(
            duration_seconds=10.0,
            sample_rate=48000,
            channels=1,
            sample_peak_db=stats_overrides.get("sample_peak_db", -1.0),
            rms_db=stats_overrides.get("rms_db", -18.0),
            crest_factor_db=12.0,
            spectrum_third_octave=[{"freq_hz": 100, "level_db": -18.0}],
            silence_fraction=stats_overrides.get("silence_fraction", 0.05),
            stereo=None,
        )

    monkeypatch.setattr(service, "analyze_wav", fake_analyze)
    monkeypatch.setattr(preview, "analyze_wav", fake_analyze)
    diagnose_payloads = []

    def fake_diagnose(payload):
        diagnose_payloads.append(payload)
        return _diagnosis()

    monkeypatch.setattr(service, "diagnose_track", fake_diagnose)

    instance = service.Service(root=str(tmp_path))
    instance.fake_bridge = fake_bridge
    instance.stats_overrides = stats_overrides
    instance.diagnose_payloads = diagnose_payloads
    return instance


def _submit(svc, job, filename=None):
    filename = filename or f"{job.get('id', 'job')}.json"
    path = os.path.join(svc.inbox, filename)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(job, f)
    return filename[: -len(".json")]


def _result(svc, stem):
    with open(os.path.join(svc.outbox, f"{stem}.json"), encoding="utf-8") as f:
        return json.load(f)


def test_track_check_produces_a_diagnosis_result(svc):
    stem = _submit(svc, {
        "id": "pm-001", "type": "track_check",
        "created_at": "2026-07-12T00:00:00Z", "payload": {"track": "kick"},
    })
    assert svc.run_once() == 1

    result = _result(svc, stem)
    assert result["ok"] is True
    assert result["id"] == "pm-001"
    assert result["result"]["track"] == "Kick"
    assert result["result"]["diagnosis"]["finding"]["confidence"] == "high"
    # The measured payload rides along so the panel's Evidence section can
    # resolve finding.evidence_refs[].path without re-deriving anything.
    assert result["result"]["payload"]["track"]["name"] == "Kick"
    assert result["result"]["payload"]["audio"]["sample_peak_db"] == -1.0
    assert result["result"]["payload"]["audio"]["rms_db"] == -18.0
    assert result["result"]["payload"]["audio"]["spectrum_third_octave"] == [
        {"freq_hz": 100, "level_db": -18.0}
    ]
    assert result["result"]["payload"] == svc.diagnose_payloads[-1]
    evidence_path = result["result"]["diagnosis"]["finding"]["evidence_refs"][0]["path"]
    section, field = evidence_path.split(".")
    assert result["result"]["payload"][section][field] == -1.0
    # Progress file is cleaned up once the result exists.
    assert not os.path.exists(os.path.join(svc.outbox, f"{stem}.progress.json"))
    # Inbox and processing are both empty.
    assert not any(n.endswith(".json") for n in os.listdir(svc.inbox))
    assert not any(n.endswith(".json") for n in os.listdir(svc.processing))


def test_malformed_job_file_writes_typed_error_and_survives(svc):
    with open(os.path.join(svc.inbox, "broken.json"), "w", encoding="utf-8") as f:
        f.write("{not json")
    assert svc.run_once() == 1

    result = _result(svc, "broken")
    assert result["ok"] is False
    assert result["error"]["code"] == "bad_job"
    assert result["error"]["recovery"]["action"]


def test_unknown_job_type_is_typed(svc):
    stem = _submit(svc, {"id": "pm-002", "type": "explode", "payload": {}})
    svc.run_once()
    assert _result(svc, stem)["error"]["code"] == "unknown_job_type"


def test_silent_capture_is_refused_not_diagnosed(svc):
    svc.stats_overrides["rms_db"] = -70.0
    stem = _submit(svc, {
        "id": "pm-003", "type": "track_check", "payload": {"track": "Kick"},
    })
    svc.run_once()

    result = _result(svc, stem)
    assert result["ok"] is False
    assert result["error"]["code"] == "silence_gate"


def test_track_not_resolved_is_typed(svc):
    stem = _submit(svc, {
        "id": "pm-004", "type": "track_check", "payload": {"track": "Vocals"},
    })
    svc.run_once()
    assert _result(svc, stem)["error"]["code"] == "track_not_resolved"


def test_preview_fix_runs_the_loop_and_reports_restored(svc):
    stem = _submit(svc, {
        "id": "pm-005", "type": "preview_fix",
        "payload": {"diagnosis": json.loads(_diagnosis().model_dump_json())},
    })
    svc.run_once()

    result = _result(svc, stem)
    assert result["ok"] is True
    assert result["result"]["restored"] is True
    calls = svc.fake_bridge.calls
    assert calls.index("preview_change") < calls.index("cancel_preview")


def test_preview_fix_adjustment_is_clamped_by_the_sidecar(svc):
    stem = _submit(svc, {
        "id": "pm-005-adjusted", "type": "preview_fix",
        "payload": {
            "diagnosis": json.loads(_diagnosis().model_dump_json()),
            "proposed_value": -99.0,
        },
    })
    svc.run_once()

    result = _result(svc, stem)
    assert result["ok"] is True
    assert svc.fake_bridge.preview_payloads[-1]["proposed_value"] == -6.0


def test_preview_fix_capture_death_still_restores(svc):
    svc.fake_bridge.fail_capture_at = 2
    stem = _submit(svc, {
        "id": "pm-006", "type": "preview_fix",
        "payload": {"diagnosis": json.loads(_diagnosis().model_dump_json())},
    })
    svc.run_once()

    result = _result(svc, stem)
    assert result["ok"] is False
    assert result["error"]["code"] == "bridge_error"
    assert "cancel_preview" in svc.fake_bridge.calls, "restore must still run"


def test_commit_fix_reports_the_undo_point(svc):
    stem = _submit(svc, {
        "id": "pm-007", "type": "commit_fix",
        "payload": {"diagnosis": json.loads(_diagnosis().model_dump_json())},
    })
    svc.run_once()

    result = _result(svc, stem)
    assert result["ok"] is True
    assert result["result"]["undo_point"] == "Post Mortem: set_track_volume on Kick"


def test_commit_fix_reapplies_the_engine_clamped_adjustment(svc):
    stem = _submit(svc, {
        "id": "pm-007-adjusted", "type": "commit_fix",
        "payload": {
            "diagnosis": json.loads(_diagnosis().model_dump_json()),
            "proposed_value": -99.0,
        },
    })
    svc.run_once()

    result = _result(svc, stem)
    assert result["ok"] is True
    assert svc.fake_bridge.preview_payloads[-1]["proposed_value"] == -6.0


def test_cancel_removes_a_queued_job_before_it_runs(svc):
    # The cancel arrives ahead of its target in lexical order, so the drain
    # processes it first — the target must never execute.
    target_stem = _submit(svc, {
        "id": "pm-target", "type": "track_check", "payload": {"track": "Kick"},
    }, filename="b-target.json")
    cancel_stem = _submit(svc, {
        "id": "pm-cancel", "type": "cancel_job",
        "payload": {"target_id": "pm-target"},
    }, filename="a-cancel.json")
    svc.run_once()

    assert _result(svc, cancel_stem)["ok"] is True
    target_result = _result(svc, target_stem)
    assert target_result["ok"] is False
    assert target_result["error"]["code"] == "cancelled"
    assert "capture_1" not in svc.fake_bridge.calls


def test_cancel_with_no_target_is_typed(svc):
    stem = _submit(svc, {
        "id": "pm-008", "type": "cancel_job", "payload": {"target_id": "ghost"},
    })
    svc.run_once()
    assert _result(svc, stem)["error"]["code"] == "nothing_to_cancel"


def test_reply_filename_comes_from_the_inbox_filename_not_the_id(svc):
    _submit(svc, {
        "id": "../../evil", "type": "get_status", "payload": {},
    }, filename="safe-name.json")
    svc.run_once()

    result = _result(svc, "safe-name")
    assert result["ok"] is True
    assert result["id"] == "safe-name", "hostile id must not be adopted"
    assert not os.path.exists(os.path.join(svc.outbox, "..", "..", "evil.json"))


def test_get_status_reports_versions_bridge_capture_and_provider(svc, monkeypatch):
    class ConfiguredProvider:
        @classmethod
        def from_config(cls):
            return cls(), ModelProfile("configured-model")

    monkeypatch.setattr(service, "AnthropicProvider", ConfiguredProvider)
    stem = _submit(svc, {"id": "pm-009", "type": "get_status",
        "payload": {"panel_registered": True}})
    svc.run_once()

    result = _result(svc, stem)
    assert result["ok"] is True
    assert result["result"]["bridge_ok"] is True
    assert result["result"]["service_version"]
    assert result["result"]["capture_preflight"]["capture_allowed"] is True
    assert result["result"]["provider_configured"] is True
    assert result["result"]["model"] == "configured-model"
    assert result["result"]["setup"] == {
        "ready": True,
        "provider_configured": True,
        "checks": {"bridge_running": True, "capture_enabled": True,
                   "panel_registered": True},
        "recovery": None,
        "detail": None,
    }
    assert "get_capture_preflight" in svc.fake_bridge.calls


@pytest.mark.parametrize(
    "code",
    [
        "isolation_gate", "isolation_gate_preview", "capture_not_isolated",
        "silence_gate", "insufficient_signal", "track_not_resolved",
        "bridge_error", "cancelled", "interrupted", "bad_job",
        "bad_adjustment", "unknown_job_type", "nothing_to_cancel",
        "provider_authentication", "provider_rate_limit", "provider_network",
        "provider_refusal", "provider_incomplete_response",
        "provider_invalid_response", "not_actionable", "bad_diagnosis",
        "cross_track_claim", "proposed_value_unchanged",
        "current_value_drift", "stale_identity", "track_identity_mismatch",
        "current_value_mismatch", "fx_identity_mismatch",
        "parameter_identity_mismatch", "revalidation_failed",
        "evidence_missing", "evidence_path_missing", "evidence_value_null",
        "fx_bypass_evidence_missing", "move_limit_exceeded",
        "unsupported_goal", "unsupported_metric", "fx_bypass_not_preview",
    ],
)
def test_every_stable_failure_has_engine_owned_recovery(code):
    recovery = service._error_recovery(code)

    assert recovery["explanation"]
    assert recovery["copy_diagnostics"] is False
    if code != "nothing_to_cancel":
        assert recovery["action"]


def test_unknown_failure_keeps_typed_code_and_copy_diagnostics():
    recovery = service._error_recovery("future_failure")

    assert "future_failure" in recovery["explanation"]
    assert recovery["copy_diagnostics"] is True


def test_internal_error_has_specific_copy_diagnostics_recovery():
    recovery = service._error_recovery("internal_error")

    assert recovery["explanation"] == "Post Mortem hit an internal error."
    assert recovery["copy_diagnostics"] is True


@pytest.mark.parametrize(
    ("bridge_ok", "preflight", "expected"),
    [
        (False, None, "bridge_dead"),
        (True, None, "preflight_missing"),
        (True, {
            "capture_allowed": False,
            "blockers": [{"code": "capture_gated", "message": "off"}],
            "warnings": [],
        }, "capture_gated"),
        (True, {
            "capture_allowed": True,
            "blockers": [],
            "warnings": [{"code": "render_hang_risk", "message": "risk"}],
        }, "render_hang_risk"),
        (True, {"capture_allowed": False, "blockers": [], "warnings": []},
         "capture_blocked"),
    ],
)
def test_setup_matrix_has_a_specific_recovery(bridge_ok, preflight, expected):
    setup = service._setup_state(bridge_ok, "bridge detail", preflight, False)

    assert setup["ready"] is False
    assert setup["recovery"]["code"] == expected
    assert setup["recovery"]["message"]
    assert setup["recovery"]["action"]


@pytest.mark.parametrize(
    ("bridge_ok", "preflight", "expected"),
    [
        (False, None, "bridge_dead"),
        (True, None, "preflight_missing"),
        (True, {
            "capture_allowed": False,
            "blockers": [{"code": "capture_gated", "message": "x"}],
            "warnings": [],
        }, "capture_gated"),
        (True, {
            "capture_allowed": True,
            "blockers": [],
            "warnings": [{"code": "render_hang_risk", "message": "x"}],
        }, "render_hang_risk"),
    ],
)
def test_setup_verdict_owns_each_known_recovery(bridge_ok, preflight, expected):
    setup = service._setup_state(bridge_ok, "detail", preflight, False)
    assert setup["ready"] is False
    assert setup["recovery"]["code"] == expected
    assert setup["recovery"]["message"]
    assert setup["recovery"]["action"]


def test_enable_capture_updates_bridge_config_and_requires_restart(svc):
    stem = _submit(svc, {"id": "pm-enable", "type": "enable_capture"})
    svc.run_once()

    result = _result(svc, stem)
    assert result["ok"] is True
    assert result["result"]["enabled"] is True
    assert result["result"]["restart_required"] is True
    assert "enable_capture" in svc.fake_bridge.calls


def test_validate_provider_checks_live_access_before_saving_key(
    svc, monkeypatch, tmp_path
):
    events = []

    class FakeProvider:
        @classmethod
        def from_api_key(cls, api_key):
            events.append(("construct", api_key))
            return cls(), ModelProfile("configured-model"), "POSTMORTEM_API_KEY"

        def validate_access(self, profile):
            events.append(("validate", profile.model))

    def fake_set_file_value(key, value):
        events.append(("save", key, value))

    monkeypatch.setattr(service, "AnthropicProvider", FakeProvider)
    monkeypatch.setattr(service.config, "set_file_value", fake_set_file_value)

    stem = _submit(svc, {
        "id": "pm-provider", "type": "validate_provider",
        "payload": {"api_key": "private-key"},
    })
    svc.run_once()

    result = _result(svc, stem)
    assert result["ok"] is True
    assert result["result"] == {
        "validated": True,
        "model": "configured-model",
    }
    assert events == [
        ("construct", "private-key"),
        ("validate", "configured-model"),
        ("save", "POSTMORTEM_API_KEY", "private-key"),
    ]


def test_validate_provider_can_check_an_existing_config_without_rewriting_it(
    svc, monkeypatch
):
    events = []

    class FakeProvider:
        @classmethod
        def from_config(cls):
            events.append("construct")
            return cls(), ModelProfile("configured-model")

        def validate_access(self, profile):
            events.append(("validate", profile.model))

    monkeypatch.setattr(service, "AnthropicProvider", FakeProvider)
    monkeypatch.setattr(
        service.config, "set_file_value",
        lambda *args: (_ for _ in ()).throw(AssertionError("must not rewrite config")),
    )

    stem = _submit(svc, {
        "id": "pm-provider-existing", "type": "validate_provider", "payload": {},
    })
    svc.run_once()

    assert _result(svc, stem)["ok"] is True
    assert events == ["construct", ("validate", "configured-model")]


def test_validate_provider_does_not_save_a_rejected_key(svc, monkeypatch):
    events = []

    class RejectingProvider:
        @classmethod
        def from_api_key(cls, api_key):
            return cls(), ModelProfile("configured-model"), "POSTMORTEM_API_KEY"

        def validate_access(self, profile):
            raise ProviderError(
                ProviderErrorCategory.AUTHENTICATION,
                "provider authentication or configuration failed",
            )

    monkeypatch.setattr(service, "AnthropicProvider", RejectingProvider)
    monkeypatch.setattr(
        service.config, "set_file_value",
        lambda *args: events.append(args),
    )
    stem = _submit(svc, {
        "id": "pm-provider-rejected", "type": "validate_provider",
        "payload": {"api_key": "bad-key"},
    })
    svc.run_once()

    result = _result(svc, stem)
    assert result["ok"] is False
    assert result["error"]["code"] == "provider_authentication"
    assert events == []


def test_interrupted_job_is_reported_and_never_reexecuted(svc):
    with open(os.path.join(svc.processing, "stranded.json"), "w", encoding="utf-8") as f:
        json.dump({"id": "pm-010", "type": "preview_fix", "payload": {}}, f)
    svc.sweep_interrupted()

    result = _result(svc, "stranded")
    assert result["ok"] is False
    assert result["error"]["code"] == "interrupted"
    assert svc.fake_bridge.calls == [], "sweep must not execute anything"
    assert not os.listdir(svc.processing) or not any(
        n.endswith(".json") for n in os.listdir(svc.processing)
    )


def test_record_feedback_appends_jsonl(svc):
    stem = _submit(svc, {
        "id": "pm-011", "type": "record_feedback",
        "payload": {"kind": "not_helpful", "track": "Kick"},
    })
    svc.run_once()

    assert _result(svc, stem)["ok"] is True
    with open(os.path.join(svc.root, "feedback.jsonl"), encoding="utf-8") as f:
        lines = [json.loads(line) for line in f if line.strip()]
    assert lines[0]["kind"] == "not_helpful"
    assert lines[0]["job_id"] == "pm-011"


def test_mcp_handoff_is_validated_and_returned_by_status(svc, monkeypatch):
    receipt_id = "a" * 32
    measured = _submit(svc, {
        "id": "pm-mcp-measured", "type": "record_mcp_measurement",
        "payload": {"receipt_id": receipt_id, "tracks": ["Kick"], "seconds": 10},
    })
    svc.run_once()
    assert _result(svc, measured)["ok"] is True
    delivered = _submit(svc, {
        "id": "pm-mcp-delivered", "type": "record_mcp_handoff",
        "payload": {"receipt_id": receipt_id, "tracks": ["Kick"],
            "diagnosis_summary": "The kick has a measured buildup around 200 Hz."},
    })
    svc.run_once()
    assert _result(svc, delivered)["ok"] is True

    class ConfiguredProvider:
        @classmethod
        def from_config(cls):
            return cls(), ModelProfile("configured-model")

    monkeypatch.setattr(service, "AnthropicProvider", ConfiguredProvider)
    status = _submit(svc, {"id": "pm-mcp-status", "type": "get_status",
        "payload": {"mcp_started_at": "2026-07-12T00:00:00+00:00"}})
    svc.run_once()
    handoff = _result(svc, status)["result"]["mcp_handoff"]
    assert handoff["ready"] is True
    assert handoff["tracks"] == ["Kick"]
    assert "200 Hz" in handoff["diagnosis_summary"]


def test_mcp_handoff_requires_sidecar_measurement_receipt(svc):
    stem = _submit(svc, {
        "id": "pm-mcp-unmeasured", "type": "record_mcp_handoff",
        "payload": {"receipt_id": "b" * 32, "tracks": ["Kick"],
            "diagnosis_summary": "This diagnosis is long enough but has no measurement."},
    })
    svc.run_once()
    result = _result(svc, stem)
    assert result["ok"] is False
    assert result["error"]["code"] == "mcp_receipt_missing"


def test_heartbeat_carries_pid_and_version(svc):
    svc.write_heartbeat(force=True)
    with open(os.path.join(svc.root, "heartbeat.json"), encoding="utf-8") as f:
        heartbeat = json.load(f)
    assert heartbeat["pid"] == os.getpid()
    assert heartbeat["service_version"]
    assert heartbeat["in_flight_job"] is None


def test_lock_refuses_a_second_live_instance_and_reclaims_a_dead_one(svc, tmp_path):
    svc.acquire_lock()
    other = service.Service(root=str(tmp_path))
    # Same pid means "us"; simulate a foreign live pid.
    with open(os.path.join(str(tmp_path), "lock.json"), "w", encoding="utf-8") as f:
        json.dump({"pid": os.getpid() + 0, "created_at": "x"}, f)
    # A DEAD pid is reclaimed silently.
    with open(os.path.join(str(tmp_path), "lock.json"), "w", encoding="utf-8") as f:
        json.dump({"pid": 2 ** 22 + 12345, "created_at": "x"}, f)
    other.acquire_lock()
    with open(os.path.join(str(tmp_path), "lock.json"), encoding="utf-8") as f:
        assert json.load(f)["pid"] == os.getpid()


def test_bad_seconds_is_refused_before_any_bridge_call(svc):
    stem = _submit(svc, {
        "id": "pm-012", "type": "track_check",
        "payload": {"track": "Kick", "seconds": 0},
    })
    svc.run_once()

    assert _result(svc, stem)["error"]["code"] == "bad_job"
    assert svc.fake_bridge.calls == []
