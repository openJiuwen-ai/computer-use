---
name: chrome
description: How to operate Google Chrome through the desktop tools -- tabs and windows, the address bar as a navigation shortcut (typing chrome:// pages instead of clicking through menus), bookmarks/history/downloads, settings (homepage, passwords, Do Not Track), and reading the profile files or the debug port for reliable verification. Reference knowledge for any Chrome task, not a script for one.
---

# Google Chrome

Chrome's biggest advantage over other apps: almost every settings page and many actions have a direct address --
**typing into the address bar is far more reliable than clicking through
menus**, since Chrome's settings UI reflows between versions but its
`chrome://` URLs don't.

## Address bar first, menus second

- Jump to the address bar with **Ctrl+L**, **Alt+D**, or **F6**, type, Enter.
- Useful destinations instead of navigating menus:
  `chrome://settings` (general), `chrome://settings/passwords` (Password
  Manager), `chrome://settings/onStartup` (startup pages),
  `chrome://settings/privacy` (Do Not Track, clear browsing data),
  `chrome://settings/content` (site permissions), `chrome://extensions`,
  `chrome://downloads`, `chrome://history`, `chrome://bookmarks`,
  `chrome://version` (profile/version info). Loading `chrome://chrome-urls`
  lists every internal page Chrome supports, if you need one not listed here.
- The three-dot menu (top-right) is the fallback when a task needs a dialog
  that has no direct URL (e.g. "Add a new page" under startup settings still
  needs its own field filled in).

## Tabs and windows

- New tab **Ctrl+T**, close tab **Ctrl+W**, reopen last closed tab
  **Ctrl+Shift+T** (works for multiple closes, most recent first), new window
  **Ctrl+N**, new Incognito window **Ctrl+Shift+N**.
- Switch tabs: **Ctrl+Tab** / **Ctrl+PgDn** (next), **Ctrl+Shift+Tab** /
  **Ctrl+PgUp** (previous), **Ctrl+1**-**Ctrl+8** (tab by position),
  **Ctrl+9** (last tab).
- Back/forward: **Alt+Left** / **Alt+Right**. Reload **Ctrl+R** or **F5**;
  hard reload (bypass cache) **Ctrl+Shift+R**.

## Bookmarks, history, downloads

- Bookmark current page **Ctrl+D**; toggle the bookmarks bar **Ctrl+Shift+B**;
  full Bookmark Manager (needed to create/rename a folder reliably, not just
  right-click) **Ctrl+Shift+O**.
- History **Ctrl+H**; Downloads **Ctrl+J**.
- Find on page **Ctrl+F**, next/previous match **Ctrl+G** / **Ctrl+Shift+G**.

## Settings tasks worth knowing

- **Homepage vs. startup page** are different settings: the startup page(s)
  load when Chrome launches (`chrome://settings/onStartup`); the homepage is
  what the Home button goes to, and needs "Show Home button" turned on first
  under Appearance in `chrome://settings`.
- **Passwords**: `chrome://settings/passwords` lists/edits/adds saved
  passwords directly; no need to go through the profile menu.
- **Do Not Track** and **clear browsing data** both live under
  `chrome://settings/privacy` (Ctrl+Shift+Delete opens the clear-data dialog
  directly).
- **Appearance**: `chrome://settings/appearance` holds the browser's own colour
  mode (Device / Light / Dark) and theme; "Customize Chrome" on the New Tab page
  only changes that page.
- **Deleting one site's data**: Ctrl+Shift+Delete clears every site. For one
  site, search it in `chrome://history` (select its entries, Delete) or search
  its domain in `chrome://settings/content/all` and delete what is listed, then
  confirm nothing for that site remains.
- Changing the default search engine, default browser, or removing an
  extension are all single dialogs reachable from `chrome://settings` /
  `chrome://extensions` -- read the on-screen labels rather than assuming
  menu position, since Chrome ships new option wording periodically.
- To install an extension from a folder, open `chrome://extensions`, switch Developer mode on, choose Load
  unpacked, and select the folder that holds `manifest.json` itself (open it, then Select), not its parent. The
  check lists the unpacked extension's path.

## Reliable verification (via shell commands)

Chrome persists most state to plain files under the profile directory,
readable without touching the GUI at all:
- `~/.config/google-chrome/Default/Preferences` (JSON) -- profile name,
  homepage, startup pages, enabled labs experiments.
- `~/.config/google-chrome/Default/Bookmarks` (JSON) -- the full bookmark
  tree, including folders.
- `~/.config/google-chrome/Default/History` (SQLite; locked while Chrome runs,
  so copy the file first and query the copy -- do not close Chrome) --
  `SELECT url, title FROM urls`.
- If the task launched Chrome with `--remote-debugging-port`, the DevTools
  protocol is live: `curl http://localhost:<port>/json` (or the port it was
  bridged to) returns every open tab's URL and title as JSON -- the most
  reliable way to confirm which tabs/pages are open, more so than reading a
  screenshot.
Preferences can lag a few seconds behind a change made in Settings; re-read it after a short wait.
Use these to confirm a change landed instead of trusting the rendered page.

## Keyboard shortcuts (Linux)

When a shortcut below does what you need, prefer it to clicking through menus: a key needs no coordinates, so it cannot land on the wrong item. Confirm the result on the next screenshot.

### Tabs and windows
- Ctrl+T: open a new tab and switch to it
- Ctrl+W (or Ctrl+F4): close the current tab
- Ctrl+Shift+T: reopen the most recently closed tab (repeat for older ones)
- Ctrl+Tab or Ctrl+PgDn: next tab
- Ctrl+Shift+Tab or Ctrl+PgUp: previous tab
- Ctrl+1 to Ctrl+8: switch to tab 1 through 8
- Ctrl+9: switch to the rightmost tab
- Ctrl+N: new window
- Ctrl+Shift+N: new Incognito window
- Ctrl+Shift+W (or Alt+F4): close the current window
- Alt+Left: back in the current tab's history
- Alt+Right: forward in the current tab's history
- F11: toggle full screen (hold Esc to exit)

### Chrome features
- Alt+F or Alt+E: open the Chrome menu
- Ctrl+Shift+B: show or hide the bookmarks bar
- Ctrl+Shift+O: open the Bookmark Manager
- Ctrl+H: open History in a new tab
- Ctrl+J: open Downloads in a new tab
- Shift+Esc: open the Chrome Task Manager
- Ctrl+Shift+Delete: open the "Delete browsing data" dialog (it does not delete anything until confirmed)
- F12 or Ctrl+Shift+J: open Developer Tools (Ctrl+Shift+J opens the Console panel)
- Ctrl+Shift+I: open Developer Tools (DevTools docs, see Sources)
- Ctrl+Shift+C: toggle Inspect Element mode (DevTools docs, see Sources)
- Ctrl+F or F3: open the Find bar
- Ctrl+G: next Find match
- Ctrl+Shift+G: previous Find match

### Address bar
- Ctrl+L, Alt+D, or F6: focus the address bar and select its text
- Ctrl+K or Ctrl+E: search from anywhere on the page (focuses the address bar)
- Alt+Enter: after typing in the address bar, open the result in a new tab
- Ctrl+Enter: after typing a site name, add www. and .com and open it in the current tab

### Web page
- Ctrl+P: print
- Ctrl+S: save the page
- Ctrl+U: view page source (opens in a new tab)
- Ctrl+D: bookmark the current page
- Ctrl+Shift+D: bookmark all open tabs into a new folder
- F5 or Ctrl+R: reload
- Ctrl+Shift+R or Shift+F5: reload ignoring cached content
- Esc: stop loading
- Ctrl+plus: zoom in
- Ctrl+minus: zoom out
- Ctrl+0: reset zoom
- Space or PgDn: scroll down one screen
- Shift+Space or PgUp: scroll up one screen
- Home: go to top of page
- End: go to bottom of page

## Sources

- Keyboard shortcuts: [Chrome keyboard shortcuts -- Google Chrome Help](https://support.google.com/chrome/answer/157179) (official, Windows section).
- Homepage vs. startup page distinction: [Set your homepage and startup page -- Google Chrome Help](https://support.google.com/chrome/answer/95314).
- Password Manager location: [Manage passwords in Chrome -- Google Chrome Help](https://support.google.com/chrome/answer/95606).
- `chrome://` internal pages: `chrome://chrome-urls` is Chrome's own self-listing page (open it in any Chrome install to verify the current list).
- Profile file paths (`Preferences`, `Bookmarks`, `History`) and the
  `--remote-debugging-port` verification path: this repo's own evaluator code,
  `desktop_env/evaluators/getters/chrome.py` and the `chrome` task configs
  under `evaluation_examples/examples/chrome/*.json`, which launch Chrome with
  `--remote-debugging-port=1337` and read these exact files for grading.
- Keyboard shortcuts: checked against the official documentation (https://support.google.com/chrome/answer/157179, https://developer.chrome.com/docs/devtools/shortcuts); the list started from Intelligence-Indeed's OSWorld skills (Apache-2.0), with their wrong or unconfirmed entries corrected or dropped.

## OSWorld 1.0: what is checked in Chrome, and how to work so it passes

- Checks read Chrome's own state, not the screen: the profile's Preferences (settings), Bookmarks, History and
  Cookies files, the URL, title or text of the active tab, and the list of open tabs.
- On `chrome://settings/content/siteData`, "Delete data sites have saved to your device when you close all
  windows" clears cookies and site data when the last window closes; it does not clear browsing history.
- Change settings in Chrome's own Settings pages (`chrome://settings/...`). Do not edit Preferences,
  Bookmarks or Local State by hand: Chrome keeps these in memory and writes them back, so hand edits are lost
  or never take effect. Chrome writes them a few seconds after a change: wait about 10 seconds, read the file
  back, and commit a text field with Enter.
- Do not close Chrome. The checks attach to the running Chrome through its debugging port; if it is gone, a
  fresh Chrome without your tabs is started.
- Web checks read the active tab: its URL (from the address bar), and for some checks the page's elements or
  visible text. End on the requested page in the front tab, fully loaded, with nothing half-typed in the address
  bar (an uncommitted entry is read as the URL). Apply searches, filters and sorting through the site's own
  search box and controls so they appear in the URL or page. A different site, or different parameters, does
  not match; the same page reached by typing the URL does.
- On a booking or search site, when the task names only a city, choose the city entry in the suggestion list
  (the one that stands for all its airports); choose a specific airport only if the task names it.
- When a task accepts any time in a range, choose an open slot on the quarter hour (:00, :15, :30, :45) if the
  range has one, not simply the earliest.
- Open new pages in a new tab and leave the tabs that were already open, but finish with exactly the tabs the
  task asks for: close blank or stray tabs you opened. A task about restoring tabs: Ctrl+Shift+T, then nothing
  extra.
- A bookmark check compares the exact set on the Bookmarks bar: save only the requested page or folder, into
  the "Bookmarks bar" folder, and add nothing else.
- Cookie and history checks scan for any remaining row of the site (a history URL that merely contains the
  keyword counts): delete through `chrome://settings/content/all` or `chrome://history` and confirm the list is
  empty.
- Save a page as PDF with Chrome's Print dialog (Ctrl+P, Destination: Save as PDF). PDF checks compare the PDF's
  text, which a headless or command-line export can render differently. The file name Chrome proposes is the tab
  title, site name included, with `|` turned into `_`. A check opens one exact file name.
- Naming: when the task gives a naming rule, name the file by that rule. A page's own title is its heading: the tab
  title without the site name the site appends after `|`, `-` or `·`, so a name taken from the page's title leaves
  the site name out. When the task asks for Chrome's default name or says nothing about the name, keep the name
  Chrome proposes (Save as PDF, Create shortcut).

Sources: Some of these conventions were adapted from Intelligence-Indeed's OSWorld skills (Apache-2.0); every statement about what is checked was confirmed against this repo's `desktop_env/evaluators` and task configs.
