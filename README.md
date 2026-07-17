# Post Mortem

Ever know a REAPER track is wrong but have no idea whether the culprit is
level, EQ, compression, routing, or the room lying to you again? Post Mortem is
a mix debugger for that exact moment. It checks the track you are working on,
shows the most likely problem, and lets you audition one safe fix without
acting like a spectrum chart has ears.

It reads the selected track's FX, live parameter values, routing, fader, and
pan. It captures a short post-FX section, measures the audio, and gives the
model those measurements and project details. Raw audio stays on your
computer.

Post Mortem is built around one rule: if the evidence is weak, it has to say
so. A single-track check cannot prove what another track is masking. A full-mix
or unverified capture cannot be used as evidence for one track. Silence is not
enough evidence for a diagnosis.

A Dead Pixel Design release.

## Release status

Post Mortem `0.1.0` is live for Apple silicon Macs as a `$39` one-time early
access purchase through the Dead Pixel Design storefront. The macOS build is
signed and notarized. Purchase includes permanent version 1 use and 12 months
of version 1 updates.

The paid Windows and Linux installers are not part of this release. Their
customer paths remain gated until the clean-machine checks pass. The MIT
engine and Reaper Daemon continue to support macOS, Windows, and Linux.

## What ships in the paid app

- A dockable REAPER panel with Track Check and Fix Preview.
- One installer for the panel, local engine, Reaper Daemon, ReaImGui, and SWS.
- A packaged runtime. Customers do not need Git, Python, pip, or a terminal.
- Offline license validation. A purchased major version keeps working.
- BYO provider access through an API key, or diagnosis through an MCP client.
- Local setup checks, plain-language recovery, update, and uninstall.

Mix Check, history, hosted credits, Windows paid release, and Linux paid
release are not included in `0.1.0`.

## macOS quick start

You need REAPER 7 or newer, an Apple silicon Mac, a Post Mortem license file,
and either an Anthropic API key or an MCP client connected to Reaper Daemon.

1. Open the Post Mortem disk image.
2. Run **Post Mortem Setup** and choose **Install or Update**.
3. Choose **Add License** and select the signed JSON license you received.
4. Restart REAPER once.
5. Open **Actions > Show action list**, search for `Post Mortem`, and run the
   panel action.
6. Choose **Connect to REAPER** and follow any setup message shown in the
   panel.
7. Connect an API key, or choose the MCP client path.
8. Select a track, put the edit cursor over audio, and run the first 10-second
   Track Check.

Full instructions are in [Installation](docs/INSTALLATION.md). If the first
check refuses or stalls, use [Troubleshooting](docs/TROUBLESHOOTING.md).

## The important capture limit

Post Mortem only diagnoses a track when Reaper Daemon proves the capture is
that track alone. Item-less routing and bus tracks are supported by the current
isolation path. Ordinary tracks containing media items may be refused rather
than diagnosed from a full-mix render.

That refusal is intentional. Soloing a track by hand does not change the proof
requirement. This is a known product limit, not something the product hides.

## Fix Preview

When a diagnosis contains a supported move, **Preview Fix** rechecks the track,
FX, parameter, and current value before touching anything. It captures a
baseline, applies the proposed value temporarily, captures the candidate, and
restores the original.

The result shows measured deltas and safety guardrails. It does not call the
preview better. You listen and decide.

**Apply Fix** performs a fresh identity check and creates one named REAPER undo
point. One Ctrl+Z returns the project to the previous state. The first release
supports track volume, track pan, FX bypass, and one verified numeric FX
parameter. It does not add or remove plug-ins, rewrite routing, edit items, or
write automation.

## Privacy

- Raw audio stays local and is not sent to the model provider.
- The provider receives measurements plus relevant project metadata, including
  track, plug-in, parameter, and routing details.
- API keys are stored in the local Post Mortem config, not in the project.
- License checks are local and do not contact a license server.
- Feedback records and service logs stay local.
- Version `0.1.0` has no telemetry or crash reporting.

Read [Privacy](docs/PRIVACY.md) for the exact data paths and cleanup behavior.

## Free engine and developer install

This repository contains the MIT-licensed engine, CLI, schemas, provider
adapters, measurement code, and local sidecar. Reaper Daemon remains a
separate MIT project. The docked panel, installer, and licensing code are the
paid layer and are not in this repository.

The free engine is useful on its own, but its install is for developers and
terminal users:

```bash
git clone https://github.com/wretcher207/reaper-daemon.git
cd reaper-daemon
python3 setup/install.py

pipx install git+https://github.com/wretcher207/post-mortem.git
```

Create `~/.config/postmortem/config`:

```text
ANTHROPIC_API_KEY=your-key
REAPER_DAEMON_ROOT=/path/to/reaper-daemon
```

Compatible Anthropic-protocol endpoints use a dedicated key so an unrelated
Anthropic key can never be forwarded to another host:

```text
POSTMORTEM_API_KEY=your-provider-key
ANTHROPIC_BASE_URL=https://provider.example/anthropic
POSTMORTEM_MODEL=your-model
REAPER_DAEMON_ROOT=/path/to/reaper-daemon
```

With REAPER open and the bridge running:

```bash
postmortem "Kick"
postmortem "Kick" --format json
postmortem "Kick" "Bass"
```

Useful options:

```text
--seconds N        capture length, default 10
--keep-wav         keep the local temporary stem
--payload-only     print measured data and skip the model call
--format text|json diagnosis output format; JSON is single-track only
--force            bypass the silence gate only
```

`--force` never bypasses capture isolation.

For the terminal Fix Preview path:

```bash
postmortem "Kick" --format json > diagnosis.json
postmortem preview diagnosis.json
postmortem commit diagnosis.json
```

## Developer references

- [Developing](docs/DEVELOPING.md)
- [Structured results](docs/STRUCTURED_RESULTS.md)
- [Provider adapters](docs/PROVIDER_ADAPTERS.md)
- [Sidecar protocol](docs/SIDECAR_PROTOCOL.md)
- [Product plan](docs/PRODUCT_PLAN.md)
- [Phase 3 implementation record](docs/PHASE_3_IMPLEMENTATION.md)
- [Changelog](CHANGELOG.md)

## License

The code in this repository is MIT licensed. The paid panel and installer are
proprietary with separate commercial terms. Purchases and license delivery are
handled through the Dead Pixel Design storefront.
