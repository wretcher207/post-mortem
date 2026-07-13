import io
import json
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from postmortem import __version__
from postmortem import bridge, launcher


ROOT = Path(__file__).resolve().parents[1]


def test_launcher_reports_stamped_version():
    output = io.StringIO()
    with redirect_stdout(output):
        assert launcher.main(["--version"]) == 0
    assert output.getvalue().strip() == f"postmortem-sidecar {__version__}"


def test_launcher_setup_smoke_reports_live_capture_readiness(capsys):
    daemon_root = ROOT / "packaging" / "smoke" / "fake_daemon"

    code = launcher.main([
        "setup-smoke",
        "--reaper-daemon-root",
        str(daemon_root),
    ])

    report = json.loads(capsys.readouterr().out)
    assert code == 0
    assert report["bridge_ok"] is True
    assert report["capture_preflight"]["capture_allowed"] is True
    assert report["setup"]["ready"] is True
    assert report["setup"]["checks"] == {
        "bridge_running": True,
        "capture_enabled": True,
    }


def test_launcher_setup_smoke_returns_restart_recovery_when_bridge_is_down(
    tmp_path,
    capsys,
):
    code = launcher.main([
        "setup-smoke",
        "--reaper-daemon-root",
        str(tmp_path),
    ])

    report = json.loads(capsys.readouterr().out)
    assert code == 3
    assert report["bridge_ok"] is False
    assert report["capture_preflight"] is None
    assert report["setup"]["ready"] is False
    assert report["setup"]["recovery"]["code"] == "bridge_dead"
    assert "restart REAPER" in report["setup"]["recovery"]["action"]


def test_launcher_setup_smoke_returns_capture_gate_recovery(monkeypatch, capsys):
    daemon_root = ROOT / "packaging" / "smoke" / "fake_daemon"
    monkeypatch.setenv("POSTMORTEM_FAKE_CAPTURE_GATED", "1")

    code = launcher.main([
        "setup-smoke",
        "--reaper-daemon-root",
        str(daemon_root),
    ])

    report = json.loads(capsys.readouterr().out)
    assert code == 3
    assert report["bridge_ok"] is True
    assert report["capture_preflight"]["capture_allowed"] is False
    assert report["setup"]["recovery"] == {
        "code": "capture_gated",
        "message": "Safe track capture is off.",
        "action": "Enable Safe Capture, restart REAPER, then test again.",
        "primary_action": {
            "label": "Enable Safe Capture",
            "job_type": "enable_capture",
            "payload": {},
        },
    }


def test_launcher_setup_smoke_distinguishes_missing_preflight_from_dead_bridge(
    monkeypatch,
    capsys,
):
    def missing_preflight():
        raise bridge.BridgeError("get_capture_preflight is unavailable")

    monkeypatch.setattr(bridge, "status", lambda: "CONNECTED")
    monkeypatch.setattr(bridge, "get_capture_preflight", missing_preflight)

    code = launcher.main([
        "setup-smoke",
        "--reaper-daemon-root",
        str(ROOT),
    ])

    report = json.loads(capsys.readouterr().out)
    assert code == 3
    assert report["bridge_ok"] is True
    assert report["bridge_status"] == "CONNECTED"
    assert report["capture_preflight"] is None
    assert report["capture_preflight_detail"] == (
        "get_capture_preflight is unavailable"
    )
    assert report["setup"]["checks"]["bridge_running"] is True
    assert report["setup"]["recovery"]["code"] == "preflight_missing"


def test_launcher_setup_smoke_fails_closed_on_malformed_preflight(
    monkeypatch,
    capsys,
):
    monkeypatch.setattr(bridge, "status", lambda: "CONNECTED")
    monkeypatch.setattr(
        bridge,
        "get_capture_preflight",
        lambda: {
            "capture_allowed": True,
            "blockers": None,
            "warnings": None,
        },
    )

    code = launcher.main([
        "setup-smoke",
        "--reaper-daemon-root",
        str(ROOT),
    ])

    report = json.loads(capsys.readouterr().out)
    assert code == 3
    assert report["bridge_ok"] is True
    assert report["setup"]["ready"] is False
    assert report["setup"]["recovery"]["code"] == "preflight_invalid"


def test_launcher_routes_cli_arguments_unchanged():
    with patch("postmortem.cli.main", return_value=7) as cli_main:
        assert launcher.main(["cli", "Kick", "--payload-only"]) == 7
    cli_main.assert_called_once_with(["Kick", "--payload-only"])


def test_launcher_runs_sidecar_by_default_and_with_explicit_service_command():
    with patch("postmortem.service.main", return_value=0) as service_main:
        assert launcher.main(["--once"]) == 0
        assert launcher.main(["service", "--once"]) == 0
    assert service_main.call_args_list[0].args == (["--once"],)
    assert service_main.call_args_list[1].args == (["--once"],)


def test_launcher_runs_unit_suite_inside_bundled_interpreter():
    with patch("postmortem.launcher._run_bundle_tests", return_value=0) as run_tests:
        assert launcher.main(["test-bundle", "-q", "tests"]) == 0
    run_tests.assert_called_once_with(["-q", "tests"])


def test_frozen_bridge_uses_embedded_reaperd_runner():
    with (
        patch.object(launcher.sys, "frozen", True, create=True),
        patch.object(launcher.sys, "executable", "/bundle/postmortem-sidecar"),
        patch("postmortem.bridge._reaperd", return_value="/daemon/reaperd.py"),
    ):
        assert bridge.reaperd_command(["status"]) == [
            "/bundle/postmortem-sidecar",
            "reaperd",
            "/daemon/reaperd.py",
            "status",
        ]


def test_launcher_preserves_python_c_for_bundled_subprocess_tests(capsys):
    assert launcher.main(["-c", 'print("inside bundle")']) == 0
    assert capsys.readouterr().out.strip() == "inside bundle"
