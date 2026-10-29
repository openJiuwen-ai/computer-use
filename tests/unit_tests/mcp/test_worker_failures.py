# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import asyncio
import json
import os
import sys
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from computer_use_mcp.session import ExecutionSession
from computer_use_mcp.worker_lifecycle import STDERR_BYTES

ROOT = Path(__file__).resolve().parents[3]
FIXTURE = ROOT / "tests/fixtures/mcp_worker_errors.py"
MISSING_MODULE = "computer_use.interaction.read.presentation.interface.readable.pipeline"


def text(result):
    return "\n".join(block["text"] for block in result["content"] if block["type"] == "text")


@unittest.skipIf(os.name == "nt", "Inherited-FD fixture; Windows launcher has portable tests")
class WorkerFailureTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mcp-worker-errors-")
        self.directory = Path(self.temp.name)
        self.env = {
            "COMPUTER_USE_LOG_DIR": str(self.directory / "logs"),
            "COMPUTER_USE_PACKAGES_DIR": str(self.directory / "packages"),
            "MCP_FIXTURE_DISPATCHES": str(self.directory / "dispatches"),
        }
        self.environment = patch.dict(os.environ, self.env)
        self.environment.start()
        self.session = ExecutionSession(3000)
        self.children = []
        original = asyncio.create_subprocess_exec

        async def spawn(*args, **kwargs):
            if args[-2:] == ("-m", "computer_use_mcp.runtime") and os.environ.get("MCP_FIXTURE_FAULT"):
                args = (*args[:-2], str(FIXTURE), "--worker")
            child = await original(*args, **kwargs)
            self.children.append(child)
            return child

        self.spawning = patch("computer_use_mcp.session.asyncio.create_subprocess_exec", spawn)
        self.spawning.start()

    async def until(self, condition):
        async with asyncio.timeout(3):
            while not condition():
                await asyncio.sleep(0.005)

    async def asyncTearDown(self):
        await self.session.close()
        self.spawning.stop()
        self.environment.stop()
        self.temp.cleanup()

    def events(self):
        return [json.loads(line) for line in self.session._journal.path.read_text().splitlines()]

    async def test_public_stdio_tool_reports_deep_import_failure_without_executing_code(self):
        params = StdioServerParameters(
            command=sys.executable,
            args=[str(FIXTURE), "--host"],
            cwd=ROOT,
            env={**os.environ, **self.env, "MCP_FIXTURE_FAULT": "import"},
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=10)) as client:
                await client.initialize()
                result = await client.call_tool("computer_use", dict(code="raise AssertionError('must not run')"))
                self.assertTrue(result.isError)
                message = "\n".join(block.text for block in result.content if block.type == "text")
                self.assertIn("startup", message.lower())
                self.assertIn("ModuleNotFoundError", message)
                self.assertIn(MISSING_MODULE, message)
                self.assertIn("code was not sent", message)
                self.assertNotIn("exited (None)", message)
                self.assertNotIn("Traceback", message)
                self.assertFalse(Path(self.env["MCP_FIXTURE_DISPATCHES"]).exists())

    async def test_runtime_exit_keeps_output_exit_status_and_no_replay(self):
        os.environ["MCP_FIXTURE_FAULT"] = "exit"
        result = await asyncio.wait_for(self.session.execute("pass"), 8)
        self.assertTrue(result["isError"])
        message = text(result)
        self.assertIn("before exit", message)
        self.assertIn("23", message)
        self.assertIn("RuntimeError", message)
        self.assertIn("may already have executed", message)
        self.assertNotIn("fixture-private-value", message)
        self.assertEqual(Path(self.env["MCP_FIXTURE_DISPATCHES"]).read_text(), "received\n")
        self.assertIsNotNone(self.children[-1].returncode)
        event = next(event for event in self.events() if event["kind"] == "worker_exit")
        self.assertEqual(event["returncode"], 23)
        self.assertEqual(event["stderrSummary"], "RuntimeError")
        self.assertNotIn("fixture-private-value", json.dumps(self.events()))

    async def test_startup_stderr_flood_is_bounded_and_does_not_block_exit(self):
        os.environ["MCP_FIXTURE_FAULT"] = "flood"
        pending = self.session.execute("pass")
        await self.until(lambda: self.session._worker is not None)
        worker = self.session._worker
        result = await asyncio.wait_for(pending, 5)
        self.assertTrue(result["isError"])
        self.assertIn(MISSING_MODULE, text(result))
        self.assertLess(len(text(result)), 1000)
        self.assertLessEqual(len(worker._tail), STDERR_BYTES)
        event = next(event for event in self.events() if event["kind"] == "worker_exit")
        self.assertGreater(event["stderrBytes"], 4 * 1024 * 1024)
        self.assertTrue(event["stderrTruncated"])
        self.assertTrue(event["stderrComplete"])
        self.assertLess(len(json.dumps(event)), 10000)
        self.assertFalse(Path(self.env["MCP_FIXTURE_DISPATCHES"]).exists())

    async def test_early_channel_eof_waits_for_actual_exit_status(self):
        os.environ["MCP_FIXTURE_FAULT"] = "eof"
        result = await asyncio.wait_for(self.session.execute("pass"), 5)
        self.assertIn("status 29", text(result))
        self.assertIn("RuntimeError", text(result))
        self.assertNotIn("None", text(result))
        self.assertEqual(self.children[-1].returncode, 29)
        self.assertEqual(Path(self.env["MCP_FIXTURE_DISPATCHES"]).read_text(), "received\n")

    async def test_eof_from_live_process_is_bounded_and_reports_unconfirmed_exit(self):
        os.environ["MCP_FIXTURE_FAULT"] = "eof-alive"
        result = await asyncio.wait_for(self.session.execute("pass"), 2)
        self.assertTrue(result["isError"])
        self.assertIn("exit is not yet confirmed", text(result))
        self.assertIn("may already have executed", text(result))
        self.assertLess(self.children[-1].returncode, 0)
        event = next(event for event in self.events() if event["kind"] == "worker_exit")
        self.assertEqual(event["returncode"], self.children[-1].returncode)

    async def test_ready_followed_by_eof_never_dispatches_this_call(self):
        os.environ["MCP_FIXTURE_FAULT"] = "ready-eof"
        result = await self.session.execute("pass")
        self.assertTrue(result["isError"])
        self.assertIn("code was not sent", text(result))
        self.assertFalse(any(event["kind"] == "execution_start" for event in self.events()))
        self.assertFalse(Path(self.env["MCP_FIXTURE_DISPATCHES"]).exists())

    async def test_startup_readiness_deadline_reaps_worker_without_dispatch(self):
        os.environ["MCP_FIXTURE_FAULT"] = "startup-wait"
        self.session.timeout_ms = 300
        result = await asyncio.wait_for(self.session.execute("pass"), 2)
        self.assertIn("Timed out waiting for worker readiness", text(result))
        self.assertIn("code was not sent", text(result))
        self.assertFalse(Path(self.env["MCP_FIXTURE_DISPATCHES"]).exists())
        self.assertIsNone(self.session._child)
        self.assertIsNotNone(self.children[-1].returncode)
        self.assertFalse(any(event["kind"] == "execution_start" for event in self.events()))

    async def test_spawn_failure_is_safe_and_does_not_claim_a_started_process(self):
        with patch("computer_use_mcp.session.asyncio.create_subprocess_exec", side_effect=OSError(28, "private-value")):
            result = await self.session.execute("pass")
        self.assertTrue(result["isError"])
        self.assertIn("startup failed", text(result))
        self.assertIn("process was not started", text(result))
        self.assertIn("OSError", text(result))
        self.assertNotIn("private-value", json.dumps(self.events()))
        self.assertEqual(self.children, [])
        self.assertIsNone(self.session._worker)

    async def test_reset_during_real_spawn_yield_retires_late_worker(self):
        entered, release = asyncio.Event(), asyncio.Event()
        original = asyncio.create_subprocess_exec

        async def delayed(*args, **kwargs):
            entered.set()
            await release.wait()
            return await original(*args, **kwargs)

        with patch("computer_use_mcp.session.asyncio.create_subprocess_exec", delayed):
            pending = self.session.execute("pass")
            await entered.wait()
            reset = self.session.reset()
            release.set()
            result, reset_result = await asyncio.gather(pending, reset)
        self.assertIn("before code was sent", text(result))
        self.assertFalse(reset_result.get("isError"), reset_result)
        self.assertIsNone(self.session._worker)
        self.assertIsNotNone(self.children[-1].returncode)
        self.assertFalse(any(event["kind"] == "execution_start" for event in self.events()))
        self.assertEqual(text(await self.session.execute("print('fresh')")), "fresh")

    async def test_runtime_stderr_flood_does_not_block_watchdog_or_new_worker(self):
        os.environ["MCP_FIXTURE_FAULT"] = "runtime-flood"
        pending = self.session.execute("pass")
        await self.until(lambda: self.session._pending is not None)
        worker = self.session._worker
        self.session.timeout_ms = 100
        self.session._pending["progress"]()
        result = await asyncio.wait_for(pending, 2)
        self.assertIn("without native command progress", text(result))
        self.assertLessEqual(len(worker._tail), STDERR_BYTES)
        self.assertNotIn("private-input-value", json.dumps(self.events()))
        self.assertEqual(Path(self.env["MCP_FIXTURE_DISPATCHES"]).read_text(), "received\n")
        os.environ.pop("MCP_FIXTURE_FAULT")
        self.session.timeout_ms = 3000
        fresh = await self.session.execute("print('fresh')")
        self.assertEqual(text(fresh), "fresh")
        self.assertEqual(Path(self.env["MCP_FIXTURE_DISPATCHES"]).read_text(), "received\n")

    async def test_startup_signal_cancel_and_new_epoch_are_isolated(self):
        os.environ["MCP_FIXTURE_FAULT"] = "startup-wait"
        cancel = asyncio.Event()
        pending = self.session.execute("pass", signal=cancel)
        await self.until(lambda: self.session._worker is not None)
        old = self.session._worker
        await self.until(lambda: old._bytes > 0)
        cancel.set()
        result = await asyncio.wait_for(pending, 2)
        self.assertTrue(result["isError"])
        self.assertIn("before code was sent", text(result))
        self.assertFalse(Path(self.env["MCP_FIXTURE_DISPATCHES"]).exists())
        self.assertIsNotNone(self.children[-1].returncode)
        os.environ.pop("MCP_FIXTURE_FAULT")
        fresh = await self.session.execute("print('new worker')")
        self.assertEqual(text(fresh), "new worker")
        new = self.session._worker
        self.assertIsNot(new, old)
        self.assertNotEqual(new.generation, old.generation)
        active = self.session.execute("import asyncio\nawait asyncio.sleep(0.05)\nprint('current')")
        await self.until(lambda: self.session._pending is not None)
        execution_id = self.session._pending["id"]
        stale = asyncio.StreamReader()
        for message in (
            dict(ready=True),
            dict(execution_id=execution_id, output=dict(type="text", text="stale output")),
            dict(execution_id=execution_id, diagnostic=dict(kind="stale_epoch")),
        ):
            payload = json.dumps(message).encode()
            stale.feed_data(len(payload).to_bytes(4, "big") + payload)
        stale.feed_eof()
        await self.session._receive(self.children[0], stale, old)
        self.assertEqual(text(await active), "current")
        self.assertFalse(any(event["kind"] == "stale_epoch" for event in self.events()))
        await self.session.close()
        exits = [event for event in self.events() if event["kind"] == "worker_exit"]
        self.assertEqual(len(exits), 2)
        self.assertEqual(exits[0]["workerGeneration"], old.generation)
        self.assertEqual(exits[0]["stderrSummary"], "RuntimeError")
        self.assertEqual(exits[1]["workerGeneration"], new.generation)
        self.assertIsNone(exits[1]["stderrSummary"])
        self.assertEqual(exits[1]["stderrBytes"], 0)
        self.assertNotIn("startup-private-value", json.dumps(self.events()))

    async def test_task_cancellation_during_startup_reaps_worker_without_dispatch(self):
        os.environ["MCP_FIXTURE_FAULT"] = "startup-wait"
        pending = self.session.execute("pass")
        await self.until(lambda: self.session._worker is not None and self.session._worker._bytes > 0)
        pending.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await pending
        self.assertIsNone(self.session._child)
        self.assertIsNotNone(self.children[-1].returncode)
        self.assertFalse(Path(self.env["MCP_FIXTURE_DISPATCHES"]).exists())
        self.assertTrue(next(event for event in self.events() if event["kind"] == "execution_reset")["complete"])

    async def test_task_cancellation_after_dispatch_keeps_unknown_effect_and_never_replays(self):
        os.environ["MCP_FIXTURE_FAULT"] = "runtime-wait"
        pending = self.session.execute("pass")
        await self.until(lambda: Path(self.env["MCP_FIXTURE_DISPATCHES"]).exists())
        pending.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await pending
        reset = next(event for event in self.events() if event["kind"] == "execution_reset")
        self.assertIn("Call cancelled", reset["reason"])
        ended = next(event for event in self.events() if event["kind"] == "execution_end")
        self.assertIn("may already have executed", text(ended))
        self.assertNotIn("code was not sent", text(ended))
        self.assertIsNotNone(self.children[-1].returncode)
        self.assertEqual(Path(self.env["MCP_FIXTURE_DISPATCHES"]).read_text(), "received\n")

    async def test_close_during_startup_is_prompt_and_never_admits_another_call(self):
        os.environ["MCP_FIXTURE_FAULT"] = "startup-wait"
        pending = self.session.execute("pass")
        await self.until(lambda: self.session._worker is not None and self.session._worker._bytes > 0)
        reset = await asyncio.wait_for(self.session.close(), 2)
        self.assertFalse(reset.get("isError"), reset)
        result = await pending
        self.assertIn("before code was sent", text(result))
        self.assertIn("closed", text(await self.session.execute("pass")))
        await self.until(lambda: not self.session._tasks)

    async def test_inherited_stderr_does_not_extend_native_cleanup_barrier(self):
        os.environ["MCP_FIXTURE_FAULT"] = "held-stderr"
        loop = asyncio.get_running_loop()
        started = loop.time()
        result = await asyncio.wait_for(self.session.execute("pass"), 2)
        self.assertIn("status 31", text(result))
        self.assertLess(loop.time() - started, 0.6)
        event = next(event for event in self.events() if event["kind"] == "worker_exit")
        self.assertFalse(event["stderrComplete"])
        self.assertIsNone(self.session._retiring)

    async def test_pending_native_lifetime_is_not_claimed_complete_or_replayed(self):
        os.environ["MCP_FIXTURE_FAULT"] = "held-lifetime"
        with patch("computer_use_mcp.session.CLEANUP_TIMEOUT_SECONDS", 0.05):
            result = await self.session.execute("pass")
            self.assertIn("Native cleanup is still pending", text(result))
            reset = next(event for event in self.events() if event["kind"] == "execution_reset")
            self.assertFalse(reset["complete"])
            blocked = await self.session.execute("pass")
            self.assertIn("no code was executed", text(blocked))
            self.assertEqual(Path(self.env["MCP_FIXTURE_DISPATCHES"]).read_text(), "received\n")
        await self.until(lambda: self.session._retiring is None)
        os.environ.pop("MCP_FIXTURE_FAULT")
        self.assertEqual(text(await self.session.execute("print('fresh')")), "fresh")

    async def test_portable_windows_session_reports_startup_import_and_releases_resources(self):
        class PortableJob:
            def __init__(self):
                self.closed = False
                jobs.append(self)

            def assign(self, pid):
                pass

            def contains(self, pid):
                return True

            async def wait_empty(self):
                pass

            def close(self):
                self.closed = True

        jobs = []
        with (
            patch("computer_use_mcp.session.os", SimpleNamespace(name="nt", environ=os.environ)),
            patch("computer_use_mcp.windows_worker.WindowsJob", PortableJob),
        ):
            os.environ["MCP_FIXTURE_FAULT"] = "import"
            result = await asyncio.wait_for(self.session.execute("pass"), 3)
            self.assertTrue(result["isError"])
            self.assertIn("startup failed", text(result))
            self.assertIn(MISSING_MODULE, text(result))
            self.assertIn("status 1", text(result))
            self.assertIn("code was not sent", text(result))
            self.assertTrue(jobs[0].closed)
            self.assertIsNone(self.session._child)
            os.environ.pop("MCP_FIXTURE_FAULT")
            fresh = await self.session.execute("print('windows fresh')")
            self.assertEqual(text(fresh), "windows fresh")
            await self.session.close()
            self.assertTrue(all(job.closed for job in jobs))

    async def test_windows_job_refusal_keeps_safe_cause_before_dispatch(self):
        jobs = []

        class EscapedJob:
            allowed = False

            def __init__(self):
                self.closed = False
                jobs.append(self)

            def assign(self, pid):
                self.pid = pid

            def contains(self, pid):
                self.authenticatedPid = pid
                return self.allowed

            async def wait_empty(self):
                pass

            def close(self):
                self.closed = True

        with (
            patch("computer_use_mcp.session.os", SimpleNamespace(name="nt", environ=os.environ)),
            patch("computer_use_mcp.windows_worker.WindowsJob", EscapedJob),
        ):
            result = await self.session.execute("print('must not run')")
            self.assertTrue(result["isError"])
            self.assertIn("escaped its lifetime Job", text(result))
            self.assertIn("ordinary CPython", text(result))
            self.assertIn("uv --managed-python", text(result))
            self.assertIn("Microsoft Store execution alias", text(result))
            self.assertIn("worker_interpreter_outside_job", text(result))
            self.assertIn("code was not sent", text(result))
            self.assertNotIn("may already have executed", text(result))
            self.assertNotIn("must not run", text(result))
            self.assertFalse(any(event["kind"] == "execution_start" for event in self.events()))
            self.assertEqual(len(self.children), 1)
            self.assertEqual(jobs[0].authenticatedPid, self.children[0].pid)
            self.assertIsNotNone(self.children[0].returncode)
            self.assertTrue(jobs[0].closed)
            EscapedJob.allowed = True
            self.assertEqual(text(await self.session.execute("print('fresh')")), "fresh")
            await self.session.close()
            self.assertTrue(all(job.closed for job in jobs))

    async def test_windows_unknown_startup_exception_never_exposes_its_body(self):
        class UnknownStartupError(RuntimeError):
            def __str__(self):
                return "private-credential-value"

        class UnexpectedJob:
            def assign(self, pid):
                pass

            def contains(self, pid):
                raise UnknownStartupError()

            async def wait_empty(self):
                pass

            def close(self):
                pass

        with (
            patch("computer_use_mcp.session.os", SimpleNamespace(name="nt", environ=os.environ)),
            patch("computer_use_mcp.windows_worker.WindowsJob", UnexpectedJob),
        ):
            result = await self.session.execute("pass")
        self.assertTrue(result["isError"])
        self.assertIn("UnknownStartupError", text(result))
        self.assertIn("code was not sent", text(result))
        self.assertNotIn("private-credential-value", text(result))
        self.assertNotIn("private-credential-value", json.dumps(self.events()))
        self.assertFalse(any(event["kind"] == "execution_start" for event in self.events()))
        self.assertIsNotNone(self.children[-1].returncode)

    async def test_windows_job_refusal_preserves_pending_cleanup_and_blocks_replay(self):
        release = asyncio.Event()

        class PendingEscapedJob:
            def assign(self, pid):
                pass

            def contains(self, pid):
                return False

            async def wait_empty(self):
                await release.wait()

            def close(self):
                pass

        with (
            patch("computer_use_mcp.session.os", SimpleNamespace(name="nt", environ=os.environ)),
            patch("computer_use_mcp.windows_worker.WindowsJob", PendingEscapedJob),
            patch("computer_use_mcp.session.CLEANUP_TIMEOUT_SECONDS", 0.05),
        ):
            try:
                result = await self.session.execute("pass")
                self.assertIn("escaped its lifetime Job", text(result))
                self.assertIn("code was not sent", text(result))
                self.assertIn("Native cleanup is still pending", text(result))
                reset = next(event for event in self.events() if event["kind"] == "execution_reset")
                self.assertFalse(reset["complete"])
                self.assertIn("escaped its lifetime Job", reset["reason"])
                self.assertIn("no code was executed", text(await self.session.execute("pass")))
                self.assertEqual(len(self.children), 1)
                self.assertFalse(any(event["kind"] == "execution_start" for event in self.events()))
            finally:
                release.set()
                await self.until(lambda: self.session._retiring is None)


if __name__ == "__main__":
    unittest.main()
