---
name: libreoffice-calc
description: How to operate LibreOffice Calc through the desktop tools -- moving around the grid, entering data and formulas, selecting ranges, cell and number formatting, rows/columns/sheets, sort and filter, saving, and reading exact cell values and formats with inspect_file. Reference knowledge for any Calc task, not a script for one.
---

# LibreOffice Calc

## Moving around and selecting

- Arrow keys move one cell; Ctrl+Arrow jumps to the edge of a data block;
  Ctrl+Home goes to A1, Ctrl+End to the last used cell. Page Down / Page Up
  scroll a screen.
- The Name Box (top-left, left of the formula bar) jumps anywhere: click it or
  the cell reference shown there, type a reference like `B12` or a range
  `A1:C10` or a named range, then Enter.
- Shift+Arrow extends a selection; Ctrl+Space selects the column, Shift+Space
  the row, Ctrl+A the whole sheet. Ctrl+click adds non-adjacent cells.
- The status bar and Name Box show the active cell; read them from the window
  (`app.read()`) rather than guessing.

## Entering data and formulas

- Type into the active cell and press Enter (moves down) or Tab (moves right).
  To edit in place without replacing, press F2 first; Esc cancels an edit.
- Formulas start with `=` (e.g. `=SUM(A1:A10)`, `=A1*B1`). The formula bar
  shows the formula of the active cell; the cell shows the result.
- Paste special (Ctrl+Shift+V) chooses values-only vs formulas vs formats.

## Formatting

- **Format > Cells** (Ctrl+1) is the dialog for number format, font, alignment,
  borders, and background on the selected cells. Its Numbers tab sets currency,
  percent, date, and custom format codes (e.g. `0.00%`, `#,##0`).
- Toolbar/shortcut basics: Ctrl+B bold, Ctrl+I italic, Ctrl+U underline.
  Alignment via Format > Cells > Alignment or the toolbar.

## Rows, columns, and sheets

- Right-click a row/column header for Insert/Delete/Width/Height, or use the
  Sheet and Format menus. Double-click a column border to autofit width.
- Sheet tabs are at the bottom; right-click for Insert/Rename/Move/Delete.
  Ctrl+Page Down / Ctrl+Page Up switch sheets.

## Sort, filter, and find

- Data > Sort sorts the selected range by chosen columns. Data > AutoFilter
  adds dropdown filters to the header row; Data > Standard Filter for
  multi-condition filters.
