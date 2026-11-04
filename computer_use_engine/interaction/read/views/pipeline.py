# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Application discovery and fixed Window reads with bounded related content."""

from __future__ import annotations

import asyncio
from contextlib import nullcontext
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from computer_use.diagnostics import diagnostic, local_failure
from computer_use.errors import ComputerUseError
from computer_use.interaction.query.instances.membership import ancestor
from computer_use.interaction.query.instances.scope import adopt, allowed, refresh, resolve, retain_fixed_failure
from computer_use.interaction.read.acquisition.components.support import ObservationGap, collect_component
from computer_use.interaction.read.acquisition.images.fixed import FixedWindowImagePipeline
from computer_use.interaction.read.acquisition.loading import window_content_unexposed
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
from computer_use.native.scopes import FixedWindowInvalidated, FixedWindowUnconfirmed
from computer_use.session.events import observation_completed
from computer_use.session.interfaces import WindowIdentity
from computer_use.session.observation_data import observation_data, retain_observation_data

from .collection import CollectionPlan, components
from .menus import InterfaceMenuPipeline, remember_menu
from .observation_preparation import ObservationPreparationPipeline, annotate_preparation_failure
from .publication import capture_header, publish, related_window_summary, tree_document
from .related import RelatedRead
from .settling import same_window_content

if TYPE_CHECKING:
    from collections.abc import Sequence

    from computer_use.interaction.controller import SessionController
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
    """Present the component and its observed collection gap."""
    if concise and gap.component == "directory" and gap.code == "observation_incomplete":
        return template("Directory incomplete: {0}.", gap.detail)
    return template("{0}: {1}: {2}", gap.component, gap.code, gap.detail)


