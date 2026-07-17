# Changelog

## 0.1.1 - 2026-07-17

### Fixed

- Bound in-flight capture liveness to the submitted capture duration so a
  stalled job cannot remain busy indefinitely.
- Made bridge job ownership collision-resistant and runtime lock creation
  atomic.
- Preserved result-referenced preview WAV files during cleanup.
- Forwarded the requested capture duration through Preview and Apply.
- Hardened installer ownership, validation, and release documentation for the
  current Apple silicon early-access build.

## 0.1.0 - 2026-07-15

### Added

- Dockable paid REAPER panel with onboarding, Track Check, evidence, feedback,
  and Fix Preview screens.
- Native macOS setup app that installs or updates the panel, packaged engine,
  Reaper Daemon, ReaImGui, and SWS without Git, Python, or terminal use.
- Signed offline license activation. A purchased major version remains usable
  after update entitlement ends.
- Packaged sidecar with the public `0.1.0` engine.
- Setup smoke test, guided recovery, ownership-safe uninstall, and optional
  full app-data removal.
- API-key onboarding and MCP-client onboarding paths.

### Safety

- Only verified isolated-track captures can support a diagnosis.
- Raw audio remains local. Providers receive measurements and project metadata.
- Preview changes are temporary and restore the original before returning.
- Apply rechecks identity and current values, then creates one REAPER undo point.
- License validation has no network path.
- Missing provider credentials never fall back to machine-specific secret files.

### Known limits

- The paid release is Apple silicon macOS only. Purchase is live through the
  Dead Pixel Design storefront with automated license delivery.
- The hosted Windows install-to-first-check proof remains open.
- The Linux paid customer path is withheld from this release.
- Ordinary tracks with media items may fail the capture-isolation gate.
- The panel ships Track Check and Fix Preview only. Mix Check, history, hosted
  credits, and automatic update checks are not in `0.1.0`.
