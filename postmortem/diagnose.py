"""Payload assembly + model call. The hedge contract lives here; don't weaken it."""

import math
from .providers.base import ModelProfile, TextDiagnosisResult

SYSTEM_PROMPT = """\
You are a mix engineer analyzing a single track inside a REAPER session. You
receive the track's FX chain (with current parameter values), routing (sends,
receives, parent bus, phase, automation mode), and a post-FX audio snapshot
(sample peak, crest factor, 1/3-octave spectrum, and, when available: integrated
LUFS, true peak, loudness range (LRA), momentary/short-term LUFS maxima, and a
stereo block with phase correlation, mid/side levels, and L/R balance). Some
fields may be null; treat null as "not measured", never as a value. Use
true_peak_db when present; never infer true peak from sample peak, they differ.
silence_fraction is how much of the capture is dead air: when it is high, every
level statistic is diluted by silence — weight your confidence accordingly and
say so.

Your job: diagnose what's wrong or could be improved. Be specific. Name
frequencies. Name parameters. Propose one concrete move, not five.

You see ONE track, not the mix. Do not diagnose frequency masking or claim
anything about how this track sits against others; you have no data for that.
Stay on what the single-track evidence supports: tonal balance, dynamics,
gain staging, FX-chain configuration.

Format:
1. DIAGNOSIS: [2-3 sentences. What you hear in the numbers.]
2. PROBABLE CAUSE: [1-2 sentences. Which FX / parameter / routing is likely
   causing it.]
3. SUGGESTED MOVE: [1-2 sentences. The exact parameter change. Include the
   plugin name, parameter name, current value, proposed value, and why.]
4. CONFIDENCE: [low / medium / high. Based on how much signal you have vs.
   how much you're guessing.]

Do not suggest moves you can't verify from the data. If the diagnosis is
uncertain, say so. An honest "I'm not sure" beats a confident wrong answer.
"""

# Sibling of SYSTEM_PROMPT. Only ever used when the payload carries 2+ tracks'
# spectra plus a computed contested-band table, so masking claims ARE backed by
# data here. This is a deliberate, gated relaxation of the single-track hedge,
# NOT a weakening of it: it stays honest about what coarse bands can prove.
MASKING_SYSTEM_PROMPT = """\
You are a mix engineer analyzing frequency masking BETWEEN tracks inside a REAPER
session. You receive two or more tracks, each with its FX chain, routing, and a
post-FX audio snapshot (1/3-octave spectrum, sample peak, crest factor, and when
available: LUFS, true peak, LRA, a stereo block with phase correlation and
mid/side levels, and silence_fraction — how much of that capture is dead air).
You also receive a precomputed masking table: for each pair of tracks,
the "contested" 1/3-octave bands where BOTH tracks have real energy, with each
track's level and which one is louder. Some fields may be null; treat null as "not
measured", never as a value.

Unlike single-track analysis, you DO have cross-track data, so you ARE allowed to
diagnose masking here, but only what the contested-band table supports. Honesty
rules that still bind you:
- 1/3-octave bands are coarse. A contested band flags a candidate collision
  region, not a proven audible one. Say "candidate" / "likely", not "definitely".
- A shared band is not automatically a problem. Two tracks can share low end by
  design (kick + bass). Judge whether the overlap is likely to cause mud or
  smearing, and say when it's probably fine.
- You cannot resolve the exact collision frequency inside a wide band; name the
  region, not a false-precise single Hz.
- The louder track in a contested band is the likely masker; the quieter is at
  risk. Base "who moves" on that plus the FX chains you can see.

Your job: identify the most likely masking problem across these tracks and propose
ONE concrete move. Prefer a complementary move (a small carve in the masker where
the masked track lives, or a boost/reposition of the masked track) referencing a
real EQ/plugin already in the relevant track's FX chain when one exists.

Format:
1. DIAGNOSIS: [2-3 sentences. Which two tracks contest which region, and why it
   likely does or doesn't matter.]
2. PROBABLE CAUSE: [1-2 sentences. Which track/FX is doing the masking.]
3. SUGGESTED MOVE: [1-2 sentences. The exact change: track, plugin, parameter,
   current value, proposed value, and why. One move.]
4. CONFIDENCE: [low / medium / high. Coarse bands and a single snapshot cap how
   high this can honestly go.]

If the tracks barely overlap, say the masking is minimal and stop; do not invent a
problem to look useful. An honest "these two aren't really fighting" beats a
confident wrong carve.
"""


# REAPER's RENDER_STATS keys -> payload field names. The raw string is
# semicolon-separated KEY:value pairs; key spellings vary a little between
# stats, so each field lists every spelling observed or plausible. First
# match wins. Non-numeric values (e.g. FILE:C:\out.wav) are skipped.
_RENDER_STATS_FIELDS = [
    ("true_peak_db", ("TRUEPEAK", "TPEAK", "TPK")),
    ("loudness_range_lu", ("LRA",)),
    ("lufs_momentary_max", ("LUFSMMAX", "MAXLUFSM", "LUFSM")),
    ("lufs_short_term_max", ("LUFSSMAX", "MAXLUFSS", "LUFSS")),
]


def parse_render_stats(raw):
    """Pull true peak, LRA, and momentary/short-term LUFS maxima out of the
    raw RENDER_STATS string the bridge passes through. The bridge itself only
    parses LUFS-I (surfaced separately as integrated_lufs); everything else
    REAPER measured was being dropped on the floor. Returns {} when raw is
    missing; absent keys are simply omitted (never null-filled)."""
    if not raw:
        return {}
    values = {}
    for pair in raw.split(";"):
        key, sep, value = pair.partition(":")
        if not sep:
            continue
        try:
            number = float(value)
        except ValueError:
            continue
        if math.isfinite(number):
            values.setdefault(key.strip().upper(), number)
    out = {}
    for field, keys in _RENDER_STATS_FIELDS:
        for key in keys:
            if key in values:
                out[field] = values[key]
                break
    return out


