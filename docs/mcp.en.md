# MCP: build, install and integrate from source

[Back to README](../README.md) | [简体中文](mcp.md) | [DeepAgent integration](deepagent.en.md)

Add desktop operation tools to an existing agent over stdio MCP. The agent client continues to manage its model configuration.

## 1. Prepare the environment

Install Python 3.11 or later and `uv`. Build and run the MCP server on the computer whose desktop it will operate.

Prepare the native dependencies on the computer whose desktop you will operate:

| Platform | Requirements |
| --- | --- |
| macOS | macOS 14 or later, an Xcode toolchain with Swift 6, Accessibility and Screen Recording permissions. |
| Windows | Windows x64 with .NET Framework 4.8 and its C# compiler; window capture requires Windows 10 1903 or later. |
| Linux | A graphical desktop session with AT-SPI and PyGObject. Use X11 for screenshots and mouse input. |

On Debian or Ubuntu, install the Linux system dependencies first:

```bash
sudo apt install python3-gi python3-venv gir1.2-atspi-2.0 libx11-6 libxkbcommon0
```

## 2. Install from source and build the backend

```bash
git clone https://gitcode.com/openJiuwen/computer-use.git
cd computer-use
uv sync --no-dev --extra mcp
```

The SDK and MCP server are installed in editable mode into the checkout's `.venv`, without model dependencies. Run the following commands from the checkout root.

Build or prepare the native backend, then check desktop access:

```bash
uv run --no-dev --extra mcp computer-use-build
uv run --no-dev --extra mcp computer-use-permissions
```

On macOS, use `uv run --no-dev --extra mcp computer-use-permissions --request` to request missing permissions. Approve them in **System Settings → Privacy & Security**, then run the check again. Run the commands from the same terminal or host connection that will launch the SDK or its MCP adapter.

## 3. Connect your agent

After installing from source, preparing the platform dependencies and building the
backend above, connect the MCP server to your own agent. The server runs on the
computer whose desktop it operates, using its own `.venv`; the agent client starts
and calls it over stdio. The agent must support MCP tool calls. To use screenshots,
the client and model must also support image input.

**1. Configure the connector.** Add `computer-use` to the agent's MCP configuration.
For clients accepting the `mcpServers` format, use this on macOS / Linux:

```json
{
  "mcpServers": {
    "computer-use": {
      "command": "/absolute/path/to/computer-use/.venv/bin/computer-use-mcp",
      "args": [],
      "env": {
        "PYTHONSAFEPATH": "1"
      }
    }
  }
}
```

Replace `command` with the absolute path to the executable in your checkout.
On Windows, use `C:/absolute/path/to/computer-use/.venv/Scripts/computer-use-mcp.exe`.
`PYTHONSAFEPATH=1` prevents same-named directories in the host's working directory
from shadowing the SDK package.

For a connector form, such as WorkSwarm's, select **stdio**, name it `computer-use`,
set the command to the executable path above, leave arguments empty and add the
environment variable `PYTHONSAFEPATH=1`. The client manages configuration storage
and connector activation.

For manual server debugging, you can also run this from the checkout root:

```bash
uv run --no-dev --extra mcp computer-use-mcp
```

For a wheel installation, install the same wheel with its `mcp` extra, for example
`uv pip install '/absolute/path/to/openjiuwen_computeruse-0.0.1-py3-none-any.whl[mcp]'`,
then run `computer-use-mcp` from that environment.

**2. Enable and verify the connection.** Save the configuration, enable the connector
and reconnect MCP; restart the agent client if needed. Confirm that it discovers
the `computer_use` and `computer_use_reset` tools, then send your agent:

```text
Use computer_use to list the running applications and verify calls to this computer's backend.
```

The tool provides `computer`, `print()` and `print_image()`. The code for this first
check is:

```python
print(computer.list_apps())
```

You should receive the backend's application list. Tool discovery verifies the MCP
connection; application enumeration verifies that the agent can call the local backend.
Then select an application with a window, use `app.read(view="both")` as described
below, and check the interface text and screenshot. Grant the required platform
permissions as described above. Use `computer_use_reset` to clear the current Python
execution session.

**3. Reload after source changes.** Restart the MCP connection after editing Python
source. After changing dependencies or entry points, first rerun
`uv sync --no-dev --extra mcp`. After changing the native backend, run
`uv run --no-dev --extra mcp computer-use-build`, then restart the MCP connection.

On macOS, App/Element operations never inject global mouse or keyboard input; the agent explicitly selects direct Computer input when needed. Windows/Linux retain their existing global-input capability. Each action records the selected route's declared need for global input separately from its actual effects. To explicitly restrict a session to non-global input, add `--no-allow-global-input` to the connector's `args`. System permissions must still be available on the target computer.

