"""A shell is incomplete observation, not proof of an empty/ready application."""

import copy
import unittest
from dataclasses import replace
from types import SimpleNamespace

from observation_fixture import from_records
from sdk.interaction.read.facts.test_native_facts import batch, edge, node

from computer_use.interaction.read.acquisition.loading import window_content_unexposed
from computer_use.interaction.read.facts.index import observation_index
from computer_use.protocol.observation import NativeNode, ReadIssue, Relation


def item(token, role, name="", bounds=None, **extra):
    return dict(
        node=token,
        values=dict(ControlType="ControlType." + role, Name=name, BoundingRectangle=bounds, **extra),
        fieldStatus={},
        actions=dict(items=[]),
    )


def captured(raw):
    record = copy.deepcopy(raw)
    for raw_node in record["nodes"]:
        raw_node["requestedFields"] = list(dict.fromkeys((*raw_node["values"], *raw_node["fieldStatus"])))
        for failed in raw_node["fieldStatus"]:
            raw_node["values"].pop(failed, None)
        raw_node["actions"].setdefault("state", "complete")
    # Coverage unit cases include incomplete/inconsistent relations. Construct
    # those graph facts directly; transport validation has its own negative cases.
    relations = record["relations"]
    unread = list(record.get("unread", []))
    stops = list(record.get("stopReasons", []))
    record.update(relations=[], requestedRelations=[], unread=[], stopReasons=[])
    native = from_records(record)
    nodes, issues = dict(native.nodes), list(native.issues)
    kinds = {"AXChildren": "children", "AXParent": "parent", "AXWindow": "window", "AXTitleUIElement": "labelled_by"}
    for raw_relation in relations:
        key = kinds[raw_relation["property"]]
        source = nodes[raw_relation["source"]]
        offset = raw_relation.get("offset", 0)
        relation = Relation(
            kind=key,
            targets=tuple(raw_relation["targets"]),
            positions=tuple(range(offset, offset + len(raw_relation["targets"]))),
            total=raw_relation.get("total"),
            enumeration="enumerated",
            evidence="candidate" if raw_relation["certainty"] == "unknown" else "reported",
        )
        nodes[raw_relation["source"]] = replace(source, relations={**source.relations, key: relation})
        if raw_relation["state"] != "complete":
            issues.append(ReadIssue(("nodes", raw_relation["source"], "relations", key), raw_relation["state"]))
        unread.extend(target for target in raw_relation["targets"] if target not in nodes)
    for key in set(unread):
        nodes.setdefault(key, NativeNode())
        issues.append(ReadIssue(("nodes", key), "not_read"))
    issues.extend(ReadIssue(("nodes",), "partial", stop) for stop in stops)
    native = replace(native, nodes=nodes, issues=tuple(issues))
    return SimpleNamespace(native=native, facts=observation_index(native))


def snapshot(*children):
    root = item(
        "root",
        "Window",
        "Application",
        {"x": 0, "y": 0, "width": 600, "height": 500},
        **{"Win32.ClientRectangle": {"x": 10, "y": 40, "width": 580, "height": 450}},
    )
    return dict(
        kind="nodes",
        session="session",
        surface="surface",
        platform="windows",
        observed=dict(id="readiness", startedAt=1, completedAt=2),
        roots=["root"],
        nodes=[root, *children],
        relations=[],
        stopReasons=[],
        unread=[],
    )


class WindowReadinessTests(unittest.TestCase):
    def test_window_chrome_does_not_establish_client_content(self):
        s = snapshot(
            item("title", "TitleBar", "Title", dict(x=0, y=0, width=600, height=40)),
            item("close", "Button", "Close", dict(x=550, y=0, width=50, height=40)),
        )
        self.assertTrue(window_content_unexposed(captured(s)))

    def test_single_control_or_raw_text_counts_without_node_count_threshold(self):
        for role, name in [("Button", ""), ("Text", "A"), ("Edit", ""), ("Custom", "Painted data"), ("Pane", "Status")]:
            self.assertFalse(
                window_content_unexposed(
                    captured(snapshot(item("body", role, name, dict(x=15, y=45, width=30, height=30))))
                )
            )

    def test_unknown_geometry_partial_and_unsupported_platform_are_not_guessed(self):
        self.assertFalse(window_content_unexposed(captured(snapshot(item("body", "Button", "X")))))
        for key, value in [("stopReasons", ["limited"]), ("unread", ["unread-node"]), ("platform", "macos")]:
            s = snapshot()
            s[key] = value
            self.assertFalse(window_content_unexposed(captured(s)))
        s = snapshot()
        s["nodes"][0]["fieldStatus"]["Win32.ClientRectangle"] = {"state": "unsupported"}
        self.assertFalse(window_content_unexposed(captured(s)))

    def test_unnamed_layout_is_preserved_but_does_not_prove_body_coverage(self):
        s = snapshot(item("pane", "Pane", bounds=dict(x=10, y=40, width=580, height=450)))
        self.assertTrue(window_content_unexposed(captured(s)))
        self.assertEqual(len(s["nodes"]), 2)


