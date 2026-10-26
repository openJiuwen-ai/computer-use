import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

from computer_use._json import loads as jsonLoads
from computer_use.paths import nativeDriver, repositoryRoot
from computer_use.protocol.session import nativeProtocolVersion


def run(command, args, cwd=None, capture=False):
    result = subprocess.run([str(command), *map(str, args)], cwd=cwd, text=True, capture_output=capture)
    if result.returncode:
        raise RuntimeError(
            f"{Path(command).name} failed ({result.returncode})." + (f"\n{result.stderr}" if capture else "")
        )
    return result.stdout.strip() if capture else result


def buildMacOS(root=None):
    if sys.platform != "darwin":
        raise RuntimeError("Only the macOS backend is implemented.")
    from .pointer import generatePointerAssets

    root = Path(root) if root else repositoryRoot()
    generatePointerAssets(root=root)
    native = root / "os_adapter/macos"
    run("swift", ["build", "-c", "debug"], native)
    binary = Path(run("swift", ["build", "-c", "debug", "--show-bin-path"], native, True))
    bundle = native / ".build/ComputerUseDriver.app"
    executable = bundle / "Contents/MacOS/computer-use-driver"
    executable.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(binary / "computer-use-driver", executable)
    (bundle / "Contents/Info.plist").write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>CFBundleIdentifier</key><string>local.computer-use.driver</string>
