# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Build a Rust read engine artifact or pair it with an existing driver archive."""

import argparse
import hashlib
import json
import os
import runpy
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILD = runpy.run_path(str(ROOT / "scripts/read_engine_build.py"))
BACKENDS = runpy.run_path(str(ROOT / "scripts/backend_artifacts.py"))
FORMAT = 2
MANIFEST = "sdk-native.json"
NOTICE = BUILD["NOTICE_FILENAME"]


DEFAULT_PYTHON_TAG = "cp311"


def _source_fingerprint(root, build):
    crate = root / "computer_use_engine/interaction/read/accessibility"
    paths = [root / "scripts/read_engine_build.py", root / "scripts/sdk_native.py"]
    paths.extend(crate / name for name in ("Cargo.toml", "Cargo.lock", "rust-toolchain.toml"))
    paths.extend((crate / "src").rglob("*.rs"))
    inputs = {
        item.relative_to(root).as_posix(): BACKENDS["sha256"](item.read_bytes().replace(b"\r\n", b"\n"))
        for item in paths
    }
    return BACKENDS["sha256"](BACKENDS["canonical"]([FORMAT, build.backend_target, build.wheel_tag, inputs]))


def artifact_name(root, target, python_tag):
    build = BUILD["build_for"](target, python_tag)
    if build is None:
        raise ValueError(f"Unsupported native SDK target or Python ABI: {target}/{python_tag}.")
    return f"sdk-native-{target}-{python_tag}-{_source_fingerprint(root, build)}"


def file_digest(filename):
    with Path(filename).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def write_archive(root, build, extension, output):
    notice = BUILD["notice_path"](extension)
    manifest = {
        "format": FORMAT,
        "target": build.backend_target,
        "wheel_tag": list(build.wheel_tag),
        "filename": build.filename,
        "fingerprint": _source_fingerprint(root, build),
        "protocol": BACKENDS["protocol_version"](root),
        "sha256": file_digest(extension),
        "notice_sha256": file_digest(notice),
    }
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(MANIFEST, BACKENDS["canonical"](manifest))
        archive.write(extension, build.filename)
        archive.write(notice, NOTICE)
    return inspect_archive(root, output)


def inspect_archive(root, filename):
    with zipfile.ZipFile(filename) as archive:
        manifest = json.loads(archive.read(MANIFEST))
        tag = manifest["wheel_tag"]
        build = BUILD["build_for"](manifest["target"], tag[0]) if len(tag) == 3 else None
        if (
            manifest["format"] != FORMAT
            or build is None
            or tuple(tag) != build.wheel_tag
            or manifest["filename"] != build.filename
        ):
            raise ValueError("Invalid native SDK artifact target, ABI or extension name.")
        if len(archive.namelist()) != 3 or set(archive.namelist()) != {MANIFEST, build.filename, NOTICE}:
            raise ValueError("Native SDK artifact file list is inconsistent.")
        if manifest["protocol"] != BACKENDS["protocol_version"](root) or manifest["fingerprint"] != _source_fingerprint(
            root, build
        ):
            raise ValueError("Native SDK artifact does not match this checkout's source or protocol.")
        _read_payload(archive, manifest)
    return manifest


def check_archive(root, filename, target, python_tag):
    manifest = inspect_archive(root, filename)
    if (manifest["target"], manifest["wheel_tag"][0]) != (target, python_tag):
        raise ValueError(f"Native SDK artifact does not match requested target and ABI: {target}/{python_tag}.")
    return manifest


def _read_member(archive, name, expected_digest):
    member = archive.getinfo(name)
    if not member.file_size or stat.S_ISLNK(member.external_attr >> 16):
        raise ValueError(f"Invalid native SDK artifact file: {name}.")
    data = archive.read(member)
    if BACKENDS["sha256"](data) != expected_digest:
        raise ValueError(f"Native SDK artifact checksum mismatch: {name}.")
    return data


def _read_payload(archive, manifest):
    return (
        _read_member(archive, manifest["filename"], manifest["sha256"]),
        _read_member(archive, NOTICE, manifest["notice_sha256"]),
    )


def read_payload(filename, manifest):
    with zipfile.ZipFile(filename) as archive:
        return _read_payload(archive, manifest)


