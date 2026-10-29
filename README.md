<div align="center">

# Computer Use

**openJiuwen's computer-operation engine and agent: agents that read the screen, act on controls and fill forms the way people do**

English | [简体中文](README_CN.md)

<a href="LICENSE"><img src="https://img.shields.io/badge/license-Apache--2.0-green.svg" alt="License" /></a>
<img src="https://img.shields.io/badge/python-3.12%2B-blue.svg" alt="Python" />
<img src="https://img.shields.io/badge/platform-macOS%20%7C%20Windows%20%7C%20Linux-lightgrey.svg" alt="Platform" />

</div>

## Introduction

Computer Use is the desktop-operation component of the openJiuwen Agent System Service layer. It packages "operating a computer" as an independently deployable execution runtime: it reads the accessibility tree and window captures of native applications, dispatches mouse and keyboard input by element or by coordinates, and returns a verifiable receipt for every step. Any agent framework can integrate it over MCP or the Python SDK; openJiuwen's JiuwenSwarm uses it as a sub-agent. macOS, Windows and Linux are supported.

Developers can adopt it in two ways:

**Option 1: use the backend computer_use engine only.** Unlike one-tool-per-action designs, the engine exposes a single tool to the model, `computer_use(title, code)`: the model writes Python against the three-level `Computer → App → Element` SDK to complete multi-step work. The accessibility tree comes first and screenshots second; actions return only `dispatched` or `verified` receipts, and outcomes must be confirmed by reading the UI again.

**Option 2: use the DeepAgent that embeds the computer_use engine.** Send the task to the agent directly; the agent recognizes, observes and operates the desktop to complete it.

## Quick Start

### Install and prepare the host

Install Python 3.12 or later and `uv`, then clone the repository and install its dependencies:

```bash
git clone https://gitcode.com/openJiuwen/computer-use.git
cd computer-use
uv sync --group dev
```

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

Build or prepare the native backend, then check desktop access:

```bash
uv run computer-use-build
uv run computer-use-permissions
```

On macOS, use `uv run computer-use-permissions --request` to request missing permissions. Approve them in **System Settings → Privacy & Security**, then run the check again. Run the commands from the same terminal or host connection that will launch MCP.

### Start the MCP server

```bash
uv run computer-use-mcp --desktop exclusive
```

The server uses stdio. For a client that accepts an `mcpServers` configuration, use:

```json
{
  "mcpServers": {
    "computer-use": {
      "command": "uv",
      "args": [
        "--directory", "/absolute/path/to/computer-use",
        "run", "computer-use-mcp", "--desktop", "exclusive"
      ]
    }
  }
}
```

Replace the checkout path with its absolute path on the target computer. `exclusive` allows foreground preparation and desktop-wide input; `shared` keeps input within the selected App or Window. Without an explicit mode, `COMPUTER_USE_DESKTOP` is used, defaulting to `shared`.

Inside the `computer_use` tool, `computer`, `print()` and `print_image()` are available. Start by discovering application references:

```python
print(await computer.list_apps())
```

Select an application with `computer.app(id)`, observe it with `await app.read(view="both")`, and resolve a control with `await app.element(name_or_path)` before operating it.

### Use the Python SDK

Save this example as `list_apps.py` and run it with `uv run python list_apps.py`:

```python
import asyncio

from computer_use import Computer


async def main():
    computer = Computer()
    for app in await computer.list_apps():
        print(app.id, app.display_name, app.is_running)


asyncio.run(main())
```

## Architecture

The MCP host exposes the SDK to an agent through persistent Python execution. Applications and windows share the `UI` interface, while elements retain their own operation targets:

```text
Agent / MCP client
        ↓ stdio
interface_adapter/mcp
        ↓ Python SDK
Computer → App / Window → Element
        ↓ read / query / write pipelines
os_adapter: macOS / Windows / Linux
        ↓ native accessibility, capture and input
Desktop applications
```

The shared Python layer manages handles, target scope, observation presentation and operation routing. Each native backend resolves operating-system objects, prepares the input receiver and dispatches the selected operation. Action receipts describe dispatch or verification; agents read the interface again to confirm the task's result.

## Features

- **Single-tool code execution**: the model writes Python, variables persist across calls, one call can complete a multi-step workflow.
- **Accessibility-tree first**: elements are numbered by decimal refs; `element(path)` queries, paged text reads and menu reads are supported.
- **Verifiable receipts**: every action returns `dispatched` / `verified`; failures are structured as `code / effect / next`.
- **Shared and exclusive desktop modes**: background-window-directed input by default without stealing focus; foreground assistance on demand.
- **Cross-platform native drivers**: one each for macOS, Windows and Linux behind the same protocol.
- **Observable**: every execution is journaled as JSONL under `~/.computer-use/logs`, including code, observations and receipts.

## Project Structure

The core components are organized as follows:

```
computer-use/
├── interface_adapter/mcp/   # stdio MCP host and persistent Python execution
├── computer_use_engine/     # computer_use SDK, observation and operation pipelines
├── os_adapter/              # native platform backends and shared resources
│   ├── macos/
│   ├── windows/
│   └── linux/
├── scripts/                 # native build, permissions and resource generation
├── tests/                   # unit tests
├── pyproject.toml           # package metadata, dependencies and CLI entry points
├── LICENSE                  # Apache License 2.0
└── OPEN_SOURCE_SOFTWARE_NOTICE.md
```

## Documentation

- [SDK API reference](computer_use_engine/__init__.pyi): public interfaces and parameter types for `Computer`, `App`, `Window` and `Element`.
- [MCP server](interface_adapter/mcp/server.py): tool schemas, execution instructions and desktop-mode arguments.
- [Native setup commands](scripts/native.py): platform builds, permission checks and Linux session setup.

## Contributing

Issues and pull requests are welcome. Before submitting, please make sure that:

1. native driver changes come with instructions that build cleanly on a local machine;
2. engine-layer changes introduce no dependency on any agent framework;
3. new behavior is covered by unit tests or a reproducible probe script.

## License

This project is licensed under Apache License 2.0.

This product serves solely as a workflow orchestration tool and does not embed any AI model capabilities. When users integrate AI models for specific business scenarios, they shall bear full responsibility for compliance obligations under the EU AI Act and other relevant regulatory frameworks.
