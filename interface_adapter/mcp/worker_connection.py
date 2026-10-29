# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Private worker channel shared by the MCP and Human runtimes."""

import asyncio
import os
import socket


async def worker_connection():
    port = os.environ.pop("COMPUTER_USE_WORKER_PORT", None)
    if port is not None:
        token = os.environ.pop("COMPUTER_USE_WORKER_TOKEN", "")
        if len(token) != 64 or any(char not in "0123456789abcdef" for char in token):
            raise RuntimeError("Invalid internal worker authentication.")
        reader, writer = await asyncio.open_connection("127.0.0.1", int(port), limit=12 * 1024 * 1024)
        try:
            writer.write((token + "\t" + str(os.getpid()) + "\n").encode("ascii"))
            await writer.drain()
        except BaseException:
            writer.close()
            raise
        return reader, writer
    ipc = socket.socket(fileno=3)
    try:
        ipc.setblocking(False)
        return await asyncio.open_connection(sock=ipc, limit=12 * 1024 * 1024)
    except BaseException:
        ipc.close()
        raise
