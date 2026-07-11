# Phase 1 Implementation Backlog: Structured Track Check

**Status:** Ready for implementation
**Date:** 2026-07-10
**Target:** Cross-platform, open-core engine milestone
**Depends on:** Reaper Daemon `cbff8d5` or later; Post Mortem `3184131` or later

## 1. Outcome

Phase 1 converts the current prose-only diagnosis into a validated, provider-independent result that can safely power the later docked panel and Verified Fix Preview.

At the end of this phase, this command:

```text
postmortem "Kick" --format json
```

must return:

- A plain-language finding.
- Confidence and its reason.
- Evidence references that point to real payload fields.
- Either one machine-readable supported proposal or an explicit `none` proposal.
- Stable track, FX, and parameter identities where an actionable proposal exists.

The current human-readable CLI remains available and renders from the same structured result.

## 2. Scope boundary

### Included

- Single-track Track Check.
- Structured diagnosis schema.
- Stable bridge identities required for future safe application.
- Provider adapter boundary.
- Proposal validation and conservative limits.
- JSON and text CLI formats.
- Cross-platform CI.
- Golden evaluation fixtures.

### Not included

- Applying or previewing a proposal.
- Mix Check or cross-track schema conversion.
- ReaImGui panel.
- Licensing, accounts, or hosted credits.
- Local history.
- New audio measurements beyond what is needed to validate the result contract.

Cross-track diagnosis continues to work through the existing prose path until its Phase 4 conversion.

## 3. Repository ownership

| Workstream | Repository | Primary files |
|---|---|---|
| Stable track and FX identities | `reaper-daemon` | `bridge/reaper_agent_bridge.lua`, `bridge/command_schema.md`, `tests/` |
| Structured schemas | `post-mortem` | `postmortem/schemas.py`, `tests/test_schemas.py` |
| Provider boundary | `post-mortem` | `postmortem/providers/`, `postmortem/diagnose.py` |
| Proposal validation | `post-mortem` | `postmortem/proposals.py`, `tests/test_proposals.py` |
| CLI formats | `post-mortem` | `postmortem/cli.py`, `tests/test_cli.py` |
| Evaluation fixtures | `post-mortem` | `tests/fixtures/diagnoses/`, `tests/test_diagnosis_contract.py` |
| Cross-platform CI | both | `.github/workflows/test.yml` |

## 4. Delivery sequence

Implement in this order. Later tasks depend on the contracts established earlier.

### P1-001 — Add stable bridge identities

**Repository:** `reaper-daemon`
**Priority:** Blocking
**Status:** Completed and verified 2026-07-10

Add these fields without removing or renaming existing output:

- `scan_fx.tracks[].guid`
- `scan_fx.tracks[].fx[].guid`
- `get_fx_parameters.track.guid`
- `get_fx_parameters.fx.guid`
- `get_fx_parameters.fx.index`
- `get_fx_parameters.fx.scope`

Use REAPER's actual track and FX GUID APIs. Never manufacture identities from names or indices.

Tasks:

1. Add GUIDs in the Lua response builders.
2. Update the command schema and README response examples.
3. Extend Lua tests for presence and shape.
4. Extend Python fake-bridge fixtures so MCP and CLI tests receive the new fields.
5. Confirm older clients ignore the additive fields.

Acceptance criteria:

- Duplicate track names still have distinct track GUIDs.
- Duplicate FX names on one track still have distinct FX GUIDs.
- Existing 109 Python tests remain green.
- Additive response changes do not alter current command semantics.

### P1-002 — Define the structured result schema

**Repository:** `post-mortem`
**Priority:** Blocking
**Depends on:** P1-001 field contract
**Status:** Completed and verified 2026-07-10

Create `postmortem/schemas.py` using Pydantic v2 and add Pydantic as a direct project dependency rather than relying on it transitively through an SDK.

Required types:

- `Confidence`: `low | medium | high`
- `ProposalOperation`: `none | set_track_volume | set_track_pan | set_fx_param | set_fx_bypass`
- `EvidenceRef`
- `Finding`
- `ExpectedMetricDirection`
- `ProposalTarget`
- `Proposal`
- `DiagnosisResult`

