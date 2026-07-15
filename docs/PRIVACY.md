# Post Mortem privacy

This document describes Post Mortem `0.1.0`. It is a plain account of what the
current product does, not a promise about unbuilt features.

## What stays on the computer

Post Mortem captures short WAV files to measure a track. Raw audio is not sent
to the model provider.

A normal Track Check removes its temporary WAV after measurement. Fix Preview
keeps baseline and candidate files long enough to play them in the panel, then
removes them when the preview is closed, replaced, applied, rejected, or the
panel exits normally. A crash or forced shutdown can leave local files in the
`captures` folder. They can be deleted with REAPER and the panel closed.

The local data root is:

- macOS: `~/Library/Application Support/PostMortem`
- Windows: `%APPDATA%\PostMortem`
- Linux: `$XDG_DATA_HOME/postmortem`, or `~/.local/share/postmortem`

The data root can contain capture files, service logs, job results, local
feedback, setup state, and license status.

## What the model provider receives

For the API-key path, Post Mortem sends structured measurements and the project
details needed for the diagnosis. Those details can include:

- Track names, IDs, fader, and pan values.
- Plug-in names, parameter names, and current parameter values.
- Sends, receives, parent-bus details, and other routing metadata.
- Loudness, peak, crest factor, spectrum, stereo, and silence measurements.
- The diagnosis prompt and validated result schema.

It does not attach the captured WAV. Project and plug-in names can still be
sensitive. The provider's own retention and training terms apply to data sent
to that provider.

For the MCP path, the selected MCP client and its model provider receive the
measurement payload. Their privacy settings and retention terms apply. Post
Mortem cannot control what a separate client stores.

## API keys

The panel validates the entered key with one small provider request before
saving it. The key is stored in `~/.config/postmortem/config` on macOS and
Linux, with owner-only permissions where the operating system supports them.
Environment variables can be used instead.

Post Mortem does not search unrelated local files for provider credentials. A
third-party compatible endpoint uses `POSTMORTEM_API_KEY`, kept separate from
`ANTHROPIC_API_KEY` so the wrong credential is not forwarded to another host.

## License checks

The paid panel uses a signed JSON license. Validation uses the public key in
the installed product and does not contact a license server. The local license
record contains the holder, product, purchased major version, issue date,
update-entitlement date, signing-key ID, and signature.

The purchased major version does not expire. The update-entitlement date is a
separate check with a 30-day offline grace period.

## Feedback, logs, and telemetry

The **Not Helpful** action appends a local record to `feedback.jsonl`. Version
`0.1.0` does not upload that file.

Service errors are written to `logs/service.log`. Diagnostic output can contain
local paths, track names, plug-in names, and provider error details. Review it
before sharing.

Version `0.1.0` has no analytics, usage telemetry, remote crash reporting,
account system, or automatic license check. The only normal network request is
the model-provider call initiated for analysis, or traffic initiated by the
user's separate MCP client.

## Remove local data

Post Mortem Setup removes managed application files while keeping settings,
license, and local history by default. Select the explicit option to also
remove app data when uninstalling.

You can also inspect the data root before removal. Do not delete it while a
Track Check or Fix Preview is running.

Privacy questions: `david@deadpixeldesign.com`
