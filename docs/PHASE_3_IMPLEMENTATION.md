# Phase 3 Implementation Backlog: Product Shell and Installer ("Kill the Terminal")

**Status:** IN PROGRESS — P3-001 through P3-007 complete; P3-008 live setup smoke, remote Linux graphical installer acceptance, and signed/notarized macOS delivery complete; fresh-machine exit gate remains
**Date:** 2026-07-14
**Target:** PRODUCT_PLAN §12 Phase 3 — a fresh user installs, restarts REAPER,
and finishes their first Track Check without ever opening a terminal
**Depends on:** Phase 2 complete (live-verified 2026-07-12); Reaper Daemon
`v3.10.0` or later

## 1. Outcome

Phase 3 wraps the proven CLI loop (diagnose → preview → commit) in the product
shell: a dockable ReaImGui panel inside REAPER, backed by a packaged Python
sidecar, delivered by an installer that needs no Git, pip, or preinstalled
Python. This is the make-or-break milestone for the 90-day GTM plan — nothing
about the engine changes; everything about who can use it changes.

At the end of this phase:

1. A customer runs one installer, restarts REAPER, and opens the Post Mortem
   panel from the Actions list (or it opens itself on first run).
2. Onboarding connects to the bridge, smoke-tests capture, takes an API key,
   and lands them on the Track screen.
3. Track Check and Fix Preview run entirely inside the panel, with the same
   refusal honesty the CLI has (`STALE_IDENTITY`, `insufficient_signal`,
   confidence field — none of it softened for the UI).
4. Uninstall removes every Post Mortem-owned file and nothing else. Shared
   REAPER extensions remain available to other scripts.

The CLI stays fully supported. The panel is a THIN client: no analysis,
validation, or orchestration logic in Lua. Everything the panel does must be
reproducible from the terminal, which is also how we test it.

## 2. Scope boundary

### Included

- Job-folder sidecar service (`postmortem/service.py`) and its protocol spec.
- New bridge commands the panel needs: `get_selected_track`,
  `get_capture_preflight`.
- Docked ReaImGui panel: header/status, Track screen (idle → progress →
  result), Fix Preview screen (guardrails, Apply / Adjust / Keep Original).
- First-run onboarding with guided recovery for every known setup failure.
- Packaged sidecar (bundled Python) for Windows, macOS, Linux.
- Installer, updater, uninstaller.
- License validation with an offline grace period.

### Not included

- Mix Check, session graph, issue ranking (Phase 4).
- History/SQLite, Sonic Memory, feedback retrieval (Phase 5). The panel's
  "Not Helpful" button writes a local JSONL stub so no feedback is lost, but
  no history UI ships in Phase 3.
- Hosted analysis / starter credits (Phase 5). Onboarding's analysis-access
  step offers BYO API key and MCP only; the starter-credits slot is designed
  in but disabled.
- Loudness-matched A/B monitoring path. Phase 3 A/B is
  preview-capture playback of the baseline/candidate WAVs; live in-project
  A/B transport switching is a Phase 4 refinement.
- Solving amp-sim capture isolation for item-based tracks. Still the biggest
  open engine problem (see HANDOFF), still not a Phase 3 gate. The panel must
  EXPLAIN the refusal in plain language, not fix it.
- New proposal operations. Same four: volume, pan, FX bypass, FX param.

## 3. Repository ownership

| Workstream | Repository | Primary files |
|---|---|---|
| `get_selected_track`, `get_capture_preflight` | `reaper-daemon` | `bridge/reaper_agent_bridge.lua`, `bridge/command_schema.md`, `tests/` |
| Watchdog JSON-lock fix (pre-existing chip) | `reaper-daemon` | `setup/` startup block, `__startup.lua` template |
| Sidecar service + job protocol | `post-mortem` | `postmortem/service.py`, `docs/SIDECAR_PROTOCOL.md`, `tests/test_service.py` |
| ReaImGui panel | `post-mortem` | `panel/` (new top-level dir, Lua) |
| Onboarding + guided recovery | `post-mortem` | `panel/`, `postmortem/service.py` |
| Packaging (PyInstaller) | `post-mortem` | `packaging/`, CI workflows |
| Installer / updater / uninstaller | `post-mortem-panel` (private) | `installer/` |
| Licensing | `post-mortem` | `postmortem/licensing.py`, `tests/test_licensing.py` |
| Docs | both | README, command schema, this backlog |

## 4. Decisions needed (recommendations inline, none block the early PRs)

1. **Sidecar packaging tool.** Recommendation: PyInstaller **onedir** builds
   per platform. The engine is numpy + stdlib + one HTTP client; no exotic
   imports. Onedir over onefile: faster startup, updater can diff, no
   temp-extraction antivirus flags.
