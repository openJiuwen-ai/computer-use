"""Negotiation is operation-local; a malformed post-input receipt cannot downgrade to legacy."""

import unittest
from copy import deepcopy
from dataclasses import replace

from sdk.interaction.write.test_click_pipeline import pointer, row_selection
from sdk.interaction.write.test_execution_engine import TARGET

from computer_use.native.contracts import HostPolicy, Intent, UnitCommand
from computer_use.native.transport import ConnectionPort, decode_click_receipt, encode_target
from computer_use.protocol.execution import decodeBegin, decodeExecutionResult


def wire_receipt():
    return dict(
        target=encode_target(TARGET),
        dispatch="dispatched",
        detail="posted",
        preparation=[],
        confirmation="unavailable",
        cleanup="complete",
        errorCode=None,
        retryReason=None,
        context=None,
        click=dict(
            preparationEvidence=[],
            transportEvidence=dict(kind="posted", callEntered=True, callReturned=True, submitted=3, expected=3),
            continuation=dict(kind="stop", target=encode_target(TARGET), routeFamily="pid"),
        ),
    )


class Peer:
    protocol_version = 6
    platform = "macos"
    sessionIdentity = "transport-session"

    def __init__(self, capable=True, echo=True, unexpected=False):
        self.capabilities = ("nativeClickPipelineV1", "nativeElementPointer") if capable else ("nativeElementPointer",)
        self.echo, self.unexpected, self.requests = echo, unexpected, []
        self.result = wire_receipt()

    async def request(self, method, payload, options):
        self.requests.append((method, deepcopy(payload)))
        if method == "begin":
            decodeBegin(payload)
            result = dict(operation="op", target=payload["target"], remainingMs=options["timeoutMs"])
            if self.unexpected or self.echo and "clickContract" in payload:
                result["clickContract"] = payload.get("clickContract", "v1")
            return decodeExecutionResult("begin", result)
        if method == "execute":
            return decodeExecutionResult("execute", self.result)
        raise AssertionError(method)


class NegotiationTests(unittest.IsolatedAsyncioTestCase):
    async def test_selection_transport_cannot_appear_in_a_pointer_receipt(self):
        peer = Peer()
        port = ConnectionPort(peer, HostPolicy())
        await port.begin(Intent("click"), TARGET, 1000)
        peer.result["click"]["transportEvidence"].update(kind="nativeSelection", submitted=None, expected=None)
        with self.assertRaises(ValueError):
            await port.execute("op", 1, UnitCommand(pointer(), TARGET, Intent("click")), 1000)
        self.assertTrue(port.calls.retired)
        self.assertEqual(len(peer.requests), 2)

    async def test_v2_selection_requires_explicit_negotiation_and_echo(self):
        peer = Peer()
        peer.capabilities += ("nativeClickPipelineV2",)
        port = ConnectionPort(peer, HostPolicy())
        self.assertEqual((await port.begin(Intent("click"), TARGET, 1000)).click_contract, "v2")
        selected = row_selection()
        peer.result["confirmation"] = "confirmed"
        peer.result["click"]["continuation"]["routeFamily"] = selected.click.route_family
        peer.result["click"]["transportEvidence"].update(kind="nativeSelection", submitted=None, expected=None)
        result = await port.execute("op", 1, UnitCommand(selected, TARGET, Intent("click")), 1000)
        self.assertEqual(result.click.transport_evidence.kind, "nativeSelection")
        old_peer = Peer()
        old_port = ConnectionPort(old_peer, HostPolicy())
        await old_port.begin(Intent("click"), TARGET, 1000)
        with self.assertRaises(ValueError):
            await old_port.execute("op", 1, UnitCommand(selected, TARGET, Intent("click")), 1000)
        self.assertEqual(len(old_peer.requests), 1)

    async def test_new_client_old_driver_never_sends_unknown_field(self):
        peer = Peer(capable=False)
        port = ConnectionPort(peer, HostPolicy())
        op = await port.begin(Intent("click"), TARGET, 1000)
        self.assertIsNone(op.click_contract)
        self.assertNotIn("clickContract", peer.requests[0][1])

    async def test_capability_requires_explicit_begin_echo(self):
        for echo in (True, False):
            peer, port = Peer(echo=echo), None
            port = ConnectionPort(peer, HostPolicy())
            if echo:
                self.assertEqual((await port.begin(Intent("click"), TARGET, 1000)).click_contract, "v1")
            else:
                with self.assertRaises(ValueError):
                    await port.begin(Intent("click"), TARGET, 1000)
                self.assertTrue(port.calls.retired)
            self.assertEqual(peer.requests[0][1]["clickContract"], "v1")

    async def test_non_click_does_not_enable_extension(self):
        for intent in (Intent("key", {"key": "Return"}), Intent("action", {"action": "AXPress"})):
            peer = Peer()
            await ConnectionPort(peer, HostPolicy()).begin(intent, TARGET, 1000)
            self.assertNotIn("clickContract", peer.requests[0][1])

    async def test_unrequested_echo_is_rejected(self):
        port = ConnectionPort(Peer(capable=False, unexpected=True), HostPolicy())
        with self.assertRaises(ValueError):
            await port.begin(Intent("click"), TARGET, 1000)

    async def test_separate_preparation_is_rejected_before_request(self):
        peer = Peer()
        port = ConnectionPort(peer, HostPolicy())
        await port.begin(Intent("click"), TARGET, 1000)
        with self.assertRaises(ValueError):
            await port.execute("op", 1, UnitCommand(pointer(), TARGET, Intent("click"), prepare=True), 1000)
        self.assertEqual(len(peer.requests), 1)

    async def test_receipt_extension_and_fixed_delivery_are_mandatory_after_input(self):
        for invalid in ("missing", "wrongFamily"):
            peer = Peer()
            port = ConnectionPort(peer, HostPolicy())
            await port.begin(Intent("click"), TARGET, 1000)
            if invalid == "missing":
                del peer.result["click"]
            else:
                peer.result["click"]["continuation"]["routeFamily"] = "hid"
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                await port.execute("op", 1, UnitCommand(pointer(), TARGET, Intent("click")), 1000)
            self.assertTrue(port.calls.retired)
            self.assertEqual(len(peer.requests), 2)