def inspect_pair(root, driver_archive, engine_archive):
    engine = inspect_archive(root, engine_archive)
    driver = BACKENDS["inspect_archive"](driver_archive, root=root, target=engine["target"])
    if driver["protocol"] != engine["protocol"]:
        raise ValueError("Driver and native SDK artifacts have different protocol versions.")
    return driver, engine


def build_archive(root, output):
    if sys.version_info.releaselevel != "final":
        raise RuntimeError("Native SDK artifacts require a final CPython release. Update uv or install that Python.")
    build = BUILD["host_build"]()
    if build is None:
        raise RuntimeError(BUILD["HOST_REQUIREMENT"])
    output.mkdir(parents=True, exist_ok=True)
    name = artifact_name(root, build.backend_target, build.wheel_tag[0])
    filename = name + ".zip"
    artifact = output / filename
    with tempfile.TemporaryDirectory(prefix=".computer-use-native-sdk-", dir=output) as temporary:
        staging = Path(temporary)
        extension = BUILD["build_read_engine"](root, staging / build.filename)
        load_extension = """
import importlib.util
import sys

spec = importlib.util.spec_from_file_location('_ax_read', sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
if not hasattr(module, 'open_read_envelope') or not hasattr(module, 'NativeReadReferences'):
    raise ImportError('The native library does not expose the Rust read engine.')
"""
        subprocess.run([sys.executable, "-I", "-c", load_extension, str(extension.resolve())], check=True)
        archive = staging / filename
        manifest = write_archive(root, build, extension, archive)
        if name.rsplit("-", 1)[1] != manifest["fingerprint"]:
            raise RuntimeError("Native SDK sources changed while building the extension.")
        archive.replace(artifact)
    return artifact


def assemble_wheel(root, driver_archive, engine_archive, output):
    ignored = {
        ".git", ".venv", ".build", "build", "dist", "target", "__pycache__", "outputs", "logs", "_ax_read_licenses",
    }

    def skip_local_artifacts(_directory, names):
        return {
            name
            for name in names
            if name in ignored
            or name.startswith(".")
            or name.endswith(".egg-info")
            or (name.startswith("_ax_read.") and name.endswith((".so", ".pyd")))
        }

    with tempfile.TemporaryDirectory(prefix="computer-use-native-wheel-") as temporary:
        staging = Path(temporary)
        driver = shutil.copyfile(driver_archive, staging / "driver.zip")
        engine = shutil.copyfile(engine_archive, staging / "engine.zip")
        inspect_pair(root, driver, engine)
        environment = {
            **os.environ,
            "COMPUTER_USE_BACKEND_ARCHIVE": str(driver),
            "COMPUTER_USE_SDK_NATIVE_ARCHIVE": str(engine),
        }
        environment.pop("COMPUTER_USE_BACKEND_DIR", None)
        source = staging / "source"
        shutil.copytree(root, source, ignore=skip_local_artifacts)
        subprocess.run(
            [sys.executable, "-m", "build", "--wheel", "--outdir", str(output.resolve()), str(source)],
            env=environment,
            check=True,
        )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build")
    build.add_argument("--output", type=Path, default=Path("dist/native"))
    name = commands.add_parser("name", help="Print the exact SDK artifact name for a target and Python ABI.")
    name.add_argument("--target", required=True, choices=BUILD["_TARGETS"])
    name.add_argument("--python-tag", required=True, choices=BUILD["_PYTHON_TAGS"])
    wheel = commands.add_parser("wheel")
    wheel.add_argument("--driver", type=Path, required=True)
    wheel.add_argument("--engine", type=Path, required=True)
    wheel.add_argument("--output", type=Path, default=Path("dist"))
    args = parser.parse_args(argv)
    try:
        if args.command == "build":
            sys.stdout.write(f"{build_archive(ROOT, args.output)}\n")
        elif args.command == "name":
            sys.stdout.write(artifact_name(ROOT, args.target, args.python_tag) + "\n")
        else:
            assemble_wheel(ROOT, args.driver, args.engine, args.output)
    except (KeyError, OSError, RuntimeError, ValueError, zipfile.BadZipFile, subprocess.CalledProcessError) as error:
        parser.exit(1, f"Native SDK packaging failed: {error}\n")


if __name__ == "__main__":
    main()
