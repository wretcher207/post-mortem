import io
from contextlib import redirect_stdout
from unittest.mock import patch

from postmortem import __version__
from postmortem import launcher


def test_launcher_reports_stamped_version():
    output = io.StringIO()
    with redirect_stdout(output):
        assert launcher.main(["--version"]) == 0
    assert output.getvalue().strip() == f"postmortem-sidecar {__version__}"


def test_launcher_routes_cli_arguments_unchanged():
    with patch("postmortem.launcher.cli.main", return_value=7) as cli_main:
        assert launcher.main(["cli", "Kick", "--payload-only"]) == 7
    cli_main.assert_called_once_with(["Kick", "--payload-only"])


def test_launcher_runs_sidecar_by_default_and_with_explicit_service_command():
    with patch("postmortem.launcher.service.main", return_value=0) as service_main:
        assert launcher.main(["--once"]) == 0
        assert launcher.main(["service", "--once"]) == 0
    assert service_main.call_args_list[0].args == (["--once"],)
    assert service_main.call_args_list[1].args == (["--once"],)


def test_frozen_bridge_uses_embedded_reaperd_runner():
    with (
        patch.object(launcher.sys, "frozen", True, create=True),
        patch.object(launcher.sys, "executable", "/bundle/postmortem-sidecar"),
        patch("postmortem.bridge._reaperd", return_value="/daemon/reaperd.py"),
    ):
        assert launcher.bridge.reaperd_command(["status"]) == [
            "/bundle/postmortem-sidecar",
            "reaperd",
            "/daemon/reaperd.py",
            "status",
        ]
