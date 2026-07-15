# Post Mortem troubleshooting

Start with the exact message shown by Post Mortem Setup or the panel. Recovery
messages are part of the product and should be followed before changing files
by hand.

## The panel is not in REAPER's Actions list

1. Quit REAPER.
2. Open Post Mortem Setup and choose **Install or Update** again.
3. Restart REAPER.
4. Open **Actions > Show action list** and search for `Post Mortem`.

For a portable REAPER installation, make sure Setup points to that portable
resource folder, not the default user folder.

## The panel says Engine not running

Choose **Start Engine** in the panel. If it does not stay running, open Post
Mortem Setup and choose **Test Setup**. Reinstall if Setup reports that the
sidecar or Reaper Daemon files are incomplete.

## The panel says Needs REAPER

REAPER must be open with the installed Reaper Daemon watcher running. Restart
REAPER once after installation or update. If the message remains, use **Test
Setup** and follow its recovery text.

## Safe capture is not enabled

Choose **Enable Safe Capture** in the panel, then restart REAPER. This changes
the bridge's capture permission and does not edit the project.

## The render window stays open

In REAPER's render window, enable **Automatically close when finished**. REAPER
remembers the setting. Return to the panel and choose **Test Again**.

## The capture is silent

Move the edit cursor to a section where the selected track is making sound,
then check it again. Muted items, an empty time selection, or a quiet routing
track can all produce a correct silence refusal.

## Post Mortem refuses capture isolation

The current bridge can prove isolated capture for item-less routing and bus
tracks. A track containing media items may be refused because its render could
be the full mix. Post Mortem will not turn that uncertainty into a diagnosis.

Soloing the track by hand does not bypass this gate. Try an item-less bus or
routing track, or wait for an update that closes the item-track isolation gap.

## The API key is rejected

- Confirm that the key is active with the provider that issued it.
- Confirm that billing or credits are available on that provider account.
- If you configured a third-party endpoint, use `POSTMORTEM_API_KEY`, not an
  unrelated `ANTHROPIC_API_KEY`.
- Check `ANTHROPIC_BASE_URL` and `POSTMORTEM_MODEL` for spelling errors.

Post Mortem does not search other files on the computer for a missing key. A
key must come from the environment, the Post Mortem config, or the panel's
validated connection screen.

## The license is not accepted

- Use the original signed JSON license file. Do not edit it.
- Confirm that the license is for Post Mortem version 1.
- Check that the Mac's date and time are correct.
- Reopen Setup, choose **Add License**, and select the file again.

License validation stays offline. A valid version 1 license keeps version 1
running after its update-entitlement date. If a valid file is still refused,
contact `david@deadpixeldesign.com` and include the exact error text. Do not
send an API key.

## Preview audio will not play

Preview playback uses SWS. Reinstall with Post Mortem Setup if the panel says
SWS is missing. Existing user-installed SWS or ReaImGui files are not
overwritten by the installer.

## A preview did not confirm restoration

Do not choose **Apply Fix**. Stop playback and check the affected parameter in
REAPER. The bridge owns crash recovery and normally restores the saved value
when it resumes. If the value is still changed, use REAPER's undo history and
save the project under a new name before continuing.

## Local files and diagnostics

The panel's **Copy Diagnostics** action can include local paths, track names,
plug-in names, and typed error details. Review it before sharing.

Service logs are stored under the Post Mortem data folder:

- macOS: `~/Library/Application Support/PostMortem/logs/service.log`
- Windows: `%APPDATA%\PostMortem\logs\service.log`
- Linux: `$XDG_DATA_HOME/postmortem/logs/service.log`

The provider config is separate at `~/.config/postmortem/config` on macOS and
Linux. Never paste that file into a support message because it may contain an
API key.

Support: `david@deadpixeldesign.com`
