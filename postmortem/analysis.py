"""WAV -> spectrum + dynamics stats. Pure numpy, no REAPER required.

Computes a channel-combined 1/3-octave spectrum (31 bands, 20 Hz - 20 kHz),
sample peak, RMS, and crest factor. Channels are combined in the POWER domain
(not amplitude-averaged), so an out-of-phase stereo stem doesn't cancel to
silence. LUFS comes from REAPER's RENDER_STATS, not from here (see bridge.py);
if it's missing the payload carries None.
"""

import struct
from dataclasses import dataclass, field

import numpy as np

WAVE_FORMAT_PCM = 0x0001
WAVE_FORMAT_IEEE_FLOAT = 0x0003
WAVE_FORMAT_EXTENSIBLE = 0xFFFE

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


def _parse_wav(path):
    """Minimal RIFF/WAVE reader. Returns (audio_format, channels, rate,
    bits_per_sample, raw_data_bytes). Reads the fmt chunk's format tag directly
    instead of guessing PCM-vs-float from sample magnitude: 32-bit int PCM and
    32-bit IEEE float both report width 4, and the magnitude heuristic
    misclassified valid int data (e.g. a DC value) as float."""
    with open(path, "rb") as f:
        header = f.read(12)
        if len(header) < 12 or header[:4] != b"RIFF" or header[8:12] != b"WAVE":
            raise ValueError(f"not a RIFF/WAVE file: {path}")
        fmt = None
        data = None
        while True:
            chunk_header = f.read(8)
            if len(chunk_header) < 8:
                break
            chunk_id, size = struct.unpack("<4sI", chunk_header)
            body = f.read(size)
            if size % 2 == 1:
                f.read(1)  # chunks are word-aligned; skip the pad byte
            if chunk_id == b"fmt ":
                fmt = body
            elif chunk_id == b"data":
                data = body
    if fmt is None or data is None:
        raise ValueError(f"WAV missing fmt/data chunk: {path}")
    audio_format, channels, rate, _byte_rate, _block_align, bits = struct.unpack(
        "<HHIIHH", fmt[:16]
    )
    if audio_format == WAVE_FORMAT_EXTENSIBLE and len(fmt) >= 26:
        # The real format is the first 2 bytes of the SubFormat GUID.
        audio_format = struct.unpack("<H", fmt[24:26])[0]
    return audio_format, channels, rate, bits, data


def read_wav(path):
    """Read a WAV as a float64 array shaped (frames, channels) in [-1, 1].
    Returns (samples, sample_rate, channels). Supports 16/24/32-bit int PCM
    and 32/64-bit IEEE float. Channels are kept separate (not summed) so
    downstream measurement can combine them without phase cancellation."""
    audio_format, channels, rate, bits, raw = _parse_wav(path)

    if audio_format == WAVE_FORMAT_PCM:
        if bits == 16:
            data = np.frombuffer(raw, dtype="<i2").astype(np.float64) / 2**15
        elif bits == 24:
            b = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3)
            padded = np.zeros((b.shape[0], 4), dtype=np.uint8)
            padded[:, 1:] = b
            data = (padded.view("<i4").ravel() >> 8).astype(np.float64) / 2**23
        elif bits == 32:
            data = np.frombuffer(raw, dtype="<i4").astype(np.float64) / 2**31
        else:
            raise ValueError(f"unsupported PCM bit depth: {bits}-bit")
    elif audio_format == WAVE_FORMAT_IEEE_FLOAT:
        if bits == 32:
            data = np.frombuffer(raw, dtype="<f4").astype(np.float64)
        elif bits == 64:
            data = np.frombuffer(raw, dtype="<f8").astype(np.float64)
        else:
            raise ValueError(f"unsupported IEEE-float bit depth: {bits}-bit")
    else:
        raise ValueError(f"unsupported WAV format tag: 0x{audio_format:04X}")

    channels = max(1, channels)
    return data.reshape(-1, channels), rate, channels


def _db(x):
    if x <= 0:
        return SILENCE_FLOOR_DB
    return float(20.0 * np.log10(x))


