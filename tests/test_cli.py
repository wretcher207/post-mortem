"""Unit tests for track-name resolution and the wrong-track guard."""

import io
import json
import os
import sys
import unittest
from contextlib import redirect_stderr
from io import StringIO
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from postmortem import bridge, cli  # noqa: E402
from postmortem.providers.base import ProviderError, ProviderErrorCategory  # noqa: E402
from postmortem.schemas import DiagnosisResult  # noqa: E402


def _diagnosis_result():
    return DiagnosisResult.model_validate(
        {
            "schema_version": 1,
            "finding": {
                "summary": "The upper mids are elevated.",
                "probable_cause": "The measured spectrum rises around 3 kHz.",
                "confidence": "medium",
                "confidence_reason": "The spectrum supports a cautious finding.",
                "evidence_refs": [{"path": "audio.spectrum_third_octave[0]"}],
            },
            "proposal": {
                "operation": "none",
                "reason": "No verified parameter move is available.",
                "expected_direction": [],
            },
        }
    )


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


class TestSilenceGate(unittest.TestCase):
    def _stats(self, rms_db=-12.0, silence_fraction=0.0):
        from postmortem.analysis import TrackStats

        return TrackStats(
            duration_seconds=30.0,
            sample_rate=48000,
            channels=2,
            sample_peak_db=-1.0,
            rms_db=rms_db,
            crest_factor_db=11.0,
            silence_fraction=silence_fraction,
        )

    def test_normal_capture_passes(self):
        self.assertIsNone(cli.silence_gate(self._stats()))

    def test_near_silent_rms_is_gated(self):
        msg = cli.silence_gate(self._stats(rms_db=-72.0))
        self.assertIn("essentially silent", msg)

    def test_mostly_silent_capture_is_gated(self):
        msg = cli.silence_gate(self._stats(rms_db=-30.0, silence_fraction=0.9))
        self.assertIn("90% of the capture is silence", msg)

    def test_quiet_but_present_signal_passes(self):
        # Quiet is not silent: a -45 dBFS pad with steady signal must pass.
        self.assertIsNone(cli.silence_gate(self._stats(rms_db=-45.0, silence_fraction=0.2)))


class TestCaptureIsolationGate(unittest.TestCase):
    def test_full_mix_capture_is_refused(self):
        message = cli.capture_isolation_gate(
            {
                "capture_scope": "full_mix",
                "isolation_verified": False,
            }
        )
        self.assertIn("full master mix", message)
        self.assertIn("will not diagnose", message)

    def test_missing_provenance_fails_closed(self):
        message = cli.capture_isolation_gate({})
        self.assertIn("could not be verified", message)

    def test_verified_isolated_capture_passes(self):
        self.assertIsNone(
            cli.capture_isolation_gate(
                {
                    "capture_scope": "isolated_track",
                    "isolation_verified": True,
                }
            )
        )

    def test_unverified_isolated_scope_is_refused(self):
        message = cli.capture_isolation_gate(
            {
                "capture_scope": "isolated_track",
                "isolation_verified": False,
            }
        )
        self.assertIn("could not be verified", message)


