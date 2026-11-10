# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Lower normal window close to its native mechanism inside the write boundary."""

from dataclasses import replace

from computer_use.diagnostics import local_failure
from computer_use.interaction.query.instances.scope import refresh, require_identity
from computer_use.interaction.read.views.pipeline import member_target
from computer_use.interaction.write.request.model import ActionPlan
from computer_use.native.contracts import Intent
from computer_use.native.query.window_close import close_control
from computer_use.pipeline import Contract, Effect, Pass
from computer_use.session.interfaces import WindowIdentity


def create(*, controller):
    async def resolve(plan, _control):
        if plan.request.intent.kind != "closeWindow":
            return plan
        native, owner = controller.port, plan.request.owner
        platform = native.platform
        if platform not in ("macos", "windows"):
            raise local_failure("normal window close is unavailable on this platform", "input_unavailable")
        target = plan.request.target
        identity = WindowIdentity(target.root_window, target.surface, target.instance)
        # Refresh only technical facts, retaining the original exact identity.
        # Neither the foreground window nor a modal child becomes the close target.
        await refresh(controller, owner)
        require_identity(owner, identity, current=True)
        # UIHandle exposes the separate instance catalogue to internal pipelines.
        # pylint: disable-next=protected-access
        instance = owner._instance
        root = member_target(instance, instance.members[identity.window])
        if root.node is None:
            raise local_failure("the selected window has no accessible close control source", "input_unavailable")
        # Keep the requested window and intent for receipts and related observation.
        # The native action path checks WindowPattern on this exact root at dispatch.
        if platform == "windows":
            return replace(plan, target=root, mechanism=Intent("action", {"action": "Window.Close"}))
        async with native.calls.open(root, 30000) as lease:
            button = await close_control(native, lease.token, root, lease.remaining)
        return replace(plan, target=button, mechanism=Intent("click"))

    return resolve


stage = Pass.factory(
    name="ResolveWindowCloseControl",
    factory=create,
    contract=Contract(ActionPlan, ActionPlan, {Effect.QUERY, Effect.RESOURCE}),
)
