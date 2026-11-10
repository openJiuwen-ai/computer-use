# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Build backends once, then assemble SDK wheels on any Python build host."""

import argparse
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

from backend_artifacts import TARGETS, artifact_name, fingerprint, host_target, inspect_archive, sha256, write_archive
from console import write_result

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TARGETS = ("macos-arm64", "windows-x86_64", "linux-x86_64")


class ArtifactRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        if urllib.parse.urlsplit(newurl).scheme != "https":
            raise ValueError("Artifact downloads require HTTPS.")
        redirected = super().redirect_request(request, fp, code, msg, headers, newurl)
        if urllib.parse.urlsplit(request.full_url).netloc != urllib.parse.urlsplit(newurl).netloc:
            redirected.remove_header("Authorization")
        return redirected


class GitCodeArtifacts:
    def __init__(self, repository, token, *, api="https://api.gitcode.com", opener=None):
        parts = repository.split("/")
        if len(parts) != 2 or not all(parts):
            raise ValueError("Repository must be owner/repo.")
        if urllib.parse.urlsplit(api).scheme != "https":
            raise ValueError("GitCode API requires HTTPS.")
        owner, repo = (urllib.parse.quote(part, safe="") for part in parts)
        self.base = f"{api.rstrip('/')}/api/v8/repos/{owner}/{repo}"
        self.token = token
        self.opener = opener or urllib.request.build_opener(ArtifactRedirect())

    def get(self, suffix):
        headers = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        request = urllib.request.Request(self.base + suffix, headers=headers)
        return self.opener.open(request, timeout=120)

    def download(self, root, target, directory):
        name = artifact_name(root, target)
        page = 1
        while True:
            query = urllib.parse.urlencode(
                {"name": name, "sort": "created", "direction": "desc", "page": page, "per_page": 100}
            )
            with self.get(f"/actions/artifacts?{query}") as response:
                listing = json.load(response)
            artifacts = listing["artifacts"]
            for artifact in artifacts:
                if artifact["name"] != name:
                    continue  # GitCode's name filter is a fuzzy match.
                if artifact.get("expires_at") and int(artifact["expires_at"]) <= time.time() * 1000:
                    continue
                artifact_id = urllib.parse.quote(str(artifact["id"]), safe="")
                try:
                    with self.get(f"/actions/artifacts/{artifact_id}/zip") as response:
                        data = response.read()
                except urllib.error.HTTPError as error:
                    if error.code in (404, 410):
                        continue  # Artifact retention can expire between listing and downloading.
                    raise
                digest = artifact.get("digest")
                if digest and sha256(data) != digest.removeprefix("sha256:"):
                    raise ValueError("GitCode artifact download checksum mismatch.")
                # upload-artifact wraps the backend zip in its own downloadable zip.
                with zipfile.ZipFile(io.BytesIO(data)) as container:
                    matches = [p for p in container.namelist() if Path(p).name == name + ".zip"]
                    if len(matches) != 1:
                        raise ValueError("GitCode artifact must contain exactly one matching backend archive.")
                    payload = container.read(matches[0])
                directory = Path(directory)
                directory.mkdir(parents=True, exist_ok=True)
                output = directory / (name + ".zip")
                with tempfile.TemporaryDirectory(dir=directory) as temporary:
                    pending = Path(temporary) / output.name
                    pending.write_bytes(payload)
                    inspect_archive(pending, root=root, target=target)
                    shutil.copyfile(pending, output)
                return output
            if not artifacts or page * 100 >= listing["total_count"]:
                break
            page += 1
        raise ValueError(
            f"No retained backend artifact matches {target}. Build a matching archive, then pass --backend-dir."
        )


def build_backend(root, target, directory):
    if not target.startswith("linux-") and target != host_target():
        raise ValueError(f"Build {target} on its native host; current host is {host_target()}.")
    root, directory = Path(root), Path(directory).resolve()
    inputs = fingerprint(root, target)
    if target.startswith("linux-"):
        # Linux's backend is Python, not a relocatable venv. Package its wheel;
        # PyGObject/AT-SPI and the runtime environment are prepared on the desktop.
        output = root / "os_adapter/linux/.build"
        output.mkdir(parents=True, exist_ok=True)
        for old in output.glob("*.whl"):
            old.unlink()
        with tempfile.TemporaryDirectory(prefix="computer-use-linux-build-") as temporary:
            source = Path(temporary) / "source"
            shutil.copytree(
                root / "os_adapter/linux",
                source,
                ignore=shutil.ignore_patterns(".venv", ".build", "build", "dist", "*.egg-info", "__pycache__"),
            )
            subprocess.run(
                [sys.executable, "-m", "build", "--wheel", "--outdir", str(output), str(source)],
                check=True,
            )
        paths = list(output.glob("*.whl"))
    else:
        from computer_use_native_tools.native import build_mac_os, build_windows

        if target.startswith("macos-"):
            executable = build_mac_os(root)
            paths = [path for path in executable.parents[2].rglob("*") if path.is_file()]
        else:
            executable = build_windows(root)
            paths = [
                executable,
                executable.with_name("computer-use-capture.exe"),
                executable.with_name("Interop.NativeUIA.dll"),
            ]
    commit = (
        os.environ.get("COMPUTER_USE_BUILD_COMMIT")
        or subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    )
    output = directory / (artifact_name(root, target) + ".zip")
    if fingerprint(root, target) != inputs:
        raise ValueError("Native inputs changed during the build; run it again from a stable checkout.")
    write_archive(root, target, paths, output, source_commit=commit)
    return output


