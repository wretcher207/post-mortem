"""Unit tests for track-name resolution and the wrong-track guard."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from postmortem import bridge, cli  # noqa: E402


class TestResolveTrack(unittest.TestCase):
    def test_exact_match(self):
        self.assertEqual(cli.resolve_track("Kick", ["Kick", "Snare"]), "Kick")

    def test_case_insensitive(self):
        self.assertEqual(cli.resolve_track("kick", ["Kick", "Snare"]), "Kick")

    def test_unique_substring(self):
        self.assertEqual(cli.resolve_track("kick", ["Kick - stem", "Snare"]), "Kick - stem")

    def test_duplicate_exact_name_rejected(self):
        with self.assertRaises(cli.TrackNotResolved):
            cli.resolve_track("Guitar", ["Guitar", "Guitar", "Bass"])

    def test_duplicate_case_insensitive_rejected(self):
        with self.assertRaises(cli.TrackNotResolved):
            cli.resolve_track("guitar", ["Guitar", "GUITAR"])

    def test_ambiguous_substring_lists_candidates(self):
        with self.assertRaises(cli.TrackNotResolved) as ctx:
            cli.resolve_track("gtr", ["Gtr L", "Gtr R"])
        self.assertIn("Gtr L", str(ctx.exception))

    def test_whitespace_only_track_name_does_not_crash(self):
        with self.assertRaises(cli.TrackNotResolved):
            cli.resolve_track("kick", ["   ", "Snare"])

    def test_no_match_lists_tracks(self):
        with self.assertRaises(cli.TrackNotResolved) as ctx:
            cli.resolve_track("Vocals", ["Kick", "Snare"])
        self.assertIn("No track matches", str(ctx.exception))


class TestAssertSameTrack(unittest.TestCase):
    def test_guid_mismatch_raises(self):
        with self.assertRaises(bridge.BridgeError):
            cli._assert_same_track(
                {"tracks": [{"guid": "A"}]},
                {"track": {"guid": "A"}},
                {"track": {"guid": "B"}},
            )

    def test_matching_guids_ok(self):
        cli._assert_same_track(
            {"tracks": [{"guid": "A"}]},
            {"track": {"guid": "A"}},
            {"track": {"guid": "A"}},
        )

    def test_missing_guids_are_skipped(self):
        # scan has no guid; routing and capture agree -> fine.
        cli._assert_same_track(
            {"tracks": [{}]},
            {"track": {"guid": "A"}},
            {"track": {"guid": "A"}},
        )


class TestCaptureSeconds(unittest.TestCase):
    def test_rejects_zero_and_negative(self):
        import argparse

        for bad in ("0", "-30"):
            with self.assertRaises(argparse.ArgumentTypeError):
                cli._capture_seconds(bad)

    def test_rejects_over_max(self):
        import argparse

        with self.assertRaises(argparse.ArgumentTypeError):
            cli._capture_seconds("601")

    def test_accepts_valid(self):
        self.assertEqual(cli._capture_seconds("30"), 30)


if __name__ == "__main__":
    unittest.main()
