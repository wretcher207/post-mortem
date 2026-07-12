# Structured Track Check result

`postmortem "Kick" --format json` writes one validated `DiagnosisResult` to
stdout. Progress and warnings go to stderr. This contract applies only to the
single-track Track Check; Phase 1 cross-track masking output remains text.

The executable contract is `postmortem/schemas.py`. Generate standards-based
JSON Schema from the installed package when another language or service needs
it:

```bash
python -c "import json; from postmortem.schemas import DiagnosisResult; print(json.dumps(DiagnosisResult.model_json_schema(), indent=2))" > diagnosis-result-v1.schema.json
```

## Version 1 shape

Every object rejects unknown fields. User-facing strings and collections have
bounded lengths, numeric values must be finite, and invalid conditional fields
fail validation.

```json
{
  "schema_version": 1,
  "finding": {
    "summary": "The measured peak leaves very little headroom.",
    "probable_cause": "The track output level is close to clipping.",
    "confidence": "high",
    "confidence_reason": "The verified isolated capture has a measured peak.",
    "evidence_refs": [
      {"path": "audio.sample_peak_db", "description": "Measured sample peak"}
    ]
  },
  "proposal": {
    "operation": "none",
    "reason": "Keep this as advice until a safe move is supported.",
    "target": null,
    "current_value": null,
    "proposed_value": null,
    "goal": null,
    "expected_direction": [],
    "rejection_reason": null
  }
}
```

`finding.evidence_refs[].path` is a path into the exact Track Check payload sent
to the provider. Model-facing schema descriptions require an exact leaf path,
such as `audio.sample_peak_db` or `fx_chain[0].enabled`, rather than a container.
Post Mortem verifies that referenced values exist and are not null before it
exposes an actionable proposal.

`proposal.operation` is one of:

- `none`
- `set_track_volume`
- `set_track_pan`
- `set_fx_param`
- `set_fx_bypass`

An actionable proposal requires a target, current and proposed values, a goal,
and at least one expected metric direction. FX operations also require the real
track GUID plus the FX GUID, zero-based index within its scope, scope, and
verified name. Parameter moves add the verified parameter index and name.
Names are descriptive checks; GUIDs are stable identity. The deterministic
validator can replace an unsafe action with `operation: "none"` while preserving
the finding and recording `rejection_reason` for machines.

The provider does not receive this public schema directly. It receives the
stricter `ProviderDiagnosisResult`, which omits validator-owned
`rejection_reason` and exposes supported goal/metric names as closed JSON Schema
enums. Post Mortem converts a valid provider result into `DiagnosisResult`, then
runs deterministic validation. This keeps the public version 1 shape stable
while preventing models from authoring validation state.

For an accepted actionable proposal, Post Mortem rewrites `proposal.reason`
from the validated structured values and expected metric direction. Provider
prose cannot add a second move or describe a value beyond the conservative
limits, while the rendered move still says why the change is being previewed.

Value units are explicit:

- track volume: `db`
- track pan: `normalized_pan`, from `-1.0` to `1.0`
- FX parameter: `normalized`, from `0.0` to `1.0`
- FX bypass: `boolean`

`expected_direction[].direction` is `increase`, `decrease`, `not_increase`,
`not_decrease`, or `unchanged`. Supported metric names live in
`postmortem.schemas.SupportedMetric`; `postmortem.proposals.SUPPORTED_METRICS`
is derived from that type so prompt, provider schema, and validator cannot drift.

Verified isolated-track capture provenance is required for any track diagnosis.
Missing or unverified isolation is refused before the provider call and returns
a safe low-confidence unavailable result with
`rejection_reason: "capture_not_isolated"`. A `silence_fraction` of `0.75` or
greater caps confidence at `low` and rejects an actionable proposal. A response
that introduces a cross-track claim in the single-track path is replaced with a
safe low-confidence finding and `rejection_reason: "cross_track_claim"`.

## VerificationResult (Phase 2)

`postmortem preview` evaluates a candidate capture against its baseline with
`postmortem.verification.evaluate`, returning a `VerificationResult`
(`schema_version: 1`, strict shape, finite-only floats):

- `available` — false when the candidate capture is ≥ 0.75 silence; nothing
  else is claimed in that case.
- `goal_metric` / `goal_outcome` — `moved_as_intended`,
  `moved_insufficiently` (below the per-metric noise floor: 0.5 dB, 0.5 LU,
  0.05 correlation, 1.0 dB per spectrum band), `moved_against`, or
  `not_measured` (missing or null in either capture; null is never zero).
- `deltas[]` — per-metric baseline/candidate/delta; the
  `spectrum_third_octave` entry reports the largest-magnitude band change
  across bands present in both captures, with its `band_hz`.
- `expected[]` — one outcome per proposal `expected_direction` entry.
- `guardrails[]` — `new_clipping` (fail above −0.3 dBFS when the baseline was
  below; pre-existing clipping is not "new"), `loudness_shift` (warn > 3 LU;
  blocks the intended-direction sentence because the A/B becomes unfair),
  `phase` (warn on a 0.25 correlation drop or a positive→negative flip),
  `stereo_balance` (warn > 3 dB), `silence` (fail ≥ 0.75).
- `outcome_sentence` — exactly one of the three locked sentences from the
  product plan, or the explicit unavailable sentence. No code path can claim
  improvement without the goal metric moving past its noise floor in the
  intended direction, and the word "better" never appears.

The sidecar's `preview_fix` result wraps this object in a preview report. For
numeric proposals that envelope also includes `adjustment` (`minimum`,
`maximum`, `step`, `value`, `unit`) derived from the same move-limit constants
used by deterministic validation. Optional `proposed_value` inputs to
`preview_fix` and `commit_fix` are clamped by the sidecar; clients do not
rewrite `DiagnosisResult` or duplicate the limits.

## Compatibility policy

Consumers must inspect `schema_version` before reading the rest of the object.
Version `1` is the only accepted version today.

- Version 1 is a closed shape: unknown fields are rejected. New fields, removed
  fields, changes in requiredness, and enum additions require a new integer
  schema version.
- Existing field meaning, units, and enum values will not change within
  version 1.
- A breaking shape or semantic change requires a new integer version and a
  documented migration. The current runtime will fail closed on an unsupported
  version instead of guessing.
- Preserve the original JSON if forwarding it. Do not reconstruct identities
  from display names or mutable indices.

The schema describes a proposal, not permission to mutate REAPER. Phase 1 has no
preview or application path; consumers must not treat an actionable operation
as already auditioned, approved, or applied.
