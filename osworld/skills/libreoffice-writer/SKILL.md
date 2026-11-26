---
name: libreoffice-writer
description: How to operate LibreOffice Writer through the desktop tools -- moving and selecting text, paragraph and character formatting, styles, lists, tables, headers/footers, find and replace, saving, and reading exact paragraph and run formatting with inspect_file. Reference knowledge for any Writer task, not a script for one.
---

# LibreOffice Writer

## Moving and selecting text

- Arrow keys move the caret; Ctrl+Arrow moves by word; Home/End go to line
  start/end; Ctrl+Home / Ctrl+End to document start/end. Page Up/Down scroll.
- Shift with any of the above extends the selection. Ctrl+A selects all;
  double-click selects a word, triple-click a sentence/paragraph.
- To place the caret precisely, click the text on the canvas using coordinates
  read from the screenshot.

## Character and paragraph formatting

- Direct formatting applies to the current selection: Ctrl+B bold, Ctrl+I
  italic, Ctrl+U underline; Ctrl+L/E/R/J left/center/right/justify.
- **Format > Character** (Alt+O then C) sets font family, size, style, color
  and effects on the selection. **Format > Paragraph** sets alignment, indents,
  and line/paragraph spacing.

## Styles

- Paragraph styles are the durable way to format: the Set Paragraph Style box
  is at the far left of the formatting toolbar; click it, type a style name
  (e.g. `Heading 1`, `Default Paragraph Style`), and Enter to apply it to the
  current paragraph. F11 opens the Styles sidebar for the full list.
- Applying a style is not the same as direct formatting; a task asking to
  "use Heading 1" wants the style, which `inspect_file` reports as the
  paragraph's style name.
- "Default font" can mean two things: the font of the Default Paragraph Style
  (Format > Styles > Edit Style) or the application-wide Basic Fonts list in
  Tools > Options > LibreOffice Writer > Basic Fonts (Western). Use the one the
  wording points to; if it says the default font of the application, change
  Basic Fonts.

## Lists, tables, and fields

- Toggle a bulleted or numbered list from the toolbar or Format > Bullets and
  Numbering. Tab / Shift+Tab change list level while in a list item.
- **Table > Insert Table** (Ctrl+F12) creates a table; Tab moves to the next
  cell, arrow keys move between cells. The Table menu handles insert/delete
  rows and columns and merging.
- Insert > Header and Footer adds a header/footer; Insert > Field > Page Number
  inserts a page number. Insert > Hyperlink (or the Insert menu) links selected
  text.

## Find, replace, and saving

- Ctrl+H is Find & Replace (tick "Regular expressions" for patterns). Ctrl+F is
  the quick find toolbar.
- Ctrl+S saves. Reopen a closed file with Ctrl+O.

## Keyboard shortcuts (Linux)

When a shortcut below does what you need, prefer it to clicking through menus: a key needs no coordinates, so it cannot land on the wrong item. Confirm the result on the next screenshot.

Verified against the LibreOffice 7.6 and latest Help pages. Keys the desktop (GNOME / input method) grabs first are not delivered to LibreOffice (for example Ctrl+Alt+Up/Down can switch workspaces). If a key does nothing, use the menu.

### Document and general
| Key | Effect |
|---|---|
| Ctrl+N / Ctrl+O / Ctrl+S | New / Open / Save |
| Ctrl+F4 | Close the current document |
| Ctrl+Z | Undo |
| Ctrl+Y | Redo |
| Ctrl+C / Ctrl+X / Ctrl+V | Copy / Cut / Paste |
| Ctrl+Shift+V | Paste Special dialog |
| Ctrl+Alt+Shift+V | Paste as unformatted text |
| Ctrl+A | Select all |
| Ctrl+F | Show the Find toolbar |
| Ctrl+H | Find & Replace dialog |

### Character formatting
| Key | Effect |
|---|---|
| Ctrl+B / Ctrl+I / Ctrl+U | Bold / Italic / Underline |
| Ctrl+D | Double underline |
| Ctrl+Shift+P | Superscript |
| Ctrl+Shift+B | Subscript |
| Ctrl+M | Remove direct formatting |
| Ctrl+Shift+Space | Non-breaking space |