Minimum `DiagnosisResult` fields:

```text
schema_version
finding.summary
finding.probable_cause
finding.confidence
finding.confidence_reason
finding.evidence_refs[]
proposal.operation
proposal.reason
proposal.target
proposal.current_value
proposal.proposed_value
proposal.goal
proposal.expected_direction
```

Rules enforced by the schema:

- `schema_version` begins at `1`.
- `none` proposals cannot carry a target or proposed value.
- FX operations require track GUID, FX GUID, FX index, FX scope, and verified FX name.
- Parameter operations also require parameter index and verified parameter name.
- Normalized values are finite and within `0.0–1.0`.
- Track volume and pan proposals use explicitly named units rather than overloaded floats.
- User-facing strings have sensible maximum lengths.

Acceptance criteria:

- Valid examples serialize deterministically.
- Missing conditional fields fail with readable validation errors.
- NaN, infinity, out-of-range values, and unknown operations fail closed.
- The schema can represent useful advice with `operation: none`.

### P1-003 — Add a provider-independent diagnosis interface

**Repository:** `post-mortem`
**Priority:** Blocking
**Depends on:** P1-002
**Status:** Completed and verified 2026-07-10

Create:

```text
postmortem/providers/
  __init__.py
  base.py
  anthropic_provider.py
```

The provider contract should accept:

- System contract.
- Structured payload.
- Target response schema.
- Model profile.

It should return either a validated JSON-compatible object or a typed provider error. Model SDK response objects must not leak into the rest of the engine.

Move endpoint/key/model resolution out of `diagnose.py` into the Anthropic adapter while preserving the current same-source API-key protections.

Error categories:

- Authentication/configuration.
- Rate limit or exhausted credit.
- Timeout/network.
- Provider refusal.
- Empty or truncated response.
- Invalid structured response.

Acceptance criteria:

- Existing Anthropic and Anthropic-compatible configurations continue to work.
- A test provider can be injected without importing an SDK.
- Provider errors become clean CLI messages and stable exit codes.
- No API key can be forwarded to a differently configured endpoint.

### P1-004 — Convert the single-track prompt to a structured contract

**Repository:** `post-mortem`
**Priority:** Blocking
**Depends on:** P1-002, P1-003
**Status:** Completed and verified 2026-07-11

Preserve every current honesty rule:

- No single-track masking claims.
- Null means not measured.
- True peak is not inferred from sample peak.
- Silence reduces confidence.
- One move, not five.
- Uncertainty is acceptable.

Add rules for structured proposals:

- Evidence references must use payload paths supplied to the model.
- The model may propose only supported operations.
- It should return `operation: none` when evidence does not support a safe move.
- It must identify current and proposed values without inventing unavailable plug-in display mappings.
- It cannot claim the change has improved the audio before verification exists.

For providers without native structured-output enforcement:

1. Request JSON only.
2. Extract one top-level JSON object without executing or evaluating text.
3. Validate once.
4. Allow at most one compact schema-repair request.
5. Fall back to a typed unavailable result rather than accepting malformed content.

Acceptance criteria:

- Reasoning or preamble text cannot bypass JSON validation.
- A truncated response is never printed as a complete diagnosis.
- A refusal becomes a non-actionable result.
- Current prompt honesty tests are retained or strengthened.

### P1-005 — Implement deterministic proposal validation

**Repository:** `post-mortem`
**Priority:** Blocking
**Depends on:** P1-001, P1-002, P1-004
**Status:** Completed and verified 2026-07-11

Create `postmortem/proposals.py`.

Validation happens after model-schema validation and uses the actual analysis payload. It must not call REAPER again in Phase 1; the fresh-scan application check belongs to Phase 2.

Checks:

1. Evidence paths exist and resolve to non-null payload values.
2. Track GUID matches the analyzed track.
3. FX GUID, index, scope, and name all describe the same FX.
4. Parameter index and name describe the same parameter entry.
5. Current value matches the payload within a defined tolerance.
6. Proposed value fits conservative move limits.
7. Goal and expected directions use supported metric names.

Initial conservative limits:

