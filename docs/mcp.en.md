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
`uv pip install '/absolute/path/to/<wheel-filename>.whl[mcp]'`,
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

On macOS, App and Element operations use object or directed input. Call a direct Computer method for global
input. Add `--no-allow-global-input` to the connector's `args` to restrict global input. The target computer
still requires system permissions.

## 4. Interface reference

SDK methods return synchronously. Select an application with `computer.app(id)`, observe it with
`app.read(view="both")`, then use methods such as `app.element(name_or_path).click()`.
Every explicit read samples again. MCP first shows the complete observation and then shows diffs by default.
Use `app.read(full=True)` for the full view. In the SDK, `observation.text` remains complete.

Every action keeps its receipt in a batched call. Automatic observations of the same scope may be coalesced.
Distinct popups and explicit reads remain separate. Object writes can include related text in
`result.observation`, with `observation_status` and `observation_error`. An observation error leaves the action
receipt available. Request screenshots explicitly and use the UI to judge the application outcome.

### Python SDK

Save this example as `list_apps.py` and run it with `uv run --no-dev python list_apps.py`.

```python
from computer_use import Computer


with Computer() as computer:
    for app in computer.list_apps():
        print(app.id, app.display_name, app.is_running)
```

The context manager calls `Computer.close()` on exit. When embedding the SDK in an asyncio or GUI application,
run blocking SDK calls in a worker thread.

**Window scope.** The public interface is `Computer → App → Element`. Pass a window reference from observation
`choices` to `app.read(window=ref)` or to App methods such as `element`, `point` and `click`.
Existing Elements and image points retain their bindings. Name and path lookups can reuse the latest delivered
observation. Use `app.element(name, fresh=True)` for a live lookup.

On macOS, call `computer.list_windows(app_name=display_name)` with the exact display name to find windows across
same-name instances. Select a row from `catalogue.windows` and call `computer.app(row.app_id).read(window=row.window_ref)`.
On Windows, `computer.app(window_title=title)` binds a unique window by full title.
On macOS and Windows, `app.close_window(ref)` requests normal window closure. The caller handles save prompts.

**Screenshot coordinates.** Use pixels in the actual displayed image, with the origin at the top left.
Follow the example next to the image. The SDK converts scale and placement.

```python
p = app.point((120, 80), window=ref)  # From an App screenshot already received.
app.click(at=p)
computer.click(target=p)             # Explicit global click at the same point.

p = computer.point((120, 80))         # From a desktop screenshot already received.
computer.click(target=p)
```

A single left App coordinate click on macOS can use a selectable item's Press action when it hits an open menu item.
Use `app.click(at=p, force_pointer=True)` when the mouse event is required.

**Foreground and global input.** Ordinary reads on macOS and Windows observe the window in its current state.
Pass `app.read(window=ref, allow_foreground=True)` to allow observation preparation and receive its `preparation` record.
On macOS, direct Computer input goes to the current system desktop or keyboard focus. Bare coordinates are screen points
and can include negative coordinates on secondary displays. Explicit global input can dispatch directly. Read separately
for UI feedback. Set `Computer(allow_global_input=False)` to restrict the session's global input.

See the [SDK API](../computer_use_engine/__init__.pyi) for complete signatures. To install the SDK alone, use
`uv sync --no-dev`. See the [packaging guide](../PACKAGING.md) for development packages.
