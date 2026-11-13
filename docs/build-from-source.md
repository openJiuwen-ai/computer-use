# 从源码构建 Computer Use

[返回 README](../README_CN.md) | [English](build-from-source.en.md)

按本指南在目标系统上构建包含平台驱动和 Rust 无障碍读取扩展的 wheel，再把它安装到要操作的电脑。安装后可直接调用 Python SDK、运行项目自带的 DeepAgent，或通过 MCP 接入自己的 Agent。

## 1. 安装工具链和系统依赖

所有平台都需要 Git、[uv](https://docs.astral.sh/uv/getting-started/installation/) 和 [rustup](https://rustup.rs/)。SDK 和 MCP 使用标准版 CPython 3.11–3.15，DeepAgent 使用 3.11–3.13。下文统一使用 3.12。选择其他小版本时，在构建和安装命令中使用同一版本。

| 目标系统 | 源码构建依赖 | 桌面运行条件 |
| --- | --- | --- |
| macOS 14+，Apple 芯片或 Intel | 带 Swift 6 或更高版本、macOS SDK、链接器和 `codesign` 的 [Xcode 或命令行工具](https://developer.apple.com/documentation/xcode/installing-the-command-line-tools)。 | 登录图形桌面。运行 SDK 或驱动的宿主需要辅助功能权限，截图还需要屏幕录制权限。 |
| Windows 10 1903+ 或 Windows 11，x64 | [Visual Studio Build Tools](https://visualstudio.microsoft.com/downloads/#build-tools-for-visual-studio) 的「使用 C++ 的桌面开发」工作负载（x64 MSVC 工具和 Windows SDK），以及 .NET Framework 自带的 64 位 `csc.exe`。 | 登录可交互的桌面会话，需要 [.NET Framework 4.8 或 4.8.1](https://learn.microsoft.com/en-us/dotnet/framework/get-started/system-requirements)。 |
| glibc Linux，x86_64 或 ARM64 | 系统 C 编译器和链接器。驱动使用发行版的 CPython 和 PyGObject，见下文。 | AT-SPI 2、图形会话的 D-Bus、X11 和 xkbcommon 库。截图和模拟键鼠输入需要 X11 会话。Wayland 下可使用应用公开的无障碍树和无障碍动作。 |

**macOS。** 用 `xcode-select -p` 和 `xcrun swift --version` 检查当前选中的开发工具。Python 和 Rust 使用相同架构，例如 Apple 芯片上都使用 ARM64。

**Windows。** 使用 x64 CPython 和 `x86_64-pc-windows-msvc` Rust 工具链。从 Visual Studio 的 x64 Developer PowerShell 执行后续构建命令。用以下命令检查链接器和 C# 编译器，第二条应输出 `True`。

```powershell
Get-Command link.exe
Test-Path "$env:WINDIR\Microsoft.NET\Framework64\v4.0.30319\csc.exe"
```

**Linux。** 以下命令适用于 Debian 13。其他发行版请安装对应的软件包，并运行下方的 AT-SPI 检查。

```sh
sudo apt update
sudo apt install build-essential python3-gi python3-venv at-spi2-core \
  gir1.2-atspi-2.0 libx11-6 libxkbcommon0
```

Linux 源码 wheel 会按系统 Python 的 ABI 打包驱动依赖。默认使用 `/usr/bin/python3`。请选择能加载 `gi.repository.Atspi` 的系统 CPython 3.11–3.14，以匹配当前锁定的 [Pillow 11.3.0 wheel](https://pypi.org/project/Pillow/11.3.0/#files)。SDK 的 Python 版本可以与驱动不同。若指定 `COMPUTER_USE_PYTHON`，构建和运行时都使用同一驱动解释器。

```sh
/usr/bin/python3 -c "import sys, gi; gi.require_version('Atspi', '2.0'); from gi.repository import Atspi; print(sys.version.split()[0], hasattr(Atspi.Accessible, 'clear_cache_single'))"
```

检查结果的最后一项应为 `True`。Debian 13 自带的 AT-SPI 2.56.2 已验证。Debian 12 自带的 AT-SPI 2.46.0 可完成构建和安装，但已复现应用枚举超时。若检查返回 `False`，建议使用提供新版 AT-SPI 的发行版。[Debian 13 的 AT-SPI 包](https://packages.debian.org/trixie/at-spi2-core) · [Debian 12 的 AT-SPI 包](https://packages.debian.org/bookworm/at-spi2-core)

完成平台准备后，安装项目固定的 Rust 1.85.1 和示例使用的 Python 3.12。确保 `rustup`、`cargo` 和 `rustc` 在当前终端的 `PATH` 中。

```sh
rustup toolchain install 1.85.1
rustup run 1.85.1 rustc --version
uv python install 3.12
```

## 2. 构建 wheel

在源码目录创建独立的构建环境，安装构建工具和打包驱动时会导入的 Python 依赖。首次构建需要联网下载依赖和固定版本的 Unicode 数据。

```sh
git clone https://gitcode.com/openJiuwen/computer-use.git
cd computer-use
uv venv --python 3.12 .build/venv
uv pip install --python .build/venv --group build -r pyproject.toml "pip>=23.1"
rustup run 1.85.1 uv run --no-project --python .build/venv python -m build --wheel --no-isolation
```

产物在 `dist/`。完整构建的文件名带有目标 CPython 和平台标签，例如 `openjiuwen_computeruse-0.0.1-cp312-cp312-macosx_14_0_arm64.whl`。每个 Python 小版本和平台架构分别构建。

源码打包允许原生组件构建失败时继续生成可安装的 wheel。若终端输出 `Computer Use driver unavailable (native_build_failed)` 或 `Rust accessibility engine unavailable (native_build_failed)`，分别表示驱动或 Rust 扩展构建失败。先查看警告前的构建输出。如果只有警告，重新检查第 1 步的工具链是否在当前终端可用。修复后重建，再按第 4 步实际读取一次窗口。

## 3. 在独立环境安装 wheel

以下命令从源码目录开始，在它旁边创建运行目录。把安装命令中的 `<wheel>` 换成第 2 步生成的实际文件名，包括 `.whl` 后缀。根据用途任选一条安装命令。

```sh
cd ..
mkdir computer-use-runtime
cd computer-use-runtime
uv venv --python 3.12
```

| 用途 | 安装命令 |
| --- | --- |
| Python SDK | `uv pip install --python .venv "../computer-use/dist/<wheel>"` |
| 项目自带的 DeepAgent | `uv pip install --python .venv "../computer-use/dist/<wheel>[agent]"` |
| 接入支持 MCP 的 Agent | `uv pip install --python .venv "../computer-use/dist/<wheel>[mcp]"` |

如果已有宿主 Python 环境，也可把 `--python .venv` 换成该环境的路径。从源码完整构建的 wheel 已包含驱动。Linux 上仍需第 1 步的系统桌面依赖，运行 wheel 的驱动解释器须与构建时的系统 Python ABI 一致。

## 4. 检查桌面权限并验证 SDK

在已登录、解锁的目标桌面中打开终端，进入第 3 步的运行目录，检查权限。Windows 需要交互桌面会话，Linux 需要继承同一图形会话的显示和 D-Bus 环境。macOS 如缺少权限，运行第二条命令请求授权，在「系统设置 → 隐私与安全」中批准后重新检查。若最终从另一个宿主启动 MCP 或 SDK，也要给实际运行的宿主授权。

```sh
uv run --no-project --python .venv computer-use-permissions
```

```sh
uv run --no-project --python .venv computer-use-permissions --request
```

打开一个有窗口的应用，例如计算器。第一条命令列出正在运行的应用，第二条把 `APP_NAME` 换成列表中的应用名。输出应包含这个窗口的实际控件或文字。

```sh
uv run --no-project --python .venv python -c "from computer_use import Computer; print([app.display_name for app in Computer().list_apps() if app.is_running])"
uv run --no-project --python .venv python -c "from computer_use import Computer; print(Computer().app('APP_NAME').read(view='tree').text)"
```

如果提示原生组件不可用，可用下面的命令查询已安装包中记录的原因。正常输出为 `None`。若输出 `native_build_failed`，查看第 2 步的构建日志并重建。对已经安装过的环境，重新执行第 3 步的安装命令并加上 `--reinstall-package openjiuwen-computeruse`。

```sh
uv run --no-project --python .venv python -c "from computer_use.paths import installed_backend_unavailable_reason; print(installed_backend_unavailable_reason())"
```

## 5. 运行或接入 Agent

### 运行项目自带的 DeepAgent

使用第 3 步的 `[agent]` 安装方式，初始化配置。

```sh
uv run --no-project --python .venv computer-use-agent --init-config
```

在命令输出的配置文件中填写 `model`、`provider`、`api_base` 和 `api_key`。使用支持工具调用和图片输入的模型。运行下列命令验证一次桌面任务，省略任务参数可进入交互模式。

```sh
uv run --no-project --python .venv computer-use-agent "使用电脑操作工具，列出当前运行的应用"
```

### 通过 MCP 接入自己的 Agent

使用第 3 步的 `[mcp]` 安装方式。在 Agent 中添加 stdio MCP 服务，命令填写运行环境中 `computer-use-mcp` 的绝对路径，参数留空。支持 `mcpServers` 的客户端可以使用以下配置。

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

Windows 对应的命令路径形如 `C:/absolute/path/to/computer-use-runtime/.venv/Scripts/computer-use-mcp.exe`。MCP 服务运行在被操作的电脑上。

Linux 上，如果 MCP 客户端没有向服务传递图形会话的显示和 D-Bus 环境，请在目标桌面的终端中运行以下命令，并将客户端的 `command` 改为它输出的启动脚本路径。注销或更换桌面会话后重新生成脚本。

```sh
uv run --no-project --python .venv computer-use-linux-session
```

连接后应能发现 `computer_use` 工具。请 Agent 使用该工具列出当前运行的应用，并核对返回的应用列表。

### 在 Python Agent 中直接调用 SDK

将 wheel 安装到 Agent 的 Python 环境，使用 `from computer_use import Computer`。可复用第 4 步的示例验证连接。

## 本地开发

需要在源码目录迭代时，可使用 editable 安装。按需要在 `uv sync` 后加 `--extra mcp` 或 `--extra agent`。

```sh
uv sync --python 3.12 --no-dev
uv run --python 3.12 --no-sync computer-use-build
uv run --python 3.12 --no-sync computer-use-permissions
```

- 修改 Python 源码后，重启 SDK 宿主、MCP 或 Agent 会话。修改依赖或入口后，重新执行原 `uv sync` 命令。
- 修改 Rust 源码或更换 Python 版本后，给原 `uv sync` 命令加上 `--reinstall-package openjiuwen-computeruse`，重新编译扩展，再重启会话。
- 修改平台驱动后，重新执行 `uv run --python 3.12 --no-sync computer-use-build`，然后重启会话。

更多用法见 [MCP 接入](mcp.md)、[DeepAgent 接入](deepagent.md)和[发布组包](../PACKAGING.md)。
