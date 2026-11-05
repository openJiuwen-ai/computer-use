# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Application observation and fixed containment consume separate evidence."""

from copy import deepcopy
from unittest import TestCase

from computer_use.interaction.agent.scope import window_scope
from computer_use.interaction.query.instances.membership import ancestry
from computer_use.interaction.query.instances.scope import adopt, display_selection
from computer_use.native.contracts import Knowledge
from computer_use.protocol.directory import decode_directory
from unit_tests.test_application_window_lifetimes import setup
from unit_tests.test_surface_batch import directory, window


class RootCoverageTests(TestCase):
    def test_directory_gap_wording_does_not_hide_confirmed_application_windows(self):
        for platform in ("macos", "windows", "linux"):
            for detail in ("attached children timed out", "provider detail changed", "附属窗口读取不完整"):
                payload = directory(window("root"), window("child", parent="root"), reasons=[detail])
                payload.update(
                    platform=platform, rootDiscoveryComplete=True, focusedWindow="root", application="editor"
                )
                snapshot = deepcopy(payload)
                facts = decode_directory(payload)
                controller, app = setup()
                adopt(controller, app, facts)
                selected = display_selection(controller, app._instance, facts)
                self.assertEqual(selected.root.root_window, "root")
                self.assertEqual(set(app._instance.members), {"root", "child"})
                self.assertEqual(payload, snapshot)
                self.assertEqual(ancestry(facts, "child", "root"), Knowledge.YES)

    def test_missing_root_coverage_does_not_remove_explicit_window_identity(self):
        payload = directory(window("root"), reasons=["incomplete native catalogue"])
        payload.update(rootDiscoveryComplete=False, application="editor")
        controller, app = setup()
        adopt(controller, app, decode_directory(payload))
        selected = window_scope(controller, app, next(ref for ref, key in app._instance.windows.items() if key == "root"))
        self.assertEqual(selected._window_identity.root_window, "root")
        self.assertFalse(app._instance.directory["rootDiscoveryComplete"])

    def test_unknown_parent_is_not_unknown_application_ownership(self):
        rows = [window("root"), {**window("other"), "parentState": "unknown"}]
        payload = {**directory(*rows), "application": "editor"}
        controller, app = setup()
        adopt(controller, app, decode_directory(payload))
        self.assertEqual(set(app._instance.members), {"root", "other"})
        self.assertEqual(ancestry(payload, "other", "root"), Knowledge.UNKNOWN)
        chosen = window_scope(controller, app, next(ref for ref, key in app._instance.windows.items() if key == "other"))
        self.assertEqual(chosen._window_identity.root_window, "other")

    def test_independent_root_coverage_wire_fact_is_required_and_strict(self):
        payload = directory(window("root"))
        del payload["rootDiscoveryComplete"]
        with self.assertRaises(ValueError):
            decode_directory(payload)
        for value in (None, 1, "true"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                decode_directory({**payload, "rootDiscoveryComplete": value})

    def test_ended_lifetime_evidence_is_exact_and_separate_from_visible_coverage(self):
        live, closed = window("live"), window("closed")
        ended = {key: closed[key] for key in ("window", "surface", "instance")}
        payload = {**directory(live, reasons=["partial AX"]), "endedWindows": [ended]}
        self.assertEqual(decode_directory(payload)["endedWindows"], [ended])
        for invalid in ([ended, ended], [{key: live[key] for key in ("window", "surface", "instance")}],
                        [{"window": "closed"}]):
            with self.subTest(ended=invalid), self.assertRaises(ValueError):
                decode_directory({**payload, "endedWindows": invalid})
