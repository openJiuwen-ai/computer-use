---
name: libreoffice-impress
description: How to operate LibreOffice Impress through the desktop tools -- slide navigation, menus and dialogs by keyboard, selecting and formatting text, slide numbers and hyperlinks, saving, and reading exact values with inspect_file. Reference knowledge for any Impress task, not a script for one.
---

# LibreOffice Impress

No focus click is needed before keys once a shape or field is active.

## Views and navigation

- Normal view: slide pane on the left, canvas in the middle, Properties
  sidebar on the right. View > Outline shows all slide text; View > Slide
  Sorter shows thumbnails.
- With no text box in edit mode: `home` = first slide, `end` = last slide,
  `pagedown` / `pageup` = next / previous slide. Ctrl+Home / Ctrl+End move the
  caret inside text, not between slides.
- The status bar reads `Slide N of M`; find it in the window's tree
  (`app.read()`) rather than by eye.

## Menus and dialogs

- Alt+letter opens a menu: Alt+F File, Alt+E Edit, Alt+V View, Alt+I Insert,
  Alt+O Format, Alt+S Slide, Alt+T Tools. Then press the item's underlined
  letter, or arrow down and Enter. `app.read()` lists the open menu's items, and
  `app.element("<item>").click()` clicks one, if you prefer to click.
- Dialogs are separate windows; the canvas behind them goes quiet, which is
  expected. Read the dialog's fields off the returned screenshot, move between
  them with `tab` / Shift+Tab, type values, and press `enter` for OK or `esc`
  for Cancel. Confirm on the next screenshot that the dialog is gone.
- Tabbed dialogs (Character, Paragraph) reopen on the tab last used -- check
  which tab is showing before tabbing through fields.
- F5 starts the slideshow (Shift+F5 from the current slide); `esc` leaves it.
  A full-screen slide with no toolbars means a slideshow is running.

## Selecting and editing text

- To reach a text box without pixel aiming: Esc twice (deselect
  everything), then Tab until the shape you want is selected, then F2 to
  enter its text, then Ctrl+A to select all of it. Tab walks the shapes in
  creation order, which is not always top to bottom: when a task says "the
  first / second text box", decide which one it means by its position on the
  slide (reading order), and use Tab only to reach that shape.
- Or click the text on the canvas using coordinates read from the screenshot.
- Typing or Delete while slide text is selected replaces/removes that text.
  Change formatting through dialogs, the sidebar, or shortcuts -- never by
  typing into the slide. If content changed by mistake, Ctrl+Z
  immediately.
- Keep AutoCorrect at its defaults (Tools > AutoCorrect Options): typing a
  straight quote or apostrophe gives the typographic one (’ “ ”), as decks
  typed in Impress hold. Do not turn its options off or undo a replacement it
  makes.
- A picture inside a group is still the picture the task means. Select the
  group (or enter it with F3) and set the size in Position and Size (F4); the
  group's frame takes the new size, and the saved file records that frame.

## Formatting

- Direct formatting applies to the current selection. To make text match
  other slides, set the formatting explicitly on each text box; changing a
  master or layout does not restyle text that already carries its own
  formatting.
- **Format > Character** (Alt+O, then C) sets font family, style, size, and on
  its Font Effects tab the font color and underline. The Font tab opens first;
  Tab reaches Family, then Style, then Size.
- **Format > Paragraph** holds alignment and spacing. Alignment shortcuts:
  Ctrl+L left, Ctrl+E center, Ctrl+R right, Ctrl+J justified.
