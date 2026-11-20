# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import base64
import io
import json
import os
import re
import runpy
import shutil
import stat
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from computer_use.interaction.read.accessibility import _native as read_engine
from computer_use_native_tools import backend_artifacts as artifacts
from computer_use_native_tools import native, pointer, read_engine_build, sdk_native

ROOT = Path(__file__).resolve().parents[2]
DRIVER = artifacts.MAC_DRIVER
MAC_INFO = artifacts.MAC_INFO
READ_ENGINE = b"test packaged Rust read engine"


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
            "target",
            "_ax_read.*.so",
            "_ax_read.*.pyd",
        ),
    )
    return root


def backend(root, directory):
    target = "macos-arm64"
    fingerprint = artifacts.fingerprint(root, target)
    output = directory / f"backend-{target}-{fingerprint}.zip"
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = b"test native executable\n"
    executable = zipfile.ZipInfo(DRIVER)
    executable.create_system = 3
    executable.external_attr = (stat.S_IFREG | 0o755) << 16
    info = b"bundle plist fixture"
    manifest = {
        "format": artifacts.FORMAT,
        "target": target,
        "fingerprint": fingerprint,
        "protocol": artifacts.protocol_version(root),
        "source_commit": "test-commit",
        "files": {
            DRIVER: {"sha256": artifacts.sha256(payload), "mode": 0o755},
            MAC_INFO: {"sha256": artifacts.sha256(info), "mode": 0o644},
        },
    }
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("backend.json", artifacts.canonical(manifest))
        archive.writestr(executable, payload)
        archive.writestr(MAC_INFO, info)
    artifacts.inspect_archive(output, root=root, target=target)
    return output


def engine_archive(root, directory, target, python_tag):
    directory.mkdir(parents=True, exist_ok=True)
    build = read_engine_build.build_for(target, python_tag)
    extension = directory / build.filename
    extension.write_bytes(READ_ENGINE)
    notice = read_engine_build.notice_path(extension)
    notice.parent.mkdir(exist_ok=True)
    notice.write_text("read engine license fixture", encoding="utf-8")
    output = directory / (sdk_native.artifact_name(root, target, python_tag) + ".zip")
    sdk_native.write_archive(root, build, extension, output)
    return output


def test_native_fingerprint_tracks_pointer_build_inputs(checkout):
    target = "macos-arm64"
    original = artifacts.fingerprint(checkout, target)
    linux = artifacts.fingerprint(checkout, "linux-x86_64")
    with (checkout / "computer_use_engine/paths.py").open("a") as stream:
        stream.write("\n# SDK runtime path change\n")
    generated = checkout / "os_adapter/macos/Sources/Preview/AgentPointerAssets.swift"
    generated.write_text("// regenerated during native build\n", encoding="utf-8")
    assert artifacts.fingerprint(checkout, target) == original

    pointer_inputs = (
        "computer_use_engine/json.py",
        "computer_use_engine/interaction/read/presentation/formatting.py",
        "os_adapter/assets/agent-pointer/cursor.svg",
    )
    for name in pointer_inputs:
        path = checkout / name
        content = path.read_bytes()
        path.write_bytes(content + b"\n")
        assert artifacts.fingerprint(checkout, target) != original
        assert artifacts.fingerprint(checkout, "linux-x86_64") == linux
        path.write_bytes(content)


def test_pointer_compiler_runs_without_an_sdk_extension(monkeypatch):
    def unavailable():
        raise RuntimeError("Rust read engine is not built on the native driver builder")

    monkeypatch.setattr(read_engine, "require_read_engine", unavailable)
    source = ROOT / "os_adapter/assets/agent-pointer"
    asset = pointer.compile_assets(
        (source / "cursor.svg").read_text(encoding="utf-8"),
        (source / "motion.json").read_text(encoding="utf-8"),
    )
    assert "CGPoint" in pointer.swift_source(asset, "build-only")
    assert "PointF" in pointer.csharp_source(asset, "build-only")


def test_rust_change_invalidates_native_sdk_but_keeps_driver_archive(checkout):
    original = {target: artifacts.fingerprint(checkout, target) for target in artifacts.TARGETS}
    engines = {target: sdk_native.artifact_name(checkout, target, "cp311") for target in artifacts.TARGETS}
    with (checkout / "computer_use_engine/interaction/read/accessibility/src/lib.rs").open("a") as stream:
        stream.write("\n// read engine change\n")
    assert original == {target: artifacts.fingerprint(checkout, target) for target in original}
    assert all(sdk_native.artifact_name(checkout, target, "cp311") != name for target, name in engines.items())


