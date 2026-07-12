"""Thin wrapper over the Reaper Daemon CLI (reaperd.py).

All REAPER access goes through reaperd.py as a subprocess; never write to the
bridge inbox directly. Every call returns the parsed result dict and raises
BridgeError when ok is not true.
"""

import json
import os
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime, timezone

from . import config
from .constants import DEFAULT_CAPTURE_SECONDS


class BridgeError(RuntimeError):
    pass


def _reaperd():
    """Path to reaperd.py in the user's reaper-daemon clone. Set
    REAPER_DAEMON_ROOT (env or config file) to point at the clone."""
    root = config.get(
        "REAPER_DAEMON_ROOT",
        os.path.expanduser("~/workspace/audio/reaper-bridge"),
    )
    reaperd = os.path.join(root, "reaperd.py")
    if not os.path.exists(reaperd):
        raise BridgeError(
            f"reaperd.py not found at {reaperd}. Post Mortem needs the Reaper "
            "Daemon bridge: clone https://github.com/wretcher207/reaper-daemon, "
            "run its setup/install.py, then set REAPER_DAEMON_ROOT to the clone "
            f"path (env var, or a line in {config.CONFIG_PATH})."
        )
    return reaperd


def _run(args, timeout_seconds):
    try:
        proc = subprocess.run(
            [sys.executable, _reaperd(), *args],
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        # Surface as a clean BridgeError, not a raw traceback. REAPER may still
        # be rendering; a stuck render dialog hangs the bridge until dismissed.
        raise BridgeError(
            f"reaperd timed out after {timeout_seconds:.0f}s on '{args[0]}'. "
            "REAPER may still be processing; check the session for a render "
            "dialog waiting to be dismissed."
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
        reply = json.loads(out)
    except json.JSONDecodeError as e:
        raise BridgeError(f"reaperd output is not JSON: {out[:200]}") from e
    # Guard the protocol shape: a bare null / list / scalar decodes fine but
    # then blows up on res.get(...) downstream with an AttributeError that
    # bypasses clean handling.
    if not isinstance(reply, dict):
        raise BridgeError(f"reaperd reply is not a JSON object: {out[:200]}")
    return reply


def status():
    """Liveness gate. Call before anything else; raise if the bridge is dead.
    reaperd status prints human text and signals via exit code (0 = alive)."""
    try:
        proc = subprocess.run(
            [sys.executable, _reaperd(), "status"],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except subprocess.TimeoutExpired:
        raise BridgeError("bridge status check timed out after 15s (is REAPER running?)")
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
    # "data": null decodes to None (the default only fires on a missing key),
    # which then breaks .get(...) downstream; coerce to an empty dict.
    return res.get("data") or {}


def scan_fx(track_name):
    return cmd("scan_fx", {"target_track_name": track_name, "include_values": True})


def get_context():
    return cmd("get_context", {"include_fx": False})


def get_track_routing(track_name):
    return cmd("get_track_routing", {"target_track_name": track_name})


def get_capture_preflight():
    """Read-only capture readiness report for onboarding."""
    return cmd("get_capture_preflight", {})


def enable_capture():
    """Enable the daemon's level-3 capture gate without pretending it reloads.

    The bridge reads this flag once when REAPER starts. The caller must show
    the returned restart requirement instead of offering a fake live reload.
    """
    root = os.path.dirname(_reaperd())
    path = os.path.join(root, "bridge", "bridge_config.json")
    values = {}
    try:
        with open(path, encoding="utf-8") as file:
            loaded = json.load(file)
        if not isinstance(loaded, dict):
            raise BridgeError(f"bridge config is not a JSON object: {path}")
        values = loaded
    except FileNotFoundError:
        pass
    except json.JSONDecodeError as error:
        raise BridgeError(f"bridge config is not valid JSON: {path}") from error
    values["allow_risk_level_3"] = True
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as file:
            json.dump(values, file, indent=2)
            file.write("\n")
        os.replace(tmp, path)
    except OSError as error:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise BridgeError(f"could not update bridge config at {path}: {error}") from error
    return {"enabled": True, "restart_required": True, "config_path": path}


def capture_track_audio(
    track_name,
    duration_seconds=DEFAULT_CAPTURE_SECONDS,
    temp_dir=None,
):
    """Post-FX stem capture. Verifies the returned file is real and fresh:
    exists, nonzero size, mtime newer than when we sent the command. A stale
    WAV diagnosed confidently is this tool's worst failure mode."""
    if temp_dir is None:
        temp_dir = os.path.join(tempfile.gettempdir(), "reaper-diagnosis")
    os.makedirs(temp_dir, exist_ok=True)
    # Microseconds + a short random token so two captures of the same track in
    # the same second can't collide (which would overwrite or cross-diagnose).
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%f")
    safe_name = "".join(c if c.isalnum() else "-" for c in track_name.lower())
    output_file = os.path.join(temp_dir, f"{safe_name}-{stamp}-{uuid.uuid4().hex[:8]}.wav")

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

    # Only ever accept (and later delete) the exact path we asked for. A
    # malformed reply pointing file_path at some other recent WAV must not be
    # trusted, analyzed, or unlinked.
    path = data.get("file_path", output_file)
    if os.path.realpath(path) != os.path.realpath(output_file):
        raise BridgeError(
            f"capture returned an unexpected path: {path} (requested {output_file})"
        )
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
