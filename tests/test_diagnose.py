"""Unit tests for payload assembly and the model-reply handling. A fake client
stands in for the Anthropic SDK, so no network or key is needed."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from postmortem import config, diagnose  # noqa: E402
from postmortem.analysis import TrackStats  # noqa: E402
from postmortem.providers import anthropic_provider  # noqa: E402
from postmortem.providers.base import ProviderError, ProviderErrorCategory  # noqa: E402
from postmortem.schemas import DiagnosisResult  # noqa: E402


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


class _StructuredProvider:
    def __init__(self):
        self.calls = []

    def generate(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "schema_version": 1,
            "finding": {
                "summary": "The upper mids are elevated.",
                "probable_cause": "The measured spectrum rises around 3 kHz.",
                "confidence": "medium",
                "confidence_reason": "The spectrum supports the finding, but context is limited.",
                "evidence_refs": [
                    {
                        "path": "audio.spectrum_third_octave[0].level_db",
                        "description": "Measured upper-mid band level.",
                    }
                ],
            },
            "proposal": {
                "operation": "none",
                "reason": "No safe verified parameter move is available.",
                "expected_direction": [],
            },
        }


class _RefusingProvider:
    def generate(self, **kwargs):
        raise ProviderError(
            ProviderErrorCategory.REFUSAL, "the provider declined the request"
        )


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

    def test_carries_capture_provenance(self):
        scan = {"tracks": [{"name": "Rhythm L", "index": 3, "fx": []}]}
        capture = {
            "capture_scope": "full_mix",
            "isolation_verified": False,
            "note": "CAUTION: this is the full mix, not an isolated track.",
        }
        payload = diagnose.build_payload(None, scan, self._routing(), capture, _stats())
        self.assertEqual(
            payload["capture"],
            {
                "scope": "full_mix",
                "isolation_verified": False,
                "note": "CAUTION: this is the full mix, not an isolated track.",
            },
        )

    def test_null_lufs_passes_through_as_none(self):
        scan = {"tracks": [{"name": "Rhythm L", "index": 3, "fx": []}]}
        capture = {}  # empty RENDER_STATS -> no render_loudness_lufs
        payload = diagnose.build_payload(None, scan, self._routing(), capture, _stats())
        self.assertIsNone(payload["audio"]["integrated_lufs"])
        self.assertEqual(payload["audio"]["sample_peak_db"], -1.0)

    def test_render_stats_fields_land_in_audio_block(self):
        scan = {"tracks": [{"name": "Rhythm L", "index": 3, "fx": []}]}
        capture = {
            "render_loudness_lufs": -18.3,
            "render_stats_raw": (
                "FILE:C:\\temp\\out.wav;PEAK:-1.02;TRUEPEAK:-0.84;"
                "LUFSI:-18.3;LUFSM:-14.9;LUFSS:-16.2;LRA:5.4"
            ),
        }
        payload = diagnose.build_payload(None, scan, self._routing(), capture, _stats())
        audio = payload["audio"]
        self.assertEqual(audio["true_peak_db"], -0.84)
        self.assertEqual(audio["loudness_range_lu"], 5.4)
        self.assertEqual(audio["lufs_momentary_max"], -14.9)
        self.assertEqual(audio["lufs_short_term_max"], -16.2)
        # Fields carried from TrackStats defaults.
        self.assertEqual(audio["silence_fraction"], 0.0)
        self.assertIsNone(audio["stereo"])

    def test_missing_render_stats_omits_fields_not_nulls_them(self):
        scan = {"tracks": [{"name": "Rhythm L", "index": 3, "fx": []}]}
        payload = diagnose.build_payload(None, scan, self._routing(), {}, _stats())
        self.assertNotIn("true_peak_db", payload["audio"])
        self.assertNotIn("loudness_range_lu", payload["audio"])

    def test_selects_target_track_not_index_zero(self):
        scan = {"tracks": [
            {"name": "Kick", "index": 0, "fx": []},
            {"name": "Snare", "index": 1, "fx": []},
        ]}
        payload = diagnose.build_payload(None, scan, self._routing(), {}, _stats(), target_name="Snare")
        self.assertEqual(payload["track"]["name"], "Snare")
        self.assertEqual(payload["track"]["index"], 1)

    def test_carries_stable_track_identity_into_structured_payload(self):
        scan = {
            "tracks": [
                {
                    "name": "Rhythm L",
                    "index": 3,
                    "guid": "{TRACK-GUID}",
                    "fx": [],
                }
            ]
        }

        payload = diagnose.build_payload(
            None, scan, self._routing(), {}, _stats(), target_name="Rhythm L"
        )

        self.assertEqual(payload["track"]["guid"], "{TRACK-GUID}")


class TestParseRenderStats(unittest.TestCase):
    def test_none_and_empty_return_empty(self):
        self.assertEqual(diagnose.parse_render_stats(None), {})
        self.assertEqual(diagnose.parse_render_stats(""), {})

    def test_file_path_with_drive_colon_is_skipped(self):
        out = diagnose.parse_render_stats("FILE:C:\\x.wav;TRUEPEAK:-2.5")
        self.assertEqual(out, {"true_peak_db": -2.5})

    def test_alternate_key_spellings(self):
        self.assertEqual(
            diagnose.parse_render_stats("TPK:-1.5"), {"true_peak_db": -1.5}
        )
        self.assertEqual(
            diagnose.parse_render_stats("LUFSMMAX:-12.0;LUFSM:-13.0"),
            {"lufs_momentary_max": -12.0},
        )

    def test_non_numeric_and_partial_pairs_ignored(self):
        self.assertEqual(diagnose.parse_render_stats("LRA:abc;TRUEPEAK;X"), {})


class TestDiagnoseReply(unittest.TestCase):
    def _payload(self):
        return {"audio": {}}

    def test_joins_multiple_text_blocks(self):
        client = _FakeClient(_Response([_Block("part one"), _Block("part two")]))
        out = diagnose.diagnose(self._payload(), client=client)
        self.assertIn("part one", out)
        self.assertIn("part two", out)

    def test_max_tokens_truncation_is_typed(self):
        client = _FakeClient(_Response([_Block("DIAGNOSIS: boom")], stop_reason="max_tokens"))
        with self.assertRaises(ProviderError) as ctx:
            diagnose.diagnose(self._payload(), client=client)
        self.assertIs(ctx.exception.category, ProviderErrorCategory.INCOMPLETE_RESPONSE)

    def test_empty_text_is_typed(self):
        client = _FakeClient(_Response([_Block(None, type="thinking")], stop_reason="end_turn"))
        with self.assertRaises(ProviderError) as ctx:
            diagnose.diagnose(self._payload(), client=client)
        self.assertIs(ctx.exception.category, ProviderErrorCategory.INCOMPLETE_RESPONSE)

    def test_refusal_is_typed(self):
        client = _FakeClient(_Response([], stop_reason="refusal"))
        with self.assertRaises(ProviderError) as ctx:
            diagnose.diagnose(self._payload(), client=client)
        self.assertIs(ctx.exception.category, ProviderErrorCategory.REFUSAL)

    def test_track_check_uses_structured_contract_and_returns_typed_result(self):
        provider = _StructuredProvider()
        profile = diagnose.ModelProfile(model="test", thinking=False)

        result = diagnose.diagnose_track(
            self._payload(), provider=provider, profile=profile
        )

        self.assertIsInstance(result, DiagnosisResult)
        self.assertEqual(result.proposal.operation, "none")
        self.assertIs(provider.calls[0]["response_schema"], DiagnosisResult)
        contract = " ".join(provider.calls[0]["system_contract"].lower().split())
        self.assertIn("do not diagnose frequency masking", contract)
        self.assertIn("evidence references", contract)
        self.assertIn("operation: none", contract)

    def test_track_check_turns_provider_refusal_into_non_actionable_result(self):
        result = diagnose.diagnose_track(
            self._payload(),
            provider=_RefusingProvider(),
            profile=diagnose.ModelProfile(model="test", thinking=False),
        )

        self.assertEqual(result.finding.confidence, "low")
        self.assertEqual(result.proposal.operation, "none")
        self.assertEqual(result.proposal.rejection_reason, "provider_refusal")


class TestProviderProfile(unittest.TestCase):
    def test_endpoint_classification(self):
        self.assertTrue(anthropic_provider._is_anthropic_endpoint(None))
        self.assertTrue(
            anthropic_provider._is_anthropic_endpoint("https://api.anthropic.com")
        )
        self.assertFalse(
            anthropic_provider._is_anthropic_endpoint(
                "https://api.deepseek.com/anthropic"
            )
        )
        self.assertFalse(
            anthropic_provider._is_anthropic_endpoint(
                "https://anthropic.com.attacker.example"
            )
        )

    def test_thinking_default_on_off_toggle(self):
        config._file_values = {}
        os.environ.pop("POSTMORTEM_THINKING", None)
        self.assertTrue(anthropic_provider._thinking_enabled())
        os.environ["POSTMORTEM_THINKING"] = "off"
        try:
            self.assertFalse(anthropic_provider._thinking_enabled())
        finally:
            os.environ.pop("POSTMORTEM_THINKING", None)


if __name__ == "__main__":
    unittest.main()
