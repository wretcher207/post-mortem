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

from postmortem.analysis import analyze_wav, read_wav  # noqa: E402


def write_raw_wav(path, samples_2d, rate=48000, audio_format=1, bits=16):
    """Write (frames, channels) float samples as a RIFF/WAVE file with an
    explicit format tag and bit depth. Lets tests exercise 32-bit int PCM and
    IEEE float, which the stdlib wave module can't produce."""
    frames, channels = samples_2d.shape
    if audio_format == 1 and bits == 16:
        payload = (np.clip(samples_2d, -1, 1) * (2**15 - 1)).astype("<i2").tobytes()
    elif audio_format == 1 and bits == 32:
        payload = (np.clip(samples_2d, -1, 1) * (2**31 - 1)).astype("<i4").tobytes()
    elif audio_format == 3 and bits == 32:
        payload = samples_2d.astype("<f4").tobytes()
    else:
        raise ValueError(f"unsupported test format {audio_format}/{bits}")
    block_align = channels * bits // 8
    byte_rate = rate * block_align
    fmt = struct.pack("<HHIIHH", audio_format, channels, rate, byte_rate, block_align, bits)
    data_size = len(payload)
    with open(path, "wb") as f:
        f.write(b"RIFF")
        f.write(struct.pack("<I", 4 + (8 + len(fmt)) + (8 + data_size)))
        f.write(b"WAVE")
        f.write(b"fmt ")
        f.write(struct.pack("<I", len(fmt)))
        f.write(fmt)
        f.write(b"data")
        f.write(struct.pack("<I", data_size))
        f.write(payload)


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

    def test_24bit_and_stereo_read(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "s24.wav")
            write_sine_wav(path, 200, amplitude=0.8, width=3, channels=2)
            samples, rate, channels = read_wav(path)
            self.assertEqual(rate, 48000)
            self.assertEqual(channels, 2)
            self.assertEqual(samples.shape[1], 2)  # channels kept separate
            self.assertAlmostEqual(float(np.max(np.abs(samples))), 0.8, delta=0.01)

    def test_antiphase_stereo_does_not_cancel_to_silence(self):
        # L = +sine, R = -sine. Amplitude-averaging the channels would report
        # digital silence for a full-level stem (H2). Power-domain combine must
        # report its true level.
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "antiphase.wav")
            rate = 48000
            t = np.arange(int(2.0 * rate)) / rate
            sine = np.sin(2 * np.pi * 1000 * t)
            stereo = np.stack([sine, -sine], axis=1)
            write_raw_wav(path, stereo, rate=rate, audio_format=1, bits=16)
            stats = analyze_wav(path)
            self.assertGreater(stats.sample_peak_db, -1.0)
            self.assertGreater(stats.rms_db, -6.0)
            band = next(b for b in stats.spectrum_third_octave if b["freq_hz"] == 1000)
            self.assertAlmostEqual(band["level_db"], -3.0, delta=0.7)

    def test_32bit_int_pcm_not_misread_as_float(self):
        # A 32-bit int PCM DC-ish signal used to be misclassified as IEEE float
        # by the magnitude heuristic and decoded to near-silence (M3). Reading
        # the fmt tag must decode it at the right scale.
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "pcm32.wav")
            samples = np.full((48000, 1), 0.25)
            write_raw_wav(path, samples, audio_format=1, bits=32)
            got, _, _ = read_wav(path)
            self.assertAlmostEqual(float(np.mean(got)), 0.25, delta=0.001)

    def test_32bit_float_wav_reads(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "f32.wav")
            t = np.arange(48000) / 48000
            samples = (0.5 * np.sin(2 * np.pi * 1000 * t)).reshape(-1, 1)
            write_raw_wav(path, samples, audio_format=3, bits=32)
            got, _, _ = read_wav(path)
            self.assertAlmostEqual(float(np.max(np.abs(got))), 0.5, delta=0.01)

    def test_mono_has_no_stereo_block(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "mono.wav")
            write_sine_wav(path, 1000, channels=1)
            stats = analyze_wav(path)
            self.assertIsNone(stats.stereo)

    def test_dual_mono_stereo_block(self):
        # L == R: correlation +1, side channel is digital silence, balance 0.
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "dualmono.wav")
            rate = 48000
            t = np.arange(int(2.0 * rate)) / rate
            sine = 0.5 * np.sin(2 * np.pi * 1000 * t)
            write_raw_wav(path, np.stack([sine, sine], axis=1), rate=rate)
            stats = analyze_wav(path)
            self.assertAlmostEqual(stats.stereo["correlation"], 1.0, delta=0.01)
            self.assertLess(stats.stereo["side_rms_db"], -90)
            self.assertAlmostEqual(stats.stereo["balance_db"], 0.0, delta=0.05)

    def test_antiphase_stereo_block(self):
        # L == -R: correlation -1, mid channel cancels to silence.
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "anti.wav")
            rate = 48000
            t = np.arange(int(2.0 * rate)) / rate
            sine = 0.5 * np.sin(2 * np.pi * 1000 * t)
            write_raw_wav(path, np.stack([sine, -sine], axis=1), rate=rate)
            stats = analyze_wav(path)
            self.assertAlmostEqual(stats.stereo["correlation"], -1.0, delta=0.01)
            self.assertLess(stats.stereo["mid_rms_db"], -90)

    def test_silent_channel_correlation_is_none(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "onesided.wav")
            rate = 48000
            t = np.arange(int(1.0 * rate)) / rate
            sine = 0.5 * np.sin(2 * np.pi * 1000 * t)
            write_raw_wav(path, np.stack([sine, np.zeros_like(sine)], axis=1), rate=rate)
            stats = analyze_wav(path)
            self.assertIsNone(stats.stereo["correlation"])
            self.assertIsNone(stats.stereo["balance_db"])

    def test_silence_fraction_half(self):
        # 1 s of silence + 1 s of sine reads ~0.5.
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "half.wav")
            rate = 48000
            t = np.arange(rate) / rate
            sine = 0.5 * np.sin(2 * np.pi * 1000 * t)
            write_samples_wav(path, np.concatenate([np.zeros(rate), sine]), rate=rate)
            stats = analyze_wav(path)
            self.assertAlmostEqual(stats.silence_fraction, 0.5, delta=0.05)

    def test_silence_fraction_zero_for_steady_tone(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "tone.wav")
            write_sine_wav(path, 440, amplitude=0.5)
            stats = analyze_wav(path)
            self.assertEqual(stats.silence_fraction, 0.0)

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
