"""WAV -> spectrum + dynamics stats. Pure numpy, no REAPER required.

v1 computes a mono-sum 1/3-octave spectrum (31 bands, 20 Hz - 20 kHz),
sample peak, RMS, and crest factor. LUFS comes from REAPER's RENDER_STATS,
not from here (see bridge.py); if it's missing the payload carries None.
"""

import wave
from dataclasses import dataclass, field

import numpy as np

# ISO 266 preferred 1/3-octave band centers, 20 Hz to 20 kHz.
THIRD_OCTAVE_CENTERS_HZ = [
    20, 25, 31.5, 40, 50, 63, 80, 100, 125, 160,
    200, 250, 315, 400, 500, 630, 800, 1000, 1250, 1600,
    2000, 2500, 3150, 4000, 5000, 6300, 8000, 10000, 12500, 16000, 20000,
]

SILENCE_FLOOR_DB = -120.0


@dataclass
class TrackStats:
    duration_seconds: float
    sample_rate: int
    channels: int
    sample_peak_db: float
    rms_db: float
    crest_factor_db: float
    spectrum_third_octave: list = field(default_factory=list)


def read_wav_mono(path):
    """Read a PCM WAV (16/24/32-bit int or 32-bit float) as a mono-sum
    float64 array in [-1, 1]. Returns (samples, sample_rate, channels).

    Mono sum, not per-channel: v1 trade-off documented in the spec
    (hard-panned dual-mono sources read ~6 dB low; fine for diagnosis).
    """
    with wave.open(path, "rb") as w:
        channels = w.getnchannels()
        rate = w.getframerate()
        width = w.getsampwidth()
        raw = w.readframes(w.getnframes())

    if width == 2:
        data = np.frombuffer(raw, dtype="<i2").astype(np.float64) / 2**15
    elif width == 3:
        # 24-bit: pad each little-endian 3-byte sample to 4 bytes, sign via shift.
        b = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3)
        padded = np.zeros((b.shape[0], 4), dtype=np.uint8)
        padded[:, 1:] = b
        data = (padded.view("<i4").ravel() >> 8).astype(np.float64) / 2**23
    elif width == 4:
        # WAVE_FORMAT_IEEE_FLOAT also reports sampwidth 4; REAPER's default
        # 32-bit sink is int PCM. Heuristic: float WAVs stay within [-16, 16]
        # when viewed as float; int data viewed as float is astronomically
        # large or subnormal.
        as_float = np.frombuffer(raw, dtype="<f4")
        finite = np.isfinite(as_float)
        if finite.all() and (np.abs(as_float) < 16.0).all():
            data = as_float.astype(np.float64)
        else:
            data = np.frombuffer(raw, dtype="<i4").astype(np.float64) / 2**31
    else:
        raise ValueError(f"unsupported WAV sample width: {width * 8}-bit")

    if channels > 1:
        data = data.reshape(-1, channels).mean(axis=1)
    return data, rate, channels


def _db(x):
    if x <= 0:
        return SILENCE_FLOOR_DB
    return float(20.0 * np.log10(x))


def third_octave_spectrum(samples, rate):
    """Averaged power spectrum summed into 1/3-octave bands, in dBFS.

    Welch-style averaging: 8192-sample Hann segments, 50% overlap. Band edges
    at center / 2^(1/6) and center * 2^(1/6). Bands above Nyquist are omitted.
    """
    seg = 8192
    if len(samples) < seg:
        seg = max(256, 2 ** int(np.log2(max(len(samples), 256))))
    hop = seg // 2
    window = np.hanning(seg)
    win_power = np.sum(window**2)

    # ceil, not floor: floor division drops the final partial hop, so a
    # transient living only in the last <hop samples was silently excluded
    # from the spectrum while still counting toward peak/RMS. The short tail
    # segment is zero-padded below.
    n_segments = max(1, int(np.ceil((len(samples) - seg) / hop)) + 1)
    power = np.zeros(seg // 2 + 1)
    for i in range(n_segments):
        chunk = samples[i * hop : i * hop + seg]
        if len(chunk) < seg:
            chunk = np.pad(chunk, (0, seg - len(chunk)))
        spec = np.fft.rfft(chunk * window)
        power += np.abs(spec) ** 2
    power /= n_segments

    # Calibration (Parseval): sum over all bins of |X|^2 = seg * sum(x_w^2)
    # ~= seg * RMS^2 * sum(w^2), and the positive-frequency half carries half
    # of it, so band RMS = sqrt(2 * sum_band(|X|^2) / (seg * sum(w^2))).
    # A full-scale sine reads -3.01 dBFS in its band. (Using sum(w) instead
    # of sum(w^2) here overshoots by the Hann coherent gain, +1.76 dB.)
    freqs = np.fft.rfftfreq(seg, d=1.0 / rate)
    edge = 2 ** (1 / 6)
    i_1k = THIRD_OCTAVE_CENTERS_HZ.index(1000)
    bands = []
    for i, label in enumerate(THIRD_OCTAVE_CENTERS_HZ):
        # Band EDGES must come from the EXACT geometric center (base-2, anchored
        # at 1 kHz), not the rounded ISO label. The rounded labels aren't a clean
        # 2^(1/3) apart, so edges built from them leave gaps and overlaps between
        # adjacent bands (a 1412 Hz tone fell between the 1250 and 1600 bands and
        # read as near-silence). The rounded value stays only as the display label.
        center = 1000.0 * 2.0 ** ((i - i_1k) / 3.0)
        if center > rate / 2:
            break
        mask = (freqs >= center / edge) & (freqs < center * edge)
        band_rms = np.sqrt(2.0 * power[mask].sum() / (seg * win_power))
        bands.append({"freq_hz": label, "level_db": round(_db(band_rms), 1)})
    return bands


def analyze_wav(path):
    samples, rate, channels = read_wav_mono(path)
    if len(samples) == 0:
        raise ValueError(f"empty WAV: {path}")
    peak = float(np.max(np.abs(samples)))
    rms = float(np.sqrt(np.mean(samples**2)))
    peak_db, rms_db = _db(peak), _db(rms)
    return TrackStats(
        duration_seconds=round(len(samples) / rate, 2),
        sample_rate=rate,
        channels=channels,
        sample_peak_db=round(peak_db, 2),
        rms_db=round(rms_db, 2),
        crest_factor_db=round(peak_db - rms_db, 2),
        spectrum_third_octave=third_octave_spectrum(samples, rate),
    )
