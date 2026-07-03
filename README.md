# Post Mortem

AI track diagnosis for REAPER. Select a track, run one command, get told
what's actually wrong with it.

It reads the track's real state: every FX and its current parameter values,
sends, receives, parent bus, fader, pan. Then it renders a 30 second post-FX
stem, measures it (LUFS, peak, crest factor, 1/3-octave spectrum), and hands
the numbers to a model that answers like a mix engineer: what it sees, what's
probably causing it, one concrete move with the exact parameter and value,
and how confident it is. Not five suggestions. One move.

The part I care about most: it's not allowed to bluff. It sees ONE track,
not your mix, so it will never tell you your guitars are masking your vocal.
It can't know that and it says so. Every diagnosis carries a confidence
rating, and when the data is thin the diagnosis says the data is thin. An
honest "I'm not sure" beats a confident wrong answer. That contract lives in
the prompt and it's not coming out.

A Dead Pixel Design release. Built on the
[Reaper Daemon](https://github.com/wretcher207/reaper-daemon) bridge.

**Status: early build.** It works, it's diagnosed real problems on real
mixes, and it's still young. Spec in `docs/SPEC.md`.

## What a diagnosis looks like

Real output, run on a kick drum:

> **DIAGNOSIS:** The kick is hitting -0.1 dBFS sample peak with -8.7 LUFS
> integrated and an 11.65 dB crest factor, a level that leaves essentially no
> headroom for the parent Drum_Buss to do its work. Tonal shape is otherwise
> reasonable: a strong fundamental at 50 Hz, a clean scoop through 200-1000 Hz,
> and a healthy click region at 2-5 kHz; the problem is level and dynamics,
> not EQ. [...]
>
> **CONFIDENCE:** Medium. The over-hot level and the presence of the Kontakt
> receive are directly verifiable; the polarity-misalignment diagnosis fits
> the metrics but is still a hypothesis until the phase-flip test is performed.

## Requirements

- REAPER 7+ (macOS, Windows, or Linux)
- The [Reaper Daemon](https://github.com/wretcher207/reaper-daemon) bridge,
  installed and running inside REAPER
- Python 3.10+
- An API key. A plain Anthropic key is all you need (it defaults to Claude
  Opus). Any Anthropic-compatible endpoint works too (MiniMax is tested).

## Install

**1. The bridge** (this is what lets anything talk to REAPER). Copy-paste,
then restart REAPER:

```bash
git clone https://github.com/wretcher207/reaper-daemon.git
cd reaper-daemon && python3 setup/install.py   # use `python` on Windows
```

Full details and options are in that repo's README.

**2. Post Mortem.** One command:

```bash
pipx install git+https://github.com/wretcher207/post-mortem.git
```

(No pipx? `python3 -m pip install --user pipx && python3 -m pipx ensurepath`.)

**3. Config.** Create `~/.config/postmortem/config`. The whole file, for a
plain Anthropic key, is two lines:

```
ANTHROPIC_API_KEY=<your key>
REAPER_DAEMON_ROOT=/path/to/your/reaper-daemon/clone
```

That's it. It defaults to Claude Opus. To use an Anthropic-compatible
endpoint instead (MiniMax, etc.), add two more lines:

```
ANTHROPIC_BASE_URL=https://api.minimax.io/anthropic
POSTMORTEM_MODEL=MiniMax-M3
```

Environment variables with the same names also work and take precedence.

## Usage

With REAPER open and a project loaded:

```bash
postmortem "Kick"
```

That's it. Options:

```
--seconds N        capture length, default 30, starting at the edit cursor
--keep-wav         keep the temp stem instead of deleting it
--payload-only     print the data payload as JSON and skip the model call
```

The capture starts at your edit cursor, so park the cursor somewhere the
track is actually playing. A diagnosis of 30 seconds of silence is accurate
and useless.

## What it won't do (yet)

One track per run. No cross-track masking analysis, no real-time monitoring,
no automatic fix application, no fancy panel. Console output, one diagnosis,
you apply the move with your own hands and ears. If the free version earns
it, the batch and cross-track stuff is the natural next step.

## Known rough edge

REAPER's render dialog must have "Automatically close when finished" ticked
(it's a checkbox in the render window, REAPER remembers it). On a fresh
REAPER install it's unticked and the capture will sit waiting on the dialog.
Tick it once and you're good forever.

## License

MIT.
