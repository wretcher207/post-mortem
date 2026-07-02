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

## Usage (target)

```bash
python3 -m postmortem "Rhythm L"
```

Prints the diagnosis to the terminal and to REAPER's console.