## 4. Interface reference

The SDK is synchronous: each operation returns its result or raises before the next statement runs.
Select an application with `computer.app(id)`, observe it with `app.read(view="both")`, and resolve a control with
`app.element(name_or_path)`. A confirmed target can be used directly, for example `app.element("Save").click()`.

On macOS, when instances share a name or the task specifies a window, call
`computer.list_windows(app_name="Google Chrome")` with the exact display name before reading window content.
Choose a row from `catalogue.windows` by title or use `app_is_foreground` if the task specifies the foreground
instance. Then call `computer.app(row.app_id).read(window=row.window_ref)`.
The catalogue query does not capture pixels, query AX, or change focus. Missing title or foreground information
is `None`. Check `catalogue.stop_reasons` for collection gaps, and allow for hidden windows to be absent.
Unsupported backends return `unsupported_operation`.

Within one tool call, every action receipt is retained while superseded automatic observations of the same scope are
coalesced into the final state. Distinct popups and explicitly printed or full observations are preserved.
Window coordinate actions retain visual feedback and collect available accessibility information independently.
MCP automatically presents initially collected content and then changes (diffs) by default, including observations after
writes and consecutive operations in one call. Each window reports AX/image acquisition and specific gaps separately;
full/diff/unchanged identifies the text presentation mode. Use `app.read(full=True)`, with optional `window=`, to restore full
context; no private client history protocol is required. The SDK's `state.text` remains complete for the captured scope;
diff presentation does not change element bindings or screenshot coordinates.
All screenshots delivered to the model use **pixels in the actual displayed image**:
`(0, 0)` is its top-left, x increases right and y increases down. Follow the `point((x, y))`
example beside the image; the SDK handles resizing, display placement, window offsets and popup placement.
Use `p = computer.point((x, y))` for a desktop image or `p = app.point((x, y), window=ref)`
for an App image. Composite images use their whole displayed canvas. Use `app.click(at=p)`
for scoped input or `computer.click(target=p)` for explicit global input, reusing the same p when switching routes.
Choose points from an image already received, and retain p before later reads if the location will be reused.
Images with unknown sources or expired mappings do not advertise executable point constructors.
Within one Windows MCP call, `computer.point()` continues to use the last desktop image delivered
to the model until a newer image is delivered. Dispatch still checks the capture and current display layout.
In MCP, printing an observation, its `.text`, or a direct text slice reuses automatic presentation instead of adding
another full view. SDK string values, slices, and file output remain unchanged. Use a subtree or `read_text` to bound
the observation scope.

Reads do not activate or raise windows to refresh pixels by default. On macOS and Windows,
`app.read(view="both", allow_foreground=True)` explicitly allows preparation of the confirmed original window;
use `app.read(window=ref, allow_foreground=True)` to select a specific window.
This may change focus and stacking, but sends no global mouse/keyboard input and does not switch back afterward.
The observation's `preparation` record and text retain the requests made; images retain their capture provenance.
An unresolved observation target still returns window choices instead of using this permission to guess a target.
Linux does not yet implement this preparation capability.

Object writes include a bounded related observation independently of their execution result.
Inspect `result.observation`, `result.observation_status`, and `result.observation_error`.
`result.target`, `result.intent`, and `result.status` retain the operation's target, intent and dispatch/local verification.
A `ComputerUseError` for an expired target,
temporarily incomplete target evidence, a modal blocker, or `effect="partial"` / `"possible"` also retains
these fields when the original session can still be observed. Use the attached current observation to
decide the next action. The original error and effect remain unchanged; the SDK does not replay uncertain input.
If native input was dispatched and releasing the operation resources then fails,
`ComputerUseError.action_receipt` retains the dispatch receipt and `scope_cleanup="unknown"` reports unconfirmed
resource finalization. `impact.attempts` reports paired input cleanup separately. The retired session cannot
start the related observation.
Computer global input on macOS and Computer key, typing and paste on Windows return
`observation_status="not_requested"`. Read explicitly to verify the UI effect.
A failed read does not block subsequent macOS global input.

MCP and the reference Agent share one static tool contract. Results present UI content, object identities,
execution progress and specific gaps. Each image retains its ID, source, dimensions and coordinate target through
DeepAgent conversion. Advisory `next` fields have been removed from `ComputerUseError` and `ObservationError`;
`TextPage.next` remains the pagination cursor.

### Python SDK

Save this example as `list_apps.py` and run it with `uv run --no-dev python list_apps.py`:

```python
from computer_use import Computer


with Computer() as computer:  # Qualified low-impact routes remain preferred.
    for app in computer.list_apps():
        print(app.id, app.display_name, app.is_running)
```

