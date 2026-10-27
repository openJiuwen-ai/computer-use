import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from computer_use_mcp.session import ExecutionSession
from computer_use_mcp.windows_worker import startWindowsWorker
from computer_use_mcp.worker_protocol import FRAMED_PROTOCOL, PROTOCOL_ENV

"Exercise real worker framing/reset portably, and real Job lifetimes on Windows."


class _PortableJob:
    def assign(self, pid):
        self.pid = pid

    def contains(self, pid):
        return True

    async def waitEmpty(self):
        pass

    def close(self):
        pass


class WindowsWorkerTests(unittest.IsolatedAsyncioTestCase):
    async def test_authenticated_channel_persists_and_reset_stops_busy_code(self):
        with (
            tempfile.TemporaryDirectory() as folder,
            patch.dict(
                os.environ,
                {
                    "COMPUTER_USE_LOG_DIR": folder + "/logs",
                },
            ),
            patch("computer_use_mcp.windows_worker.WindowsJob", _PortableJob),
        ):
            child, reader, writer, job, listener = await startWindowsWorker(
                {**os.environ, PROTOCOL_ENV: FRAMED_PROTOCOL}
            )
            session = ExecutionSession(5000)
            session._experience = False
            session._child, session._reader, session._writer = child, reader, writer

            async def exited():
                try:
                    await child.wait()
                    await job.waitEmpty()
                finally:
                    job.close()
                    await listener.wait_closed()

            session._exited = session._track(exited())
            session._track(session._receive(child, reader))
            try:
                first = await session.execute("value=123+456\nprint(value)")
                second = await session.execute("print(value+1)")
                self.assertFalse(first.get("isError"), first)
                self.assertEqual(first["content"][0]["text"], "579")
                self.assertEqual(second["content"][0]["text"], "580")
                isolated = await session.execute(
                    "import os,sys\nprint('COMPUTER_USE_WORKER_TOKEN' in os.environ)\nprint(sys.stdin.read())",
                )
                self.assertFalse(isolated.get("isError"), isolated)
                self.assertEqual(isolated["content"][0]["text"].strip(), "False")
                running = session.execute("print('before loop')\nwhile True: pass")
                for _ in range(100):
                    await asyncio.sleep(0.01)
                    if any(
                        '"text":"before loop"' in f.read_text(encoding="utf-8") for f in Path(folder).rglob("*.jsonl")
                    ):
                        break
                reset = await session.reset()
                result = await running
                self.assertFalse(reset.get("isError"), reset)
                self.assertTrue(result.get("isError"), result)
                self.assertTrue(any("before loop" in b.get("text", "") for b in result["content"]))
                self.assertIsNotNone(child.returncode)
            finally:
                await session.close()

    @unittest.skipUnless(os.name == "nt", "Requires Windows Job Objects")
    async def test_job_tracks_and_waits_for_worker_descendants(self):
        with (
            tempfile.TemporaryDirectory() as folder,
            patch.dict(
                os.environ,
                {
                    "COMPUTER_USE_LOG_DIR": folder + "/logs",
                },
            ),
        ):
            session = ExecutionSession(5000)
            try:
                child_log = str(Path(folder) / "child.log")
                code = (
                    "import subprocess,sys\nchildLog=open("
                    + repr(child_log)
                    + (
                        ", 'w')\nchild=subprocess.Popen([sys.executable,'-c','import "
                        "time;time.sleep(2)'],stdout=childLog,stderr=childLog)\nprint(child.pid)\nprint"
                        "(child.poll())"
                    )
                )
                result = await session.execute(code)
                self.assertFalse(result.get("isError"), result)
                started = asyncio.get_running_loop().time()
                reset = await session.reset()
                self.assertFalse(reset.get("isError"), reset)
                self.assertGreater(
                    asyncio.get_running_loop().time() - started,
                    0.3,
                    (result, Path(child_log).read_text(encoding="utf-8")),
                )
                fresh = await session.execute("print('fresh')")
                self.assertEqual(fresh["content"][0]["text"], "fresh")
            finally:
                await session.close()


if __name__ == "__main__":
    unittest.main()
