# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Launch the macOS bundle as the app responsible for desktop permissions."""

import asyncio
import os
import signal
import socket
import subprocess
from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory

# Darwin <sys/un.h>. Python does not expose LOCAL_PEERPID on all versions.
_SOL_LOCAL, _LOCAL_PEERPID = 0, 2


def _launch(bundle):
    return ["/usr/bin/open", "-n", "-g", "-W", "-a", str(Path(bundle).resolve())]


def read_permissions(bundle, *, request, timeout):
    """Run the permission command under the same identity as the SDK driver."""
    flag = "--request-permissions" if request else "--check-permissions"
    with TemporaryDirectory(prefix="jiuwen-cua-permissions-") as directory:
        output = Path(directory) / "status.json"
        result = subprocess.run(
            [*_launch(bundle), "--stdout", str(output), "--args", flag],
            stdin=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
            check=False,
        )
        if result.returncode or not output.is_file():
            raise RuntimeError(result.stderr.strip() or f"Jiuwen CUA permission check failed ({result.returncode}).")
        return output.read_text(encoding="utf-8")


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
    pid, launcher = None, None
    # Darwin Unix socket paths are limited to 103 bytes. Use a private directory
    # on a short path independent of the installation and TMPDIR.
    with (
        TemporaryDirectory(prefix="jiuwen-cua-", dir="/tmp") as directory,
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
        try:
            launcher = await asyncio.create_subprocess_exec(
                *_launch(bundle),
                "--args", "--connect-stdio", endpoint,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            accepted = asyncio.create_task(loop.sock_accept(listener))
            exited = asyncio.create_task(launcher.wait())
            try:
                await asyncio.wait((accepted, exited), return_when=asyncio.FIRST_COMPLETED)
                if not accepted.done():
                    detail = (await launcher.stderr.read()).decode(errors="replace").strip()
                    raise RuntimeError(detail or "Jiuwen CUA exited before connecting to the SDK.")
                peer, _ = accepted.result()
                peer_pid = peer.getsockopt(_SOL_LOCAL, _LOCAL_PEERPID)
                if peer_pid <= 1:
                    raise RuntimeError("Jiuwen CUA connected without a process identity.")
                pid = peer_pid
                files = [input_read.fileno(), output_write.fileno(), error_write.fileno()]
                if lifetime_fd >= 0:
                    files.append(lifetime_fd)
                socket.send_fds(peer, [b"J"], files)
            finally:
                exited.cancel()
                if not accepted.cancel() and not accepted.cancelled() and accepted.exception() is None:
                    accepted.result()[0].close()
        except BaseException:
            # Closing the broker and the pipes also lets a late app exit.
            if launcher is not None and launcher.returncode is None:
                try:
                    launcher.kill()
                except ProcessLookupError:
                    pass
            raise
        host.pop_all()  # The asyncio transports now own their end of each pipe.
        return MacAppProcess(launcher, pid, stdin, stdout, stderr)
