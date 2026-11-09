# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Startup evidence records content, not later files or per-operation probes."""

import asyncio
import hashlib
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from computer_use.native import driver_exec
from computer_use.native.connection import Connection
from computer_use.runtime_provenance import startup_provenance

DRIVER_NAME = "driver.exe" if os.name == "nt" else "driver"


def checkout(tmp_path):
    root = tmp_path / "source"
    package = root / "computer_use"
    package.mkdir(parents=True)
    module = package / "__init__.py"
    module.write_text("VERSION = 1\n")
    for args in (
        ["init", "-q"],
        ["add", "."],
        ["-c", "user.name=Provenance Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "initial"],
    ):
        subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)
    driver = tmp_path / DRIVER_NAME
    driver.write_bytes(b"driver version one\n")
    driver.chmod(0o700)
    return module, driver


def test_startup_fingerprints_dirty_contents_even_when_size_and_mtime_stay_the_same(tmp_path):
    module, driver = checkout(tmp_path)
    run = subprocess.run

    def run_git(command, **options):
        return run(command, **(options | {"timeout": 10}))

    # Keep real Git and file reads while isolating this hash test from startup timing.
    git = SimpleNamespace(
        run=run_git, PIPE=subprocess.PIPE, DEVNULL=subprocess.DEVNULL, SubprocessError=subprocess.SubprocessError,
    )
    with (
        patch.object(sys.modules["computer_use"], "__file__", str(module)),
        patch("computer_use.runtime_provenance.time", SimpleNamespace(monotonic=lambda: 0.0)),
        patch("computer_use.runtime_provenance.subprocess", git),
    ):
        clean = startup_provenance(str(driver))
        assert clean["sdkModulePath"] == str(module)
        assert clean["source"]["status"] == "complete" and clean["source"]["dirty"] is False
        module.write_text("VERSION = 2\n")
        first = startup_provenance(str(driver))
        stamp = module.stat()
        module.write_text("VERSION = 3\n")
        os.utime(module, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
        second = startup_provenance(str(driver))
        assert first["source"]["head"] == second["source"]["head"] == clean["source"]["head"]
        assert first["source"]["dirty"] and second["source"]["dirty"]
        assert first["source"]["dirtyContentSha256"] != second["source"]["dirtyContentSha256"]
        untracked = module.parent / "new_module.py"
        untracked.write_text("new source\n")
        assert startup_provenance(str(driver))["source"]["dirtyContentSha256"] != second["source"]["dirtyContentSha256"]


def test_driver_hash_is_a_startup_snapshot_and_missing_git_does_not_block_it(tmp_path):
    module = tmp_path / "computer_use" / "__init__.py"
    module.parent.mkdir()
    module.write_text("# installed package without Git metadata\n")
    driver = tmp_path / DRIVER_NAME
    original = b"original executable\n"
    driver.write_bytes(original)
    driver.chmod(0o700)
    with patch.object(sys.modules["computer_use"], "__file__", str(module)):
        started = startup_provenance(str(driver))
        driver.write_bytes(b"subsequent executable\n")
        later = startup_provenance(str(driver))
    assert started["source"]["status"] == "unavailable"
    assert started["driver"]["captureStage"] == "beforeSpawn"
    assert started["driver"]["sha256"] == hashlib.sha256(original).hexdigest()
    assert started["driver"]["sha256"] != later["driver"]["sha256"]
    assert started["driver"]["path"] == str(driver.resolve())


def test_installed_sdk_in_ignored_venv_cannot_claim_its_enclosing_checkout_head(tmp_path):
    module, driver = checkout(tmp_path)
    root = module.parent.parent
    (root / ".gitignore").write_text(".venv/\n")
    installed = root / ".venv" / "computer_use" / "__init__.py"
    installed.parent.mkdir(parents=True)
    installed.write_text("# different installed SDK\n")
    with patch.object(sys.modules["computer_use"], "__file__", str(installed)):
        evidence = startup_provenance(str(driver))
    assert evidence["sdkModulePath"] == str(installed)
    assert evidence["source"]["status"] == "unavailable"
    assert evidence["source"]["head"] is None


def test_connection_collects_startup_evidence_once_per_spawn_not_per_request(tmp_path):
    async def run():
        driver = Path(__file__).resolve().parents[1] / "fixtures/observation_session_driver.py"
        connection = Connection(
            lambda: None, driver={"command": sys.executable, "args": [str(driver), str(tmp_path / "spawns")]}
        )
        events = []
        original_spawn = asyncio.create_subprocess_exec
        with (
            patch("computer_use.native.connection.startup_provenance", return_value={"startup": "frozen"}) as collect,
            patch("computer_use.native.connection.diagnostic", events.append),
            patch("computer_use.native.connection.asyncio.create_subprocess_exec", wraps=original_spawn) as spawn,
        ):
            try:
                await connection.connect(3000)
                await connection.connect(3000)
                arguments = spawn.await_args.args
                assert arguments[0] == sys.executable
                assert Path(arguments[1]).resolve() == (
                    driver if os.name == "nt" else Path(driver_exec.__file__).resolve()
                )
                collect.assert_called_once_with(sys.executable)
                assert next(event for event in events if event["kind"] == "connection")["runtime"] == {
                    "startup": "frozen"
                }
                connection.close()
                await connection.wait_closed()
                await asyncio.gather(*tuple(connection._readers), return_exceptions=True)
                await connection.connect(3000)
                assert collect.call_count == 2
            finally:
                connection.close()
                await connection.wait_closed()
                await asyncio.gather(*tuple(connection._readers), return_exceptions=True)

    asyncio.run(run())


@pytest.mark.skipif(os.name == "nt", reason="Windows launches directly without inherited POSIX FDs")
def test_packaged_file_shim_keeps_fd_contract_without_importing_sdk_package(tmp_path):
    package = tmp_path / "installed package" / "computer_use"
    native = package / "native"
    native.mkdir(parents=True)
    for initializer in (package / "__init__.py", native / "__init__.py"):
        initializer.write_text("raise AssertionError('The FD shim must not initialize the SDK package')\n")
    shim = native / "driver_exec.py"
    shim.write_bytes(Path(driver_exec.__file__).read_bytes())
    preview_read, preview_write = os.pipe()
    lifetime_read, lifetime_write = os.pipe()
    try:
        subprocess.run(
            [sys.executable, "-I", str(shim), str(preview_write), str(lifetime_write), sys.executable, "-I", "-c",
             "import os; os.write(3, b'preview'); os.write(4, b'lifetime')"],
            check=True, capture_output=True, pass_fds=(preview_write, lifetime_write),
        )
        assert os.read(preview_read, 7) == b"preview"
        assert os.read(lifetime_read, 8) == b"lifetime"
    finally:
        for descriptor in (preview_read, preview_write, lifetime_read, lifetime_write):
            os.close(descriptor)
