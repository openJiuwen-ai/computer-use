"""Select application menu root."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.read.acquisition.menus.model import (
    PURE,
    ApplicationMenuTarget,
    CollectedMenuRoots,
)
from computer_use.interaction.read.acquisition.menus.query import menu_failure
from computer_use.native.contracts import Binding
from computer_use.pipeline import Contract, Pass

if TYPE_CHECKING:
    from computer_use.pipeline.core import Control


def select_application_menu_root(value: CollectedMenuRoots, control: Control) -> ApplicationMenuTarget:
    """Resolve the unique observed menu-bar root and bind it to the request's application
    context.
    """
    request, roots = value.request, value.roots
    candidates = [item for item in roots["roots"] if item["kind"] == "menuBar"]
    if roots["stopReasons"]:
        raise menu_failure("observation_incomplete", "; ".join(roots["stopReasons"]))
    if len(candidates) != 1:
        raise menu_failure(
            "ambiguous_target" if candidates else "observation_incomplete",
            f"The application exposes {len(candidates)} observed menu-bar roots.",
        )
    root, context = candidates[0], request.context
    target = Binding(
        roots["session"],
        context.instance if context else root["instance"],
        context.root_window if context else None,
        root["surface"],
        root["node"],
    )
    return ApplicationMenuTarget(request, roots, target)


stage = Pass.define(
    name="select_application_menu_root",
    function=select_application_menu_root,
    contract=Contract(CollectedMenuRoots, ApplicationMenuTarget, PURE),
)
