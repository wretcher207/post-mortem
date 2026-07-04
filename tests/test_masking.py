"""Unit tests for cross-track masking analysis and payload assembly."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from postmortem import diagnose  # noqa: E402
from postmortem.analysis import TrackStats, masking_overlap  # noqa: E402


def _spectrum(levels):
    """levels: {freq_hz: level_db} -> the spectrum_third_octave list shape."""
    return [{"freq_hz": f, "level_db": db} for f, db in levels.items()]


class TestMaskingOverlap(unittest.TestCase):
    def test_shared_low_end_is_contested(self):
        # Kick and bass both live at 63 Hz; that band must be flagged.
        kick = _spectrum({63: -6.0, 4000: -30.0})
        bass = _spectrum({63: -4.0, 200: -8.0})
        result = masking_overlap({"Kick": kick, "Bass": bass})
        pair = result["pairs"][0]
        bands = {b["freq_hz"] for b in pair["contested_bands"]}
        self.assertIn(63, bands)
        band = next(b for b in pair["contested_bands"] if b["freq_hz"] == 63)
        # Bass is louder at 63 (-4 vs -6), so it's the likely masker.
        self.assertEqual(band["louder"], "Bass")
        self.assertEqual(band["diff_db"], -2.0)  # kick minus bass

    def test_non_overlapping_tracks_have_no_contest(self):
        # A sub track and a top-end cymbal share nothing.
        sub = _spectrum({40: -3.0, 50: -5.0})
        cym = _spectrum({8000: -6.0, 10000: -8.0})
        result = masking_overlap({"Sub": sub, "Cymbals": cym})
        self.assertEqual(result["pairs"][0]["contested_bands"], [])
        self.assertIn("No shared bands", result["pairs"][0]["summary"])

    def test_quiet_bands_below_prominence_are_ignored(self):
        # Track B has a whisper of energy at 63 (40 dB below its own peak); it
        # must not count as contesting the kick's loud 63 Hz band.
        kick = _spectrum({63: -6.0})
        b = _spectrum({63: -55.0, 1000: -8.0})
        result = masking_overlap({"Kick": kick, "B": b})
        self.assertEqual(result["pairs"][0]["contested_bands"], [])

    def test_silent_track_masks_nothing(self):
        kick = _spectrum({63: -6.0, 125: -8.0})
        silent = _spectrum({63: -110.0, 125: -112.0})
        result = masking_overlap({"Kick": kick, "Silent": silent})
        self.assertEqual(result["pairs"][0]["contested_bands"], [])

    def test_three_tracks_produce_all_pairs(self):
        a = _spectrum({63: -6.0})
        b = _spectrum({63: -5.0})
        c = _spectrum({63: -7.0})
        result = masking_overlap({"A": a, "B": b, "C": c})
        pairs = {(p["a"], p["b"]) for p in result["pairs"]}
        self.assertEqual(pairs, {("A", "B"), ("A", "C"), ("B", "C")})


class TestBuildMaskingPayload(unittest.TestCase):
    def _pt(self, name, index, spectrum):
        return {
            "name": name,
            "track_scan": {"tracks": [{"name": name, "index": index, "fx": [{"name": "ReaEQ"}]}]},
            "routing": {"volume_db": -3.0, "pan": 0.0, "parent_track": {"name": "Drum Bus"},
                        "sends": [], "receives": []},
            "capture_data": {"render_loudness_lufs": -18.0},
            "stats": TrackStats(
                duration_seconds=30.0, sample_rate=48000, channels=2,
                sample_peak_db=-1.0, rms_db=-12.0, crest_factor_db=11.0,
                spectrum_third_octave=spectrum,
            ),
        }

    def test_payload_lists_tracks_and_masking(self):
        per_track = [
            self._pt("Kick", 0, _spectrum({63: -6.0})),
            self._pt("Bass", 1, _spectrum({63: -4.0})),
        ]
        masking = masking_overlap({pt["name"]: pt["stats"].spectrum_third_octave for pt in per_track})
        payload = diagnose.build_masking_payload({"project_name": "mix.RPP", "tempo": 174}, per_track, masking)
        self.assertEqual(payload["project"]["name"], "mix.RPP")
        self.assertEqual([t["name"] for t in payload["tracks"]], ["Kick", "Bass"])
        self.assertEqual(payload["tracks"][0]["fx_chain"], [{"name": "ReaEQ"}])
        self.assertIn("pairs", payload["masking"])
        self.assertEqual(payload["tracks"][0]["audio"]["integrated_lufs"], -18.0)


class TestMaskingDiagnoseUsesSiblingContract(unittest.TestCase):
    def test_masking_prompt_is_sent_not_single_track(self):
        # Reuse the fake client shape: record the system prompt actually sent.
        calls = []

        class _Msgs:
            def create(self, **kwargs):
                calls.append(kwargs)
                class _R:
                    content = [type("B", (), {"type": "text", "text": "ok"})()]
                    stop_reason = "end_turn"
                return _R()

        class _Client:
            messages = _Msgs()

        diagnose.diagnose({"tracks": []}, client=_Client(),
                          system=diagnose.MASKING_SYSTEM_PROMPT,
                          intro="Diagnose masking across these tracks:")
        self.assertIn("frequency masking BETWEEN tracks", calls[0]["system"])
        self.assertIn("Diagnose masking across these tracks:", calls[0]["messages"][0]["content"])


if __name__ == "__main__":
    unittest.main()