- Track volume: maximum absolute move `3 dB`.
- Track pan: maximum absolute move `0.20` normalized pan distance.
- FX normalized parameter: maximum absolute move `0.20`, with a stricter default of `0.10` when the display mapping is unknown.
- FX bypass: allowed only when the finding directly cites that FX and the proposal explicitly says this is a preview, not a deletion.

When validation fails, preserve the finding but replace the proposal with `operation: none` and attach a machine-readable rejection reason. Do not discard an otherwise useful explanation.

Acceptance criteria:

- Hallucinated evidence and stale identities fail closed.
- Proposal rejection never crashes the diagnosis.
- Every rejection reason is covered by a unit test.
- The text renderer clearly distinguishes “advice only” from a previewable proposal.

### P1-006 — Render text and JSON from the same result

**Repository:** `post-mortem`
**Priority:** High
**Depends on:** P1-002 through P1-005
**Status:** Completed and verified 2026-07-11

Add:

```text
--format text|json
```

Behavior:

- `text` remains the default.
- `json` prints only the validated `DiagnosisResult`, with no progress messages on stdout.
- Progress and warnings remain on stderr.
- `--payload-only` remains available and unchanged.
- `--format json` and `--payload-only` are mutually exclusive with a clear argparse error.

The text renderer uses the existing four-part shape:

1. Diagnosis.
2. Probable Cause.
3. Suggested Move.
4. Confidence.

It must render from structured fields rather than retaining a second prose-only model path.

Acceptance criteria:

- JSON output round-trips through `DiagnosisResult`.
- Text output contains no raw JSON or internal rejection codes.
- Shell consumers can parse stdout without stripping progress lines.
- Existing CLI error exit codes remain stable unless documented.

### P1-007 — Reduce and centralize the default capture duration

**Repository:** `post-mortem`
**Priority:** Medium
**Depends on:** None
**Status:** Completed and verified 2026-07-11

Add `DEFAULT_CAPTURE_SECONDS = 10` in one shared location and use it for CLI, service, documentation, and tests.

Keep the valid range `1–600` seconds. Do not change an explicit `--seconds` value.

Acceptance criteria:

- Default single-track capture is 10 seconds.
- Explicit durations still work.
- README and `--help` agree.
- Silence gating behavior remains unchanged.

### P1-008 — Build the golden diagnosis corpus

**Repository:** `post-mortem`
**Priority:** High
**Depends on:** P1-002, P1-004, P1-005
**Status:** Completed and verified 2026-07-11

Create at least 20 de-identified payload fixtures covering:

- Kick, snare, drum bus.
- Bass guitar and synth bass.
- Clean and distorted guitars.
- Lead and backing vocals.
- Synths and pads.
- Mono and stereo sources.
- Silent and mostly silent captures.
- Clipping and near-clipping.
- Low crest factor / overcompression candidates.
- Phase and stereo imbalance cases.
- Parent bus, sends, receives, and suspicious gain staging.
- No-problem / insufficient-evidence cases.

Each fixture contains assertions, not one brittle expected paragraph:

- Claims that must appear or be represented.
- Claims that are forbidden.
- Allowed proposal operations.
- Required evidence categories.
- Maximum confidence.

Model calls are not part of the normal unit test suite. Store captured model results separately and run evaluation intentionally against pinned model snapshots.

Acceptance criteria:

- At least four fixtures require `operation: none`.
- Single-track masking language is forbidden across the corpus.
- Every supported proposal operation has a positive and negative case.
- Corpus fixtures contain no private paths, API keys, client names, or raw audio.

### P1-009 — Add cross-platform CI from the beginning

**Repository:** both
**Priority:** High
**Depends on:** None
**Status:** Completed and verified 2026-07-11

Add GitHub Actions matrices:

#### Post Mortem

- OS: Windows, macOS, Ubuntu.
- Python: `3.10`, `3.12`, latest supported stable.
- Install from `pyproject.toml` plus test dependencies.
- Run `pytest -q`.
- Run a package-build smoke test.

#### Reaper Daemon

- OS: Windows, macOS, Ubuntu.
- Python: `3.10`, latest supported stable.
- Run root Python tests and `skills/drum-apparatus` tests.
- Run syntax/compile checks on standalone Python entry points.

