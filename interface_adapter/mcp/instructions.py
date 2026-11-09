# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Shared model-facing usage contract for MCP and the reference agent."""

DESCRIPTION = """Execute synchronous Python on the MCP host in a persistent session.
Preloaded: computer, print, print_image. SDK methods return results directly;
variables persist across calls. App selectors and paths refer to this host.

All screenshot coordinates use the displayed image's pixels, with (0, 0) at
its top-left, x increasing right and y increasing down. Use the point constructor
printed beside that image with (x, y) unchanged. The SDK handles resizing,
display placement and window offsets; never calculate those conversions.
Retain the point to reuse the same location after later reads or to change the
input method. A composite image uses its whole canvas, including popup placement.

Use computer.app(name) for a known app; list_apps() discovers app names and IDs.
list_apps() entries expose id, display_name, and is_running. app() selects a
scope; the first app.read() starts a nonrunning installed app when needed.
app.read() discovers numbered windows. Use app.read(window=ref) to observe one.
On Windows, if the task specifies a full window title, use
computer.app(window_title=title).read(). The first read binds a unique,
case-sensitive match among visible native top-level windows. Reuse the App
handle after a title change. Its reads, details and action follow-up cover only
the bound window. A title-bound App rejects read(menu_path=...).
App methods accept optional window=ref to select the target for that call;
no Window object is needed. Element numbers are shared across all reads of the
same App. app.element(number) stays bound to its original control after reading
another window. app.element(name, window=ref) searches the specified window;
it does not require an extra read merely to switch contexts.
On macOS and Windows, app.close_window(ref) requests normal close of that exact
window through its native close mechanism. On macOS this also works when
screen-sharing controls cover its title-bar buttons. In an ordinary App scope,
use the returned observation to handle any confirmation. The SDK does not choose
save or discard. The application may decline a dispatched close request.
After a title-bound close, the observation covers the original window and choices
is empty. Observe a separate confirmation window only when the task authorizes
that scope. A receipt confirms closure when window_lifecycle is "closed".
Choose the interaction method for each operation using this preference:
1. Prefer clearly identified elements: app.element(name).click(),
   element.type_text(text, replace=True), element.open_menu().
2. When the target has no usable element, element interaction encounters
   problems, or the target, UI state or outcome is unclear, promptly inspect
   the relevant App screenshot.
   Reuse an adequate current image; obtain an updated image with
   app.read(view='image', window=ref) when needed. Use that
   image's point constructor: p = app.point((x, y), window=ref).
   Use app.click(at=p) or the appropriate keyboard method, such as
   app.press_key('Return', window=ref).
3. When global pointer input is needed on macOS, select a direct Computer method.
   Reuse p from the App image, or use p = computer.point((x, y)) for a desktop
   image, then call computer.click(target=p). The image coordinates stay the
   same when choosing App or Computer input. Do not pass screenshot pixel tuples
   directly to Computer.click/scroll/drag; use their retained image points.
   computer.click(target=element) locates its center.
   These locators only find a screen point; they do not activate, raise, focus,
   or require that the object currently receives input. The global click hits
   whatever is actually at that screen location.
   computer.scroll('down', target=p) and computer.drag(start_point, end_point)
   use the same retained points; drag endpoints may come from different images.
   Windows pointer input uses the existing screenshot coordinate mapping.
   On macOS and Windows, computer.type_text(text), computer.press_key('Return'),
   and computer.paste(text) go directly to the current system recipient without
   a prior read. They take only the key or text. Establish focus explicitly if needed. These methods return
   dispatch receipts. Read the relevant UI to confirm its response.
   Windows paste leaves the published text on the system clipboard. Verify
   the effect before publishing the next paste payload.
Apply this preference anew to every subsequent operation, including after
desktop input. Reuse established behavior for the corresponding control and
context; prefer usable elements for the next target. Choose another method
directly when current evidence makes it more direct, reliable or efficient.
On macOS, App/Element operations never inject global mouse or keyboard
input, including preparation and recovery. They may activate/focus their target.
Computer direct input explicitly uses global events; it does not grant later
object calls global authority. Windows/Linux currently retain their existing
ability to use global input for object operations. A host restriction can still
forbid global input. Linux Computer inputs retain their captured-desktop
adapter. Use App or Element methods for paste on Linux.
Element.target retains the exact element binding.
app.point((x, y), window=ref) retains that window's delivered image mapping.
Retained points also work in app.click(at=p, window=ref) for scoped input.
They survive later reads and window translation; real identity, layout or display
changes can still invalidate the corresponding target. A known input dispatch
is not grounds to blindly repeat it through a global method.
The SDK handles target preparation, delivery routes and technical recovery.
Each window/page has its own accessibility coverage.
For toggles, click when the observed state differs from the requested state.
type_text() accepts one line. For multiline text or tabs, use element.paste(text)
or app.paste(text, window=ref); paste inserts at the target's current selection.
For double-click: from computer_use import ClickOption;
app.element(ref).click(ClickOption(count=2)). A displayed secondary action belongs
to its Element: app.element(ref).perform_action('raise'), including a window root.
Use an action's attached observation first. A new form with only an image can
benefit from one scoped tree/both read when several semantic fields are needed.

App.read() defaults to tree; view='image' and view='both' include images.
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
Explicit native detail geometry retains its declared screen/AX coordinate space;
do not treat it as screenshot pixels.

After reading multiple windows, use window=ref to reuse the corresponding
image without rereading it. Composite images keep whole-image coordinates.
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
