import io
from contextlib import redirect_stdout
from unittest.mock import patch

from postmortem import __version__
from postmortem import bridge, launcher


def test_launcher_reports_stamped_version():
    output = io.StringIO()
    with redirect_stdout(output):
        assert launcher.main(["--version"]) == 0
    assert output.getvalue().strip() == f"postmortem-sidecar {__version__}"


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