Lua bridge behavior still needs REAPER integration testing later; CI covers parsers, response shaping helpers where extractable, and all Python surfaces.

Acceptance criteria:

- No operating system is allowed to be red at merge.
- CI uses locked major versions for third-party actions.
- Packaging failures block Phase 1 completion.

### P1-010 — Update product and developer documentation

**Repository:** both
**Priority:** Medium
**Depends on:** All prior tasks
**Status:** Completed and verified 2026-07-11

Update:

- Post Mortem README examples and options.
- Reaper Daemon command schema identity fields.
- Structured result schema documentation.
- Provider adapter extension guide.
- Open-core boundary statement.
- Migration note for consumers that parse text output.

Acceptance criteria:

- A new developer can run tests and produce a fixture-backed JSON diagnosis.
- Public docs do not imply that Phase 2 preview/application already exists.
- JSON schema versioning and compatibility expectations are explicit.

## 5. Parallel work plan

After the schema shape is agreed, work can split safely:

| Lane | Tasks | Notes |
|---|---|---|
| Bridge identity | P1-001 | Independent repository work; unblocks validator integration. |
| Schema/provider | P1-002, P1-003, P1-004 | Keep one owner until the provider contract stabilizes. |
| Validator/CLI | P1-005, P1-006, P1-007 | Starts with schema fixtures before provider integration completes. |
| Evaluation/CI/docs | P1-008, P1-009, P1-010 | Corpus design can start immediately; final docs land last. |

Do not parallelize edits to `diagnose.py` and `cli.py` until the schema and provider contracts are merged; those are the highest-conflict files.

## 6. Definition of done

Phase 1 is complete only when:

1. All existing tests pass in both repositories.
2. New schema, validator, provider, and CLI tests pass across Windows, macOS, and Linux CI.
3. Every actionable result carries real track/FX/parameter identity.
4. Malformed, ambiguous, unsupported, or over-large proposals fail closed.
5. Human text and machine JSON render from one validated result.
6. The golden corpus contains at least 20 scenarios and explicit forbidden claims.
7. A pinned economical model and the current quality baseline have been evaluated against the same corpus.
8. The JSON contract is documented as version `1`.
9. No preview or mutation path has accidentally entered Phase 1.
10. The next phase can consume `DiagnosisResult.proposal` without parsing prose.

### Model evaluation result — 2026-07-11

DeepSeek V4 Flash, DeepSeek V4 Pro, and MiniMax M3 were evaluated against the
same 25-case corpus. The gate was executed, but no model met the 80% usefulness
threshold for primary findings: MiniMax M3 reached 12/25, DeepSeek V4 Flash
9/25, and DeepSeek V4 Pro 8/25. Their stricter full-contract pass counts were
3/25, 2/25, and 0/25. The benchmark also found and closed a prose path around
structured move limits. See `evaluations/results/2026-07-11-model-benchmark.md`;
Phase 1 model selection remains open.

The first model-contract hardening iteration then separated the model-facing
schema from public `DiagnosisResult`, reserved rejection state for deterministic
code, constrained metric names, strengthened evidence-path instructions, and
added runtime confidence/cross-track guards. A same-corpus rerun improved
MiniMax M3 to 19/25 useful findings and 15/25 full-contract passes, DeepSeek V4
Pro to 12/25 and 8/25, and DeepSeek V4 Flash to 11/25 and 8/25. MiniMax remains
one useful finding short of the selection gate, so the configured default is
unchanged. See `evaluations/results/2026-07-11-model-contract-v2-benchmark.md`.

## 7. Recommended pull request sequence

1. **Reaper Daemon:** additive track/FX GUID response fields and tests.
2. **Post Mortem:** schemas and direct dependency.
3. **Post Mortem:** provider abstraction preserving current behavior.
4. **Post Mortem:** structured prompt and validation.
5. **Post Mortem:** CLI format renderer and 10-second default.
6. **Post Mortem:** golden corpus and evaluation harness.
7. **Both:** cross-platform CI and documentation cleanup.

Keep these changes in small PRs. The schema PR should merge before broad UI or Phase 2 work begins because it becomes the central compatibility contract for the engine, panel, history database, and hosted service.
