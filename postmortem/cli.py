"""python3 -m postmortem "Track Name" [--seconds N]"""

import argparse
import difflib
import os
import sys

from . import bridge
from .analysis import analyze_wav
from .diagnose import build_payload, diagnose


class TrackNotResolved(Exception):
    """Raised when the requested track name can't be matched to exactly one
    track. Carries a human-readable, multi-line message for the CLI to print."""


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
    if requested in names:
        return requested

    ci = [n for n in names if n.lower() == requested.lower()]
    if len(ci) == 1:
        return ci[0]

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
        description="Post Mortem: AI mix diagnosis for a single REAPER track.",
    )
    parser.add_argument("track", help="target track name (case-insensitive; unique substring is enough)")
    parser.add_argument("--seconds", type=_capture_seconds, default=30, help="capture length 1-600 (default 30, from cursor)")
    parser.add_argument("--keep-wav", action="store_true", help="don't delete the temp stem after analysis")
    parser.add_argument("--payload-only", action="store_true", help="print the payload JSON and exit (no model call)")
    args = parser.parse_args(argv)

    try:
        return _run(args)
    except bridge.BridgeError as e:
        print(f"[postmortem] {e}", file=sys.stderr)
        return 1


def _run(args):
    print(f"[postmortem] {bridge.status()}", file=sys.stderr)

    context = bridge.get_context()
    try:
        track = resolve_track(args.track, _track_names(context))
    except TrackNotResolved as e:
        print(f"[postmortem] {e}", file=sys.stderr)
        return 2
    if track != args.track:
        print(f"[postmortem] resolved '{args.track}' -> '{track}'", file=sys.stderr)

    print(f"[postmortem] reading FX chain + routing for '{track}'...", file=sys.stderr)
    track_scan = bridge.scan_fx(track)
    routing = bridge.get_track_routing(track)

    print(f"[postmortem] capturing {args.seconds}s post-FX stem...", file=sys.stderr)
    capture_data, wav_path = bridge.capture_track_audio(track, duration_seconds=args.seconds)

    # We own wav_path (bridge verified it's the exact temp file we asked for);
    # clean it up on every path unless --keep-wav, even if analysis or the model
    # call raises. --keep-wav preserves it for inspection.
    try:
        print("[postmortem] analyzing...", file=sys.stderr)
        stats = analyze_wav(wav_path)
        payload = build_payload(context, track_scan, routing, capture_data, stats)

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


if __name__ == "__main__":
    sys.exit(main())
