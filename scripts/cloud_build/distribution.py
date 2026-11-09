# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Build and package the native backend for each supported platform."""

import argparse
import ast
import logging
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from email.parser import BytesParser
from pathlib import Path

from backend_artifact import (
    FORMAT,
    LINUX_LOCK,
    TARGETS,
    WHEEL_PLATFORMS,
    WINDOWS_FILES,
    canonical,
    digest,
    host_target,
    inspect_archive,
)

ROOT = Path(__file__).resolve().parents[2]
_LOG = logging.getLogger(__name__)


def source_commit(root):
    clean = subprocess.run(["git", "diff", "--quiet", "HEAD", "--"], cwd=root, check=False)
    if clean.returncode == 1:
        raise ValueError("Commit tracked source changes before making build artifacts.")
    clean.check_returncode()
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()


def protocol_version(root):
    source = root / "computer_use_engine/protocol/session.py"
    for node in ast.parse(source.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "NATIVE_PROTOCOL_VERSION" for target in node.targets
        ):
            return ast.literal_eval(node.value)
    raise ValueError("Native protocol version is missing.")


def fingerprint(root, target):
    system = target.split("-")[0]
    native = root / "os_adapter" / system
    inputs = [
        root / name for name in ("scripts/cloud_build/distribution.py", "scripts/cloud_build/backend_artifact.py")
    ]
    sources = {
        "linux": ("src", "pyproject.toml", "requirements.lock"),
        "macos": ("Sources", "Package.swift"),
        "windows": ("Sources", "Build", "Workers"),
    }
    source_names = sources.get(system)
    if source_names is None:
        raise ValueError(f"Unknown backend target: {target}")
    for name in source_names:
        path = native / name
        if path.is_dir():
            inputs.extend(p for p in path.rglob("*") if p.suffix in (".swift", ".cs", ".py") and p.is_file())
        else:
            inputs.append(path)
    if system != "linux":
        shared_inputs = (
            "scripts/native.py",
            "scripts/pointer.py",
            "computer_use_engine/json.py",
            "computer_use_engine/interaction/read/presentation/formatting.py",
        )
        inputs.extend(root / name for name in shared_inputs)
        inputs.extend(
            p
            for p in (root / "os_adapter/assets/agent-pointer").rglob("*")
            if p.is_file() and p.suffix in (".svg", ".json")
        )
    hashes = {
        path.relative_to(root).as_posix(): digest(path.read_bytes().replace(b"\r\n", b"\n"))
        for path in inputs
        if path.name not in ("AgentPointerAssets.swift", "AgentPointerAssets.cs")
    }
    return digest(canonical([FORMAT, target, protocol_version(root), hashes]))


def artifact_name(root, target):
    return f"backend-{target}-{fingerprint(root, target)}"


def validate_backend(root, path, target, *, destination=None):
    return inspect_archive(
        path,
        target,
        destination=destination,
        expected_inputs=(fingerprint(root, target), protocol_version(root)),
    )


def _build_wheel(source, output):
    subprocess.run([sys.executable, "-m", "build", "--wheel", "--outdir", str(output), str(source)], check=True)


