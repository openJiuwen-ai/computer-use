"""Delivery selection precedes text materialization, logging, and baseline publication."""

import unittest
from types import SimpleNamespace

from computer_use._identity import Record
from computer_use.diagnostics import setDiagnosticHandler
from computer_use.session import images as image_state
from computer_use.session.events import setActionHandler, setObservationHandler
from computer_use.session.observation import lazyObservation, peekObservationText
from computer_use.session.operations import setExecutionGuard
from computer_use.session.results import setTextPageHandler
from computer_use_mcp.presentation import ObservationOutput, ObservationPresenter
from computer_use_mcp.runtime import Runtime


class CountingDocument:
    estimated_bytes = 64

    def __init__(self, text="captured full text"):
        self.value, self.renders = text, 0

    @property
    def text(self):
        self.renders += 1
        return self.value

    def summary_data(self):
        return dict(parts=1)


def observation(source, document, scope="window"):
    return dict(
        source=source,
        scope=scope,
        view="window",
        full=False,
        state=lazyObservation(document),
        refs={"0": "native-window"},
        document=document,
    )


class LatePresentationTests(unittest.TestCase):
    def test_observe_arbitrary_print_and_baseline_never_request_full_text(self):
        source, document = Record(), CountingDocument()
        event = observation(source, document)
        presenter = ObservationPresenter()
        output = ObservationOutput(presenter)
        output.observe(event)
        self.assertIsNone(output.printed("ordinary output"))
        presenter.delivered(event)
        baseline = presenter._baselines[presenter.key(event)]
        self.assertIs(baseline["document"], document)
        self.assertNotIn("text", baseline)
        self.assertIsNone(peekObservationText(event["state"]))
        self.assertEqual(document.renders, 0)

    def test_explicit_full_print_matches_only_materialized_observations(self):
        source = Record()
        first, second = CountingDocument("first full"), CountingDocument("second full")
        a, b = observation(source, first, "a"), observation(source, second, "b")
        output = ObservationOutput(ObservationPresenter())
        output.observe(a)
        output.observe(b)
        self.assertIs(output.printed(a["state"].text), a)
        self.assertEqual(first.renders, 1)
        self.assertEqual(second.renders, 0)


class LateRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.old_delivery = image_state._deliveryRequired
        self.messages, self.events = [], []
        computer = SimpleNamespace(_controller=SimpleNamespace(transport=SimpleNamespace()))
        self.runtime = Runtime(self.messages.append, lambda: computer)
        self.documents = []
        self.source = Record()

        async def capture():
            document = CountingDocument("full capture " + str(len(self.documents)))
            self.documents.append(document)
            event = observation(self.source, document)
            self.runtime.observation(dict(kind="observed", observation=event))
            self.assertEqual(document.renders, 0)
            return event["state"]

        self.runtime.taskNamespace["capture"] = capture

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
        image_state._deliveryRequired = self.old_delivery

    async def run_code(self, code):
        return await self.runtime.execute(
            dict(code=code, executionId=1, history=dict(resetText=False, resetImages=False, incremental=True))
        )

    async def test_selection_runs_before_full_evidence_logging(self):
        def choose(event):
            self.assertEqual(event["document"].renders, 0)
            self.events.append("selected")
            return "Tree: diff\nchanged node"

        self.runtime.presenter.text = choose
        result = await self.run_code('state = await capture()\nprint("ordinary output")')
        self.assertNotIn("isError", result)
        self.assertEqual(self.events, ["selected"])
        self.assertEqual(self.documents[0].renders, 1)
        kinds = [m["diagnostic"]["kind"] for m in self.messages if "diagnostic" in m]
        self.assertEqual(kinds, ["observation_captured", "observation"])
        self.assertEqual(
            next(
                m["diagnostic"]["text"] for m in self.messages if m.get("diagnostic", {}).get("kind") == "observation"
            ),
            "full capture 0",
        )
        self.assertEqual([item["text"] for item in result["content"]], ["ordinary output", "Tree: diff\nchanged node"])

    async def test_explicit_print_is_not_duplicated_or_re_rendered(self):
        self.runtime.presenter.text = lambda _: self.fail("already printed full observation")
        result = await self.run_code("state = await capture()\nprint(state.text)")
        self.assertNotIn("isError", result)
        self.assertEqual([item["text"] for item in result["content"]], ["full capture 0"])
        self.assertEqual(self.documents[0].renders, 1)
        self.assertEqual(sum(m.get("diagnostic", {}).get("kind") == "observation" for m in self.messages), 1)

    async def test_superseded_observations_are_logged_at_the_terminal_boundary(self):
        def choose(event):
            self.assertTrue(all(document.renders == 0 for document in self.documents))
            return "selected latest observation"

        self.runtime.presenter.text = choose
        result = await self.run_code("old = await capture()\nlatest = await capture()")
        self.assertNotIn("isError", result)
        self.assertEqual([item["text"] for item in result["content"]], ["selected latest observation"])
        texts = [m["diagnostic"]["text"] for m in self.messages if m.get("diagnostic", {}).get("kind") == "observation"]
        self.assertEqual(texts, ["full capture 0", "full capture 1"])
        self.assertEqual([document.renders for document in self.documents], [1, 1])


if __name__ == "__main__":
    unittest.main()
