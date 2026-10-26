"""Format menu header."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from computer_use.interaction.read.app import window_summary_content
from computer_use.interaction.read.delivery.menu.model import PURE, MenuTextDraft
from computer_use.interaction.read.presentation.content import join, json_value as quoted, template
from computer_use.pipeline import Contract, Pass

if TYPE_CHECKING:
    from computer_use.pipeline.core import Control


def format_menu_header(value: MenuTextDraft, control: Control) -> MenuTextDraft:
    """Attach app, menu path, working-window, launch, and partial-directory context to the menu
    draft.
    """
    observed, windows = value.observed, value.windows
    request, menu, directory = observed.request, observed.menu, observed.directory
    application, selection, path, launch = request.application, request.selection, request.path, request.launch
    scope = "application menu" if menu.scope_kind == "applicationMenu" else "working-window menu"
    lines = [
        template("App: {}.", quoted(application["displayName"])),
        template("Tree: full ({} only)", scope),
        template("Menu: {}", join((quoted(title) for title in path), separator=" > ") if path else "menu bar"),
    ]
    if selection is not None:
        lines.append(
            template(
                "Working window: {}",
                next(ref for ref, target in windows.items() if target == selection.root.root_window),
            )
        )
    else:
        lines.append("No working window is bound; this is an application UI root.")
    if len(directory["surfaces"]) > 1:
        lines.append(template("Windows: {}", window_summary_content(directory, windows, selection=selection)))
    if launch:
        lines.append(template("Launch: {}", launch["detail"]))
    lines.extend(template("Directory partial: {}", reason) for reason in directory["stopReasons"])
    return replace(value, lines=tuple(lines))


stage = Pass.define(
    name="format_menu_header",
    function=format_menu_header,
    contract=Contract(MenuTextDraft, MenuTextDraft, PURE),
)
