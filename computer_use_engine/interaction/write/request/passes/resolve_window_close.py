# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Lower normal window close to its declared control, inside the write boundary."""

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
        if native.platform != "macos":
            raise local_failure("normal window close is currently implemented on macOS only", "input_unavailable")
        target = plan.request.target
        identity = WindowIdentity(target.root_window, target.surface, target.instance)
        # Refresh only technical facts, retaining the original exact identity.
        # Neither the foreground window nor a modal child becomes the close target.
        await refresh(controller, owner)
        require_identity(owner, identity, current=True)
        root = member_target(owner._instance, owner._instance.members[identity.window])
        if root.node is None:
            raise local_failure("the selected window has no accessible close control source", "input_unavailable")
        async with native.calls.open(root, 30000) as lease:
            button = await close_control(native, lease.token, root, lease.remaining)
        # Keep the requested window/intent for receipts and related observation;
        # existing click preparation, final native checks and effect tracking apply.
        return replace(plan, target=button, mechanism=Intent("click"))

    return resolve


stage = Pass.factory(
    name="ResolveWindowCloseControl",
    factory=create,
    contract=Contract(ActionPlan, ActionPlan, {Effect.QUERY, Effect.RESOURCE}),
)
