---
name: vscode
description: How to operate Visual Studio Code through the desktop tools -- the Command Palette as the way into almost every setting, settings.json/keybindings.json locations, extensions (including VSIX installs), multi-root workspaces, and shortcuts for files/find/terminal/editor. Reference knowledge for any VS Code task, not a script for one.
---

# Visual Studio Code

Most VS Code tasks are really
**settings or extension changes with an obscure name** -- the Command Palette
turns "find the right menu" into "search for the setting," and is the single
most useful tool here.

## Command Palette: the way in

- Open with **Ctrl+Shift+P** (or F1). Type a few words of what you want; VS
  Code fuzzy-matches command names, so "display lang", "install vsix",
  "open settings json" all work without knowing the exact command title.
- Two settings surfaces: **Preferences: Open Settings (UI)** (searchable GUI,
  what Ctrl+, opens) and **Preferences: Open User Settings (JSON)** (raw
  `settings.json` -- faster and unambiguous for a setting you already know the
  key for, e.g. `"editor.tabSize": 2`).
- Custom keybindings: **Preferences: Open Keyboard Shortcuts (JSON)** opens
  `keybindings.json` directly -- add an entry
  `{"key": ..., "command": ..., "when": ...}` rather than hunting the Keyboard
  Shortcuts UI for the right command id. A binding meant for one context
  needs its `when` clause, e.g. `"when": "editorTextFocus"`. Removing a
  default binding is an entry whose command starts with `-` (for example
  `"command": "-workbench.action.files.save"`), which the Keyboard Shortcuts UI's Remove
  Keybinding writes.
- **Configure Display Language** changes the UI language. Every language
  other than English is a language-pack extension; without the pack,
  setting `locale` changes nothing.

## Where the config files live (Linux, this environment)

- User settings: `~/.config/Code/User/settings.json`
- Keybindings: `~/.config/Code/User/keybindings.json`
- Workspace-specific settings (only when a folder/workspace is open):
  `<workspace-root>/.vscode/settings.json`
- A saved multi-root workspace is one JSON file with a `.code-workspace`
  extension, containing a `folders` array of paths plus optional `settings`.

## Extensions

- Install from the Extensions view (Ctrl+Shift+X), or faster: Command
  Palette > **Extensions: Install from VSIX...**, then pick the `.vsix` file
  -- this is the reliable path when a task hands you a local VSIX rather than
  a marketplace listing.
- The same view lists, disables, and uninstalls extensions; a disabled/newly
  installed extension often prompts "Reload Required."
- Language support such as running and debugging Python comes from an
  extension (Python, by Microsoft) that a fresh install does not have; install
  it from the Extensions view. With it, Run and Debug (Ctrl+Shift+D) runs the
  file, and while it is paused the VARIABLES pane lists the program's
  variables.

## Shortcuts

- File: New **Ctrl+N**, Open **Ctrl+O**, Save **Ctrl+S**, Save As
  **Ctrl+Shift+S**, Close editor **Ctrl+F4**, Quick Open a file by name
  **Ctrl+P**.
- Find/replace: Find **Ctrl+F**, Replace **Ctrl+H**, Find in Files
  **Ctrl+Shift+F**, Replace in Files **Ctrl+Shift+H** -- for "change every
  occurrence of X to Y in this document," Replace (Ctrl+H) with "Replace All"
  is far more reliable than manual edits.
