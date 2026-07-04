"""Unit tests for the analysis layer. Synthetic WAVs, no REAPER needed."""

import math
import os
import struct
import sys
import tempfile
import unittest
import wave

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from postmortem.analysis import analyze_wav, read_wav_mono  # noqa: E402


def write_sine_wav(path, freq_hz, amplitude=1.0, seconds=2.0, rate=48000, width=2, channels=1):
    n = int(seconds * rate)
    t = np.arange(n) / rate
    samples = amplitude * np.sin(2 * np.pi * freq_hz * t)
    with wave.open(path, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.setframerate(rate)
        if width == 2:
            data = (samples * (2**15 - 1)).astype("<i2")
            frames = np.repeat(data, channels).tobytes()
        elif width == 3:
            ints = (samples * (2**23 - 1)).astype("<i4")
            b = ints.view(np.uint8).reshape(-1, 4)[:, :3]
            frames = np.repeat(b, channels, axis=0).tobytes()
        else:
            raise ValueError(width)
        w.writeframes(frames)


def write_samples_wav(path, samples, rate=48000):
    """Write a mono float array in [-1, 1] as 16-bit PCM."""
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes((np.clip(samples, -1, 1) * (2**15 - 1)).astype("<i2").tobytes())


class TestAnalysis(unittest.TestCase):
    def test_boundary_tone_is_not_lost_between_bands(self):
        # 1412 Hz fell in the gap between the old (rounded-label) 1250 and 1600
        # bands and read as near-silence. With edges from exact centers it must
        # land in a real band at full strength.
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "boundary.wav")
            write_sine_wav(path, 1412.109375, amplitude=1.0)  # bin-centered at 48k/8192
            stats = analyze_wav(path)
            home = next(b for b in stats.spectrum_third_octave if b["freq_hz"] == 1250)
            self.assertGreater(home["level_db"], -6.0)

    def test_transient_in_final_partial_segment_is_captured(self):
        # A burst living only in the last <hop samples was dropped by the old
        # floor-division segment count while still counting toward peak/RMS.
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "tail.wav")
            rate = 48000
            head = np.zeros(8192)
            t = np.arange(4095) / rate
            tail = np.sin(2 * np.pi * 1000 * t)
            write_samples_wav(path, np.concatenate([head, tail]), rate=rate)
            stats = analyze_wav(path)
            band = next(b for b in stats.spectrum_third_octave if b["freq_hz"] == 1000)
            self.assertGreater(band["level_db"], -40.0)


    def test_full_scale_sine_reads_minus_3dbfs_in_its_band(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "sine.wav")
            write_sine_wav(path, 1000, amplitude=1.0)
            stats = analyze_wav(path)
            band = next(b for b in stats.spectrum_third_octave if b["freq_hz"] == 1000)
            self.assertAlmostEqual(band["level_db"], -3.0, delta=0.5)
            # Off-frequency bands should be way down.
            far = next(b for b in stats.spectrum_third_octave if b["freq_hz"] == 100)
            self.assertLess(far["level_db"], -60)

    def test_sine_crest_factor_is_3db(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "sine.wav")
            write_sine_wav(path, 440, amplitude=0.5)
            stats = analyze_wav(path)
            self.assertAlmostEqual(stats.crest_factor_db, 3.01, delta=0.1)
            self.assertAlmostEqual(stats.sample_peak_db, 20 * math.log10(0.5), delta=0.1)

    def test_24bit_and_stereo_mono_sum(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "s24.wav")
            write_sine_wav(path, 200, amplitude=0.8, width=3, channels=2)
            samples, rate, channels = read_wav_mono(path)
            self.assertEqual(rate, 48000)
            self.assertEqual(channels, 2)
            self.assertAlmostEqual(float(np.max(np.abs(samples))), 0.8, delta=0.01)

    def test_duration_and_band_count(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "sine.wav")
            write_sine_wav(path, 1000, seconds=2.0)
            stats = analyze_wav(path)
            self.assertAlmostEqual(stats.duration_seconds, 2.0, delta=0.01)
            # 48kHz Nyquist is 24k: all 31 bands fit.
            self.assertEqual(len(stats.spectrum_third_octave), 31)


if __name__ == "__main__":
    unittest.main()
