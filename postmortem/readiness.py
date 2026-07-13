"""One-shot, read-only setup readiness probe for installers and the panel."""

from __future__ import annotations

import argparse
import json
import os

from . import bridge


def _valid_preflight_entries(value) -> bool:
    return isinstance(value, list) and all(
        isinstance(item, dict)
        and isinstance(item.get("code"), str)
        and bool(item["code"])
        for item in value
    )


def probe_bridge() -> dict:
    """Check liveness and preflight without misclassifying capability failures."""
    try:
        bridge_status = bridge.status()
    except bridge.BridgeError as error:
        return {
            "bridge_ok": False,
            "bridge_status": str(error),
            "capture_preflight": None,
            "capture_preflight_detail": None,
        }

    try:
        capture_preflight = bridge.get_capture_preflight()
    except bridge.BridgeError as error:
        return {
            "bridge_ok": True,
            "bridge_status": bridge_status,
            "capture_preflight": None,
            "capture_preflight_detail": str(error),
        }
    return {
        "bridge_ok": True,
        "bridge_status": bridge_status,
        "capture_preflight": capture_preflight,
        "capture_preflight_detail": None,
    }


def setup_state(
    bridge_ok,
    bridge_status,
    preflight,
    provider_configured,
    panel_registered=None,
):
    """Engine-owned setup verdict shared by the panel and installer."""
    checks = {
        "bridge_running": bridge_ok is True,
        "capture_enabled": bool(
            isinstance(preflight, dict) and preflight.get("capture_allowed") is True
        ),
    }
    if panel_registered is not None:
        checks["panel_registered"] = panel_registered is True
    recovery = None
    if not bridge_ok:
        recovery = {
            "code": "bridge_dead",
            "message": (
                "Reaper Daemon is not answering. The watchdog normally restarts it."
            ),
            "action": (
                "If REAPER is already open and it does not reconnect, restart "
                "REAPER once, then test again."
            ),
            "primary_action": {
                "label": "Test Again",
                "job_type": "get_status",
                "payload": {},
            },
        }
    elif not isinstance(preflight, dict):
        recovery = {
            "code": "preflight_missing",
            "message": "Reaper Daemon did not include Post Mortem's capture check.",
            "action": "Update Reaper Daemon, restart REAPER, then test again.",
            "primary_action": {
                "label": "Test Again",
                "job_type": "get_status",
                "payload": {},
            },
        }
    elif not (
        _valid_preflight_entries(preflight.get("blockers"))
        and _valid_preflight_entries(preflight.get("warnings"))
    ):
        recovery = {
            "code": "preflight_invalid",
            "message": "Reaper Daemon returned an invalid capture check.",
            "action": "Update Reaper Daemon, restart REAPER, then test again.",
            "primary_action": {
                "label": "Test Again",
                "job_type": "get_status",
                "payload": {},
            },
        }
    else:
        blockers = {
            item["code"]: item
            for item in preflight.get("blockers", [])
        }
        warnings = {
            item["code"]: item
            for item in preflight.get("warnings", [])
        }
        if "capture_gated" in blockers:
            recovery = {
                "code": "capture_gated",
                "message": "Safe track capture is off.",
                "action": "Enable Safe Capture, restart REAPER, then test again.",
                "primary_action": {
                    "label": "Enable Safe Capture",
                    "job_type": "enable_capture",
                    "payload": {},
                },
            }
        elif "render_hang_risk" in warnings:
            recovery = {
                "code": "render_hang_risk",
                "message": "One REAPER setting must be switched on before the first capture.",
                "action": (
                    "Open any render window, tick 'Automatically close when finished' "
                    "once, close the window, then test again. Installing SWS also "
                    "handles this automatically."
                ),
                "manual_steps": ["Automatically close when finished"],
                "primary_action": {
                    "label": "Test Again",
                    "job_type": "get_status",
                    "payload": {},
                },
            }
        elif preflight.get("capture_allowed") is not True:
            recovery = {
                "code": "capture_blocked",
                "message": (
                    "Safe capture is still blocked without a supported blocker code."
                ),
                "action": "Update Reaper Daemon, then test again before running audio.",
                "primary_action": {
                    "label": "Test Again",
                    "job_type": "get_status",
                    "payload": {},
                },
            }
        elif panel_registered is False:
            recovery = {
                "code": "panel_not_registered",
                "message": "Post Mortem is open, but REAPER has not registered its action.",
                "action": (
                    "Add Post Mortem to the Actions list, run it there once, then "
                    "Test Again."
                ),
                "primary_action": {
                    "label": "Test Again",
                    "job_type": "get_status",
                    "payload": {},
                },
            }
    return {
        "ready": recovery is None,
        "provider_configured": provider_configured is True,
        "checks": checks,
        "recovery": recovery,
        "detail": bridge_status if not bridge_ok else None,
    }


def probe_setup() -> dict:
    live = probe_bridge()
    return {
        **live,
        "setup": setup_state(
            bridge_ok=live["bridge_ok"],
            bridge_status=live["bridge_status"],
            preflight=live["capture_preflight"],
            provider_configured=False,
            panel_registered=None,
        ),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="postmortem-sidecar setup-smoke",
        description="Check the live REAPER bridge and safe-capture preflight.",
    )
    parser.add_argument("--reaper-daemon-root", required=True)
    args = parser.parse_args(argv)

    previous = os.environ.get("REAPER_DAEMON_ROOT")
    os.environ["REAPER_DAEMON_ROOT"] = args.reaper_daemon_root
    try:
        report = probe_setup()
    finally:
        if previous is None:
            os.environ.pop("REAPER_DAEMON_ROOT", None)
        else:
            os.environ["REAPER_DAEMON_ROOT"] = previous

    print(json.dumps(report, sort_keys=True))
    return 0 if report["setup"]["ready"] else 3
