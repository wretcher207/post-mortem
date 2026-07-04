"""Unit tests for payload assembly and the model-reply handling. A fake client
stands in for the Anthropic SDK, so no network or key is needed."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from postmortem import config, diagnose  # noqa: E402
from postmortem.analysis import TrackStats  # noqa: E402


class _Block:
    def __init__(self, text=None, type="text"):
        self.type = type
        self.text = text


class _Response:
    def __init__(self, content, stop_reason="end_turn"):
        self.content = content
        self.stop_reason = stop_reason


class _FakeClient:
    """Records the create() kwargs and returns a canned response."""

    def __init__(self, response):
        self._response = response
        self.calls = []

        class _Messages:
            def create(inner, **kwargs):
                self.calls.append(kwargs)
                return self._response

        self.messages = _Messages()


def _stats():
    return TrackStats(
        duration_seconds=30.0,
        sample_rate=48000,
        channels=2,
        sample_peak_db=-1.0,
        rms_db=-12.0,
        crest_factor_db=11.0,
        spectrum_third_octave=[{"freq_hz": 1000, "level_db": -20.0}],
    )


class TestBuildPayload(unittest.TestCase):
    def _routing(self):
        return {
            "volume_db": -3.0,
            "pan": 0.0,
            "parent_track": {"name": "Guitar Bus"},
            "phase_inverted": True,
            "automation_mode": "trim/read",
            "sends": [{"target": "Drum Bus"}],
            "receives": [],
        }

    def test_carries_phase_and_automation(self):
        scan = {"tracks": [{"name": "Rhythm L", "index": 3, "fx": []}]}
        capture = {"render_loudness_lufs": -18.3}
        payload = diagnose.build_payload(None, scan, self._routing(), capture, _stats())
        self.assertEqual(payload["track"]["phase_inverted"], True)
        self.assertEqual(payload["track"]["automation_mode"], "trim/read")

    def test_null_lufs_passes_through_as_none(self):
        scan = {"tracks": [{"name": "Rhythm L", "index": 3, "fx": []}]}
        capture = {}  # empty RENDER_STATS -> no render_loudness_lufs
        payload = diagnose.build_payload(None, scan, self._routing(), capture, _stats())
        self.assertIsNone(payload["audio"]["integrated_lufs"])
        self.assertEqual(payload["audio"]["sample_peak_db"], -1.0)

    def test_selects_target_track_not_index_zero(self):
        scan = {"tracks": [
            {"name": "Kick", "index": 0, "fx": []},
            {"name": "Snare", "index": 1, "fx": []},
        ]}
        payload = diagnose.build_payload(None, scan, self._routing(), {}, _stats(), target_name="Snare")
        self.assertEqual(payload["track"]["name"], "Snare")
        self.assertEqual(payload["track"]["index"], 1)


class TestDiagnoseReply(unittest.TestCase):
    def _payload(self):
        return {"audio": {}}

    def test_joins_multiple_text_blocks(self):
        client = _FakeClient(_Response([_Block("part one"), _Block("part two")]))
        out = diagnose.diagnose(self._payload(), client=client)
        self.assertIn("part one", out)
        self.assertIn("part two", out)

    def test_max_tokens_truncation_is_flagged(self):
        client = _FakeClient(_Response([_Block("DIAGNOSIS: boom")], stop_reason="max_tokens"))
        out = diagnose.diagnose(self._payload(), client=client)
        self.assertIn("WARNING", out)
        self.assertIn("incomplete", out)

    def test_empty_text_reports_unavailable(self):
        client = _FakeClient(_Response([_Block(None, type="thinking")], stop_reason="end_turn"))
        out = diagnose.diagnose(self._payload(), client=client)
        self.assertIn("no text", out)

    def test_refusal_is_reported(self):
        client = _FakeClient(_Response([], stop_reason="refusal"))
        out = diagnose.diagnose(self._payload(), client=client)
        self.assertIn("declined", out)


class TestProviderProfile(unittest.TestCase):
    def test_endpoint_classification(self):
        self.assertTrue(diagnose._is_anthropic_endpoint(None))
        self.assertTrue(diagnose._is_anthropic_endpoint("https://api.anthropic.com"))
        self.assertFalse(diagnose._is_anthropic_endpoint("https://api.deepseek.com/anthropic"))

    def test_thinking_default_on_off_toggle(self):
        config._file_values = {}
        os.environ.pop("POSTMORTEM_THINKING", None)
        self.assertTrue(diagnose._thinking_enabled(None))
        os.environ["POSTMORTEM_THINKING"] = "off"
        try:
            self.assertFalse(diagnose._thinking_enabled("https://api.deepseek.com/anthropic"))
        finally:
            os.environ.pop("POSTMORTEM_THINKING", None)


if __name__ == "__main__":
    unittest.main()
