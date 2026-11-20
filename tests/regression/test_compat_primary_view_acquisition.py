# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Primary acquisition can leave the same member eligible for direct reads."""

import asyncio
import sys

import pytest
from computer_use.interaction.read.views import pipeline as views
from computer_use.native.scopes import FixedWindowUnconfirmed, window_binding
from computer_use.session.observation_data import observation_data
from regression.test_compat_d34_read_contracts import directory, setup, window
from regression.test_compat_dynamic_observation import tree

pytestmark = pytest.mark.usefixtures("native_read")


def test_unconfirmed_primary_can_still_collect_its_exact_scheduled_member():
    item = window("A")
    current = directory(item, focused="A", reasons=("provider scan was partial",))
    controller, app, instance = setup(current)
    target = views.member_target(instance, item)
    pipeline = views.InterfaceReadPipeline(controller)
    body = tree(item, "exact member")

    async def execute():
        caller = asyncio.current_task()
        calls = []

        async def read(selected, **options):
            assert asyncio.current_task() is caller and window_binding(selected.root) == window_binding(target)
            assert options["source_directory"] is current
            calls.append("app")
            raise FixedWindowUnconfirmed(target, directory=current)

        async def member(ui, selected, view, **options):
            assert asyncio.current_task() is caller and sys.exception() is None
            assert ui is app and selected is item and view == "tree"
            assert options["component_budget_ms"] is None
            calls.append("member")
            return views.WindowPart(target, tree=body)

        controller.reader.read = read
        pipeline.member = member
        result = await pipeline._collect(app, "tree", False, None, current, None, None, None)
        assert calls == ["app", "member"]
        assert "exact member" in result.text
        assert observation_data(result)["window_coverage"][0]["captured"] == ("tree",)
        assert controller.state(instance.owners["A"]).query_snapshot is body

    asyncio.run(execute())
