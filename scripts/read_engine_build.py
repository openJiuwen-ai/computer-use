# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Build the SDK read engine for one native CPython release ABI."""

import os
import platform
import shutil
import subprocess
import sys
import sysconfig
import tomllib
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

HOST_REQUIREMENT = (
    "The Rust SDK requires a CPython 3.11–3.15 release ABI on macOS ARM64/x86_64, "
    "Windows x86_64 (MSVC), or glibc Linux ARM64/x86_64."
)
_PYTHON_TAGS = ("cp311", "cp312", "cp313", "cp314", "cp315")
_TARGETS = {
    "macos-arm64": "aarch64-apple-darwin",
    "macos-x86_64": "x86_64-apple-darwin",
    "windows-x86_64": "x86_64-pc-windows-msvc",
    "linux-x86_64": "x86_64-unknown-linux-gnu",
    "linux-aarch64": "aarch64-unknown-linux-gnu",
}


@dataclass(frozen=True)
class ReadEngineBuild:
    backend_target: str
    filename: str
    target: str
    wheel_tag: tuple[str, str, str]
    soabi: str
    library: str


def build_for(backend_target, python_tag):
    """Resolve the exact extension and wheel names for a published build identity."""
    target = _TARGETS.get(backend_target)
    if target is None or python_tag not in _PYTHON_TAGS:
        return None
    system, machine = backend_target.split("-", 1)
    minor = python_tag.removeprefix("cp")
    if system == "macos":
        soabi, platform_tag = f"cpython-{minor}-darwin", f"macosx_14_0_{machine}"
        extension, library = "so", "lib_ax_read.dylib"
    elif system == "windows":
        soabi, platform_tag = f"{python_tag}-win_amd64", "win_amd64"
        extension, library = "pyd", "_ax_read.dll"
    else:
        soabi, platform_tag = f"cpython-{minor}-{machine}-linux-gnu", f"linux_{machine}"
        extension, library = "so", "lib_ax_read.so"
    return ReadEngineBuild(
        backend_target,
        f"_ax_read.{soabi}.{extension}",
        target,
        (python_tag, python_tag, platform_tag),
        soabi,
        library,
    )


def host_build():
    """Select a full-API build for this exact interpreter and native host."""
    system = {"darwin": "macos", "win32": "windows", "linux": "linux"}.get(sys.platform)
    if system is None or sys.implementation.name != "cpython":
        return None
    machine = platform.machine().lower()
    machine = {"amd64": "x86_64", "aarch64": "arm64"}.get(machine, machine)
    if system == "linux" and machine == "arm64":
        machine = "aarch64"
    # Windows CPython 3.11 and 3.12 expose EXT_SUFFIX without SOABI.
    config_key = "EXT_SUFFIX" if system == "windows" else "SOABI"
    installed_abi = sysconfig.get_config_var(config_key)
    for tag in _PYTHON_TAGS:
        build = build_for(f"{system}-{machine}", tag)
        if build is None:
            return None
        expected_abi = build.filename.removeprefix("_ax_read") if system == "windows" else build.soabi
        if expected_abi == installed_abi:
            return build
    return None


def build_read_engine(root, output):
    build = host_build()
    if build is None:
        raise RuntimeError(HOST_REQUIREMENT)
    output = Path(output)
    if output.name != build.filename:
        raise RuntimeError(f"This interpreter builds {build.filename}. Build the artifact with its target Python ABI.")
    crate = Path(root) / "computer_use_engine/interaction/read/accessibility"
    # Cargo's library basename omits the Python ABI.
    target_dir = crate.resolve() / "target" / build.soabi
    with (crate / "rust-toolchain.toml").open("rb") as specification:
        toolchain = tomllib.load(specification)["toolchain"]["channel"]
    environment = {
        **os.environ,
        "PYO3_PYTHON": sys.executable,
        "RUSTC": os.environ.get("RUSTC", "rustc"),
    }
    if build.backend_target.startswith("macos-"):
        environment["MACOSX_DEPLOYMENT_TARGET"] = "14.0"
        environment["RUSTFLAGS"] = (
            "-C link-arg=-undefined -C link-arg=dynamic_lookup "
            "-C link-arg=-Wl,-install_name,@rpath/lib_ax_read.dylib"
        )
        environment.pop("CARGO_ENCODED_RUSTFLAGS", None)
    try:
        compiler = subprocess.check_output([environment["RUSTC"], "--version"], cwd=crate, env=environment, text=True)
    except FileNotFoundError as error:
        raise RuntimeError(f"Install Rust {toolchain} and make rustc and cargo available on PATH.") from error
    if not compiler.startswith(f"rustc {toolchain} "):
        raise RuntimeError(f"The read accelerator requires Rust {toolchain}, got {compiler.strip()}.")
    subprocess.run(
        ["cargo", "build", "--release", "--locked", "--target", build.target, "--target-dir", str(target_dir)],
        cwd=crate,
        env=environment,
        check=True,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=output.parent, prefix=".read-engine-") as temporary:
        pending = Path(temporary) / output.name
        shutil.copy2(target_dir / build.target / "release" / build.library, pending)
        pending.replace(output)
    return output
