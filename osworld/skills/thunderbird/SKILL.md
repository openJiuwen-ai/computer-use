---
name: thunderbird
description: How to operate Mozilla Thunderbird through the desktop tools -- account settings (signatures, forwarding, incoming/outgoing servers), Local Folders vs. IMAP folders, message filters, themes, the profile manager, and shortcuts for compose/reply/mark/search. Reference knowledge for any Thunderbird task, not a script for one.
---

# Mozilla Thunderbird

Most Thunderbird tasks are
account/folder/filter configuration rather than composing mail, and almost
everything configurable lives in **Account Settings** (per-account, left-hand
tree) rather than a single global Preferences dialog.

## Account Settings: the hub

Open via the menu button (top-right, "hamburger") > Account Settings, or
Tools > Account Settings on a visible menu bar. It's per-account -- each
account in the left column has its own Server Settings, Copies & Folders,
Composition & Addressing (signature lives here), and Junk Settings; there's
also a global "Local Folders" entry (see below) and an
"Outgoing Server (SMTP)" entry shared across accounts.
- Every mail account has an incoming server (IMAP or POP3); the account
  setup has no send-only option. An Outgoing Server (SMTP) entry sends mail
  only through a mail account that uses it. A placeholder incoming server is
  a workaround users resort to, not a send-only feature.
- **Signature**: select the account > the "Signature text" field on its main
  settings page; a plain-text signature works for both HTML and plain-text
  outgoing mail, no extra toggle needed.
- **Forwarding incoming mail**: either the account's Server Settings has no
  direct "forward" toggle for POP/IMAP -- the reliable way is a **message
  filter** with a "Forward" action (see below), which also works
  per-condition rather than for every message.
- **Removing an account**: Account Settings > select the account > the gear
  icon at the bottom > Remove Account.
- **Checking multiple accounts on one interval**: each account's Server
  Settings has "Check for new messages every N minutes" -- set per account,
  there's no single global switch for all of them at once.

## Local Folders vs. IMAP/POP folders

- **Local Folders** is a single, always-present root (not tied to any mail
  account) for mail you keep only on this machine. You can only create
  **subfolders** under it (right-click "Local Folders" > New Folder) -- you
  cannot create a second separate "Local Folders" root without an add-on.
  Its on-disk location is set in Account Settings > Local Folders > Local
  Directory.
- Folders under a real account (right-click the account or an existing
  folder > New Folder) live on that account's server (IMAP) instead.

## Message filters

Tools > Message Filters (via the menu button), or select a message first and
use Message > "Create Filter From Message" to prefill the sender/subject
condition. A filter has conditions (sender, subject, folder it arrived in,
etc.) and actions (move to folder, forward, mark read, star, ...). **Filters
belong to one account** (the "Filters for" list at the top of the dialog), so a
filter meant to catch mail in every account has to be created in each
account's list; "Run Now" applies the selected filters to the folder chosen
beside it.

## Themes and appearance

Menu button > Add-ons and Themes > Themes tab lists built-in Light/Dark/
System themes and any installed ones -- enabling a theme there is the
supported route (there's no single "dark mode" checkbox elsewhere in
Preferences).

## Profile manager

The "Choose User Profile" picker is a startup dialog: run `thunderbird -P` (or
`-ProfileManager`) with Thunderbird **not already running**. A separate
"About Profiles" page also opens inside Thunderbird as a tab (Help >
Troubleshooting Information); use that when the task asks for the profile page
inside the app.

## Reliable verification (via shell commands)

The profile lives at `~/.thunderbird/<random>.default-release/` (the prefix
is randomized per install, so glob for it). Useful files, all plain
text/SQLite -- no need to screenshot the UI to confirm state:
- `prefs.js` -- most settings as `user_pref("key", value);` lines, including
  signatures, filters-enabled flags, and check-interval settings.
- `global-messages-db.sqlite` (Gloda index) -- queryable with `sqlite3` for
  message state such as starred/tagged/read flags across folders.
- `Mail/Local Folders/<FolderName>` -- Local Folders mail as flat mbox files;
  `ImapMail/<account>/<Folder>` for IMAP-cached mail.
- `msgFilterRules.dat` (per account, under `ImapMail/<account>/` or
  `Mail/Local Folders/`) -- the actual filter definitions as plain text.

## Keyboard shortcuts (Linux)

When a shortcut below does what you need, prefer it to clicking through menus: a key needs no coordinates, so it cannot land on the wrong item. Confirm the result on the next screenshot.

Verified against Thunderbird Help "Keyboard shortcuts" (last updated 4/2/26). Shortcuts are context sensitive: the same key can act differently in the main window and in the compose window, and single-letter keys need the message list or message pane focused, not a text field.

Message functions (main window):
  Ctrl+N              New message
  Ctrl+R              Reply to sender
  Ctrl+Shift+R        Reply all
  Ctrl+Shift+L        Reply to list
  Ctrl+L              Forward
  Ctrl+O              Open message (new tab/window)
  F5                  Get new messages for current account
  Shift+F5            Get new messages for all accounts
  Ctrl+P              Print
  Ctrl+U              View message source
  Ctrl+A              Select all messages
  Ctrl+Shift+A        Select thread (main window)

Managing messages:
  A                   Archive message
  Delete              Delete message
  Shift+Delete        Delete bypassing Trash (no undo)
  M                   Mark read/unread
  R                   Mark thread as read
  Shift+C             Mark all read
  J                   Mark as junk
  Shift+J             Mark as not junk
  S                   Add/remove star
  1 to 9              Add/remove tag 1 to 9
  0                   Remove all tags
  Ctrl+Z / Ctrl+Y     Undo / redo

