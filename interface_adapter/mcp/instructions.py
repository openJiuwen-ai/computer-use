# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Shared model-facing usage contract for MCP and the reference agent."""

DESCRIPTION = """Execute synchronous Python on the MCP host in a persistent session.
Preloaded: computer, print, print_image. SDK methods return results directly;
variables persist across calls. App selectors and paths refer to this host.
Use the preloaded computer throughout the task. You may alias it with
c = computer. A desktop image and its point constructor belong to the
Computer instance that read the image.

All screenshot coordinates use the displayed image's pixels, with (0, 0) at
its top-left, x increasing right and y increasing down. Use the point constructor
printed beside that image with (x, y) unchanged. The SDK handles resizing,
display placement and window offsets; never calculate those conversions.
Retain the point to reuse the same location after later reads or to change the
input method. A composite image uses its whole canvas, including popup placement.

Use computer.app(name) for a known app; list_apps() discovers app names and IDs.
list_apps() entries expose id, display_name, and is_running. app() selects a
scope; the first app.read() starts a nonrunning installed app when needed.
On macOS, if the task names a window or there are same-name instances, use
computer.list_windows(app_name=exact_display_name) before reading app content.
It returns visible window metadata without screenshots. Choose the intended
title or use app_is_foreground if the task specifies the foreground instance,
then use computer.app(row.app_id).read(window=row.window_ref). Check
stop_reasons for an incomplete catalogue. A missing title or foreground value
is unknown, and hidden windows may be absent. Stay within the task's read scope.
app.read() also discovers numbered windows within the selected app.
On Windows, if the task specifies a full window title, use
computer.app(window_title=title).read(). The first read binds a unique,
case-sensitive match among visible native top-level windows. Reuse the App
handle after a title change. Its reads, details and action follow-up cover only
the bound window. A title-bound App rejects read(menu_path=...).
On Windows, use app.read(window=ref, view='both', allow_foreground=True)
when observation requires restoring or activating the selected window.
The observation reports preparation and the observed foreground state.
App methods accept optional window=ref to select the target for that call;
no Window object is needed. Element numbers are shared across all reads of the
same App. app.element(number) stays bound to its original control after reading
another window. app.element(name, window=ref) searches the specified window;
it does not require an extra read merely to switch contexts.
Names and paths first reuse the latest returned semantic observation, including
action observations within the same script. Missing or incomplete evidence
falls back to the ordinary live query. Use app.element(name, fresh=True) when
current lookup facts are explicitly needed. Saved Elements and numeric refs
keep their original binding; fresh does not rebind them. Lookup reuse does not
skip input preparation or live execution checks. Explicit reads remain fresh.
On macOS, app.close_window(ref) requests normal close of that exact window,
including when screen-sharing controls cover its title-bar buttons. Use the
returned observation to handle any confirmation; the SDK does not choose save
or discard. A dispatched close request may be declined by the application.
On Windows, app.close_window(ref) requests normal close through the window's
native UI Automation Window.Close action. After a title-bound close, the
observation covers the original window and choices is empty. Observe a
separate confirmation window when the task authorizes that scope. A receipt
confirms closure when window_lifecycle is "closed".
For an application menu bar that supports menu reads, use
app.read(menu_path=['Edit'], window=ref) with exact visible menu titles in order.
Choose an enabled item from that result and use
app.element(item_ref).click() on the same App.
If native menu tracking refuses the click before dispatch and the menu closes,
a shortcut displayed for that enabled item can be used explicitly. Send it to
the intended window with app.press_key('super+o', window=ref), using the actual
shortcut shown and checking that the item and window still apply.
On macOS, displayed punctuation + and ? can be used as the final key,
for example app.press_key('+', window=ref) or app.press_key('super++', window=ref).
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
3. When global input is needed on macOS, select a direct Computer input method.
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
   computer.type_text(text), computer.press_key('Return'), and computer.paste(text)
   go directly to the OS's current receiver. They have no target or replace
   option: use explicit clicks/keys when a global operation needs preparation.
   Global inputs return dispatch/effect receipts, without forced observations;
   request an App/Computer read when the next decision needs observation.
Apply this preference anew to every subsequent operation, including after
desktop input. Reuse established behavior for the corresponding control and
context; prefer usable elements for the next target. Choose another method
directly when current evidence makes it more direct, reliable or efficient.
On macOS, App/Element operations never inject global mouse or keyboard
input, including preparation and recovery. They may activate/focus their target.
Computer direct input explicitly uses global events; it does not grant later
object calls global authority. Windows/Linux currently retain their existing
ability to use global input for object operations. A host restriction can still
forbid global input. Windows pointer input uses the existing screenshot
coordinate mapping. A Windows capture fallback labeled as a desktop image uses
computer.point((x, y)). Within a tool call, computer.point() retains the desktop
image the model has received until a newer image is delivered.
On Windows, computer.type_text(text), computer.press_key(key), and
computer.paste(text) send input directly to the current system recipient.
Establish focus when needed and read the relevant UI to confirm the effect.
Windows paste leaves the text on the system clipboard. Confirm the paste effect
before publishing the next payload.
Linux Computer inputs use their captured-desktop adapter. Use App or Element
methods for paste on Linux.
Element.target retains the exact element binding.
app.point((x, y), window=ref) retains that window's delivered image mapping.
Retained points also work in app.click(at=p, window=ref) for scoped input.
They survive later reads and window translation; real identity, layout or display
changes can still invalidate the corresponding target. A known input dispatch
is not grounds to blindly repeat it through a global method.
On macOS, an ordinary App coordinate click on an open menu item may use its
Press action. Use app.click(at=p, force_pointer=True) when the mouse-event
sequence itself is required. This option never grants global input.
The SDK handles target preparation, delivery routes and technical recovery.
Each window/page has its own accessibility coverage.
For toggles, click when the observed state differs from the requested state.
type_text() accepts one line. Use explicit paste for multiline text or tabs, or
for single-line literal text when paste behavior fits the task. Use
element.paste(text) on a confirmed editable control, or app.paste(text, window=ref)
when the intended editor already receives input in that window. Paste inserts
at the current selection.
For double-click: from computer_use import ClickOption;
app.element(ref).click(ClickOption(count=2)). A displayed secondary action belongs
to its Element: app.element(ref).perform_action('raise'), including a window root.
Use an action's attached observation first. Request one scoped read if more
context is needed. Request an image explicitly when visual evidence is needed.

App.read() defaults to tree; view='image' and view='both' include images.
Reads and action observations automatically present captured content; action
receipts also appear automatically. Combined calls keep each receipt and the
latest automatic observation per scope. Each explicit app.read() samples at
its own call site and keeps its result in order.
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

Use the point constructor beside the current delivered image for the chosen
window. A newer delivered image can replace that window's default coordinate
space. If a caption marks the window's coordinates superseded, use the named
image's pixels or read that window alone with app.read(view='image', window=ref).
Composite images keep whole-image coordinates.
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
