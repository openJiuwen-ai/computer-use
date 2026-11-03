# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Application discovery and fixed Window reads with bounded related content."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from computer_use.diagnostics import local_failure
from computer_use.errors import ComputerUseError
from computer_use.interaction.query.instances.membership import ancestor
from computer_use.interaction.query.instances.scope import adopt, allowed, resolve
from computer_use.interaction.read.acquisition.components.support import collect_component
from computer_use.interaction.read.acquisition.images.fixed import FixedWindowImagePipeline
from computer_use.interaction.read.limits import DEFAULT_WINDOW_MAX_NODES
from computer_use.interaction.read.model import (
    AppObservation,
    MenuAppObservation,
    WindowSelectionRequired,
)
from computer_use.interaction.read.presentation.content import DisplayText, join, template
from computer_use.interaction.read.presentation.document import DisplayDocument
from computer_use.interaction.read.presentation.interface.disclosure.groups.model import WindowDisclosureContext
from computer_use.interaction.read.presentation.model import SnapshotRenderRequest
from computer_use.interaction.read.presentation.pipeline import PrepareSnapshotPipeline
from computer_use.native.contracts import Binding
from computer_use.native.query.directory import select_surface
from computer_use.session.interfaces import WindowIdentity

from .menus import InterfaceMenuPipeline, remember_menu
from .observation_preparation import ObservationPreparationPipeline, annotate_preparation_failure
from .publication import capture_header, publish, tree_document
from .related import RelatedRead

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
    return Binding(instance.directory["session"], item["instance"], item["window"], item["surface"], item["node"])


