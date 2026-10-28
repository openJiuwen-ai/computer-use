"""V6 read: fixed instance scope -> bounded members -> one public observation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from computer_use.diagnostics import local_failure
from computer_use.errors import ComputerUseError
from computer_use.interaction.query.instances.membership import ancestor
from computer_use.interaction.query.instances.scope import adopt, allowed, resolve
from computer_use.interaction.read.acquisition.components.support import collect_component
from computer_use.interaction.read.model import (
    AppObservation,
    MenuAppObservation,
    WindowSelectionRequired,
)
from computer_use.interaction.read.presentation.content import DisplayText, join, template
from computer_use.interaction.read.presentation.content import json_value as quoted
from computer_use.interaction.read.presentation.document import DisplayDocument
from computer_use.interaction.read.presentation.model import SnapshotRenderRequest
from computer_use.interaction.read.presentation.pipeline import PrepareSnapshotPipeline
from computer_use.native.contracts import Binding
from computer_use.native.query.directory import select_surface
from computer_use.session.interfaces import WindowIdentity

from .menus import InterfaceMenuPipeline, remember_menu
from .publication import directory_text, publish, tree_document

if TYPE_CHECKING:
    from typing import Sequence

    from computer_use.interaction.controller import SessionController
    from computer_use.interaction.read.acquisition.components.support import ObservationGap
    from computer_use.interaction.read.acquisition.images.capture import WindowImage
    from computer_use.interaction.read.acquisition.menus.query import MenuRead
    from computer_use.interaction.read.facts.snapshot import SemanticSnapshot
    from computer_use.interaction.records import UIHandle
    from computer_use.protocol.records import SurfaceInfo
    from computer_use.session.interfaces import InstanceState
    from computer_use.types import Observation


@dataclass
class WindowPart:
    """Facts, pixels, and menus collected for one member of an interface scope.

    Attributes:
        target: Native identity of the element, window, or application being observed.
        tree: Semantic snapshot with retained native facts and display-reference bindings.
        image: Captured pixels and their original coordinate/identity metadata, when available.
        menus: Menu snapshots collected for the same application scope.
        gaps: Explicit collection failures or omissions retained in the observation.
        source: Full application observation used to construct this window part, when available.
    """

    target: Binding
    tree: SemanticSnapshot | None = None
    image: WindowImage | None = None
    menus: MenuRead | None = None
    gaps: tuple[ObservationGap, ...] = ()
    source: AppObservation | None = None


def member_target(instance: InstanceState, item: SurfaceInfo) -> Binding:
    anchor = cast("WorkingWindow", instance.anchor).root
    return Binding(anchor.session, anchor.instance, anchor.root_window, item["surface"], item["node"])


def remember(controller: SessionController, ui: UIHandle, part: WindowPart) -> None:
    """Retain snapshots and native bindings for the observed interface windows.

    Args:
        controller: Session service owner; this dependency does not create another execution
            route.
        ui: App or Window handle whose instance and observation bounds constrain the operation.
    """
    # The pipeline shares retained bindings with their owning service.
    # pylint: disable-next=protected-access
    instance = ui._instance
    window = next(key for key, item in instance.members.items() if item["surface"] == part.target.surface)
    owner = instance.owners[window]
    state = controller.state(owner)
    state.query_target = part.target
    state.query_scope_kind = "window"
    # The interface branch reconciles membership itself; an old current-modal
    # observation must not retarget a fixed member during a semantic lookup.
    state.selection, state.observation = None, None
    if part.tree is not None:
        # pylint: disable-next=protected-access
        controller._remember(state, part.target, part.tree)
        identity = WindowIdentity.of(instance.members[window])
        for ref in part.tree.refs:
            instance.refs[ref]["window_identity"] = identity
    # An explicit bounded window read observes its identity even if AX and
    # pixels are unavailable. Native keyboard routing still proves the current
    # receiver at dispatch; image input separately requires delivered pixels.
    instance.observed.add(window)


class InterfaceReadPipeline:
    """Read the confirmed windows in an App or Window interface scope.

    Establish the principal window, collect the bounded set of confirmed members, and retain
    each member's facts and menu bindings. Compose the requested tree and image observation
    with window relationships and partial-collection notes.

    Attributes:
        controller: Session services owning interface membership, native collection, and
            observation publication.
    """

    def __init__(self, controller: SessionController) -> None:
        self.controller = controller
        self.preparation = PrepareSnapshotPipeline()

    async def run(self, ui: UIHandle, view: str, full: bool, menu_path: Sequence[str] | None) -> Observation:
        """Read the handle's interface scope and publish its combined observation.

        Args:
            ui: App or Window handle whose instance and observation bounds constrain the
                operation.
            view: Requested observation components: tree, image, or both.
            full: Whether delivery requests a complete tree instead of an eligible diff.
            menu_path: Requested menu path; None keeps the ordinary window read.
        """
        # pylint: disable-next=protected-access
        controller, instance = self.controller, ui._instance
        directory, first = await resolve(controller, ui, view)
        if instance.anchor is None:
            state = controller.state(ui)
            matches_view = (
                True
                if (
                    not directory["surfaces"]
                    and not directory["stopReasons"]
                    and state.running is not None
                    and view != "image"
                    and "nativeApplicationRoots" in controller.transport.capabilities
                )
                else False
            )
            if matches_view:
                return await InterfaceMenuPipeline(controller).run(
                    ui, menu_path or (), observed=first if isinstance(first, MenuAppObservation) else None
                )
            choices = controller.interfaces.discovery.publish_choices(state.name or str(instance.selector), directory)
            lines = [
                template("App: {0!r}.", state.name or instance.selector),
                "Select an application instance with computer.app(id).",
                "Tree: unavailable; no working instance has been selected.",
            ]
            lines += [template("{0} {1!r}; computer.app({0})", item.id, item.display_name) for item in choices]
            if not choices:
                lines.append("No observable working window. Installed/running does not imply an input context.")
            if isinstance(first, dict) and "detail" in first:
                lines.append(template("Launch: {0}", first["detail"]))
            lines.extend(template("Directory partial: {0}", reason) for reason in directory["stopReasons"])
            return publish(
                controller,
                ui,
                DisplayDocument.join(lines),
                full=True,
                scope="choices:" + str(instance.selector),
                directory=directory,
            )
        if menu_path is not None:
            return await InterfaceMenuPipeline(controller).run(ui, menu_path)
        permitted = allowed(ui)
        # pylint: disable-next=protected-access
        anchor = ui._anchor or instance.anchor.root.root_window
        principal_error = None
        try:
            current = select_surface(directory, anchor)
            principal = next(key for key, item in permitted.items() if item["surface"] == current.surface)
        except (ComputerUseError, StopIteration) as error:
            principal, principal_error = anchor, error
        ordered = [principal, *[key for key in permitted if key != principal]]
        parts, notes = [], []
        # pylint: disable-next=protected-access
        ui._lookup_scopes = ()
        # pylint: disable-next=protected-access
        ui._menu_only = False
        for index, key in enumerate(ordered[:6]):
            if key not in permitted or any(part.target.surface == permitted[key]["surface"] for part in parts):
                continue
            item = permitted[key]
            # pylint: disable-next=protected-access
            if index == 0 and principal_error is None and ui._anchor is None:
                # pylint: disable-next=protected-access
                selected = controller.reader._selection(
                    instance.anchor.application, instance.anchor.name, directory, anchor
                )
                try:
                    observed = (
                        first
                        # pylint: disable-next=protected-access
                        if isinstance(first, AppObservation) and ui._anchor is None
                        else await controller.reader.read(selected, view=view, overview=False)
                    )
                except WindowSelectionRequired as error:
                    observed, principal_error = None, error
                if observed is not None:
                    adopt(controller, ui, observed.directory, instance.anchor)
                    directory, permitted = observed.directory, allowed(ui)
                if observed is not None and any(
                    member["surface"] == cast("AppObservation", observed).target.surface
                    for member in permitted.values()
                ):
                    item = next(
                        member
                        for member in permitted.values()
                        if member["surface"] == cast("AppObservation", observed).target.surface
                    )
                    part = WindowPart(
                        member_target(instance, item),
                        cast("AppObservation", observed).tree,
                        cast("AppObservation", observed).image,
                        cast("AppObservation", observed).menus,
                        cast("AppObservation", observed).gaps,
                        observed,
                    )
                    parts.append(part)
                    remember(controller, ui, part)
                    # pylint: disable-next=protected-access
                    ui._context = item["window"] if cast("AppObservation", observed).scope_confirmed else None
                    continue
                if observed is not None:
                    # A dialog can disappear during the principal read. Its
                    # captured surface must not be relabelled as a new member
                    # or fed to remember() after membership was reconciled.
                    principal_error = local_failure(
                        "the observed window left this instance during collection",
                        "inspect the current member observations before input",
                        "observation_incomplete",
                    )
                    continue
            part = await self.member(ui, item, view, max_nodes=1000 if index == 0 else 200)
            parts.append(part)
            remember(controller, ui, part)
        captured = {part.target.surface for part in parts}
        for key, item in permitted.items():
            if item["surface"] not in captured and len(parts) < 6:
                part = await self.member(ui, item, view)
                parts.append(part)
                remember(controller, ui, part)
                captured.add(item["surface"])
        if principal_error is not None:
            # pylint: disable-next=protected-access
            ui._context = None
            notes.append(
                "Current input window is not uniquely confirmed; explicit elements remain scoped to their windows."
            )
        # pylint: disable-next=protected-access
        elif parts and ui._anchor is not None:
            # pylint: disable-next=protected-access
            ui._context = principal
        for key in (key for key, item in permitted.items() if item["surface"] not in captured):
            ref = next(ref for ref, window in instance.windows.items() if window == key)
            notes.append(template("Window {0}: not expanded (read budget); use app.window({0}).read().", ref))
        lines: list[str | DisplayText | DisplayDocument] = list(directory_text(controller, ui, directory))
        if isinstance(first, AppObservation) and first.launch:
            lines.append(template("Launch: {0}", first.launch["detail"]))
        screenshot = None
        if view != "tree":
            from computer_use.interaction.read.acquisition.images.composite.pipeline import InterfaceImagePipeline

            image, image_notes = InterfaceImagePipeline(controller).run(ui, parts)
            ui._image, screenshot = image, image.png if image is not None else None
            lines += image_notes
        else:
            lines.append("Screenshot: not requested")
        trees, shown = [], set()
        if view != "image":
            lines.append("Tree: full")
        else:
            lines.append("Tree: not requested")
        for part in parts:
            key = next(key for key, item in permitted.items() if item["surface"] == part.target.surface)
            if key in shown:
                continue
            shown.add(key)
            ref = next(ref for ref, window in instance.windows.items() if window == key)
            lines.append(template("Window {0}: {1}", ref, quoted(permitted[key]["title"])))
            if part.source is not None:
                lines.extend(part.source.context_notes)
            blockers = [
                other
                for other in permitted.values()
                if other["modal"] is True and other["window"] != key and ancestor(directory, other["window"], key)
            ]
            if blockers:
                lines.append(
                    template(
                        "Blocked by: {0}",
                        join(
                            (
                                template("{0}", next(r for r, w in instance.windows.items() if w == item["window"]))
                                for item in blockers
                            ),
                            ", ",
                        ),
                    )
                )
            if part.tree is not None:
                trees.append((part.target, part.tree))
                prepared = self.preparation.run(SnapshotRenderRequest(part.tree, compact=True, public_api=True))
                lines.append(tree_document(controller, part.target, part.tree, prepared=prepared))
            elif view != "image":
                lines.append("Tree: unavailable for this window; use its image when available.")
            if part.menus is not None:
                lines.append("Application menu (context of this window):")
                owners, menu_trees = remember_menu(controller, ui, part.menus, key)
                ui._lookup_scopes += owners
                for target, tree in menu_trees:
                    trees.append((target, tree))
                    prepared = self.preparation.run(SnapshotRenderRequest(tree, compact=True, public_api=True))
                    lines.append(tree_document(controller, target, tree, prepared=prepared))
            lines += [
                template("{0}: {1}: {2}; next: {3}", gap.component, gap.code, gap.detail, gap.next) for gap in part.gaps
            ]
            if part.image is not None:
                from computer_use.protocol.captures import capture_metadata

                lines += [
                    template("Image note: {0}", note)
                    for note in capture_metadata(part.image.image).get("stopReasons", ())
                ]
        lines += notes
        # pylint: disable-next=protected-access
        scope = instance.anchor.root.surface + (":app" if ui._anchor is None else ":window:" + ui._anchor) + ":" + view
        return publish(
            controller,
            ui,
            DisplayDocument.join(lines),
            screenshot,
            trees=tuple(trees),
            full=full,
            scope=scope,
            directory=directory,
        )

    async def member(self, ui: UIHandle, item: SurfaceInfo, view: str, *, max_nodes: int = 200) -> WindowPart:
        """Collect one confirmed window member of the interface scope.

        Args:
            ui: App or Window handle whose instance and observation bounds constrain the
                operation.
            view: Requested observation components: tree, image, or both.
            max_nodes: Maximum number of native objects this collection may read.
        """
        controller = self.controller
        # pylint: disable-next=protected-access
        target = member_target(ui._instance, item)
        tree = image = None
        gaps = []
        if view != "image":
            tree, gap = await collect_component(
                controller.port,
                "tree",
                lambda: controller.reader.reader.read(target, max_nodes=max_nodes, scope_kind="window"),
            )
            if gap:
                gaps.append(gap)
            if tree is not None:
                (tree,) = controller.reader.number_snapshots((tree,))
        if view != "tree":
            image, gap = await collect_component(
                controller.port, "image", lambda: controller.reader.images.window(target)
            )
            if gap:
                gaps.append(gap)
        return WindowPart(target, tree, image, gaps=tuple(gaps))


if TYPE_CHECKING:
    from computer_use.native.scopes import WorkingWindow