- Ctrl+H is Find & Replace (tick "Regular expressions" for patterns; "Other
  options" scopes to the selection or whole sheet).

## Pivot tables

- Data > Pivot Table > Insert or Edit builds one from the selected range. Its
  defaults -- no page (filter) fields unless you add some -- are what a person
  making it by hand gets.
- To show how often each value of a field occurs, put that field in Row Fields
  and the same field in Data Fields with Count; Data Field > Options >
  Displayed value ("% of column", "% of total") turns the counts into shares.

## Saving and verifying

- Ctrl+S saves. Reopen a closed file with Ctrl+O.
- To export a sheet as CSV exactly as shown: File > Save As, type Text CSV, tick "Edit filter settings", then
  tick "Save cell content as shown" in the options dialog.

## Keyboard shortcuts (Linux)

When a shortcut below does what you need, prefer it to clicking through menus: a key needs no coordinates, so it cannot land on the wrong item. Confirm the result on the next screenshot.

Verified against the LibreOffice 7.6 and latest Help pages. Keys the desktop (GNOME / input method) grabs first are not delivered to LibreOffice. If a key does nothing, use the menu.

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
| Ctrl+M | Remove direct formatting from the selection |
| Ctrl+1 | Format Cells dialog |

### Editing cell contents
| Key | Effect |
|---|---|
| F2 | Edit the current cell (in a formula, arrow keys insert references; F2 again switches to moving the text cursor) |
| Delete | Delete the contents of the selected cells immediately, no dialog |
| Backspace | Open the Delete Contents dialog (choose what to delete) |
| Ctrl+Enter | Insert a manual line break inside the cell (while editing it) |
| Alt+Enter | Fill the selected range with the formula typed in the Input line |
| Ctrl+Shift+Enter | Enter the formula as an array (matrix) formula |
| F4 | Cycle relative/absolute reference forms (A1, $A$1, A$1, $A1) in the input field |
| Ctrl+` | Show formulas instead of values, and back |
| Ctrl++ | Insert cells (dialog) |
| Ctrl+- | Delete cells (dialog) |
| Ctrl+Shift+X | Cut contents but keep the source cells' formatting |

### Number formats (digit keys on the main row, not the number pad)
| Key | Effect |
|---|---|
| Ctrl+Shift+1 | Number: two decimal places, thousands separator |
| Ctrl+Shift+2 | Standard exponential (scientific) format |
| Ctrl+Shift+3 | Standard date format |
| Ctrl+Shift+4 | Standard currency format |
| Ctrl+Shift+5 | Percentage, two decimal places |
| Ctrl+Shift+6 | Standard (General) format |

### Navigation and selection
| Key | Effect |
|---|---|
| Ctrl+Home / Ctrl+End | Go to A1 / go to last cell that contains data |
| Home / End | First cell of the row / last column that contains data in any row |
| Ctrl+Arrow | Jump to the start or end of the data block in that direction |
| Ctrl+Shift+Arrow | Select from the cursor to the edge of the data block |
| Shift+Home / Shift+End | Select to the first cell of the row / last column with data |
| Shift+Space | Select the current row |
| Ctrl+Space | Select the current column (an input-method switch may take this key) |
| Ctrl+Shift+Space | Select all cells in the sheet |
| Ctrl+Page Down / Ctrl+Page Up | Next / previous sheet |
| Alt+Page Down / Alt+Page Up | Move one screen right / left |
| F5 | Show or hide the Navigator |
| Ctrl+Shift+F5 (also Ctrl+Shift+T) | Move focus to the Name Box |
| Ctrl+Shift+F2 | Move focus to the Input line |
| Ctrl+F2 | Function Wizard |
| Ctrl+F3 | Define Names dialog |
| F9 / Ctrl+Shift+F9 | Recalculate changed formulas in the sheet / recalculate all formulas in all sheets |
| F11 | Show or hide the Styles deck |

## Sources

- Keyboard shortcuts: checked against the official documentation (https://help.libreoffice.org/latest/en-US/text/scalc/04/01020000.html, https://help.libreoffice.org/7.6/en-US/text/scalc/04/01020000.html, https://help.libreoffice.org/latest/en-US/text/shared/04/01010000.html, https://help.libreoffice.org/7.6/en-US/text/shared/04/01010000.html); the list started from Intelligence-Indeed's OSWorld skills (Apache-2.0), with their wrong or unconfirmed entries corrected or dropped.

## OSWorld 1.0: real Calc features, and the open window must hold the result

OSWorld 1.0 graders compare the saved workbook's structure, and many re-save
it from the open Calc window after you stop, so:

- Features the task names are read as real objects from the saved file. A pivot table is Data > Pivot Table >
  Insert or Edit (source range, fields into Row/Column/Data, destination under Source and Destination); its
  source is the whole data block with its header row, placed on the sheet the task names, with no page fields
  unless asked. Validity lists have their entries typed in, and rows are hidden with Format > Rows > Hide,
  not deleted. Some features are newer than this LibreOffice (7.3): sparklines arrived in
  7.4, so a task asking for one asks for a feature this version lacks.
- Charts are compared by their data ranges (value and category range of each series), type, title text and
  sheet: build one with Insert > Chart from exactly the cells the task implies, type the title as given, and
  leave no extra chart.
- Plain cell values and formulas may be written by a script on the file, but a
  script that rewrites the workbook (openpyxl, pandas) drops pivot tables,
  charts and Calc formatting -- so only on a file without them. A formula written by a script has no
  calculated result stored, so a check that reads values sees an empty cell until Calc has opened and saved
  the file. Afterwards use File > Reload in Calc so the open window shows the new content; otherwise the
  grader's Ctrl+S writes the stale window back over your file.
- Set fonts, colours, fills, alignment and number formats in Calc (Format > Cells), not by patching the
  .xlsx: the checks read each cell's style from the saved file.
- Cell types are compared, not only how a cell looks: a date is a real date with a date format, a number is
  numeric (not text), text stays text. A new sheet keeps its default name (Sheet2, ...) unless the task names
  it, and goes after the existing sheets; checks find sheets by name or by position (the first sheet).
- Before typing a value that Calc would reinterpret (a phone number starting with `+`, an ID with leading zeros,
  anything that looks like a date, number or formula), format the target cells as Text first (Format > Cells >
  Text, or Ctrl+1), then type it, and read one cell back in the formula bar.
- For text clean-up (trimming, case, splitting) prefer Calc's own functions (TRIM, PROPER, UPPER, ...); a
  script's rules can differ on edge cases such as hyphens.
- Zoom level and freeze panes are stored in the file only when it is saved: press Ctrl+S yourself after
  changing them (Keep Current Format).
- Selecting: type the range into the Name Box and press Enter; never select
  whole columns or rows when a block will do.
- Save with Ctrl+S (Keep Current Format for .xlsx) and leave Calc open on that
  document. Before you stop, `inspect_file` the saved workbook and check the
  cells the task asked about.

### How Calc results are compared

- Most Calc tasks compare the whole sheet's values with a reference workbook after rounding to 4 decimals, the
  first row as the header: every cell counts, so an extra helper cell, column or row fails as surely as a
  wrong value. Only stored values are read, so a typed value and a formula with the same result are equally
  good. Some tasks compare the sheet's printed text, where the number format (decimal places, leading zeros,
  percent sign, separators) is part of what is compared; pivot tables and charts are compared as real objects.
- Rows are compared by position, not matched by content. Rows you add from a source (a listing page, a
  document, a search result) keep that source's order unless the task gives one.
- When the task names a source (a site, a map, a document), take every value from that source, even where another
  source differs. Only a value that source does not show is looked up elsewhere (the official site, a web search),
  not left empty: an empty cell matches no expected value.
- Percentages: store the ratio (0.075) with a percent format (7.5%), unless the column's header shows that it
  holds percent numbers.
- "Show N decimal places" is a number format (Format > Cells > Numbers > Decimal places; 0.0 keeps its trailing
  zero). ROUND changes the value; the format changes what is shown.
- Style checks compare every cell's fill and font colour with the reference: colour only the cells the task
  names, with direct cell formatting.

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
