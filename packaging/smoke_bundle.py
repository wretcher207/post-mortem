"""Black-box smoke suite for a frozen postmortem-sidecar onedir bundle."""

import argparse
import hashlib
import json
import os
import statistics
import subprocess
import tempfile
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FAKE_DAEMON = ROOT / "packaging" / "smoke" / "fake_daemon"


def _run(binary, args, env):
    completed = subprocess.run(
        [str(binary), *args],
        env=env,
        capture_output=True,
        text=True,
        timeout=45,
        check=False,
    )
    if completed.returncode != 0:
        raise AssertionError(
            f"bundle command failed ({completed.returncode}): {args!r}\n"
            f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
        )
    return completed


def _bundle_size(path):
    root = path.parent
    return sum(item.stat().st_size for item in root.rglob("*") if item.is_file())


def _sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def smoke(binary):
    binary = Path(binary).resolve()
    if not binary.is_file():
        raise AssertionError(f"bundle executable not found: {binary}")
    with tempfile.TemporaryDirectory(prefix="postmortem-bundle-smoke-") as temp:
        test_env = os.environ.copy()
        test_env.update({
            "PATH": "",
            "POSTMORTEM_DATA_DIR": temp,
        })
        version = _run(binary, ["--version"], test_env).stdout.strip()
        if not version.startswith("postmortem-sidecar "):
            raise AssertionError(f"unexpected version output: {version!r}")

        _run(binary, ["test-bundle", "-q", str(ROOT / "tests")], test_env)
        env = {**test_env, "REAPER_DAEMON_ROOT": str(FAKE_DAEMON)}

        setup_smoke = _run(
            binary,
            ["setup-smoke", "--reaper-daemon-root", str(FAKE_DAEMON)],
            test_env,
        )
        setup_report = json.loads(setup_smoke.stdout)
        if (
            setup_report.get("bridge_ok") is not True
            or setup_report.get("capture_preflight", {}).get("capture_allowed")
            is not True
            or setup_report.get("setup", {}).get("ready") is not True
        ):
            raise AssertionError(f"setup smoke was not ready: {setup_report!r}")

        completed = _run(
            binary,
            ["cli", "Kick", "--seconds", "1", "--payload-only"],
            env,
        )
        payload = json.loads(completed.stdout)
        audio = payload["audio"]
        if (
            payload["capture"].get("scope") != "isolated_track"
            or payload["capture"].get("isolation_verified") is not True
        ):
            raise AssertionError(f"capture provenance changed: {payload['capture']!r}")
        if not 0.99 <= audio["duration_seconds"] <= 1.01:
            raise AssertionError(f"wrong analyzed duration: {audio['duration_seconds']}")
        if not -6.2 <= audio["sample_peak_db"] <= -5.8:
            raise AssertionError(f"golden WAV peak drifted: {audio['sample_peak_db']}")
        band = next(
            item
            for item in audio["spectrum_third_octave"]
            if item["freq_hz"] == 1000
        )
        if not -9.5 <= band["level_db"] <= -8.5:
            raise AssertionError(f"golden WAV spectrum drifted: {band['level_db']}")

        starts = []
        for _ in range(3):
            before = time.perf_counter()
            _run(binary, ["--version"], env)
            starts.append(time.perf_counter() - before)
    return {
        "version": version,
        "executable_sha256": _sha256(binary),
        "bundle_size_bytes": _bundle_size(binary),
        "cold_start_seconds_median": round(statistics.median(starts), 4),
        "path_without_system_python": True,
    }


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("binary")
    parser.add_argument("--metrics-out")
    args = parser.parse_args(argv)
    metrics = smoke(args.binary)
    rendered = json.dumps(metrics, indent=2) + "\n"
    print(rendered, end="")
    if args.metrics_out:
        metrics_path = Path(args.metrics_out)
        metrics_path.parent.mkdir(parents=True, exist_ok=True)
        metrics_path.write_text(rendered, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