def test_macos_driver_archive_cannot_supply_an_embedded_read_engine(checkout, tmp_path):
    driver = backend(checkout, tmp_path)
    manifest = artifacts.inspect_archive(driver, root=checkout, target="macos-arm64")
    assert set(manifest["files"]) == {DRIVER, MAC_INFO}

    with zipfile.ZipFile(driver, "a") as archive:
        archive.writestr("read-engine.bin", READ_ENGINE)
    with pytest.raises(ValueError, match="file list is inconsistent"):
        artifacts.inspect_archive(driver, root=checkout, target="macos-arm64")


def test_protocol_change_rejects_previous_backend(checkout, tmp_path):
    archive = backend(checkout, tmp_path)
    session = checkout / "computer_use_engine/protocol/session.py"
    session.write_text(session.read_text().replace("NATIVE_PROTOCOL_VERSION = 6", "NATIVE_PROTOCOL_VERSION = 7"))
    with pytest.raises(ValueError, match="sources/protocol"):
        artifacts.inspect_archive(archive, root=checkout)


def test_windows_wheel_preserves_native_build_sources(checkout, tmp_path, distribution, monkeypatch):
    target = "windows-x86_64"
    shutil.copytree(ROOT / "os_adapter/windows/Build", checkout / "os_adapter/windows/Build", dirs_exist_ok=True)
    (checkout / "build").mkdir()
    (checkout / "local/build").mkdir(parents=True)
    output = checkout / "os_adapter/windows/.build"
    output.mkdir()
    files = [output / name for name in ("computer-use-driver.exe", "computer-use-capture.exe", "Interop.NativeUIA.dll")]
    for path in files:
        path.write_bytes(b"test binary")
    archive = tmp_path / (artifacts.artifact_name(checkout, target) + ".zip")
    artifacts.write_archive(checkout, target, files, archive, source_commit="test-commit")
    engine_archive(checkout, tmp_path, target, "cp312")
    stages = []

    def check_staged_source(command, **kwargs):
        stage = Path(command[-1])
        stages.append(stage)
        assert not (stage / "build").exists()
        assert not (stage / "local/build").exists()
        assert (stage / "os_adapter/windows/Build").is_dir()
        assert "COMPUTER_USE_BACKEND_DIR" not in kwargs["env"]
        driver, engine = sdk_native.inspect_pair(
            stage,
            kwargs["env"]["COMPUTER_USE_BACKEND_ARCHIVE"],
            kwargs["env"]["COMPUTER_USE_SDK_NATIVE_ARCHIVE"],
        )
        assert driver["target"] == target and engine["wheel_tag"][0] == "cp312"

    monkeypatch.setenv("COMPUTER_USE_BACKEND_DIR", str(tmp_path / "cloud-payload"))
    monkeypatch.setattr(subprocess, "run", check_staged_source)
    distribution["build_wheels"](
        checkout,
        [target],
        tmp_path / "wheels",
        python_tag="cp312",
        backend_directory=tmp_path,
    )
    assert len(stages) == 1


def test_wheels_choose_exact_native_read_abi(checkout, tmp_path, distribution, monkeypatch):
    target = "linux-aarch64"
    archive_dir = tmp_path / "downloaded"
    payload = checkout / "os_adapter/linux/.build/driver-0.0.1-py3-none-any.whl"
    payload.parent.mkdir(parents=True)
    payload.write_bytes(b"unchanged driver wheel")
    archive = archive_dir / (artifacts.artifact_name(checkout, target) + ".zip")
    artifacts.write_archive(checkout, target, [payload], archive, source_commit="test-commit")
    first = engine_archive(checkout, archive_dir, target, "cp311")
    second = engine_archive(checkout, archive_dir, target, "cp313")
    assembled = []

    def assemble(_command, *, env, **_kwargs):
        assembled.append((Path(env["COMPUTER_USE_BACKEND_ARCHIVE"]), Path(env["COMPUTER_USE_SDK_NATIVE_ARCHIVE"])))

    def build(tag):
        distribution["build_wheels"](
            checkout,
            [target],
            tmp_path / "wheels",
            python_tag=tag,
            backend_directory=archive_dir,
        )

    monkeypatch.setattr(subprocess, "run", assemble)
    build("cp311")
    build("cp313")
    assert assembled == [(archive, first), (archive, second)]
    assembled.clear()
    second.unlink()
    with pytest.raises(FileNotFoundError):
        build("cp313")
    assert not assembled
    shutil.copy2(first, second)
    with pytest.raises(ValueError, match="requested target and ABI"):
        build("cp313")
    assert not assembled