- Terminal: toggle the integrated terminal with **Ctrl+`**; focus moves
  there when opened, and clicking the editor or **Ctrl+1** sends it back.
- Window/workspace: new window **Ctrl+Shift+N**, split editor **Ctrl+\\**,
  toggle sidebar **Ctrl+B**, switch editor group **Ctrl+1/2/3**.
- Multi-root workspaces: File > **Add Folder to Workspace...** adds a folder
  to the current workspace without replacing it; it does not open a second
  workspace file in the window. File > **Save Workspace As...** persists the set
  as a `.code-workspace` file.

## Reliable verification (via shell commands)

Most settings/keybinding/workspace tasks are graded by reading the JSON files
above directly, not by screen state -- so after making a change, `cat` or
`python3 -c "import json; ..."` the relevant file to confirm the exact key and
value landed, the same way `inspect_file` is preferred over reading a
rendered spreadsheet cell in the Calc skill.

## Keyboard shortcuts (Linux)

When a shortcut below does what you need, prefer it to clicking through menus: a key needs no coordinates, so it cannot land on the wrong item. Confirm the result on the next screenshot.

This list is trimmed for size. Other Linux PDF entries (for example Ctrl+Shift+K delete line, Ctrl+L select line, Ctrl+Shift+M Problems, Alt+Z word wrap, F8 next problem) are also official but omitted.

### General and files
- Ctrl+Shift+P or F1: Command Palette
- Ctrl+P: Quick Open (go to file)
- Ctrl+,: open Settings
- Ctrl+K Ctrl+S: open Keyboard Shortcuts
- Ctrl+N: new file
- Ctrl+O: open file
- Ctrl+S: save
- Ctrl+Shift+S: save as
- Ctrl+W: close editor
- Ctrl+Shift+T: reopen closed editor

### Editing
- Ctrl+Z: undo
- Ctrl+Y: redo
- Alt+Down / Alt+Up: move line down / up
- Ctrl+Shift+Alt+Down / Ctrl+Shift+Alt+Up: copy line down / up (Linux only; on Windows it is Shift+Alt+Down/Up)
- Ctrl+/: toggle line comment
- Ctrl+Shift+A: toggle block comment (Linux only; Windows is Shift+Alt+A)
- Ctrl+Shift+I: format document
- F2: rename symbol
- Ctrl+.: quick fix

### Search and replace
- Ctrl+F: find
- Ctrl+H: replace
- F3 / Shift+F3: find next / previous
- Alt+Enter: select all occurrences of the Find match
- Ctrl+Shift+F: search across files (shows the Search view)

### Multi-cursor and selection
- Alt+Click: insert cursor (the PDF warns it may not work on some Linux distributions; set "editor.multiCursorModifier" to Ctrl+Click to change the modifier)
- Shift+Alt+Up / Shift+Alt+Down: insert cursor above / below (Linux only; Windows is Ctrl+Alt+Up/Down)
- Ctrl+D: add next occurrence of the selection
- Ctrl+Shift+L: select all occurrences of the current selection

### Navigation
- Ctrl+G: go to line
- Ctrl+Shift+O: go to symbol in file
- F12: go to definition
- Ctrl+Shift+F10: peek definition (Linux only; Windows is Alt+F12)
- Shift+F12: show references
- Ctrl+Alt+-: navigate back (Linux only; Windows is Alt+Left)
- Ctrl+Shift+-: navigate forward (Linux only; Windows is Alt+Right)

### View and layout
- Ctrl+B: toggle sidebar
- Ctrl+Shift+E: Explorer
- Ctrl+Shift+G: Source Control
- Ctrl+Shift+D: Run and Debug
- Ctrl+Shift+X: Extensions
- Ctrl+\: split editor

### Integrated terminal
- Ctrl+`: show/toggle the integrated terminal
- Ctrl+Shift+`: new terminal
- Ctrl+Shift+5: split terminal
- Ctrl+Shift+C / Ctrl+Shift+V: copy / paste inside the terminal (Linux; plain Ctrl+C is the interrupt signal)

### Debug
- F5: start / continue
- Shift+F5: stop
- F9: toggle breakpoint
- F10: step over
- F11 / Shift+F11: step into / step out

## Sources

- Command Palette, Settings UI vs. `settings.json`, and file locations:
  [User and workspace settings -- VS Code Docs](https://code.visualstudio.com/docs/configure/settings).
- Default keyboard shortcuts (Windows/Linux): [Default keyboard shortcuts reference -- VS Code Docs](https://code.visualstudio.com/docs/reference/default-keybindings).
- Installing an extension from a local VSIX: [Extension Marketplace -- VS Code Docs](https://code.visualstudio.com/docs/configure/extensions/extension-marketplace).
- Configure Display Language behaviour: [Display Language -- VS Code Docs](https://code.visualstudio.com/docs/configure/locales).
- Task shapes (VSIX install, settings, custom keybinding, multi-root
  workspace, display language) drawn from this repo's own task set,
  `evaluation_examples/examples/vs_code/*.json`.
- Keyboard shortcuts: checked against the official documentation (https://code.visualstudio.com/shortcuts/keyboard-shortcuts-linux.pdf, https://code.visualstudio.com/docs/reference/default-keybindings, https://code.visualstudio.com/docs/getstarted/keybindings, https://code.visualstudio.com/docs/terminal/basics, https://code.visualstudio.com/docs/getstarted/userinterface); the list started from Intelligence-Indeed's OSWorld skills (Apache-2.0), with their wrong or unconfirmed entries corrected or dropped.

## OSWorld 1.0: real VS Code features only

Editing `settings.json` or `keybindings.json` is the fast way to set
something VS Code has a setting or command for; it does not create a feature
VS Code lacks. Before writing JSON for a behaviour, look for it in the
Settings search and the Command Palette (rule 2 applies when neither has it).

### What is checked

- Checks read settings.json and keybindings.json, the installed-extension list, the files you saved, and for some
  tasks they run a command inside the open VS Code window through the Command Palette. Leave VS Code open and in
  front with the right folder open and no dialog showing.
- Settings checks read the User `settings.json`, not a folder's `.vscode/settings.json` (choose the User tab in
  Settings, or Preferences: Open User Settings (JSON)). Both JSON files are read with a strict parser: no
  comments and no trailing commas (one comment line at the very top of `keybindings.json` is tolerated). Each
  key needs the exact value and type (a number, not a string), and an object-valued setting is compared as a
  whole. A keybinding entry must match in every field, including `when`, with no extra fields; removing a
  keybinding is an entry whose command starts with `-`.
- A text file you edited is compared exactly with the expected text: change only what the task asks, and leave
  whitespace, line endings and the final newline alone. Make it the active editor, in front, with no dialog.
- Open folders and files in the running window (File > Open Folder, Ctrl+O). A `code <path>` run while a VS Code
  window is already open can open a second window that the checks do not look at (`code -r <path>` reuses the
  window). When the task asks for the terminal, or no VS Code window is open yet, `code <path>` from the
  terminal is fine.

Sources: Some of these conventions were adapted from Intelligence-Indeed's OSWorld skills (Apache-2.0); every statement about what is checked was confirmed against this repo's `desktop_env/evaluators` and task configs.
