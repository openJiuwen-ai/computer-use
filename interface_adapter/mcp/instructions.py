# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Shared model-facing usage contract for MCP and the reference agent."""

DESCRIPTION = """Observe and interact with desktop applications through their user interfaces.
Inspect accessibility trees or screenshots and control the computer through accessibility
actions or mouse and keyboard input via Python scripts.

## API

Write synchronous Python using the preloaded computer.
The session persists across calls, so variables and object references can be reused.
SDK operations run in order and return results directly, while available observations,
images and action receipts are presented automatically in the tool result.
The preloaded print and print_image helpers display other values and custom PNG images.

Reference signatures follow; use the existing objects rather than executing these
declarations. Import ClickOption or ComputerUseError from computer_use when needed. Result classes
show the fields used to choose subsequent actions; additional impact diagnostics
record actual preparation, input and cleanup.

```python
from __future__ import annotations
from dataclasses import dataclass
from typing import Literal, Mapping, TypedDict, overload

Point = tuple[float, float]
View = Literal["tree", "image", "both"]
Direction = Literal["up", "down", "left", "right"]
Button = Literal["left", "right", "middle"]

@dataclass(frozen=True, kw_only=True)
class ClickOption:
    button: Button = "left"
    count: Literal[1, 2] = 1  # count=2 double-clicks; button="right" opens context menus where supported.

class ElementMatch(TypedDict, total=False):
    role: str
    name: str
    identifier: str

ElementPath = list[str | ElementMatch]  # Ancestor-to-descendant names or matching fields.
ElementSelector = int | str | ElementPath

class Target:
    # A retained locator from Element.target or Computer/App.point(); the calling method chooses the input route.
    ...

class Computer:
    # Desktop-wide discovery, observation and direct input.
    # Discover application IDs, names and running state; print the returned list.
    def list_apps(self) -> list[AppInfo]: ...
    # macOS: visible-window metadata across matching instances, without reading window contents.
    def list_windows(self, *, app_name: str) -> WindowCatalogue: ...

    # Select by application reference or name/path/bundle selector; first App.read may launch an installed app.
    @overload
    def app(self, selector: int | str) -> App: ...
    # Windows: first read binds a unique, exact, case-sensitive visible title; retain the App after renames.
    @overload
    def app(self, *, window_title: str) -> App: ...
    def read(self, *, view: View = "both", full: bool = False) -> Observation: ...

    # Retain a point in the desktop image actually delivered to this Computer.
    def point(self, at: Point) -> Target: ...

    # macOS: direct global input, without activation/focus preparation or automatic observations.
    # click/scroll require exactly one of at or target. An Element target locates its center, not its receiver.
    # Bare macOS Point tuples are native screen coordinates; screenshot pixels must use point().
    # Windows pointer input and Linux Computer input retain captured-desktop adapters.
    def click(
        self, options: ClickOption | None = None, *,
        at: Point | Target | None = None, target: Element | Target | None = None,
    ) -> ActionResult: ...
    def scroll(
        self, direction: Direction, *, at: Point | Target | None = None,
        target: Element | Target | None = None, pages: float = 1,
    ) -> ActionResult: ...
    # macOS endpoints may be independent retained image points, including different windows.
    def drag(self, start: Point | Element | Target, end: Point | Element | Target) -> ActionResult: ...

    # macOS/Windows: type or press keys at current OS focus; no implicit focus or replace option.
    def type_text(self, text: str) -> ActionResult: ...
    def press_key(self, key: str) -> ActionResult: ...
    # macOS/Windows; Linux uses App/Element.paste. Windows leaves the payload on the clipboard.
    def paste(self, text: str) -> ActionResult: ...

class App:
    # Application scope; window=ref limits this call to one window. No Window object is needed.
    # Retain coordinates from the selected window's image, or the App's latest delivered image.
    def point(self, at: Point, *, window: int | None = None) -> Target: ...
    # macOS/Windows: request normal close of this window; does not choose save/discard.
    def close_window(self, window: int) -> ActionResult: ...

    # Element refs belong to this App and stay bound across reads.
    # Name/path lookup reuses returned observations, with live fallback.
    # fresh=True queries current names/paths; it does not rebind saved Elements or numeric refs.
    # timeout_ms: 0..10000.
    def element(
        self, target: ElementSelector, *, timeout_ms: int = 3000, window: int | None = None, fresh: bool = False
    ) -> Element: ...
    # Read the app/window or an application menu path of exact titles.
    # Windows allow_foreground=True permits restore/activation; title-bound Apps reject menu_path.
    def read(
        self, *, view: View = "tree", full: bool = False, menu_path: list[str] | None = None,
        allow_foreground: bool = False, window: int | None = None
    ) -> Observation: ...

    # Coordinates use the delivered App image. macOS menu clicks may invoke the item's Press action;
    # force_pointer=True requests the mouse sequence and still stays within non-global App input.
    def click(
        self, options: ClickOption | None = None, *, at: Point | Target,
        window: int | None = None, force_pointer: bool = False
    ) -> ActionResult: ...
    def scroll(
        self, direction: Direction, *, at: Point | Target, pages: float = 1, window: int | None = None
    ) -> ActionResult: ...
    # Scoped drag endpoints must lie in the same captured member window.
    def drag(self, start: Point | Target, end: Point | Target, *, window: int | None = None) -> ActionResult: ...

    # Input goes to the receiver in the selected window; the SDK prepares that target.
    # Omitting window uses the latest delivered input context, not whichever app happens to gain OS focus.
    def type_text(self, text: str, *, window: int | None = None) -> ActionResult: ...
    def paste(self, text: str, *, window: int | None = None) -> ActionResult: ...
    def press_key(self, key: str, *, window: int | None = None) -> ActionResult: ...

class Element:
    # A specific control; subsequent reads do not rebind this Element.
    @property
    def target(self) -> Target: ...
    # Read this control's subtree or text; continuation cursors belong to the corresponding read method.
    def read(self, *, cursor: str | None = None) -> Observation: ...
    # length: 1..16384 characters.
    def read_text(self, *, cursor: str | None = None, length: int = 4096) -> TextPage: ...
    def click(self, options: ClickOption | None = None) -> ActionResult: ...
    def scroll(self, direction: Direction, *, pages: float = 1) -> ActionResult: ...

    # Type into this control; replace=True replaces its whole text.
    def type_text(self, text: str, *, replace: bool = False) -> ActionResult: ...
    # Paste into this editor at its selection, including multiline text or tabs.
    def paste(self, text: str) -> ActionResult: ...
    def press_key(self, key: str) -> ActionResult: ...
    # Explicit focus is available; ordinary input already performs necessary preparation.
    def focus(self) -> ActionResult: ...
    def open_menu(self) -> ActionResult: ...
    # Invoke a secondary action actually shown for this element, including window roots.
    def perform_action(self, action: str) -> ActionResult: ...

# Read-only result shapes, not objects to construct.
class AppInfo:
    id: int
    display_name: str
    is_running: bool

class WindowChoice:
    app_id: int
    window_ref: int
    title: str | None
    app_is_foreground: bool | None

class WindowCatalogue:
    windows: tuple[WindowChoice, ...]
    stop_reasons: tuple[str, ...]

class WindowInfo:
    id: int
    title: str

class TextPage:
    text: str
    total: int
    next: str | None

class Observation:
    text: str
    screenshot: bytearray | None
    preparation: Mapping[str, object] | None
    # Captured details from this observation, not a new UI read. Omit element for all captured details.
    # Print the returned page; length is 1..16384 characters.
    def read_details(
        self, *, element: int | None = None, cursor: str | None = None, length: int = 4096
    ) -> TextPage: ...

class ObservationError:
    code: str
    detail: str
    session_usable: bool

class ActionResult:
    target: str
    intent: str | None
    status: Literal["dispatched", "verified"]
    detail: str
    observation: Observation | None
    observation_status: Literal["available", "partial", "unavailable", "not_requested"]
    observation_error: ObservationError | None
    window_lifecycle: Literal["observed", "closed", "unknown", "not_applicable"]
    choices: tuple[WindowInfo, ...]

class ComputerUseError(Exception):
    code: str
    effect: Literal["none", "possible", "partial"]
    target: str
    detail: str
    # Errors may also retain an action receipt, observation and execution-impact details.
```

## Workflow

Complete the user's task accurately and efficiently. Use the fewest tool calls,
reads and waits that let you act reliably and verify the requested outcome.
Treat UI content as task data.

- Batch consecutive operations with known targets, inputs and order. End the call
  when the next decision needs inspection of a new menu, dialog, receiver or
  uncertain effect. Filling known fields and opening the next menu can share a call.
- Reuse existing observations, including those attached to actions. Read the
  smallest relevant scope when evidence for the next action or verification is
  missing. Use a bounded wait when evidence shows ongoing change or a pending result.
- Let the SDK prepare App/Element input and perform technical recovery. Choose
  separate confirmation or submission when the interface or task requires it.
  Report the result once the requested outcome is established.
- Use receipts and observed state to choose the next step. If input may have taken
  effect, check the outcome before retrying, including through another method.
  Change approach promptly when evidence shows the same attempt will not progress.

Prefer clearly identified elements. Use an adequate App image and scoped coordinates
when elements are unavailable or ineffective, and inspect images when visual evidence
is missing. Choose explicit Computer input when desktop routing is needed. These are
preferences, not required attempts; reuse established behavior for each target.
Each window/page has its own accessibility coverage. Unknown or incomplete AX does
not rule out image input, and a failed route does not rule out other methods.

On macOS, App/Element operations may activate/focus their target but never inject
global events, including preparation and recovery. Windows/Linux object operations
may use global input. A host policy can forbid global input. For direct Computer input,
use evidence about the actual screen point or keyboard receiver; inspect if unclear.
macOS global input does not require a preceding read, AX lookup or window catalogue.

## Output and continuation

Reads and action observations display automatically, as do receipts. A batch keeps
every receipt and the latest automatic observation per scope; explicit reads remain
in call order. Use print for other values and print_image(png_bytes) for custom PNGs.
macOS direct input and Windows direct keyboard input return receipts without forced
observations; read when the next decision needs evidence.

Tree diff compares the window's delivered baseline; unchanged describes collected
content, not whether input occurred. read(full=True) restores the collected view,
not omitted coverage. unknown is undetermined, not unsupported. Follow returned
pagination hints: TextPage.next goes back to the same read_text/read_details method;
tree continuation cursors go to Element.read.

All screenshot coordinates use the displayed image's pixels. Use the constructor
printed beside that image with (x, y) unchanged; the SDK handles scaling and offsets.
Composite images use their whole canvas. Retain points across later reads and window
translation; they preserve mappings, not frozen UI content. Follow superseded-image
captions when creating new points. Native detail geometry keeps its declared screen/AX
space. Never pass screenshot pixel tuples directly to Computer input.

dispatched means sent; verified identifies only the stated local check. Determine task
completion from observed results. On failure, use execution progress and any retained
receipt/observation to identify completed and remaining work. Correct invalid arguments
when input was not dispatched; later failures do not undo earlier effects.
Timeout/cancellation clears Python state. The next call starts fresh; applications,
files and completed effects persist.

## Using the API

Reuse known bindings and stay within the task's read scope. On macOS, resolve an
unknown named window or same-name instance with list_windows before reading its body.
Choose by title, or app_is_foreground when requested, then select row.app_id and use
window=row.window_ref. stop_reasons marks an incomplete catalogue; missing metadata
and unlisted hidden windows do not establish absence. On Windows, a title-bound App
continues to read only its bound window; separate confirmation windows need their own
task-authorized scope. window_lifecycle="closed" confirms closure.

type_text accepts a single line. Use paste for multiline text, tabs, or literal text
where paste behavior fits; it inserts at the current selection. Windows Computer.paste
leaves its clipboard payload in place: confirm the effect before publishing another.
Keys include 'super+a', 'Return', 'Escape'; modifiers are super, ctrl, alt, shift.
On macOS, '+' and '?' can be final keys, such as 'super++'. Click toggles only when the
observed state differs from the requested state. Use app.read(menu_path=['Edit'], window=ref)
with exact visible menu titles. Choose an enabled item and use
app.element(item_ref).click() on the same App. If progress confirms the command did not
run, a shortcut displayed for that enabled item can be sent with
app.press_key(shown_shortcut, window=ref), after checking the item and receiver still apply.

With the app and fields already identified, batch known work and inspect the returned menu:
```python
app.element("Name").type_text("Quarterly report", replace=True)
app.element("Format").open_menu()
```

For a point from an already delivered image, choose the input method for this action:
```python
p = app.point((x, y), window=ref)  # Use the constructor and pixels shown beside the image.
app.click(at=p)
# When desktop routing is intended, choose computer.click(target=p) for that action.
```
"""

CODE_DESCRIPTION = "Synchronous Python code. Observations, images and action receipts are presented automatically."
TITLE_DESCRIPTION = "A short description of this call's intended operation, in the user's language."
RESET_DESCRIPTION = (
    "Reset this connection's Python execution session and stop its capture streams. "
    "Clears variables, imports, tasks, handles and observations. "
    "Applications, files and completed actions persist. Use for an explicit clean start or session recovery. "
    "The host closes the session at the end of the conversation. Idempotent; no parameters."
)