def _audio_block(stats, capture_data):
    block = {
        "duration_seconds": stats.duration_seconds,
        "integrated_lufs": capture_data.get("render_loudness_lufs"),
        "sample_peak_db": stats.sample_peak_db,
        "rms_db": stats.rms_db,
        "crest_factor_db": stats.crest_factor_db,
        "silence_fraction": stats.silence_fraction,
        "stereo": stats.stereo,
    }
    block.update(parse_render_stats(capture_data.get("render_stats_raw")))
    block["spectrum_third_octave"] = stats.spectrum_third_octave
    block["spectrum_note"] = (
        "1/3-octave band levels in dBFS, channels combined in the power domain "
        "(no phase cancellation)"
    )
    return block


def _capture_block(capture_data):
    """Capture provenance from Reaper Daemon's result.

    Audio metrics are only per-track evidence when the daemon verified the
    capture was isolated. Preserve the daemon's scope and human explanation in
    the payload so callers can enforce that boundary rather than losing it in a
    presentation-only note.
    """
    return {
        "scope": capture_data.get("capture_scope") or "unknown",
        "isolation_verified": capture_data.get("isolation_verified") is True,
        "note": capture_data.get("note"),
    }


def _resolve_scan_track(track_scan, target_name):
    """Pick the target track out of a scan_fx result by name, falling back to the
    first entry so a multi-track scan isn't silently reduced to index 0."""
    tracks = track_scan.get("tracks") or [{}]
    if target_name is not None:
        match = next((t for t in tracks if t.get("name") == target_name), None)
        if match is not None:
            return match
    return tracks[0]


def build_payload(context, track_scan, routing, capture_data, stats, target_name=None):
    """Assemble the model payload from bridge results + local analysis.

    context: get_context data (or None), track_scan: scan_fx data for the
    target track, routing: get_track_routing data, capture_data: the
    capture_track_audio result, stats: TrackStats from analysis.analyze_wav,
    target_name: the resolved track name to pick out of the scan (falls back to
    the first entry) so a multi-track scan isn't silently reduced to index 0.
    """
    track = _resolve_scan_track(track_scan, target_name)
    payload = {
        "project": {
            "name": (context or {}).get("project_name"),
            "tempo": (context or {}).get("tempo"),
        },
        "track": {
            "name": track.get("name"),
            "index": track.get("index"),
            "volume_db": routing.get("volume_db"),
            "pan": routing.get("pan"),
            "parent_track": (routing.get("parent_track") or {}).get("name"),
            "phase_inverted": routing.get("phase_inverted"),
            "automation_mode": routing.get("automation_mode"),
        },
        "fx_chain": track.get("fx", []),
        "routing": {
            "sends": routing.get("sends", []),
            "receives": routing.get("receives", []),
        },
        "capture": _capture_block(capture_data),
        "audio": _audio_block(stats, capture_data),
    }
    return payload


def build_masking_payload(context, per_track, masking):
    """Assemble the cross-track masking payload.

    per_track: list of dicts, one per captured track, each with keys
    {name, track_scan, routing, capture_data, stats} (the same objects the
    single-track path produces, one set per track). masking: the dict returned by
    analysis.masking_overlap over every track's spectrum. Structure mirrors the
    single-track payload but carries a list of tracks plus the masking table.
    """
    tracks = []
    for pt in per_track:
        track = _resolve_scan_track(pt["track_scan"], pt["name"])
        routing = pt["routing"]
        tracks.append(
            {
                "name": track.get("name"),
                "index": track.get("index"),
                "volume_db": routing.get("volume_db"),
                "pan": routing.get("pan"),
                "parent_track": (routing.get("parent_track") or {}).get("name"),
                "fx_chain": track.get("fx", []),
                "routing": {
                    "sends": routing.get("sends", []),
                    "receives": routing.get("receives", []),
                },
                "capture": _capture_block(pt["capture_data"]),
                "audio": _audio_block(pt["stats"], pt["capture_data"]),
            }
        )
    return {
        "project": {
            "name": (context or {}).get("project_name"),
            "tempo": (context or {}).get("tempo"),
        },
        "tracks": tracks,
        "masking": masking,
    }


def diagnose(
    payload,
    client=None,
    system=SYSTEM_PROMPT,
    intro="Diagnose this track:",
    provider=None,
    profile: ModelProfile | None = None,
):
    """Send the payload to the model, return the diagnosis text. system/intro
    default to the single-track hedge contract; the masking path passes
    MASKING_SYSTEM_PROMPT and its own intro."""
    if provider is None:
        from .providers.anthropic_provider import AnthropicProvider

        if client is None:
            provider, profile = AnthropicProvider.from_config()
        else:
            provider = AnthropicProvider(client)
            profile = profile or AnthropicProvider.model_profile_from_config()
    elif profile is None:
        raise ValueError("an injected provider requires an explicit model profile")

    if profile is None:  # Defensive for type checkers and future provider factories.
        raise ValueError("provider resolution did not return a model profile")
    result = provider.generate(
        system_contract=system,
        payload=payload,
        response_schema=TextDiagnosisResult,
        model_profile=profile,
        user_instruction=intro,
    )
    return TextDiagnosisResult.model_validate(result).text
