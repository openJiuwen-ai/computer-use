"""Resolve menu."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from computer_use.interaction.write.model import ResolutionFrame
from computer_use.interaction.write.resolution.checks import parts
from computer_use.interaction.write.resolution.menu import MenuContextQuery
from computer_use.interaction.write.routes.policy import validate_target
from computer_use.native.contracts import Knowledge
from computer_use.pipeline import Contract, Effect, Pass

if TYPE_CHECKING:
    from typing import Awaitable, Callable

    from computer_use.native.port import NativePort
    from computer_use.pipeline.core import Control


def create(*, native: NativePort) -> Callable[[ResolutionFrame, Control], Awaitable[ResolutionFrame]]:
    async def resolve_menu(frame: ResolutionFrame, _: object) -> ResolutionFrame:
        """Attach menu ancestry and submenu evidence to a native menu action.

        For a supported macOS menu action, establish and retain its ancestor bounds;
        inspect submenu state when opening a menu. Reinspect the bounded target and
        return facts containing the retained submenu state.
        """
        intent, target, operation, remaining = parts(frame)
        facts, memory = (frame.facts, frame.request.memory)
        scope = frame.scope
        if (
            "nativeMenuActions" in getattr(native, "capabilities", ())
            and facts.platform == "macos"
            and (facts.native_role in ("AXMenuBarItem", "AXMenuItem", "AXMenu"))
            and any((p.unit == "native_action" and p.supported == Knowledge.YES for p in facts.primitives))
        ):
            if memory.menu_context is None:
                query = MenuContextQuery(native, operation, target, remaining)
                if facts.application_menu is False:
                    within, has_submenu = (
                        memory.within,
                        await query.has_submenu() if intent.kind == "openMenu" else False,
                    )
                else:
                    within, has_submenu = await query.resolve(memory.within, inspect_submenu=intent.kind == "openMenu")
                memory = replace(memory, within=within, menu_context=(within, has_submenu))
                facts = await native.inspect(operation, target, remaining(), within=within)
                scope = {"within": within}
                if not facts.binding.same_target(target):
                    raise ValueError("Native menu inspection returned a different target")
                validate_target(facts, native.policy)
            facts = replace(facts, has_submenu=memory.menu_context[1])
        return replace(frame, facts=facts, scope=scope, request=replace(frame.request, memory=memory))

    return resolve_menu


stage = Pass.factory(
    name="MenuTargetPipeline",
    factory=create,
    contract=Contract(
        ResolutionFrame,
        ResolutionFrame,
        {Effect.QUERY},
        requires=("pinned original target", "active native lease"),
        failures=("query", "target boundary"),
    ),
)