<key>CFBundleName</key><string>Computer Use Driver</string>
<key>CFBundleExecutable</key><string>computer-use-driver</string>
<key>CFBundlePackageType</key><string>APPL</string>
<key>CFBundleVersion</key><string>1</string>
<key>CFBundleShortVersionString</key><string>0.0.1</string>
<key>LSMinimumSystemVersion</key><string>14.0</string>
<key>LSUIElement</key><true/>
</dict></plist>
""",
        encoding="utf-8",
    )
    run("codesign", ["--force", "--sign", "-", "--identifier", "local.computer-use.driver", bundle], native)
    print(executable)
    return executable


def _protocol_version(version):
    if type(version) is not int or version != nativeProtocolVersion:
        raise ValueError("This backend requires protocol 6; upgrade the client.")
    return version


def buildLinux(root=None, *, protocol_version=nativeProtocolVersion):
    _protocol_version(protocol_version)
    if sys.platform != "linux":
        raise RuntimeError("Build the Linux backend on Linux (PyGObject is a system dependency).")
    native = (Path(root) if root else repositoryRoot()) / "os_adapter/linux"
    python = os.environ.get("COMPUTER_USE_PYTHON") or "/usr/bin/python3"
    check = (
        f"from computer_use_linux.runtime.resources import check_installation; check_installation({protocol_version}"
    )
    try:
        run(
            python,
            [
                "-c",
                (
                    "import sys; assert sys.version_info >= (3,10), 'Python >= 3.10 "
                    "required'; "
                    "sys.path.insert(0, 'src'); "
                )
                + check
                + ")",
            ],
            native,
        )
    except Exception as error:
        raise RuntimeError(
            (
                f"{error}"
                f"\nInstall protocol 6 system dependencies first. Debian/Ubuntu:\n "
                f" sudo apt install python3-gi python3-venv gir1.2-atspi-2.0 "
                f"libx11-6 libxkbcommon0\nUse the distribution's Python."
            )
        ) from error
    venv = native / ".venv"
    executable = venv / "bin/python"
    if not executable.exists():
        run(python, ["-m", "venv", "--system-site-packages", venv], native)
    requirements = (native / "requirements.lock").read_text(encoding="utf-8")
    stamp = venv / "computer-use-requirements.txt"
    if not stamp.exists() or stamp.read_text(encoding="utf-8") != requirements:
        run(executable, ["-m", "pip", "install", "-r", "requirements.lock"], native)
        stamp.write_text(requirements, encoding="utf-8")
    run(executable, ["-m", "pip", "install", "--no-deps", "--editable", "."], native)
    run(
        executable,
        [
            "-c",
            check
            + (
                f", installed=True); print('Linux protocol "
                f"{protocol_version}"
                f" dependencies installed; desktop verification: "
                f"computer-use-permissions --protocol-version "
                f"{protocol_version}"
                f".')"
            ),
        ],
        native,
    )
    return executable


def buildNative(root=None, *, protocol_version=nativeProtocolVersion):
    _protocol_version(protocol_version)
    if sys.platform == "darwin":
        return buildMacOS(root)
    if sys.platform == "linux":
        return buildLinux(root, protocol_version=protocol_version)
    if sys.platform == "win32":
        return buildWindows(root)
    raise RuntimeError(f"platform {sys.platform} is not implemented")


def buildWindows(root=None):
    "Use the system Framework compiler; no Node, NuGet or shell command."
    if sys.platform != "win32":
        raise RuntimeError("Build the Windows backend on Windows x64 with .NET Framework 4.8.")
    native = (Path(root) if root else repositoryRoot()) / "os_adapter/windows"
    from .pointer import generatePointerAssets

    framework = Path(os.environ.get("WINDIR", "C:\\Windows")) / "Microsoft.NET/Framework64/v4.0.30319"
    compiler = framework / "csc.exe"
    if not compiler.is_file():
        raise RuntimeError("Windows x64 with .NET Framework 4.8 and its C# compiler is required.")
    generatePointerAssets(root=native.parents[1])
    output = native / ".build/computer-use-driver.exe"
    output.parent.mkdir(parents=True, exist_ok=True)
    # Import the installed Windows UIA COM type library at build time. Live
    # collection/input uses IUIAutomation; framework assemblies below supply
    # public property identifiers and enums, not managed provider traversal.
    importer = output.with_name("import-uia.exe")
    interop = output.with_name("Interop.NativeUIA.dll")
    run(
        compiler, ["/nologo", "/target:exe", "/platform:x64", f"/out:{importer}", native / "Build/ImportUia.cs"], native
    )
    run(importer, [output.parent], native)
    references = (
        "System.dll",
        "System.Core.dll",
        "System.Web.Extensions.dll",
        "System.Drawing.dll",
        "System.Windows.Forms.dll",
        "WPF/WindowsBase.dll",
        "WPF/UIAutomationClient.dll",
        "WPF/UIAutomationTypes.dll",
    )
    sources = sorted((native / "Sources").rglob("*.cs"))
    if not sources:
        raise RuntimeError("Windows backend sources are missing; reinstall the toolkit.")
    run(
        compiler,
        [
            "/nologo",
            "/target:exe",
            "/platform:x64",
            "/optimize+",
            "/warnaserror+",
            f"/out:{output}",
            *(f"/reference:{framework / name}" for name in references),
            f"/reference:{interop}",
            *sources,
        ],
        native,
    )
    # Compile the WinRT pixel helper against metadata shipped with Windows.
    # UIA objects and the public driver do not depend on WinRT projections.
    metadata = framework.parents[2] / "System32/WinMetadata"
    capture = output.with_name("computer-use-capture.exe")
    capture_refs = (
        "System.dll",
        "System.Core.dll",
        "System.Web.Extensions.dll",
        "System.Drawing.dll",
        "System.Runtime.WindowsRuntime.dll",
        "System.Runtime.dll",
    )
    winmd = (
        "Windows.Foundation.winmd",
        "Windows.Graphics.winmd",
        "Windows.Storage.winmd",
        "Windows.Media.winmd",
        "Windows.Globalization.winmd",
    )
    required = [*(framework / name for name in capture_refs), *(metadata / name for name in winmd)]
    if any(not path.is_file() for path in required):
        raise RuntimeError(("Windows 10 1903+ WinRT metadata and .NET Framework 4.8 are required for window capture."))
    run(
        compiler,
        [
            "/nologo",
            "/target:exe",
            "/platform:x64",
            "/optimize+",
            "/warnaserror+",
            f"/out:{capture}",
            *(f"/reference:{path}" for path in required),
            native / "Sources/Contracts/Wire.cs",
            native / "Sources/Capture/NativeCaptureGeometry.cs",
            *sorted((native / "Workers/Capture").glob("*.cs")),
        ],
        native,
    )
    print(output)
    return output


def readPermissions(request=False, root=None, timeout=None, *, protocol_version=nativeProtocolVersion):
    _protocol_version(protocol_version)
    native = nativeDriver(root=root)
    version_args = ["--protocol-version", str(protocol_version)] if sys.platform == "linux" else []
    try:
        result = subprocess.run(
            [
                native["command"],
                *native["args"],
                "--request-permissions" if request else "--check-permissions",
                *version_args,
            ],
            stdout=subprocess.PIPE,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError:
        raise RuntimeError("本机后端尚未构建，请先运行 computer-use-setup 或 computer-use-build。") from None
    if result.returncode:
        raise RuntimeError(f"权限检查未完成（{result.returncode}）。")
    status = jsonLoads(result.stdout)
    if not isinstance(status, dict):
        raise ValueError("Invalid permission response")
    if sys.platform == "linux" and protocol_version == 6 and status.get("protocolVersion") != 6:
        raise ValueError(("Permission response did not check protocol 6 resources; rebuild the Linux driver."))
    return status


def permissionState(status, platform=None):
    platform = platform or sys.platform
    missing = [name for name in ("accessibility", "screenCapture", "input") if status.get(name) is not True]
    ready = status["ready"] is True if platform == "linux" and "ready" in status else not missing
    return ready, missing


def permissions(argv=None):
    parser = argparse.ArgumentParser(
        description="检查目标桌面权限；--request 在 macOS 发起系统授权提示，Linux 仅报告状态。请使用与 MCP 相同的终端或 SSH 会话。"
    )
    parser.add_argument("--request", action="store_true")
    parser.add_argument(
        "--protocol-version",
        type=int,
        choices=(6,),
        default=nativeProtocolVersion,
        help=(
            "Inspect native protocol 6 resources by default; Linux ready means "
            "observation resources, not all input "
            "methods."
        ),
    )
    args = parser.parse_args(argv)
    status = readPermissions(args.request, protocol_version=args.protocol_version)
    print(json.dumps(status, ensure_ascii=False, separators=(",", ":")))
    ready, missing = permissionState(status)
    if not ready:
        if sys.platform == "linux":
            raise RuntimeError(
                (
                    f"Linux 桌面未就绪："
                    f"{', '.join(missing)}"
                    f"。"
                    f"{' '.join(status.get('details', []))}"
                    f"\n请在已解锁的目标桌面会话中运行，使用同一会话的 DBUS_SESSION_BUS_ADDRESS "
                    f"及显示环境。--request 不会更改 Linux 权限或桌面模式。"
                )
            )
        message = (
            "已请求缺失权限；请在系统提示或「系统设置 → 隐私与安全」中授权，必要时重启启动终端，再运行 computer-use-permissions。"
            if args.request
            else "可运行 computer-use-permissions --request 发起系统授权提示。"
        )
        print(
            (
                f"未就绪："
                f"{', '.join(missing)}"
                f"。"
                f"{message}"
                f"\n权限归属以系统显示的启动程序为准（驱动、终端或 SSH 服务，例如 sshd-keygen-wrapper）；请从与 MCP "
                f"相同的终端或 SSH 连接复查。请求命令不会代替你批准授权。"
            ),
            file=sys.stderr,
        )
        return 1
    return 0


def linuxSessionLauncher(*, directory=None, env=None):
    if sys.platform != "linux":
        raise RuntimeError("Run this command in the target Linux desktop terminal.")
    env = os.environ if env is None else env
    wayland = bool(env.get("WAYLAND_DISPLAY")) or env.get("XDG_SESSION_TYPE") == "wayland"
    if not wayland and not env.get("DISPLAY"):
        raise RuntimeError("Run in the target graphical session (DISPLAY or WAYLAND_DISPLAY is required).")
    if not env.get("DBUS_SESSION_BUS_ADDRESS"):
        raise RuntimeError(("Run in the graphical user's desktop terminal; DBUS_SESSION_BUS_ADDRESS is missing."))
    mode = env.get("COMPUTER_USE_DESKTOP", "exclusive")
    if mode not in ("shared", "exclusive"):
        raise ValueError("COMPUTER_USE_DESKTOP must be shared or exclusive.")
    directory = Path(directory) if directory else Path.home() / ".local/bin"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Quote values as shell data, including embedded apostrophes and line breaks.
    values = {
        key: env[key]
        for key in (
            "DISPLAY",
            "WAYLAND_DISPLAY",
            "XAUTHORITY",
            "DBUS_SESSION_BUS_ADDRESS",
            "XDG_RUNTIME_DIR",
            "XDG_SESSION_ID",
        )
        if env.get(key)
    }
    values.update(
        XDG_SESSION_TYPE="wayland" if wayland else "x11",
        COMPUTER_USE_DESKTOP=mode,
        COMPUTER_USE_FOREGROUND_ASSIST="1" if env.get("COMPUTER_USE_FOREGROUND_ASSIST") == "1" else "0",
    )
    command = " ".join(f"{key}={shlex.quote(value)}" for key, value in values.items())
    output = directory / "computer-use-mcp"
    output.write_text(
        (
            "#!/bin/sh\nunset DISPLAY WAYLAND_DISPLAY XAUTHORITY AT_SPI_BUS_ADDRESS "
            "GDK_BACKEND QT_QPA_PLATFORM\nexec env "
        )
        + command
        + " "
        + shlex.quote(sys.executable)
        + " -m computer_use_mcp\n",
        encoding="utf-8",
    )
    output.chmod(0o700)
    print(
        (
            f"Saved "
            f"{output}"
            f" ("
            f"{mode}"
            f"; "
            f"{'Wayland native accessibility; capture/input limited' if wayland else 'X11'}"
            f"). Regenerate after logging out or changing the desktop session."
        )
    )
    return output


def _cli(action):
    try:
        return action()
    except Exception as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1) from None


def buildCli(argv=None):
    parser = argparse.ArgumentParser(
        description="Install/build native driver dependencies; this does not verify desktop access."
    )
    parser.add_argument(
        "--protocol-version",
        type=int,
        choices=(6,),
        default=nativeProtocolVersion,
        help=("Prepare native protocol 6 resources by default; This does not change the SDK protocol."),
    )
    args = parser.parse_args(argv)
    _cli(lambda: buildNative(protocol_version=args.protocol_version))


def permissionsCli():
    raise SystemExit(_cli(permissions))


def linuxLauncherCli():
    _cli(linuxSessionLauncher)