class TestSingleTrackCaptureSafety(unittest.TestCase):
    def test_non_diagnosable_captures_stop_before_analysis(self):
        args = SimpleNamespace(seconds=30, payload_only=False, force=False, keep_wav=True)
        track_scan = {"tracks": [{"name": "Guitar", "guid": "A", "fx": []}]}
        routing = {"track": {"guid": "A"}}
        for case, provenance in (
            ("full_mix", {"capture_scope": "full_mix", "isolation_verified": False}),
            ("master_output", {"capture_scope": "master_output", "isolation_verified": False}),
            ("unverified isolated_track", {"capture_scope": "isolated_track", "isolation_verified": False}),
            ("missing provenance", {}),
        ):
            with self.subTest(case=case):
                capture = {
                    "track": {"guid": "A"},
                    **provenance,
                }
                with (
                    patch.object(cli.bridge, "scan_fx", return_value=track_scan),
                    patch.object(cli.bridge, "get_track_routing", return_value=routing),
                    patch.object(cli.bridge, "capture_track_audio", return_value=(capture, "/tmp/capture.wav")),
                    patch.object(cli, "analyze_wav", side_effect=AssertionError("must not analyze unsafe capture")) as analyze,
                ):
                    self.assertEqual(cli._run_single(args, {"project_name": "mix.RPP"}, "Guitar"), 4)

                analyze.assert_not_called()

    def test_payload_only_keeps_full_mix_provenance_without_diagnosing(self):
        from postmortem.analysis import TrackStats

        args = SimpleNamespace(seconds=30, payload_only=True, force=False, keep_wav=True)
        track_scan = {"tracks": [{"name": "Guitar", "guid": "A", "fx": []}]}
        routing = {"track": {"guid": "A"}}
        capture = {
            "track": {"guid": "A"},
            "capture_scope": "full_mix",
            "isolation_verified": False,
            "note": "CAUTION: full mix fallback.",
        }
        stats = TrackStats(30.0, 48000, 2, -1.0, -12.0, 11.0)
        stdout = io.StringIO()

        with (
            patch.object(cli.bridge, "scan_fx", return_value=track_scan),
            patch.object(cli.bridge, "get_track_routing", return_value=routing),
            patch.object(cli.bridge, "capture_track_audio", return_value=(capture, "/tmp/full-mix.wav")),
            patch.object(cli, "analyze_wav", return_value=stats),
            patch.object(cli, "diagnose_track") as diagnose,
            redirect_stdout(stdout),
        ):
            self.assertEqual(cli._run_single(args, {"project_name": "mix.RPP"}, "Guitar"), 0)

        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["capture"]["scope"], "full_mix")
        self.assertFalse(payload["capture"]["isolation_verified"])
        diagnose.assert_not_called()

    def test_verified_isolated_capture_reaches_diagnosis(self):
        from postmortem.analysis import TrackStats

        args = SimpleNamespace(seconds=30, payload_only=False, force=False, keep_wav=True)
        track_scan = {"tracks": [{"name": "Guitar", "guid": "A", "fx": []}]}
        routing = {"track": {"guid": "A"}}
        capture = {
            "track": {"guid": "A"},
            "capture_scope": "isolated_track",
            "isolation_verified": True,
        }
        stats = TrackStats(30.0, 48000, 2, -1.0, -12.0, 11.0)

        with (
            patch.object(cli.bridge, "scan_fx", return_value=track_scan),
            patch.object(cli.bridge, "get_track_routing", return_value=routing),
            patch.object(cli.bridge, "capture_track_audio", return_value=(capture, "/tmp/isolated.wav")),
            patch.object(cli, "analyze_wav", return_value=stats),
            patch.object(cli, "diagnose_track", return_value=_diagnosis_result()) as diagnose,
        ):
            self.assertEqual(cli._run_single(args, {"project_name": "mix.RPP"}, "Guitar"), 0)

        diagnose.assert_called_once()

    def test_verified_single_track_uses_structured_track_check_and_text_output(self):
        from postmortem.analysis import TrackStats

        args = SimpleNamespace(seconds=30, payload_only=False, force=False, keep_wav=True)
        track_scan = {"tracks": [{"name": "Guitar", "guid": "A", "fx": []}]}
        routing = {"track": {"guid": "A"}}
        capture = {
            "track": {"guid": "A"},
            "capture_scope": "isolated_track",
            "isolation_verified": True,
        }
        stats = TrackStats(30.0, 48000, 2, -1.0, -12.0, 11.0)
        stdout = StringIO()

        with (
            patch.object(cli.bridge, "scan_fx", return_value=track_scan),
            patch.object(cli.bridge, "get_track_routing", return_value=routing),
            patch.object(cli.bridge, "capture_track_audio", return_value=(capture, "/tmp/isolated.wav")),
            patch.object(cli, "analyze_wav", return_value=stats),
            patch.object(cli, "diagnose_track", return_value=_diagnosis_result()) as track_check,
            redirect_stdout(stdout),
        ):
            self.assertEqual(cli._run_single(args, {"project_name": "mix.RPP"}, "Guitar"), 0)

        track_check.assert_called_once()
        output = stdout.getvalue()
        self.assertIn("DIAGNOSIS: The upper mids are elevated.", output)
        self.assertIn("SUGGESTED MOVE: Advice only.", output)


