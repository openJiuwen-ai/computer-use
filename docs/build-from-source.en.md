# Build Computer Use from source

[Back to README](../README.md) | [简体中文](build-from-source.md)

Build a wheel containing the platform driver and the Rust accessibility read extension, then install it on the computer whose desktop you want to operate. The same wheel can run the Python SDK, start the bundled DeepAgent, or connect to your own Agent through MCP.

## 1. Install the toolchain and system dependencies

Install Git, [uv](https://docs.astral.sh/uv/getting-started/installation/), and [rustup](https://rustup.rs/). The SDK and MCP use standard CPython 3.11–3.15, and DeepAgent uses 3.11–3.13. The commands below use 3.12. If you choose another minor version, use it for both the build and the installation.

| Target | Build dependencies | Desktop runtime |
| --- | --- | --- |
| macOS 14+, Apple silicon or Intel | [Xcode or Command Line Tools](https://developer.apple.com/documentation/xcode/installing-the-command-line-tools) with Swift 6 or later, the macOS SDK, the linker, and `codesign`. | A logged-in desktop session. Grant Accessibility access to the host running the SDK or driver, and Screen Recording access for screenshots. |
| Windows 10 1903+ or Windows 11, x64 | [Visual Studio Build Tools](https://visualstudio.microsoft.com/downloads/#build-tools-for-visual-studio) with the Desktop development with C++ workload (x64 MSVC tools and Windows SDK), plus the 64-bit `csc.exe` from .NET Framework. | A logged-in interactive desktop and [.NET Framework 4.8 or 4.8.1](https://learn.microsoft.com/en-us/dotnet/framework/get-started/system-requirements). |
| glibc Linux, x86_64 or ARM64 | A system C compiler and linker. The driver uses the distribution's CPython and PyGObject, described below. | AT-SPI 2, the graphical session's D-Bus, and the X11 and xkbcommon libraries. Screenshots and simulated mouse and keyboard input require an X11 session. On Wayland, the SDK can use the accessibility trees and actions exposed by apps. |

**macOS.** Run `xcode-select -p` and `xcrun swift --version` to check the selected developer tools. Use matching Python and Rust architectures, such as ARM64 for both on Apple silicon.

**Windows.** Use x64 CPython and the `x86_64-pc-windows-msvc` Rust toolchain. Run the build commands from the Visual Studio x64 Developer PowerShell. Use these commands to check the linker and C# compiler. The second should print `True`.

```powershell
Get-Command link.exe
Test-Path "$env:WINDIR\Microsoft.NET\Framework64\v4.0.30319\csc.exe"
```

**Linux.** The commands below use Debian 13. On other distributions, install the equivalent packages and run the AT-SPI check below.

```sh
sudo apt update
sudo apt install build-essential python3-gi python3-venv at-spi2-core \
  gir1.2-atspi-2.0 libx11-6 libxkbcommon0
```

The source wheel packages driver dependencies for the system Python ABI. It uses `/usr/bin/python3` by default. Use system CPython 3.11–3.14 with access to `gi.repository.Atspi` to match the pinned [Pillow 11.3.0 wheels](https://pypi.org/project/Pillow/11.3.0/#files). The SDK can use a different Python version. If you set `COMPUTER_USE_PYTHON`, use the same driver interpreter at build time and runtime.

```sh
/usr/bin/python3 -c "import sys, gi; gi.require_version('Atspi', '2.0'); from gi.repository import Atspi; print(sys.version.split()[0], hasattr(Atspi.Accessible, 'clear_cache_single'))"
```

The check should print `True` after the Python version. Debian 13's AT-SPI 2.56.2 passed verification. On Debian 12 with AT-SPI 2.46.0, the wheel built and installed, but application enumeration timed out. If the check returns `False`, use a distribution that provides a newer AT-SPI package. [Debian 13 AT-SPI package](https://packages.debian.org/trixie/at-spi2-core) · [Debian 12 AT-SPI package](https://packages.debian.org/bookworm/at-spi2-core)

After installing the platform tools, install the project's pinned Rust 1.85.1 and the example's Python 3.12. Make sure `rustup`, `cargo`, and `rustc` are on `PATH` in the current shell.

```sh
rustup toolchain install 1.85.1
rustup run 1.85.1 rustc --version
uv python install 3.12
```

## 2. Build the wheel

Create a dedicated environment for the build tools and the Python dependencies imported during driver packaging. The first build needs network access to download dependencies and the pinned Unicode data.

```sh
git clone https://gitcode.com/openJiuwen/computer-use.git
cd computer-use
uv venv --python 3.12 .build/venv
uv pip install --python .build/venv --group build -r pyproject.toml "pip>=23.1"
rustup run 1.85.1 uv run --no-project --python .build/venv python -m build --wheel --no-isolation
```

Find the wheel in `dist/`. A complete build has the target CPython and platform tags in the filename, such as `openjiuwen_computeruse-0.0.1-cp312-cp312-macosx_14_0_arm64.whl`. Build separately for each Python minor version and platform architecture.

A source build can still produce an installable wheel if a native component fails to build. `Computer Use driver unavailable (native_build_failed)` identifies a driver build failure, and `Rust accessibility engine unavailable (native_build_failed)` identifies a Rust extension build failure. Check the output before the warning. If you only see the warning, check that the tools from step 1 are available in the current shell. Fix the error, rebuild, and read a real window in step 4 to verify the installed wheel.

## 3. Install the wheel in a separate environment

Start in the source directory and create a runtime directory alongside it. Replace `<wheel>` in the install command with the actual filename from step 2, including the `.whl` suffix. Choose one command for your use case.

```sh
cd ..
mkdir computer-use-runtime
cd computer-use-runtime
uv venv --python 3.12
```

| Use case | Install command |
| --- | --- |
| Python SDK | `uv pip install --python .venv "../computer-use/dist/<wheel>"` |
| Bundled DeepAgent | `uv pip install --python .venv "../computer-use/dist/<wheel>[agent]"` |
| Connect an Agent through MCP | `uv pip install --python .venv "../computer-use/dist/<wheel>[mcp]"` |

To use an existing host Python environment, replace `--python .venv` with its path. A complete source wheel contains the driver. On Linux, keep the system desktop dependencies from step 1 and use a driver interpreter with the same system Python ABI as the build.

## 4. Check desktop permissions and verify the SDK

Open a terminal on the logged-in, unlocked target desktop, enter the runtime directory from step 3, and check permissions. Windows needs the interactive desktop session. On Linux, the terminal must inherit the display and D-Bus environment from the target graphical session. On macOS, run the second command to request missing permissions, approve them in System Settings under Privacy & Security, and check again. Grant access to the actual host if you later start MCP or the SDK from a different one.

```sh
uv run --no-project --python .venv computer-use-permissions
```

```sh
uv run --no-project --python .venv computer-use-permissions --request
```

Open an app with a visible window, such as Calculator. The first command lists running apps. Replace `APP_NAME` in the second with a name from the list. The output should contain controls or text from the actual window.

```sh
uv run --no-project --python .venv python -c "from computer_use import Computer; print([app.display_name for app in Computer().list_apps() if app.is_running])"
uv run --no-project --python .venv python -c "from computer_use import Computer; print(Computer().app('APP_NAME').read(view='tree').text)"
```

If the SDK reports that a native component is unavailable, check the reason recorded in the installed package with the command below. The expected result is `None`. For `native_build_failed`, check the build log from step 2 and rebuild. To update an existing environment, rerun its install command from step 3 with `--reinstall-package openjiuwen-computeruse`.

```sh
uv run --no-project --python .venv python -c "from computer_use.paths import installed_backend_unavailable_reason; print(installed_backend_unavailable_reason())"
```

## 5. Run or connect an Agent

### Run the bundled DeepAgent

Install with `[agent]` in step 3 and initialize the configuration.

```sh
uv run --no-project --python .venv computer-use-agent --init-config
```

Fill in `model`, `provider`, `api_base`, and `api_key` in the configuration file printed by the command. Use a model with tool calling and image input. Run the command below to verify a desktop task. Omit the task argument to start an interactive session.

```sh
uv run --no-project --python .venv computer-use-agent "Use the desktop tools to list the apps that are currently running."
```

### Connect your own Agent through MCP

Install with `[mcp]` in step 3. Add a stdio MCP service to your Agent. Use the absolute path to `computer-use-mcp` in the runtime environment as its command and leave the argument list empty. For clients that use `mcpServers`, use the following configuration.

```json
{
  "mcpServers": {
    "computer-use": {
      "command": "/absolute/path/to/computer-use-runtime/.venv/bin/computer-use-mcp",
      "args": [],
      "env": { "PYTHONSAFEPATH": "1" }
    }
  }
}
```

On Windows, the command path looks like `C:/absolute/path/to/computer-use-runtime/.venv/Scripts/computer-use-mcp.exe`. Run the MCP service on the computer whose desktop it operates.

On Linux, if the MCP client does not pass the desktop's display and D-Bus environment to the service, run the command below from a terminal in the target desktop session. Set the client's `command` to the launcher path it prints. Regenerate the launcher after logging out or changing desktop sessions.

```sh
uv run --no-project --python .venv computer-use-linux-session
```

Once connected, the Agent should discover the `computer_use` tool. Ask the Agent to list running apps with that tool and check the returned list.

### Call the SDK from your Python Agent

Install the wheel in your Agent's Python environment and use `from computer_use import Computer`. Use the example in step 4 to verify the connection.

## Local development

Use an editable install to work in the source directory. Add `--extra mcp` or `--extra agent` to `uv sync` as needed.

```sh
uv sync --python 3.12 --no-dev
uv run --python 3.12 --no-sync computer-use-build
uv run --python 3.12 --no-sync computer-use-permissions
```

- After editing Python source, restart the SDK host, MCP, or Agent session. After changing dependencies or entry points, rerun your original `uv sync` command.
- After editing Rust source or changing the Python version, add `--reinstall-package openjiuwen-computeruse` to your original `uv sync` command to rebuild the extension, then restart the session.
- After editing a platform driver, rerun `uv run --python 3.12 --no-sync computer-use-build` and restart the session.

See also [MCP integration](mcp.en.md), [DeepAgent integration](deepagent.en.md), and [release packaging](../PACKAGING.md).
