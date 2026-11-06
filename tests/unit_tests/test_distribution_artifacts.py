# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import io
import json
import os
import runpy
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from computer_use_native_tools import backend_artifacts as artifacts
from computer_use_native_tools import native

ROOT = Path(__file__).resolve().parents[2]
DRIVER = "os_adapter/macos/.build/ComputerUseDriver.app/Contents/MacOS/computer-use-driver"


@pytest.fixture
def distribution(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    return runpy.run_path(str(ROOT / "scripts/distribution.py"))


@pytest.fixture
def checkout(tmp_path):
    root = tmp_path / "source"
    shutil.copytree(
        ROOT,
        root,
        ignore=shutil.ignore_patterns(
            ".git",
            ".venv",
            ".build",
            "build",
            "dist",
            "*.egg-info",
            "__pycache__",
            ".artifacts",
            ".pytest_cache",
            ".ruff_cache",
            "outputs",
            "logs",
            "report",
        ),
    )
    return root


def backend(root, directory):
    executable = root / DRIVER
    executable.parent.mkdir(parents=True, exist_ok=True)
    executable.write_bytes(b"test native executable\n")
    executable.chmod(0o755)
    output = directory / (artifacts.artifact_name(root, "macos-arm64") + ".zip")
    artifacts.write_archive(root, "macos-arm64", [executable], output, source_commit="test-commit")
    return output


def test_native_fingerprint_reuses_sdk_changes_but_invalidates_native_changes(checkout):
    original = artifacts.fingerprint(checkout, "macos-arm64")
    with (checkout / "computer_use_engine/paths.py").open("a") as stream:
        stream.write("\n# SDK-only change\n")
    assert artifacts.fingerprint(checkout, "macos-arm64") == original
    generated = checkout / "os_adapter/macos/Sources/Preview/AgentPointerAssets.swift"
    generated.write_text("// regenerated during native build\n")
    assert artifacts.fingerprint(checkout, "macos-arm64") == original
    with (checkout / "os_adapter/assets/agent-pointer/cursor.svg").open("a") as stream:
        stream.write("\n")
    assert artifacts.fingerprint(checkout, "macos-arm64") != original


def test_protocol_change_rejects_previous_backend(checkout, tmp_path):
    archive = backend(checkout, tmp_path)
    session = checkout / "computer_use_engine/protocol/session.py"
    session.write_text(session.read_text().replace("NATIVE_PROTOCOL_VERSION = 6", "NATIVE_PROTOCOL_VERSION = 7"))
    with pytest.raises(ValueError, match="sources/protocol"):
        artifacts.inspect_archive(archive, root=checkout)


@pytest.mark.parametrize("corruption", ["hash", "traversal", "target", "executable"])
def test_backend_rejects_invalid_payload(checkout, tmp_path, corruption):
    archive = backend(checkout, tmp_path)
    with zipfile.ZipFile(archive) as source:
        manifest = json.loads(source.read("backend.json"))
        data = source.read(DRIVER)
    name = DRIVER
    if corruption == "hash":
        data += b"corrupt"
    elif corruption == "traversal":
        name = "os_adapter/macos/.build/ComputerUseDriver.app/../../outside"
        manifest["files"][name] = manifest["files"].pop(DRIVER)
    elif corruption == "target":
        manifest["target"] = "windows-x86_64"
    else:
        manifest["files"][DRIVER]["mode"] = 0o644
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("backend.json", json.dumps(manifest))
        output.writestr(name, data)
    with pytest.raises(ValueError):
        artifacts.inspect_archive(archive, root=checkout, target="macos-arm64")


def test_gitcode_paginates_fuzzy_matches_and_downloads_exact_archive(checkout, tmp_path, distribution):
    archive = backend(checkout, tmp_path)
    name = archive.stem
    container = io.BytesIO()
    with zipfile.ZipFile(container, "w") as output:
        output.writestr(archive.name, archive.read_bytes())
    responses = iter(
        [
            json.dumps({"total_count": 101, "artifacts": [{"name": name + "-other"}]}).encode(),
            json.dumps(
                {"total_count": 101, "artifacts": [{"name": name, "id": "build-42", "expires_at": "9999999999999"}]}
            ).encode(),
            container.getvalue(),
        ]
    )
    requests = []

    class Opener:
        def open(self, request, timeout):
            requests.append(request)
            return io.BytesIO(next(responses))

    client = distribution["GitCodeArtifacts"]("space/project", "secret", opener=Opener())
    downloaded = client.download(checkout, "macos-arm64", tmp_path / "downloads")
    assert downloaded.read_bytes() == archive.read_bytes()
    assert "page=2" in requests[1].full_url
    assert requests[2].full_url.endswith("/actions/artifacts/build-42/zip")
    assert requests[0].get_header("Authorization") == "Bearer secret"


def test_artifact_redirect_does_not_send_token_to_storage(distribution):
    handler = distribution["ArtifactRedirect"]()
    request = urllib.request.Request("https://api.gitcode.com/artifact", headers={"Authorization": "Bearer secret"})
    result = handler.redirect_request(request, None, 302, "", {}, "https://storage.example/archive.zip")
    assert result.get_header("Authorization") is None
    with pytest.raises(ValueError, match="HTTPS"):
        handler.redirect_request(request, None, 302, "", {}, "http://storage.example/archive.zip")


def test_linux_backend_wheel_build_needs_no_native_desktop(checkout, tmp_path, distribution, monkeypatch):
    monkeypatch.setenv("COMPUTER_USE_BUILD_COMMIT", "test-commit")
    archive = distribution["build_backend"](checkout, "linux-x86_64", tmp_path / "backends")
    metadata = artifacts.inspect_archive(archive, root=checkout, target="linux-x86_64")
    (name,) = metadata["files"]
    assert name.endswith("-py3-none-any.whl")
    assert ".venv" not in name
    with zipfile.ZipFile(archive) as outer, zipfile.ZipFile(io.BytesIO(outer.read(name))) as inner:
        assert "computer_use_linux/__init__.py" in inner.namelist()


@pytest.mark.parametrize("bundled", [False, True])
def test_linux_preparation_uses_wheel_only_for_bundled_installation(checkout, monkeypatch, bundled):
    root = checkout / "os_adapter/linux"
    python = root / ".venv/bin/python"
    python.parent.mkdir(parents=True)
    python.touch()
    wheel = root / ".build/computer_use_linux-0.0.1-py3-none-any.whl"
    wheel.parent.mkdir()
    wheel.touch()
    if bundled:
        (checkout / "backend.json").write_text("{}")
    calls = []
    monkeypatch.setattr(native, "sys", SimpleNamespace(platform="linux"))
    monkeypatch.setattr(native, "run", lambda command, args, cwd: calls.append([str(command), *map(str, args)]))
    native.build_linux(checkout)
    install = next(call for call in calls if "--no-deps" in call)
    if bundled:
        assert str(wheel) in install
        assert "--editable" not in install
    else:
        assert install[-2:] == ["--editable", "."]


def test_cross_platform_wheel_installs_backend_and_restores_executable(checkout, tmp_path, distribution):
    archives, wheels, installed = tmp_path / "archives", tmp_path / "wheels", tmp_path / "installed"
    archive = backend(checkout, archives)
    distribution["build_wheels"](checkout, ["macos-arm64"], wheels, backend_directory=archives)
    (wheel,) = wheels.glob("*.whl")
    assert wheel.name.endswith("-py3-none-macosx_14_0_arm64.whl")
    subprocess.run(
        [
            "uv",
            "pip",
            "install",
            "--python",
            sys.executable,
            "--no-deps",
            "--target",
            str(installed),
            "--python-platform",
            "aarch64-apple-darwin",
            str(wheel),
        ],
        check=True,
        capture_output=True,
        env={**os.environ, "MACOSX_DEPLOYMENT_TARGET": "14.0"},
    )
    packaged = installed / "computer_use/_resources"
    assert (packaged / DRIVER).read_bytes() == b"test native executable\n"
    (packaged / DRIVER).chmod(0o644)  # Simulate an installer dropping executable bits.
    script = """
import json, os
from pathlib import Path
from computer_use import paths
Path.home = classmethod(lambda cls: Path('home').resolve())
root = paths.repository_root()
driver = Path(paths.native_driver('darwin', root)['command'])
assert driver.read_bytes() == b'test native executable\\n'
assert os.access(driver, os.X_OK)
assert json.loads((root / 'backend.json').read_text())['source_commit'] == 'test-commit'
assert not (root / 'os_adapter/windows/.build').exists()
"""
    subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(installed)},
        check=True,
        capture_output=True,
    )
    # The built wheel contains the full backend, independent of remote retention.
    assert artifacts.inspect_archive(archive, root=checkout)["target"] == "macos-arm64"
