"""python3 -m postmortem "Track Name" [--seconds N]"""

import argparse
import sys

from . import bridge
from .analysis import analyze_wav
from .diagnose import build_payload, diagnose


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="postmortem",
        description="Post Mortem: AI mix diagnosis for a single REAPER track.",
    )
    parser.add_argument("track", help="target track name (exact, case-insensitive)")
    parser.add_argument("--seconds", type=int, default=30, help="capture length (default 30, from cursor)")
    parser.add_argument("--keep-wav", action="store_true", help="don't delete the temp stem after analysis")
    parser.add_argument("--payload-only", action="store_true", help="print the payload JSON and exit (no model call)")
    args = parser.parse_args(argv)

    print(f"[postmortem] {bridge.status()}", file=sys.stderr)

    print(f"[postmortem] reading FX chain + routing for '{args.track}'...", file=sys.stderr)
    track_scan = bridge.scan_fx(args.track)
    routing = bridge.get_track_routing(args.track)
    context = bridge.get_context()

    print(f"[postmortem] capturing {args.seconds}s post-FX stem...", file=sys.stderr)
    capture_data, wav_path = bridge.capture_track_audio(args.track, duration_seconds=args.seconds)

    print("[postmortem] analyzing...", file=sys.stderr)
    stats = analyze_wav(wav_path)
    payload = build_payload(context, track_scan, routing, capture_data, stats)

    if not args.keep_wav:
        import os

        os.unlink(wav_path)

    if args.payload_only:
        import json

        print(json.dumps(payload, indent=2))
        return 0

    print("[postmortem] diagnosing...", file=sys.stderr)
    print(diagnose(payload))
    return 0


if __name__ == "__main__":
    sys.exit(main())
