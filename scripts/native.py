# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import argparse
import json
import logging
import os
import shlex
import shutil
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from computer_use.json import loads as jsonLoads
from computer_use.paths import LINUX_RUNTIME, linux_python, native_driver, repository_root
from computer_use.protocol.session import NATIVE_PROTOCOL_VERSION

from .console import report

_LOG = logging.getLogger(__name__)


def run(command, args, cwd=None, capture=False):
    result = subprocess.run([str(command), *map(str, args)], cwd=cwd, text=True, capture_output=capture)
    if result.returncode:
        raise RuntimeError(
            f"{Path(command).name} failed ({result.returncode})." + (f"\n{result.stderr}" if capture else "")
        )
    return result.stdout.strip() if capture else result


def build_mac_os(root=None, *, configuration="release"):
    if sys.platform != "darwin":
        raise RuntimeError("Only the macOS backend is implemented.")
    from .pointer import generate_pointer_assets

    root = Path(root) if root else repository_root()
    generate_pointer_assets(root=root)
    native = root / "os_adapter/macos"
    run("swift", ["build", "-c", configuration], native)
    binary = Path(run("swift", ["build", "-c", configuration, "--show-bin-path"], native, True))
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
    report(executable)
    return executable


def _protocol_version(version):
    # Exact types preserve scalar validation and identity contracts.
    # pylint: disable-next=unidiomatic-typecheck
    if type(version) is not int or version != NATIVE_PROTOCOL_VERSION:
        raise ValueError("This backend requires protocol 6; upgrade the client.")
    return version


