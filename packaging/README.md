# Packaged sidecar

P3-007 ships Post Mortem as a PyInstaller **onedir** bundle named
`postmortem-sidecar`. The executable owns three entry points:

```text
postmortem-sidecar                 # run the sidecar service
postmortem-sidecar service --once  # explicit service command
postmortem-sidecar cli Kick --payload-only
postmortem-sidecar --version
```

The bundle still uses the separately installed Reaper Daemon `reaperd.py`.
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

The smoke suite clears `PATH`, checks the stamped binary version, runs a
payload-only Track Check against a fake file bridge, and validates the bundled
WAV analyzer against a 1 kHz golden tone.

## Release artifacts

Every `v*` tag runs `.github/workflows/release-sidecar.yml` on macOS, Windows,
and Linux. Each job runs the complete source suite, builds and smokes the
bundled binary, archives the onedir folder, writes an adjacent SHA-256 file,
records bundle metrics, and uploads all three files as one platform artifact.

## macOS arm64 baseline

Recorded locally on 2026-07-12 with Python 3.14.6 and PyInstaller 6.21.0:

- onedir size: 53,399,299 bytes (53.4 MB)
- compressed `.tar.gz`: 21 MB
- median cold start (`--version`, three launches): 0.6345 seconds
- system Python on child `PATH`: absent

These numbers are a baseline for P3-008 installer and updater decisions, not a
release budget. Each release artifact carries its own `bundle-metrics.json`.
