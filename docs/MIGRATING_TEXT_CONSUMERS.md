# Migrate text parsers to JSON

The default `postmortem "Track"` output remains human-readable, but its wording
and line wrapping are presentation, not an API. Scripts that split the
`DIAGNOSIS`, `PROBABLE CAUSE`, `SUGGESTED MOVE`, or `CONFIDENCE` headings should
move to the versioned single-track JSON contract.

## Migration

1. Change the command to `postmortem "Track" --format json`.
2. Parse stdout as one JSON object. Keep stderr separate; progress and warnings
   intentionally go there.
3. Require `schema_version == 1` before reading fields.
4. Read explanation from `finding` and actionability from `proposal.operation`.
5. Treat `operation: "none"` as a successful, non-actionable diagnosis, not a
   parser failure. Inspect `proposal.rejection_reason` only for machine logging;
   show `proposal.reason` to people.
6. For actionable proposals, preserve every identity and unit exactly. Never
   resolve a target from its display name alone.

Example shell capture:

```bash
postmortem "Kick" --format json > diagnosis.json 2>postmortem.log
python -c "import json; d=json.load(open('diagnosis.json')); assert d['schema_version'] == 1; print(d['proposal']['operation'])"
```

`--format json` is single-track only and is mutually exclusive with
`--payload-only`. Cross-track masking remains on the prose path in Phase 1, so a
consumer must handle that mode separately rather than assuming it returns
`DiagnosisResult`.

See `docs/STRUCTURED_RESULTS.md` for the complete shape and compatibility
policy. JSON output is a validated recommendation contract; it does not mean a
proposal has been previewed or applied.