def _collection_gap(component: str, error: Exception) -> ObservationGap:
    return ObservationGap(
        component,
        error.code if isinstance(error, ComputerUseError) else "observation_incomplete",
        error.detail if isinstance(error, ComputerUseError) else str(error) or type(error).__name__,
    )


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
    if part.tree is not None or not same_window:
        state.query_snapshot = part.tree
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
                raise local_failure("the observation window is not confirmed in this instance")
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

    async def _collect(self, ui, view, full, menu_path, directory, first, preparation, related, on_observation=None):
        controller, instance = self.controller, ui._instance
        # Stage this observation's own lookup configuration, including a
        # catalogue-only result. Previous menu samples are not its content.
        ui._lookup_scopes, ui._menu_only = (), False
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
                bool(not directory["surfaces"] and not directory["stopReasons"] and state.running is not None and view != "image" and "nativeApplicationRoots" in controller.transport.capabilities)
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
                lines.append("Observable working window: unavailable.")
            if related is not None:
                lines.append(related_window_summary(related, directory, instance.windows))
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
                on_observation=on_observation,
            )
        permitted = allowed(ui)
        # pylint: disable-next=protected-access
        anchor = ui._anchor or instance.anchor.root.root_window
        if related is not None:
            anchor = related.preferred(directory, anchor)
        plan = CollectionPlan(anchor, view, related)
        plan.update(directory, permitted)
        principal, principal_error = plan.principal, plan.selection_error
        parts, notes, collection_gaps = [], [], []
        ui._lookup_scopes, ui._menu_only = (), False
        initial = True
        recheck_pending = False
        while True:
            work = plan.next()
            if work is None:
                if not recheck_pending:
                    break
                # Reconcile once after the currently known component batch,
                # not once for every child toolbar/tree/image. Any new member
                # or newly available AX root becomes concrete missing work.
                recheck_pending = False
                try:
                    directory = await refresh(controller, ui, accessibility=True)
                    permitted = allowed(ui)
                    plan.update(directory, permitted)
                except asyncio.CancelledError:
                    raise
                except Exception as error:  # noqa: BLE001 - optional recheck cannot erase captured content
                    collection_gaps.append(_collection_gap("directory", error))
                    break
                continue
            identity, member_view = work
            key = identity.window
            item = permitted[key]
            if initial and principal_error is None and ui._anchor is None:
                initial = False
                selected = controller.reader._selection(
                    instance.anchor.application, instance.anchor.name, directory, anchor
                )
                try:
                    observed = (
                        first if isinstance(first, AppObservation)
                        else await controller.reader.read(
                            selected, view=member_view, overview=False, source_directory=directory,
                            scope_managed=True, include_menus=related is None,
                        )
                    )
                except WindowSelectionRequired as error:
                    observed, principal_error = None, error
                except ComputerUseError as error:
                    if related is None:
                        raise
                    observed, principal_error = None, error
                    if isinstance(error, (FixedWindowInvalidated, FixedWindowUnconfirmed)):
                        # This exact selector already proved a lifetime or
                        # membership change. Preserve that evidence when the
                        # accompanying catalogue merely omits the old window.
                        retain_fixed_failure(instance, selected, error)
                    if getattr(error, "directory", None) is not None:
                        directory = error.directory
                        adopt(controller, ui, directory)
                        permitted = allowed(ui)
                if observed is not None:
                    adopt(controller, ui, observed.directory, instance.anchor)
                    directory, permitted = observed.directory, allowed(ui)
                plan.update(directory, permitted)
                if observed is not None:
                    item = next(
                        (member for member in permitted.values() if member["surface"] == observed.target.surface), None
                    )
                    if item is not None:
                        part = WindowPart(
                            member_target(instance, item), observed.tree, observed.image,
                            observed.menus, observed.gaps, observed,
                        )
                        parts.append(part)
                        plan.captured(item, part)
                        remember(controller, ui, part)
                        ui._context = item["window"] if observed.scope_confirmed else None
                    else:
                        # Exact old objects never acquire a replacement's body.
                        principal_error = local_failure(
                            "the observed window left the application catalogue during collection",
                            "observation_incomplete",
                        )
                    continue
            initial = False
            if key not in permitted or WindowIdentity.of(permitted[key]) != identity:
                continue
            item = permitted[key]
            part = await self.member(
                ui, item, member_view, max_nodes=DEFAULT_WINDOW_MAX_NODES if not parts else 200,
                component_budget_ms=2000 if related is not None else None,
            )
            previous = next((old for old in parts if old.target.surface == part.target.surface), None)
            if previous is not None:
                # A late discovery may add image work to an already captured tree.
                resolved = {name for name in ("tree", "image") if getattr(part, name) is not None}
                upgraded_tree_attempt = identity in plan.tree_upgrades and "tree" in components(member_view)
                if upgraded_tree_attempt:
                    # A fresh-root attempt supersedes the old no-root failure
                    # even if its new failure is local (for example its budget).
                    # Keep the current reason alongside the retained pixels.
                    resolved.add("tree")
                previous.gaps = tuple(gap for gap in previous.gaps if gap.component not in resolved)
                if part.tree is not None or upgraded_tree_attempt:
                    # The replacement attempt uses the current root. Do not
                    # retain the old no-root target or its source observation's
                    # collection gaps as this new tree's source.
                    previous.target, previous.source = part.target, part.source
                previous.tree = part.tree if part.tree is not None else previous.tree
                previous.image = part.image if part.image is not None else previous.image
                previous.gaps += part.gaps
                part = previous
            else:
                parts.append(part)
            plan.captured(item, part)
            remember(controller, ui, part)
            if related is not None:
                # Only automatic related reads follow member transitions.
                recheck_pending = True
        if related is not None:
            notes.append(related_window_summary(
                related, directory, instance.windows,
                refresh_incomplete=any(gap.component == "directory" for gap in collection_gaps),
            ))
        captured = {part.target.surface for part in parts}
        current_surfaces = {item["surface"] for item in permitted.values()}
        if related is not None and not any(
            part.target.surface in current_surfaces and (part.tree is not None or part.image is not None)
            for part in parts
        ):
            # Selection and acquisition can straddle a modal's closure. Use
            # the rechecked catalogue to recover the exact original target or
            # its still-live fixed owner within the same observation budget.
            unavailable = frozenset(part.target.root_window for part in parts)
            old = permitted.get(related.recovery_window(directory, unavailable))
            if old is not None and old["surface"] not in captured and len(parts) < 6:
                fallback_view = "both" if related.new_windows(directory) else view
                fallback = await self.member(ui, old, fallback_view, component_budget_ms=2000)
                identity = WindowIdentity.of(old)
                plan.requested[identity] = components(fallback_view)
                plan.attempted[identity] = components(fallback_view)
                parts.append(fallback)
                remember(controller, ui, fallback)
                captured.add(old["surface"])
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
                        if related.context_root is not None and old["window"] == related.context_root.window
                        else "The previous detail could not be delivered; the App's current window is shown."
                    )
        # Convenience context follows locally bound content. Ambiguity still
        # leaves every explicitly bound window readable.
        current_part = next((part for part in parts if part.target.root_window == plan.principal), None)
        if current_part is not None and (current_part.tree is not None or current_part.image is not None):
            principal, principal_error = plan.principal, plan.selection_error
            if principal_error is None and (related is not None or ui._anchor is not None):
                ui._context = principal
        original_part = next(
            (part for part in parts if related is not None and part.target.root_window == related.original.root_window),
            None,
        )
        needs_refresh = (
            related is not None and related.settle and not related.new_windows(directory)
            and original_part is not None
            and same_window_content(related.before_tree, original_part.tree)
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
            key = next((key for key, item in permitted.items() if item["surface"] == part.target.surface), None)
            if key is not None and key not in shown:
                shown.add(key)
                displayed_parts.append((key, part))
        displayed_parts.sort(key=lambda entry: entry[0] != plan.principal)
        delivered_gaps = tuple(
            (part.source.directory, part.gaps)
            for _, part in displayed_parts
            if view != "image" and part.tree is not None and part.source is not None
        )
        lines: list[str | DisplayText | DisplayDocument] = []
        screenshot, included_images = None, ()
        image_parts = [
            part for _, part in displayed_parts
            if part.image is not None or "image" in plan.requested.get(
                WindowIdentity(part.target.root_window, part.target.surface, part.target.instance), ()
            )
        ]
        if image_parts:
            from computer_use.interaction.read.acquisition.images.composite.pipeline import InterfaceImagePipeline

            try:
                image, image_notes = InterfaceImagePipeline(controller).run(ui, image_parts)
                ui._image, screenshot = image, image.png if image is not None else None
                if image is not None:
                    included_images = tuple(piece.window for piece in getattr(image, "pieces", ()))
                    if not included_images:
                        included_images = tuple(
                            part.target.root_window for part in image_parts
                            if part.image is not None and image.png is part.image.image
                        )
                lines += image_notes
            except Exception as error:  # noqa: BLE001 - image composition must not discard independent text content
                collection_gaps.append(_collection_gap("image", error))
        header = capture_header(
            controller, ui, directory, parts=displayed_parts, delivered_gaps=delivered_gaps,
            image_windows=included_images,
        )
        lines.insert(0, DisplayText("interface_header", (header,)))
        if isinstance(first, AppObservation) and first.launch:
            lines.insert(1, template("Launch: {0}", first.launch["detail"]))
        trees = []
        if any(part.tree is not None for _, part in displayed_parts):
            lines.append("Tree: full")
        elif any("tree" in requested for requested in plan.requested.values()):
            lines.append("Tree: unavailable")
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
            requested = plan.requested.get(
                WindowIdentity(part.target.root_window, part.target.surface, part.target.instance), ()
            )
            ax_status = (
                "collected" if part.tree is not None else "unavailable" if "tree" in requested else "not requested"
            )
            image_status = (
                "collected" if key in included_images else "unavailable" if "image" in requested else "not requested"
            )
            lines.append(f"AX: {ax_status}; Image: {image_status}.")
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
        try:
            coverage = plan.coverage(directory, parts, included_images)
        except Exception as error:  # noqa: BLE001 - accounting cannot veto already acquired content or the action
            coverage = ()
            collection_gaps.append(_collection_gap("coverage", error))
        for record in coverage:
            if record["missing"]:
                ref = next((ref for ref, key in instance.windows.items() if key == record["window"]), record["window"])
                collection_gaps.append(
                    ObservationGap(
                        "window",
                        "observation_incomplete",
                        f"Window {ref}: {', '.join(record['missing'])} content was not included in this observation.",
                    )
                )
        lines += [_gap_line(gap) for gap in collection_gaps]
        lines += notes
        # pylint: disable-next=protected-access
        scope = directory["application"] + (":app" if ui._anchor is None else ":window:" + ui._anchor) + ":" + view
        pending = []
        result = publish(
            controller,
            ui,
            DisplayDocument.join(lines),
            screenshot,
            trees=tuple(trees),
            full=full,
            scope=scope,
            directory=directory,
            preparation=preparation,
            content_available=bool(trees) or screenshot is not None,
            gaps=tuple(gap for part in parts for gap in part.gaps) + tuple(collection_gaps),
            window_coverage=coverage,
            context_owner=related.context_owner if related is not None else None,
            partial=bool(
                principal_error
                or directory["stopReasons"]
                or any(part.gaps for part in parts)
                or collection_gaps
            ),
            on_observation=pending.append if needs_refresh else on_observation,
        )
        if not needs_refresh:
            return result
        try:
            # A complete first observation is retained before optional work.
            # The budget is still the original suffix budget, never restarted.
            with controller.port.calls.claim(5000) as remaining:
                can_refresh = remaining() >= 500
                if can_refresh:
                    diagnostic({
                        "kind": "postwrite_observation_refresh", "reason": "unchanged_window_content",
                        "window": original_part.target.root_window, "wait_ms": 200, "refresh_limit": 1,
                    })
                    await controller.reader.reader.wait_for_change(
                        original_part.target, original_part.tree, remaining, delay_ms=200
                    )
            if can_refresh:
                refreshed_directory, refreshed_first = await resolve(controller, ui, view)
                refreshed_events = []
                refreshed = await self._collect(
                    ui, view, full, menu_path, refreshed_directory, refreshed_first, preparation,
                    replace(related, settle=False, before_tree=None),
                    on_observation=refreshed_events.append,
                )
                if not (observation_data(refreshed) or {}).get("content_available", True):
                    raise local_failure(
                        "the refreshed observation has no available content",
                        "observation_incomplete",
                    )
                for event in refreshed_events:
                    (on_observation or observation_completed)(event)
                return refreshed
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001 - optional refresh failures must preserve the first captured observation
            # A technical refresh cannot erase already captured content. Keep
            # its exact directory/interval and report the failed newer sample.
            metadata = observation_data(result)
            gap = ObservationGap(
                "refresh",
                "observation_refresh_failed",
                "The optional post-input refresh failed; the first captured observation is retained. "
                + (error.detail if isinstance(error, ComputerUseError) else str(error) or type(error).__name__),
            )
            retain_observation_data(result, **{**metadata, "partial": True, "gaps": metadata["gaps"] + (gap,)})
        for event in pending:
            (on_observation or observation_completed)(event)
        return result

    async def member(
        self, ui: UIHandle, item: SurfaceInfo, view: str, *, max_nodes: int = 200,
        component_budget_ms: int | None = None,
    ) -> WindowPart:
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
        generation = controller.port.connection.session_identity

        def failed(component, error):
            if controller.port.calls.retired or controller.port.connection.session_identity != generation:
                raise error
            gaps.append(_collection_gap(component, error))

        async def capture_image():
            nonlocal image
            try:
                captured = await FixedWindowImagePipeline(controller.port, controller.reader.images).arun(target)
                image = captured.image
                if captured.gap is not None:
                    gaps.append(captured.gap)
            except Exception as error:  # noqa: BLE001 - preserve the independently acquired tree
                failed("image", error)
        image_first = component_budget_ms is not None and view == "both"
        if image_first:
            await capture_image()
        if view != "image":
            try:
                # This exact-member component must leave room for pixels and
                # directory reconciliation within the enclosing call deadline.
                with (
                    controller.port.calls.limit(component_budget_ms)
                    if component_budget_ms is not None else nullcontext()
                ):
                    tree, gap = await collect_component(
                        controller.port, "tree",
                        lambda: controller.reader.reader.read(target, max_nodes=max_nodes, scope_kind="window"),
                    )
                if gap:
                    gaps.append(gap)
                if tree is not None:
                    (tree,) = controller.reader.number_snapshots((tree,))
            except Exception as error:  # noqa: BLE001 - independent optional component, preserving pixels and other windows
                tree = None
                failed("tree", error)
            if tree is not None and component_budget_ms is not None:
                try:
                    if window_content_unexposed(tree):
                        gaps.append(
                            ObservationGap(
                                "tree",
                                "observation_incomplete",
                                "AX content unavailable in this observation.",
                            )
                        )
                except Exception as error:  # noqa: BLE001 - a completeness annotation cannot invalidate captured facts
                    gaps.append(_collection_gap("coverage", error))
        if view != "tree" and not image_first:
            await capture_image()
        return WindowPart(target, tree, image, gaps=tuple(gaps))
