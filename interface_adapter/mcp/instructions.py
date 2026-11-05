# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Shared model-facing usage contract for MCP and the reference agent."""

DESCRIPTION = """Execute synchronous Python on the MCP host in a persistent session.
Preloaded: computer, print, print_image. SDK methods return results directly;
variables persist across calls. App selectors and paths refer to this host.

Use computer.app(name) for a known app; list_apps() discovers app names and IDs.
list_apps() entries expose id, display_name, and is_running. app() selects a
scope; the first app.read() starts a nonrunning installed app when needed.
app.read() discovers numbered windows; app.window(ref) binds one observed window.
Choose the interaction method for each operation using this preference:
1. Prefer clearly identified elements: app.element(name).click(),
   element.type_text(text, replace=True), element.open_menu().
2. When the target has no usable element, element interaction encounters
   problems, or the target, UI state or outcome is unclear, promptly inspect
   the relevant App/Window screenshot.
   Reuse an adequate current image; obtain an updated image with
   app.read(view='image') or window.read(view='image') when needed. Use that
   App/Window's coordinate or keyboard methods as appropriate, such as
   window.click(at=(x, y)) or window.press_key('Return').
3. When global input is needed, select a direct Computer input method. Reuse
   an existing Element with computer.click(target=element),
   computer.type_text(text, target=element, replace=True), or
   computer.press_key('Return', target=element). No desktop read is needed.
   For a displayed App/Window image, retain p = app.point((x, y)) and call
   computer.click(target=p). computer.scroll('down', target=p) and
   computer.drag(start_point, end_point) also reuse retained image targets.
   Bare computer.click(at=(x, y)) uses the delivered desktop image, obtained
   with computer.read(view='image'); bare Computer keyboard methods use its
   captured input recipient.
Apply this preference anew to every subsequent operation, including after
desktop input. Reuse established behavior for the corresponding control and
context; prefer usable elements for the next target. Choose another method
directly when current evidence makes it more direct, reliable or efficient.
On macOS, App/Window/Element operations never inject global mouse or keyboard
input, including preparation and recovery. They may activate/focus their target.
Computer direct input explicitly uses global events; it does not grant later
object calls global authority. Windows/Linux currently retain their existing
ability to use global input for object operations. A host restriction can still
forbid global input. Use computer.paste(text, target=element_or_window) for an
explicit global paste. Element.target and Window.target retain exact bindings;
app.point((x, y)) and window.point((x, y)) retain one delivered image's mapping.
Retained points also work in app.click(at=p) or window.click(at=p) for scoped input.
They survive later reads and window translation; real identity, layout or display
changes can still invalidate the corresponding target. A known input dispatch
is not grounds to blindly repeat it through a global method.
The SDK handles target preparation, delivery routes and technical recovery.
Each window/page has its own accessibility coverage.
For toggles, click when the observed state differs from the requested state.
type_text() accepts one line. For multiline text or tabs, use element.paste(text)
or window.paste(text); paste inserts at the target's current selection.
For double-click: from computer_use import ClickOption;
app.element(ref).click(ClickOption(count=2)). A displayed secondary action belongs
to its Element: app.element(ref).perform_action('raise'), including a window root.
Use an action's attached observation first. A new form with only an image can
benefit from one scoped tree/both read when several semantic fields are needed.

App/Window.read() defaults to tree; view='image' and view='both' include images.
Reads and action observations automatically present text and images; action
receipts also appear automatically. Combined calls keep each receipt and the
latest automatic observation per scope; explicitly printed views are retained.
print() is for other values; print_image()
is for custom images. A subtree or element.read_text() bounds text content. The first
observation presents collected content; later observations use diff by default.
diff lists changes against that window's delivered baseline; unchanged means
the observed scope matches that baseline. read(full=True) presents the complete
collected view when needed, including after losing the baseline from context.
unknown means the stated capability or fact has not been determined.
observation.read_details(element=N) retrieves captured detail content for that
element; omitting element retrieves all captured details. TextPage.next is the
continuation cursor for further pages. A tree continuation's cursor is accepted
by element.read(cursor=...). AX collected reports acquired content; scope gaps
and omitted children identify partial coverage. Tree full/diff/unchanged is the
presentation mode.

Coordinates use the displayed image's pixels and its named coordinate target.
Choose coordinates from a newly returned image in the following tool call.
Combine operations whose targets and steps are already known in one call.
Return at a new menu, dialog or other state that requires a fresh decision.
Calls execute one at a time; each SDK operation completes or raises before the
next statement. Additional reads and waits depend on the information needed.

dispatched means input was sent; verified identifies the stated local check.
Assess task completion from observed UI content. On failure, use execution
progress and attached observations. Correct an invalid argument when its input
was not dispatched. For unknown input progress, inspect the affected scope
before deciding the remaining actions.
Key examples: 'super+a', 'Return', 'Escape'; modifiers: super, ctrl, alt, shift.
Timeout/cancellation clears this Python session; a subsequent call starts fresh.
"""

CODE_DESCRIPTION = "Synchronous Python code. Observations, images and action receipts are presented automatically."
TITLE_DESCRIPTION = "A short description of this call's intended operation, in the user's language."
RESET_DESCRIPTION = (
    "Reset this connection's Python execution session and stop its capture streams. "
    "Clears variables, imports, tasks, handles and observations. "
    "Applications, files and completed actions persist. Use for an explicit clean start or session recovery. "
    "The host closes the session at the end of the conversation. Idempotent; no parameters."
)
