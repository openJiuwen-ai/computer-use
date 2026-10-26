"""Reconcile an App query/input context when an observed modal closes.

Reconciliation follows the confirmed modal-parent chain back toward the
instance's fixed working root.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from computer_use.errors import ComputerUseError
from computer_use.interaction.read.acquisition.directory.service import select_surface
from computer_use.native.scopes import window_binding

if TYPE_CHECKING:
    from typing import NoReturn

    from computer_use.interaction.read.app import NativeAppReader
    from computer_use.interaction.read.model import AppObservation
    from computer_use.interaction.records import Remaining
    from computer_use.native.contracts import Binding
    from computer_use.native.scopes import WorkingWindow
    from computer_use.pipeline.core import Control


def refuse(code: str, detail: str) -> NoReturn:
    raise ComputerUseError(
        dict(
            code=code,
            effect="none",
            target="App context",
            detail=detail,
            next="read this App again before continuing; no input was sent",
        )
    )


async def app_context(
    reader: NativeAppReader,
    selection: WorkingWindow,
    observed: AppObservation,
    target: Binding,
    remaining: Remaining,
    *,
    _control: Control | None = None,
) -> Binding:
    """Resume an App context at the confirmed parent of a departed modal.

    Args:
        reader: Reader used to refresh the selected instance's window directory.
        selection: Fixed working instance and root-window identity.
        observed: Earlier observation containing the modal-parent relationships.
        target: Previously observed query/input binding to reconcile.
        remaining: Milliseconds remaining on the enclosing call.
        _control: Inherited pipeline diagnostics and call metadata.

    Returns:
        The original binding if its view remains current, or its confirmed parent
        binding after the modal closes.

    Raises:
        ComputerUseError: The current directory cannot establish the parent chain
            or the original instance identity.
    """
    previous_window = window_binding(target)
    if previous_window == selection.root:
        return target
    _, current = await reader._discover(selection, remaining, _control=_control)
    active = select_surface(current, selection.root.root_window)
    if window_binding(active) == previous_window:
        return target
    if current["stopReasons"]:
        refuse("observation_incomplete", "The window directory is partial; the previous modal view may still exist.")
    if any(item["parentState"] == "unknown" for item in current["surfaces"]):
        refuse("observation_incomplete", "Window ownership is unknown; the App context cannot be reconciled.")

    previous = {item["surface"]: item for item in observed.source_directory["surfaces"]}
    windows = {item["window"]: item for item in previous.values()}
    present = {item["surface"] for item in current["surfaces"]}
    surface, seen = target.surface, set()
    while surface != active.surface:
        if surface in seen or surface not in previous or surface in present:
            refuse("stale_target", "The observed view changed without a confirmed return to its previous parent.")
        seen.add(surface)
        item = previous[surface]
        if item["modal"] is not True or item["parentState"] != "known" or item["parent"] not in windows:
            refuse("observation_incomplete", "The previous view has no confirmed modal-parent chain.")
        parent = windows[item["parent"]]
        surface = parent["surface"]
    if window_binding(active) != replace(previous_window, surface=surface):
        refuse("stale_target", "The previous App context no longer belongs to the same working window.")
    if target.node is not None and active.node is None:
        refuse("observation_incomplete", "The resumed window has no accessible query root.")
    return active if target.node is not None else window_binding(active)
