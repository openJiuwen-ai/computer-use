---
name: vlc
description: How to operate VLC media player through the desktop tools -- the Preferences dialog's Simple/All split, Effects and Filters for video adjustments, Convert/Save for extracting or converting media, playback/volume/snapshot shortcuts, and reading vlcrc for reliable verification. Reference knowledge for any VLC task, not a script for one.
---

# VLC media player

Most VLC tasks are settings changes
buried in **Preferences**, or a video transform/adjustment in **Effects and
Filters** -- both are single dialogs, not scattered across menus.

## Preferences: Simple vs. All

- Tools > Preferences (**Ctrl+P**) opens a dialog with a "Show settings"
  radio at the bottom-left: **Simple** (default) shows only the common
  Interface/Audio/Video/Subtitles/Input-Codecs/Hotkeys panes; **All** expands
  every pane into a full tree with far more granular options.
- For an obscure one-off setting (recording directory, max volume, disabling
  a UI element), switch to **All** first and search the left tree by keyword
  rather than guessing which Simple pane it's under -- many settings (e.g.
  the Qt interface's maximum displayed volume, under Interface > Main
  interfaces > Qt) only exist in the All view.
- Recording directory: Preferences (Simple) > Input / Codecs > "Record
  directory or filename".
- Volume above 100%: Preferences (All) > Interface > Main interfaces > Qt >
  "Maximum Volume displayed" -- raise this, then the volume slider/hotkeys
  can push past 100% up to that new ceiling.
- Changes require **Save**, and some (interface-level ones) need restarting
  VLC to take effect.

## Effects and Filters

Tools > Effects and Filters (**Ctrl+E**) is a tabbed dialog for adjustments
that would otherwise need a full re-encode: Video Effects > **Basic** tab has
Brightness/Contrast/Saturation/Hue; **Geometry** tab has Transform (rotate 90/
180/270, flip) and Crop; Audio Effects has Equalizer/Compressor. Changes here
apply live to the current playback.

## Convert / Save (extracting or converting media)

Media > **Convert / Save...** (**Ctrl+R**): Add the source file, click
Convert/Save, pick a profile (e.g. "Audio - MP3" to extract just the audio
track), Browse to set the destination filename, Start. This is the reliable
route for "turn this video's audio into an MP3" or "convert this to a
different format" tasks -- much more predictable than trying to do it via
playback controls.

## Snapshot, streaming, and playlist

- Video > **Take Snapshot** (or **Shift+S**) saves the current frame; the
  destination folder is Preferences (Simple) > Video > "Directory" under
  Snapshot.
- Media > **Open Network Stream...** (**Ctrl+N**) plays a URL directly.
- To play a file, use Media > **Open File...** (**Ctrl+O**) in the VLC window
  that is already open and type the full path. Running another `vlc` (or
  launching VLC with the file) starts a separate player, and the one the task
  opened stays idle.
- File > **Open Multiple Files** or dragging several files into the playlist
  queues them.

## Shortcuts

- Play/pause **Space**; stop **S**; toggle fullscreen **F**; mute **M**.
- Volume up/down **Ctrl+Up** / **Ctrl+Down**.
- Seek: 3s **Shift+Left/Right**, 10s **Alt+Left/Right**, 1 min
  **Ctrl+Left/Right**.
- Speed: faster **+**, slower **-**, normal rate **=**.
- Next/previous item in playlist **N** / **P**.

## Reliable verification (via shell commands)

VLC writes its settings to a single plain-text INI-style file,
`~/.config/vlc/vlcrc`, as `key=value` lines (commented-out lines show the
default and valid range) -- grep it to confirm a Preferences change actually
saved, rather than trusting the dialog's on-screen state after closing it.

## Keyboard shortcuts (Linux)

When a shortcut below does what you need, prefer it to clicking through menus: a key needs no coordinates, so it cannot land on the wrong item. Confirm the result on the next screenshot.

Verified for VLC 3.x against the VLC hotkeys wiki and the default key table in VLC 3.0.x source (src/libvlc-module.c, non-Apple block). Needs the player window focused. Defaults can be changed in Tools > Preferences > Hotkeys.

