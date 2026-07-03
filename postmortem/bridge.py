"""Thin wrapper over the Reaper Daemon CLI (reaperd.py).

All REAPER access goes through reaperd.py as a subprocess; never write to the
bridge inbox directly. Every call returns the parsed result dict and raises
BridgeError when ok is not true.
"""

import json
import os
import subprocess
from datetime import datetime, timezone

BRIDGE_ROOT = os.environ.get(
    "REAPER_DAEMON_ROOT",
    os.path.expanduser("~/workspace/audio/reaper-bridge"),
)
REAPERD = os.path.join(BRIDGE_ROOT, "reaperd.py")


class BridgeError(RuntimeError):
    pass


def _run(args, timeout_seconds):
    proc = subprocess.run(
        ["python3", REAPERD, *args],
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
    )
    out = proc.stdout.strip()
    if not out:
        raise BridgeError(
            f"reaperd returned no output (exit {proc.returncode}): {proc.stderr.strip()}"
        )
    # reaperd prints a human "Sent command <id>" line before the JSON reply;
    # the reply is the last non-empty line of stdout.
    out = out.splitlines()[-1]
    try:
        return json.loads(out)
    except json.JSONDecodeError as e:
        raise BridgeError(f"reaperd output is not JSON: {out[:200]}") from e


def status():
    """Liveness gate. Call before anything else; raise if the bridge is dead.
    reaperd status prints human text and signals via exit code (0 = alive)."""
    proc = subprocess.run(
        ["python3", REAPERD, "status"],
        capture_output=True,
        text=True,
        timeout=15,
    )
    line = proc.stdout.strip().splitlines()[0] if proc.stdout.strip() else "no output"
    if proc.returncode != 0:
        raise BridgeError(f"bridge is not alive: {line}")
    return line


def cmd(cmd_type, payload, timeout_ms=10000):
    res = _run(
        ["cmd", cmd_type, json.dumps(payload), "--timeout", str(timeout_ms)],
        timeout_seconds=timeout_ms / 1000 + 30,
    )
    if not res.get("ok"):
        raise BridgeError(f"{cmd_type} failed: {json.dumps(res)[:400]}")
    return res.get("data", {})


def scan_fx(track_name):
    return cmd("scan_fx", {"target_track_name": track_name, "include_values": True})


def get_context():
    return cmd("get_context", {"include_fx": False})


def get_track_routing(track_name):
    return cmd("get_track_routing", {"target_track_name": track_name})


def capture_track_audio(track_name, duration_seconds=30, temp_dir="/tmp/reaper-diagnosis"):
    """Post-FX stem capture. Verifies the returned file is real and fresh:
    exists, nonzero size, mtime newer than when we sent the command. A stale
    WAV diagnosed confidently is this tool's worst failure mode."""
    os.makedirs(temp_dir, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    safe_name = "".join(c if c.isalnum() else "-" for c in track_name.lower())
    output_file = os.path.join(temp_dir, f"{safe_name}-{stamp}.wav")

    sent_at = datetime.now(timezone.utc).timestamp()
    data = cmd(
        "capture_track_audio",
        {
            "target_track_name": track_name,
            "duration_seconds": duration_seconds,
            "output_file": output_file,
        },
        # Render blocks the bridge defer loop; give it room.
        timeout_ms=120000,
    )

    path = data.get("file_path", output_file)
    if not os.path.exists(path):
        raise BridgeError(f"capture reported ok but file does not exist: {path}")
    stat = os.stat(path)
    if stat.st_size == 0:
        raise BridgeError(f"capture produced an empty file: {path}")
    if stat.st_mtime < sent_at - 5:
        raise BridgeError(
            f"capture file predates the command (stale render?): {path} "
            f"mtime={stat.st_mtime:.0f} sent={sent_at:.0f}"
        )
    return data, path
