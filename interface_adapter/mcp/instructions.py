# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Shared model-facing usage contract for MCP and the reference agent."""

DESCRIPTION = """Execute synchronous Python on the MCP host in a persistent session.
Preloaded: computer, print, print_image. SDK methods return results directly;
variables persist across calls. App selectors and paths refer to this host.

Use computer.app(name) for a known app; list_apps() discovers app names and IDs.
Prefer observed elements for semantic actions: app.element(name).click(),
element.type_text(text, replace=True), element.open_menu(). The SDK handles
target preparation and delivery. Use screenshot coordinates for regions without
a usable semantic target. Each window/page has its own accessibility coverage.
For toggles, click when the observed state differs from the requested state.
Use an action's attached observation first. A new form with only an image can
benefit from one scoped tree/both read when several semantic fields are needed.

App/Window.read() defaults to tree; view='image' and view='both' include images.
Reads and action observations automatically present text and images; action
receipts also appear automatically. Combined calls keep each receipt and the
latest automatic observation per scope; explicitly printed views are retained.
print() is for other values; print_image()
is for custom images. A subtree or read_text() bounds text content. The first
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
before deciding the remaining actions. The SDK handles technical recovery.
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
