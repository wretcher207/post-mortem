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
    # Fraction of the capture (100 ms blocks) that is effectively silent. High
    # values mean every other number here is diluted by dead air.
    silence_fraction: float = 0.0
    # Stereo image block (phase correlation, mid/side, L/R balance); None for mono.
    stereo: dict = None


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


# A band counts as real "energy" for a track when it sits within this many dB of
# that track's own loudest band. Keeps near-silent bands out of the masking test.
MASKING_PROMINENCE_DB = 12.0
# A track whose loudest band is below this is treated as effectively silent and
# excluded from masking entirely (a near-empty capture can't mask anything).
# Same -60 dBFS idea as cli.NEAR_SILENT_RMS_DB but a different quantity
# (loudest 1/3-octave band vs broadband RMS) — tune them together.
MASKING_ABSOLUTE_FLOOR_DB = -60.0


def _significant_bands(bands, prominence_db, floor_db):
    """Return {freq_hz: level_db} for bands with real energy: within prominence_db
    of the track's loudest band AND above the absolute floor. Empty dict if the
    whole track is below the floor (effectively silent)."""
    levels = {b["freq_hz"]: b["level_db"] for b in bands}
    if not levels:
        return {}
    top = max(levels.values())
    if top < floor_db:
        return {}
    threshold = max(top - prominence_db, floor_db)
    return {f: lvl for f, lvl in levels.items() if lvl >= threshold}


def masking_overlap(
    spectra,
    prominence_db=MASKING_PROMINENCE_DB,
    floor_db=MASKING_ABSOLUTE_FLOOR_DB,
):
    """Find candidate frequency masking between tracks from their 1/3-octave spectra.

    spectra: dict {track_name: [ {freq_hz, level_db}, ... ]} (the
    spectrum_third_octave lists from analyze_wav). For every unordered pair of
    tracks, a band is CONTESTED when both tracks have real energy in it (see
    _significant_bands). Contested bands are candidate masking; the louder track
    is the likely masker, the quieter one is at risk. This is deliberately coarse:
    1/3-octave bands flag a contested region, they do not prove an audible
    collision, and a narrow clash can hide inside a wide band.

    Returns {"pairs": [...], "method_note": str}. Each pair carries every contested
    band (freq, both levels, signed diff, which track is louder) sorted by
    frequency, plus a short human summary.
    """
    names = list(spectra)
    sig = {n: _significant_bands(spectra[n], prominence_db, floor_db) for n in names}
    pairs = []
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            a, b = names[i], names[j]
            shared = sorted(set(sig[a]) & set(sig[b]))
            contested = []
            for f in shared:
                diff = round(sig[a][f] - sig[b][f], 1)
                contested.append(
                    {
                        "freq_hz": f,
                        "a_level_db": sig[a][f],
                        "b_level_db": sig[b][f],
                        "diff_db": diff,          # a minus b; positive => a louder
                        "louder": a if diff >= 0 else b,
                    }
                )
            pairs.append(
                {
                    "a": a,
                    "b": b,
                    "contested_bands": contested,
                    "summary": _masking_summary(a, b, contested),
                }
            )
    return {
        "pairs": pairs,
        "method_note": (
            "A band is contested when both tracks have energy within "
            f"{prominence_db:.0f} dB of their own loudest band. Contested bands "
            "flag CANDIDATE masking in a shared region, not a proven audible "
            "collision; 1/3-octave bands are coarse, so a narrow clash can hide "
            "inside a wide band and exact collision frequencies are not resolved."
        ),
    }


def _masking_summary(a, b, contested):
    if not contested:
        return f"No shared bands between '{a}' and '{b}' (little spectral overlap)."
    lo = contested[0]["freq_hz"]
    hi = contested[-1]["freq_hz"]
    span = f"{lo} Hz" if lo == hi else f"{lo}-{hi} Hz"
    return f"{len(contested)} contested band(s) between '{a}' and '{b}', spanning {span}."


# A 100 ms block below this combined-channel RMS counts as silence.
SILENCE_BLOCK_FLOOR_DB = -70.0
SILENCE_BLOCK_SECONDS = 0.1


def silence_fraction(samples, rate, block_seconds=SILENCE_BLOCK_SECONDS,
                     floor_db=SILENCE_BLOCK_FLOOR_DB):
    """Fraction of the capture that is effectively silent: the share of
    block_seconds-long blocks whose combined-channel RMS sits below floor_db.
    Distinguishes 'the track is quiet' from 'the cursor was parked in a gap'."""
    frames = samples.shape[0]
    block = max(1, int(rate * block_seconds))
    n_blocks = max(1, int(np.ceil(frames / block)))
    floor_amp = 10.0 ** (floor_db / 20.0)
    # Pad the tail block with zeros and compare mean-square per block against
    # the squared floor, all vectorized. Zero-padding biases the tail block
    # quieter, which errs toward calling it silent — acceptable for a <100 ms
    # remainder.
    padded = np.zeros((n_blocks * block, samples.shape[1]))
    padded[:frames] = samples
    block_ms = np.mean(padded.reshape(n_blocks, block, -1) ** 2, axis=(1, 2))
    silent = int(np.count_nonzero(block_ms < floor_amp**2))
    return round(silent / n_blocks, 3)


def stereo_stats(samples):
    """Stereo image block, or None for mono. Uses the first two channels.

    correlation is the zero-lag normalized cross-correlation (the convention a
    phase-correlation meter uses, no mean subtraction): +1.0 dual-mono, ~0
    decorrelated, -1.0 anti-phase. None when either channel is digital silence
    (the ratio is undefined). Mid = (L+R)/2, side = (L-R)/2; balance_db is the
    L-minus-R RMS difference (positive = left-heavy)."""
    if samples.shape[1] < 2:
        return None
    left = samples[:, 0]
    right = samples[:, 1]
    l_energy = float(np.sum(left**2))
    r_energy = float(np.sum(right**2))
    if l_energy > 0.0 and r_energy > 0.0:
        correlation = round(float(np.sum(left * right)) / np.sqrt(l_energy * r_energy), 3)
    else:
        correlation = None
    mid_rms = float(np.sqrt(np.mean(((left + right) / 2.0) ** 2)))
    side_rms = float(np.sqrt(np.mean(((left - right) / 2.0) ** 2)))
    l_rms = np.sqrt(l_energy / len(left))
    r_rms = np.sqrt(r_energy / len(right))
    balance_db = round(_db(l_rms) - _db(r_rms), 2) if l_energy > 0 and r_energy > 0 else None
    return {
        "correlation": correlation,
        "mid_rms_db": round(_db(mid_rms), 2),
        "side_rms_db": round(_db(side_rms), 2),
        "balance_db": balance_db,
        "note": (
            "correlation is zero-lag L/R phase correlation: +1 dual-mono, ~0 "
            "decorrelated, -1 anti-phase (mono-cancel risk). balance_db is "
            "L minus R RMS."
        ),
    }


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
        silence_fraction=silence_fraction(samples, rate),
        stereo=stereo_stats(samples),
    )
