# Post Mortem — AI track diagnosis for REAPER

A Dead Pixel Design release. Reads a selected track's full state (FX chain,
routing, volume/pan) plus a post-FX stem capture, sends it to an LLM, and
returns a specific, honest mix diagnosis with one concrete proposed move.

**The spec is law:** `docs/SPEC.md`. Read it before changing anything
architectural. It encodes verified ReaScript facts (stems render source,
custom bounds, RENDER_STATS semantics) and hard-won corrections; don't
re-litigate them from memory. The wiki copy in the second brain is history,
this repo's copy is build-authoritative.

## Architecture (two repos)

- **This repo**: the Python layer. Drives the bridge, reads the rendered WAV,
  computes the spectrum/stats, assembles the payload, calls the model, prints
  the diagnosis. Plus release packaging (ReaPack, later).
- **`~/workspace/audio/reaper-bridge`** (github.com/wretcher207/reaper-daemon):
  the Lua bridge inside REAPER. Post Mortem's two capture commands
  (`capture_track_audio`, `get_track_routing`) live THERE, as bridge commands.
  Changes to them follow that repo's CLAUDE.md ship rules (branch → PR → CI →
  merge → tag → bump @version + index.xml in lockstep).

The client talks to REAPER exclusively through
`python3 ~/workspace/audio/reaper-bridge/reaperd.py` (status gate first,
one command per action, confirm `ok:true`). Never bypass it with raw file
writes to the inbox.

## Layout

- `postmortem/analysis.py` — WAV → mono-sum 1/3-octave spectrum, crest factor.
  Pure numpy, no librosa (that's a v2 call, see SPEC).
- `postmortem/bridge.py` — thin wrapper over reaperd.py subprocess calls.
- `postmortem/diagnose.py` — payload assembly + model call + output formatting.
- `postmortem/cli.py` — `python3 -m postmortem <track name>`.
- `tests/` — unit tests; synthetic WAV fixtures, no REAPER required.

## Hard rules

- **The hedge contract is the product.** The diagnosis prompt forbids claims
  the data can't support (no masking claims from single-track data, confidence
  field mandatory). Never weaken it to make output more impressive.
- **Never report a capture as verified without checking the file**: mtime newer
  than the command's created_at, nonzero size. A stale WAV diagnosed
  confidently is the worst failure mode this tool can have.
- Lua edits in the bridge repo need a REAPER reload before live testing; the
  defer loop runs last-loaded code. Don't claim live verification without it.
- v1 scope is frozen in SPEC.md ("What is NOT in v1"). Push extras to v2 notes,
  don't build them.

## Voice

Public-facing copy (README, ReaPack description, site page) is David's voice:
load `~/workspace/_config/voice-profile.md` first. Never the word "workflow".