class ClickWireTests(unittest.TestCase):
    def test_selection_requires_its_own_delivery_and_no_fabricated_action(self):
        selected = row_selection()
        with self.assertRaises(ValueError):
            replace(selected, click=None)
        with self.assertRaises(ValueError):
            replace(selected, action="AXPress")
        with self.assertRaises(ValueError):
            replace(selected, click=replace(selected.click, delivery="object_action"))

    def test_submission_does_not_claim_effect_confirmation(self):
        wire = decodeExecutionResult("execute", wire_receipt())
        receipt = decode_click_receipt(wire["click"])
        self.assertEqual(wire["confirmation"], "unavailable")
        self.assertEqual(receipt.transport_evidence.submitted, 3)

    def test_malformed_evidence_cannot_authorize_replay(self):
        for mutate in (
            lambda w: w["click"]["continuation"].update(kind="reselectWithinScope"),
            lambda w: w["click"]["transportEvidence"].update(callEntered=False),
            lambda w: w["click"]["transportEvidence"].update(submitted=4),
            lambda w: w.update(dispatch="notDispatched"),
            lambda w: w["click"]["continuation"]["target"].update(node="other"),
            lambda w: w["click"]["transportEvidence"].update(extraAuthority=True),
        ):
            value = wire_receipt()
            mutate(value)
            with self.subTest(value=value), self.assertRaises(ValueError):
                decodeExecutionResult("execute", value)

    def test_explicit_zero_submission_is_retained_without_retry_authority(self):
        value = wire_receipt()
        value.update(dispatch="notDispatched", errorCode="input_unavailable")
        value["click"]["transportEvidence"].update(kind="inserted", submitted=0)
        result = decodeExecutionResult("execute", value)
        self.assertIsNone(result["retryReason"])
        self.assertEqual(result["dispatch"], "notDispatched")

    def test_returned_void_post_cannot_claim_zero_dispatch_and_reselect(self):
        value = wire_receipt()
        value.update(dispatch="notDispatched", errorCode="input_unavailable", retryReason="routeUnavailable")
        value["click"]["transportEvidence"].update(submitted=None)
        value["click"]["continuation"]["kind"] = "reselectWithinScope"
        with self.assertRaises(ValueError):
            decodeExecutionResult("execute", value)
