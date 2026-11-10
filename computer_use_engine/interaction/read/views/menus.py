# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Observe menus as separate native scopes, preserving their input context."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from computer_use.interaction.read.presentation.content import DisplayText, template
from computer_use.interaction.read.presentation.document import DisplayDocument
from computer_use.interaction.read.presentation.model import SnapshotRenderRequest
from computer_use.interaction.read.presentation.pipeline import PrepareSnapshotPipeline
from computer_use.native.scopes import RunningApplication
from computer_use.native.contracts import Binding
from computer_use.native.query.directory import select_surface
from computer_use.pipeline.core import Control
from computer_use.session.interfaces import SurfaceOwner, WindowIdentity
from computer_use.session.operations import current_operation_budget
from computer_use.session.state import AppState

from .publication import directory_text, publish, tree_document

if TYPE_CHECKING:
    from typing import Sequence

    from computer_use.interaction.controller import SessionController
    from computer_use.interaction.read.acquisition.menus.query import MenuRead
    from computer_use.interaction.read.facts.snapshot import SemanticSnapshot
    from computer_use.interaction.read.model import MenuAppObservation
    from computer_use.interaction.records import UIHandle
    from computer_use.types import Observation


def remember_menu(
    controller: SessionController, ui: UIHandle, menu: MenuRead, context: str | None
) -> tuple[tuple[object, ...], tuple[tuple[Binding, SemanticSnapshot], ...]]:
    """Retain the native snapshots and bindings for each collected menu source.

    Args:
        controller: Session service owner; this dependency does not create another execution
            route.
        ui: App or request scope whose instance and observation bounds constrain the operation.
    """
    owners, trees = [], []
    for part in menu.parts:
        (tree,) = controller.reader.number_snapshots((part.tree,))
        state = AppState(
            # The pipeline shares retained bindings with their owning service.
            # pylint: disable-next=protected-access
            str(ui._instance.selector),
            running=controller.state(ui).running,
            # pylint: disable-next=protected-access
            refs=ui._instance.refs,
            query_target=part.target,
            query_scope_kind=tree.scope_kind,
        )
        # pylint: disable-next=protected-access
        state.menu_window_identity = WindowIdentity.of(ui._instance.members[context]) if context is not None else None
        owner = SurfaceOwner(controller, state)
        # pylint: disable-next=protected-access
        controller.remember_observation(state, part.target, tree)
        # Menu references retain the exact source window independently of a
        # later menu read that may reuse the same application-menu surface.
        for element_ref in tree.refs:
            state.refs[element_ref]["menu_window_identity"] = state.menu_window_identity
        owners.append(owner)
        trees.append((part.target, tree))
    return tuple(owners), tuple(trees)


class InterfaceMenuPipeline:
    """Read menus within the selected App or Window interface scope.

    Resolve the handle's current window context and collect its menu sources or requested
    path. Retain source bindings and publish the combined menu observation within that
    interface scope.

    Attributes:
        controller: Session services providing interface context, native menus, and source
            retention.
    """

    def __init__(self, controller: SessionController) -> None:
        self.controller = controller
        self.preparation = PrepareSnapshotPipeline()

    async def run(
        self,
        ui: UIHandle,
        path: Sequence[str],
        *,
        observed: MenuAppObservation | None = None,
        preparation: dict | None = None,
        full: bool = False,
        on_observation=None,
    ) -> Observation:
        """Collect and publish menus within the handle's established interface scope.

        Args:
            ui: App or request scope whose instance and observation bounds constrain the
                operation.
            path: Ordered semantic lookup/menu components within the declared target scope.
            observed: Capture provenance or original observation used by the current stage.
        """
        # pylint: disable-next=protected-access
        controller, instance = self.controller, ui._instance
        budget = current_operation_budget()
        presentation_control = Control(
            remaining=budget.remaining if budget is not None else None,
            cancelled=budget.cancelled if budget is not None else None,
            call_id=budget.operation_id if budget is not None else None,
        )
        state = controller.state(ui)
        lines: list[str | DisplayText | DisplayDocument]
        # pylint: disable-next=protected-access
        if instance.anchor is None and ui._anchor is None and instance.members:
            # An application catalogue can be useful without a preferred
            # document. Application-menu observation does not grant an input
            # receiver to any of those independent windows.
            with controller.port.calls.claim(30000) as remaining:
                menu = await controller.reader.menus.collect(
                    state.running.application, remaining, path=path, context=None
                )
            context = None
            # pylint: disable-next=protected-access
            ui._context = context
            lines = directory_text(controller, ui, cast("DirectoryFacts", instance.directory))
        # pylint: disable-next=protected-access
        elif instance.anchor is None and ui._anchor is None:
            # NativeAppReader retains the established running identity and
            # performs the same final directory/session recheck as window reads.
            observed = observed or await controller.reader.read(
                state.running, menu_path=path or None, view="tree", overview=False
            )
            if observed.selection is not None:
                raise RuntimeError("windowless menu branch unexpectedly acquired a working window")
            instance.directory = observed.directory
            state.running = RunningApplication(observed.application, observed.name, observed.directory["session"])
            menu, context = observed.menu, None
            lines = [
                template("App: {0!r}.", observed.name),
                "No working window is bound; this is the application menu only.",
            ]
        else:
            # This is observation selection, not permission to deliver input.
            # A new App.read(menu_path=...) must not require a prior delivered
            # keyboard context, nor overwrite it until this result is returned.
            # pylint: disable-next=protected-access
            selected = select_surface(instance.directory, ui._anchor or instance.anchor.root.root_window)
            item = next(item for item in instance.members.values() if item["surface"] == selected.surface)
            target = Binding(instance.directory["session"], item["instance"], item["window"], item["surface"], None)
            with controller.port.calls.claim(30000) as remaining:
                menu = await controller.reader.menus.collect(
                    state.running.application, remaining, path=path, context=target
                )
                # Collection cannot turn a closed instance into an application
                # menu for an unrelated document.
                # pylint: disable-next=protected-access
                await controller.reader._discover(state.selection or instance.anchor, remaining)
            context = next(key for key, item in instance.members.items() if item["surface"] == target.surface)
            # pylint: disable-next=protected-access
            ui._context = context
            lines = directory_text(controller, ui, cast("DirectoryFacts", instance.directory))
        # pylint: disable-next=protected-access
        ui._context = context
        owners, trees = remember_menu(controller, ui, menu, context)
        # pylint: disable-next=protected-access
        ui._lookup_scopes = owners
        # pylint: disable-next=protected-access
        ui._menu_only = True
        lines.append("Scope: application menu.")
        for target, tree in trees:
            prepared = self.preparation.run(
                SnapshotRenderRequest(tree, compact=True, public_api=True), presentation_control
            )
            lines.append(tree_document(controller, target, tree, prepared=prepared))
        lines.extend(template("Menu discovery partial: {0}", gap) for gap in menu.discovery_gaps)
        if observed is not None and observed.launch:
            lines.append(template("Launch: {0}", observed.launch["detail"]))
        return publish(
            controller,
            ui,
            DisplayDocument.join(lines),
            trees=trees,
            full=full,
            # pylint: disable-next=protected-access
            scope="menu:" + state.application + ":" + str(ui._anchor) + ":" + repr(path),
            view="menu",
            directory=instance.directory,
            preparation=preparation,
            on_observation=on_observation,
        )


if TYPE_CHECKING:
    from computer_use.protocol.records import NativeDirectory as DirectoryFacts