def test_read_engine_wheel_pairs_current_extension_with_retained_driver(checkout, tmp_path, distribution, monkeypatch):
    target = "macos-arm64"
    driver = backend(checkout, tmp_path / "drivers")
    original = driver.read_bytes()
    rust = checkout / "computer_use_engine/interaction/read/accessibility/src/lib.rs"
    with rust.open("a") as stream:
        stream.write("\n// later SDK implementation\n")
    assert artifacts.artifact_name(checkout, target) == driver.stem
    engine = engine_archive(checkout, tmp_path / "native", target, "cp311")
    assembled = []

    class RetainedDriver:
        def __init__(self, repository, token):
            assert repository == "space/project" and token == "fixture-token"

        def download(self, root, requested_target, _directory):
            assert root == checkout and requested_target == target
            return driver

        def download_engine(self, root, requested_target, requested_tag, _directory):
            assert root == checkout and requested_target == target and requested_tag == "cp311"
            return engine

    def assemble(command, *, env, **_kwargs):
        retained = Path(env["COMPUTER_USE_BACKEND_ARCHIVE"])
        extension = Path(env["COMPUTER_USE_SDK_NATIVE_ARCHIVE"])
        archived_driver, archived_sdk = sdk_native.inspect_pair(Path(command[-1]), retained, extension)
        assert archived_driver["target"] == target and archived_sdk["wheel_tag"][0] == "cp311"
        assembled.append((retained, extension))

    build = distribution["build_wheels"]
    monkeypatch.setitem(build.__globals__, "GitCodeArtifacts", RetainedDriver)
    monkeypatch.setattr(subprocess, "run", assemble)
    build(checkout, [target], tmp_path / "wheels", repository="space/project", token="fixture-token")
    assert assembled == [(driver, engine)]
    assert driver.read_bytes() == original


@pytest.mark.parametrize("corruption", ["hash", "traversal", "target", "executable", "missing-plist"])
def test_backend_rejects_invalid_payload(checkout, tmp_path, corruption):
    archive = backend(checkout, tmp_path)
    with zipfile.ZipFile(archive) as source:
        manifest = json.loads(source.read("backend.json"))
        data = source.read(DRIVER)
        info = source.read(MAC_INFO)
    name = DRIVER
    if corruption == "hash":
        data += b"corrupt"
    elif corruption == "traversal":
        name = f"{artifacts.MAC_BUNDLE}/../../outside"
        manifest["files"][name] = manifest["files"].pop(DRIVER)
    elif corruption == "target":
        manifest["target"] = "windows-x86_64"
    elif corruption == "executable":
        manifest["files"][DRIVER]["mode"] = 0o644
    elif corruption == "missing-plist":
        del manifest["files"][MAC_INFO]
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("backend.json", json.dumps(manifest))
        output.writestr(name, data)
        if MAC_INFO in manifest["files"]:
            output.writestr(MAC_INFO, info)
    with pytest.raises(ValueError):
        artifacts.inspect_archive(archive, root=checkout, target="macos-arm64")


@pytest.mark.parametrize("kind", ["driver", "sdk"])
def test_gitcode_paginates_fuzzy_matches_and_downloads_exact_archive(checkout, tmp_path, distribution, kind):
    target = "macos-arm64"
    archive = backend(checkout, tmp_path) if kind == "driver" else engine_archive(checkout, tmp_path, target, "cp312")
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
    if kind == "driver":
        downloaded = client.download(checkout, target, tmp_path / "downloads")
    else:
        downloaded = client.download_engine(checkout, target, "cp312", tmp_path / "downloads")
    assert downloaded.read_bytes() == archive.read_bytes()
    assert "page=2" in requests[1].full_url
    assert requests[2].full_url.endswith("/actions/artifacts/build-42/zip")
    assert requests[0].get_header("Authorization") == "Bearer secret"


