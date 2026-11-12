# Build Computer Use from source

[Back to README](../README.md) | [简体中文](build-from-source.md)

A source install has two steps: `uv sync` installs the Python SDK and compiles the shared Rust accessibility extension; `computer-use-build` prepares the platform desktop driver. Install on the computer whose desktop you want to operate. A prebuilt wheel assembled as described in the [packaging guide](../PACKAGING.md) includes both components. On Linux, run `computer-use-build` to prepare the driver environment after installing the wheel.

## 1. Install the toolchain and system dependencies

Every platform needs Git, [uv](https://docs.astral.sh/uv/getting-started/installation/), a standard CPython 3.11–3.15 interpreter that matches the target architecture, and Rust 1.85.1 (`cargo` and `rustc`) on PATH. The examples use Python 3.12, which also works with DeepAgent's Python 3.11–3.13 requirement.

After installing [rustup](https://rustup.rs/) and the platform tools listed below, run:

```sh
rustup toolchain install 1.85.1
rustup run 1.85.1 rustc --version
uv python install 3.12
```

| Target | Build dependencies | Desktop runtime |
| --- | --- | --- |
| macOS 14+, Apple silicon or Intel | Apple developer tools with Swift 6, the macOS SDK, the linker, and `codesign`. | A logged-in desktop session. Grant Accessibility access to the host running the SDK or driver, and Screen Recording access for screenshots. |
| Windows x64, Windows 10 1903+ | [Visual Studio Build Tools](https://visualstudio.microsoft.com/downloads/#build-tools-for-visual-studio) with the Desktop development with C++ workload, including x64 MSVC tools and a Windows SDK for the Rust extension. The UIA driver uses .NET Framework 4.8 and its 64-bit `csc.exe`. | A logged-in interactive desktop. Window capture uses the system's WinRT metadata. |
| glibc Linux, x86_64 or ARM64 | The system C compiler and linker, plus the distribution's Python 3.11+, venv module, and PyGObject. | AT-SPI 2, the graphical session's D-Bus, and the X11 and xkbcommon libraries. Full screenshot and pointer support uses X11. Capabilities under Wayland depend on the session. |

**macOS.** Use `xcode-select -p` and `xcrun swift --version` to check the active developer tools. Use matching Python and Rust architectures, such as ARM64 for both on Apple silicon.

**Windows.** Use x64 CPython and the `x86_64-pc-windows-msvc` Rust toolchain. In an x64 Developer PowerShell, run `Get-Command link.exe` to check the MSVC linker. The driver also looks for `%WINDIR%\Microsoft.NET\Framework64\v4.0.30319\csc.exe`, which the modern .NET SDK does not supply.

**Linux.** Install these packages on Debian or Ubuntu. Use the equivalent packages on other distributions.

```sh
sudo apt install build-essential python3-gi python3-venv at-spi2-core \
  gir1.2-atspi-2.0 libx11-6 libxkbcommon0
```

The SDK can use a uv-managed Python. The Linux driver uses `/usr/bin/python3` by default to access the system's PyGObject. To choose a different driver interpreter, set `COMPUTER_USE_PYTHON` to a system Python 3.11+ that can load `gi.repository.Atspi`. Run the permissions check from the target graphical session so the driver inherits its display and D-Bus environment.

## 2. Install the SDK and prepare the driver

```sh
git clone https://gitcode.com/openJiuwen/computer-use.git
cd computer-use
uv sync --python 3.12 --no-dev
uv run --no-sync computer-use-build
uv run --no-sync computer-use-permissions
```

Add `--extra mcp` or `--extra agent` to `uv sync` for that integration. The project environment defaults to `.venv`. On Windows and macOS the build command compiles the desktop driver. On Linux it prepares a separate driver environment in `os_adapter/linux/.venv`. On macOS, run `uv run --no-sync computer-use-permissions --request` to prompt for missing permissions and approve them in System Settings.

## 3. Verify and update the installation

List the running apps, then replace `APP_NAME` with the name of an app from the list that has an open window. The second command reads its accessibility tree.

```sh
uv run --no-sync python -c "from computer_use import Computer; print(Computer().list_apps())"
uv run --no-sync python -c "from computer_use import Computer; print(Computer().app('APP_NAME').read(view='tree').text)"
```

- After editing Python source, restart the SDK host, MCP, or Agent session. After changing dependencies or entry points, rerun your original `uv sync` command.
- After editing Rust source or changing the Python version, add `--reinstall-package openjiuwen-computeruse` to your original `uv sync` command to rebuild the extension, then restart the session.
- After editing a platform driver, rerun `uv run --no-sync computer-use-build` and restart the session.

Next: [MCP integration](mcp.en.md) · [DeepAgent integration](deepagent.en.md) · [Build and assemble wheels](../PACKAGING.md).
