# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Public references, observation membership, and input ancestry remain distinct."""

import asyncio

import pytest
from computer_use.errors import ComputerUseError
from computer_use.interaction.agent.scope import window_scope
from computer_use.interaction.query.instances.membership import ancestry, in_scope
from computer_use.native.contracts import Binding, Knowledge
from computer_use.native.scopes import FixedWindowInvalidated, FixedWindowUnconfirmed
from computer_use.session.interfaces import WindowIdentity, WindowLifetime
from regression.test_application_window_lifetimes import ref, setup, surface


def test_attached_observation_surface_does_not_change_physical_input_ancestry():
    menu = surface("menu", parent="b")
    menu["content"] = {"kind": "menu", "host": "a"}
    controller, app = setup(surface("a"), surface("b"), menu)
    root = window_scope(controller, app, ref(app, "a"))
    identity = WindowIdentity.of(menu)
    snapshot = {"binding": Binding("session", menu["instance"], "menu", menu["surface"], menu["node"])}

    assert in_scope(root, menu["surface"])
    assert ancestry(app._instance.directory, "menu", "a") is Knowledge.NO
    controller.interfaces.validate_snapshot(root, snapshot)
    assert snapshot["window_identity"] == identity
    assert not controller.interfaces._snapshot_needs_refresh(root, snapshot)


def test_retained_window_reference_can_refresh_but_retired_one_cannot():
    controller, app = setup(surface("a"))
    item = app._instance.members["a"]
    identity = WindowIdentity.of(item)
    snapshot = {
        "binding": Binding("session", item["instance"], "a", item["surface"], item["node"]),
        "window_identity": identity,
    }
    app._instance.unconfirm(identity)
    controller.interfaces.validate_reference_owner(app, snapshot)
    controller.interfaces.validate_snapshot(app, snapshot, allow_unknown=True)
    assert controller.interfaces._snapshot_needs_refresh(app, snapshot)
    with pytest.raises(ComputerUseError) as unconfirmed:
        controller.interfaces.validate_snapshot(app, snapshot)
    assert unconfirmed.value.code == "observation_incomplete"

    app._instance.retire(identity)
    controller.interfaces.validate_reference_owner(app, snapshot)
    try:
        raise RuntimeError("previous operation")
    except RuntimeError as active:
        with pytest.raises(ComputerUseError) as retired:
            controller.interfaces.validate_snapshot(app, snapshot, allow_unknown=True)
        assert retired.value.code == "stale_target"
        assert retired.value.__context__ is active


@pytest.mark.parametrize("error_type", [FixedWindowInvalidated, FixedWindowUnconfirmed])
@pytest.mark.parametrize("matches", [True, False])
def test_public_read_only_updates_the_window_named_by_its_fixed_failure(monkeypatch, error_type, matches):
    controller, app = setup(surface("a"), surface("b"))
    root = app._instance.anchor
    a = WindowIdentity.of(app._instance.members["a"])
    b = WindowIdentity.of(app._instance.members["b"])
    other = Binding("session", b.instance, b.window, b.surface, app._instance.members["b"]["node"])
    target = root.root if matches else other
    original = (
        error_type(target, "Fixture native window ended")
        if error_type is FixedWindowInvalidated else error_type(target)
    )

    async def run():
        task = asyncio.current_task()

        async def read(*_, on_observation, **options):
            assert asyncio.current_task() is task
            assert on_observation is not None and not options["allow_foreground"]
            await asyncio.sleep(0)
            raise original

        monkeypatch.setattr(controller.interfaces.read_pipeline, "run", read)
        with pytest.raises(error_type) as failed:
            await controller.interfaces.read(app, "tree", False, None, explicit_read=True)
        assert failed.value is original

    asyncio.run(run())
    assert app._instance.evidence[b].membership is Knowledge.YES
    if matches:
        assert app._instance.anchor is None
        if error_type is FixedWindowInvalidated:
            assert app._instance.evidence[a].lifetime is WindowLifetime.RETIRED
        else:
            assert app._instance.evidence[a].membership is Knowledge.UNKNOWN
    else:
        assert app._instance.anchor is root
        assert app._instance.evidence[a].membership is Knowledge.YES
