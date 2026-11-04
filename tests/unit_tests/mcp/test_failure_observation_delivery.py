"""Later failures retain earlier captures in the actual stdio tool result."""

import inspect
import os
import sys
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def fixture():
    """Exercise the real SDK failure boundary without opening a desktop connection."""
    from types import SimpleNamespace

    from computer_use.client.execution import session_executor
    from computer_use.errors import ComputerUseError
    from computer_use.identity import Record
    from computer_use.interaction.agent.pipeline import AgentInterfacePipeline
    from computer_use.interaction.read.presentation.document import DisplayDocument
    from computer_use.interaction.requests import ExecuteAction
    from computer_use.interaction.write.request.pipeline import WriteFailureBoundary
    from computer_use.native.contracts import Binding, Intent
    from computer_use.pipeline import Contract, Control, Effect, Pass
    from computer_use.session.events import action_completed, observation_completed, observations_invalidated
    from computer_use.session.observation import lazy_observation

    source = Record(desktop=SimpleNamespace(reset=lambda: None))
    executor = session_executor()

    def capture(text, scope):
        document = DisplayDocument.join((text,))
        state = lazy_observation(document, None)
        observation_completed({
            "source": source, "scope": scope, "view": "window", "state": state,
            "document": document, "refs": {}, "full": False, "publicApi": True,
        })

    def click():
        action_completed({"target": "Original field", "status": "dispatched", "detail": "Click sent once."})
        capture("CAPTURE after the click", "original")

    def failure(effect):
        return ComputerUseError({
            "code": "input_unavailable" if effect == "none" else "execution_unknown",
            "target": "Original field", "effect": effect, "detail": "NEXT WRITE FAILED",
            "next": "Inspect the captured facts and failure before choosing another action.",
        })

    def reject(effect="none", *, desktop=False):
        async def dispatch(request, control):
            # A visible marker distinguishes historical facts from a result
            # falsely rendered as if it had been captured after this attempt.
            print("NEXT WRITE ENTERED")  # noqa: T201 - assert ordering in actual MCP stdout delivery
            raise failure(effect)

        body = Pass.define(
            name="fixture_dispatch", function=dispatch,
            contract=Contract(ExecuteAction, dict, {Effect.DISPATCH}),
        )()
        boundary = WriteFailureBoundary(source, body)
        request = ExecuteAction(
            None if desktop else source, Intent("insert", {"text": "a"}),
            Binding("fixture", "application", "window", "surface", "field"),
        )
        executor.run(lambda: boundary.arun(request, Control()))

    def fail_desktop_read():
        async def read():
            raise RuntimeError("DESKTOP CAPTURE FAILED")

        source.desktop_reader = SimpleNamespace(read=read)
        interface = SimpleNamespace(controller=source)
        executor.run(lambda: AgentInterfacePipeline.read_desktop(interface, "image", False))

    globals().update(
        capture=capture, click=click, reject=reject, fail_desktop_read=fail_desktop_read,
        retire=lambda: observations_invalidated(source),
    )


class FailureObservationDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_prior_facts_survive_refused_uncertain_and_desktop_operations(self):
        with tempfile.TemporaryDirectory(prefix="mcp-failed-write-") as directory:
            params = StdioServerParameters(
                command=sys.executable, args=["-m", "computer_use_mcp"],
                cwd=Path(__file__).resolve().parents[3],
                env={**os.environ, "COMPUTER_USE_LOG_DIR": directory},
            )
            async with (
                stdio_client(params) as (read, write),
                ClientSession(read, write, read_timeout_seconds=timedelta(seconds=20)) as client,
            ):
                await client.initialize()

                async def call(code):
                    result = await client.call_tool("computer_use", {"code": code})
                    return result, "\n".join(block.text for block in result.content if block.type == "text")

                setup = inspect.getsource(fixture) + "\nfixture()\n"
                result, text = await call(setup)
                self.assertFalse(result.isError, text)
                for effect, desktop in (("none", False), ("partial", False), ("possible", True)):
                    with self.subTest(effect=effect, desktop=desktop):
                        result, text = await call(
                            "click()\ncapture('OTHER APP capture', 'other-app')\n"
                            f"reject({effect!r}, desktop={desktop!r})\nprint('UNREACHED')"
                        )
                        self.assertTrue(result.isError, text)
                        self.assertEqual(text.count("Action [dispatched]"), 1)
                        self.assertIn("CAPTURE after the click", text)
                        self.assertIn("OTHER APP capture", text)
                        self.assertLess(text.index("CAPTURE after the click"), text.index("NEXT WRITE ENTERED"))
                        self.assertLess(text.index("OTHER APP capture"), text.index("NEXT WRITE ENTERED"))
                        self.assertLess(text.index("NEXT WRITE ENTERED"), text.index("NEXT WRITE FAILED"))
                        self.assertNotIn("UNREACHED", text)

                result, text = await call("capture('APP before desktop read', 'read-gap')\nfail_desktop_read()")
                self.assertTrue(result.isError, text)
                self.assertIn("APP before desktop read", text)
                self.assertIn("DESKTOP CAPTURE FAILED", text)

                # A proven session retirement is different from an operation
                # failure: its undelivered bindings must still be retired.
                result, text = await call("capture('RETIRED FACT', 'retired')\nretire()")
                self.assertFalse(result.isError, text)
                self.assertNotIn("RETIRED FACT", text)
