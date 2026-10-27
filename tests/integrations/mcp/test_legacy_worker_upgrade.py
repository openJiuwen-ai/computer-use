"""An already imported JSONL parent can keep using the new on-disk worker."""

import asyncio
import json
import os
import socket
import sys
import unittest

from computer_use_mcp.worker_protocol import PROTOCOL_ENV


@unittest.skipIf(os.name == "nt", "POSIX inherited-socket parent; codec tests cover both platforms")
class LegacyWorkerUpgradeTests(unittest.IsolatedAsyncioTestCase):
    async def test_old_jsonl_parent_runs_two_calls_on_current_worker(self):
        parent, child_socket = socket.socketpair()
        parent.setblocking(False)
        env = {**os.environ, "COMPUTER_USE_EXPERIENCE": "0"}
        # Old parents did not declare a version. Do not use the new parent,
        # codec reader or writer to exercise this compatibility direction.
        for name in (PROTOCOL_ENV, "COMPUTER_USE_WORKER_PORT", "COMPUTER_USE_WORKER_TOKEN"):
            env.pop(name, None)
        child = None
        writer = None
        try:
            child = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "computer_use.native.driver_exec",
                str(child_socket.fileno()),
                "-1",
                sys.executable,
                "-m",
                "computer_use_mcp.runtime",
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                pass_fds=(child_socket.fileno(),),
                env=env,
            )
            child_socket.close()
            reader, writer = await asyncio.open_connection(sock=parent, limit=12 * 1024 * 1024)

            async def execute(code, execution_id):
                request = {
                    "code": code,
                    "executionId": execution_id,
                    "history": {"incremental": False, "resetText": True, "resetImages": False},
                }
                writer.write((json.dumps(request) + "\n").encode("utf-8"))
                await writer.drain()
                traces = []
                async with asyncio.timeout(15):
                    while line := await reader.readline():
                        message = json.loads(line)
                        if "diagnostic" in message:
                            traces.append(message["diagnostic"])
                        if "content" in message:
                            return message, traces
                self.fail("Current worker closed before returning a legacy JSONL result")

            first, _ = await execute("answer = 40\nprint(answer)", 1)
            self.assertNotIn("isError", first)
            self.assertEqual(first["content"], [{"type": "text", "text": "40"}])
            second, traces = await execute(
                "from computer_use.diagnostics import diagnostic\n"
                'diagnostic(dict(kind="pipeline", phase="enter", path=["legacy"]))\n'
                "answer += 2\nprint(answer)",
                2,
            )
            self.assertNotIn("isError", second)
            self.assertEqual(second["content"], [{"type": "text", "text": "42"}])
            self.assertTrue(any(trace.get("kind") == "pipeline" for trace in traces))
        finally:
            child_socket.close()
            if writer is not None:
                writer.close()
                await writer.wait_closed()
            else:
                parent.close()
            if child is not None:
                try:
                    await asyncio.wait_for(child.wait(), 10)
                except TimeoutError:
                    child.kill()
                    await child.wait()