Playback:
  Space               Play/Pause
  S                   Stop
  N                   Next track
  P                   Previous track
  E                   Next frame
  ]                   Faster (fine step)
  [                   Slower (fine step)
  +                   Faster
  -                   Slower
  =                   Normal speed
  T                   Show current position/time
  Esc                 Leave fullscreen
  Ctrl+Q              Quit

Seeking (default jump sizes in source):
  Shift+Left / Right  Jump back/forward 3 seconds
  Alt+Left / Right    Jump back/forward 10 seconds
  Ctrl+Left / Right   Jump back/forward 1 minute
  Ctrl+Alt+Left / Right  Jump back/forward 5 minutes
  Ctrl+T              Go to time

Volume and audio:
  Ctrl+Up / Ctrl+Down Volume up / down
  M                   Mute
  B                   Cycle audio track
  J                   Audio delay down
  K                   Audio delay up

Video:
  F                   Toggle fullscreen
  Shift+S             Take snapshot
  A                   Cycle aspect ratio
  C                   Cycle crop
  Z / Shift+Z         Zoom / un-zoom
  Shift+R             Start/stop recording

Subtitles:
  V                   Cycle subtitle track
  Shift+V             Toggle subtitles on/off
  G                   Subtitle delay down
  H                   Subtitle delay up

Playlist and dialogs:
  Ctrl+L              Show/hide playlist
  R                   Random (shuffle) toggle
  L                   Cycle loop mode (normal / loop / repeat)
  Ctrl+O              Open file
  Ctrl+N              Open network stream
  Ctrl+D              Open disc
  Ctrl+P              Preferences
  Ctrl+I              Media information
  Ctrl+E              Extended settings (Adjustments and Effects)

Notes
- The VLC wiki says Shift+arrow seeks 5 seconds, but the 3.0.x source default (extrashort-jump-size) is 3 seconds; the source value is used here. The jump sizes are configurable in Preferences, so check the on-screen time if precision matters.
- Many hotkeys are shown in the Tools > Preferences > Hotkeys list; use it to confirm a binding on a given install.

## Sources

- Preferences Simple/All split and the pane list: [Preferences -- VideoLAN Wiki](https://wiki.videolan.org/Preferences).
- Keyboard shortcuts (Qt interface): [QtHotkeys -- VideoLAN Wiki](https://wiki.videolan.org/QtHotkeys/).
- Maximum volume above 100% via the Qt interface preference: cross-referenced
  community how-tos pointing at the same Preferences (All) > Interface > Main
  interfaces > Qt path (no single official KB page dedicated to this one
  setting; verify the exact current label in Preferences (All) since VLC
  occasionally renames Qt-interface options between versions).
- Convert/Save workflow for extracting audio: [VLC HowTo/Extract audio from a file -- VideoLAN Wiki](https://wiki.videolan.org/VLC_HowTo/Extract_audio_from_a_file/).
- `vlcrc` config file path (`~/.config/vlc/vlcrc` on Linux): this repo's own
  evaluator, `desktop_env/evaluators/getters/vlc.py`, which reads this exact
  path for grading.
- Keyboard shortcuts: checked against the official documentation (https://wiki.videolan.org/Hotkeys/, https://raw.githubusercontent.com/videolan/vlc/3.0.x/src/libvlc-module.c); the list started from Intelligence-Indeed's OSWorld skills (Apache-2.0), with their wrong or unconfirmed entries corrected or dropped.

## OSWorld 1.0: what is checked in VLC, and how to work so it passes

- Settings checks read VLC's configuration file (vlcrc). Change settings in Tools > Preferences and press Save,
  which is when VLC writes them; do not edit vlcrc by hand while VLC is running, since VLC writes its own copy.
- Playback checks ask the running VLC, over its own interface, what it is playing. Keep the VLC that the task
  opened running and playing the right file (playing, not paused or stopped); do not start a second VLC or
  drive it with `vlc` commands.
- Display state is judged on the running player when you stop. "Fullscreen" means the VLC window equals the
  screen size, not just maximised: use View > Fullscreen (F) and leave it that way. Setting values are
  compared as written (a folder path without a trailing slash); a key missing from vlcrc means its default.
- VLC's command line (`cvlc`) is for producing a file, such as a conversion, not for changing the open player.
- When the task asks for a result file (a flipped or rotated copy, a converted file, the audio alone) and does
  not name VLC as the tool, `ffmpeg` is installed and is the reliable way: `ffmpeg -i IN -vf hflip OUT` (mirror;
  also `vflip` upside down, `transpose=1` 90° clockwise), `ffmpeg -i IN -vn OUT.mp3`. VLC's Effects > Geometry transform only changes playback
  and is not saved; if VLC's Convert / Save writes a 0-byte file, switch to the command line instead of retrying
  the dialog.

Sources: Some of these conventions were adapted from Intelligence-Indeed's OSWorld skills (Apache-2.0); every statement about what is checked was confirmed against this repo's `desktop_env/evaluators` and task configs.
