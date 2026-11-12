# 从源码构建 Computer Use

[返回 README](../README_CN.md) | [English](build-from-source.en.md)

源码安装分两步：`uv sync` 安装 Python SDK，并编译公共 Rust 无障碍读取扩展；`computer-use-build` 准备当前系统的桌面驱动。请在要操作桌面的目标电脑上安装。按[打包指南](../PACKAGING.md)组装的预构建 wheel 已包含扩展和驱动；Linux 上仍需运行 `computer-use-build` 准备驱动环境。

## 1. 安装工具链和系统依赖

所有平台都需要 Git、[uv](https://docs.astral.sh/uv/getting-started/installation/)、与系统架构匹配的常规 CPython 3.11–3.15，以及 PATH 上的 Rust 1.85.1（`cargo` 和 `rustc`）。本指南使用 Python 3.12，也适用于需要 Python 3.11–3.13 的 DeepAgent。

安装 [rustup](https://rustup.rs/) 和下表中的系统工具链后，可运行：

```sh
rustup toolchain install 1.85.1
rustup run 1.85.1 rustc --version
uv python install 3.12
```

| 目标系统 | 源码构建依赖 | 桌面运行条件 |
| --- | --- | --- |
| macOS 14+，Apple 芯片或 Intel | 带 Swift 6、macOS SDK、链接器和 `codesign` 的 Apple 开发工具。 | 登录图形桌面，为实际运行 SDK 或驱动的宿主授予辅助功能权限；截图还需屏幕录制权限。 |
| Windows x64，Windows 10 1903+ | [Visual Studio Build Tools](https://visualstudio.microsoft.com/downloads/#build-tools-for-visual-studio) 的「使用 C++ 的桌面开发」工作负载，含 x64 MSVC 工具和 Windows SDK，用于链接 Rust 扩展；.NET Framework 4.8 及其 64 位 `csc.exe` 用于编译 UIA 驱动。 | 登录可交互的桌面会话。窗口截图需要系统自带的 WinRT 元数据。 |
| glibc Linux，x86_64 或 ARM64 | 系统 C 编译器和链接器；发行版提供的 Python 3.11+、venv 和 PyGObject。 | AT-SPI 2、图形会话的 D-Bus、X11 和 xkbcommon 库。完整截图与鼠标输入使用 X11；Wayland 下读取和输入能力取决于会话。 |

**macOS。** 用 `xcode-select -p` 和 `xcrun swift --version` 检查当前选中的开发工具和 Swift 版本。Python 和 Rust 应使用同一架构，例如 Apple 芯片上都使用 ARM64。

**Windows。** 使用 x64 CPython 和 `x86_64-pc-windows-msvc` Rust 工具链。在 x64 Developer PowerShell 中可用 `Get-Command link.exe` 检查 MSVC 链接器。驱动还会查找 `%WINDIR%\Microsoft.NET\Framework64\v4.0.30319\csc.exe`；安装现代 .NET SDK 不会提供此编译器。

**Linux。** Debian 或 Ubuntu 可安装下列依赖。其他发行版使用对应的软件包。

```sh
sudo apt install build-essential python3-gi python3-venv at-spi2-core \
  gir1.2-atspi-2.0 libx11-6 libxkbcommon0
```

SDK 可以使用 uv 安装的 Python。Linux 驱动默认使用 `/usr/bin/python3`，以便加载系统的 PyGObject。若需指定其他驱动解释器，通过 `COMPUTER_USE_PYTHON` 指向能加载 `gi.repository.Atspi` 的系统 Python 3.11+。从目标图形会话执行权限检查，使驱动拿到正确的显示和 D-Bus 环境。

## 2. 安装 SDK 并准备驱动

```sh
git clone https://gitcode.com/openJiuwen/computer-use.git
cd computer-use
uv sync --python 3.12 --no-dev
uv run --no-sync computer-use-build
uv run --no-sync computer-use-permissions
```

按接入方式在 `uv sync` 命令后加 `--extra mcp` 或 `--extra agent`。默认安装到仓库的 `.venv`。Windows 与 macOS 会编译桌面驱动；Linux 会在 `os_adapter/linux/.venv` 准备独立的驱动环境。macOS 可用 `uv run --no-sync computer-use-permissions --request` 发起系统授权提示，并在系统设置里批准。

## 3. 验证与更新

先列出正在运行的应用，再将 `APP_NAME` 换成列表里有窗口的应用名，读取无障碍树：

```sh
uv run --no-sync python -c "from computer_use import Computer; print(Computer().list_apps())"
uv run --no-sync python -c "from computer_use import Computer; print(Computer().app('APP_NAME').read(view='tree').text)"
```

- 修改 Python 源码后，重启 SDK 宿主、MCP 或 Agent 会话。修改依赖或入口后，重新执行原 `uv sync` 命令。
- 修改 Rust 源码或更换 Python 版本后，给原 `uv sync` 命令加上 `--reinstall-package openjiuwen-computeruse`，重新编译扩展，再重启会话。
- 修改平台驱动后，重新执行 `uv run --no-sync computer-use-build`，然后重启会话。

下一步：[MCP 接入](mcp.md) · [DeepAgent 接入](deepagent.md) · [构建和组装 wheel](../PACKAGING.md)。
