# Post Mortem Product and Implementation Plan

**Status:** Active; Phase 3 live verification remains open
**Date:** 2026-07-10, updated 2026-07-14
**Primary audience:** Bedroom musicians and self-producing artists using REAPER
**Product owner:** Dead Pixel Design

## 1. Executive decision

Post Mortem should be positioned as **the REAPER mix debugger**:

> Find the highest-leverage problem, try one safe fix, and prove whether it helped.

Reaper Daemon remains the free, open local control layer. Post Mortem becomes the inexpensive paid product built on top of it. It should not try to replace the user's taste, automatically remix the entire song, or compete feature-for-feature with large plug-in suites. Its advantage is that it understands the user's actual REAPER project, works with the plug-ins already in the session, and distinguishes measured facts from informed guesses.

The first commercial release should make three connected capabilities feel like one product:

1. **Track Check:** diagnose one selected track.
2. **Fix Preview:** temporarily try one recommendation, capture before and after, and let the user apply or reject it.
3. **Mix Check:** scan a selected section and rank the three most important session-level issues.

Sonic Memory and revision comparison should follow once the first three flows are trustworthy.

## 2. Customer and job to be done

### Primary customer

A musician who writes, records, and mixes their own music in a bedroom or small home studio. They understand basic production language but do not always know whether a problem is caused by EQ, compression, level, routing, phase, or arrangement. They may own several good plug-ins without feeling confident using them.

Likely characteristics:

- Uses REAPER because it is affordable and flexible.
- Has an untreated or partially treated room.
- Finishes fewer songs than they start because mixing becomes an endless loop.
- Wants specific help, not a lecture or twenty generic tips.
- Is price-sensitive and suspicious of subscriptions.
- Wants to remain in control of the sound.

### Core job

> When something in my mix feels wrong and I do not know why, help me find the most likely problem, show me the evidence, and let me safely hear one practical fix.

### Secondary customers

- Intermediate producers who want a fast second opinion.
- Musicians learning mixing through their own sessions.
- Freelance engineers who want a quick technical QA pass before delivery.

The interface and copy should be written for the primary customer. Advanced details can expand on demand.

## 3. Product principles

1. **One problem, one move.** Do not overwhelm the user with a checklist.
2. **Measured and inferred are visually different.** The user should know what the system proved and what it merely suspects.
3. **Nothing changes without an explicit click.** Analysis is read-only. Preview is temporary. Apply creates one undo point.
4. **The ear decides.** Metrics support an A/B decision; they do not declare a creative winner.
5. **No terminal in the normal experience.** Installation, updates, model setup, diagnosis, and recovery belong in the product UI.
6. **Audio stays local by default.** Model providers receive measurements and project metadata, not raw audio, unless a future feature clearly asks permission.
7. **Plain language first.** Explain “the vocal is fighting the guitars around the presence range” before displaying band tables.
8. **Do not fake certainty.** Silence gates, confidence ratings, coarse-spectrum caveats, and refusal behavior remain product features.

## 4. Product architecture and packaging

### Components

#### Reaper Daemon Core — free and MIT

Responsibilities:

- Local REAPER state discovery.
- Track, FX, routing, marker, region, and transport commands.
- Safe mutation and undo blocks.
- Track, bus, and master capture.
- MCP and CLI access for developers and agent users.

It should contain no licensing or billing logic. Its value is trust, adoption, and ecosystem reach.

#### Post Mortem Engine — local analysis

Responsibilities:

- Audio measurements and silence detection.
- Structured diagnosis payloads.
- Provider-independent model calls.
- Structured fix proposals and validation.
- Before/after comparison.
- Session-level issue ranking.
- Local history and preference retrieval.

The existing CLI remains a supported diagnostic and automation surface.

#### Post Mortem Panel — paid product shell

The first paid interface will be a dockable ReaImGui Lua panel backed by a packaged local Python sidecar. The panel stays thin and communicates with the sidecar through atomic JSON job/result folders, matching the bridge's existing no-socket architecture.

Responsibilities:

- Onboarding and status.
- Track Check, Fix Preview, Mix Check, and History screens.
- Progress, errors, recovery, and A/B controls.
- License state and optional hosted-analysis credits.

