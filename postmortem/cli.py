"""python3 -m postmortem "Track Name" [--seconds N]"""

import argparse
import difflib
import os
import sys

from . import bridge
from .analysis import analyze_wav, masking_overlap
from .diagnose import (
    MASKING_SYSTEM_PROMPT,
    build_masking_payload,
    build_payload,
    diagnose,
)


class TrackNotResolved(Exception):
    """Raised when the requested track name can't be matched to exactly one
    track. Carries a human-readable, multi-line message for the CLI to print."""


def _assert_same_track(track_scan, routing, capture_data):
    """Raise BridgeError if the scan / routing / capture commands resolved to
    different tracks. Compares GUIDs where present (routing and capture return
    one); scan entries may not carry a GUID, so they're skipped if absent."""
    guids = {
        "scan": (track_scan.get("tracks") or [{}])[0].get("guid"),
        "routing": (routing.get("track") or {}).get("guid"),
        "capture": (capture_data.get("track") or {}).get("guid"),
    }
    present = {k: v for k, v in guids.items() if v}
    if len(set(present.values())) > 1:
        raise bridge.BridgeError(
            "commands resolved to different tracks (GUID mismatch): "
            f"{present}. Refusing to diagnose mixed-track evidence."
        )


def _track_names(context):
    names = []
    for t in context.get("tracks", []):
        name = t.get("name") or t.get("track_name")
        if name:
            names.append(name)
    return names


def resolve_track(requested, names):
    """Forgiving track-name resolution against the live track list. Tries, in
    order: exact, case-insensitive exact, unique case-insensitive substring.
    Raises TrackNotResolved (with a helpful message) on no match or ambiguity."""
    exact = [n for n in names if n == requested]
    if len(exact) == 1:
        return requested
    if len(exact) > 1:
        raise TrackNotResolved(
            f"'{requested}' is the exact name of {len(exact)} tracks. Rename or "
            "reorder so the target is unique; the daemon can't tell them apart."
        )

    ci = [n for n in names if n.lower() == requested.lower()]
    if len(ci) == 1:
        return ci[0]
    if len(ci) > 1:
        listing = "\n".join(f"  - {n}" for n in ci)
        raise TrackNotResolved(
            f"'{requested}' matches {len(ci)} tracks case-insensitively:\n{listing}\n"
            "Quote the exact name."
        )

    sub = [n for n in names if requested.lower() in n.lower()]
    if len(sub) == 1:
        return sub[0]

    if len(sub) > 1:
        listing = "\n".join(f"  - {n}" for n in sub)
        raise TrackNotResolved(
            f"'{requested}' matches {len(sub)} tracks:\n{listing}\n"
            "Be more specific (quote the exact name)."
        )

    # No match. Suggest the closest names, then list everything. Match against
    # each track's leading token too, so a short typo ("kik") still finds
    # "Kick - stem" despite the suffix dragging down the full-string ratio.
    candidates = {n: n for n in names}
    for n in names:
        tokens = n.replace("_", " ").split(" - ")[0].split()
        token = tokens[0] if tokens else n
        candidates.setdefault(token, n)
    hits = difflib.get_close_matches(requested, list(candidates), n=3, cutoff=0.4)
    suggestions = list(dict.fromkeys(candidates[h] for h in hits))
    lines = [f"No track matches '{requested}'."]
    if suggestions:
        lines.append("Did you mean: " + ", ".join(f'"{s}"' for s in suggestions) + "?")
    lines.append("Tracks in this project:")
    lines.extend(f"  - {n}" for n in names)
    raise TrackNotResolved("\n".join(lines))


# Silence gate: refuse to spend a model call on a capture that is essentially
# dead air. Overall RMS below the first threshold, or nearly the whole capture
# below the per-block silence floor, both mean "the cursor was in the wrong
# place", not "here is a mix problem".
# Same -60 dBFS idea as analysis.MASKING_ABSOLUTE_FLOOR_DB but a different
# quantity (broadband RMS vs loudest 1/3-octave band) — tune them together.
NEAR_SILENT_RMS_DB = -60.0
SILENCE_GATE_FRACTION = 0.85


