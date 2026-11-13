# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Build the SDK read engine for one native CPython release ABI."""

import hashlib
import io
import json
import os
import platform
import runpy
import shutil
import subprocess
import sys
import sysconfig
import tarfile
import tomllib
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.request import urlopen

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
NOTICE_FILENAME = "THIRD_PARTY_LICENSES.txt"

# These two notices come from rust-lang/rust's library/Cargo.lock, not from
# the read engine's Cargo.lock. The prebuilt toolchain only ships Rust's own
# top-level copyright and license files.
_RUST_1851_NOTICES = (
    ("adler-1.0.2", "f26201604c87b1e01bd3d98f8d5d9a8fcbb815e8cedb41ffccbeb4bf593a35fe", "LICENSE-0BSD"),
    ("compiler_builtins-0.1.140", "df14d41c5d172a886df3753d54238eefb0f61c96cbd8b363c33ccc92c457bee3", "LICENSE.txt"),
)


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


def notice_path(extension):
    return Path(extension).parent / "_ax_read_licenses" / NOTICE_FILENAME


def _standard_dependency_notice(cache, package, checksum, filename):
    cached = cache / f"{package}.crate"
    if cached.is_file():
        data = cached.read_bytes()
    else:
        name = package.rsplit("-", 1)[0]
        with urlopen(f"https://static.crates.io/crates/{name}/{cached.name}", timeout=30) as response:
            data = response.read()
    if hashlib.sha256(data).hexdigest() != checksum:
        raise ValueError(f"Rust standard library dependency {package} has an unexpected checksum.")
    if not cached.is_file():
        with TemporaryDirectory(dir=cache) as temporary:
            pending = Path(temporary) / cached.name
            pending.write_bytes(data)
            pending.replace(cached)
    with tarfile.open(fileobj=io.BytesIO(data)) as archive, archive.extractfile(f"{package}/{filename}") as notice:
        return notice.read().decode("utf-8")


def _read_engine_notice(crate, build, environment, toolchain):
    def run(*command):
        return subprocess.check_output(command, cwd=crate, env=environment, text=True, encoding="utf-8")

    def section(owner, filename, content):
        return f"{owner} — {filename}\n\n{content.strip()}\n"

    sysroot = Path(run(environment["RUSTC"], "--print", "sysroot").strip())
    rust_docs = sysroot / "share/doc/rust"
    sections = ["Third-party licenses for the accessibility read engine\n"]
    for filename in ("COPYRIGHT", "LICENSE-APACHE", "LICENSE-MIT"):
        sections.append(section(f"Rust {toolchain}", filename, (rust_docs / filename).read_text(encoding="utf-8")))
    unicode_notice = runpy.run_path(str(crate / "build_unicode.py"))["unicode_notice"]()
    sections.append(section("Unicode Character Database 14.0.0 and 18.0.0", "LICENSE", unicode_notice))
    cache = Path(environment.get("CARGO_HOME") or Path.home() / ".cargo").expanduser() / "computer-use-license-crates"
    cache.mkdir(parents=True, exist_ok=True)
    for package, checksum, filename in _RUST_1851_NOTICES:
        sections.append(section(package, filename, _standard_dependency_notice(cache, package, checksum, filename)))

    metadata = json.loads(
        run("cargo", "metadata", "--locked", "--format-version", "1", "--filter-platform", build.target)
    )
    for package in sorted(metadata["packages"], key=lambda item: (item["name"], item["version"])):
        if package["id"] in metadata["workspace_members"]:
            continue
        directory = Path(package["manifest_path"]).parent
        owner = f"{package['name']} {package['version']} ({package['license'] or 'see license text'})"
        notices = {
            path
            for path in directory.iterdir()
            if path.is_file() and path.name.startswith(("LICENSE", "COPYING", "COPYRIGHT", "NOTICE"))
        }
        if package["license_file"]:
            notices.add(directory / package["license_file"])
        if not notices:
            raise ValueError(f"No upstream license file found for {owner}.")
        for path in sorted(notices):
            sections.append(section(owner, path.name, path.read_text(encoding="utf-8")))
    return "\n".join(sections)


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
    notice = _read_engine_notice(crate, build, environment, toolchain)
    output.parent.mkdir(parents=True, exist_ok=True)
    destination = notice_path(output)
    destination.parent.mkdir(exist_ok=True)
    with TemporaryDirectory(dir=output.parent, prefix=".read-engine-") as temporary:
        pending = Path(temporary) / output.name
        pending_notice = Path(temporary) / NOTICE_FILENAME
        shutil.copy2(target_dir / build.target / "release" / build.library, pending)
        pending_notice.write_text(notice, encoding="utf-8", newline="\n")
        pending_notice.replace(destination)
        pending.replace(output)
    return output
