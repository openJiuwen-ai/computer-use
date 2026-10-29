# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""A local display of received screenshots; it never captures or sends input."""

import asyncio
import hashlib
import json
import platform
import plistlib
import sys
from importlib.resources import as_file, files
from pathlib import Path


async def run(command, *args):
    process = await asyncio.create_subprocess_exec(
        command,
        *map(str, args),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        async with asyncio.timeout(120):
            stdout, stderr = await process.communicate()
        if process.returncode:
            raise RuntimeError(stderr.decode("utf-8", "replace"))
        return stdout
    finally:
        if process.returncode is None:
            process.kill()
        await process.wait()


async def build():
    if sys.platform != "darwin":
        raise RuntimeError("The local preview requires macOS.")
    with as_file(files("computer_use_cli").joinpath("preview")) as source:
        sources = [source / "main.swift", source / "PreviewController.swift"]
        digest = hashlib.sha256(platform.machine().encode())
        for file in sources:
            digest.update(file.read_bytes())
        root = Path.home() / ".computer-use" / "runtime" / "preview" / digest.hexdigest()[:16]
        bundle = root / "ComputerUsePreview.app"
        binary = bundle / "Contents" / "MacOS" / "computer-use-preview"
        if binary.exists():
            return binary
        binary.parent.mkdir(parents=True, exist_ok=True)
        await run("/usr/bin/xcrun", "swiftc", "-swift-version", "6", *sources, "-o", binary)
        (bundle / "Contents" / "Info.plist").write_bytes(
            plistlib.dumps(
                {
                    "CFBundleIdentifier": "local.computer-use.preview",
                    "CFBundleName": "Computer Use Preview",
                    "CFBundleExecutable": binary.name,
                    "CFBundlePackageType": "APPL",
                    "CFBundleVersion": "1",
                    "LSUIElement": True,
                }
            )
        )
        await run("/usr/bin/codesign", "--force", "--sign", "-", bundle)
        return binary


class Preview:
    def __init__(self, process):
        self.process = process
        self.pending = None
        self.wake = asyncio.Event()
        self.closed = False
        self.writer = asyncio.create_task(self._write())

    @classmethod
    async def start(cls):
        binary = await build()
        process = await asyncio.create_subprocess_exec(
            binary,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            async with asyncio.timeout(10):
                if (await process.stdout.readline()).strip() != b"ready":
                    raise RuntimeError("The local preview did not start.")
        except BaseException:
            if process.returncode is None:
                process.kill()
            await process.wait()
            raise
        return cls(process)

    def event(self, kind, data):
        if kind != "tool_result" or self.closed or self.process.returncode is not None:
            return
        if data["name"] == "computer_use_reset":
            self.pending = {"kind": "reset"}
        else:
            images = [block for block in data["result"].get("content", []) if block.get("type") == "image"]
            if not images:
                return
            self.pending = {"kind": "frame", "name": "Computer Use", "png": images[-1]["data"]}
        self.wake.set()

    async def _write(self):
        try:
            while not self.closed:
                await self.wake.wait()
                self.wake.clear()
                value, self.pending = self.pending, None
                if value is not None:
                    self.process.stdin.write((json.dumps(value) + "\n").encode())
                    await self.process.stdin.drain()
        except ConnectionError:
            return

    async def close(self):
        self.closed = True
        self.writer.cancel()
        await asyncio.gather(self.writer, return_exceptions=True)
        self.process.stdin.close()
        try:
            async with asyncio.timeout(2):
                await self.process.wait()
        except TimeoutError:
            if self.process.returncode is None:
                self.process.kill()
            await self.process.wait()
