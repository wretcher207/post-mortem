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


def _is_anthropic_endpoint(base_url):
    """True for the real Anthropic API (or no override). A compatible endpoint
    like DeepSeek's /anthropic or MiniMax's /anthropic is NOT the Anthropic API,
    even though its path contains the word 'anthropic'."""
    return (not base_url) or "anthropic.com" in base_url


def _thinking_enabled(base_url):
    """Whether to send the adaptive-thinking extension. Configurable via
    POSTMORTEM_THINKING (adaptive|off); defaults on. Compatible endpoints that
    don't implement thinking can turn it off without code changes."""
    mode = (config.get("POSTMORTEM_THINKING") or "adaptive").strip().lower()
    return mode not in ("off", "0", "false", "none")


def _resolve_client_and_model():
    """Resolve endpoint, key, and model as ONE provider profile.

    A non-Anthropic compatible endpoint (DeepSeek, MiniMax, ...) must use its
    OWN key: either POSTMORTEM_API_KEY, or an ANTHROPIC_API_KEY set in the
    config file alongside the base_url. It must NEVER borrow a bare env
    ANTHROPIC_API_KEY, which belongs to api.anthropic.com and would be leaked to
    the third-party vendor. Lives here (not the wrapper) so every invocation
    path gets the same auth; the david-secrets fallbacks are dev-machine no-ops
    elsewhere."""
    model = config.get("POSTMORTEM_MODEL")
    base_url = config.get("ANTHROPIC_BASE_URL")

    if not _is_anthropic_endpoint(base_url):
        # Same-source key only: a dedicated POSTMORTEM_API_KEY, or an
        # ANTHROPIC_API_KEY that lives in the config file next to the base_url.
        key = config.get("POSTMORTEM_API_KEY") or config.file_get("ANTHROPIC_API_KEY")
        if key:
            return anthropic.Anthropic(api_key=key, base_url=base_url), model or "claude-opus-4-8"
        # Dev-machine MiniMax fallback (explicit, from david-secrets).
        key_line = _first_line_matching(
            os.path.join(SECRETS_DIR, "minimax-api.md"),
            lambda l: l.startswith("- Key:"),
        )
        if key_line and "minimax" in base_url:
            key = key_line.split(":", 1)[1].strip()
            return anthropic.Anthropic(api_key=key, base_url=base_url), model or "MiniMax-M3"
        raise SystemExit(
            f"postmortem: {base_url} is a non-Anthropic endpoint. Set its own key\n"
            f"as POSTMORTEM_API_KEY (env or {config.CONFIG_PATH}). A bare\n"
            "ANTHROPIC_API_KEY from the environment is NOT used for a third-party\n"
            "endpoint, to avoid sending your Anthropic key to another vendor."
        )

    # Real Anthropic API.
    key = config.get("ANTHROPIC_API_KEY") or _first_line_matching(
        os.path.join(SECRETS_DIR, "anthropic-api-key"),
        lambda l: l.startswith("sk-ant-"),
    )
    if key:
        return anthropic.Anthropic(api_key=key), model or "claude-opus-4-8"

    raise SystemExit(
        "postmortem: no API key found. Set ANTHROPIC_API_KEY in the\n"
        f"environment, or create {config.CONFIG_PATH} with:\n"
        "  ANTHROPIC_API_KEY=<your key>\n"
        "  # optional, for Anthropic-compatible endpoints like DeepSeek/MiniMax:\n"
        "  ANTHROPIC_BASE_URL=https://api.deepseek.com/anthropic\n"
        "  POSTMORTEM_API_KEY=<that endpoint's key>\n"
        "  POSTMORTEM_MODEL=deepseek-v4-flash"
    )

SYSTEM_PROMPT = """\
You are a mix engineer analyzing a single track inside a REAPER session. You
receive the track's FX chain (with current parameter values), routing (sends,
receives, parent bus, phase, automation mode), and a post-FX audio snapshot
(sample peak, crest factor, 1/3-octave spectrum, and integrated LUFS when it is
available). Some fields may be null; treat null as "not measured", never as a
value. Do not infer true-peak headroom from sample peak; they differ.

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


def build_payload(context, track_scan, routing, capture_data, stats, target_name=None):
    """Assemble the model payload from bridge results + local analysis.

    context: get_context data (or None), track_scan: scan_fx data for the
    target track, routing: get_track_routing data, capture_data: the
    capture_track_audio result, stats: TrackStats from analysis.analyze_wav,
    target_name: the resolved track name to pick out of the scan (falls back to
    the first entry) so a multi-track scan isn't silently reduced to index 0.
    """
    tracks = track_scan.get("tracks") or [{}]
    track = None
    if target_name is not None:
        track = next((t for t in tracks if t.get("name") == target_name), None)
    if track is None:
        track = tracks[0]
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
        "audio": {
            "duration_seconds": stats.duration_seconds,
            "integrated_lufs": capture_data.get("render_loudness_lufs"),
            "sample_peak_db": stats.sample_peak_db,
            "rms_db": stats.rms_db,
            "crest_factor_db": stats.crest_factor_db,
            "spectrum_third_octave": stats.spectrum_third_octave,
            "spectrum_note": "1/3-octave band levels in dBFS, channels combined in the power domain (no phase cancellation)",
        },
    }
    return payload


def diagnose(payload, client=None):
    """Send the payload to the model, return the diagnosis text."""
    if client is None:
        client, model = _resolve_client_and_model()
    else:
        # Honor the config file too, not just the environment, so an injected
        # client still targets the configured model.
        model = config.get("POSTMORTEM_MODEL", "claude-opus-4-8")
    request = {
        "model": model,
        # Thinking counts against this budget; reasoning models can burn 4k
        # tokens before the first text block, which returned an empty diagnosis.
        "max_tokens": 16384,
        "system": SYSTEM_PROMPT,
        "messages": [
            {
                "role": "user",
                "content": "Diagnose this track:\n\n" + json.dumps(payload, indent=1),
            }
        ],
    }
    if _thinking_enabled(config.get("ANTHROPIC_BASE_URL")):
        request["thinking"] = {"type": "adaptive"}
    response = client.messages.create(**request)
    if response.stop_reason == "refusal":
        return "Diagnosis unavailable: the model declined this request."
    # Join every text block, not just the first: a reasoning model can emit the
    # diagnosis across multiple text blocks, and taking only content[0] dropped
    # the rest.
    text = "\n".join(b.text for b in response.content if b.type == "text" and b.text).strip()
    if not text:
        return (
            "Diagnosis unavailable: the model returned no text "
            f"(stop_reason={response.stop_reason})."
        )
    # Fail loud, not open: a max_tokens cutoff yields a partial diagnosis that is
    # probably missing its CONFIDENCE line. Flag it instead of printing a truncated
    # answer as if it were complete.
    if response.stop_reason == "max_tokens":
        return (
            "[postmortem] WARNING: the model hit its token limit before finishing; "
            "this diagnosis is incomplete (likely missing its CONFIDENCE line). "
            "Treat it as partial.\n\n" + text
        )
    return text
