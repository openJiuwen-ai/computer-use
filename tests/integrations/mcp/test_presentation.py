import copy
import unittest

from baseline import baseline
from display_documents import fixture_renderer, with_document
from sdk.parity.test_protocol_parity import case, normalize

from computer_use._identity import Record
from computer_use.interaction.read.presentation.actions.presentation import ActionPresentation
from computer_use_mcp.history import HistoryGate, historyRequest
from computer_use_mcp.output import OutputBuffer
from computer_use_mcp.presentation import ObservationOutput, ObservationPresenter


def runOutput(steps):
    output = OutputBuffer()
    results = []
    for step in steps:
        try:
            output.append(copy.deepcopy(step["block"]), step.get("merge") is True)
            results.append(dict(ok=True))
        except Exception as error:
            results.append(dict(ok=False, error=normalize(error)))
    return dict(results=results, content=output.content)


def runHistory(steps):
    gate = HistoryGate()
    cursors = []
    results = []
    for step in steps:
        if step["op"] == "complete":
            reply = gate.complete()
            cursors.append(reply["cursor"])
            results.append(dict(version=reply["version"], cursor=f"cursor-{len(cursors)}"))
        else:
            request = step.get("request")
            if (
                isinstance(request, dict)
                and isinstance(request.get("previous"), str)
                and request["previous"].startswith("cursor-")
            ):
                request = {**request, "previous": cursors[int(request["previous"][7:]) - 1]}
            results.append(gate.begin(historyRequest(request)))
    return results


def runActions(receipts, steps):
    p = ActionPresentation()
    return [p.has(receipts[s["index"]]) if s["op"] == "has" else p.render(receipts[s["index"]]) for s in steps]


class OutputParity(unittest.TestCase):
    def test_exact_budgets_receipt_factoring_and_history_policies(self):
        requests = []

        def block(text, merge=False):
            return dict(block=dict(type="text", text=text), merge=merge)

        requests.append(
            case(
                "output_sequence",
                "run",
                [block("x" * (256 * 1024 - 2), True), block("xx", True), block("y", True), block("z")],
            )
        )
        requests.append(
            case(
                "output_sequence",
                "run",
                [
                    dict(block=dict(type="image", mimeType="image/png", data="a" * 1024 * 1024)),
                    block("中" * 40000),
                    block("🙂" * 35000),
                    block("🙂" * 537),
                    block("🙂" * 536),
                ],
            )
        )
        for text in ["literal", '"\n\t', "\ud800", "\x85\u2028", "a" * 65000]:
            requests.append(
                case(
                    "output_sequence",
                    "run",
                    [block(text, True), block(text, True), block("", False), block(text, True)],
                )
            )
        requests.append(case("output_sequence", "run", [block(str(i)) for i in range(130)]))
        receipts = [
            dict(status="dispatched", target="Control", detail=detail)
            for detail in [
                "Native action dispatched.",
                "Native action dispatched.",
                "Input sent. Foreground input used. Original pointer restored.",
                "Click dispatched. Foreground input used. Original pointer restored.",
                "Input may be partial. Pointer restoration failed.",
            ]
        ]
        requests.append(
            case(
                "action_sequence",
                "run",
                receipts,
                [dict(op=op, index=i) for i in range(len(receipts)) for op in ("has", "render", "has")]
                + [dict(op="render", index=0)],
            )
        )
        requests.append(
            case(
                "history_sequence",
                "run",
                [
                    dict(op="begin"),
                    dict(op="complete"),
                    dict(op="begin", request=dict(version=1, previous="cursor-1")),
                    dict(op="complete"),
                    dict(op="begin", request=dict(version=1, previous="cursor-1")),
                    dict(op="begin"),
                    dict(op="begin", request=dict(version=1, previous=None)),
                    dict(op="complete"),
                    dict(op="begin", request=dict(version=1, previous="cursor-3")),
                ],
            )
        )
        process = baseline("functions", requests)
        expected = process
        methods = dict(output_sequence=runOutput, action_sequence=runActions, history_sequence=runHistory)
        for i, (request, oracle) in enumerate(zip(requests, expected)):
            with self.subTest(index=i):
                self.assertEqual(dict(ok=True, value=methods[request["module"]](*request["args"])), oracle)


