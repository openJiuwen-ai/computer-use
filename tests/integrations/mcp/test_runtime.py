import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path

from display_documents import fixture_renderer
from legacy_client import Controller, computerForController
from legacy_results import decodeResult
from sdk.parity.test_client_parity import read

from computer_use._json import dumps
from computer_use.client.host import (
    setActionHandler,
    setDiagnosticHandler,
    setExecutionGuard,
    setObservationHandler,
    setTextPageHandler,
)
from computer_use.session import images as images_state
from computer_use_mcp.runtime import Runtime
from computer_use_mcp.session import ExecutionSession


def text(result):
    return "\n".join(block["text"] for block in result["content"] if block["type"] == "text")


class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.enterContext(fixture_renderer())
        self.oldDelivery = images_state._deliveryRequired
        self.output = []
        self.commands = []
        self.revision = 0
        test = self

        class Transport:
            async def request(self, method, params):
                test.commands.append((method, params))
                await asyncio.sleep(0.005)
                if method == "readApp":
                    test.revision += 1
                    wire = read(revision=test.revision)["wire"]
                    # A fresh observation of unchanged native controls keeps
                    # their identities; display numbers alone do not prove this.
                    wire["refs"] = {ref: f"token-{ref}" for ref in wire["refs"]}
                    wire["observation"]["root"]["children"].append(
                        dict(ref="99", role="text", name="unchanged " * 180, children=[])
                    )
                    wire["refs"]["99"] = "body"
                    return decodeResult(method, wire)
                if method == "listApps":
                    return [dict(id="fixture", displayName="测试应用", isRunning=True)]
                return dict(
                    target="Fixture", status="dispatched", detail="Native action dispatched; outcome unconfirmed."
                )

        self.runtime = Runtime(
            lambda event: self.output.append(json.loads(dumps(event))),
            lambda: computerForController(Controller(lambda _: Transport())),
        )
        self.execution = 0

    async def asyncTearDown(self):
        await self.runtime.close()
        for setter in (
            setActionHandler,
            setObservationHandler,
            setExecutionGuard,
            setDiagnosticHandler,
            setTextPageHandler,
        ):
            setter(None)
        images_state._deliveryRequired = self.oldDelivery

    async def runCode(self, code, **history):
        self.execution += 1
        return await self.runtime.execute(
            dict(
                code=code,
                executionId=self.execution,
                history=dict(resetText=False, resetImages=False, incremental=True, **history),
            )
        )

    async def test_persistent_namespace_explicit_reads_and_receipt_deduplication(self):
        result = await self.runCode('app=computer.app("fixture")\nstate=await app.read()\nprint(state)')
        self.assertNotIn("isError", result)
        self.assertEqual(text(result).count("Observation:"), 1)
        result = await self.runCode('receipt=await app.element("10").typeText("hello")\nprint(receipt)')
        self.assertEqual(text(result).count("Action [dispatched]"), 1)
        self.assertNotIn("Tree:", text(result))
        self.assertEqual([method for method, _ in self.commands], ["readApp", "typeTextElement"])
        result = await self.runCode("print(receipt)")
        self.assertIn("Earlier action [dispatched]", text(result))

    async def test_auto_presentation_history_and_explicit_full(self):
        first = await self.runCode('app=computer.app("fixture")\nawait app.read()')
        second = await self.runCode("await app.read()")
        self.assertIn("Tree: full", text(first))
        self.assertIn("Tree: unchanged", text(second))
        full = await self.runCode('await app.read({"full":True})')
        self.assertIn("Tree: full", text(full))
        reset = await self.runtime.execute(
            dict(
                code="await app.read()",
                executionId=99,
                history=dict(resetText=True, resetImages=False, incremental=True),
            )
        )
        self.assertIn("Tree: full", text(reset))

    async def test_image_delivery_is_not_granted_within_the_same_call(self):
        first = await self.runCode(
            ('app=computer.app("fixture")\nstate=await app.read()\nprintImage(state.screenshot)\nawait app.click(0,0)')
        )
        self.assertTrue(first["isError"])
        self.assertIn("has not been returned to the model", text(first))
        self.assertEqual(sum(block["type"] == "image" for block in first["content"]), 1)
        second = await self.runCode("await app.click(0,0)")
        self.assertNotIn("isError", second)
        self.assertEqual([m for m, _ in self.commands], ["readApp", "click"])

    async def test_explicit_bytes_conversion_keeps_delivery_after_the_call_returns(self):
        first = await self.runCode(
            (
                'app=computer.app("fixture")\nstate=await '
                "app.read()\nprintImage(bytes(state.screenshot))\nawait "
                "app.click(0,0)"
            )
        )
        self.assertTrue(first["isError"])
        self.assertIn("has not been returned to the model", text(first))
        self.assertNotIn("source unknown", text(first))
        second = await self.runCode("await app.click(0,0)")
        self.assertNotIn("isError", second)
        self.assertEqual([m for m, _ in self.commands], ["readApp", "click"])

    async def test_unawaited_work_is_drained_and_old_callbacks_cannot_start_input(self):
        result = await self.runCode(
            "import asyncio\npending=asyncio.create_task(computer.listApps())\nawait asyncio.sleep(0)"
        )
        self.assertTrue(result["isError"])
        self.assertIn("not awaited", text(result))
        self.assertTrue(self.runtime.taskNamespace["pending"].done())
        await self.runCode(
            (
                "import asyncio\nasync def later():\n    await asyncio.sleep(0.03)\n    return "
                "await computer.listApps()\nlate=asyncio.create_task(later())"
            )
        )
        with self.assertRaisesRegex(RuntimeError, "ended tool execution"):
            await self.runtime.taskNamespace["late"]
        self.assertEqual([m for m, _ in self.commands], ["listApps"])

    async def test_python_failure_keeps_prior_output(self):
        result = await self.runCode('print("kept")\nraise ValueError("stop")')
        self.assertTrue(result["isError"])
        self.assertIn("kept", text(result))
        self.assertIn("ValueError: stop", text(result))



class SessionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="computer-use-python-session-")
        self.old = os.environ.get("COMPUTER_USE_LOG_DIR")
        os.environ["COMPUTER_USE_LOG_DIR"] = self.temp.name
        # General lifecycle checks must allow a fresh Windows worker to import
        # the runtime. Watchdog tests below use a ready worker and a short budget.
        self.session = ExecutionSession(5000)

    async def asyncTearDown(self):
        await self.session.close()
        if self.old is None:
            os.environ.pop("COMPUTER_USE_LOG_DIR", None)
        else:
            os.environ["COMPUTER_USE_LOG_DIR"] = self.old
        self.temp.cleanup()

    async def test_worker_persists_and_reset_clears_variables(self):
        first = await self.session.execute("answer=40\nprint(answer)", title="读取值")
        second = await self.session.execute("answer+=2\nprint(answer)")
        self.assertEqual(text(first), "40")
        self.assertEqual(text(second), "42")
        self.assertEqual(first["_meta"]["computer-use"]["title"], "读取值")
        self.assertTrue(Path(first["_meta"]["computer-use"]["logPath"]).exists())
        reset = await self.session.reset()
        self.assertNotIn("isError", reset)
        third = await self.session.execute('print("answer" in globals())')
        self.assertEqual(text(third), "False")

    async def test_large_native_diagnostic_preserves_output_journal_and_worker_state(self):
        # Approximate Code's 28.9 MiB / 1002-node observation diagnostic. The
        # real worker, private socket, and parent journal must retain it intact.
        self.session.timeoutMs = 15000
        result = await self.session.execute(
            'from computer_use.diagnostics import diagnostic\n'
            'answer = 41\n'
            'print("before observation")\n'
            'diagnostic(dict(kind="command_end", method="inspect", status="ok", '
            'result=dict(kind="observation", nodes=[dict(value="x" * 28900)] * 1002)))\n'
            'print("after observation")'
        )
        self.assertNotIn("isError", result)
        self.assertEqual(text(result), "before observation\nafter observation")
        self.assertEqual(text(await self.session.execute("answer += 1\nprint(answer)")), "42")
        events = [json.loads(line) for line in self.session._journal.path.read_text().splitlines()]
        diagnostic = next(event for event in events if event.get("kind") == "command_end")
        nodes = diagnostic["result"]["nodes"]
        self.assertEqual(len(nodes), 1002)
        self.assertEqual(nodes[0]["value"], "x" * 28900)
        self.assertEqual(nodes[-1]["value"], "x" * 28900)

    async def test_print_is_atomic_and_raw_stdio_cannot_pollute_tool_output(self):
        result = await self.session.execute(
            (
                'import builtins, sys\nsys.stdout.write("raw stdout")\nsys.stderr.write("raw '
                'stderr")\nbuiltins.print("package", 7, '
                'sep="/")\nprint("done")'
            )
        )
        self.assertNotIn("isError", result)
        self.assertEqual(text(result), "package/7\ndone")

    async def ready_for_watchdog(self):
        self.assertNotIn("isError", await self.session.execute("pass"))
        self.session.timeoutMs = 1000

    async def test_infinite_loop_can_be_stopped_and_keeps_output(self):
        await self.ready_for_watchdog()
        pending = self.session.execute('print("before loop")\nwhile True: pass')
        result = await pending
        self.assertTrue(result["isError"])
        self.assertIn("before loop", text(result))
        self.assertIn("without native command progress", text(result))
        self.session.timeoutMs = 5000
        self.assertEqual(text(await self.session.execute('print("alive")')), "alive")

    async def test_output_does_not_renew_watchdog(self):
        await self.ready_for_watchdog()
        result = await self.session.execute(
            "import asyncio\nfor i in range(10):\n    print(i)\n    await asyncio.sleep(0.2)"
        )
        self.assertTrue(result["isError"])
        self.assertIn("without native command progress", text(result))

    async def test_overlap_rejected_and_cancellation_clears_session(self):
        cancel = asyncio.Event()
        pending = self.session.execute("import asyncio\nawait asyncio.sleep(10)", signal=cancel)
        await asyncio.sleep(0.1)
        self.assertIn("Another call is running", text(await self.session.execute('print("unexpected")')))
        cancel.set()
        result = await pending
        self.assertIn("Call cancelled", text(result))
        self.assertEqual(text(await self.session.execute('print("asyncio" in globals())')), "False")


if __name__ == "__main__":
    unittest.main()