def stage_linux_runtime(root, destination):
    """Package the driver and its Python dependencies for the host's desktop Python."""
    native = root / "os_adapter/linux"
    desktop_python = linux_python()
    probe = run(
        desktop_python,
        [
            "-I", "-S", "-c",
            (
                "import json, sys, sysconfig\n"
                "if sys.implementation.name != 'cpython' or sys.version_info < (3, 11):\n"
                "    sys.exit('Linux driver requires system CPython >= 3.11')\n"
                "sys.stdout.write(json.dumps([sys.version_info[:2], "
                "bool(sysconfig.get_config_var('Py_GIL_DISABLED')), sysconfig.get_config_var('SOABI')]))\n"
            ),
        ],
        native,
        True,
    )
    try:
        (major, minor), free_threaded, soabi = json.loads(probe)
        version_is_valid = isinstance(major, int) and isinstance(minor, int)
        abi_is_valid = isinstance(free_threaded, bool) and (soabi is None or isinstance(soabi, str))
        if not version_is_valid or not abi_is_valid:
            raise ValueError("Invalid desktop Python version or ABI")
    except (TypeError, ValueError) as error:
        raise RuntimeError(f"Could not read the Linux desktop Python version and ABI from {desktop_python}.") from error
    wheel_abi = f"cp{major}{minor}{'t' if free_threaded else ''}"
    run(
        sys.executable,
        [
            "-m", "pip", "install", "--target", destination, "--no-deps", "--no-compile",
            "--only-binary=:all:", "--python-version", f"{major}.{minor}", "--implementation", "cp",
            "--abi", wheel_abi, "-r", "requirements.lock",
        ],
        native,
    )
    shutil.copytree(
        native / "src/computer_use_linux", destination / "computer_use_linux",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    (destination / "__main__.py").write_text(
        "import sys\nimport sysconfig\n\n"
        "if (\n"
        f"    sys.version_info[:2] != {(major, minor)!r}\n"
        f"    or bool(sysconfig.get_config_var('Py_GIL_DISABLED')) != {free_threaded!r}\n"
        f"    or sysconfig.get_config_var('SOABI') != {soabi!r}\n"
        "):\n"
        "    raise SystemExit('Desktop Python changed. Rebuild the Computer Use wheel without the pip cache.')\n\n"
        "from computer_use_linux.__main__ import main\n\n"
        "raise SystemExit(main())\n",
        encoding="utf-8",
    )


def bundle_wheel_backend(root: Path, output: Path, platform_tag: str) -> None:
    """Build the host's driver and record the exact installed payload."""
    from .backend_artifacts import FORMAT, TARGETS, canonical, host_target, sha256

    target = host_target()
    if TARGETS[target][2] != platform_tag:
        raise ValueError(f"Source backend does not match wheel platform {platform_tag}.")
    if sys.platform == "darwin":
        bundle = build_mac_os(root, configuration="release").parents[2]
        destination = output / bundle.relative_to(root)
        shutil.copytree(bundle, destination)
        paths = [path for path in destination.rglob("*") if path.is_file()]
    elif sys.platform == "win32":
        driver = build_windows(root)
        destination = output / driver.parent.relative_to(root)
        destination.mkdir(parents=True, exist_ok=True)
        paths = [
            Path(shutil.copy2(driver.parent / name, destination / name))
            for name in (driver.name, "computer-use-capture.exe", "Interop.NativeUIA.dll")
        ]
    else:
        destination = output / LINUX_RUNTIME
        stage_linux_runtime(root, destination)
        paths = [path for path in destination.rglob("*") if path.is_file()]
    files = {
        path.relative_to(output).as_posix(): {
            "sha256": sha256(path.read_bytes()), "mode": 0o755 if path.stat().st_mode & 0o111 else 0o644,
        }
        for path in sorted(paths)
    }
    (output / "backend.json").write_bytes(canonical({
        "format": FORMAT, "target": target, "protocol": NATIVE_PROTOCOL_VERSION, "files": files,
    }))


def build_linux(root=None, *, protocol_version=NATIVE_PROTOCOL_VERSION):
    _protocol_version(protocol_version)
    if sys.platform != "linux":
        raise RuntimeError("Build the Linux backend on Linux (PyGObject is a system dependency).")
    root = Path(root) if root else repository_root()
    native = root / "os_adapter/linux"
    bundled = (root / "backend.json").is_file()
    wheel = None
    if bundled:
        wheels = [
            path for path in (native / ".build").glob("*.whl")
            if path.name.startswith(("openjiuwen_computeruse_linux-", "computer_use_linux-"))
        ]
        if len(wheels) != 1:
            raise RuntimeError("Bundled Linux backend wheel is missing or ambiguous. Reinstall openjiuwen-computeruse.")
        wheel = wheels[0].resolve()
    import_path = str(wheel) if bundled else "src"
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
                    "import sys; assert sys.version_info >= (3,11), 'Python >= 3.11 "
                    "required'; "
                    f"sys.path.insert(0, {import_path!r}); "
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
        binary = ["--only-binary=:all:"] if bundled else []
        run(executable, ["-m", "pip", "install", *binary, "-r", "requirements.lock"], native)
        stamp.write_text(requirements, encoding="utf-8")
    package = [str(wheel)] if bundled else ["--editable", "."]
    run(executable, ["-m", "pip", "install", "--no-deps", *package], native)
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


def build_native(root=None, *, protocol_version=NATIVE_PROTOCOL_VERSION):
    _protocol_version(protocol_version)
    executable = None
    if root is None and sys.platform == "win32":
        # Resolve the installed driver's short-path fallback before materializing a runtime copy.
        executable = Path(native_driver()["command"])
        root = executable.parents[3]
    else:
        root = Path(root) if root else repository_root()
    backend = root / "backend.json"
    if backend.is_file():
        from .backend_artifacts import TARGETS, host_target

        metadata = jsonLoads(backend.read_text(encoding="utf-8"))
        if "format" in metadata or "files" in metadata:
            matches = metadata["target"] == host_target() and metadata["protocol"] == protocol_version
        else:
            # Source wheels built before the unified manifest recorded only a platform and protocol.
            matches = metadata.get("protocol", protocol_version) == protocol_version and (
                "platform" not in metadata or metadata["platform"] == TARGETS[host_target()][2]
            )
        if not matches:
            raise RuntimeError("Install a backend wheel matching this platform and native protocol.")
        if sys.platform != "linux":
            executable = executable or Path(native_driver(root=root)["command"])
            report(executable)
            return executable
    if sys.platform == "darwin":
        return build_mac_os(root)
    if sys.platform == "linux":
        if (root / LINUX_RUNTIME / "__main__.py").is_file():
            return Path(linux_python())
        return build_linux(root, protocol_version=protocol_version)
    if sys.platform == "win32":
        return build_windows(root)
    raise RuntimeError(f"platform {sys.platform} is not implemented")


def build_windows(root=None):
    "Use the system Framework compiler; no Node, NuGet or shell command."
    if sys.platform != "win32":
        raise RuntimeError("Build the Windows backend on Windows x64 with .NET Framework 4.8.")
    native = (Path(root) if root else repository_root()) / "os_adapter/windows"
    from .pointer import generate_pointer_assets

    framework = Path(os.environ.get("WINDIR", "C:\\Windows")) / "Microsoft.NET/Framework64/v4.0.30319"
    compiler = framework / "csc.exe"
    if not compiler.is_file():
        raise RuntimeError("Windows x64 with .NET Framework 4.8 and its C# compiler is required.")
    generate_pointer_assets(root=native.parents[1])
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
    arguments = [
        "/nologo",
        "/target:exe",
        "/platform:x64",
        "/optimize+",
        "/warnaserror+",
        f"/out:{output}",
        *(f"/reference:{framework / name}" for name in references),
        f"/reference:{interop}",
        *sources,
    ]
    # Deep checkouts can exceed the Win32 command-line limit once every source
    # path is expanded. The BOM lets Framework csc read Unicode paths reliably.
    with TemporaryDirectory(prefix="computer-use-csc-") as folder:
        response = Path(folder) / "driver.rsp"
        response.write_text("".join(f'"{argument}"\n' for argument in arguments), encoding="utf-8-sig")
        run(compiler, [f"@{response}"], native)
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
    report(output)
    return output


def read_permissions(request=False, root=None, timeout=None, *, protocol_version=NATIVE_PROTOCOL_VERSION):
    _protocol_version(protocol_version)
    native = native_driver(root=root)
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
        raise RuntimeError("本机后端尚未构建，请先运行 computer-use-build，再运行 computer-use-permissions。") from None
    if result.returncode:
        raise RuntimeError(f"权限检查未完成（{result.returncode}）。")
    status = jsonLoads(result.stdout)
    if not isinstance(status, dict):
        raise ValueError("Invalid permission response")
    if sys.platform == "linux" and protocol_version == 6 and status.get("protocolVersion") != 6:
        raise ValueError(("Permission response did not check protocol 6 resources; rebuild the Linux driver."))
    return status


def permission_state(status, platform=None):
    platform = platform or sys.platform
    if platform == "win32" and "ready" in status:
        return (True, []) if status["ready"] is True else (False, ["interactiveDesktop"])
    missing = [name for name in ("accessibility", "screenCapture", "input") if status.get(name) is not True]
    ready = status["ready"] is True if platform == "linux" and "ready" in status else not missing
    return ready, missing


def permissions(argv=None):
    parser = argparse.ArgumentParser(
        description="检查目标桌面权限；--request 在 macOS 发起系统授权提示，Windows 和 Linux 报告状态。"
        "请使用与 SDK 相同的终端或宿主连接。"
    )
    parser.add_argument("--request", action="store_true")
    parser.add_argument(
        "--protocol-version",
        type=int,
        choices=(6,),
        default=NATIVE_PROTOCOL_VERSION,
        help=(
            "Inspect native protocol 6 resources by default; Linux ready means "
            "observation resources, not all input "
            "methods."
        ),
    )
    args = parser.parse_args(argv)
    status = read_permissions(args.request, protocol_version=args.protocol_version)
    report(json.dumps(status, ensure_ascii=False, separators=(",", ":")))
    ready, missing = permission_state(status)
    if not ready:
        if sys.platform == "win32":
            raise RuntimeError(
                f"Windows 桌面未就绪（{status.get('desktopState', 'unknown')}）。"
                "请在目标用户已解锁的桌面启动宿主，并处理正在显示的 Windows 安全提示。"
            )
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
        _LOG.warning(
            (
                f"未就绪："
                f"{', '.join(missing)}"
                f"。"
                f"{message}"
                f"\n权限归属以系统显示的启动程序为准（驱动、终端或 SSH 服务，例如 sshd-keygen-wrapper）；请从与 SDK "
                f"相同的终端或 SSH 连接复查。请求命令不会代替你批准授权。"
            )
        )
        return 1
    return 0


def linux_session_launcher(*, directory=None, env=None):
    if sys.platform != "linux":
        raise RuntimeError("Run this command in the target Linux desktop terminal.")
    env = os.environ if env is None else env
    wayland = bool(env.get("WAYLAND_DISPLAY")) or env.get("XDG_SESSION_TYPE") == "wayland"
    if not wayland and not env.get("DISPLAY"):
        raise RuntimeError("Run in the target graphical session (DISPLAY or WAYLAND_DISPLAY is required).")
    if not env.get("DBUS_SESSION_BUS_ADDRESS"):
        raise RuntimeError(("Run in the graphical user's desktop terminal; DBUS_SESSION_BUS_ADDRESS is missing."))
    directory = Path(directory) if directory else Path.home() / ".local/bin"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)

    def _iter_key(_source_items: Iterator[Any]) -> Iterator[Any]:
        for key in _source_items:
            if env.get(key):
                yield (key, env[key])

    # Quote values as shell data, including embedded apostrophes and line breaks.
    values = dict(
        _iter_key(
            iter(
                (
                    "DISPLAY",
                    "WAYLAND_DISPLAY",
                    "XAUTHORITY",
                    "DBUS_SESSION_BUS_ADDRESS",
                    "XDG_RUNTIME_DIR",
                    "XDG_SESSION_ID",
                )
            )
        )
    )
    values.update(
        XDG_SESSION_TYPE="wayland" if wayland else "x11",
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
        + ' -m computer_use_mcp "$@"\n',
        encoding="utf-8",
    )
    output.chmod(0o700)
    report(
        (
            f"Saved "
            f"{output}"
            f" ("
            f"{'Wayland native accessibility; capture/input limited' if wayland else 'X11'}"
            f"). Regenerate after logging out or changing the desktop session."
        )
    )
    return output


def _cli(action):
    try:
        result = action()
    except Exception as error:
        _LOG.error("%s", error)
        return 1
    return result if isinstance(result, int) else 0


def build_cli(argv=None):
    parser = argparse.ArgumentParser(
        description="Install/build native driver dependencies; this does not verify desktop access."
    )
    parser.add_argument(
        "--protocol-version",
        type=int,
        choices=(6,),
        default=NATIVE_PROTOCOL_VERSION,
        help=("Prepare native protocol 6 resources by default; This does not change the SDK protocol."),
    )
    args = parser.parse_args(argv)
    return _cli(lambda: build_native(protocol_version=args.protocol_version)) or None


def permissions_cli():
    return _cli(permissions)


def linux_launcher_cli():
    return _cli(linux_session_launcher) or None


if __name__ == "__main__":
    source_dir, resource_dir, wheel_platform = sys.argv[1:]
    bundle_wheel_backend(Path(source_dir), Path(resource_dir), wheel_platform)