def observation(source, value="before", scope="window-A", **options):
    result = dict(
        source=source,
        scope=scope,
        view="window",
        full=False,
        state=dict(
            text=(
                f'Window: "Document", App: Editor.\nTree: full\n0 window Document\n\t1 '
                f"text field Name, Value: {value}\n\t2 text "
            )
            + "unrelated " * 200,
            screenshot=None,
            overview=None,
        ),
        refs={"0": "window", "1": "field", "2": "body"},
        **options,
    )
    result["document"] = with_document(dict(text=result["state"]["text"], refs=result["refs"]))["document"]
    return result


def deliver(presenter, state):
    output = ObservationOutput(presenter)
    texts = []
    output.observe(state)
    output.flush(lambda text, _: texts.append(text))
    assert len(texts) == 1
    return texts[0]


class PresentationTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(fixture_renderer())

    def test_latest_window_image_is_delivered_even_when_text_was_printed(self):
        output = ObservationOutput(ObservationPresenter())
        source = Record()
        first = observation(source)
        first["state"]["screenshot"] = b"old pixels"
        latest = observation(source, "latest")
        latest["state"]["screenshot"] = b"new pixels"
        output.observe(first)
        output.observe(latest)
        output.printed(latest["state"]["text"])
        images = []
        output.flush(lambda *_: self.fail("text duplicated"), images.append)
        self.assertEqual(images, [b"new pixels"])

    def test_only_successful_delivery_advances_baseline(self):
        presenter = ObservationPresenter()
        source = Record()
        first = observation(source)
        output = ObservationOutput(presenter)
        output.observe(first)
        final = observation(source, "after")
        output.observe(final)
        texts = []
        output.flush(lambda text, _: texts.append(text))
        self.assertEqual(texts, [final["state"]["text"]])
        self.assertIn("Tree: unchanged", deliver(presenter, final))
        self.assertIn("~\t1 text field Name, Value: latest", deliver(presenter, observation(source, "latest")))
        self.assertIn("Tree: full", final["state"]["text"])
        next_state = observation(source, "undelivered")
        output.observe(next_state)

        def fail(*_):
            raise RuntimeError("output limit")

        with self.assertRaisesRegex(RuntimeError, "output limit"):
            output.flush(fail)
        self.assertIn("Value: undelivered", deliver(presenter, next_state))

    def test_equal_text_scopes_do_not_guess_delivery_and_cache_is_bounded(self):
        presenter = ObservationPresenter(100000, 1)
        source = Record()
        a, b = observation(source), observation(source, scope="window-B")
        output = ObservationOutput(presenter)
        output.observe(a)
        output.observe(b)
        self.assertIsNone(output.printed(a["state"]["text"]))
        texts = []
        output.flush(lambda text, _: texts.append(text))
        self.assertEqual(texts, [a["state"]["text"], b["state"]["text"]])
        self.assertEqual(deliver(presenter, a), a["state"]["text"])
        self.assertEqual(deliver(presenter, {**a, "view": "subtree"}), a["state"]["text"])
        self.assertEqual(deliver(presenter, a), a["state"]["text"])

    def test_explicit_full_text_is_not_duplicated(self):
        presenter = ObservationPresenter()
        state = observation(Record())
        output = ObservationOutput(presenter)
        output.observe(state)
        output.printed(state["state"]["text"])
        output.flush(lambda *_: self.fail("observation duplicated"))
        self.assertIn("Tree: unchanged", deliver(presenter, state))
        self.assertEqual(deliver(presenter, {**state, "full": True}), state["state"]["text"])


if __name__ == "__main__":
    unittest.main()
