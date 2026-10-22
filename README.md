<div align="center">

# Computer Use

**openJiuwen's computer-operation engine and agent: agents that read the screen, act on controls and fill forms the way people do**

English | [简体中文](README_CN.md)

<a href="LICENSE"><img src="https://img.shields.io/badge/license-Apache--2.0-green.svg" alt="License" /></a>
<img src="https://img.shields.io/badge/python-3.11%2B-blue.svg" alt="Python" />
<img src="https://img.shields.io/badge/platform-macOS%20%7C%20Windows%20%7C%20Linux-lightgrey.svg" alt="Platform" />

</div>

## Introduction

Computer Use is the desktop-operation component of the openJiuwen Agent System Service layer. It packages "operating a computer" as an independently deployable execution runtime: it reads the accessibility tree and window captures of native applications, dispatches mouse and keyboard input by element or by coordinates, and returns a verifiable receipt for every step. Any agent framework can integrate it over MCP or the Python SDK; openJiuwen's JiuwenSwarm uses it as a sub-agent. macOS, Windows and Linux are supported.

Developers can adopt it in two ways:

**Option 1: use the backend computer_use engine only.** Unlike one-tool-per-action designs, the engine exposes a single tool to the model, `computer_use(title, code)`: the model writes Python against the three-level `Computer → App → Element` SDK to complete multi-step work. The accessibility tree comes first and screenshots second; actions return only `dispatched` or `verified` receipts, and outcomes must be confirmed by reading the UI again.

**Option 2: use the DeepAgent that embeds the computer_use engine.** Send the task to the agent directly; the agent recognizes, observes and operates the desktop to complete it.

## Quick Start

To be added.

## Architecture

To be added.

## Features

- **Single-tool code execution**: the model writes Python, variables persist across calls, one call can complete a multi-step workflow.
- **Accessibility-tree first**: elements are numbered by decimal refs; `find` path queries, paged text reads and menu reads are supported.
- **Verifiable receipts**: every action returns `dispatched` / `verified`; failures are structured as `code / effect / next`.
- **Shared and exclusive desktop modes**: background-window-directed input by default without stealing focus; foreground assistance on demand.
- **Cross-platform native drivers**: one each for macOS, Windows and Linux behind the same protocol.
- **Observable**: every execution is journaled as JSONL under `~/.computer-use/logs`, including code, observations and receipts.

## Project Structure

The project is organized in four layers (top down):

```
computer-use/
├── interface_adapter/       # system-service access layer: native JiuwenSwarm rail integration; A2A / MCP / CLI for third parties
├── computer_use_agent/      # agent-loop assembly layer: a DeepAgent built on agent-core, covering context management,
│                            #   observation, decision, cascaded grounding, multi-path selection and atomic compilation
├── computer_use_engine/     # engine layer: turns each OS's raw interfaces into an agent-readable common interface,
│                            #   enhanced drivers, MCP access for external agents such as Codex
├── os_adapter/              # OS adaptation layer: raw interfaces of macOS, Linux, Windows
│   ├── macos/
│   ├── windows/
│   └── linux/
├── tests/                   # unit tests
├── docs/                    # documentation
├── LICENSE                  # Apache License 2.0
└── OPEN_SOURCE_SOFTWARE_NOTICE.md
```

## Documentation

To be added.

## Contributing

Issues and pull requests are welcome. Before submitting, please make sure that:

1. native driver changes come with instructions that build cleanly on a local machine;
2. engine-layer changes introduce no dependency on any agent framework;
3. new behavior is covered by unit tests or a reproducible probe script.

## License

This project is licensed under Apache License 2.0.

This product serves solely as a workflow orchestration tool and does not embed any AI model capabilities. When users integrate AI models for specific business scenarios, they shall bear full responsibility for compliance obligations under the EU AI Act and other relevant regulatory frameworks.