ReaImGui is the committed beta surface, not a later upgrade. The thin-client protocol still stays UI-agnostic so a companion window can be added as a recovery or accessibility option without rewriting the engine.

#### Installer and updater

The public product must bundle Python and dependencies. A customer should not need Git, pip, pipx, a shell, or a preinstalled Python runtime.

The installer should:

1. Detect the REAPER resource directory.
2. Install or update Reaper Daemon and the Post Mortem panel.
3. Install the packaged Post Mortem sidecar.
4. Configure bridge authentication and capture permission.
5. Register the panel in REAPER's Actions list.
6. Perform a bridge and capture smoke test.
7. Offer to launch REAPER or explain that a restart is required.

## 5. Information architecture

Keep navigation to four destinations:

1. **Track** — selected-track diagnosis and Fix Preview.
2. **Mix** — session-level scan of a time selection or region.
3. **History** — prior checks, accepted fixes, and revision comparisons.
4. **Settings** — connection, model, privacy, license, updates, and diagnostics.

The default screen is Track because it provides the fastest first success. The interface targets intermediate users: the conclusion remains plain, while the evidence strip is always visible and deeper measurements expand on demand.

### Persistent header

- Post Mortem wordmark.
- Current project name.
- Bridge status: Ready, Busy, Needs REAPER, or Needs Attention.
- Model state: Starter Credits, Connected, or Setup Required.
- Compact settings button.

## 6. Core user flows

### 6.1 First-run onboarding

#### Step 1: Welcome

Promise:

> Post Mortem checks the track you are working on, explains the most likely problem, and lets you audition one safe fix.

Show three trust statements:

- It never changes the project during analysis.
- Preview changes are temporary.
- Raw audio stays on the computer.

Primary action: **Connect to REAPER**.

#### Step 2: Automatic setup

Show a short checklist rather than technical logs:

- Found REAPER.
- Installed the local bridge.
- Enabled safe track capture.
- Added the Post Mortem panel.

If the render dialog requires “Automatically close when finished,” show one illustrated instruction and a **Test Again** button. This should be the only manual setup exception.

#### Step 3: Analysis access

Offer these choices in order:

1. **Use included starter checks** — recommended for a first run.
2. **Connect an API key** — for unlimited pay-as-you-go use.
3. **Use through an MCP client** — advanced option.

Do not make users choose a model name during onboarding. Use a tested default and move model selection into Advanced Settings.

#### Step 4: First success

Ask the user to select a track with audio under the edit cursor, then run a 10-second Track Check. Explain the result in the interface before exposing detailed measurements.

Onboarding is complete only when a real project track has produced a diagnosis.

### 6.2 Track Check

#### Idle state

Display:

- Current selected track and its FX count.
- Capture source: edit cursor, active time selection, or selected region.
- Duration default: 10 seconds. Advanced range: 5–60 seconds.
- Primary action: **Check This Track**.

If there is no selected track, say “Select a track in REAPER” and update automatically when selection changes.

#### Progress state

Use human steps:

1. Reading the track.
2. Capturing a short section.
3. Measuring the audio.
4. Checking the likely cause.

Allow cancellation before the model request begins. Never leave the UI looking frozen during a render.

#### Result state

Show, in this order:

1. **Main finding** — two plain-language sentences.
2. **Suggested fix** — Track, Plug-in, Parameter, Current → Proposed.
3. **Why this may help** — one sentence.
4. **Confidence** — Low, Medium, or High with a short reason.
5. **Evidence** — collapsed by default; LUFS, peak, crest factor, spectrum, stereo, routing, and FX evidence.

Actions:

- **Preview Fix** — primary.
- **I’ll Fix It Myself** — closes the action area but preserves the report.
- **Not Helpful** — captures feedback.

### 6.3 Verified Fix Preview

This is the product's hero workflow.

#### Supported first-release changes

- Track volume.
- Track pan.
- FX bypass state.
- One numeric FX parameter identified by index and verified name.

Do not automatically add or remove plug-ins, rewrite routing, write automation, or make destructive item changes in the first release.

#### Preview sequence