class MacWindowContentCoverageTests(unittest.TestCase):
    @staticmethod
    def shell():
        return batch(
            [
                node("root", AXRole="AXWindow", AXTitle="Ordinary window"),
                node("close", AXRole="AXButton", AXSubrole="AXCloseButton"),
                node("body", AXRole="AXGroup"),
                node("layout", AXRole="AXScrollArea"),
            ],
            [edge("root", "close", "body"), edge("body", "layout"), edge("root", "application", property="AXParent")],
        )

    def classify(self, raw):
        before = copy.deepcopy(raw)
        result = window_content_unexposed(captured(raw))
        self.assertEqual(raw, before, "Coverage diagnosis must not edit facts or references")
        return result

    def test_complete_window_chrome_and_anonymous_layout_with_external_parent(self):
        raw = self.shell()
        raw["unread"] = ["application"]
        self.assertTrue(self.classify(raw))
        self.assertTrue(self.classify(batch([node("root", AXRole="AXWindow")], [edge("root")])))

    def test_one_control_text_named_group_or_unknown_role_prevents_shell_classification(self):
        for values in (
            dict(AXRole="AXButton"),
            dict(AXRole="AXTextField", AXValue=""),
            dict(AXRole="AXStaticText", AXValue=""),
            dict(AXRole="AXStaticText", AXValue="Ready"),
            dict(AXRole="AXGroup", AXTitle="Status"),
            dict(AXRole="AXGroup", AXValue=0),
            dict(AXRole="AXUnknown"),
            dict(AXRole="AXWebArea"),
            dict(AXRole="VendorCanvas"),
            {},
        ):
            with self.subTest(values=values):
                raw = self.shell()
                raw["nodes"][-1]["values"] = values
                self.assertFalse(self.classify(raw))

    def test_window_title_requires_explicit_relation_not_matching_text(self):
        raw = self.shell()
        raw["nodes"][-1]["values"] = dict(AXRole="AXStaticText", AXValue="Ordinary window")
        self.assertFalse(self.classify(raw))
        raw["relations"].append(edge("root", "layout", property="AXTitleUIElement"))
        self.assertTrue(self.classify(raw))
        raw["nodes"][-1]["actions"]["items"] = [dict(name="AXOpen")]
        self.assertFalse(self.classify(raw))

    def test_custom_actions_editing_or_unknown_catalogue_are_not_anonymous_layout(self):
        for change in ("action", "unknown-actions", "editable", "setter", "label-error", "root-value"):
            with self.subTest(change=change):
                raw = self.shell()
                body = raw["nodes"][2]
                if change == "action":
                    body["actions"]["items"] = [dict(name="AXPress")]
                elif change == "unknown-actions":
                    body["actions"]["state"] = "error"
                elif change == "editable":
                    body["values"]["AXEditable"] = True
                elif change == "setter":
                    body["values"]["settable.AXValue"] = True
                elif change == "label-error":
                    body["fieldStatus"]["AXDescription"] = dict(state="timeout")
                else:
                    raw["nodes"][0]["values"]["AXValue"] = "Window-provided document text"
                self.assertFalse(self.classify(raw))

    def test_incomplete_children_relations_and_unread_content_are_not_classified(self):
        for change in (
            "partial",
            "offset",
            "total",
            "unknown",
            "missing-edge",
            "unread-child",
            "unread-content",
            "stopped",
            "cycle",
            "shared",
        ):
            with self.subTest(change=change):
                raw = self.shell()
                children = raw["relations"][0]
                if change == "partial":
                    children["state"] = "partial"
                elif change == "offset":
                    children["offset"] = 1
                elif change == "total":
                    children["total"] = 50
                elif change == "unknown":
                    children["certainty"] = "unknown"
                elif change == "missing-edge":
                    raw["relations"].remove(children)
                elif change == "unread-child":
                    children["targets"].append("missing")
                elif change == "unread-content":
                    raw["unread"] = ["missing-body"]
                elif change == "stopped":
                    raw["stopReasons"] = ["deadline"]
                elif change == "cycle":
                    raw["relations"][1]["targets"].append("root")
                else:
                    children["targets"].append("layout")
                self.assertFalse(self.classify(raw))

    def test_window_controls_must_be_direct_children_of_that_window(self):
        raw = self.shell()
        raw["relations"][0]["targets"].remove("close")
        raw["relations"][1]["targets"].append("close")
        self.assertFalse(self.classify(raw))
