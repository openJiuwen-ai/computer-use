# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Real subprocess faults behind the production Session and stdio tool entry."""

import asyncio
import importlib.abc
import os
import subprocess
import sys
from pathlib import Path


MISSING_MODULE = "computer_use.interaction.read.presentation.interface.readable.pipeline"


def redirect_worker(original):
    async def spawn(*args, **kwargs):
        if args[-2:] == ("-m", "computer_use_mcp.runtime"):
            args = (*args[:-2], str(Path(__file__).resolve()), "--worker")
        return await original(*args, **kwargs)

    return spawn


async def worker():
    mode = os.environ["MCP_FIXTURE_FAULT"]
    if mode in ("import", "flood"):
        if mode == "flood":
            for _ in range(64):
                os.write(2, b"x" * 65536)
            os.write(2, b"\n")

        class MissingModule(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path=None, target=None):
                if fullname == MISSING_MODULE:
                    raise ModuleNotFoundError(f"No module named '{fullname}'", name=fullname)

        sys.meta_path.insert(0, MissingModule())
        # A deep import failure, before the runtime can open its IPC channel.
        import computer_use_mcp.runtime  # noqa: F401

    from computer_use_mcp.worker_transport import worker_connection
    from computer_use_mcp.worker_transport import read_message, write_message

    reader, writer = await worker_connection()
    if mode == "startup-wait":
        os.write(2, b"RuntimeError: startup-private-value\n")
        await asyncio.sleep(30)
    if os.environ.pop("COMPUTER_USE_WORKER_READY", None) == "1":
        write_message(writer, dict(ready=True))
        await writer.drain()
    if mode == "ready-eof":
        writer.close()
        await writer.wait_closed()
        await asyncio.sleep(0.05)
        os._exit(37)
    message = await read_message(reader)
    if message is None:
        return
    with Path(os.environ["MCP_FIXTURE_DISPATCHES"]).open("a", encoding="utf-8") as file:
        file.write("received\n")
    if mode == "exit":
        write_message(writer, dict(executionId=message["executionId"], output=dict(type="text", text="before exit")))
        await writer.drain()
        os.write(2, b"Traceback (most recent call last):\nRuntimeError: fixture-private-value\n")
        os._exit(23)
    if mode in ("eof", "eof-alive"):
        writer.close()
        await writer.wait_closed()
        await asyncio.sleep(30 if mode == "eof-alive" else 0.05)
        os.write(2, b"RuntimeError: fixture-private-value\n")
        os._exit(29)
    if mode == "runtime-flood":
        while True:
            os.write(2, b"private-input-value " * 1024)
    if mode == "runtime-wait":
        write_message(writer, dict(executionId=message["executionId"], output=dict(type="text", text="before cancel")))
        await writer.drain()
        await asyncio.sleep(30)
    if mode in ("held-stderr", "held-lifetime"):
        # Only the named inherited handle is retained. This distinguishes
        # diagnostic pipe shutdown from the actual native lifetime barrier.
        retained = (2,) if mode == "held-stderr" else (4,)
        subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(0.6)"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=None if mode == "held-stderr" else subprocess.DEVNULL,
            pass_fds=retained,
        )
        os.write(2, b"ValueError: fixture-private-value\n")
        os._exit(31)
    raise AssertionError(mode)


if __name__ == "__main__":
    if sys.argv[-1] == "--host":
        asyncio.create_subprocess_exec = redirect_worker(asyncio.create_subprocess_exec)
        from computer_use_mcp.server import cli

        cli([])
    else:
        asyncio.run(worker())
