# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Replay the Finder menu read where pre-input discovery saw an independent peer."""

import asyncio
import json
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from computer_use.errors import ComputerUseError
from computer_use.interaction.agent.requests import ActOnElement
from computer_use.interaction.query.instances.scope import adopt
from computer_use.interaction.read.numbering.references import DisplayReferences
from computer_use.interaction.read.views.collection import CollectionPlan
from computer_use.interaction.read.views.pipeline import InterfaceReadPipeline, WindowPart, member_target
from computer_use.interaction.write.engine import Engine
from computer_use.native.contracts import Intent
from computer_use.native.transport import decode_step_result
from computer_use.session.interfaces import WindowIdentity
from computer_use.session.observation_data import observation_data
from test_d34_read_contracts import setup
from test_dynamic_observation import tree
from test_write_stage_contracts import CASES, RecordingPort, facts, primitive

FIXTURE = Path(__file__).parents[1] / "fixtures" / "recorded-finder-menu-directories.json"


@pytest.mark.parametrize("scenario", ["recorded", "new-peer", "replaced-origin", "missing-root", "unproved-menu"])
def test_failed_menu_uses_the_actual_preinput_directory_without_an_extra_query(scenario, monkeypatch):
    samples = json.loads(FIXTURE.read_text())
    old, fresh, after = (samples[k] for k in ("20", "29", "36"))
    if scenario in ("new-peer", "unproved-menu"):
        fresh["surfaces"] = [item for item in fresh["surfaces"] if item["window"] != "22"]
    if scenario in ("replaced-origin", "missing-root"):
        row = next(item for item in after["surfaces"] if item["window"] == "0")
        row.update(instance="different instance") if scenario == "replaced-origin" else row.update(node=None)
        after["focusedWindow"] = "23"
    controller, app, instance = setup(old)
    original = next(item for item in old["surfaces"] if item["window"] == "0")
    original_identity = WindowIdentity.of(original)
    target = decode_step_result(samples["33"]).target
    snapshot = {"binding": target, "window_identity": original_identity, "scope_kind": "window", "container": False}
    instance.refs["7"] = snapshot
    preinput = AsyncMock(return_value=fresh)
    monkeypatch.setattr(controller.reader.directory, "_query", preinput)
    refusal = ComputerUseError({
        "code": "execution_unknown", "effect": "possible", "target": "native element", "detail": "AX error",
    })
    refusal._invoked_menu_target = replace(target, node="another element") if scenario == "unproved-menu" else target

    async def execute(*args, **kwargs):
        # A post-input cache can see the actual popup. It must not stand in
        # for the sampled pre-input catalogue when the observation compares.
        instance.directory = after
        raise refusal

    controller.engine.execute = AsyncMock(side_effect=execute)
    seen = []
    members_read = []
    if scenario == "new-peer":
        read_pipeline = InterfaceReadPipeline(controller)
        controller.reader.read = AsyncMock(side_effect=AssertionError("Menu origin must not follow a different root"))
        references = DisplayReferences(target.session)

        async def member(_owner, item, view, **options):
            members_read.append((WindowIdentity.of(item), view, options["component_budget_ms"]))
            return WindowPart(
                member_target(instance, item),
                tree=tree(item, "Recorded member", references) if item["node"] is not None else None,
            )

        read_pipeline.member = member
        monkeypatch.setattr("computer_use.interaction.read.views.pipeline.refresh", AsyncMock(return_value=after))

    async def read(owner, view, full, menu_path, *, related):
        permitted = {item["window"]: item for item in after["surfaces"]}
        plan = CollectionPlan("0", view, related)
        plan.update(after, permitted)
        seen.append((related.new_windows(after), plan.next()))
        if scenario == "new-peer":
            adopt(controller, app, after)
            return await read_pipeline._collect(owner, view, full, menu_path, after, None, None, related)
        return None  # No further native collection is needed for the planner-only cases.

    controller.interfaces.read = AsyncMock(side_effect=read)

    async def run():
        with pytest.raises(ComputerUseError) as caught:
            await controller.run(ActOnElement(app, snapshot, "openMenu", {}))
        assert caught.value is refusal

    asyncio.run(run())
    assert len(seen) == 1
    discoveries, work = seen[0]
    assert discoveries == (
        ("22", "23") if scenario in ("new-peer", "unproved-menu")
        else ("0", "23") if scenario == "replaced-origin" else ("23",)
    )
    assert work is not None
    if scenario in ("replaced-origin", "missing-root", "unproved-menu"):
        assert work[0] != original_identity
    else:
        assert work == (original_identity, "tree")
    if scenario == "new-peer":
        assert members_read[0] == (original_identity, "tree", 2000)
        assert [identity.window for identity, _, _ in members_read].count("0") == 1
        assert any(identity.window == "23" for identity, _, _ in members_read)
        assert any(record["window"] == "0" and "tree" in record["included"]
                   for record in observation_data(refusal.observation)["window_coverage"])
        controller.reader.read.assert_not_awaited()
    preinput.assert_awaited_once()
    controller.engine.execute.assert_awaited_once()
    controller.interfaces.read.assert_awaited_once()


def test_recorded_unknown_ax_receipt_carries_its_invoked_target():
    recorded = decode_step_result(json.loads(FIXTURE.read_text())["33"])
    case = next(case for case in CASES if case[0] == "openMenu")
    case = (*case[:4], recorded.target, *case[5:])
    port = RecordingPort([facts(case, primitive(case))], [recorded])

    async def run():
        with pytest.raises(ComputerUseError) as caught:
            await Engine(port).execute(Intent("openMenu", {}), recorded.target)
        assert caught.value._invoked_menu_target == recorded.target
        assert caught.value.effect == "possible"

    asyncio.run(run())
    assert len(port.commands) == 1