def silence_gate(stats):
    """Message explaining why this capture is too silent to diagnose, or None
    when it has enough signal. The caller decides whether the gate is binding
    (--force and --payload-only bypass it)."""
    if stats.rms_db <= NEAR_SILENT_RMS_DB:
        return (
            f"capture is essentially silent (RMS {stats.rms_db:.1f} dBFS). A "
            "diagnosis of dead air would be accurate and useless. Park the edit "
            "cursor where the track is actually playing and rerun "
            "(--force to diagnose anyway)."
        )
    if stats.silence_fraction >= SILENCE_GATE_FRACTION:
        return (
            f"{stats.silence_fraction:.0%} of the capture is silence "
            f"(RMS {stats.rms_db:.1f} dBFS). Park the edit cursor where the "
            "track is actually playing and rerun (--force to diagnose anyway)."
        )
    return None


def capture_isolation_gate(capture_data):
    """Explain why a capture is unsafe for per-track model diagnosis.

    Post Mortem can only make per-track or cross-track claims when Reaper
    Daemon verifies that the WAV is an isolated track capture. Missing
    provenance fails closed so an older daemon cannot silently reintroduce the
    full-mix diagnosis bug.
    """
    scope = capture_data.get("capture_scope") or "unknown"
    verified = capture_data.get("isolation_verified") is True
    if scope == "isolated_track" and verified:
        return None
    if scope == "full_mix":
        return (
            "capture is the full master mix, not an isolated track. Post Mortem "
            "will not diagnose it as per-track evidence. Use --payload-only for "
            "debugging, or capture an item-less routing track until item-track "
            "isolation is supported."
        )
    if scope == "master_output":
        return (
            "capture is the project master output, not an isolated track. Post "
            "Mortem will not use it for a per-track diagnosis."
        )
    return (
        "capture isolation could not be verified. Post Mortem will not diagnose "
        "unproven per-track evidence. Upgrade Reaper Daemon, then rerun."
    )


def _capture_seconds(value):
    """argparse type: a capture length in [1, 600]. Rejects zero/negative up
    front instead of forwarding a nonsense render duration to the daemon."""
    try:
        seconds = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"'{value}' is not an integer")
    if not 1 <= seconds <= 600:
        raise argparse.ArgumentTypeError("must be between 1 and 600 seconds")
    return seconds


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="postmortem",
        description="Post Mortem: AI mix diagnosis for REAPER tracks. One track "
        "= single-track diagnosis; two or more = cross-track masking.",
    )
    parser.add_argument(
        "track",
        nargs="+",
        help="target track name(s), case-insensitive, unique substring is enough. "
        "One name = single-track diagnosis; two or more = cross-track masking.",
    )
    parser.add_argument("--seconds", type=_capture_seconds, default=30, help="capture length 1-600 (default 30, from cursor)")
    parser.add_argument("--keep-wav", action="store_true", help="don't delete the temp stem after analysis")
    parser.add_argument("--payload-only", action="store_true", help="print the payload JSON and exit (no model call)")
    parser.add_argument("--force", action="store_true", help="diagnose even a capture the silence gate would refuse")
    args = parser.parse_args(argv)

    try:
        return _run(args)
    except bridge.BridgeError as e:
        print(f"[postmortem] {e}", file=sys.stderr)
        return 1


def _resolve_all(requested, names):
    """Resolve each requested name against the live track list. Prints each
    rename, returns the resolved list. Raises TrackNotResolved on any miss."""
    resolved = []
    for req in requested:
        r = resolve_track(req, names)
        if r != req:
            print(f"[postmortem] resolved '{req}' -> '{r}'", file=sys.stderr)
        resolved.append(r)
    return resolved


def _run(args):
    print(f"[postmortem] {bridge.status()}", file=sys.stderr)

    context = bridge.get_context()
    names = _track_names(context)
    try:
        resolved = _resolve_all(args.track, names)
    except TrackNotResolved as e:
        print(f"[postmortem] {e}", file=sys.stderr)
        return 2

    if len(resolved) > 1:
        return _run_masking(args, context, resolved)
    return _run_single(args, context, resolved[0])