### Paragraph formatting and styles
| Key | Effect |
|---|---|
| Ctrl+L / Ctrl+E / Ctrl+R / Ctrl+J | Align left / center / right / justify |
| Ctrl+0 | Apply Body Text paragraph style |
| Ctrl+1 .. Ctrl+5 | Apply Heading 1 .. Heading 5 |
| Ctrl+F11 | Focus the Apply Style box |
| F11 | Show or hide the Styles deck |
| F12 / Shift+F12 | Toggle numbered list / toggle bulleted list |
| Ctrl+Shift+F12 | Turn numbered/bulleted list off |
| Tab / Shift+Tab | In a heading or list paragraph (cursor at its start): demote / promote the level |
| Ctrl+Alt+Up / Ctrl+Alt+Down | Move the paragraph up / down |
| Alt+Enter | In a list: new paragraph without a number or bullet |

### Breaks and inserts
| Key | Effect |
|---|---|
| Shift+Enter | Line break without a new paragraph |
| Ctrl+Enter | Manual page break |
| Ctrl+Shift+Enter | Column break (multi-column text) |
| Ctrl+F12 | Insert or edit table (Insert Table dialog) |
| Ctrl+F2 | Insert Fields dialog |

### Navigation and selection
| Key | Effect |
|---|---|
| Ctrl+Home / Ctrl+End | Start / end of document |
| Home / End | Start / end of line |
| Ctrl+Left / Ctrl+Right | Start of previous word / start of next word |
| Ctrl+Up / Ctrl+Down | Start of paragraph (again: previous paragraph) / start of next paragraph |
| Ctrl+Shift+Home / Ctrl+Shift+End | Select to start / end of document |
| Ctrl+Shift+Left / Right | Select word by word |
| Ctrl+Delete / Ctrl+Backspace | Delete to end / start of the word |
| F5 | Show or hide the Navigator |
| Ctrl+Shift+F5 | Navigator on, go to page number |
| Ctrl+Page Up / Ctrl+Page Down | Switch cursor between body text and header / footer |
| Ctrl+F10 | Show or hide formatting marks |
| F9 | Update fields |

## Sources