def third_octave_spectrum(samples, rate):
    """Averaged power spectrum in 1/3-octave bands, in dBFS.

    samples is (frames, channels). Welch-style averaging: 8192-sample Hann
    segments, 50% overlap. Per-channel power spectra are averaged in the POWER
    domain (so anti-phase channels don't cancel). Band edges at
    exact_center / 2^(1/6) and exact_center * 2^(1/6); a band whose upper edge
    exceeds Nyquist is omitted (it would be incomplete and not comparable).
    """
    samples = np.asarray(samples)
    if samples.ndim == 1:
        samples = samples.reshape(-1, 1)
    frames, n_ch = samples.shape

    seg = 8192
    if frames < seg:
        seg = max(256, 2 ** int(np.log2(max(frames, 256))))
    hop = seg // 2
    window = np.hanning(seg)
    win_power = np.sum(window**2)

    # ceil, not floor: floor division drops the final partial hop, so a
    # transient living only in the last <hop samples was silently excluded
    # from the spectrum while still counting toward peak/RMS. The short tail
    # segment is zero-padded below.
    n_segments = max(1, int(np.ceil((frames - seg) / hop)) + 1)
    power = np.zeros(seg // 2 + 1)
    for ch in range(n_ch):
        col = samples[:, ch]
        ch_power = np.zeros(seg // 2 + 1)
        for i in range(n_segments):
            chunk = col[i * hop : i * hop + seg]
            if len(chunk) < seg:
                chunk = np.pad(chunk, (0, seg - len(chunk)))
            spec = np.fft.rfft(chunk * window)
            ch_power += np.abs(spec) ** 2
        power += ch_power / n_segments
    power /= n_ch  # power-domain channel average: no cancellation

    # Calibration (Parseval): sum over all bins of |X|^2 = seg * sum(x_w^2)
    # ~= seg * RMS^2 * sum(w^2), and the positive-frequency half carries half
    # of it, so band RMS = sqrt(2 * sum_band(|X|^2) / (seg * sum(w^2))).
    # A full-scale sine reads -3.01 dBFS in its band. (Using sum(w) instead
    # of sum(w^2) here overshoots by the Hann coherent gain, +1.76 dB.)
    freqs = np.fft.rfftfreq(seg, d=1.0 / rate)
    edge = 2 ** (1 / 6)
    nyquist = rate / 2
    i_1k = THIRD_OCTAVE_CENTERS_HZ.index(1000)
    bands = []
    for i, label in enumerate(THIRD_OCTAVE_CENTERS_HZ):
        # Band EDGES must come from the EXACT geometric center (base-2, anchored
        # at 1 kHz), not the rounded ISO label. The rounded labels aren't a clean
        # 2^(1/3) apart, so edges built from them leave gaps and overlaps between
        # adjacent bands (a 1412 Hz tone fell between the 1250 and 1600 bands and
        # read as near-silence). The rounded value stays only as the display label.
        center = 1000.0 * 2.0 ** ((i - i_1k) / 3.0)
        if center * edge > nyquist:
            break  # incomplete band above Nyquist; ascending, so we're done
        mask = (freqs >= center / edge) & (freqs < center * edge)
        band_rms = np.sqrt(2.0 * power[mask].sum() / (seg * win_power))
        bands.append({"freq_hz": label, "level_db": round(_db(band_rms), 1)})
    return bands


def analyze_wav(path):
    samples, rate, channels = read_wav(path)  # (frames, channels)
    if samples.shape[0] == 0:
        raise ValueError(f"empty WAV: {path}")
    # Peak across all channels; RMS/crest from combined channel energy (mean
    # square over every sample of every channel), so an anti-phase pair reads
    # its true level instead of cancelling to silence.
    peak = float(np.max(np.abs(samples)))
    rms = float(np.sqrt(np.mean(samples**2)))
    peak_db, rms_db = _db(peak), _db(rms)
    return TrackStats(
        duration_seconds=round(samples.shape[0] / rate, 2),
        sample_rate=rate,
        channels=channels,
        sample_peak_db=round(peak_db, 2),
        rms_db=round(rms_db, 2),
        crest_factor_db=round(peak_db - rms_db, 2),
        spectrum_third_octave=third_octave_spectrum(samples, rate),
    )
