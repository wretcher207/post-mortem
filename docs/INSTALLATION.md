# Install Post Mortem on macOS

## Current release

Post Mortem `0.1.2` is live as a `$39` Apple silicon early-access release.
Checkout delivers a private signed and notarized disk image plus a unique
signed JSON license.

The paid Windows and Linux builds are withheld until their clean-machine
customer checks pass. The free engine remains available for terminal users on
all three platforms.

## What you need

- An Apple silicon Mac.
- REAPER 7 or newer. Open REAPER at least once before installing.
- The Post Mortem disk image and its signed JSON license file.
- An internet connection for the model provider during diagnosis.
- Either an Anthropic API key or an MCP client connected to Reaper Daemon.

The installer includes the Post Mortem panel, packaged engine, Reaper Daemon,
ReaImGui, and SWS. It does not require administrator access. It does not touch
your REAPER projects.

## Install

1. Quit REAPER if it is open.
2. Open `PostMortem-0.1.2-macos-arm64.dmg`.
3. Open **Post Mortem Setup**.
4. Confirm the REAPER folder shown by Setup. For a portable or nonstandard
   installation, choose **Choose REAPER Folder** and select the folder that
   contains `reaper.ini` and `Scripts`.
5. Choose **Install or Update**.
6. Choose **Add License**, then select the signed `.json` license delivered
   with the purchase.
7. Restart REAPER once. The restart loads the installed bridge and registers
   the panel action.

Setup may report that installation succeeded but the live check still needs a
restart. That is normal on a first install. After REAPER restarts, reopen Setup
and choose **Test Setup** if you want to confirm the connection before opening
the panel.

## Open the panel

1. In REAPER, open **Actions > Show action list**.
2. Search for `Post Mortem`.
3. Run **Post Mortem (docked panel)**.
4. Dock or place the panel where you want it. REAPER remembers the placement.

On first run, choose **Connect to REAPER**. If the panel asks to enable safe
capture, approve that change and restart REAPER once more. Post Mortem will not
run a diagnosis until the bridge confirms that capture is allowed.

## Connect analysis

The first release does not include hosted checks.

For the direct provider path, paste an Anthropic API key into the panel and
choose **Connect an API Key**. Post Mortem makes one small validation request
before saving the key locally.

For the MCP path, choose **Use Through an MCP Client** and follow the prompt in
the panel. The model in that client performs the diagnosis, so Post Mortem does
not need a separate API key. The client still needs a working Reaper Daemon MCP
connection.

Anthropic-compatible providers are an advanced configuration. Put the endpoint,
model, and dedicated provider key in `~/.config/postmortem/config` before
opening the panel:

```text
POSTMORTEM_API_KEY=your-provider-key
ANTHROPIC_BASE_URL=https://provider.example/anthropic
POSTMORTEM_MODEL=your-model
```

Do not put an Anthropic key under `POSTMORTEM_API_KEY` for a third-party host.
Post Mortem keeps the two key paths separate on purpose.

## Run the first Track Check

1. Select one track in REAPER.
2. Put the edit cursor where the track is making sound.
3. In the panel, choose **Check This Track**.
4. Let the 10-second capture and diagnosis finish.

Post Mortem may refuse ordinary tracks that contain media items because the
current bridge cannot always prove that their render is isolated. Item-less
routing and bus tracks are the most reliable first checks. A refusal protects
you from a confident diagnosis built on the full mix.

## Update

Open the newer disk image and choose **Install or Update**. The installer
replaces only managed runtime files. It preserves the license, API settings,
history, bridge authentication, and unrelated REAPER startup content.

## Uninstall

Open Post Mortem Setup and choose **Remove Post Mortem**. By default, the
panel, engine, and managed bridge files are removed while settings, license,
and local history remain available for a later reinstall.

Select **Also delete settings, license, and history** only when you want the
full local data removal. The uninstaller does not delete REAPER projects or
unrelated scripts.

## Optional checksum verification

The download includes a `.sha256` file. Terminal users can verify the exact
disk image before opening it:

```bash
shasum -a 256 -c PostMortem-0.1.2-macos-arm64.dmg.sha256
```

A successful check prints `OK`. This is optional and is not part of the normal
installer path.

For setup failures, see [Troubleshooting](TROUBLESHOOTING.md). For data handling,
see [Privacy](PRIVACY.md).