`Computer.close()` releases the session's driver resources; the context manager calls it on exit.
The public SDK uses ordinary synchronous calls, including `Observation.read_details()`.
When embedding it in an asyncio or GUI application, run the blocking computer-operation code in a worker thread
so the host remains responsive. Internal driver communication, progress and cleanup continue asynchronously.

`allow_global_input` is a strict boolean session option, defaulting to `True`;
it does not grant macOS system permissions. On macOS, App/Element methods
use AX or directed events throughout preparation, input and recovery. They may
activate/focus their target, but never inject global mouse or keyboard input.
Direct Computer input methods explicitly select global delivery for that call.
Windows/Linux retain the existing ability to use global input for object operations;
the same fixed boundary is future platform work.
`Computer(allow_global_input=False)` rejects explicit global input with `permission_required`.
A macOS object operation lacking a qualified non-global route reports `input_unavailable`.

On macOS, direct Computer input sends system-wide events. The current desktop and
keyboard focus determine the recipient. Bare coordinates are global screen points,
including negative secondary-display coordinates; no prior observation is required.
The SDK does not separately activate a target or restore the previous foreground.
Natural activation and focus changes caused by global clicks are handled by the OS.
This is a programmatic device-input interface: do not pass screenshot pixels as bare coordinates.
When system screen coordinates are already known, no preceding read is necessary:

```python
computer.click(at=(640, 360))
computer.type_text("global input")
computer.press_key("Return")
```

For screenshot input, retain displayed pixels with `point()` and let the SDK convert them.
An existing element can also supply the pointer location:

```python
field = app.element("Input")
field.type_text("scoped", replace=True)
computer.click(target=field)  # Resolve a screen point, then send a global click.
computer.press_key("super+a") # Explicit selection in the current system recipient.
computer.type_text("global")
computer.paste("multiple\nlines")

p = app.point((120, 80), window=ref)  # Pixels in the previously delivered App image; no read or input.
app.click(at=p)          # Directed on macOS.
computer.click(target=p) # Global input at the same destination.

# For a previously delivered desktop image, use its displayed pixels in the same way.
p = computer.point((120, 80))
computer.click(target=p)
```

`Target` retains its element or image-point source independently of later reads.
For Computer pointer input it resolves a screen point once, without handling occlusion,
scrolling into view, or focus. Dispatch does not subsequently lock the original object.
Global drag endpoints may come from different windows or images. Object methods keep
their original target and image-binding semantics.
Global keyboard methods do not accept `target=` or an implicit `replace=True`;
click and select explicitly before typing when needed. Global input returns its dispatch
receipt; read explicitly for observation. A failed read does not block subsequent global
input. Cursor position and foreground remain as the operation leaves them. See `result.impact`.
On Windows, `computer.press_key(key)`, `computer.type_text(text)` and
`computer.paste(text)` send input to the current system recipient without a prior screenshot
or accessibility read. Establish focus explicitly when needed. Mouse operations still use the
existing screenshot coordinate mapping.
Windows paste leaves the published text on the system clipboard until another write replaces it.
A dispatch receipt means the paste chord was sent. Verify the target's paste effect before
publishing the next payload so a slow application can read the intended text.
Linux desktop input retains its existing screenshot context and uses App or Element methods for paste.

`App` identifies an application scope; `list_apps()` lists applications. App and
desktop observations publish window references. Use `app.read(window=ref)` for a
specific window. `element`, `point`, `click`, `scroll`, `drag`, `type_text`, `paste`, and
`press_key` accept the same optional `window=` parameter. Selecting a window and
ordinary reads do not activate it. Titles and positions describe windows; native
identity defines their lifetime.

On Windows, use `app = computer.app(window_title=title)` when a task gives a
full window title. The first `app.read()` binds a unique, case-sensitive match
from the current desktop's visible top-level native windows. Multiple matches
return `ambiguous_target`. A catalogue gap that prevents a unique match returns
`observation_incomplete`. Reuse the handle after title changes. Closing the
bound window makes the handle stale.

Reads, `read_details()` and action follow-up from this handle cover only the
bound window. An ordinary `app.read(window=ref)` may also include confirmed
owned windows. The title-bound App rejects `read(menu_path=...)` with
`invalid_request`. Menu paths are available through ordinary App scopes on
supporting platforms.

The public object model is `Computer → App → Element`; there is no public Window
class or window factory. Element numbers are shared across read scopes, and
`app.element(number)` keeps its original control after another window is read.
A name lookup with `window=ref` prepares its query scope within the same call;
it does not require a preceding public read to switch windows. Omitting `window=`
uses the latest actually delivered, unambiguous lookup or input context rather
than the current system focus.