def build_wheels(root, targets, directory, *, backend_directory=None, repository=None, token=None):
    root, directory = Path(root), Path(directory).resolve()
    if backend_directory is None and not repository:
        raise ValueError("Supply --backend-dir or --repository owner/repo (or GITCODE_REPOSITORY).")
    directory.mkdir(parents=True, exist_ok=True)
    ignore_artifacts = shutil.ignore_patterns(
        ".git", ".venv", ".build", "build", "dist", "__pycache__", ".artifacts",
        ".pytest_cache", ".ruff_cache", "outputs", "logs", "report", "*.egg-info",
    )
    windows_sources = root / "os_adapter/windows"

    def skip_local_artifacts(directory, names):
        ignored = ignore_artifacts(directory, names)
        # Windows glob filtering folds case and would drop the real Build
        # sources under os_adapter/windows along with generated build output.
        if Path(directory) == windows_sources:
            ignored.discard("Build")
        return ignored

    with tempfile.TemporaryDirectory(prefix="computer-use-package-") as temporary:
        temporary = Path(temporary)
        client = GitCodeArtifacts(repository, token) if backend_directory is None else None
        for target in targets:
            backend = (
                Path(backend_directory) / (artifact_name(root, target) + ".zip")
                if backend_directory is not None
                else client.download(root, target, temporary / "backends")
            )
            inspect_archive(backend, root=root, target=target)
            # Separate build trees prevent another target's binary leaking into a wheel.
            source = temporary / target
            shutil.copytree(root, source, ignore=skip_local_artifacts)
            environment = {**os.environ, "COMPUTER_USE_BACKEND_ARCHIVE": str(backend.resolve())}
            environment.pop("COMPUTER_USE_BACKEND_DIR", None)
            subprocess.run(
                [sys.executable, "-m", "build", "--wheel", "--outdir", str(directory), str(source)],
                env=environment,
                check=True,
            )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    native = commands.add_parser("backend", help="Build a native backend archive on its own OS.")
    native.add_argument("--target", choices=TARGETS, default=None)
    native.add_argument("--output", type=Path, default=Path(".artifacts/backends"))
    name = commands.add_parser("name", help="Print the exact matching artifact name for a target.")
    name.add_argument("--target", choices=TARGETS, required=True)
    wheels = commands.add_parser("wheels", help="Assemble SDK wheels without native compilation or publishing.")
    wheels.add_argument("--target", choices=TARGETS, action="append")
    wheels.add_argument("--backend-dir", type=Path)
    wheels.add_argument(
        "--repository", default=os.environ.get("GITCODE_REPOSITORY") or os.environ.get("ATOMGIT_REPOSITORY")
    )
    wheels.add_argument("--output", type=Path, default=Path("dist"))
    args = parser.parse_args(argv)
    try:
        if args.command == "name":
            write_result(sys.stdout, artifact_name(ROOT, args.target))
        elif args.command == "backend":
            write_result(sys.stdout, str(build_backend(ROOT, args.target or host_target(), args.output)))
        else:
            build_wheels(
                ROOT,
                args.target or DEFAULT_TARGETS,
                args.output,
                backend_directory=args.backend_dir,
                repository=args.repository,
                token=os.environ.get("GITCODE_TOKEN") or os.environ.get("ATOMGIT_TOKEN"),
            )
    except urllib.error.HTTPError as error:
        parser.exit(
            1, f"GitCode artifact API returned HTTP {error.code}; check token permissions and artifact retention.\n"
        )
    except (ValueError, OSError, KeyError, zipfile.BadZipFile, subprocess.CalledProcessError) as error:
        parser.exit(1, f"Packaging failed: {error}\n")


if __name__ == "__main__":
    main()