class TestMaskingCaptureSafety(unittest.TestCase):
    def test_non_diagnosable_captures_stop_before_masking_analysis(self):
        args = SimpleNamespace(seconds=30, payload_only=False, force=False, keep_wav=True)
        track_scan = {"tracks": [{"name": "Guitar", "guid": "A", "fx": []}]}
        routing = {"track": {"guid": "A"}}
        for case, provenance in (
            ("full_mix", {"capture_scope": "full_mix", "isolation_verified": False}),
            ("master_output", {"capture_scope": "master_output", "isolation_verified": False}),
            ("unverified isolated_track", {"capture_scope": "isolated_track", "isolation_verified": False}),
            ("missing provenance", {}),
        ):
            with self.subTest(case=case):
                capture = {
                    "track": {"guid": "A"},
                    **provenance,
                }
                with (
                    patch.object(cli.bridge, "scan_fx", return_value=track_scan),
                    patch.object(cli.bridge, "get_track_routing", return_value=routing),
                    patch.object(cli.bridge, "capture_track_audio", return_value=(capture, "/tmp/capture.wav")),
                    patch.object(cli, "analyze_wav", side_effect=AssertionError("must not analyze unsafe capture")) as analyze,
                ):
                    self.assertEqual(
                        cli._run_masking(args, {"project_name": "mix.RPP"}, ["Guitar", "Bass"]),
                        4,
                    )

                analyze.assert_not_called()

    def test_verified_isolated_captures_reach_masking_diagnosis(self):
        from postmortem.analysis import TrackStats

        args = SimpleNamespace(seconds=30, payload_only=False, force=False, keep_wav=True)
        guitar_scan = {"tracks": [{"name": "Guitar", "guid": "A", "fx": []}]}
        bass_scan = {"tracks": [{"name": "Bass", "guid": "B", "fx": []}]}
        guitar_routing = {"track": {"guid": "A"}}
        bass_routing = {"track": {"guid": "B"}}
        guitar_capture = {
            "track": {"guid": "A"},
            "capture_scope": "isolated_track",
            "isolation_verified": True,
        }
        bass_capture = {
            "track": {"guid": "B"},
            "capture_scope": "isolated_track",
            "isolation_verified": True,
        }
        guitar_stats = TrackStats(30.0, 48000, 2, -1.0, -12.0, 11.0, [{"freq_hz": 63, "level_db": -6.0}])
        bass_stats = TrackStats(30.0, 48000, 2, -1.0, -12.0, 11.0, [{"freq_hz": 63, "level_db": -4.0}])

        with (
            patch.object(cli.bridge, "scan_fx", side_effect=[guitar_scan, bass_scan]),
            patch.object(cli.bridge, "get_track_routing", side_effect=[guitar_routing, bass_routing]),
            patch.object(
                cli.bridge,
                "capture_track_audio",
                side_effect=[
                    (guitar_capture, "/tmp/guitar.wav"),
                    (bass_capture, "/tmp/bass.wav"),
                ],
            ),
            patch.object(cli, "analyze_wav", side_effect=[guitar_stats, bass_stats]),
            patch.object(cli, "diagnose", return_value="diagnosis") as diagnose,
        ):
            self.assertEqual(
                cli._run_masking(args, {"project_name": "mix.RPP"}, ["Guitar", "Bass"]),
                0,
            )

        diagnose.assert_called_once()


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


class TestProviderErrors(unittest.TestCase):
    def test_cli_prints_clean_provider_error_and_returns_stable_exit_code(self):
        error = ProviderError(
            ProviderErrorCategory.RATE_LIMIT,
            "the provider rate limit or available credit was exhausted",
        )
        stderr = StringIO()

        with patch.object(cli, "_run", side_effect=error), redirect_stderr(stderr):
            exit_code = cli.main(["Kick"])

        self.assertEqual(exit_code, 5)
        self.assertEqual(
            stderr.getvalue().strip(),
            "[postmortem] provider/rate_limit: "
            "the provider rate limit or available credit was exhausted",
        )


if __name__ == "__main__":
    unittest.main()
