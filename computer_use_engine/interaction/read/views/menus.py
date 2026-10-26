"""Observe menus as separate native scopes, preserving their input context."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from computer_use.interaction.read.presentation.content import DisplayText, template
from computer_use.interaction.read.presentation.document import DisplayDocument
from computer_use.interaction.read.presentation.model import SnapshotRenderRequest
from computer_use.interaction.read.presentation.pipeline import PrepareSnapshotPipeline
from computer_use.native.scopes import RunningApplication
from computer_use.session.interfaces import SurfaceOwner
from computer_use.session.state import AppState

from .publication import directory_text, publish, tree_document

if TYPE_CHECKING:
    from typing import Sequence

    from computer_use.interaction.controller import SessionController
    from computer_use.interaction.read.acquisition.menus.query import MenuRead
    from computer_use.interaction.read.facts.snapshot import SemanticSnapshot
    from computer_use.interaction.read.model import MenuAppObservation
    from computer_use.interaction.records import UIHandle
    from computer_use.native.contracts import Binding
    from computer_use.types import Observation


def remember_menu(
    controller: SessionController, ui: UIHandle, menu: MenuRead, context: str | None
) -> tuple[tuple[object, ...], tuple[tuple[Binding, SemanticSnapshot], ...]]:
    """Retain the native snapshots and bindings for each collected menu source.

    Args:
        controller: Session service owner; this dependency does not create another execution
            route.
        ui: App or Window handle whose instance and observation bounds constrain the operation.
    """
    owners, trees = [], []
    for part in menu.parts:
        (tree,) = controller.reader.number_snapshots((part.tree,))
        state = AppState(
            str(ui._instance.selector),
            running=controller.state(ui).running,
            refs=ui._instance.refs,
            query_target=part.target,
            query_scope_kind=tree.scope_kind,
        )
        owner = SurfaceOwner(controller, state)
        controller._remember(state, part.target, tree)
        ui._instance.menu_contexts[part.target.surface] = context
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
        self, ui: UIHandle, path: Sequence[str], *, observed: MenuAppObservation | None = None
    ) -> Observation:
        """Collect and publish menus within the handle's established interface scope.

        Args:
            ui: App or Window handle whose instance and observation bounds constrain the
                operation.
            path: Ordered semantic lookup/menu components within the declared target scope.
            observed: Capture provenance or original observation used by the current stage.
        """
        controller, instance = self.controller, ui._instance
        state = controller.state(ui)
        lines: list[str | DisplayText | DisplayDocument]
        if instance.anchor is None:
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
            target = controller.interfaces.keyboard_context(ui, require_observed=False)
            with controller.port.calls.claim(30000) as remaining:
                menu = await controller.reader.menus.collect(
                    instance.anchor.application, remaining, path=path, context=target
                )
                # Collection cannot turn a closed instance into an application
                # menu for an unrelated document.
                await controller.reader._discover(instance.anchor, remaining)
            context = next(key for key, item in instance.members.items() if item["surface"] == target.surface)
            lines = directory_text(controller, ui, cast("DirectoryFacts", instance.directory))
        owners, trees = remember_menu(controller, ui, menu, context)
        ui._lookup_scopes = owners
        ui._menu_only = True
        lines.append("Tree: full; menuPath is read-only and does not open a menu.")
        for target, tree in trees:
            prepared = self.preparation.run(SnapshotRenderRequest(tree, compact=True, public_api=True))
            lines.append(tree_document(controller, target, tree, prepared=prepared))
        lines.extend(template("Menu discovery partial: {0}", gap) for gap in menu.discovery_gaps)
        if observed is not None and observed.launch:
            lines.append(template("Launch: {0}", observed.launch["detail"]))
        return publish(
            controller,
            ui,
            DisplayDocument.join(lines),
            trees=trees,
            full=True,
            scope="menu:" + state.application + ":" + str(ui._anchor) + ":" + repr(path),
            view="menu",
            directory=instance.directory,
        )


if TYPE_CHECKING:
    from computer_use.protocol.records import NativeDirectory as DirectoryFacts
