# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import asyncio
import json
import os
import sys
from contextlib import suppress
from pathlib import Path

import pytest
from computer_use.native import macos_app
from computer_use.paths import native_driver
from computer_use.protocol.session import NATIVE_PROTOCOL_VERSION


@pytest.mark.skipif(os.name != "posix", reason="The macOS launcher uses Unix sockets and pipes.")
def test_macos_launcher_failure_preserves_diagnostics(monkeypatch, tmp_path):
    command = "import sys; print('could not open test app', file=sys.stderr); sys.exit(1)"
    monkeypatch.setattr(macos_app, "_launch", lambda bundle: [sys.executable, "-c", command])

    async def run():
        async with asyncio.timeout(5):
            with pytest.raises(RuntimeError, match="could not open test app"):
                await macos_app.start_app(tmp_path / "Driver.app", lifetime_fd=-1)

    asyncio.run(run())


@pytest.mark.skipif(os.name != "posix", reason="The macOS launcher uses Unix sockets and pipes.")
def test_cancelling_macos_launch_reaps_launcher(monkeypatch, tmp_path):
    monkeypatch.setattr(macos_app, "_launch", lambda bundle: [sys.executable, "-c", "import time; time.sleep(30)"])
    create_subprocess = asyncio.create_subprocess_exec

    async def run():
        started = asyncio.get_running_loop().create_future()

        async def track_child(*args, **kwargs):
            child = await create_subprocess(*args, **kwargs)
            started.set_result((child, Path(args[-1]).parent))
            return child

        monkeypatch.setattr(asyncio, "create_subprocess_exec", track_child)
        async with asyncio.timeout(5):
            launch = asyncio.create_task(macos_app.start_app(tmp_path / "Driver.app", lifetime_fd=-1))
            child, directory = await started
            try:
                launch.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await launch
                assert child.returncode is not None
                assert not directory.exists()
            finally:
                if child.returncode is None:
                    with suppress(ProcessLookupError):
                        child.kill()
                await child.wait()

    asyncio.run(run())


@pytest.mark.skipif(sys.platform != "darwin", reason="Requires Launch Services and a built macOS driver.")
@pytest.mark.parametrize("force_kill", [False, True], ids=["stdin-eof", "kill-app"])
def test_macos_launch_services_transfers_stdio_and_lifetime(force_kill):
    root = Path(__file__).resolve().parents[2]
    executable = Path(native_driver(root=root)["command"])
    if not executable.is_file():
        pytest.skip("Run computer-use-build before testing the native Launch Services connection.")

    async def run():
        read_fd, write_fd = os.pipe()
        with os.fdopen(read_fd, "rb", buffering=0) as lifetime, os.fdopen(write_fd, "wb", buffering=0) as writer:
            os.set_blocking(lifetime.fileno(), False)
            async with asyncio.timeout(10):
                child = await macos_app.start_app(executable.parents[2], lifetime_fd=writer.fileno())
                writer.close()
                try:
                    hello = {
                        "version": NATIVE_PROTOCOL_VERSION,
                        "id": 0,
                        "method": "hello",
                        "params": {"globalInputAllowed": False},
                    }
                    child.stdin.write(json.dumps(hello).encode() + b"\n")
                    assert json.loads(await child.stdout.readline())["id"] == 0
                    diagnostic = json.loads(await child.stderr.readline())
                    assert diagnostic["kind"] == "backend"
                    assert Path(diagnostic["executable"]).resolve() == executable.resolve()
                    with pytest.raises(BlockingIOError):
                        os.read(lifetime.fileno(), 1)
                    if force_kill:
                        child.kill()
                    else:
                        child.stdin.close()
                    await child.wait()
                    assert os.read(lifetime.fileno(), 1) == b""
                finally:
                    child.stdin.close()
                    with suppress(ProcessLookupError):
                        child.kill()

    asyncio.run(run())
