# Packaged sidecar

P3-007 ships Post Mortem as a PyInstaller **onedir** bundle named
`postmortem-sidecar`. The executable owns these entry points:

```text
postmortem-sidecar                 # run the sidecar service
postmortem-sidecar service --once  # explicit service command
postmortem-sidecar service --reaper-daemon-root /path/to/installed/daemon
postmortem-sidecar cli Kick --payload-only
postmortem-sidecar setup-smoke --reaper-daemon-root /path/to/installed/daemon
postmortem-sidecar --version
postmortem-sidecar test-bundle -q tests  # release acceptance gate
```

The bundle still uses the separately installed Reaper Daemon `reaperd.py`.
The installed panel starts the service with `--reaper-daemon-root`, which pins
all bridge status and capture commands to the daemon shipped in the same
managed runtime instead of a development checkout.
`setup-smoke` performs only bridge liveness and `get_capture_preflight`; it
prints the engine-owned setup verdict as JSON and never starts a render. Exit
status `0` means capture is ready. Exit status `3` means the JSON contains an
actionable restart or configuration recovery.

When frozen, it executes that script inside the bundled Python runtime, so a
customer does not need system Python on `PATH`.

## Local build and smoke

```bash
python -m pip install ".[packaging]" pytest
python -m pytest -q
pyinstaller --clean --noconfirm packaging/postmortem-sidecar.spec
python packaging/smoke_bundle.py \
  dist/postmortem-sidecar/postmortem-sidecar \
  --metrics-out bundle-metrics.json
```

The smoke suite clears `PATH`, runs the full pytest suite inside the bundled
interpreter, checks the stamped binary version and setup preflight, runs a
payload-only Track Check against a fake file bridge, and validates the bundled
WAV analyzer against a 1 kHz golden tone.

## Release artifacts

Every `v*` tag runs `.github/workflows/release-sidecar.yml` on macOS, Windows,
and Linux. Each job runs the complete source suite, builds and smokes the
bundled binary, archives the onedir folder, writes an adjacent SHA-256 file,
records bundle metrics, and uploads all three files as one platform artifact.

## macOS arm64 baseline

Recorded locally on 2026-07-12 with Python 3.14.6 and PyInstaller 6.21.0:

- onedir size: 54,389,155 bytes (54.4 MB)
- compressed `.tar.gz`: 22 MB
- median process cold start (`--version`, three launches): 0.1495 seconds
- system Python on child `PATH`: absent

These numbers are a warm-filesystem baseline for P3-008 installer and updater
decisions, not a release budget. Each release artifact carries its own
`bundle-metrics.json`.
