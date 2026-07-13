"""Fake Reaper Daemon used only by the frozen-binary smoke suite."""

import json
import math
import os
import struct
import sys
import wave


GUID = "{PACKAGED-SMOKE-KICK}"


def _write_sine(path):
    rate = 48_000
    frames = bytearray()
    for index in range(rate):
        sample = int(0.5 * math.sin(2 * math.pi * 1_000 * index / rate) * 32767)
        frames.extend(struct.pack("<h", sample))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with wave.open(path, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(frames)


def _reply(command_type, payload):
    if command_type == "get_capture_preflight":
        capture_gated = os.environ.get("POSTMORTEM_FAKE_CAPTURE_GATED") == "1"
        return {
            "capture_allowed": not capture_gated,
            "blockers": (
                [{"code": "capture_gated", "message": "safe capture is off"}]
                if capture_gated
                else []
            ),
            "warnings": [],
            "risk_gate": {
                "allow_risk_level_3": not capture_gated,
                "requires_restart_to_change": True,
            },
            "sws_installed": True,
            "render_autoclose": False,
            "target": None,
        }
    if command_type == "get_context":
        return {
            "project_name": "packaged-smoke.RPP",
            "tempo": 120.0,
            "tracks": [{"name": "Kick", "guid": GUID, "index": 1}],
        }
    if command_type == "scan_fx":
        return {"tracks": [{"name": "Kick", "guid": GUID, "fx": []}]}
    if command_type == "get_track_routing":
        return {"track": {"name": "Kick", "guid": GUID}, "sends": [], "receives": []}
    if command_type == "capture_track_audio":
        path = payload["output_file"]
        _write_sine(path)
        return {
            "file_path": path,
            "track": {"name": "Kick", "guid": GUID},
            "capture_scope": "isolated_track",
            "isolation_verified": True,
        }
    raise SystemExit(f"unsupported fake command: {command_type}")


if sys.argv[1:] == ["status"]:
    print("CONNECTED: packaged smoke bridge")
    raise SystemExit(0)

if len(sys.argv) < 5 or sys.argv[1] != "cmd":
    raise SystemExit("expected: reaperd.py cmd TYPE PAYLOAD --timeout N")
command_type = sys.argv[2]
payload = json.loads(sys.argv[3])
print(json.dumps({"ok": True, "data": _reply(command_type, payload)}))