1. Confirm the track GUID, FX identity, parameter name, and current value still match the diagnosis.
2. Save a baseline state snapshot.
3. Capture the baseline section if it is not already cached.
4. Apply the proposed value temporarily.
5. Capture the candidate section.
6. Restore the baseline automatically.
7. Compute before/after deltas and guardrails.
8. Present live A/B controls.

#### Preview screen

Display:

- **Original** and **Suggested** A/B buttons.
- A loudness-match toggle for subjective comparison.
- The exact change.
- Goal metric: what the change intended to improve.
- Guardrails: clipping, excessive loudness change, phase, stereo balance, and silence.
- Outcome language:
  - “The candidate moved in the intended direction.”
  - “The measurement changed, but not enough to prove improvement.”
  - “This introduced a new risk; keeping the original is safer.”

Final actions:

- **Apply Fix** — creates one named REAPER undo point.
- **Adjust** — offers a conservative slider around the proposed value.
- **Keep Original** — guarantees the original state.

Metrics must never call the candidate “better” without reference to the stated goal. The user makes the final sonic decision.

### 6.4 Mix Check / Session Autopsy

#### Setup

Ask the user to choose:

- Active time selection — recommended.
- Current region.
- 10 seconds from the edit cursor.

List included audible tracks and allow exclusions. Hide muted and empty tracks by default. Warn before scanning more than 32 tracks because capture time will grow.

#### Analysis stages

1. Build the session graph: folders, parents, sends, receives, FX, volume, pan, and phase.
2. Capture relevant tracks and buses.
3. Screen candidate conflicts using level, role, spectral prominence, and routing.
4. Run finer analysis only on likely problem pairs.
5. Rank issues by likely audible impact, evidence strength, and fixability.

#### Result

Return no more than three findings:

- **Most important**
- **Worth checking**
- **Minor / optional**

Each finding includes affected tracks, the evidence, confidence, and one action. Findings that do not support a concrete safe action remain informational.

Selecting a finding opens Track Check or Fix Preview with the relevant track already targeted.

### 6.5 Sonic Memory and revision comparison

Do not claim to “train a personal AI” in the first version. Start with transparent retrieval and preference weighting.

Store locally:

- Project and track identifiers.
- Measurement snapshots.
- Structured proposals.
- Applied, adjusted, rejected, and manually handled outcomes.
- Optional user labels such as genre, track role, and client/project type.

Useful first features:

- Compare the current check with the last check of the same track.
- Show what changed since the prior mix snapshot.
- Prefer ranges the user previously accepted for similar track roles.
- Suppress repeated suggestions the user consistently rejects.
- Export or delete all history.

## 7. Visual design direction

Use the existing Dead Pixel language as the foundation:

- Background: near-black `#0a0a0a`.
- Elevated surfaces: `#141414`.
- Primary text: bone `#e8e6e1`.
- Secondary text: muted bone `#807e78`.
- Brand/action accent: blood red `#a83232` / `#cd4040`.

Adjustments for an application rather than a marketing page:

- Use Cinzel sparingly for the product name and major empty-state headlines.
- Use a readable system sans-serif for explanations and buttons.
- Use JetBrains Mono for measurements, parameter values, IDs, and technical evidence.
- Reserve red for primary actions, warnings, and changed values. Do not flood the interface with horror styling.
- Add a muted amber for uncertainty and a restrained teal/green for a passed safety check; both must meet WCAG AA contrast on the dark background.
- Keep scanlines and the moving dead pixel off inside the working panel. They are branding flourishes, not workflow UI.

### Core components

- Status pill.
- Selected-track card.
- Progress checklist.
- Finding card.
- Suggested-change row with Current → Proposed values.
- Confidence badge.
- Measured / Inferred evidence labels.
- Before/after metric strip.
- A/B segmented control.
- Spectrum and delta chart.
- Inline recovery notice.

### Tone

Use direct, musician-friendly language:

- Good: “The vocal is likely fighting the guitars around 2–4 kHz.”
- Bad: “A spectral conflict anomaly has been detected.”
- Good: “Try lowering Pro-Q 4 Band 3 by 1.5 dB.”
- Bad: “Optimize the equalization topology.”