def build_backend(root, target, output, commit):
    inputs = fingerprint(root, target)
    builder = {"system": platform.platform(), "python": platform.python_version()}
    if target != "linux-x86_64" and target != host_target():
        raise ValueError(f"Build {target} on a matching native host.")
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="cu-backend-", dir=output) as temporary:
        staging = Path(temporary)
        if target == "linux-x86_64":
            # Only build the pure Python wheel; desktop dependencies stay on the Linux host.
            source = staging / "source"
            shutil.copytree(
                root / "os_adapter/linux",
                source,
                ignore=shutil.ignore_patterns(".venv", ".build", "build", "dist", "*.egg-info", "__pycache__"),
            )
            _build_wheel(source, staging)
            (wheel,) = staging.glob("*.whl")
            files = {f"os_adapter/linux/.build/{wheel.name}": wheel, LINUX_LOCK: source / "requirements.lock"}
        elif target == "macos-arm64":
            from computer_use_native_tools.native import build_mac_os

            swift = shutil.which("swift")
            if swift is None:
                raise ValueError("Install the Swift toolchain before building the macOS backend.")
            driver = build_mac_os(root, configuration="release")
            bundle = driver.parents[2]
            files = {p.relative_to(root).as_posix(): p for p in bundle.rglob("*") if p.is_file()}
            builder["swift"] = subprocess.check_output([Path(swift).absolute(), "--version"], text=True).strip()
        else:
            from computer_use_native_tools.native import build_windows

            build_windows(root)
            files = {name: root / name for name in WINDOWS_FILES}
        if fingerprint(root, target) != inputs:
            raise ValueError("Native inputs changed during the build.")
        manifest = {
            "format": FORMAT,
            "target": target,
            "protocol": protocol_version(root),
            "source_commit": commit,
            "fingerprint": inputs,
            "builder": builder,
            "files": {},
        }
        pending = staging / "backend.zip"
        with zipfile.ZipFile(pending, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, path in sorted(files.items()):
                if path.is_symlink():
                    raise ValueError(f"Backend file must not be a symlink: {name}")
                data = path.read_bytes()
                mode = 0o755 if name.endswith("/computer-use-driver") else 0o644
                manifest["files"][name] = {"sha256": digest(data), "mode": mode}
                archive.writestr(name, data)
            archive.writestr("backend.json", canonical(manifest))
        validate_backend(root, pending, target)
        result = output / f"backend-{target}-{inputs}.zip"
        pending.replace(result)
    return result


def _assemble_platform(source, archive, staging, target):
    payload = staging / "payload"
    payload.mkdir(parents=True)
    manifest = validate_backend(source, archive, target, destination=payload)
    cache_manifest = {name: entry["sha256"] for name, entry in manifest["files"].items()}
    cache_manifest["backend.json"] = digest((payload / "backend.json").read_bytes())
    (payload / "manifest.json").write_bytes(canonical(cache_manifest))
    tag = WHEEL_PLATFORMS.get(target)
    if tag is None:
        raise ValueError(f"Unknown wheel platform: {target}")
    environment = {**os.environ, "COMPUTER_USE_BACKEND_DIR": str(payload)}
    environment.pop("COMPUTER_USE_BACKEND_ARCHIVE", None)
    subprocess.run(
        [
            sys.executable,
            "-m",
            "build",
            "--wheel",
            "--outdir",
            str(staging),
            f"--config-setting=--build-option=--plat-name={tag}",
            str(source),
        ],
        env=environment,
        check=True,
    )
    (wheel,) = staging.glob("*.whl")
    with zipfile.ZipFile(wheel) as built:
        (dist_info,) = {name.rpartition("/")[0] for name in built.namelist() if name.endswith(".dist-info/WHEEL")}
        tags = BytesParser().parsebytes(built.read(f"{dist_info}/WHEEL")).get_all("Tag")
        if tags != [f"py3-none-{tag}"] or not wheel.name.endswith(f"-py3-none-{tag}.whl"):
            raise ValueError(f"Built wheel has the wrong platform tag for {target}.")
        prefix = "computer_use/_resources/"
        installed = [name.removeprefix(prefix) for name in built.namelist() if name.startswith(prefix)]
        expected = {"manifest.json", *cache_manifest}
        if len(installed) != len(expected) or set(installed) != expected:
            raise ValueError(f"Built wheel contains unexpected backend files for {target}.")
        for name in expected:
            info = built.getinfo(prefix + name)
            if digest(built.read(info)) != digest((payload / name).read_bytes()):
                raise ValueError(f"Built wheel contains the wrong backend file: {name}")
            if target == "macos-arm64" and info.external_attr >> 16 & 0o777 != (payload / name).stat().st_mode & 0o777:
                raise ValueError(f"Built wheel lost macOS backend permissions: {name}")
    return wheel


def set_package_version(project, value):
    import tomllib

    from packaging.version import Version

    content = project.read_text(encoding="utf-8")
    current = tomllib.loads(content)["project"]["version"]
    updated, count = re.subn(rf'(?m)^version = "{re.escape(current)}"$', f'version = "{Version(value)}"', content)
    if count != 1:
        raise ValueError("Could not set the project version in the staged pyproject.toml.")
    project.write_text(updated, encoding="utf-8")


def package_wheels(root, targets, backend_dir, output, commit, *, package_version=None):
    with tempfile.TemporaryDirectory(prefix="cu-wheel-") as temporary:
        staging = Path(temporary)
        tree = staging / "source.zip"
        with tree.open("wb") as stream:
            subprocess.run(["git", "archive", "--format=zip", commit], cwd=root, stdout=stream, check=True)
        wheels = []
        with zipfile.ZipFile(tree) as archive:
            for target in targets:
                source = staging / target / "source"
                archive.extractall(source)
                if package_version is not None:
                    set_package_version(source / "pyproject.toml", package_version)
                backend = backend_dir / f"{artifact_name(source, target)}.zip"
                wheels.append(_assemble_platform(source, backend, source.parent, target))
        output.mkdir(parents=True, exist_ok=True)
        return [Path(shutil.move(wheel, output / wheel.name)) for wheel in wheels]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    backend = commands.add_parser("backend")
    backend.add_argument("--target", choices=TARGETS)
    backend.add_argument("--output", type=Path, default=Path(".artifacts/backends"))
    name = commands.add_parser("name")
    name.add_argument("--target", choices=TARGETS, required=True)
    wheels = commands.add_parser("wheels")
    wheels.add_argument("--target", choices=TARGETS, action="append")
    wheels.add_argument("--backend-dir", type=Path, required=True)
    wheels.add_argument("--output", type=Path, default=Path("dist"))
    wheels.add_argument("--version")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)
    try:
        if args.command == "name":
            _LOG.info("%s", artifact_name(ROOT, args.target))
            return
        commit = source_commit(ROOT)
        if args.command == "backend":
            result = [build_backend(ROOT, args.target or host_target(), args.output.resolve(), commit)]
        else:
            result = package_wheels(
                ROOT,
                tuple(dict.fromkeys(args.target or TARGETS)),
                args.backend_dir.resolve(),
                args.output.resolve(),
                commit,
                package_version=args.version,
            )
        for path in result:
            _LOG.info("%s", path)
    except (RuntimeError, ValueError, KeyError, OSError, zipfile.BadZipFile, subprocess.CalledProcessError) as error:
        parser.exit(1, f"Packaging failed: {error}\n")


if __name__ == "__main__":
    main()