def test_remote_driver_validates_target_before_publishing(checkout, tmp_path, distribution, monkeypatch):
    remote = runpy.run_path(str(ROOT / "scripts/remote_backend.py"))["main"]
    monkeypatch.setitem(remote.__globals__, "ROOT", checkout)
    monkeypatch.setenv("BUILDER_HOST", "ci@fixture.example")
    monkeypatch.setenv("BUILDER_SSH_KEY", "synthetic-key")
    monkeypatch.setenv("BUILDER_KNOWN_HOSTS", "synthetic-host")
    monkeypatch.setenv("BUILDER_UV", "/tools/with space/uv")
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/ssh" if name == "ssh" else None)
    target = "macos-arm64"
    artifact = backend(checkout, tmp_path / "fixture")
    manifest = artifacts.inspect_archive(artifact)
    wrong = tmp_path / "wrong.zip"
    with zipfile.ZipFile(artifact) as source, zipfile.ZipFile(wrong, "w") as output:
        output.writestr("backend.json", artifacts.canonical({**manifest, "target": "macos-x86_64"}))
        for name in manifest["files"]:
            output.writestr(name, source.read(name))
    responses = iter([wrong.read_bytes(), artifact.read_bytes()])
    output = tmp_path / "published"

    def git(command, **_kwargs):
        return "test-commit" if command[1] == "rev-parse" else b"exact checkout bytes"

    def ssh(command, **kwargs):
        assert kwargs["input"] == b"exact checkout bytes"
        assert "StrictHostKeyChecking=yes" in command
        encoded = re.search(r"b64decode\('([^']+)'\)", command[-1])[1]
        bootstrap = base64.b64decode(encoded).decode()
        assert "'/tools/with space/uv', 'run', '--python', '3.12'" in bootstrap
        assert "'scripts/distribution.py', 'backend'" in bootstrap
        assert "'test-commit'" in bootstrap
        return SimpleNamespace(stdout=next(responses))

    monkeypatch.setattr(subprocess, "check_output", git)
    monkeypatch.setattr(subprocess, "run", ssh)
    args = ["--target", target, "--output", str(output)]
    with pytest.raises(ValueError, match="Backend target mismatch"):
        remote(args)
    assert not output.exists()
    remote(args)
    assert (output / artifact.name).read_bytes() == artifact.read_bytes()


def test_artifact_redirect_does_not_send_token_to_storage(distribution):
    handler = distribution["ArtifactRedirect"]()
    request = urllib.request.Request("https://api.gitcode.com/artifact", headers={"Authorization": "Bearer secret"})
    result = handler.redirect_request(request, None, 302, "", {}, "https://storage.example/archive.zip")
    assert result.get_header("Authorization") is None
    with pytest.raises(ValueError, match="HTTPS"):
        handler.redirect_request(request, None, 302, "", {}, "http://storage.example/archive.zip")


def test_linux_backend_wheel_build_needs_no_native_desktop(checkout, tmp_path, distribution, monkeypatch):
    monkeypatch.setenv("COMPUTER_USE_BUILD_COMMIT", "test-commit")
    pytest.importorskip("build.__main__")
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
    pytest.importorskip("build.__main__")
    archives, wheels, installed = tmp_path / "archives", tmp_path / "wheels", tmp_path / "installed"
    archive = backend(checkout, archives)
    engine_archive(checkout, archives, "macos-arm64", "cp311")
    extension = "interaction/read/accessibility/_ax_read.cpython-311-darwin.so"
    (checkout / "computer_use_engine" / extension).write_bytes(b"stale editable extension")
    distribution["build_wheels"](checkout, ["macos-arm64"], wheels, backend_directory=archives)
    (wheel,) = wheels.glob("*.whl")
    assert wheel.name.endswith("-cp311-cp311-macosx_14_0_arm64.whl")
    with zipfile.ZipFile(wheel) as packaged_wheel:
        assert packaged_wheel.read(f"computer_use/{extension}") == READ_ENGINE
        assert not any(
            name.startswith("computer_use/_resources/") and "_ax_read" in name for name in packaged_wheel.namelist()
        )
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
            "--python-version",
            "3.11",
            str(wheel),
        ],
        check=True,
        capture_output=True,
        env={**os.environ, "MACOSX_DEPLOYMENT_TARGET": "14.0"},
    )
    packaged = installed / "computer_use/_resources"
    assert (packaged / DRIVER).read_bytes() == b"test native executable\n"
    if os.name != "nt":
        (packaged / DRIVER).chmod(0o644)  # Simulate an installer dropping executable bits.
    script = """
import json, os
from pathlib import Path
from computer_use import paths
Path.home = classmethod(lambda cls: Path('home').resolve())
root = paths.repository_root()
driver = Path(paths.native_driver('darwin', root)['command'])
assert driver.read_bytes() == b'test native executable\\n'
if os.name != 'nt':
    assert os.access(driver, os.X_OK)
manifest = json.loads((root / 'backend.json').read_text())
assert manifest['source_commit'] == 'test-commit'
assert manifest['files'][driver.relative_to(root).as_posix()]['mode'] == 0o755
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
