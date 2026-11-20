# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Exercise the native graph refinement of one compound text control."""

import unittest

from computer_use.interaction.write.resolution.control_part import ControlPartQuery
from computer_use.native.contracts import Actuator, Binding, Intent


class CompoundTextPort:
    platform = "macos"

    async def query_nodes(self, operation, target, fields, max_nodes, max_depth, timeout_ms):
        roles = {"owner": "AXStaticText", "layout": "AXGroup", "caption": "AXStaticText", "button": "AXButton"}
        children = {"owner": ["layout"], "layout": ["button", "caption"], "caption": [], "button": []}
        names = {
            "owner": ["AXPress", "AXShowMenu"],
            "layout": ["AXShowMenu"],
            "caption": ["AXShowMenu", "AXScrollToVisible"],
            "button": ["AXPress"],
        }
        parents = {"owner": [], "layout": ["owner"], "caption": ["layout"], "button": ["layout"]}
        token = target.node
        values = {field: roles[token] if field == "AXRole" else False for field in fields}
        return {
            "session": target.session,
            "surface": target.surface,
            "platform": self.platform,
            "roots": [token],
            "observed": {"id": token},
            "nodes": [
                {
                    "node": token,
                    "requestedFields": fields,
                    "values": values,
                    "fieldStatus": {},
                    "actions": {"state": "complete", "items": [{"name": action} for action in names[token]]},
                }
            ],
            "relations": [
                {
                    "source": token,
                    "property": name,
                    "targets": targets,
                    "state": "complete",
                    "certainty": "confirmed",
                    "total": len(targets),
                }
                for name, targets in (("AXChildren", children[token]), ("AXParent", parents[token]))
            ],
        }


class CompoundContextPointerTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def query(button="right", count=1):
        target = Binding("session", "app", "root", "window", "owner")
        return ControlPartQuery(
            CompoundTextPort(),
            "operation",
            target,
            lambda: 1000,
            intent=Intent("click", {"button": button, "count": count}),
        )

    async def test_initial_context_click_selects_a_passive_caption_under_its_owner(self):
        for hit in ("button", "layout", "caption"):
            with self.subTest(hit=hit):
                query = self.query()
                self.assertEqual(
                    await query.resolve(hit, allow_passive_candidate=True), Actuator("caption", "controlPart")
                )
                self.assertEqual({fact.node for fact in query.basis}, {"owner", "layout", "caption"})

    async def test_compound_text_accepts_its_exact_passive_caption_hit(self):
        self.assertEqual(await self.query().resolve("caption"), Actuator("caption", "controlPart"))

    async def test_independent_child_stays_a_boundary_during_revalidation_or_other_clicks(self):
        self.assertIsNone(await self.query().resolve("button"))
        self.assertIsNone(await self.query(button="left").resolve("button", allow_passive_candidate=True))
        self.assertIsNone(await self.query(count=2).resolve("button", allow_passive_candidate=True))
