# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Launch the macOS bundle as the app responsible for desktop permissions."""

import asyncio
import os
import signal
import socket
import subprocess
from contextlib import ExitStack, suppress
from pathlib import Path
from tempfile import TemporaryDirectory, TemporaryFile

# Darwin <sys/un.h>. Python does not expose LOCAL_PEERPID on all versions.
_SOL_LOCAL, _LOCAL_PEERPID = 0, 2


def _launch(bundle):
    return ["/usr/bin/open", "-n", "-g", "-a", str(Path(bundle).resolve())]


def read_permissions(bundle, *, timeout):
    """Run the permission command under the same identity as the SDK driver."""
    from computer_use.json import loads

    with TemporaryDirectory(prefix="jiuwen-cua-permissions-") as directory:
        output = Path(directory) / "status.json"
        result = subprocess.run(
            [*_launch(bundle), "-W", "--stdout", str(output), "--args", "--check-permissions"],
            stdin=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
            check=False,
        )
        if result.returncode or not output.is_file():
            raise RuntimeError(result.stderr.strip() or f"Jiuwen CUA permission check failed ({result.returncode}).")
        status = loads(output.read_bytes())
        if not isinstance(status, dict):
            raise TypeError("Invalid Jiuwen CUA permission response")
        return status


def open_permission_guide(bundle, *, timeout):
    result = subprocess.run(
        [*_launch(bundle), "--args", "--permission-guide"],
        stdin=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout,
        check=False,
    )
    if result.returncode:
        detail = result.stderr.strip() or f"Jiuwen CUA permission guide failed to open ({result.returncode})."
        raise RuntimeError(detail)


def _pipe(read_owner, write_owner):
    read_fd, write_fd = os.pipe()
    return (
        read_owner.enter_context(os.fdopen(read_fd, "rb", buffering=0)),
        write_owner.enter_context(os.fdopen(write_fd, "wb", buffering=0)),
    )


async def _reader(loop, file, owner):
    reader = asyncio.StreamReader()
    transport, _ = await loop.connect_read_pipe(lambda: asyncio.StreamReaderProtocol(reader), file)
    owner.callback(transport.close)
    return reader


class MacAppProcess:
    """The driver's streams and PID, with Launch Services waiting for its exit."""

    def __init__(self, launcher, pid, stdin, stdout, stderr):
        self._launcher = launcher
        self.pid, self.stdin, self.stdout, self.stderr = pid, stdin, stdout, stderr

    @property
    def returncode(self):
        return self._launcher.returncode

    async def wait(self):
        return await self._launcher.wait()

    def kill(self):
        if self.returncode is None:
            os.kill(self.pid, signal.SIGKILL)


async def start_app(bundle, *, lifetime_fd):
    """Hand the existing protocol pipes to a fresh Launch Services app instance."""
    loop = asyncio.get_running_loop()
    # Darwin Unix socket paths are limited to 103 bytes. Use a private directory
    # on a short path independent of the installation and TMPDIR.
    with (
        TemporaryDirectory(prefix="jiuwen-cua-", dir="/tmp") as directory,
        TemporaryFile() as launch_log,
        socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener,
        ExitStack() as host,
        ExitStack() as app,
    ):
        endpoint = str(Path(directory) / "stdio")
        listener.bind(endpoint)
        listener.listen(1)
        listener.setblocking(False)
        input_read, input_write = _pipe(app, host)
        output_read, output_write = _pipe(host, app)
        error_read, error_write = _pipe(host, app)
        stdin, _ = await loop.connect_write_pipe(asyncio.Protocol, input_write)
        host.callback(stdin.close)
        stdout = await _reader(loop, output_read, host)
        stderr = await _reader(loop, error_read, host)
        launcher = await asyncio.create_subprocess_exec(
            *_launch(bundle),
            "-W", "--args", "--connect-stdio", endpoint,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=launch_log,
        )
        accepted = asyncio.create_task(loop.sock_accept(listener))
        exited = asyncio.create_task(launcher.wait())
        try:
            await asyncio.wait((accepted, exited), return_when=asyncio.FIRST_COMPLETED)
            if not accepted.done():
                launch_log.seek(0)
                detail = launch_log.read().decode(errors="replace").strip()
                raise RuntimeError(detail or "Jiuwen CUA exited before connecting to the SDK.")
            peer, _ = accepted.result()
            pid = peer.getsockopt(_SOL_LOCAL, _LOCAL_PEERPID)
            if pid <= 1:
                raise RuntimeError("Jiuwen CUA connected without a process identity.")
            files = [input_read.fileno(), output_write.fileno(), error_write.fileno()]
            if lifetime_fd >= 0:
                files.append(lifetime_fd)
            socket.send_fds(peer, [b"J"], files)
        except BaseException:
            # Closing the broker and the pipes also lets a late app exit.
            if launcher.returncode is None:
                with suppress(ProcessLookupError):
                    launcher.kill()
            await launcher.wait()
            raise
        finally:
            accepted.cancel()
            exited.cancel()
            connection, _ = await asyncio.gather(accepted, exited, return_exceptions=True)
            if isinstance(connection, tuple):
                connection[0].close()
        host.pop_all()  # The asyncio transports now own their end of each pipe.
        return MacAppProcess(launcher, pid, stdin, stdout, stderr)
