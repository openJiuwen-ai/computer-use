---
name: gimp
description: How to operate GIMP through the desktop tools -- the single-window UI, Toolbox and tool options, layers and channels, selections, Colors and Filters, and opening and exporting images. Reference knowledge for any GIMP task, not a script for one.
---

# GIMP

Read image state with `inspect_file` (dimensions, mode, alpha, dominant
colours of a saved or exported file). GIMP's GUI has no batch processing: no
menu or dialog applies one edit to many files, and the BIMP plug-in that adds
one is not installed.

When the task is about GIMP's own state
or interface -- docks and windows, Preferences, a value a dialog remembers (such
as the name typed in the New Layer dialog), or opening a filter dialog -- do it in
the GUI: GIMP records those in `gimprc`, `sessionrc` or `action-history` as you
use it.

## The window and menus

- GIMP is usually in single-window mode: Toolbox top-left, tool options below
  it, the image in the middle, Layers/Channels docks on the right. If tools or
  docks are missing, Windows > Single-Window Mode and the Windows > Dockable
  Dialogs menu bring them back.
- Windows > Hide Docks (Tab) hides or shows all docks at once, and GIMP
  remembers that choice; closing the Toolbox or a dockable is a different
  change, not a way of hiding the docks.
- Top menus: File, Edit, Select, View, Image, Layer, Colors, Tools, Filters,
  Windows, Help. Reach them with the mouse (read positions from the screenshot)
  or, once a menu is open, read the window (`app.read()`) and click the item by name
  (`app.element("<item>").click()`).
- Many dialogs (Scale Image, Canvas Size, Export) are separate windows -- read
  their fields off the screenshot, Tab between them, and confirm on the next
  screenshot that the dialog closed.

## Layers, selections, and tools

- The Layers dock lists layers top-to-bottom; the highlighted one is active.
  Layer menu handles New, Duplicate, Delete, Flatten Image, Merge Down, and
  layer opacity/mode is in the dock. Most edits apply to the ACTIVE layer only.
- Toolbox tools: Move (M), Rectangle/Ellipse Select (R/E), Free Select (F),
  Fuzzy Select / "magic wand" (U), Crop (Shift+C), Bucket Fill (Shift+B),
  Pencil/Paintbrush (N/P), Eraser (Shift+E), Text (T). Select > None (Ctrl+
  Shift+A) clears a selection; Select > All is Ctrl+A.
- Foreground/background colours are the two swatches at the bottom of the
  Toolbox; click one to open the colour picker.
- Delete on a selection only makes pixels transparent if the layer has an alpha channel: do Layer > Transparency
  > Add Alpha Channel first, otherwise Delete paints the background colour. Select by Color (Shift+O) with a low
  threshold selects every pixel of the clicked colour in one click.

## Colors, filters, and image size

- **Colors** menu: Brightness-Contrast, Hue-Saturation, Color Balance, Curves,
  Levels, Desaturate, Invert, Threshold -- the usual colour adjustments, each a
  dialog with a preview.
- **Filters** menu: Blur, Enhance (Sharpen), Distorts, Light and Shadow,
  Artistic, and more -- this is where named effects live.
- **Image > Scale Image** resizes the whole image; **Image > Canvas Size**
  changes the canvas without scaling; **Image > Flatten Image** merges layers.

## Opening, saving, and exporting

- File > Open (Ctrl+O). GIMP's own save (Ctrl+S) writes **.xcf** (its native
  layered format). To produce a PNG/JPEG the task expects, use
  **File > Export As** (Shift+Ctrl+E), type the filename with the target
  extension, Export, then confirm on the format dialog. "Overwrite <name>" in
  the File menu re-exports to the last-exported file.
- File > Open Location opens an image from a URI you type in (`http(s)://`,
  `ftp://` or a path). GIMP has no web search or browser: which URL holds an
  image has to be found outside GIMP.
- After exporting, verify the result with `inspect_file` (size, mode, alpha,
  colours) rather than judging from the on-screen thumbnail.

## Exact positions

- For an exact layer position, open Layer > Layer Attributes (double-click the
  layer in the Layers dock) and type Offset X / Offset Y instead of dragging.

## Keyboard shortcuts (Linux)

When a shortcut below does what you need, prefer it to clicking through menus: a key needs no coordinates, so it cannot land on the wrong item. Confirm the result on the next screenshot.

Verified for GIMP 2.10 against the official manual (Keys and Mouse Reference) and the 2.10 action definitions in the GIMP source. Shortcuts act on the focused image window; click the canvas or window first so a dock or text field does not swallow the key.

File:
  Ctrl+N              New image
  Ctrl+O              Open image
  Ctrl+Alt+O          Open image as layers
  Ctrl+S              Save (XCF)
  Ctrl+Shift+S        Save as (different name)
  Ctrl+E              Export (re-exports to the file/format last exported; File > Export As is needed the first time)
  Ctrl+Shift+E        Export As (choose file name and format)
  Ctrl+W              Close the image window
  Ctrl+Shift+W        Close all images
  Ctrl+Q              Quit

Edit:
  Ctrl+Z              Undo
  Ctrl+Y              Redo
  Ctrl+C              Copy selection
  Ctrl+Shift+C        Copy visible (all visible layers)
  Ctrl+X              Cut selection
  Ctrl+V              Paste
  Ctrl+Alt+V          Paste in place
  Ctrl+Shift+V        Paste as new image
  Delete              Clear (erase the selected pixels)
  Ctrl+,              Fill with foreground color
  Ctrl+.              Fill with background color