On macOS and Windows, `app.close_window(ref)` requests normal close of that exact
window. macOS resolves the declared close control, including when screen-sharing
controls cover its title-bar buttons. Windows uses the window's UI Automation
`Window.Close` action when the window exposes it. Both routes use the existing
input and observation flow.
The application may decline the request or show a confirmation; the SDK does not
choose save or discard. The receipt uses `intent="closeWindow"`; `dispatched`
means the request was sent, while `window_lifecycle` and the attached observation
report the available state. Linux support remains follow-up work.

After `close_window` on a title-bound App, the attached observation is limited
to the original window and `choices` is empty. Observe a separate confirmation
window through its own scope when the task authorizes it. A receipt confirms
closure when `window_lifecycle` is `closed`.

Each window retains its current delivered image independently. After reading A
and B, `app.click(at=(x, y), window=a)` can reuse A's displayed image. Composite
images retain whole-image coordinates and map the actual receiver internally.
Undelivered samples do not replace these mappings. A closed window's old reference
and captures expire without retiring the App. Each `choices` item is a `WindowInfo`
with an `id` accepted by `window=` and a descriptive `title`. Related observations
may reveal new windows without rebinding existing Elements or image targets.
The shared API applies on all platforms; real multi-window acceptance is currently
performed on macOS, with Windows/Linux validation remaining follow-up work.

On macOS, live element references survive unrelated layout-wrapper changes, partial observations,
and cache pressure from other references. The SDK refreshes necessary operation facts.
A destroyed element, closed/reopened window, or actual document ownership change still rejects the
old reference and includes the related observation when available. `app.element(ref)` can retrieve a reference already published by that application; constructing
the handle does not claim its target is alive or authorize access. Actual reads and writes check
the original window scope and lifetime, so failed writes can return current observation in the same call.
Action identities are independent
of bounded historical text caches; minimal reference metadata remains until confirmed retirement or
session end. This native stability improvement and its real SDK/MCP validation cover macOS;
the corresponding Windows/Linux native changes and validation are follow-up work.

The standalone SDK returns source screenshots and their original pixel coordinates.
When MCP resizes an image, public observation text and PNG share the delivered coordinate
space, including text split into lines or assembled into strings. Image point locators map
back using each axis's actual encoded size. Arbitrary PIL crops do not acquire an input
mapping. On macOS, bare Computer coordinates always mean screen points; use `point()`
to explicitly convert image coordinates.

Original native/AX details returned by `read_details()` retain their declared screen/AX
coordinate space; do not treat them as screenshot pixels.

Execution and observation have separate results. `status` reports dispatch or
available verification, not completion of the application's business operation.
`observation_status` is `available`, `partial`, `unavailable`, or `not_requested`.
`window_lifecycle` is `observed`, `closed`, `unknown`, or `not_applicable`; absence
of pixels alone does not prove closure. When the original window is gone, related
observations can still provide current window choices. Existing window references and Element
handles do not switch to those alternatives. `observation_error.session_usable` tells the caller whether
the original session remains usable; it is not permission to replay uncertain input.

`ActionResult.impact` and `ComputerUseError.impact` expose read-only execution
evidence. `global_input_allowed` records the session grant. Each `attempts` item
separates `declared_global_input` and `preparation_potential` from dispatch,
cleanup, and `actual` native evidence. `actual.effects` groups calls by stage,
device, transport, mechanism, and invocation state; it is not a per-keystroke log
or proof that the app consumed the input. `foreground_changed` and
`clipboard_restoration` retain their known or unknown outcomes.
Missing `impact`/`actual` or partial `actual.coverage` means evidence is unavailable
or incomplete, not that nothing happened. Even complete coverage describes only
that native execution step; it excludes separate reads, later session cleanup,
and the application's asynchronous consequences.

Each SDK operation targets roughly 1000ms without a default total deadline. Native requests
retain their own timeouts. An explicitly supplied operation deadline is shared across nested
lookup, preparation, requests and observation; recovery does not renew it.
Returned native outcomes are retained before subsequent observation or
cleanup. `sdk_operation_end` diagnostics record elapsed time, target overruns, deadline overruns, observation
completeness and gaps. MCP metadata separately records the whole tool call, including
startup, multiple SDK operations and final delivery.

On macOS, post-action observation can use application change notifications to collect
new windows or missing components within the original budget. Image-only observation
does not initialize accessibility for this feature. Fixed windows retain their own
delivered image mappings; hiding and reappearing are distinct from destruction.
These notification, native input and capture improvements currently target macOS;
the corresponding Windows/Linux implementations and desktop validation are follow-up work.

## 5. Development packages

For the Python SDK alone, use `uv sync --no-dev`. Plain `uv sync` installs development, test and build tools. See the [packaging guide](../PACKAGING.md) for development packages and reusable backend artifacts.
