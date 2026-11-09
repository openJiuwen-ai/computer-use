<div align="center">

# Computer Use

**openJiuwen's computer-operation engine and agent: agents that read the screen, act on controls and fill forms the way people do**

English | [简体中文](README_CN.md)

<a href="LICENSE"><img src="https://img.shields.io/badge/license-Apache--2.0-green.svg" alt="License" /></a>
<img src="https://img.shields.io/badge/python-3.12%2B-blue.svg" alt="Python" />
<img src="https://img.shields.io/badge/platform-macOS%20%7C%20Windows%20%7C%20Linux-lightgrey.svg" alt="Platform" />

</div>

## Introduction

Computer Use is the desktop-operation component of the openJiuwen Agent System Service layer. It reads the accessibility tree and screenshots of desktop applications, operates controls through mouse and keyboard input, and returns operation receipts. It supports macOS, Windows and Linux.

Developers can adopt it in two ways:

**Option 1: integrate the computer_use engine.** Connect an agent through MCP or call the Python SDK directly. The MCP tool `computer_use(title, code)` runs Python against the `Computer → App → Element` interface, with variables retained across calls. Agents use accessibility trees and screenshots to locate controls and verify operation results.

**Option 2: integrate the Computer Use DeepAgent.** Use the agent assembled on openJiuwen DeepAgent through its standard `invoke()`, `stream()` and `Runner` interfaces. Applications pass natural-language tasks to the agent, which observes the desktop, performs operations and checks their results.

## Quick Start

### Connect your existing agent with one prompt

Send the appropriate prompt to a coding agent that can edit your project and run commands:

**Integrate DeepAgent:**

```text
Read https://gitcode.com/openJiuwen/computer-use/blob/main/docs/deepagent.en.md, follow its instructions to build and install Computer Use from source, integrate Computer Use DeepAgent into this project, and verify the integration.
```

**Connect over MCP:**

```text
Read https://gitcode.com/openJiuwen/computer-use/blob/main/docs/mcp.en.md, follow its instructions to build and install Computer Use from source, connect it to the agent I am using over MCP, and verify the integration.
```

## Architecture

The project has four layers: interfaces, agent assembly, the execution engine and OS adapters. Applications can integrate DeepAgent, connect an external agent over MCP, or call the SDK directly:

```mermaid
flowchart TD
    App["Agent application"] --> Agent["computer_use_agent · DeepAgent"]
    CLI["computer_use_agent/cli"] --> Agent
    Agent --> MCP["interface_adapter/mcp"]
    External["External agent"] --> MCP
    MCP --> Engine["computer_use_engine · Python SDK"]
    Python["Python application"] --> Engine
    Engine --> OS["os_adapter · macOS / Windows / Linux"]
    OS --> Desktop["Desktop applications"]
```

DeepAgent manages model calls, conversation context and the tool loop. The engine manages the `Computer → App → Element` interfaces, target identity, observations and operation routing. OS adapters resolve native objects, prepare the input receiver and dispatch operations. A completed object action returns its `dispatched` or `verified` receipt and a bounded related observation. On macOS, Computer global input returns its dispatch receipt; request observation explicitly when needed. Agents use that observation, and further reads when needed, to judge task results. Failure of the related read does not erase or replay the action.

## Features

App methods accept an optional `window=` scope; retained Elements and image Targets keep their own bindings.
On macOS, confirmed dropdowns and menus are delivered as attached content of their host. A separate
native capture or input receiver does not automatically become a new working window. Related content
is returned with object actions; unknown relationships remain explicitly labelled sources, without
widening the original window's input scope. Observations and same-source menu reads prefer diffs;
the first delivered source or explicit `full=True` produces complete content. Each explicit read
samples separately, including consecutive reads in one tool call. Automatic post-action
observations return scoped text and receipts. Request an image explicitly when needed.
On macOS, set `COMPUTER_USE_AX_SUBTREE_PREFETCH=1` before launching the SDK or MCP server to
enable bulk attribute prefetch for eligible window trees. Child order, geometry and identity are
read live. Missing or unavailable prefetch data uses ordinary AX collection. Image coordinates are
pixels from the top-left of the delivered PNG, and one missing capture does not invalidate other usable pieces.
On macOS, an ordinary single-left App coordinate click that precisely hits a selectable item in an
open menu may use that item's Press action. Other coordinates retain their directed mouse route.
Use `app.click(at=p, force_pointer=True)` when the mouse-event sequence itself is required. This
option never grants global input or changes Computer/Element semantics. Windows/Linux keep their
existing coordinate mouse routes.