The mortuary theme can name secondary surfaces—Autopsy, Case File, Evidence—but primary buttons should remain obvious: Check Track, Preview Fix, Apply Fix.

## 8. Structured diagnosis and fix contract

Replace prose-only model output with a validated schema while preserving a rendered explanation.

Suggested response shape:

```json
{
  "finding": {
    "summary": "string",
    "probable_cause": "string",
    "confidence": "low|medium|high",
    "confidence_reason": "string",
    "evidence_refs": ["audio.true_peak_db", "fx.2.param.17"]
  },
  "proposal": {
    "operation": "set_track_volume|set_track_pan|set_fx_param|set_fx_bypass|none",
    "track_guid": "string",
    "fx_guid": "string|null",
    "fx_name": "string|null",
    "parameter_index": 17,
    "parameter_name": "Threshold",
    "current_normalized": 0.63,
    "proposed_normalized": 0.58,
    "current_display": "-18.0 dB",
    "proposed_display": "-21.0 dB",
    "goal": "reduce_overcompression",
    "expected_direction": {
      "crest_factor_db": "increase",
      "true_peak_db": "not_increase"
    }
  }
}
```

Validation rules:

- Evidence references must exist in the payload.
- A proposal must target an existing track and FX identity.
- Parameter index and name must agree with a fresh scan.
- Proposed normalized values must be within 0–1.
- Maximum default move sizes should be conservative.
- Unsupported or ambiguous proposals degrade to explanation-only.
- The model cannot authorize an operation; code-level policy decides what is previewable.

## 9. Technical implementation

### Reaper Daemon work

Add or extend commands:

- `get_selected_track`
- `snapshot_track_state`
- `restore_track_state`
- `preview_change`
- `commit_preview`
- `cancel_preview`
- `capture_track_audio` with explicit source mode:
  - `track_post_fx_pre_parent`
  - `folder_or_bus_output`
  - `via_master`
- `get_capture_preflight` to detect risk gating and known render-dialog setup.

Preview state should be identified by a random token, tied to track/FX GUIDs, expire automatically, and restore on bridge restart when possible. Only one active preview should exist per project.

Add Lua tests for command validation and Python fake-bridge tests for every state transition.

### Post Mortem engine work

Recommended modules:

```text
postmortem/
  schemas.py          # validated diagnosis/proposal/result models
  providers/          # Anthropic, OpenAI, compatible endpoint adapters
  rules.py            # deterministic warnings and proposal limits
  proposals.py        # validate and normalize model proposals
  verification.py     # before/after capture and guardrail evaluation
  session.py          # graph building, capture planning, issue ranking
  history.py          # SQLite persistence and retrieval
  service.py          # job-folder sidecar process
  licensing.py        # paid shell entitlement only
```

Keep `analysis.py`, `bridge.py`, and the CLI usable without the UI.

### Local state

Use a platform-appropriate application data directory rather than the repository folder:

```text
PostMortem/
  config.json
  history.sqlite3
  jobs/inbox/
  jobs/processing/
  jobs/outbox/
  captures/
  logs/
```

SQLite tables:

- `projects`
- `tracks`
- `analysis_runs`
- `measurements`
- `findings`
- `proposals`
- `preview_runs`
- `feedback`
- `reference_profiles`

Raw WAV captures should be deleted after comparison unless the user explicitly chooses to keep them. History stores measurements and metadata, not audio.

### Provider strategy

Support provider adapters rather than hard-coding one API shape. The first paid beta can keep Anthropic-compatible support and add one lower-cost structured-output provider. Pin a tested model snapshot for hosted analysis and evaluate it against a fixed mix-diagnosis corpus before changing defaults.

Raw audio is not sent to the model. The hosted request contains structured measurements, relevant FX values, routing context, and the honesty contract.

### Hosted analysis service

This is optional for BYO-key users but necessary for a zero-friction bedroom-musician experience.

Responsibilities:

- License/account authentication.
- Credit balance.
- Provider proxy and model pinning.
- Rate limiting and abuse protection.
- No permanent prompt or payload retention by default.
- Usage and cost accounting per request.

Do not build subscription machinery before measuring real per-check costs. Current efficient-model pricing makes low-cost credits plausible, but payload sizes and retry rates must be benchmarked with real sessions.