2. **Installer technology.** Recommendation: per-platform native minimal —
   Inno Setup (Windows), signed `.pkg` or drag-install `.dmg` + first-run
   setup (macOS), tar.gz + `install.sh` AND the panel's self-setup path
   (Linux). Cross-platform installer frameworks add weight without buying
   trust. Signing (P6 hardening) is out of scope but the layout must not
   preclude it.
3. **License mechanism.** Recommendation: Ed25519-signed license file
   (keygen-style, fully offline verification), issued at purchase. "Offline
   grace period" then only matters for update-entitlement checks, not for
   running the product. Payment platform (Gumroad / Lemon Squeezy / Stripe)
   is David's call and only gates P3-009's issuing side, not the validation
   code.
4. **Panel code repo boundary.** PRODUCT_PLAN locks the panel as paid /
   proprietary, and existing MIT code stays MIT. Recommendation: develop
   `panel/`, `packaging/`, and `licensing.py` in this repo on branches as
   usual, but DO NOT publish them in any public release artifact until the
   license text and repo boundary review happens (tracked as part of P3-009).
   If this repo is public, that review must happen BEFORE the first panel PR
   merges — verify visibility before P3-003.
5. **Dependency strategy for ReaImGui and SWS.** The panel requires the
   ReaImGui extension; render auto-close requires SWS. ReaPack remains a valid
   user-managed source, but its documented ReaScript API does not provide a
   supported specific-package install operation (tracked upstream in
   [ReaPack issue #37](https://github.com/cfillion/reapack/issues/37)). Release
   builds therefore fetch pinned official binaries for both extensions,
   verify committed SHA-256 values, and place only the verified target files
   into the hash-manifested release payload. Setup installs a dependency only
   when its target file is absent, never overwrites an existing copy, and
   leaves shared extensions in place on uninstall. Onboarding still verifies
   availability and presents a plain-language recovery path when refused.

## 5. Delivery sequence

### P3-001 — Sidecar service and job-folder protocol

**Repository:** `post-mortem`
**Priority:** Blocking (everything panel-side builds on this)

Create `postmortem/service.py`: a long-running process that watches a jobs
folder and executes engine calls. Same architecture as the bridge — atomic
JSON files, no sockets — because it is proven, debuggable, and firewall-inert.

Rules:

1. App-data root is platform-appropriate (`~/Library/Application
   Support/PostMortem`, `%APPDATA%\PostMortem`, `$XDG_DATA_HOME/postmortem`),
   never the repo folder. Layout per PRODUCT_PLAN §9: `config.json`,
   `jobs/inbox|processing|outbox`, `captures/`, `logs/`.
2. Job types: `get_status`, `track_check`, `preview_fix`, `commit_fix`,
   `cancel_job`, `record_feedback`. Each maps onto the EXISTING engine
   functions (`diagnose`, `preview.py`, `cli.py` internals refactored as
   needed — no new analysis logic).
3. Every job file carries `id`, `type`, `created_at`, `payload`; every result
   carries `ok`, typed `error` on failure, and structured progress. Long jobs
   (capture, model call) write progress updates the panel can poll
   (`stage: reading_track | capturing | measuring | diagnosing`), mirroring
   PRODUCT_PLAN §6.2's human steps.
4. A sidecar heartbeat file (pid, version, `updated_at`) lets the panel
   distinguish "sidecar busy" from "sidecar dead" — the same lesson the
   bridge learned.
5. Crash safety: `preview_fix` reuses the Phase 2 try/finally restore
   contract; a killed sidecar leaves the bridge-side preview to the bridge's
   own startup recovery (verified in P2-005). The sidecar never introduces a
   new mutation path.
6. Protocol documented in `docs/SIDECAR_PROTOCOL.md` — this is the UI-agnostic
   contract that lets a future companion window exist without engine changes.

Acceptance criteria:

- Full fake-bridge test: submit `track_check` job file → structured result in
  outbox, progress stages observed, WAV verification rule enforced.
- Killing the sidecar mid-`preview_fix` never leaves the project mutated
  (fake-bridge test per step, same pattern as P2-004).
- Malformed job files produce typed errors in outbox, never crashes.
- CLI behavior unchanged; service is additive.

### P3-002 — Bridge commands for the panel

**Repository:** `reaper-daemon`
**Priority:** Blocking (parallel with P3-001)

Add `get_selected_track` and `get_capture_preflight` (PRODUCT_PLAN §9).

Rules:

1. `get_selected_track` returns GUID, name, index, FX count, and whether
   audio exists under the edit cursor / active time selection — enough for
   the Track screen's idle card to update on selection change by polling.
2. `get_capture_preflight` reports, without rendering: risk-gate state
   (`allow_risk_level_3`), SWS presence, render auto-close state, and
   whether a capture would currently be refused and why. This powers
   onboarding's checklist and "Test Again" without a 10-second render per
   check.
3. Additive only; existing command semantics untouched.

Acceptance criteria:

- Lua tests for both commands (validation + reply shape).
- Preflight correctly reports each known gate in fake configurations.
- Ships per that repo's rules: PR → CI → merge → tag `v3.11.0` → `@version`
  + `index.xml` in lockstep.

**Related, pre-existing:** the `__startup.lua` watchdog still parses the
pre-3.1 numeric lock format and cannot detect a dead JSON-lock bridge (fix
task already spawned as a chip). The installer (P3-008) manages the startup
block, and guided recovery (P3-006) assumes a working watchdog — land that fix
before P3-006 live verification.

### P3-003 — Panel skeleton and Track screen (idle)

**Repository:** `post-mortem`
**Priority:** High
**Depends on:** P3-001, P3-002, decision 4 (repo visibility check)

Create `panel/` — the ReaImGui Lua client. Skeleton first: docking, header,
status plumbing, job-folder client, styling. No diagnosis flow yet.

Rules:

1. Thin client, hard rule: the panel reads/writes job files and renders
   results. Any logic beyond formatting and state display belongs in the
   sidecar.
2. Persistent header per PRODUCT_PLAN §5: wordmark, project name, bridge
   status pill (Ready / Busy / Needs REAPER / Needs Attention), model state,
   settings button.
3. The panel launches the sidecar on demand (`reaper.ExecProcess`) when the
   heartbeat is stale, and reports plainly when it cannot.
4. Visual language per PRODUCT_PLAN §7: near-black `#0a0a0a`, bone text,
   blood-red accents reserved for primary actions and changed values, mono
   for measurements, amber for uncertainty, teal/green for passed checks.
   No scanlines inside the working panel.
5. Track screen idle state: selected-track card (via `get_selected_track`
   polling), capture source + duration controls (default 10 s, advanced
   5–60 s), primary **Check This Track** button; "Select a track in REAPER"
   empty state that self-updates.

Acceptance criteria:

- Panel docks, redocks, and survives REAPER restart (remembers dock state).
- Selection changes in REAPER update the idle card within one poll cycle.
- Dead sidecar and dead bridge each produce a distinct, plain-language status
  with a retry action, not a frozen UI.
- All strings live in one Lua table (single place to review tone).

### P3-004 — Track Check flow in the panel

**Repository:** `post-mortem`
**Priority:** High
**Depends on:** P3-003

Rules:

1. Progress state renders the sidecar's human stages; cancellation is
   available until the model request begins (job `cancel_job`), matching
   PRODUCT_PLAN §6.2. The UI never looks frozen during a render.
2. Result state, in order: main finding (plain language), suggested fix row
   (Track / Plug-in / Parameter / Current → Proposed), why-this-may-help,
   confidence badge with reason, collapsed Evidence section (LUFS, peak,
   crest, spectrum, stereo, routing, FX).
3. Measured vs inferred labels are visually distinct (product principle 2).
4. Every engine refusal (`insufficient_signal`, capture-scope gate, silence
   gate, model refusal) renders its plain-language explanation — the honesty
   contract's hedges are product features, not errors to hide. Amp-sim
   isolation refusal gets its specific explanation ("this track's FX don't
   render in isolation — Post Mortem can't capture it alone yet").
5. Actions: **Preview Fix** (primary), **I'll Fix It Myself** (closes action
   area, keeps report), **Not Helpful** (writes feedback stub).

Acceptance criteria:

- Fake-sidecar UI test checklist (scripted job results → rendered states)
  covering: actionable proposal, explanation-only result, every refusal type.
- No code path renders the word "better" or an unhedged claim; the outcome
  strings come from the structured result, never composed in Lua.

### P3-005 — Fix Preview flow in the panel

**Repository:** `post-mortem`
**Priority:** High
**Depends on:** P3-004

Rules:

1. Preview runs the Phase 2 sequence via `preview_fix`; the panel shows the
   exact change, goal metric, guardrail results (pass / warn / fail with the
   locked outcome sentence), and per-metric deltas.
2. A/B in Phase 3 = playback of the baseline and candidate capture WAVs from
   the panel (temporary media preview, not project mutation). Label honestly:
   "captured preview," not live monitoring.
3. **Apply Fix** runs `commit_fix` (fresh re-verification inside the engine,
   ONE named undo point — the Phase 2 contract, unchanged). **Keep
   Original** does nothing to the project (preview already restored) and
   says so. **Adjust** offers the conservative slider around the proposed
   value (clamped to the Phase 1 move limits), then re-runs preview with the
   adjusted value.
4. Stale-identity refusals between diagnose and preview/apply render the
   specific mismatch in plain language.

Acceptance criteria:

- Fake-sidecar tests: guardrail warn/fail rendering, stale refusal, Adjust
  loop produces a new preview job with the clamped value.
- Live sanity: preview → Apply on a routing track yields exactly one undo
  point; preview → Keep Original leaves routing state byte-identical.
- Adjust can never exceed the deterministic move limits, regardless of UI
  state.

### P3-006 — Onboarding and guided recovery

**Repository:** `post-mortem`
**Priority:** High
**Depends on:** P3-003; watchdog chip fix landed

The four-step flow from PRODUCT_PLAN §6.1, plus recovery paths for every
setup failure we have actually hit.

Rules:

1. Step 1 Welcome: the promise plus the three trust statements (never changes
   the project during analysis; previews are temporary; raw audio stays
   local). Primary action **Connect to REAPER**.
2. Step 2 Automatic setup: checklist driven by `get_capture_preflight` and
   bridge status — found REAPER, bridge running, capture enabled, panel
   registered. Each failed item has an illustrated, specific fix path:
   - Render auto-close off + no SWS → the one illustrated manual step
     ("Automatically close when finished") + **Test Again**.
   - `allow_risk_level_3` off → explain, flip via config, "restart REAPER"
     instruction (it is read once at startup — known fact, do not pretend a
     reload works).
   - Bridge dead / lock stale → watchdog-informed guidance.
3. Step 3 Analysis access: **Connect an API key** (validated with one cheap
   live call before accepting) and **Use through an MCP client** (advanced).
   Starter-credits button present but disabled ("coming soon") — the Phase 5
   slot, designed in now. No model-name choice; the tested default from
   config, model selection lives in Advanced Settings.
4. Step 4 First success: prompt to select a track with audio, run a real
   10-second Track Check. Onboarding completes ONLY when a real diagnosis
   has rendered (exit criterion, not a skippable tour).
5. Every recoverable failure the engine can report has a mapped plain-language
   explanation and next action. Unknown errors show the typed error code and
   a "copy diagnostics" action rather than a fake explanation.

Acceptance criteria:

- Scripted fake-preflight matrix: each known failure state renders its
  specific recovery screen.
- A fresh config (no key, no history) reaches the Track screen only through
  completed onboarding; a configured install never sees onboarding again
  unless setup breaks.
- Onboarding strings reviewed against the voice rules (no "workflow", no
  empty superlatives, plain musician language).

**Completed 2026-07-12.** The private panel now runs the four-step first-use
flow and reopens setup only when a completed install loses bridge, sidecar, or
capture readiness. The public sidecar adds preflight-backed status,
capture-gate configuration with an explicit REAPER restart, and one-token live
provider validation before a supplied key is saved. The MCP path is functional:
Reaper Daemon requires a fresh verified 10-second single-track handoff before
accepting the client model's diagnosis, and the panel renders that diagnosis
before exposing the Track screen. Known service,
provider, capture, identity, evidence, and proposal failures have distinct
plain-language next actions; unknown typed codes alone offer Copy Diagnostics.
The scripted setup matrix and thin-client rendering pass in 161 panel checks.
Public engine recovery and verification pass 332 tests plus 8 subtests,
compileall, and package build. Reaper Daemon's JSON-lock-aware startup watchdog
and sidecar-owned MCP diagnosis handoff pass 122 Python tests plus 175 Lua
checks; v3.11.1 carries the watchdog fix.

### P3-007 — Packaged sidecar builds

**Repository:** `post-mortem`
**Priority:** High
**Depends on:** P3-001 (parallel with panel work)

Rules:

1. PyInstaller onedir spec under `packaging/`, one artifact per platform:
   `postmortem-sidecar` bundling Python, numpy, and the engine. The CLI
   entry point ships inside the same bundle (`postmortem-sidecar cli ...`)
   so power users still get the terminal without pip.
2. CI matrix builds all three platforms on every release tag; artifacts
   uploaded with checksums. Build must run the unit suite against the BUNDLED
   binary (not the venv) before an artifact is accepted — a packaged import
   miss is the classic PyInstaller failure and it must fail CI, not a
   customer.
3. Version stamped into the binary and reported in `get_status` and the
   panel's settings screen.

Acceptance criteria:

- On each platform: bundled binary passes the smoke suite (payload-only run
  against the fake bridge, WAV analysis golden test) with system Python
  absent from PATH.
- Binary size and cold-start time recorded in the PR (baseline for updater
  decisions).

**Completed 2026-07-12.** The public repo now ships a PyInstaller onedir spec
and unified `postmortem-sidecar` launcher. The frozen binary runs the service by
default, exposes the existing CLI through `postmortem-sidecar cli ...`, reports
the stamped engine version, and executes the separately installed `reaperd.py`
inside its bundled Python runtime. Release tags build macOS, Windows, and Linux
artifacts, run the complete suite inside the frozen interpreter plus the
black-box frozen-binary smoke, archive
each onedir folder, and upload SHA-256 and metrics files. The macOS arm64 bundle
passes payload-only capture and the 1 kHz WAV golden test with system Python
absent from `PATH`: 54,389,155 bytes on disk, 22 MB compressed, and 0.1495s
median process cold start. Both the source and frozen interpreters pass 340
tests plus 8 subtests; the
panel reports the sidecar's `0.1.0` engine version and passes 163 Lua checks.

### P3-008 — Installer, updater, uninstaller

**Repository:** `post-mortem-panel` (private paid-product boundary)
**Priority:** High
**Depends on:** P3-006, P3-007

The installer sequence from PRODUCT_PLAN §4:

1. Detect the REAPER resource directory (all platforms, portable installs
   included; ask with a pre-filled path when ambiguous).
2. Install/update Reaper Daemon (managed `__startup.lua` block — same
   BEGIN/END markers as `setup/install.py`, with the abort-on-BEGIN-without-END
   guard) and the Post Mortem panel script.
3. Install the packaged sidecar into the app-data root.
4. Configure bridge auth and capture permission (`allow_risk_level_3`).
5. Install checksum-locked ReaImGui and SWS binaries when missing (decision
   5), preserving any existing copies.
6. Register the panel in the Actions list.
7. Bridge + capture smoke test (via preflight, not a blind render).
8. Offer to launch REAPER / explain the restart.

Updater: replace sidecar + panel + bridge in place, preserving `config.json`,
license, and history; never touch user projects. Uninstaller: remove the
managed startup block (markers only), panel script, sidecar, and Actions
entry; leave shared ReaImGui/SWS extensions in place; ASK about app-data
(config/history) rather than silently deleting it.

Acceptance criteria:

- Fresh VM per platform: installer → REAPER restart → onboarding → first
  Track Check, zero terminal use. This IS the phase exit criterion.
- Uninstall leaves `__startup.lua` exactly as it would be without us (byte
  comparison around the markers), and leaves unrelated startup lines alone.
- Update preserves config and license; a killed update is resumable or
  cleanly re-runnable.
- Installer refuses politely (with the reason) on unsupported REAPER
  versions rather than half-installing.
- Release assembly refuses a dependency whose file list, metadata, version,
  source URL, notice, or SHA-256 differs from the committed target lock.
- Install and update preserve existing ReaImGui/SWS bytes; uninstall leaves
  both shared extensions available to other REAPER scripts.

**In progress 2026-07-12.** The private panel repo now contains the
transactional installer core, updater, uninstaller, payload assembler and
SHA-256 manifest validation. It preserves existing product and bridge config,
restores a pre-existing standalone Reaper Daemon startup block on uninstall,
keeps unrelated Actions entries, and has macOS, Windows, and Linux CI coverage
configured in the private repo. Private panel PR #5 added the unsigned native
macOS AppKit setup app and architecture-labeled disk-image builder. The native
app discovers standard, stored, environment-provided, and Spotlight-indexed
portable REAPER resources; asks on ambiguity; installs or updates through the
same transactional core; confirms before deleting app data; and refuses
uninstall without Post Mortem ownership state. Its payload travels through
PyInstaller as an opaque archive so nested sidecar binaries retain their
manifest hashes.

The 120-file arm64 production payload passes install, update, default-retain
uninstall, explicit app-data removal, and second-uninstall refusal from the app
inside a mounted read-only DMG. The 62 MB app compresses to a 44 MB image; its
SHA-256 and APFS image checksums verify. Private workflow `29220526141` passes
the installer and Lua suites on macOS, Windows, and Ubuntu. macOS signing and
notarization remain a later release-hardening gate, not part of this unsigned
wrapper slice.

Private panel PR #6 added the native Windows delivery slice: a per-user Inno
Setup wrapper around the same transactional core. The payload travels as an
opaque archive so packaged sidecar hashes stay byte-exact, the setup validates
a standard or portable REAPER resource path before any mutation, and native
uninstall stays ownership-safe: it refuses without Post Mortem install state
and preserves app data unless deletion is explicitly requested. Windows CI
assembles the production x86_64 payload from the public engine, compiles the
setup executable with Inno Setup, and exercises it black-box: refused install
on an invalid resource path (leaving no data behind), install, license-
preserving update, default-retain uninstall with byte-exact startup
restoration, uninstall refusal after ownership-state removal, and explicit
app-data deletion. The 35 MB `PostMortem-0.1.0-windows-x86_64.exe` and its
SHA-256 sidecar pass on panel main workflow `29223831872` across all macOS,
Windows, and Ubuntu jobs. Windows code signing remains a later
release-hardening gate. The macOS signing and notarization path was completed
subsequently in private PR #13.

Private panel PR #7 added the native Linux delivery slice: an unsigned
architecture-labeled `tar.gz` release containing the same frozen installer
(payload carried as an opaque archive) plus a POSIX `install.sh` wrapper that
defaults to install and passes `update` and `uninstall [--remove-app-data]`
through. Ubuntu CI assembles the production x86_64 payload from the public
engine, builds the release with its SHA-256 sidecar, and exercises the
extracted archive black-box against an isolated portable REAPER resource
directory: refused install on an invalid resource path, install,
license-preserving update, default-retain uninstall with byte-exact startup
restoration, uninstall refusal after ownership-state removal, and explicit
app-data deletion. The 73 MB `PostMortem-0.1.0-linux-x86_64.tar.gz` passes on
panel main workflow `29224398360` across all macOS, Windows, and Ubuntu jobs.

Private panel PR #8 added the shared REAPER version gate. Install and update
read REAPER's own NUL-tolerant `reaper-install-rev.txt` resource marker and
refuse missing, malformed, or pre-7 versions before changing installer-owned
files. macOS and Linux inherit that check directly from the transactional
core. Windows also extracts the frozen helper into Inno's temporary directory
and runs the same `preflight` command during `PrepareToInstall`, before Inno
copies wrapper files or writes Add/Remove Programs registration; the core
repeats the gate as defense in depth. Native acceptance exercises a REAPER
6.83 refusal before continuing with 7.77. The Windows path additionally proves
that refused fresh install and refused update preserve the wrapper,
uninstaller, runtime, panel, startup bytes, and app data byte-for-byte, with
the uninstall registry unchanged. Panel PR workflow `29226600640` passes all
eight checks; its Windows job reports 42 passed tests plus 8 platform skips.
Panel main workflow `29226755956` then passes every macOS, Windows, and Ubuntu
job and completes the production setup lifecycle with a 36,211,542-byte x86_64
executable.

Private panel PR #9 adds checksum-locked dependency delivery. The release lock
pins official ReaImGui `0.10.0.5` and SWS `2.14.0.7` binaries for macOS arm64,
macOS x86_64, Windows x86_64, and Linux x86_64, plus their upstream license
notices. The fetch step verifies every download before replacing a previously
verified bundle. Payload assembly independently reloads the lock, checks the
exact target file list and metadata, and rehashes every binary and notice
before replacing its output or writing the payload manifest. This closes the
post-fetch tampering gap rather than trusting a directory merely because the
fetch step created it.

The installer validates the dependency target against the current OS and
architecture before mutation, stages missing binaries as a pair, rolls both
back on copy failure, refuses occupied non-file destinations, and never
replaces an existing extension. Update keeps the installed bytes even when a
newer payload is supplied. Uninstall removes the managed notices with the
Post Mortem runtime but deliberately leaves both shared REAPER extensions in
`UserPlugins`. Native macOS, Windows, and Linux lifecycle coverage proves the
missing-install, update-preservation, refusal, and uninstall-retention paths.
The local suite passes 59 installer tests with two expected non-macOS skips,
the panel still passes 163 Lua checks, all four locked targets fetch live, and
the real arm64 production payload builds into a 47 MB macOS DMG with a
matching SHA-256 sidecar. PR workflow `29232340100` passes every macOS,
Windows, and Ubuntu check. Its production lifecycle validates a 39,536,187-byte
Windows setup executable and a 79,166,564-byte Linux archive, then retains
both artifacts with their SHA-256 sidecars. Panel main workflow `29232521710`
repeats the full green matrix after merge.

Public engine PR #30 added the read-only installer readiness boundary. The
`postmortem-sidecar setup-smoke` command calls only bridge status and Reaper
Daemon `get_capture_preflight`, emits one structured JSON report, returns `0`
when safe capture is ready and `3` with engine-owned recovery when it is not,
and fails closed on malformed preflight data. The panel and installer consume
the same setup verdict, while installer output omits `panel_registered`
because an external setup probe cannot observe that state. The final public
suite passes 345 tests plus 8 subtests. Its frozen smoke metrics record a
54,391,651-byte bundle, launcher SHA-256
`c3c56f2b262056386a6fe1b5372873f1b80665ad229c41768f943d46f8ca7545`,
0.1396-second median cold start, and successful execution without system
Python on `PATH`. PR workflow `29247403581` and post-merge main workflow
`29247483323` pass the complete platform/Python matrix and bundled smoke.

Private panel PR #10 added `python -m installer smoke`, automatic non-rollback
smoke after install/update, exact recovery propagation, a 65-second outer
timeout around the engine's 55-second worst case, Linux wrapper dispatch, and
native Test Setup controls for macOS and Windows. The private suite passes 67
tests with two expected platform skips, and the panel passes all 163 Lua
checks. Windows CI also caught an Inno character-code continuation and an old
native acceptance fixture that still used text bytes as a fake executable;
both root causes now have regression coverage, and silent setup remains
explicitly non-interactive. Final PR workflow `29248730068` and post-merge
main workflow `29249064637` pass every installer and Lua job, including the
production Windows setup and Linux release lifecycles. CodeRabbit and
GitGuardian pass on both implementation PRs.

The final reviewed payload produced a 49,324,507-byte macOS arm64 DMG with
SHA-256 `f3886d0224609cd86bb395344067a1dd5bf9dbd4d69f7b326b6da8ebd4fe9b1b`.
That exact payload was installed into the real REAPER resource and Post Mortem
data roots, then REAPER was closed normally and restarted. Against the live
`j-space.RPP` session, the customer-facing installed smoke returned exit `0`
and reported the bridge connected with safe capture ready. The raw report had
no blockers or warnings, and daemon history showed `get_capture_preflight`
with no capture command. REAPER remained idle with its transport, cursor,
selected track, and selected item unchanged.

The live installer smoke gate is therefore closed. The only remaining P3-008
gate is fresh-machine install on macOS, Windows, and Linux through REAPER
restart, onboarding, and the first Track Check with zero terminal use. P3-008
is not complete until that phase exit criterion passes on every supported
platform.

Private panel PR #11 then hardened release checksum portability after staging
the customer artifacts exposed a Windows newline defect. All three native
builders now use one binary writer for exact ASCII checksum records with LF
line endings, and their tests inspect raw bytes instead of normalized text.
PR workflow `29250533231` and main workflow `29250770020` passed every
installer and Lua job. The rebuilt main Windows executable has SHA-256
`5b9218f72cae14211cd09b2a47eb7fccc62340bb98046a68cf16e0e064004280`,
and its exact sidecar passes macOS `shasum -a 256 -c`.

Private panel commits `087312c` and `82eb6df` then moved the unfinished Linux
graphical-installer proof off the local desktop and onto a fresh GitHub-hosted
Ubuntu runner under Xvfb. The bounded driver launches the actual production
`Post Mortem Setup` executable from isolated HOME, XDG, REAPER, and product
data roots, performs the real graphical Install action, and verifies the
panel, public sidecar, daemon, bridge, auth/runtime configuration, install
state, managed startup and Actions hooks, ReaImGui, and SWS. Main workflow
`29288223371` passed all six jobs; the graphical install completed in 3.189
seconds, the separate headless Linux lifecycle passed, and the Linux artifact
was retained. This closes automated Linux graphical-installer acceptance. It
does not replace the fresh-machine REAPER restart, onboarding, and first Track
Check journey required to close P3-008.

Private panel PR #13 completed the production macOS trust path. Payload
assembly now signs every copied Mach-O after checksum-lock verification and
before writing manifest schema 2, which covers both signed file bytes and the
literal targets of PyInstaller's relative `Python.framework` links. Archive
extraction rejects escaping, dangling, cyclic, or nested-link paths and install
preserves the validated framework structure. The outer builder signs nested
code from the inside out with hardened runtime and secure timestamps, signs
the app and DMG with David's Developer ID, submits both to Apple's notary
service, staples both tickets, and writes the checksum only after stapling.

Live release acceptance then exposed a macOS 27 disk-image race: deprecated
`hdiutil create` could return while an attached image helper still held the
output inode, blocking `notarytool` before upload. Follow-up private PR #14
uses Apple's current `diskutil image create from` command when available. It
feature-probes that command and retains the older `hdiutil` path with
fresh-inode publication as a compatibility fallback. The final build left no
attached Post Mortem image or `diskimages-helper` process.

Apple accepted final app submission `8c22c014-e55a-493e-a4fb-f2e349cda9bf`
and final DMG submission `55376e82-c961-42de-b83e-4a8683da28ba`. Deep
signature, stapler, APFS image, and checksum validation all passed. Gatekeeper
accepted both the app and image with `source=Notarized Developer ID`. The
exact final arm64 DMG is 35,674,185 bytes with SHA-256
`79f7618535ff271c9738ef2cee715e67bf4b89f8a454bd64fef1a83247290d89`, and
the accessibility-driven real AppKit install from that image passed in 3.754
seconds. The local installer suite passes 91 tests with two expected platform
skips, and the panel passes all 166 Lua checks. Private PR workflows
`29304768933` and `29305506240` passed every macOS, Windows, and Ubuntu
installer/Lua job plus CodeRabbit and GitGuardian. Post-merge main workflows
`29304923701` and `29305660057` repeated the complete green matrix, ending at
private commit `43e9e91`.

Signing, notarization, stapling, Gatekeeper, and the macOS setup-button path are
therefore closed. P3-008 remains open for the manual ReaImGui onboarding and
first Track Check on macOS, followed by fresh Windows and Linux customer
journeys through their first Track Check.

### P3-009 — License validation

**Repository:** `post-mortem`
**Priority:** Medium (must not gate panel development; must gate release)

Rules:

1. `postmortem/licensing.py`: Ed25519-signed license file validation, fully
   offline. Fields: holder, product, major version, issue date, signature.
2. Grace behavior: a valid license never phones home to RUN. Online checks
   (if any) only gate update entitlement, with a generous offline grace
   period and a plain statement in Settings of exactly what is checked.
3. The FREE surface (engine, CLI, MCP) never imports licensing. The license
   gates the panel + installer convenience layer only — the open-core
   boundary from PRODUCT_PLAN §11.
4. Unlicensed panel state: clear purchase path, no dark patterns, engine and
   CLI keep working. (Exact trial behavior is David's call; default to
   panel-requires-license, CLI free.)
5. This PR includes the repo-boundary/license-text review from decision 4 —
   the checklist item that must close before any public release artifact
   contains `panel/` or `licensing.py`.

Acceptance criteria:

- Unit tests: valid, tampered, expired-updates, wrong-product, wrong-major
  licenses; clock skew tolerance.
- No network call exists in the validation path (test asserts it).
- Free-surface imports verified license-free by a test that imports every
  free module with `licensing.py` deleted.

### P3-010 — Live verification protocol

**Repository:** both
**Priority:** High
**Depends on:** P3-001 through P3-008

The Phase 2 discipline, applied to the shell:

1. Fresh-machine install test per platform (VM or clean user account):
   installer → restart → onboarding → first Track Check without a terminal.
   Timed; install-to-first-diagnosis is the metric that matters (§13).
2. Panel preview loop on the real rig (Kick routing track): preview, A/B
   playback, Apply, single Ctrl+Z restores — the P2-005 pass, driven from
   the panel.
3. Recovery drills: kill the sidecar mid-preview (panel reports, bridge
   restores); kill REAPER mid-preview (startup recovery reported in panel
   status); break the render auto-close setting and verify onboarding's
   "Test Again" catches it.
4. Uninstall → reinstall → license and config survive as designed.
5. Amp-sim guitar track (GEETS): verify the panel renders the isolation
   refusal explanation, not a hang or a fake result.

Record results here and in both HANDOFFs. Same hard rules: capture verified
only by WAV mtime + size; Lua edits need a REAPER reload before any live
claim; David's ear confirms anything audible.

### P3-011 — Documentation

**Repository:** both
**Priority:** Medium
**Depends on:** all prior

- `docs/SIDECAR_PROTOCOL.md` finalized against the shipped implementation.
- Command schema entries for the two new bridge commands.
- README: installer-first quickstart; CLI demoted to the developer section.
- PRODUCT_PLAN Phase 3 exit criteria checked off with evidence links.
- User-facing install/troubleshooting page content (voice-profile rules
  apply; it is public copy).

## 6. Definition of done

Phase 3 is complete only when (PRODUCT_PLAN §12 Phase 3 exit criteria, plus
the standing rules):

1. A fresh user on each platform can install, restart REAPER, and finish
   their first Track Check without opening a terminal — demonstrated on
   clean machines, not claimed from a dev box.
2. Uninstall removes managed files without touching user projects or
   unrelated startup-script content.
3. The panel explains every recoverable setup failure in plain language;
   unknown failures show typed errors and a diagnostics path, never a
   fake explanation.
4. All existing tests pass in both repos; new service, panel-state, and
   licensing tests pass across the CI matrix; bundled binaries pass the
   suite on all three platforms.
5. No panel or sidecar code path weakens the honesty contract, adds a
   mutation path outside the Phase 2 preview/commit lifecycle, or claims
   improvement without the goal metric moving.
6. The license boundary review (decision 4) is closed before any public
   artifact ships panel/licensing code.

## 7. Recommended pull request sequence

1. **Post Mortem:** sidecar service + protocol doc + fake-bridge tests
   (P3-001).
2. **Reaper Daemon:** `get_selected_track` + `get_capture_preflight` + tests,
   tag v3.11.0 (P3-002). Land the watchdog lock-format chip fix around here.
3. **Post Mortem:** panel skeleton + Track idle screen (P3-003).
4. **Post Mortem:** Track Check flow (P3-004).
5. **Post Mortem:** Fix Preview flow (P3-005).
6. **Post Mortem:** onboarding + guided recovery (P3-006).
7. **Post Mortem:** packaged builds + CI matrix (P3-007).
8. **Post Mortem:** installer/updater/uninstaller (P3-008).
9. **Post Mortem:** licensing + boundary review (P3-009).
10. **Both:** live verification notes + docs (P3-010, P3-011).

Keep the daemon PRs small (the bridge is in daily use), and keep the panel
thin — every time logic wants to live in Lua, it belongs in the sidecar.
