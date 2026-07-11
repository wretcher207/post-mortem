---
title: "Track-Context AI Mix Analysis"
tags: [wiki, audio, project-design, ai]
type: note
updated: 2026-07-02
---

# Post Mortem — Track-Context AI Mix Analysis — Build Spec

**Tier:** REFERENCE. Load when building Post Mortem or discussing AI-driven
diagnosis inside REAPER.

**Product name:** Post Mortem (chosen 2026-07-02). A Dead Pixel Design release.
Repo: `~/workspace/audio/post-mortem` (github.com/wretcher207/post-mortem).
The build-authoritative copy of this spec lives in that repo as `docs/SPEC.md`;
this wiki page is the design-history copy.

**Status:** build started 2026-07-02. Originally validated against the Reaper
Daemon bridge v3.3.0 source; bridge is now v3.7.0 (`~/workspace/audio/reaper-bridge/`).

## What it is

A tool that reads a selected track's state (FX chain, parameter values, routing,
volume, pan) plus a post-FX audio snapshot, sends the combined payload to an LLM,
and returns a one-paragraph diagnostic: tonal balance problems (mud, harshness,
missing weight), dynamic range collapse, FX-chain sanity issues, and a concrete
proposed move with specific values.

**Scope honesty:** true frequency *masking* is a between-tracks phenomenon. One
track's spectrum cannot prove masking; claiming it would make the model bluff.
v1 diagnoses what single-track data can support (tonal balance, dynamics, chain
sanity). Masking detection needs cross-track spectra.