## 10. Safety and trust requirements

### State safety

- Every applied fix is one undo point.
- Every preview records and restores the exact prior value.
- GUID identity is checked immediately before preview and apply.
- Stale results cannot apply after a track, FX chain, or parameter changes.
- A crash-recovery file records any active preview.
- A panel close, service exit, or REAPER shutdown attempts to restore the baseline.

### Audio safety

- Clamp gain and parameter move sizes to conservative defaults.
- Refuse candidate changes that create clipping or severe phase risk unless the user explicitly overrides.
- Loudness-match only the A/B monitoring path; never disguise the actual applied gain change.
- Silence, missing capture data, and excessive dead air prevent diagnosis.

### Model safety

- Model output must validate against a strict schema.
- Model-provided names never become shell commands or file paths.
- The provider never receives API keys for a different endpoint.
- Unsupported actions render as advice only.
- Prompt injection inside track, project, marker, or FX names is treated as untrusted data.

### Privacy

The Settings screen should explicitly show:

- Raw audio stays local.
- Which structured fields are sent for analysis.
- Which provider is being used.
- Whether history is local or synced.
- Delete History and Export History controls.

## 11. Pricing and licensing

### Recommended simple offer

#### Reaper Daemon + Post Mortem CLI/MCP — free

- Open local bridge.
- Measurements and payload generation.
- CLI and MCP workflows.
- Community and developer adoption.

#### Post Mortem — $39 launch / $49 standard, one-time

- One-click installer and updater.
- Dockable panel.
- Track Check.
- Verified Fix Preview.
- Mix Check.
- Local Sonic Memory and revision comparison.
- Bring-your-own API key for unlimited provider-billed use.
- Permanent use of the purchased major version and at least 12 months of updates.

Avoid splitting the core experience into Basic and Pro tiers at launch. One affordable paid version is easier to understand and support. The `$39` launch price is now the committed target.

**Early-access decision, 2026-07-15:** the complete-product offer above remains
the longer-term target. The owner separately approved the live `$39` Apple
silicon early-access offer for Track Check and Fix Preview, permanent version
1 use, and 12 months of version 1 updates. Mix Check, local Sonic Memory,
history, hosted checks, automatic updates, and paid Windows/Linux installers
are explicitly excluded from `0.1.x`.

#### Optional hosted analysis credits

- Include 10 starter checks with the paid product.
- Sell small non-expiring credit packs rather than requiring a subscription.
- Initial test: **$5 for 100 credits**.
- Working weights:
  - Track Check: 1 credit.
  - Fix verification: 1 additional credit only when another model pass is needed; deterministic verification should normally cost zero.
  - Mix Check: 5 credits.

These are pricing experiments, not promises. Final weights require a measured cost report across at least 100 real calls, including failures and retries.

### Licensing direction

Reaper Daemon stays MIT. Post Mortem will use an open-core model:

- Free/open: capture, measurements, CLI/MCP, schemas, and provider adapters.
- Paid/proprietary: panel, installer/updater, Fix Preview orchestration, Mix Check ranking, history UI, hosted credits, and support.

Existing MIT releases remain MIT regardless of future licensing. The exact license text and repository boundary must be reviewed before proprietary panel code is published, but the product boundary is locked: the engine remains useful without payment, while customers pay for the finished workflow and convenience.

## 12. Implementation roadmap

### Phase 0 — Decision and benchmark gate

Deliverables:

- Resolve the product questions at the end of this document.
- Collect 15–25 anonymized test scenarios across drums, bass, guitars, vocals, synths, buses, and full mixes.
- Save expected findings, acceptable moves, and unacceptable claims.
- Benchmark DeepSeek V4 Flash and MiniMax M3 against DeepSeek V4 Pro; do not use
  an Anthropic model as the Post Mortem quality baseline.
- Define the proposal schema and conservative move limits.

Exit criteria:

- At least 80% of test scenarios produce a useful primary finding.
- Zero unsupported masking claims in single-track mode.
- Zero invalid or ambiguous proposals pass validation.

### Phase 1 — Structured Track Check

Deliverables:

- Structured response schema.
- Deterministic proposal validator.
- Provider adapter interface.
- Faster 10-second default capture.
- New CLI JSON output mode.
- Golden tests for diagnosis parsing and validation.

Exit criteria:

- Existing CLI behavior remains supported.
- Every proposal references a real track/FX/parameter identity.
- Explanation-only fallback works cleanly.

### Phase 2 — Verified Fix Preview

**Completed 2026-07-12** at CLI scope (the terminal proof loop from §17; the
docked panel and A/B transport controls remain Phase 3). All four exit
criteria live-verified on a real project; evidence in
`docs/PHASE_2_IMPLEMENTATION.md` P2-005. "Adjust" ships with the panel — the
CLI covers Apply (`postmortem commit`) and Keep Original (the preview's
automatic restore).

Deliverables:

- Daemon preview lifecycle commands.
- Baseline/candidate capture flow.
- Automatic restoration and crash recovery.
- Metric goal and guardrail evaluator.
- Apply, Adjust, and Keep Original operations.

Exit criteria:

- Killing the sidecar or closing the panel during preview restores the baseline in recovery testing.
- Apply creates exactly one undo point.
- Stale diagnoses refuse to apply after an FX-chain change.
- No preview operation supports destructive actions.

### Phase 3 — Product shell and installer

Deliverables:

- Track screen and onboarding UI.
- Packaged sidecar and docked ReaImGui panel for Windows, macOS, and Linux.
- Installer, updater, and uninstaller.
- Bridge/capture/model status and guided recovery.
- License validation with an offline grace period.

Exit criteria:

- [ ] A fresh user can install, restart REAPER, and finish their first Track
  Check without opening a terminal on every paid-release platform. macOS and
  Linux evidence is recorded in
  [P3-008](PHASE_3_IMPLEMENTATION.md#p3-008--installer-updater-uninstaller).
  The hosted Windows journey remains explicitly open in
  [P3-010](PHASE_3_IMPLEMENTATION.md#p3-010--live-verification-protocol).
- [x] Uninstall removes managed files without touching user projects or
  unrelated startup scripts. Transactional ownership and preservation evidence
  is recorded in
  [P3-008](PHASE_3_IMPLEMENTATION.md#p3-008--installer-updater-uninstaller).
- [x] The panel explains every recoverable setup failure in plain language and
  preserves typed diagnostics for unknown failures. Known setup failures have
  engine-owned recovery text from
  [P3-006](PHASE_3_IMPLEMENTATION.md#p3-006--onboarding-and-guided-recovery).
  The installed Apple silicon release passed sidecar crash, REAPER crash, and
  render-setting restoration drills on 2026-07-15.

Phase 3 remains open until the unchecked cross-platform criterion passes. The
live `0.1.1` Apple silicon release is not evidence that the paid Windows and
Linux paths are ready for sale.

### Phase 4 — Mix Check

Deliverables:

- Session graph.
- Bus- and master-aware capture modes.
- Candidate-pair screening.
- Top-three issue ranking.
- Mix screen linked into Track and Fix Preview.

Exit criteria:

- A 16-track, 10-second scan completes within an acceptable user-tested time budget.
- Results never exceed three primary findings.
- Every finding cites measured evidence.
- Parent-bus coloration is represented correctly.

### Phase 5 — Sonic Memory and paid beta

Deliverables:

- Local SQLite history.
- Prior-run comparison.
- Applied/rejected feedback.
- Export/delete controls.
- Purchase, license, starter credits, and hosted-analysis pilot.

Exit criteria:

- History can be fully deleted.
- Preference retrieval never silently overrides safety or evidence.
- Ten external beta users complete onboarding and at least three real checks each.

### Phase 6 — Public release hardening

Deliverables:

- Signed installers where practical.
- Automated release pipeline and checksums.
- Crash reporting that is opt-in and excludes project/audio content.
- Support diagnostics bundle with redaction.
- Documentation, short demo project, and launch site.

Exit criteria:

- No known data-loss or preview-restoration defect.
- Cross-platform smoke tests pass on supported REAPER versions.
- Pricing page and product copy accurately distinguish local analysis, hosted analysis, and BYO-key use.

## 13. Testing strategy

### Unit tests

- WAV parsing and measurements.
- Structured schema validation.
- Proposal bounds and identity checks.
- Before/after goal evaluation.
- Session graph and candidate-pair selection.
- History migrations and deletion.
- Provider billing/usage accounting.

### Integration tests

- Fake bridge command lifecycle.
- Preview, crash, restore, and commit sequences.
- Stale track/FX/parameter identities.
- Model refusal, timeout, malformed JSON, and partial responses.
- Interrupted render and lingering dialog recovery.

### REAPER matrix

- Windows 10/11, current REAPER 7.
- macOS current and previous major release.
- One mainstream Linux distribution supported after installer validation.
- Stock plug-ins plus representative VST3/AU plug-ins with deep parameter lists.
- Duplicate track and FX names.
- Folder tracks, nested buses, receives, automation modes, mono and stereo sources.

### Product testing

Measure:

- Install-to-first-diagnosis completion.
- Time to first useful result.
- Preview-to-apply rate.
- Rejection and “not helpful” reasons.
- Percentage of findings users understand without opening Evidence.
- Undo/restoration confidence.
- Average hosted cost per Track Check and Mix Check.

The primary beta success metric is not model praise. It is the percentage of checks that lead to an informed Apply, Adjust, or “keep original” decision.

## 14. Launch scope and what not to build yet

Do not include these in the first paid release:

- Voice control.
- Automatic full-song mixing.
- Automatic plug-in insertion or purchasing recommendations.
- Destructive item editing.
- Automation writing.
- A community preset marketplace.
- Cloud audio uploads.
- Mobile remote control.
- Cross-DAW support.
- “Train on my entire catalog” claims.

They dilute the product's strongest promise and dramatically expand support risk.

## 15. Product risks and mitigations

### Setup remains too technical

Mitigation: treat installation and smoke testing as a product feature. No paid public launch until a clean machine can reach first diagnosis without a terminal.

### Recommendations sound plausible but do not help

Mitigation: fixed evaluation corpus, structured evidence references, conservative proposals, user A/B, and explicit “not enough evidence” outcomes.

### Capture feels slow

Mitigation: default to 10 seconds, cache baseline captures, reuse time selections, parallelize only work outside REAPER's blocking render, and show honest progress.

### Users fear project changes

Mitigation: read-only analysis, temporary preview, visible restoration, one named undo point, and crash recovery.

### Hosted inference erodes low pricing

Mitigation: economical model routing, compact payloads, cached system prompts, deterministic verification, BYO-key support, and credit packs priced from measured usage.

### MIT code weakens the commercial moat

Mitigation: monetize convenience, polish, workflow, updates, evaluation data, hosted access, and support; decide the future Post Mortem license before major new orchestration work lands.

## 16. Locked product decisions

The following decisions were confirmed on 2026-07-10:

1. **Platform:** the paid `0.1.1` early-access release is Apple silicon only. Windows and Linux remain withheld until their customer gates close. The public engine and Reaper Daemon remain cross-platform.
2. **Business model:** Open-core. Reaper Daemon and a genuinely useful Post Mortem engine remain open; the docked product shell, advanced workflow orchestration, installer/updater, hosted credits, and support form the paid product.
3. **Primary interface:** Docked inside REAPER using ReaImGui for the first beta.
4. **Customer level:** Intermediate. Lead with a clear finding, keep evidence visible, and preserve user control rather than over-explaining fundamentals.
5. **Price:** `$39` launch / `$49` standard, one-time, with no mandatory subscription.
6. **Hosted analysis:** Optional accounts and hosted credit packs are acceptable. BYO-key and local engine paths remain available.

## 17. Immediate next implementation slice

The first code milestone should be a CLI-only proof of the hero promise:

1. Add the structured diagnosis/proposal schema.
2. Validate the proposal against a fresh FX scan.
3. Apply one supported parameter change temporarily.
4. Capture before and after.
5. Restore the original value.
6. Print a before/after proof report.
7. Offer an explicit commit command that creates one undo point.

Do this before building the panel. If the proof loop is not genuinely useful in the terminal, a polished UI will not rescue it. The task-level backlog is maintained in `docs/PHASE_1_IMPLEMENTATION.md`.