Select:
  Ctrl+A              Select all
  Ctrl+Shift+A        Select none
  Ctrl+I              Invert selection
  Ctrl+Shift+L        Float selection
  Shift+Q             Toggle Quick Mask
  Shift+O             Select by color (tool)

View:
  + and -             Zoom in / zoom out
  1                   Zoom 1:1
  Shift+Ctrl+J        Fit image in window
  Shift+J             Center image in window
  F11                 Toggle fullscreen
  Tab                 Show/hide toolbox and docks

Image and layers:
  Ctrl+D              Duplicate image
  Ctrl+M              Merge visible layers
  Ctrl+Shift+N        New layer
  Ctrl+Shift+D        Duplicate layer
  Page Up / Page Down Select layer above / below
  Home / End          Select top / bottom layer
  Ctrl+H              Anchor floating layer

Filters:
  Ctrl+F              Repeat last filter
  Ctrl+Shift+F        Re-show last filter

Tools (single key, canvas focused):
  R rectangle select, E ellipse select, F free select, U fuzzy select,
  P paintbrush, Shift+E eraser, N pencil, G gradient, Shift+B bucket fill,
  C clone, H heal, M move, Shift+C crop, Shift+R rotate, Shift+S scale,
  Shift+F flip, T text, O color picker, Z zoom, B paths.
  D reset foreground/background colors to default (black/white)
  X swap foreground and background colors

Dialogs:
  Ctrl+L              Layers dialog
  Ctrl+B              Raise the toolbox

Notes
- Ctrl+Shift+N is listed in the manual for both New Layer and Offset; use the Layer menu to confirm the dialog that opens.
- Flatten Image and the Colors-menu adjustments (Hue-Saturation, Levels, Color Balance, Desaturate) have no default shortcut; use the menus.

## Sources
- Open Location: [Using Open Location -- GIMP Documentation](https://docs.gimp.org/en/gimp-using-open-location.html).
- Keyboard shortcuts: checked against the official documentation (https://docs.gimp.org/2.10/en/key-reference-file.html, https://docs.gimp.org/2.10/en/key-reference-edit.html, https://docs.gimp.org/2.10/en/key-reference-select.html, https://docs.gimp.org/2.10/en/key-reference-view.html, https://docs.gimp.org/2.10/en/key-reference-image.html, https://docs.gimp.org/2.10/en/key-reference-layer.html, https://docs.gimp.org/2.10/en/key-reference-tools.html, https://docs.gimp.org/2.10/en/key-reference-filters.html, https://docs.gimp.org/2.10/en/key-reference-windows.html); the list started from Intelligence-Indeed's OSWorld skills (Apache-2.0), with their wrong or unconfirmed entries corrected or dropped.

## OSWorld 1.0: the open GIMP window must hold the result

Many 1.0 graders press Shift+Ctrl+E (Export As) in the running GIMP window
after you stop and score the file that exports. So whatever produced the edit,
the image open in GIMP must show it when you stop, as the active image, with no
dialog or menu open. If you exported yourself, keep the file in the image's own
folder, which is where the grader's Export As dialog opens.
Do not close the image or quit GIMP.

Work only through GIMP's GUI: its menus, dialogs and tools in the open window.
**Do not use Script-Fu** -- neither the Script-Fu console nor a headless
`gimp -i -b` run -- and do not edit the image files with other programs or
scripts.

- File > Open Location opens a URL; GIMP cannot search the web. When the task
  gives the URL, opening it there is GIMP's way. When it does not, and GIMP
  itself is to find the image online, a URL you already know is an input you
  supply yourself -- judge it by rule 2.

- Pixel edits on the one image the task names (brightness, contrast, curves,
  scaling, cropping): use the Colors, Filters and Image menus in the open
  window, typing exact values into the dialog fields, with a moderate change
  that leaves the picture recognisable (unless the task gives a number). Do not
  use Script-Fu or `gimp -i -b`. The checks look at the direction of the
  change (darker, more saturated, higher contrast) plus similarity to the
  original, so change only the requested property.
- Check before you stop: `inspect_file` the result and compare it with the
  original -- mean brightness and dominant colours should move in the asked
  direction without clipping large areas to pure white or black. For moves and
  crops, check where things ended up against the instruction. A move in a
  direction without a distance goes as far as it can in that direction while
  staying fully inside the canvas.
- Leave GIMP running on that image with no dialog open.

### Settings and canvas

- GIMP writes preferences (`gimprc`), window layout (`sessionrc`) and the action history when it exits, and
  some checks read those files after the grader quits GIMP. GIMP records some of it only through the GUI: a
  new layer's name only when typed in the Layer > New Layer dialog, the action history only when a filter is
  opened from the Filters menu, docks through Windows > Hide Docks, preferences through Edit > Preferences.
  Do these in the GUI (a script or hand edit records nothing or is overwritten) and leave the main window with
  no dialog open.
- Keep the canvas size unless the task asks to resize or crop: removing a background makes pixels transparent,
  it does not trim the image (Image > Crop to Content changes the size the check compares). When a size is given,
  type the exact integer; the check has no tolerance. "Resize the layer" is Layer > Scale Layer, not Image >
  Scale Image.

Sources: Some of these conventions were adapted from Intelligence-Indeed's OSWorld skills (Apache-2.0); every statement about what is checked was confirmed against this repo's `desktop_env/evaluators` and task configs.