def _gap_line(gap: ObservationGap, *, concise: bool = False) -> DisplayText:
    """Expose the directory's actual gap; the Skill supplies the normal reread syntax."""
    if (
        concise
        and gap.component == "directory"
        and gap.code == "observation_incomplete"
        and gap.next == "inspect the directory again if the required window is missing"
    ):
        return template("Directory incomplete: {0}.", gap.detail)
    return template("{0}: {1}: {2}; next: {3}", gap.component, gap.code, gap.detail, gap.next)


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
    previous = state.query_target
    same_window = previous is not None and (
        previous.session,
        previous.instance,
        previous.root_window,
        previous.surface,
    ) == (part.target.session, part.target.instance, part.target.root_window, part.target.surface)
    # Visual directory collection deliberately omits AX roots. That absence
    # does not invalidate an already observed query binding for this exact
    # native window; lookup still validates its node when it is used.
    if part.target.node is not None or not same_window:
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

    async def run(
        self,
        ui: UIHandle,
        view: str,
        full: bool,
        menu_path: Sequence[str] | None,
        *,
        allow_foreground: bool = False,
        related: RelatedRead | None = None,
    ) -> Observation:
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
        instance = ui._instance
        preparation = None
        if allow_foreground and (ui._anchor is not None or instance.anchor is not None):
            permitted = allowed(ui)
            anchor = ui._anchor or instance.anchor.root.root_window
            current = select_surface(directory, anchor)
            item = next((item for item in permitted.values() if item["surface"] == current.surface), None)
            if item is None:
                raise local_failure(
                    "the observation window is not confirmed in this instance", "read its window choices"
                )
            preparation = await ObservationPreparationPipeline(controller.port).arun(member_target(instance, item))
        try:
            if preparation is not None:
                # Raising may change geometry, focus or modal membership. Reuse the
                # original instance and refresh before any pixels are collected.
                directory, _ = await resolve(controller, ui, view)
                first = None
            return await self._collect(ui, view, full, menu_path, directory, first, preparation, related)
        except BaseException as error:
            if preparation is not None:
                annotated = annotate_preparation_failure(error, preparation)
                if annotated is not error:
                    raise annotated from error
            raise

    async def _collect(self, ui, view, full, menu_path, directory, first, preparation, related):
        controller, instance = self.controller, ui._instance
        # Stage this observation's own lookup configuration, including a
        # catalogue-only result. Previous menu samples are not its content.
        ui._lookup_scopes, ui._menu_only = (), False
        if related is not None and related.new_windows(directory) and view == "tree":
            # A new peer may expose no useful AX content. Include its bounded
            # visual view in this same response instead of requiring another
            # model round trip solely to obtain executable observation.
            view = "both"
        if related is not None and ui._anchor is None and instance.anchor is None:
            preferred = related.preferred(directory, related.original.root_window)
            if preferred in instance.members:
                instance.anchor = controller.reader._selection(
                    directory["application"], instance.name, directory, preferred
                )
        if menu_path is not None:
            return await InterfaceMenuPipeline(controller).run(
                ui,
                menu_path,
                observed=first if isinstance(first, MenuAppObservation) else None,
                preparation=preparation,
            )
        if instance.anchor is None and ui._anchor is None:
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
            ui._context = None
            choices = controller.interfaces.windows.choices(directory, state.name or str(instance.selector))
            lines = [
                DisplayText("interface_header", (capture_header(controller, ui, directory, parts=()),)),
                "Select a window with computer.window(id) or app.window(id).",
                "Tree: not expanded; no unique observation window was selected.",
            ]
            if not choices:
                lines.append("No observable working window. Installed/running does not imply an input context.")
            if isinstance(first, dict) and "detail" in first:
                lines.append(template("Launch: {0}", first["detail"]))
            return publish(
                controller,
                ui,
                DisplayDocument.join(lines),
                full=True,
                scope="choices:" + str(instance.selector),
                directory=directory,
                content_available=False,
                partial=bool(directory["stopReasons"]),
            )
        permitted = allowed(ui)
        # pylint: disable-next=protected-access
        anchor = ui._anchor or instance.anchor.root.root_window
        if related is not None:
            anchor = related.preferred(directory, anchor)
        principal_error = None
        try:
            current = select_surface(directory, anchor)
            principal = next(key for key, item in permitted.items() if item["surface"] == current.surface)
        except (ComputerUseError, StopIteration) as error:
            principal, principal_error = anchor, error
        # Other independent application windows remain selectable in the header;
        # only the principal and its confirmed attached scope get detailed reads.
        ordered = [principal, *[key for key in permitted if key != principal and ancestor(directory, key, anchor)]]
        if related is not None:
            ordered.extend(key for key in related.new_windows(directory)[:2] if key not in ordered)
        parts, notes = [], []
        if related is not None:
            new_refs = [ref for ref, key in instance.windows.items() if key in related.new_windows(directory)]
            if new_refs:
                notes.append(
                    template(
                        "Newly discovered windows after the action: {0}. This does not establish causation.",
                        ", ".join(map(str, new_refs)),
                    )
                )
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
                        else await controller.reader.read(
                            selected, view=view, overview=False, source_directory=directory
                        )
                    )
                except WindowSelectionRequired as error:
                    observed, principal_error = None, error
                except ComputerUseError as error:
                    if related is None:
                        raise
                    observed, principal_error = None, error
                    if getattr(error, "directory", None) is not None:
                        directory = error.directory
                        adopt(controller, ui, directory)
                        permitted = allowed(ui)
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
                        "the observed window left the application catalogue during collection",
                        "inspect the current member observations before input",
                        "observation_incomplete",
                    )
                    continue
            if key not in permitted:
                continue
            item = permitted[key]
            part = await self.member(ui, item, view, max_nodes=DEFAULT_WINDOW_MAX_NODES if index == 0 else 200)
            parts.append(part)
            remember(controller, ui, part)
        captured = {part.target.surface for part in parts}
        if related is not None and not any(part.tree is not None or part.image is not None for part in parts):
            # Selection and acquisition can straddle a modal's closure. Use
            # the rechecked catalogue to recover the exact original target or
            # its still-live fixed owner within the same observation budget.
            old = permitted.get(related.recovery_window(directory))
            if old is not None and old["surface"] not in captured and len(parts) < 6:
                fallback = await self.member(ui, old, view)
                parts.append(fallback)
                remember(controller, ui, fallback)
                captured.add(old["surface"])
                ordered.append(old["window"])
                if fallback.tree is not None or fallback.image is not None:
                    ui._context = old["window"]
                    if ui._anchor is None:
                        instance.anchor = controller.reader._selection(
                            directory["application"], instance.name, directory, old["window"]
                        )
                    principal_error = None
                    notes.append(
                        "Preferred related-window detail was unavailable; the original window is shown."
                        if old["window"] == related.original.root_window
                        else "The previous detail could not be delivered; the surviving fixed Window is shown."
                        if related.context_root is not None
                        else "The previous detail could not be delivered; the App's current window is shown."
                    )
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
        # Select the actual document parts once. Header coverage and body delivery
        # must use the same windows, including their original collection objects.
        displayed_parts: list[tuple[str, WindowPart]] = []
        shown: set[str] = set()
        for part in parts:
            key = next(key for key, item in permitted.items() if item["surface"] == part.target.surface)
            if key not in shown:
                shown.add(key)
                displayed_parts.append((key, part))
        delivered_gaps = tuple(
            (part.source.directory, part.gaps)
            for _, part in displayed_parts
            if view != "image" and part.tree is not None and part.source is not None
        )
        header = capture_header(controller, ui, directory, parts=displayed_parts, delivered_gaps=delivered_gaps)
        lines: list[str | DisplayText | DisplayDocument] = [DisplayText("interface_header", (header,))]
        if isinstance(first, AppObservation) and first.launch:
            lines.append(template("Launch: {0}", first.launch["detail"]))
        screenshot = None
        if view != "tree":
            from computer_use.interaction.read.acquisition.images.composite.pipeline import InterfaceImagePipeline

            image, image_notes = InterfaceImagePipeline(controller).run(ui, parts)
            ui._image, screenshot = image, image.png if image is not None else None
            lines += image_notes
        trees = []
        if view != "image":
            lines.append("Tree: full")
        else:
            lines.append("Tree: not requested")
        recovery_prefix = 0
        for key, part in displayed_parts:
            ref = next(ref for ref, window in instance.windows.items() if window == key)
            if len(header.scoped) != 1:
                lines.append(
                    DisplayText("member_heading", (next(window for window in header.windows if window.ref == ref),))
                )
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
                member = next(window for window in header.windows if window.ref == ref)
                window_context = WindowDisclosureContext(
                    ref,
                    member.current,
                    member.modal,
                    tuple(next(r for r, w in instance.windows.items() if w == item["window"]) for item in blockers),
                    directory_complete=not directory["stopReasons"],
                    scope_confirmed=(
                        part.tree.native.session == part.target.session
                        and part.tree.native.roots == (part.target.node,)
                    ),
                )
                prepared = self.preparation.run(
                    SnapshotRenderRequest(
                        part.tree,
                        compact=True,
                        public_api=True,
                        window_context=window_context,
                        recovery_prefix_units=recovery_prefix,
                    )
                )
                recovery_prefix = max(
                    (
                        group.recovery_units
                        for root in prepared.roots
                        if root.groups is not None
                        for group in root.groups.groups
                    ),
                    default=recovery_prefix,
                )
                lines.append(tree_document(controller, part.target, part.tree, prepared=prepared))
            elif view != "image":
                lines.append("Tree: unavailable for this window; use its image when available.")
            if part.menus is not None:
                lines.append("Application menu:")
                owners, menu_trees = remember_menu(controller, ui, part.menus, key)
                ui._lookup_scopes += owners
                for target, tree in menu_trees:
                    trees.append((target, tree))
                    prepared = self.preparation.run(SnapshotRenderRequest(tree, compact=True, public_api=True))
                    lines.append(tree_document(controller, target, tree, prepared=prepared))
            lines += [_gap_line(gap, concise=True) for gap in part.gaps]
            if part.image is not None:
                from computer_use.protocol.captures import capture_metadata

                lines += [
                    template("Image note: {0}", note)
                    for note in capture_metadata(part.image.image).get("stopReasons", ())
                ]
        lines += notes
        # pylint: disable-next=protected-access
        scope = directory["application"] + (":app" if ui._anchor is None else ":window:" + ui._anchor) + ":" + view
        return publish(
            controller,
            ui,
            DisplayDocument.join(lines),
            screenshot,
            trees=tuple(trees),
            full=full,
            scope=scope,
            directory=directory,
            preparation=preparation,
            content_available=(
                bool(trees)
                if view == "tree"
                else screenshot is not None
                if view == "image"
                else bool(trees) or screenshot is not None
            ),
            gaps=tuple(gap for part in parts for gap in part.gaps),
            context_owner=related.context_owner if related is not None else None,
            partial=bool(
                principal_error
                or directory["stopReasons"]
                or any(part.gaps for part in parts)
                or len(displayed_parts) < len(set(ordered))
            ),
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
            captured = await FixedWindowImagePipeline(controller.port, controller.reader.images).arun(target)
            image = captured.image
            if captured.gap is not None:
                gaps.append(captured.gap)
        return WindowPart(target, tree, image, gaps=tuple(gaps))
