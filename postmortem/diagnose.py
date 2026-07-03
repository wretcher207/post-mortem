"""Payload assembly + model call. The hedge contract lives here; don't weaken it."""

import json
import os

import anthropic

from . import config

SECRETS_DIR = os.path.expanduser("~/.config/david-secrets")


def _first_line_matching(path, predicate):
    try:
        with open(path) as f:
            return next((line.strip() for line in f if predicate(line)), None)
    except OSError:
        return None


def _resolve_client_and_model():
    """Key/endpoint/model from env or ~/.config/postmortem/config (see
    config.py). Works with the Anthropic API or any Anthropic-compatible
    endpoint (MiniMax, etc.). Lives here (not the bin/ wrapper) so every
    invocation path gets the same auth. The david-secrets fallbacks keep
    the dev machine working; they're no-ops anywhere else."""
    model = config.get("POSTMORTEM_MODEL")
    key = config.get("ANTHROPIC_API_KEY")
    base_url = config.get("ANTHROPIC_BASE_URL")
    if key:
        if base_url:
            return anthropic.Anthropic(api_key=key, base_url=base_url), model or "claude-opus-4-8"
        return anthropic.Anthropic(api_key=key), model or "claude-opus-4-8"

    key = _first_line_matching(
        os.path.join(SECRETS_DIR, "anthropic-api-key"),
        lambda l: l.startswith("sk-ant-"),
    )
    if key:
        return anthropic.Anthropic(api_key=key), model or "claude-opus-4-8"

    key_line = _first_line_matching(
        os.path.join(SECRETS_DIR, "minimax-api.md"),
        lambda l: l.startswith("- Key:"),
    )
    if key_line:
        key = key_line.split(":", 1)[1].strip()
        return (
            anthropic.Anthropic(api_key=key, base_url=base_url or "https://api.minimax.io/anthropic"),
            model or "MiniMax-M3",
        )

    raise SystemExit(
        "postmortem: no API key found. Set ANTHROPIC_API_KEY in the\n"
        f"environment, or create {config.CONFIG_PATH} with:\n"
        "  ANTHROPIC_API_KEY=<your key>\n"
        "  # optional, for Anthropic-compatible endpoints like MiniMax:\n"
        "  ANTHROPIC_BASE_URL=https://api.minimax.io/anthropic\n"
        "  POSTMORTEM_MODEL=MiniMax-M3"
    )

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
    if client is None:
        client, model = _resolve_client_and_model()
    else:
        model = os.environ.get("POSTMORTEM_MODEL", "claude-opus-4-8")
    response = client.messages.create(
        model=model,
        # Thinking counts against this budget; reasoning models can burn 4k
        # tokens before the first text block, which returned an empty diagnosis.
        max_tokens=16384,
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
    text = next((b.text for b in response.content if b.type == "text"), "")
    if not text:
        return (
            "Diagnosis unavailable: the model returned no text "
            f"(stop_reason={response.stop_reason})."
        )
    return text