Moving between messages:
  F                   Next message
  B                   Previous message
  N                   Next unread message
  P                   Previous unread message
  T                   Next unread thread (marks current thread read)
  Space               Scroll message down, then go to next unread
  ]                   Next viewed message (history)
  [                   Previous viewed message (history)
  Right / Left arrow  Expand / collapse thread
  * and \             Expand all threads / collapse all threads

Search and find:
  Ctrl+K              Search all messages (global search)
  Ctrl+Shift+K        Quick Filter
  Esc                 Clear quick filter and hide the bar
  Ctrl+Shift+F        Advanced message search
  Ctrl+F              Find in current message
  Ctrl+G or F3        Find again
  Ctrl+Shift+G or Shift+F3  Find previous

Windows, tabs, panes:
  F6 / Shift+F6       Move to next / previous main UI area (folder pane, message list, message pane)
  F8                  Toggle message pane
  Ctrl+Tab            Next tab
  Ctrl+Shift+Tab      Previous tab
  Ctrl+W              Close tab/window
  Ctrl+Shift+T        Undo close tab
  Ctrl++ / Ctrl+- / Ctrl+0   Zoom in / out / reset
  Ctrl+Shift+B        Address Book
  Ctrl+Q              Quit

Compose window:
  Ctrl+Enter          Send now
  Ctrl+Shift+Enter    Send later
  Ctrl+S              Save draft
  Ctrl+Shift+A        Attach file (also the paperclip button in the compose toolbar)
  Ctrl+Shift+V        Paste without formatting
  Ctrl+K              Insert link
  Ctrl+Shift+P        Check spelling

## Sources

- Shortcuts: official Mozilla KB page [Keyboard shortcuts -- Thunderbird Help](https://support.mozilla.org/en-US/kb/keyboard-shortcuts-thunderbird) (cross-checked against two independent shortcut compilations, [seanh.cc](https://www.seanh.cc/2018/01/21/thunderbird-keyboard-shortcuts/) and [keycombiner.com](https://keycombiner.com/collections/thunderbird/)).
- Signatures: [Signatures -- Thunderbird Help](https://support.mozilla.org/en-US/kb/signatures).
- Profile manager (`-P` / `-ProfileManager`): [Profile Manager -- Create and remove Thunderbird profiles -- Thunderbird Help](https://support.mozilla.org/en-US/kb/profile-manager-create-and-remove-thunderbird-profiles).
- Filters: [Organize your messages with filters -- Thunderbird Help](https://support.mozilla.org/en-US/kb/organize-your-messages-using-filters).
- Local Folders behaviour (single root, subfolders only): Mozilla Support forum thread [multiple local folders possible?](https://support.mozilla.org/en-US/questions/1543604).
- Profile path, `global-messages-db.sqlite`, and the mbox layout:
  [Profiles - Where Thunderbird stores your messages and other user data -- Thunderbird Help](https://support.mozilla.org/en-US/kb/profiles-where-thunderbird-stores-user-data);
  this repo's own task configs/evaluator for Thunderbird
  (`evaluation_examples/examples/thunderbird/*.json`), one of which reads
  `~/.thunderbird/<profile>.default-release/global-messages-db.sqlite` directly for grading.
- No send-only accounts: Mozilla Support [question 1330219](https://support.mozilla.org/gn/questions/1330219) ("at least one pop or imap mail account") and the open Mozilla Connect idea [submit-only accounts](https://connect.mozilla.org/t5/ideas/submit-mail-via-custom-sendmail-command/idi-p/114277).
- Keyboard shortcuts: checked against the official documentation (https://support.mozilla.org/en-US/kb/keyboard-shortcuts-thunderbird); the list started from Intelligence-Indeed's OSWorld skills (Apache-2.0), with their wrong or unconfirmed entries corrected or dropped.

## OSWorld 1.0: real Thunderbird features only

Set things up through Thunderbird's own account setup and settings; a hand-edited
`prefs.js` is not that. Settings > General > Config Editor (about:config) is
Thunderbird's own settings UI: setting a preference there, by its exact name, is
not hand-editing files, and it is the place for options with no ordinary
settings control.

### Where mail state lives

- Stars, tags, read state and moved messages are recorded in Thunderbird's message database and folder indexes,
  which the checks read. Act on messages inside Thunderbird; editing mbox files does not update them.
- To save messages as .eml files, select them in Thunderbird and use Save As > File: Thunderbird names each file
  after the message subject, which is what the checks look for.
- Settings are written by Thunderbird itself into `prefs.js` and `xulstore.json`, and the checks close the main
  window first so the files are complete. A preference set back to its default is not written.
- Filter checks read the account's `msgFilterRules.dat` and compare fields exactly: create the filter under the
  account the task names (the "Filter for" choice in Message Filters), with exactly the condition and action
  the task states and no extra conditions, then click OK and close the Message Filters window. A folder the
  task asks for under Local Folders is created there with New Folder.

### Leave the window in the state asked for

When a task says to stay on a page, not to send, or not to close, the check reads the live window: a composed
message stays open and unsent, with each recipient committed in the To field (type the address, then Enter or
a comma) and any attachment still attached; a settings or profile page stays open on screen.

Sources: Some of these conventions were adapted from Intelligence-Indeed's OSWorld skills (Apache-2.0); every statement about what is checked was confirmed against this repo's `desktop_env/evaluators` and task configs.
