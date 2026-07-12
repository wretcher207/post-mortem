"""Onboarding writes only the daemon capture gate and preserves its config."""

import json

import pytest

from postmortem import bridge, config


def _daemon_root(tmp_path, monkeypatch, values):
    root = tmp_path / "reaper-daemon"
    (root / "bridge").mkdir(parents=True)
    (root / "reaperd.py").write_text("# test stub\n", encoding="utf-8")
    (root / "bridge" / "bridge_config.json").write_text(
        json.dumps(values), encoding="utf-8"
    )
    monkeypatch.setattr(config, "_file_values", {"REAPER_DAEMON_ROOT": str(root)})
    return root


def test_enable_capture_preserves_the_existing_bridge_config(tmp_path, monkeypatch):
    root = _daemon_root(
        tmp_path,
        monkeypatch,
        {"bridge_root": "/keep", "allow_risk_level_3": False, "auth_token": "x"},
    )

    result = bridge.enable_capture()

    saved = json.loads(
        (root / "bridge" / "bridge_config.json").read_text(encoding="utf-8")
    )
    assert saved == {
        "bridge_root": "/keep",
        "allow_risk_level_3": True,
        "auth_token": "x",
    }
    assert result["restart_required"] is True


def test_enable_capture_refuses_to_overwrite_malformed_config(tmp_path, monkeypatch):
    root = _daemon_root(tmp_path, monkeypatch, {})
    path = root / "bridge" / "bridge_config.json"
    path.write_text("{broken", encoding="utf-8")

    with pytest.raises(bridge.BridgeError, match="not valid JSON"):
        bridge.enable_capture()

    assert path.read_text(encoding="utf-8") == "{broken"