**Masking shipped (build #2, 2026-07-04):** cross-track masking is now
implemented in the same CLI. Passing two or more track names captures each stem,
computes the 1/3-octave overlap, and diagnoses masking with a SIBLING prompt
contract (`MASKING_SYSTEM_PROMPT`) only after every capture is verified isolated.
This is a deliberate, gated relaxation of the single-track hedge, not a
weakening: it still refuses to over-claim on coarse bands (contested band =
candidate collision region, not proof). Single-track text and structured output
share `_SINGLE_TRACK_HONESTY_CONTRACT`; structured Track Check adds only the
schema/proposal rules in `TRACK_CHECK_SYSTEM_PROMPT`. See "Cross-track masking
(build #2)" below.

**Capture-validity gate:** model diagnosis requires the daemon result to say
`capture_scope: "isolated_track"` and `isolation_verified: true`. A `full_mix`,
`master_output`, or missing-provenance result is not per-track evidence and is
refused before analysis or a model call. `--payload-only` can inspect raw output
without relaxing that boundary.

The wedge is the **read-and-diagnose** side of AI in REAPER. Existing projects
(DAWZY, reaper-reapy-mcp, the Reaper MCP server) all point the other direction:
telling the DAW what to do. Nobody is reading the mix state and proposing
targeted moves based on what's actually happening in the signal chain. That gap
was named directly in the community: "there isn't really anything yet that will
analyze an individual track and apply the processing the track needs
independently of any other input" (HomeRecording.com, cited unverified).
[INFERENCE] the thread exists; the exact wording is from web search, not a
first-hand read of the forum.

## Why it's a fit

The Reaper Daemon bridge already does 70% of the capture work:

- `scan_fx` / `get_fx_parameters` enumerate every FX on a track with full
  parameter names, normalized values, and formatted display values (the
  kHz-aware parser, the binary-search formatted-value matcher). See
  `command_scan_fx` and `command_get_fx_parameters` in
  `reaper_agent_bridge.lua` (function names, not line numbers; lines rot).
- `get_context` returns project name, tempo, cursor position, transport state,
  time selection, all tracks, markers, regions.
- The file-based inbox/outbox architecture means the agent can send a command,
  poll for the result, and forward it to an LLM without any new transport layer.
- `reaperd.py` already handles the client side: `send_type` sends a command by
  type, polls the outbox, returns parsed JSON.

The missing piece is audio capture. The bridge has no spectrum or waveform read.
The existing `render` command (`command_render` in the bridge source) is a
**project-level** render that uses REAPER's last-saved render settings and calls
action 42230 (File: Render project). It is NOT a per-track stem renderer. This
spec defines a new command for that.

## The hard problem: post-FX audio capture

REAPER's ReaScript audio accessor API has a fundamental limitation that shapes
the entire design.

`CreateTrackAudioAccessor(track)` + `GetAudioAccessorSamples(accessor, ...)`
returns samples **immediately pre-FX**, as stated in the official ReaScript help
(reaper.fm/sdk/reascript/reascripthelp.html): "Samples are extracted immediately
pre-FX, and returned interleaved." There is no parameter, flag, or alternative
accessor function that switches to post-FX or post-fader capture. This is by
design, not a bug.

**What does NOT exist:** `NF_AnalyzeMode` is not a real ReaScript function. It
appeared in an earlier prototype sketch and was fabricated. Do not reference it.

**What does NOT exist:** there is no ReaScript function that returns a track's
post-FX samples directly, in real time, without rendering.

### The three viable capture paths

| Path | How | Latency | Invasiveness | Fidelity |
|---|---|---|---|---|
| **Temp stem render** | Select target track, render with source = stems (selected tracks), read file back | 2-10s | Minimal (track selection, restored after) | Post-track-FX, pre-master (verified) |
| **JSFX analysis tap** | Insert a JSFX analyzer as last FX on the track, read its data via `TrackFX_GetNamedConfigParm` | Real-time | Adds an FX slot | Post-FX, pre-master |
| **Route + record** | Create a hidden receive track, route the source track's post-FX output to it, arm and record | 1-5s | Creates a track | Post-FX, pre-master |

**Decision: temp stem render for v1, using REAPER's native stems render source,
NOT solo.** Set `RENDER_SETTINGS` to `2` (stems only, renders the selected
tracks) and select just the target track. Verified in the official ReaScript
docs: `RENDER_SETTINGS : (&(1|2)==0)=master mix, &1=stems+master mix, &2=stems
only, ... &128=selected tracks via master`. The existence of the separate
`&128` "via master" flag confirms plain `&2` stems do NOT pass through the
master bus.

This kills two problems the earlier solo-based draft had:

1. **No solo dance.** No capturing and restoring the solo state of every track,
   no solo-state corruption risk if the bridge crashes mid-capture. Only the
   track *selection* needs save/restore, which is far lighter.
2. **No master-bus coloration.** A soloed-track render routes through the master
   bus (limiter, EQ, dither get printed). A stems render is the track's
   post-fader output before the master chain, which is exactly what a
   track-level diagnosis wants. The old "temporarily bypass master FX" v1.5
   workaround is unnecessary.

The trade-off is latency: a 10-second capture at 48kHz/24-bit renders in a few
seconds. Acceptable for a diagnostic tool, not for real-time monitoring.

**Parent-bus caveat (the remaining coloration source):** a stems render of a
child track still includes nothing from its parent bus FX, because the stem is
tapped at the track's own output. If the sound the user hears is shaped heavily
by a parent bus (e.g. Guitar Bus compression), the diagnosis sees the track dry
of that. The payload includes `parent_track` from `get_track_routing` so the
model knows a parent chain exists; analyzing it is v2.

## Architecture

```
┌─────────────┐     JSON command      ┌──────────────┐    ReaScript     ┌──────────┐
│  reaperd.py │ ───────────────────>  │  Lua bridge  │ ──────────────>  │  REAPER  │
│  (client)   │ <───── JSON result ── │ (in REAPER)  │ <──────────────  │          │
└──────┬──────┘                      └──────────────┘                  └──────────┘
       │
       │  scan_fx output + audio file path
       ▼
┌──────────────┐     structured prompt + payload      ┌─────────┐
│  LLM call    │ ──────────────────────────────────>   │  Model  │
│  (reaperd.py │ <───── diagnosis (JSON + prose) ─────  │         │
│   or agent)  │                                       └─────────┘
└──────┬───────┘
       │
       │  ShowInConsole() or ReaImGui panel
       ▼
┌──────────────┐
│  REAPER UI   │
└──────────────┘
```

The flow stays file-based, matching the bridge's existing architecture. No
sockets, no HTTP server inside REAPER (unlike The Stash, which needs a
localhost web app; this tool's UI is a console message or a small ReaImGui
panel, no web surface).

## New bridge command: `capture_track_audio`

This is the one new bridge command the spec requires. Everything else reuses
existing commands.

### Command

```json
{
  "id": "agent-2026-07-02T14-30-00-ab12",
  "version": 3,
  "type": "capture_track_audio",
  "created_by": "agent",
  "created_at": "2026-07-02T14:30:00-04:00",
  "payload": {
    "target_track_name": "Rhythm L",
    "duration_seconds": 10,
    "start_position": { "type": "cursor" },
    "output_file": "/tmp/reaper-diagnosis/rhythm-l-stem-20260702T143000.wav",
    "format": "wav",
    "sample_rate": 48000,
    "bit_depth": 24
  }
}
```

### What it does (step by step)

1. Resolve the target track (reuse the bridge's `find_track` selector: GUID,
   name, or selected).
2. Capture the current track selection (so we can restore it) and the current
   render settings (`RENDER_SETTINGS`, `RENDER_BOUNDSFLAG`, `RENDER_STARTPOS`,
   `RENDER_ENDPOS`, `RENDER_FILE`, `RENDER_FORMAT`, `RENDER_SRATE`).
3. Select ONLY the target track (`SetOnlyTrackSelected`).
4. Set `RENDER_SETTINGS` = 2 (stems only, selected tracks, pre-master).
5. Set `RENDER_BOUNDSFLAG` = 0 (custom bounds) and `RENDER_STARTPOS` /
   `RENDER_ENDPOS` from `start_position` + `duration_seconds` (default: cursor
   position + 10 seconds, or the active time selection's range if one exists).
   Custom bounds mean the user's actual time selection is never touched.
6. Set `RENDER_FILE` to the temp output path. The filename embeds a timestamp
   (unique per capture) so REAPER never raises an overwrite prompt, which would
   block the render mid-command.
7. Set render format to WAV/24-bit/48kHz. `RENDER_FORMAT` is set via `GetSetProjectInfo_String` (not the numeric `GetSetProjectInfo`) using a 4-byte sink string:
   - `GetSetProjectInfo_String(0, "RENDER_FORMAT", "evaw", true)` — WAV. The value is a 4-char reversed format ID: "wave" reversed = "evaw". Other verified option: `"l3pm"` for MP3. A full base64-encoded sink config is also accepted but the 4-byte shortcut uses default settings for that format.
   - `RENDER_SRATE` = 48000
   - `RENDER_DITHER` = 0
8. Write a `busy: "render"` heartbeat (same pattern as `command_render`).
9. Call action 42230 (File: Render project).
10. Restore the original track selection.
11. Restore the original render settings captured in step 2.
12. Return the file path, duration, sample rate, plus the rendered file's size
    and mtime. The client verifies mtime is newer than the command's
    `created_at` (proves a real render happened, not a stale file). No SHA-1:
    REAPER's Lua has no built-in hash, and hand-rolling one over a multi-MB WAV
    inside the defer loop is waste.

### Result

```json
{
  "ok": true,
  "type": "capture_track_audio",
  "data": {
    "track": { "index": 3, "name": "Rhythm L", "guid": "..." },
    "file_path": "/tmp/reaper-diagnosis/rhythm-l-stem.wav",
    "duration_seconds": 10.0,
    "sample_rate": 48000,
    "channels": 2,
    "bit_depth": 24,
    "file_size_bytes": 2880044,
    "file_mtime": "2026-07-02T14:30:07-04:00",
    "render_loudness_lufs": -18.3
  }
}
```

`render_loudness_lufs` comes from `GetSetProjectInfo_String("RENDER_STATS", "", false)`
after the render, which returns loudness stats (LUFS-I, LUFS-M, LUFS-S, true peak,
peak, LRA) as a semicolon-separated string, not JSON. Parse with a simple split
on `;` and extract the LUFS-I value. `RENDER_STATS_SUMMARY` is also available
for a human-readable version. Both are native REAPER 7+ functions.

### Safety

- Gated by `allow_risk_level_3: true` in `bridge_config.json` (same gate as
  `render`). Temp stem render mutates project state (track selection, render
  settings), so it's a risk-level-3 operation even though everything is restored.
- All temp files go to a configurable temp directory (default: `/tmp/reaper-diagnosis/`
  on macOS/Linux, `%TEMP%\reaper-diagnosis\` on Windows). Filenames are
  timestamped, so captures never collide and REAPER never prompts to overwrite.
  The agent cleans up after analysis.
- Track selection and render settings (source, format, sample rate, bounds) are
  captured before the render and restored after, so the user's state survives.

### What it does NOT do

- Real-time spectrum analysis (would need a JSFX tap, out of scope for v1).
- Multi-track capture (one track per command; batch multiple commands for
  parallel analysis).
- Audio playback or monitoring (render only).
- Any analysis itself (it returns a file path; the analysis happens in the
  agent/model layer).

## New bridge command: `get_track_routing`

A lighter read-only command that captures routing state the existing `scan_fx`
doesn't cover. Needed because mix diagnosis depends on sends, receives, and bus
routing, not just the FX chain.

```json
{
  "type": "get_track_routing",
  "payload": { "target_track_name": "Rhythm L" }
}
```

Returns:

```json
{
  "track": { "index": 3, "name": "Rhythm L", "guid": "..." },
  "sends": [
    { "target_track_name": "Drum Bus", "target_index": 8, "volume": 0.0,
      "channels": "1/2 -> 1/2", "mute": false, "mono": false, "phase": false }
  ],
  "receives": [
    { "source_track_name": "Bass DI", "source_index": 4, "volume": -6.0,
      "channels": "1/2 -> 1/2", "mute": false }
  ],
  "parent_track": { "index": 7, "name": "Guitar Bus" },
  "volume_db": -3.0,
  "pan": 0.0,
  "phase_inverted": false,
  "automation_mode": "trim/read"
}
```

API basis: `GetTrackSendInfo_Value` / `GetTrackReceiveInfo_Value` /
`GetSetTrackSendInfo` / `GetMediaTrackInfo_Value("P_TRACK")` for parent. All
verified in the ReaScript docs at reaper.fm/sdk/reascript/reascripthelp.html.
Read-only, no undo block.

**Unit conversion happens in the Lua bridge, not the client.** `D_VOL` values
from the API are linear (1.0 = unity, 0.5 ≈ -6 dB). All `volume_db` fields in
the result are converted with `20 * log10(linear)` (with a floor, e.g. -150 dB,
for linear 0) before serialization, so the payload the model sees is always dB.

## The analysis payload

The agent assembles the diagnosis payload from three commands:

1. `scan_fx` (existing) — full FX chain with parameter names and current values.
2. `get_track_routing` (new) — sends, receives, parent bus, volume, pan.
3. `capture_track_audio` (new) — temp post-FX stem render, returns file path +
   LUFS.

Plus, optionally:

4. `get_context` (existing) — tempo, time signature, project name (for genre
   context).

### The spectrum analysis

The agent reads the rendered WAV and computes a spectrum snapshot. This happens
OUTSIDE REAPER, in the Python layer (reaperd.py or the calling agent). Options,
in order of preference:

1. **numpy + scipy.fft**: pull the WAV, compute an averaged FFT across the
   capture window, produce octave-band or 1/3-octave band levels. Zero deps
   beyond numpy/scipy, both already in the bridge's venv.
2. **librosa**: richer features (spectral centroid, rolloff, flux, flatness).
   Adds a dependency but gives the model better signal-characterization data.
3. **Raw stats only**: skip FFT, send LUFS + peak + crest factor. The model
   works from the FX chain + dynamics numbers alone. Weakest analysis but zero
   new code.

**Decision: option 1 for v1.** A 1/3-octave band spectrum (roughly 31 bands,
20Hz to 20kHz) plus LUFS/peak/crest factor is enough for the model to identify
tonal balance and dynamic issues. librosa is a v2 upgrade if the diagnosis
quality warrants it.

The spectrum is one band array combining all channels in the **power domain**
(per-channel power spectra averaged), not an amplitude mono-sum. This avoids the
cancellation trap: an out-of-phase stereo stem (or R = -L) would amplitude-sum
to silence and report a false diagnosis. Trade-off: L/R asymmetry is still
invisible in a single band array. Per-channel spectra are a v2 field addition,
not a schema change.

### Payload shape sent to the model

```json
{
  "project": {
    "name": "juicy.RPP",
    "tempo": 174,
    "time_signature": "4/4"
  },
  "track": {
    "name": "Rhythm L",
    "index": 3,
    "volume_db": -3.0,
    "pan": 0.0,
    "parent_track": "Guitar Bus"
  },
  "fx_chain": [
    {
      "name": "VST3: ReaEQ (Cockos)",
      "enabled": true,
      "parameters": [
        { "index": 0, "name": "Band 1: Frequency", "value": 120, "formatted": "120 Hz" },
        { "index": 1, "name": "Band 1: Gain", "value": -4.5, "formatted": "-4.50 dB" },
        { "index": 2, "name": "Band 1: Bandwidth", "value": 1.2, "formatted": "1.2 Q" }
      ]
    },
    {
      "name": "VST3: ThroatWire (Dead Pixel)",
      "enabled": true,
      "parameters": [ "..." ]
    }
  ],
  "routing": {
    "sends": [{ "target": "Drum Bus", "volume_db": 0.0 }],
    "receives": []
  },
  "audio": {
    "duration_seconds": 10.0,
    "integrated_lufs": -18.3,
    "sample_peak_db": -0.8,
    "rms_db": -13.2,
    "crest_factor_db": 12.4,
    "silence_fraction": 0.03,
    "stereo": {
      "correlation": 0.82,
      "mid_rms_db": -14.1,
      "side_rms_db": -24.7,
      "balance_db": 0.4,
      "note": "correlation is zero-lag L/R phase correlation ..."
    },
    "true_peak_db": -0.5,
    "loudness_range_lu": 5.4,
    "lufs_momentary_max": -14.9,
    "lufs_short_term_max": -16.2,
    "spectrum_third_octave": [
      { "freq_hz": 20, "level_db": -52.1 },
      { "freq_hz": 25, "level_db": -48.3 },
      "...",
      { "freq_hz": 16000, "level_db": -61.2 },
      { "freq_hz": 20000, "level_db": -65.0 }
    ]
  },
  "instruct": "Diagnose this track's mix state. Identify tonal balance problems, dynamic range issues, and FX-chain problems visible in the data. Propose one concrete move with specific parameter values. Do not claim frequency masking; you can only see this one track."
}
```

### The model prompt

```
You are a mix engineer analyzing a single track inside a REAPER session. You
receive the track's FX chain (with current parameter values), routing (sends,
receives, parent bus, phase, automation mode), and a 10-second post-FX audio
snapshot (sample peak, crest factor, 1/3-octave spectrum, and integrated LUFS
when available). Some fields may be null; treat null as "not measured".

Your job: diagnose what's wrong or could be improved. Be specific. Name
frequencies. Name parameters. Propose one concrete move, not five.

You see ONE track, not the mix. Do not diagnose frequency masking or claim
anything about how this track sits against others; you have no data for that.
Stay on what the single-track evidence supports: tonal balance, dynamics,
gain staging, FX-chain configuration.

Format:
1. DIAGNOSIS: [2-3 sentences. What you hear in the numbers.]
2. PROBABLE CAUSE: [1-2 sentences. Which FX / parameter / routing is likely
   causing it.]
3. SUGGESTED MOVE: [1-2 sentences. The exact parameter change. Include the
   plugin name, parameter name, current value, proposed value, and why.]
4. CONFIDENCE: [low / medium / high. Based on how much signal you have vs.
   how much you're guessing.]

Do not suggest moves you can't verify from the data. If the diagnosis is
uncertain, say so. An honest "I'm not sure" beats a confident wrong answer.
```

The last line is deliberate. The llm-proxy-hedge-contract pattern applies here:
an AI mix tool that bluffs will destroy trust on the first wrong call.

**Audio-block additions (2026-07-08, shipped):** the audio block now also
carries, when available: `silence_fraction` (share of 100 ms blocks below
-70 dBFS — how much of the capture is dead air), a `stereo` block (zero-lag L/R
phase correlation, mid/side RMS, L-minus-R balance; `null` for mono), and the
rest of REAPER's RENDER_STATS parsed client-side in
`diagnose.parse_render_stats` (`true_peak_db`, `loudness_range_lu`,
`lufs_momentary_max`, `lufs_short_term_max` — the bridge itself still parses
only LUFS-I). Absent stats are omitted, never null-filled. The live canonical
contract in `diagnose._SINGLE_TRACK_HONESTY_CONTRACT` describes these fields;
the prompt block quoted above is the original v1 wording, kept for design
history.

**Silence gate (2026-07-08, shipped):** the CLI refuses to spend a model call
on dead air. A capture with overall RMS at or below -60 dBFS, or with
`silence_fraction >= 0.85`, prints a "park the cursor where the track is
playing" message and exits with code 3 instead of diagnosing. `--force`
overrides; `--payload-only` is never gated (the payload is still useful for
debugging). Thresholds live in `cli.NEAR_SILENT_RMS_DB` /
`cli.SILENCE_GATE_FRACTION`.

## Diagnosis output

The result comes back to the agent as JSON. The agent can:

1. Print it to REAPER's console via a `show_console_message` command (new, trivial:
   `reaper.ShowConsoleMsg(text)`).
2. Propose the move as a pending action the user can accept (which sends a
   `set_fx_param` command through the existing bridge pipeline).
3. Display in a ReaImGui panel (v2).

**v1: console message only.** Get the loop working before building UI.

## Cross-track masking (build #2, shipped 2026-07-04)

The v2 headline. Single-track diagnosis structurally cannot see masking, and the
single-track hedge explicitly forbids it. This build promotes masking to a shipped
feature in the same CLI, gated on having 2+ stems.

**CLI:** the positional `track` argument is now `nargs="+"`. One name runs the
existing single-track flow unchanged. Two or more names run the masking flow:
capture each track's post-FX stem (reusing `capture_track_audio`), analyze each
spectrum (reusing `analyze_wav`), compute the overlap, and diagnose with the
masking contract. Duplicate names that resolve to the same track are rejected
(masking needs two distinct tracks).

**Overlap computation (`analysis.masking_overlap`, pure numpy, no REAPER):** for
every unordered pair of tracks, a 1/3-octave band is *contested* when BOTH tracks
have real energy in it. "Real energy" = the band sits within
`MASKING_PROMINENCE_DB` (12 dB) of that track's own loudest band AND above
`MASKING_ABSOLUTE_FLOOR_DB` (-60 dBFS); a track below the floor is treated as
silent and masks nothing. Each contested band carries both levels, the signed
difference (a − b), and which track is louder (the likely masker; the quieter is
at risk). Deliberately coarse: a contested band flags a candidate collision
region, it does not prove an audible clash, and a narrow collision can hide inside
a wide band. The method note ships in the payload so the model sees the caveat.

**The masking payload (`diagnose.build_masking_payload`):** mirrors the
single-track payload but carries a list of tracks (each with FX chain, routing,
audio + spectrum) plus the `masking` table from `masking_overlap`. Project block
is shared (one session).

**The sibling prompt (`diagnose.MASKING_SYSTEM_PROMPT`):** a separate system
prompt, NOT an edit to the single-track hedge. It is allowed to diagnose masking
because the cross-track data backs it, but it stays honest: contested = candidate
not proof, name the region not a false-precise Hz, a shared band can be fine by
design (kick + bass), and if the tracks barely overlap it says so instead of
inventing a problem. Same 4-part format (diagnosis / cause / move / confidence),
same "one concrete move" discipline, biased toward a complementary carve
referencing a real EQ already on the relevant track. The masking path remains on
the prose `diagnose()` interface while single-track Track Check uses
`diagnose_track()` and the structured contract.

**Monetization note:** the original spec filed cross-track masking under the paid
v2 tier. Shipping it in the free CLI is a product decision still open for David;
the code doesn't gate it. If the paid tier happens, batch-all-tracks masking + the
ReaImGui panel + one-click apply are the natural paid surface, not a paywall on
this.

## Build order

### Phase 1: capture (1-2 days)

Goal: prove the audio capture path end to end.

1. Write `capture_track_audio` in the Lua bridge. Select target track, set
   render source to stems (RENDER_SETTINGS=2), custom bounds
   (RENDER_BOUNDSFLAG=0 + STARTPOS/ENDPOS), render to temp WAV, restore state,
   return file path + LUFS.
   - Verify: render a known track, confirm the WAV exists, plays, and matches
     the track's post-FX output (not pre-FX, no master-bus FX printed).
   - Verify: track selection is restored after capture.
   - Verify: render settings (source, format, sample rate, bounds) are restored.
   - Verify: the user's time selection is untouched.

2. Write `get_track_routing` in the Lua bridge. Reads sends, receives, parent,
   volume, pan, phase, automation mode.
   - Verify: output matches what you see in REAPER's routing matrix.

3. Register both in the `handlers` table and `NO_UNDO_BLOCK`. Neither gets an
   undo block: `get_track_routing` is read-only, and `capture_track_audio`
   restores everything it touches (selection, render settings), so there is no
   state a user would want to Ctrl+Z back. It IS still gated behind
   `allow_risk_level_3` because it writes a file to disk and briefly mutates
   project state.

### Phase 2: analysis (1-2 days)

Goal: produce a diagnosis from real data.

4. In Python (reaperd.py or a standalone script), chain three commands:
   `scan_fx` + `get_track_routing` + `capture_track_audio`.
5. Read the rendered WAV, compute 1/3-octave spectrum + LUFS/peak/crest.
6. Assemble the payload, send to the model.
7. Print the diagnosis to REAPER's console.

### Phase 3: validation (1 day)

8. Run it on a real Wretcher mix (David's own session).
9. Record a screen capture: select a track, run the tool, show the diagnosis,
   apply the suggested move, listen to the before/after.
10. Post the video. The demand signal is whether strangers ask "can it do X too."

## What is NOT in v1

- Real-time monitoring (static capture only)
- Multi-track simultaneous analysis (one track per run)
- Automatic fix application (diagnosis only; the user applies the move
  manually, or through the existing `set_fx_param` command)
- ReaImGui panel (console output first)
- ~~Cross-track masking analysis~~ — SHIPPED in build #2 (2026-07-04); see the
  "Cross-track masking (build #2)" section. No longer deferred.
- The model calling back to adjust its diagnosis after listening (no audio
  feedback loop; the model works from numbers, not from hearing)

## Risks

- **Temp render latency:** a long track captures slowly. Mitigated by defaulting
  to 10 seconds from the cursor, not the whole track. The user can override.
- **State corruption on crash:** if the bridge crashes mid-capture, the render
  settings and track selection are left mutated. Much smaller blast radius than
  the earlier solo-based design (render settings and selection are annoying to
  lose, solo state across a 60-track session is destructive). Mitigation: write
  the captured settings to a state file before rendering; extend the defer
  loop's startup re-queue to restore from it if found.
- **Wrong diagnosis:** the model bluffs. Mitigated by the prompt's hedge
  contract and by David being the first user (he'll know immediately if the
  diagnosis is wrong because he can hear the track).
- **Plugin introspection gaps:** some VST3 plugins don't expose parameter names
  or values through the ReaScript API (the bridge already handles this with
  `finite_or_nil` coercion and `parameters_truncated` flags). The diagnosis
  will be weaker for those plugins but won't break.
- **Spectrum resolution:** 1/3-octave bands are coarse. A narrow resonance at
  2.4kHz might show up as a bump in the 2.5kHz band but the exact frequency is
  lost. v2 can add a higher-resolution FFT or a spectral centroid computation.

## Monetization

**v1: free.** The whole point is to prove the diagnosis is useful. A free
ReaPack script that reads your track and tells you what's wrong is the product.

**Paid tier (v2):** batch analysis across all tracks, cross-track masking
detection, the ReaImGui panel, automatic fix proposal with one-click apply,
project-level analysis (mix bus diagnosis). Price: $15 one-time or $5/month for
ongoing updates. The free tier stays fully functional for single-track
diagnosis.

The bet is the same one David always makes: built it because he needed it, give
it away, see if strangers want more. If the free version gets traction, the
paid tier is the natural extension, not a paywall on existing functionality.

## API reference (verified)

All functions below are confirmed in the official ReaScript documentation at
reaper.fm/sdk/reascript/reascripthelp.html and the X-Raym searchable docs at
extremraym.com/cloud/reascript-doc/.

**FX metadata (already used by the bridge):**
- `TrackFX_GetCount(track)` — number of track FX
- `TrackFX_GetFXName(track, fx_index, "")` — plugin display name
- `TrackFX_GetParamName(track, fx_index, param_index, "")` — parameter name
- `TrackFX_GetParam(track, fx_index, param_index, minvalOut, maxvalOut)` —
  normalized value + range
- `TrackFX_GetParamEx(track, fx_index, param_index, minvalOut, maxvalOut, midvalOut)` —
  normalized value + range + midpoint
- `TrackFX_GetFormattedParamValue(track, fx_index, param_index, "")` —
  display-formatted string (e.g. "120 Hz", "-4.50 dB")
- `TrackFX_GetEnabled(track, fx_index)` — bypass state
- `TrackFX_GetNumParams(track, fx_index)` — parameter count

**Routing (new, for get_track_routing):**
- `GetTrackNumSends(track, 0)` — count of hardware/track sends
- `GetTrackSendInfo_Value(track, send_index, "B_MUTE"|"D_VOL"|"I_SRCCHAN"|"I_DSTCHAN"|"I_MIDIFLAGS")` —
  send properties
- `GetTrackNumReceives(track)` — count of receives
- `GetTrackReceiveInfo_Value(track, recv_index, ...)` — receive properties
- `GetMediaTrackInfo_Value(track, "P_TRACK")` — parent track pointer
- `GetMediaTrackInfo_Value(track, "D_VOL"|"D_PAN"|"B_PHASE"|"I_AUTOMODE")` —
  track state

**Render (adapted for capture_track_audio):**
- `GetSetProjectInfo_String(0, "RENDER_FILE", path, true)` — output path
- `GetSetProjectInfo(0, "RENDER_SETTINGS", 2, true)` — render source. Verified
  flags: `(&(1|2)==0)`=master mix, `&1`=stems+master, `&2`=stems only (selected
  tracks, pre-master), `&128`=selected tracks via master. Use `2` for capture.
- `GetSetProjectInfo(0, "RENDER_BOUNDSFLAG", n, true)` — bounds (0=custom time
  bounds, 1=project, 2=time_selection, 3=regions, 4=selected_items,
  5=selected_regions). Use `0` for capture.
- `GetSetProjectInfo(0, "RENDER_STARTPOS", t, true)` /
  `GetSetProjectInfo(0, "RENDER_ENDPOS", t, true)` — capture range in seconds,
  only used when RENDER_BOUNDSFLAG=0
- `SetOnlyTrackSelected(track)` — select just the target track for the stems render
- `GetSetProjectInfo_String(0, "RENDER_FORMAT", "evaw", true)` — 4-byte sink string for WAV (reversed: "wave" -> "evaw"). Other verified option: `"l3pm"` (MP3). Belongs on `GetSetProjectInfo_String`, NOT the numeric `GetSetProjectInfo`. A full base64 sink config is also accepted but the 4-byte shortcut uses format defaults.
- `GetSetProjectInfo(0, "RENDER_SRATE", 48000, true)` — sample rate
- `GetSetProjectInfo_String(0, "RENDER_STATS", "", false)` — post-render
  loudness stats (semicolon-separated string, NOT JSON). Contains LUFS-I, LUFS-M, LUFS-S, true peak, peak, LRA.
- `GetSetProjectInfo_String(0, "RENDER_STATS_SUMMARY", "", false)` — human-readable summary of the same stats.
- `reaper.Main_OnCommand(42230, 0)` — File: Render project (same action the
  existing `render` command uses)
- `CalculateNormalization(PCM_source, normalizeTo, normalizeValue, ...)` —
  can compute LUFS-I, RMS-I, peak, true peak, LUFS-M/S max for a source

**Audio accessor (verified, pre-FX only, NOT used for post-FX capture):**
- `CreateTrackAudioAccessor(track)` — creates accessor, samples are pre-FX
- `GetAudioAccessorSamples(accessor, samplerate, numchannels, starttime_sec, numsamplesperchannel, samplebuffer)` —
  returns pre-FX interleaved samples
- Documented limitation: "Samples are extracted immediately pre-FX."
- Do NOT use this for post-FX spectrum. The temp stem render exists specifically
  to get around this.

**Output:**
- `reaper.ShowConsoleMsg(text)` — print to REAPER's console

## Cross-references

- [[reaper-audio-workflow]] — David's 19 REAPER settings + the AI control-layer
  design this tool extends
- [[the-stash-design]] — sibling project, same bridge foundation, same
  distribution (ReaPack, wretcher207/dead-pixel-design repo). The Stash captures
  state snapshots; this tool reads live state and diagnoses.
- Bridge source: `~/workspace/audio/reaper-bridge/bridge/reaper_agent_bridge.lua`
  (v3.3.0), `reaperd.py` (client), `bridge/command_schema.md` (command reference)
- ReaScript API docs: reaper.fm/sdk/reascript/reascripthelp.html,
  extremraym.com/cloud/reascript-doc/