- Ctrl+B bold, Ctrl+I italic, Ctrl+U underline. Ctrl+] / Ctrl+[ grow / shrink
  size in steps.
- The Properties sidebar (Ctrl+F5 toggles it) shows the current font name and
  size.

## Lists and bullets

- Select the paragraphs (click into the text box, Ctrl+A for all of it), then
  Format > Bullets and Numbering, or the Properties sidebar's Lists section
  (Unordered List / Ordered List). Tab / Shift+Tab change the level of a list item.
- Bullets and numbering are paragraph formatting: applying them to existing
  paragraphs adds no text. Save, then `inspect_file`: each paragraph's `bullets:` entry
  shows `char` for a bullet and `none` for none.

## Slide numbers, footers, and links

- Insert > Header and Footer opens a dialog with a Slide number checkbox;
  "Apply to All" adds it to every slide. Insert > Field > Slide Number inserts
  the number into the text box being edited.
- Insert > Hyperlink (Ctrl+K) opens the Hyperlink dialog with a URL field and a
  Text field; select the text first to link it.
- **To change something on every slide** -- the background, or the colour or
  font of the slide number, date or footer -- change it on the master: View >
  Master Slide (Slide > Properties > Background for the background; select the
  number field's text box to restyle it), then View > Normal. A deck can use
  several masters: change each one its slides use. Slides follow their master
  unless they carry their own value (a background of their own, direct
  formatting on the field): change those slides too, so the masters and the
  slides hold the value. Do the masters first, then those slides: checks of
  an every-slide change read the master.
- Slide > Slide Properties in Normal view sets one slide's layout and background.

## Saving and verifying

- Ctrl+S saves.
- If the deck is ever closed, reopen it with Ctrl+O or from the Start Center's
  recent documents.

## Matching an existing style

When a task asks to match an existing style or format, the exact values are not
in the instruction -- read them with `inspect_file` (font, size, color,
alignment, underline, hyperlinks, slide-number field) from unmodified items,
comparing like with like: a heading-plus-body slide takes its values from other
heading-plus-body slides, never from a one-line section-title slide. Read at
least two references and use the value they agree on, then apply every
attribute they share, not just the first you notice. Save, then `inspect_file`
the changed item to confirm before moving on.

## Keyboard shortcuts (Linux)

When a shortcut below does what you need, prefer it to clicking through menus: a key needs no coordinates, so it cannot land on the wrong item. Confirm the result on the next screenshot.

Verified against the LibreOffice 7.6 and latest Help pages. Keys the desktop (GNOME / input method) grabs first are not delivered to LibreOffice. If a key does nothing, use the menu. For arranging objects (bring to front, send to back) use the Shape/Arrange menu rather than keys.

### Document and general
| Key | Effect |
|---|---|
| Ctrl+N / Ctrl+O / Ctrl+S | New / Open / Save |
| Ctrl+F4 | Close the current document |
| Ctrl+Z | Undo |
| Ctrl+Y | Redo |
| Ctrl+C / Ctrl+X / Ctrl+V | Copy / Cut / Paste |
| Ctrl+Shift+V | Paste Special dialog |
| Ctrl+A | Select all |
| Ctrl+F | Show the Find toolbar |
| Ctrl+H | Find & Replace dialog |
| Ctrl+B / Ctrl+I / Ctrl+U | Bold / Italic / Underline |

### Normal view: slides and objects
| Key | Effect |
|---|---|
| Page Down / Page Up | Next / previous slide (no effect past the first or last slide) |
| Tab / Shift+Tab | Select next / previous object (in creation order) |
| Enter | With an object or placeholder selected: start text input in it |
| F2 | Edit text of the selected object |
| Esc | Exit the current mode (for example leave text editing) |
| Arrow keys | Move the selected object (or the page view if nothing is selected) |
| F4 | Position and Size dialog |
| Shift+F3 | Duplicate dialog |
| Ctrl+Shift+G | Group the selected objects |
| Ctrl+Alt+Shift+A | Ungroup the selected group |
| F3 / Ctrl+F3 | Enter group / exit group |
| Ctrl+Shift+K | Combine the selected objects |
| Ctrl+Enter | Move to the next text object on the slide; after the last one, insert a new slide with the same layout |
| Ctrl+Shift+F5 | Navigator |
| F11 | Show or hide the Styles deck |

### While editing text in an object
| Key | Effect |
|---|---|
| Shift+Enter | Line break without a new paragraph |
| Ctrl+Home / Ctrl+End | Start / end of the text block |
| Ctrl+Left / Ctrl+Right | Start of previous word / start of next word |
| Ctrl+Shift+Left / Right | Select word by word |
| Ctrl+Backspace / Ctrl+Delete | Delete to start / end of the word |
| Ctrl+Shift+Space | Non-breaking space |

### Slide Sorter and slide pane
| Key | Effect |
|---|---|
| Home / End | Focus first / last slide |
| Alt+Shift+Page Down / Page Up | Move selected slides down / up one position |
| Alt+Shift+End / Home | Move selected slides to the end / start |
| Enter | Slide Sorter: switch to Normal view with that slide. Slide pane: add a new slide |

### Slide show
| Key | Effect |
|---|---|
| F5 | Start the slide show |
| Esc | End the slide show |
| Right, Down, Page Down, Space, Enter | Next effect, else next slide |
| Left, Up, Page Up, Backspace | Previous effect, else previous slide |
| Alt+Page Down / Alt+Page Up | Next / previous slide without playing effects |
| Home / End | First / last slide |
| number then Enter | Go to that slide number |
| B or . | Black screen until next key |
| W or , | White screen until next key |

## Sources

- Master slide background: [Changing the Slide Background Fill -- LibreOffice Help](https://help.libreoffice.org/latest/en-US/text/simpress/guide/background.html).
- Slide numbers on the master: [Page Number -- LibreOffice Help](https://help.libreoffice.org/latest/en-US/text/simpress/01/04990500.html).
- Keyboard shortcuts: checked against the official documentation (https://help.libreoffice.org/latest/en-US/text/simpress/04/01020000.html, https://help.libreoffice.org/7.6/en-US/text/simpress/04/01020000.html, https://help.libreoffice.org/latest/en-US/text/shared/04/01010000.html, https://help.libreoffice.org/7.6/en-US/text/shared/04/01010000.html); the list started from Intelligence-Indeed's OSWorld skills (Apache-2.0), with their wrong or unconfirmed entries corrected or dropped.

## OSWorld 1.0: what is checked in Impress, and how to work so it passes

- After you stop, the checker brings the "<file> - LibreOffice Impress" window to the front and presses Ctrl+S,
  then reads the saved file. Leave the deck open in that window, saved in its own format (Keep Current Format).
- Most Impress tasks compare the saved deck with a reference deck attribute by attribute: number of slides,
  text, the number of text runs in each paragraph, number and order of shapes, font name, size, bold, italic,
  colour, underline, strike-through, alignment, bullets, slide background, speaker notes, and each shape's size
  and position (within about 0.5%). One extra change anywhere scores 0, so change only what the task names.
  When only text formatting is asked for (colour, bold, size, underline), change the text's attributes and do
  not move or resize its text box.
- Do not split a paragraph into extra runs: format whole paragraphs or whole text boxes (select the box, not
  part of the text), and type a new piece of text in one go.
- Shape count and order are compared too: type into the existing title and content placeholders instead of
  adding text boxes. A blank slide is the Blank layout (Slide > Layout), with no empty placeholder left on it.
- A colour named in the task has the value given under "Colour names and their values" below. Do not mix a
  similar colour by eye or guess a hex value.
- Changes that apply to every slide -- the background, the slide-number, date and footer fields -- are made
  on the masters, plus the slides that carry their own value (see "Slide numbers, footers, and links"). A
  background for one named slide is set on that slide only (Slide > Slide Properties > Background; answer No
  if asked to apply it to all slides).
- Layouts, master slides, the Summary Slide, tables and text frames are objects Impress creates through its own
  commands (Slide > Layout, View > Master Slide, the Slide menu, Insert > Table): a hand-built shape that looks
  the same is a different object in the saved file. Audio is inserted with Insert > Audio or Video, a picture
  background with Slide Properties > Background > Image on that slide.
- Type and format text through Impress. Text written into the file with python-pptx or raw XML lacks the
  explicit font name and size that Impress stores on every run, and the comparison reads those.
- After a document-wide formatting change, search the saved file for the old value (for example the old typeface
  in the slide XML) and make sure none is left: Outline view and Select All reach only part of the objects, so
  text boxes and paragraph-end properties can keep the old value.

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