- Keyboard shortcuts: checked against the official documentation (https://help.libreoffice.org/latest/en-US/text/swriter/04/01020000.html, https://help.libreoffice.org/7.6/en-US/text/swriter/04/01020000.html, https://help.libreoffice.org/latest/en-US/text/shared/04/01010000.html, https://help.libreoffice.org/7.6/en-US/text/shared/04/01010000.html); the list started from Intelligence-Indeed's OSWorld skills (Apache-2.0), with their wrong or unconfirmed entries corrected or dropped.

## OSWorld 1.0: what is checked in Writer, and how to work so it passes

- After you stop, the checker brings the "<file> - LibreOffice Writer" window to the front and presses Ctrl+S,
  then reads the saved file. Leave the document open in that window, saved in its own format (Keep Current Format).
- Writer tasks compare the saved document with a reference document: the body paragraph text in order (every
  word, punctuation mark and letter case counts; text inside tables is not part of this comparison), and for
  formatting tasks the run attributes (font, size, colour, bold, italic, underline, strike-through,
  sub/superscript), line spacing, tab stops or tables. Change only what the task names.
- Paragraph text is compared character for character, spaces included. Splitting a paragraph or adding empty
  lines keeps every character that was there: put the break after the space that ends the sentence (as
  pressing Enter there would), do not replace ". " with a break.
- When the task says to copy text from a file, paste it as the source displays it (do not decode escape
  sequences such as \n) and add no title, label or note.
- Formatting checks read the attributes set directly on the text and paragraphs, not the styles: select the
  text and apply font, size, colour, strike-through, sub/superscript, alignment and line spacing with Format >
  Character / Paragraph. Changing a paragraph style does not satisfy them (a task that names a style is the
  exception). Set line spacing as a multiple (Single / 1.5 lines / Double), not Fixed or At least; a
  sub/superscript applies only to the characters named.
- Table tasks compare the number of tables, rows and columns and each cell's text: Table > Insert Table
  with the cells left empty, or Table > Convert > Text to Table with the separator the text uses. Data from Calc
  must be a real Writer table whose cell text equals what Calc displays (copy and paste the cells; a picture or
  embedded object does not count).
- A colour named in the task has the value given under "Colour names and their values" below; use it exactly,
  not a similar colour.
- Type and format text through Writer. Text written into the file with python-docx or raw XML lacks the
  explicit font name and size that Writer stores on each run, and the comparison reads those.
- After a document-wide formatting change, search the saved file (`word/document.xml`, plus headers, footers and
  text boxes) for the old value and make sure none is left: Ctrl+A in the body does not reach text boxes,
  headers or footers, so they can keep the old value.

### Colour names and their values

- When the task gives a hex or RGB value, use that value.
- A plain colour word means these values: Red #FF0000, Green #00A933, Blue #0000FF, Yellow #FFFF00,
  Cyan #00FFFF, Magenta #FF00FF, Orange #FF8000, Purple #800080, Dark Red #800000, Dark Green #008000,
  Dark Blue #000080, Light Grey #C0C0C0, Dark Grey #808080, Black #000000, White #FFFFFF. Note that "Blue"
  here is #0000FF, not the Standard palette's Blue swatch (#2A6099).
- A palette name with a shade number ("Light Green 3", "Dark Teal 1") means that swatch in the Standard palette
  below.
- Type the hex in the colour dialog (Custom Color > Hex) or pick the matching swatch; never mix a look-alike by eye.

The Standard palette, from LibreOffice 7.3's `share/palette/standard.soc`:

| Colour | Base | Light 1 / 2 / 3 / 4 | Dark 1 / 2 / 3 / 4 |
|---|---|---|---|
| Yellow | #FFFF00 | #FFFF38 #FFFF6D #FFFFA6 #FFFFD7 | #E6E905 #ACB20C #706E0C #443205 |
| Gold | #FFBF00 | #FFD428 #FFDE59 #FFE994 #FFF5CE | #E8A202 #B47804 #784B04 #472702 |
| Orange | #FF8000 | #FF860D #FF972F #FFB66C #FFDBB6 | #EA7500 #B85C00 #7B3D00 #492300 |
| Brick | #FF4000 | #FF5429 #FF7B59 #FFAA95 #FFD8CE | #ED4C05 #BE480A #813709 #4B2204 |
| Red | #FF0000 | #FF3838 #FF6D6D #FFA6A6 #FFD7D7 | #F10D0C #C9211E #8D281E #50200C |
| Magenta | #BF0041 | #D62E4E #E16173 #EC9BA4 #F7D1D5 | #A7074B #861141 #611729 #41190D |
| Purple | #800080 | #8D1D75 #A1467E #BF819E #E0C2CD | #780373 #650953 #4E102D #3B160E |
| Indigo | #55308D | #6B5E9B #8E86AE #B7B3CA #DEDCE6 | #5B277D #55215B #481D32 #3A1A0F |
| Blue | #2A6099 | #5983B0 #729FCF #B4C7DC #DEE6EF | #3465A4 #355269 #383D3C #362413 |
| Teal | #158466 | #50938A #81ACA6 #B3CAC7 #DEE7E5 | #168253 #1E6A39 #28471F #302709 |
| Green | #00A933 | #3FAF46 #77BC65 #AFD095 #DDE8CB | #069A2E #127622 #224B12 #2E2706 |
| Lime | #81D41A | #BBE33D #D4EA6B #E8F2A1 #F6F9D4 | #5EB91E #468A1A #395511 #342A06 |
| Gray | #808080 | #999999 #B2B2B2 #CCCCCC #DDDDDD #EEEEEE (Light Gray 1-5) | #666666 #333333 #1C1C1C #111111 |

Black #000000, White #FFFFFF.

Sources: Some of these conventions were adapted from Intelligence-Indeed's OSWorld skills (Apache-2.0); every statement about what is checked was confirmed against this repo's `desktop_env/evaluators` and task configs.
