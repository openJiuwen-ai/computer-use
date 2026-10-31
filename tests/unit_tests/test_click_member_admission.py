# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Missing click evidence may request a member query without admitting input."""

from dataclasses import replace
import unittest

from computer_use.interaction.write.routes.policy import Attempts, needs_pointer_member
from computer_use.native.contracts import Binding, Channel, ClickOffer, Facts, Intent, Knowledge, Primitive


class ClickMemberAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.pointer = Primitive(
            "pointer",
            "pointer_sequence",
            Channel.POINTER,
            Knowledge.NO,
            Knowledge.UNKNOWN,
            pending=("receiver",),
            click=ClickOffer("macos.hid.v1", "pointer", "desktop_pointer", "insufficientEvidence"),
        )
        self.facts = Facts(
            Binding("session", "instance", "window", "surface", "node"),
            "current",
            "original control",
            Knowledge.YES,
            Knowledge.YES,
            Knowledge.YES,
            (),
            (self.pointer,),
            platform="macos",
            native_role="AXRow",
            actions_complete=True,
        )
        self.intent = Intent("click", {"button": "left", "count": 2})

    def test_insufficient_evidence_requests_member_proof(self):
        self.assertTrue(needs_pointer_member(self.facts, self.intent, Attempts()))
        self.assertEqual(self.facts.primitives[0].click.admission, "insufficientEvidence")
        self.assertIs(self.facts.primitives[0].supported, Knowledge.NO)

    def test_unavailable_alternative_does_not_hide_the_member_query(self):
        unavailable = replace(
            self.pointer, id="unavailable", click=replace(self.pointer.click, admission="unavailable")
        )
        self.assertTrue(
            needs_pointer_member(replace(self.facts, primitives=(self.pointer, unavailable)), self.intent, Attempts())
        )
        self.assertFalse(needs_pointer_member(replace(self.facts, primitives=(unavailable,)), self.intent, Attempts()))

    def test_ready_action_does_not_trigger_a_speculative_member_query(self):
        action = Primitive(
            "action",
            "native_action",
            Channel.OBJECT,
            Knowledge.YES,
            Knowledge.YES,
            action="AXPress",
            click=ClickOffer("macos.axPress.v1", "semantic", "object_action", "ready"),
        )
        self.assertFalse(
            needs_pointer_member(
                replace(self.facts, primitives=(self.pointer, action)),
                Intent("click", {"button": "left", "count": 1}),
                Attempts(),
            )
        )

    def test_identity_scope_enabled_and_modal_boundaries_are_retained(self):
        cases = (
            replace(self.facts, identity=Knowledge.UNKNOWN),
            replace(self.facts, within_scope=Knowledge.UNKNOWN),
            replace(self.facts, enabled=Knowledge.NO),
            replace(self.facts, blocked_by=("modal",)),
        )
        for facts in cases:
            with self.subTest(facts=facts):
                self.assertFalse(needs_pointer_member(facts, self.intent, Attempts()))
