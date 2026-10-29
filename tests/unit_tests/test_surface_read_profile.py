# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import unittest
from dataclasses import FrozenInstanceError, replace

from computer_use.interaction.read.app_read.model import ReadRequest
from computer_use.interaction.read.app_read.startup.model import StartupDirectoryState
from computer_use.native.query.directory import SurfaceReadProfile, surfaces_query
from computer_use.protocol.execution import decode_inspect_query


class SurfaceReadProfileTests(unittest.TestCase):
    def test_legacy_and_worker_wire_shapes_are_unchanged(self):
        visual = SurfaceReadProfile.VISUAL
        self.assertEqual(surfaces_query("app", profile=visual), dict(kind="surfaces", application="app"))
        self.assertEqual(
            surfaces_query("app", profile=visual, capabilities=("nativeReadIsolation",)),
            dict(kind="surfaces", application="app", includeAccessibility=False),
        )
        self.assertEqual(
            surfaces_query("app", capabilities=("nativeReadIsolation", "nativeVisualSurfaceQuery")),
            dict(kind="surfaces", application="app"),
        )

    def test_narrow_window_profile_is_separate_from_worker_isolation(self):
        for caps in [("nativeVisualSurfaceQuery",), ("nativeVisualSurfaceQuery", "nativeReadIsolation")]:
            query = surfaces_query("app", profile=SurfaceReadProfile.VISUAL, capabilities=caps)
            self.assertEqual(query, dict(kind="surfaces", application="app", profile="visual"))
            self.assertEqual(decode_inspect_query(query), query)
        with self.assertRaises(ValueError):
            surfaces_query("app", profile=False)

    def test_profile_and_startup_attempt_are_immutable_and_keep_intent(self):
        request = ReadRequest("App", view="image")
        self.assertIs(request.directory_profile, SurfaceReadProfile.VISUAL)
        state = StartupDirectoryState(dict(id="app"), "s", True, profile=request.directory_profile)
        self.assertIs(replace(state, attempt=4).profile, state.profile)
        with self.assertRaises(FrozenInstanceError):
            state.profile = SurfaceReadProfile.ACCESSIBILITY
        self.assertIs(ReadRequest("App", view="both").directory_profile, SurfaceReadProfile.ACCESSIBILITY)

    def test_surface_profile_wire_is_strict_and_unambiguous(self):
        for value in (None, False, "contents", "accessibility"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                decode_inspect_query(dict(kind="surfaces", application="app", profile=value))
        with self.assertRaises(ValueError):
            decode_inspect_query(dict(kind="surfaces", application="app", profile="visual", includeAccessibility=False))
