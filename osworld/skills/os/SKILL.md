---
name: os
description: How to do Ubuntu (GNOME) system tasks through the desktop tools and bash -- GNOME settings with gsettings, date and time zone, users and permissions, files and archives, the terminal, the clipboard.
---
# Ubuntu desktop and system tasks

Most OS tasks are checked by running commands after you stop (gsettings, getent, ls, stat, file contents),
so the usual command-line tools are the reliable way to make and check the change. Run them from your
`computer_use` code with `subprocess.run(...)`; when the check reads the terminal (see below), type them into
the open terminal window instead.

## GNOME settings
- GNOME stores its settings in dconf; `gsettings get|set <schema> <key> [value]` reads and writes them and the
  running desktop applies them at once. `gsettings list-recursively <schema>` shows a schema's keys.
- Common schemas: `org.gnome.desktop.interface` (theme, fonts, text scaling, clock format),
  `org.gnome.desktop.background` (wallpaper `picture-uri`), `org.gnome.shell` (`favorite-apps`, the dock),
  `org.gnome.desktop.session` (`idle-delay`), `org.gnome.desktop.screensaver` (lock).
- The Settings app (gnome-control-center) shows the same values; use it when a task names it.
- A default application is stored per MIME type: set every type of the category the task names (all
  `audio/*` types for a music player, not only mp3), and confirm with `xdg-mime query default <type>` on several.

## Date, time and locale
- `timedatectl` shows and sets the time zone and NTP: `timedatectl list-timezones` lists zone names,
  `sudo timedatectl set-timezone Asia/Tokyo` sets one. Fixed-offset zones are `Etc/GMT±N` with the sign inverted
  (UTC+3 is `Etc/GMT-3`).
- `locale`, `localectl` for language and keyboard layout.

## Users, groups and permissions
- `sudo useradd -m -d <home> -s /bin/bash <user>`, `sudo passwd <user>` (or `echo '<user>:<pw>' | sudo chpasswd`),
  `sudo usermod -aG <group> <user>`, `sudo usermod -d <dir> <user>`; `getent passwd <user>` shows the result.
- `chmod`, `chown`, `ls -l`, `stat -c '%A %U %G' <path>` to set and check permissions and owners.
- The sudo password is given in the task prompt.

## Files, search and archives
- `find <dir> -name '<pattern>'`, then copy keeping directory structure with `cp --parents` or
  `rsync -R`; `tar`, `zip`/`unzip`, `gzip` for archives.
- `wc -l`, `grep -c`, `du -sh`, `sort | uniq -c` for counting.
- If a LibreOffice window is already open, a headless `soffice --convert-to` is handed to that running instance
  and may do nothing. Give the command its own profile: `soffice --headless
  -env:UserInstallation=file:///tmp/lo_profile --convert-to <ext> --outdir DIR FILE`. Type it in the visible
  terminal and wait for the prompt to come back before you stop: a large file can take minutes.

## Clipboard
- Under X11 the clipboard content belongs to the program that set it. `xclip -selection clipboard` (or
  `xsel --clipboard`) keeps a small background process serving it; leave that process running, and confirm with
  `xclip -o -selection clipboard`.

## Keyboard shortcuts (Linux)

When a shortcut below does what you need, prefer it to clicking through menus: a key needs no coordinates, so it cannot land on the wrong item. Confirm the result on the next screenshot.

Verified for Ubuntu GNOME against GNOME Help (shortcuts and keyboard navigation pages), the GNOME default key schemas (org.gnome.desktop.wm.keybindings, gnome-settings-daemon media-keys), GNOME Files help pages, GNOME Terminal help, and Ubuntu's command-line tutorial. Ubuntu may change defaults; Ubuntu-specific entries are marked.

Windows:
  Super+Up or Alt+F10 Maximize window
  Super+Down or Alt+F5  Restore a maximized window
  Super+Left          Tile window on the left half (maximize along the left side); press again to restore
  Super+Right         Tile window on the right half; press again to restore
  Super+H             Minimize window
  Alt+F4              Close window
  Alt+F7              Move window with arrow keys (Enter confirms, Esc cancels)
  Alt+F8              Resize window with arrow keys (Enter confirms, Esc cancels)
  Alt+Space           Open window menu
  Alt+Tab             Switch applications (GNOME schema default; Super+Tab is the documented key)
  Super+Tab           Switch windows (hold Super, Tab/Shift+Tab to cycle)
  Alt+` (key above Tab)  Switch windows of the same application (Super+` also documented)
  Alt+Esc             Cycle windows in the current workspace
  Shift+Super+Left / Right  Move window to the monitor on the left / right

