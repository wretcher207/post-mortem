# Post Mortem

AI track diagnosis for REAPER. Select a track, run it, and get a mix
engineer's read on what's wrong: tonal balance, dynamics, gain staging,
FX-chain problems, with one concrete move proposed, parameter values included.

It reads the track's actual state (every FX and parameter value, sends,
receives, parent bus) plus a post-FX stem capture, and diagnoses from
evidence, not vibes. When the data can't support a conclusion, it says so.

A Dead Pixel Design tool. Built on the [Reaper Daemon](https://github.com/wretcher207/reaper-daemon) bridge.

**Status: early build.** Spec in `docs/SPEC.md`.

## Requirements

- REAPER 7+ with the Reaper Daemon bridge installed and running
- Python 3.10+, numpy
- An Anthropic API key (`ANTHROPIC_API_KEY`)

## Setup (once)

From the repo root:

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
ln -sf "$PWD/bin/postmortem" ~/.local/bin/postmortem
```

## Usage

```bash
postmortem "Rhythm L"
```

That's the whole command, from any directory. The wrapper always runs
through the repo's `.venv`, so it doesn't matter which `python` your
shell finds. Prints the diagnosis to the terminal and to REAPER's console.