On macOS, object input preparation conditionally returns foreground control changed temporarily
by the SDK while retaining the target's internal input context. This is not an unconditional restore
after every operation: explicit activation and subsequent user choices are preserved. Workspace
switches and session shutdown do not send an extra deactivation notification that would undo an open
menu; paired input and observers are still released. Equivalent foreground restoration and menu
continuity improvements for Windows/Linux remain future work. Actual-impact receipts distinguish
foreground changes, restoration outcomes and global input; they do not certify application business results.
Menus that need foreground can retain it until a later selection or cancellation. The same interaction
keeps its first borrowed context and, when possible, restores the exact original window. Nothing acts
on the desktop between calls. Explicit Computer global input only discards the old restoration claim;
it adds no focus preparation or automatic return, and never moves the real pointer back automatically.
Native attached-content discovery and multiple AX sources sharing one physical window are also
implemented first on macOS; Windows/Linux require separate implementation and validation.

- **DeepAgent integration**: standard openJiuwen invocation, streaming and Runner integration, with computer-operation tools reusable in existing DeepAgent applications.
- **Single-tool code execution**: the model writes Python, variables persist across calls, one call can complete a multi-step workflow.
- **Accessibility-tree first**: elements are numbered by decimal refs; `element(path)` queries, paged text reads and menu reads are supported.
- **Verifiable receipts**: actions retain `target / intent / status` independently of `observation_status` and `observation_error`. Input failures expose `code / effect / target / detail / impact`. `unsupported_operation` means the required capability or operation is unavailable; `permission_required` identifies a missing input grant.
- **Background-first input**: prefer element and application-directed operations; macOS object methods remain non-global. Explicitly call direct Computer input methods when global mouse or keyboard input is needed.
- **Cross-platform native drivers**: one each for macOS, Windows and Linux behind the same protocol.
- **Observable**: every execution is journaled as JSONL under `~/.computer-use/logs`, including code, observations and receipts.

Name and path lookup first reuse the latest returned semantic observation, including an action's
observation. Missing, incomplete or known-invalid evidence falls back to the existing live query in
the same call. Use `app.element("Save", fresh=True)` to explicitly query current facts. `fresh` only
controls names and paths: numbers and saved Elements keep their original binding, including after
an application replaces a control with another of the same name. Cached lookup preserves all live
write checks, preparation and restoration; it does not detect unobserved external object replacement.
Use `result.observation.read_details(element=ref)` for already captured details. Explicit `app.read()`,
`element.read()` and `element.read_text()` still acquire fresh observations; `full=True` controls presentation.

## Project Structure

The four layers and supporting files are organized as follows:

```
computer-use/
├── interface_adapter/       # access layer: MCP integration
│   └── mcp/                 # stdio MCP host and persistent Python execution
├── computer_use_agent/      # agent assembly: DeepAgent, context and tool integration
│   └── cli/                 # direct conversation and configuration
├── computer_use_engine/     # engine: public SDK, observation and operation pipelines
├── os_adapter/              # OS adaptation: native platform backends and resources
│   ├── macos/
│   ├── windows/
│   └── linux/
├── docs/                    # build, installation and integration guides
├── scripts/                 # native build, permissions and resource generation
├── tests/                   # unit tests
├── pyproject.toml           # package metadata, dependencies and CLI entry points
├── LICENSE                  # Apache License 2.0
└── OPEN_SOURCE_SOFTWARE_NOTICE.md
```

## Documentation

- [MCP build, installation and integration](docs/mcp.en.md)
- [DeepAgent build, installation and integration](docs/deepagent.en.md)
- [Development packaging](PACKAGING.md)

- [DeepAgent integration](computer_use_agent/agent.py): agent construction, standard invocation and conversation lifecycle.
- [DeepAgent tools](computer_use_agent/toolkit.py): tools for existing openJiuwen applications.
- [SDK API reference](computer_use_engine/__init__.pyi): public interfaces and parameter types for `Computer`, `App` and `Element`.
- [MCP server](interface_adapter/mcp/server.py): tool schemas, execution instructions and global-input authorization.
- [Native setup commands](scripts/native.py): platform builds, permission checks and Linux session setup.
- [Direct conversation](computer_use_agent/cli/main.py): CLI options and interactive commands.

## Contributing

Issues and pull requests are welcome. Before submitting, please make sure that:

1. native driver changes come with instructions that build cleanly on a local machine;
2. engine-layer changes introduce no dependency on any agent framework;
3. new behavior is covered by unit tests or a reproducible probe script.

## License

This project is licensed under Apache License 2.0.

This product serves solely as a workflow orchestration tool and does not embed any AI model capabilities. When users integrate AI models for specific business scenarios, they shall bear full responsibility for compliance obligations under the EU AI Act and other relevant regulatory frameworks.