Desktop and workspaces:
  Super               Toggle Activities overview / desktop (type to search apps)
  Super+A             Show applications list
  Alt+F2              Run-command dialog
  Super+L             Lock screen
  Super+V             Show notification list (Esc closes)
  Ctrl+Alt+Tab        Move keyboard focus to the top bar
  Super+Page Up / Super+Page Down   Switch workspaces
  Shift+Super+Page Up / Page Down   Move window to another workspace
  Ctrl+Alt+Delete     Session dialog (GNOME Help: Power Off dialog)
  Ctrl+Alt+T          Open a terminal (Ubuntu default)

Screenshots:
  Print               Screenshot tool
  Alt+Print           Screenshot of the current window
  Shift+Print         Screenshot of the whole screen
  Shift+Ctrl+Alt+R    Start/stop screen recording

Editing (most applications):
  Ctrl+A / Ctrl+X / Ctrl+C / Ctrl+V / Ctrl+Z   Select all / cut / copy / paste / undo

Keyboard navigation in dialogs:
  Tab / Shift+Tab     Next / previous control
  Esc                 Close menu, popup or dialog
  F10                 Open the first menu on the menubar
  Shift+F10 or Menu key   Context menu
  Alt                 Reveal underlined accelerator letters

Files (Nautilus):
  Ctrl+N              New window
  Ctrl+F              Search
  Ctrl+H              Show/hide hidden files
  F2                  Rename
  Delete              Move to Trash
  Shift+Delete        Delete permanently (asks to confirm)
  Ctrl+C / Ctrl+X / Ctrl+V   Copy / cut / paste files
  Space               Preview selected file
  Ctrl+F10            Context menu for the current folder

GNOME Terminal:
  Shift+Ctrl+T        New tab
  Shift+Ctrl+N        New window
  Shift+Ctrl+W        Close tab
  Shift+Ctrl+Q        Close window
  Shift+Ctrl+C        Copy (plain Ctrl+C interrupts the running program)
  Shift+Ctrl+V        Paste
  Shift+Ctrl+F        Find
  Shift+Ctrl+G / Shift+Ctrl+H   Find next / find previous
  Shift+Ctrl+J        Clear highlight
  F11                 Full screen
  Ctrl++ / Ctrl+- / Ctrl+0   Zoom in / out / normal size
  Ctrl+Page Up / Ctrl+Page Down   Previous / next tab
  Alt+1 to Alt+9      Switch to tab 1 to 9 (Alt+0 is tab 10)
  Shift+Page Up / Page Down   Scroll up / down one page
  Shift+Home / Shift+End      Scroll to top / bottom

## Sources

- Keyboard shortcuts: checked against the official documentation (https://help.gnome.org/users/gnome-help/stable/shell-keyboard-shortcuts.html, https://help.gnome.org/users/gnome-help/stable/keyboard-nav.html, https://help.gnome.org/users/gnome-help/stable/shell-windows-tiled.html, https://help.gnome.org/users/gnome-help/stable/shell-windows-maximize.html, https://help.gnome.org/users/gnome-help/stable/shell-windows-switching.html, https://raw.githubusercontent.com/GNOME/gsettings-desktop-schemas/master/schemas/org.gnome.desktop.wm.keybindings.gschema.xml.in, https://raw.githubusercontent.com/GNOME/gnome-settings-daemon/master/data/org.gnome.settings-daemon.plugins.media-keys.gschema.xml.in, https://help.gnome.org/users/gnome-help/stable/files-hidden.html,, https://help.gnome.org/users/gnome-terminal/stable/adv-keyboard-shortcuts.html, https://ubuntu.com/tutorials/command-line-for-beginners); the list started from Intelligence-Indeed's OSWorld skills (Apache-2.0), with their wrong or unconfirmed entries corrected or dropped.

## OSWorld 1.0: what is checked in OS tasks

- OS checks run shell commands after you stop -- `gsettings get`, `getent passwd`, `ls`, `stat`, reading the
  files you were asked to produce, or the terminal's history and output. The result has to be in that system
  state, not only shown on screen.
- When a task says to do something in the terminal or from the command line, the check may read the terminal's
  history; type those commands into the visible terminal window, not in a hidden `subprocess`.
- When an SSH user is limited to one directory, make that directory the user's home (`useradd -d <dir>` or
  `usermod -d <dir>`), so their sessions start there; do not create a separate default home for them unless the
  task asks.
- "The current directory" is the directory the already-open terminal is in; run the commands in that visible
  terminal, and leave their output on screen.
- Clipboard checks read the clipboard after you stop; see "Clipboard" above for keeping its content alive.

Sources: Some of these conventions were adapted from Intelligence-Indeed's OSWorld skills (Apache-2.0); every
statement about what is checked was confirmed against this repo's `desktop_env/evaluators` and task configs.