def _run_single(args, context, track):
    print(f"[postmortem] reading FX chain + routing for '{track}'...", file=sys.stderr)
    track_scan = bridge.scan_fx(track)
    routing = bridge.get_track_routing(track)

    print(f"[postmortem] capturing {args.seconds}s post-FX stem...", file=sys.stderr)
    capture_data, wav_path = bridge.capture_track_audio(track, duration_seconds=args.seconds)

    # We own wav_path (bridge verified it's the exact temp file we asked for);
    # clean it up on every path unless --keep-wav, including a wrong-track or
    # non-isolated-capture rejection. --keep-wav preserves it for inspection.
    try:
        # Each command resolves the name independently, so a duplicate name or
        # a mid-run reorder could make them hit different tracks. Where a GUID
        # is present, they must all agree before we diagnose.
        _assert_same_track(track_scan, routing, capture_data)

        if not args.payload_only:
            gate = capture_isolation_gate(capture_data)
            if gate:
                print(f"[postmortem] {gate}", file=sys.stderr)
                return 4

        print("[postmortem] analyzing...", file=sys.stderr)
        stats = analyze_wav(wav_path)

        if not args.payload_only and not args.force:
            gate = silence_gate(stats)
            if gate:
                print(f"[postmortem] {gate}", file=sys.stderr)
                return 3

        payload = build_payload(context, track_scan, routing, capture_data, stats, target_name=track)

        if args.payload_only:
            import json

            print(json.dumps(payload, indent=2))
            return 0

        print("[postmortem] diagnosing...", file=sys.stderr)
        print(diagnose(payload))
        return 0
    finally:
        if not args.keep_wav:
            try:
                os.unlink(wav_path)
            except OSError:
                pass


def _run_masking(args, context, tracks):
    """Cross-track masking: capture each track's post-FX stem, compute the
    contested-band overlap, and diagnose masking with the sibling prompt."""
    distinct = list(dict.fromkeys(tracks))
    if len(distinct) < 2:
        print(
            "[postmortem] masking needs two distinct tracks; the names given all "
            f"resolved to '{distinct[0]}'. Pass two different tracks.",
            file=sys.stderr,
        )
        return 2

    per_track = []
    wav_paths = []
    try:
        for track in distinct:
            print(f"[postmortem] reading FX chain + routing for '{track}'...", file=sys.stderr)
            track_scan = bridge.scan_fx(track)
            routing = bridge.get_track_routing(track)

            print(f"[postmortem] capturing {args.seconds}s post-FX stem for '{track}'...", file=sys.stderr)
            capture_data, wav_path = bridge.capture_track_audio(track, duration_seconds=args.seconds)
            wav_paths.append(wav_path)
            _assert_same_track(track_scan, routing, capture_data)

            if not args.payload_only:
                gate = capture_isolation_gate(capture_data)
                if gate:
                    print(f"[postmortem] '{track}': {gate}", file=sys.stderr)
                    return 4

            print(f"[postmortem] analyzing '{track}'...", file=sys.stderr)
            stats = analyze_wav(wav_path)
            per_track.append(
                {
                    "name": track,
                    "track_scan": track_scan,
                    "routing": routing,
                    "capture_data": capture_data,
                    "stats": stats,
                }
            )

        if not args.payload_only and not args.force:
            gated = []
            for pt in per_track:
                gate = silence_gate(pt["stats"])
                if gate:
                    gated.append((pt["name"], gate))
            if gated:
                for name, gate in gated:
                    print(f"[postmortem] '{name}': {gate}", file=sys.stderr)
                print(
                    "[postmortem] a silent stem can't mask anything; not diagnosing.",
                    file=sys.stderr,
                )
                return 3

        masking = masking_overlap({pt["name"]: pt["stats"].spectrum_third_octave for pt in per_track})
        payload = build_masking_payload(context, per_track, masking)

        if args.payload_only:
            import json

            print(json.dumps(payload, indent=2))
            return 0

        print("[postmortem] diagnosing masking...", file=sys.stderr)
        print(diagnose(payload, system=MASKING_SYSTEM_PROMPT, intro="Diagnose masking across these tracks:"))
        return 0
    finally:
        if not args.keep_wav:
            for wav_path in wav_paths:
                try:
                    os.unlink(wav_path)
                except OSError:
                    pass


if __name__ == "__main__":
    sys.exit(main())
