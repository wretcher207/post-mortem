"""Payload assembly + model call. The hedge contract lives here; don't weaken it."""

import json
import os

import anthropic

MODEL = os.environ.get("POSTMORTEM_MODEL", "claude-opus-4-8")

SYSTEM_PROMPT = """\
You are a mix engineer analyzing a single track inside a REAPER session. You
receive the track's FX chain (with current parameter values), routing (sends,
receives, parent bus), and a post-FX audio snapshot (LUFS, true peak, crest
factor, and 1/3-octave spectrum).

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


def build_payload(context, track_scan, routing, capture_data, stats):
    """Assemble the model payload from bridge results + local analysis.

    context: get_context data (or None), track_scan: scan_fx data for the
    target track, routing: get_track_routing data, capture_data: the
    capture_track_audio result, stats: TrackStats from analysis.analyze_wav.
    """
    track = track_scan["tracks"][0]
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
        },
        "fx_chain": track.get("fx", []),
        "routing": {
            "sends": routing.get("sends", []),
            "receives": routing.get("receives", []),
        },
        "audio": {
            "duration_seconds": stats.duration_seconds,
            "integrated_lufs": capture_data.get("render_loudness_lufs"),
            "sample_peak_db": stats.sample_peak_db,
            "rms_db": stats.rms_db,
            "crest_factor_db": stats.crest_factor_db,
            "spectrum_third_octave": stats.spectrum_third_octave,
            "spectrum_note": "mono sum of the stem; hard-panned dual-mono sources read ~6 dB low",
        },
    }
    return payload


def diagnose(payload, client=None):
    """Send the payload to the model, return the diagnosis text."""
    client = client or anthropic.Anthropic()
    response = client.messages.create(
        model=MODEL,
        max_tokens=4096,
        thinking={"type": "adaptive"},
        system=SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": "Diagnose this track:\n\n" + json.dumps(payload, indent=1),
            }
        ],
    )
    if response.stop_reason == "refusal":
        return "Diagnosis unavailable: the model declined this request."
    return next((b.text for b in response.content if b.type == "text"), "")
