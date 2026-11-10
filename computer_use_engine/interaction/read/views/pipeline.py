# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Application discovery and fixed Window reads with bounded related content."""

from __future__ import annotations

import time
from collections.abc import Mapping
from contextlib import nullcontext
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from computer_use.diagnostics import local_failure
from computer_use.errors import ComputerUseError
from computer_use.interaction.query.instances.membership import ancestor, interaction_contains
from computer_use.interaction.query.instances.scope import (
    adopt,
    allowed,
    display_selection,
    observe_application,
    refresh,
    resolve,
    retain_fixed_failure,
)
from computer_use.interaction.read.acquisition.components.support import ObservationGap, collect_component
from computer_use.interaction.read.acquisition.images.fixed import FixedWindowImagePipeline
from computer_use.interaction.read.acquisition.images.refresh import IMAGE_REFRESH_MINIMUM_MS
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
from computer_use.native.scopes import FixedWindowInvalidated, FixedWindowUnconfirmed, window_binding
from computer_use.pipeline.core import Control
from computer_use.session.interfaces import LookupContext, WindowIdentity
from computer_use.session.operations import OperationDeadlineExceeded, current_operation_budget
from computer_use.session.surface_content import content_kind, transient_content

from .application_content import collect_application_content, remember_application_content
from .capture_companions import CapturedSharingControl, companion_capture
from .collection import CollectionPlan, components, observation_members
from .menus import InterfaceMenuPipeline, remember_menu
from .observation_preparation import ObservationPreparationPipeline, annotate_preparation_failure
from .publication import capture_header, publish, related_focus_summary, related_window_summary, tree_document
from .related import RelatedRead, original_lifecycle, window_lifecycle

if TYPE_CHECKING:
    from collections.abc import Sequence

    from computer_use.interaction.controller import SessionController
    from computer_use.interaction.read.acquisition.images.capture import WindowImage
    from computer_use.interaction.read.acquisition.menus.query import MenuRead
    from computer_use.interaction.read.facts.snapshot import SemanticSnapshot
    from computer_use.interaction.records import UIHandle
    from computer_use.protocol.records import NativeDirectory, SurfaceInfo
    from computer_use.session.interfaces import InstanceState
    from computer_use.types import Observation


@dataclass
class WindowPart:
    """Facts, pixels, and menus collected for one member of an interface scope.

    Attributes:
        target: Original native acquisition and execution binding.
        display_identity: Confirmed member identity when the native binding is anchored at its parent.
        tree: Semantic snapshot with retained native facts and display-reference bindings.
        image: Captured pixels and their original coordinate/identity metadata, when available.
        menus: Menu snapshots collected for the same application scope.
        gaps: Explicit collection failures or omissions retained in the observation.
        source: Full application observation used to construct this window part, when available.
        snapshots_numbered: Whether tree and menu refs may be registered and publicly presented.
        local_detail: A bounded element capture whose query root remains the window.
    """

    target: Binding
    tree: SemanticSnapshot | None = None
    image: WindowImage | None = None
    menus: MenuRead | None = None
    gaps: tuple[ObservationGap, ...] = ()
    source: AppObservation | None = None
    image_directory: NativeDirectory | None = None
    capture_companion: CapturedSharingControl | None = None
    snapshots_numbered: bool = True
    display_identity: WindowIdentity | None = None
    local_detail: bool = False

    @property
    def identity(self) -> WindowIdentity:
        """The display member does not change the native binding."""
        return self.display_identity or WindowIdentity(
            self.target.root_window, self.target.surface, self.target.instance,
        )


def _matches_observed_member(directory: NativeDirectory, item: SurfaceInfo, target: Binding) -> bool:
    """Prove an exact member or a native parent-anchored modal view in this catalogue."""
    if directory["session"] != target.session or item["surface"] != target.surface:
        return False
    if WindowIdentity.of(item) == WindowIdentity(target.root_window, target.surface, target.instance):
        return True
    if directory["platform"] != "macos" or target.root_window is None or target.root_window == item["window"]:
        return False
    if item["modal"] is not True or not ancestor(directory, item["window"], target.root_window):
        return False
    try:
        selected = select_surface(directory, target.root_window)
    except ComputerUseError:
        return False
    return window_binding(selected) == window_binding(target)


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
        ui: App or request scope whose instance and observation bounds constrain the operation.
    """
    # The pipeline shares retained bindings with their owning service.
    # pylint: disable-next=protected-access
    instance = ui._instance
    identity = part.identity
    window = identity.window
    item = instance.members.get(window)
    if item is None or WindowIdentity.of(item) != identity:
        return
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
        state.query_target = member_target(instance, item) if part.local_detail else part.target
    if part.tree is not None or not same_window:
        state.query_snapshot = part.tree
    state.query_scope_kind = "window"
    # The interface branch reconciles membership itself; an old current-modal
    # observation must not retarget a fixed member during a semantic lookup.
    state.selection, state.observation = None, None
    if part.tree is not None and part.snapshots_numbered:
        # pylint: disable-next=protected-access
        controller.remember_observation(state, part.target, part.tree)
        for element_ref in part.tree.refs:
            instance.refs[element_ref]["window_identity"] = identity
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
        on_observation=None,
    ) -> Observation:
        """Read the handle's interface scope and publish its combined observation.

        Args:
            ui: App or request scope whose instance and observation bounds constrain the
                operation.
            view: Requested observation components: tree, image, or both.
            full: Whether delivery requests a complete tree instead of an eligible diff.
            menu_path: Requested menu path; None keeps the ordinary window read.
        """
        # pylint: disable-next=protected-access
        controller, instance = self.controller, ui._instance
        try:
            directory, first = await resolve(controller, ui, view)
        except ComputerUseError as error:
            await self._reconcile_close_application(related, instance.directory, error)
            raise
        # pylint: disable-next=protected-access
        instance = ui._instance
        preparation = None
        # pylint: disable-next=protected-access
        if allow_foreground and (ui._anchor is not None or instance.anchor is not None):
            permitted = allowed(ui)
            # pylint: disable-next=protected-access
            anchor = ui._anchor or instance.anchor.root.root_window
            options = {"follow_modal": False} if getattr(ui, "_strict_window", False) else {}
            current = select_surface(directory, anchor, **options)
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
            return await self._collect(
                ui, view, full, menu_path, directory, first, preparation, related, on_observation,
            )
        except BaseException as error:
            if preparation is not None:
                annotated = annotate_preparation_failure(error, preparation)
                if annotated is not error:
                    raise annotated from error
            raise

    async def _reconcile_close_application(self, related, directory, error):
        """Resolve a stale close-family catalogue without reviving its current choices."""
        if related is None or not related.window_family_only or directory is None:
            return
        if directory["platform"] != "macos" or not isinstance(error, ComputerUseError):
            return
        if error.code != "stale_target" or isinstance(error, FixedWindowInvalidated):
            return
        session, application = related.original.session, related.window_family_application
        if (directory["session"], directory["application"]) != (session, application):
            return
        controller = self.controller
        if controller.transport.session != session:
            return
        if original_lifecycle(related.original, directory, related.original_window) == "closed":
            raise FixedWindowInvalidated(
                related.original, "The original window ended, and its current directory is unavailable."
            )
        windows = controller.interfaces.windows
        if not windows.application_ended(session, application):
            try:
                # This metadata lookup shares the enclosing write-observation
                # deadline and never binds or launches a replacement instance.
                applications = await controller.reader.directory.applications(include_ended=True)
            except (ComputerUseError, TimeoutError):
                applications = None
            if applications is not None and applications["session"] == session and application in applications.get(
                "endedApplications", ()
            ):
                windows.retire_applications(session, (application,))
        if windows.application_ended(session, application):
            raise FixedWindowInvalidated(related.original, "The original application instance exited.")
        raise local_failure(
            "The original application directory is unavailable. The window lifetime is unconfirmed.",
            "observation_incomplete",
        )

    async def _collect(self, ui, view, full, menu_path, directory, first, preparation, related, on_observation=None):
        collected_started = time.monotonic()
        budget = current_operation_budget()
        presentation_control = Control(
            remaining=budget.remaining if budget is not None else None,
            cancelled=budget.cancelled if budget is not None else None,
            call_id=budget.operation_id if budget is not None else None,
        )
        # pylint: disable-next=protected-access
        controller, instance = self.controller, ui._instance
        # Stage this observation's own lookup configuration, including a
        # catalogue-only result. Previous menu samples are not its content.
        ui._lookup_scopes, ui._menu_only = (), False
        window_family_only = related is not None and related.window_family_only
        if window_family_only:
            # pylint: disable-next=protected-access
            ui._context = None

        def content_members():
            if getattr(ui, "_strict_window", False):
                return allowed(ui)
            if window_family_only:
                return related.content_members(directory, allowed(ui))
            return observation_members(ui, related)

        related_app_scope = related is not None and not window_family_only
        # pylint: disable-next=protected-access
        if related_app_scope and ui._anchor is None and instance.anchor is None:
            preferred = related.preferred(directory, related.original.root_window)
            if preferred in instance.members:
                instance.anchor = controller.reader.select_working_window(
                    directory["application"], instance.name, directory, preferred
                )
        if menu_path is not None:
            return await InterfaceMenuPipeline(controller).run(
                ui,
                menu_path,
                observed=first if isinstance(first, MenuAppObservation) else None,
                preparation=preparation,
                full=full,
                on_observation=on_observation,
            )

        async def application_content():
            # pylint: disable-next=protected-access
            if window_family_only or ui._anchor is not None or view == "image":
                return None, None
            if "nativeApplicationContent" not in controller.transport.capabilities or any(
                surface["node"] is not None for surface in directory["surfaces"]
            ):
                return None, None
            content, gap = await collect_application_content(controller, directory["application"], presentation_control)
            if content is None:
                return None, gap
            target, tree = content
            try:
                (tree,) = controller.reader.number_snapshots((tree,), _control=presentation_control)
                prepared = self.preparation.run(
                    SnapshotRenderRequest(tree, compact=True, public_api=True), presentation_control
                )
            except OperationDeadlineExceeded as error:
                return None, _collection_gap("application content", error)
            owner = remember_application_content(controller, ui, target, tree)
            return (target, tree, tree_document(controller, target, tree, prepared=prepared), owner), gap

        content_gap = None
        # pylint: disable-next=protected-access
        if not window_family_only and instance.anchor is None and ui._anchor is None:
            state = controller.state(ui)
            launch = first.launch if isinstance(first, (AppObservation, MenuAppObservation)) else first
            # pylint: disable-next=protected-access
            ui._context = None
            content, content_gap = await application_content()
            if content is not None:
                target, tree, document, owner = content
                lines = [
                    DisplayText("interface_header", (capture_header(controller, ui, directory, parts=()),)),
                    "Application content (observation only)",
                    "Tree: full",
                    document,
                ]
                if view == "both":
                    lines.append("Image: unavailable for application content.")
                if isinstance(launch, Mapping) and "detail" in launch:
                    lines.append(template("Launch: {0}", launch["detail"]))
                return publish(
                    controller, ui, DisplayDocument.join(lines), trees=((target, tree),), full=full,
                    scope=target.surface + ":" + view, directory=directory, content_owner=owner,
                    partial=bool(directory["stopReasons"]), on_observation=on_observation,
                )
            matches_view = (
                bool(
                    not directory["surfaces"] and not directory["stopReasons"] and state.running is not None
                    and view != "image" and "nativeApplicationRoots" in controller.transport.capabilities
                )
            )
            if matches_view:
                if not isinstance(first, MenuAppObservation):
                    # A starting application can publish its first window between
                    # these reads. Continue with the retained application and let
                    # its actual observation select the window or menu branch.
                    directory, first = await observe_application(controller, ui, state.running, view)
                    # pylint: disable-next=protected-access
                    instance = ui._instance
                    if (
                        isinstance(first, AppObservation) and instance.anchor is not None
                        and first.selection.root != instance.anchor.root
                    ):
                        first = first.launch
                if not directory["surfaces"] and isinstance(first, MenuAppObservation):
                    return await InterfaceMenuPipeline(controller).run(
                        ui, (), observed=first, full=full, on_observation=on_observation,
                    )
        # pylint: disable-next=protected-access
        if not window_family_only and instance.anchor is None and ui._anchor is None:
            state = controller.state(ui)
            launch = first.launch if isinstance(first, (AppObservation, MenuAppObservation)) else first
            choices = controller.interfaces.windows.choices(directory, state.name or str(instance.selector))
            lines = [
                DisplayText("interface_header", (capture_header(controller, ui, directory, parts=()),)),
                "Tree: not expanded; no unique observation window was selected.",
            ]
            if not choices:
                lines.append("Observable working window: unavailable.")
            if related is not None:
                lines.append(related_window_summary(related, directory, instance.windows))
            if isinstance(launch, Mapping) and "detail" in launch:
                lines.append(template("Launch: {0}", launch["detail"]))
            if content_gap is not None:
                lines.append(_gap_line(content_gap))
            return publish(
                controller,
                ui,
                DisplayDocument.join(lines),
                full=True,
                scope="choices:" + str(instance.selector),
                directory=directory,
                content_available=False,
                partial=bool(directory["stopReasons"] or content_gap),
                gaps=(content_gap,) if content_gap is not None else (),
                on_observation=on_observation,
            )
        permitted = content_members()
        # A fixed close must not fall through to the App's new display anchor.
        # pylint: disable-next=protected-access
        anchor = related.original.root_window if window_family_only else ui._anchor or instance.anchor.root.root_window
        if related is not None:
            anchor = related.preferred(directory, anchor)
        plan = CollectionPlan(anchor, view, related, strict_window=getattr(ui, "_strict_window", False))
        plan.update(directory, permitted)
        principal, principal_error = plan.principal, plan.selection_error
        parts, notes, collection_gaps = [], [], []
        ui._lookup_scopes, ui._menu_only = (), False
        initial = True
        recheck_pending = False
        reconciled_captures = None
        change_waited = False
        if related is not None and related.seek_editor and principal_error is None:
            editor = await self._related_editor(ui, plan, directory, permitted)
            if editor is not None:
                item = permitted[editor.identity.window]
                parts.append(editor)
                plan.captured(item, editor)
                remember(controller, ui, editor)
                initial, recheck_pending, change_waited = False, True, True
        # Only an epoch sampled before this component batch proves which
        # notifications its capture may cover. A later catalogue reconciliation
        # can see an event whose tree/pixels we have not acquired yet.
        change_directory = directory
        while True:
            plan.reuse_attached_content(parts, permitted)
            work = plan.next()
            if work is None:
                can_wait_for_change = not recheck_pending and not change_waited and related is not None
                has_change_baseline = (
                    can_wait_for_change and related.changes is not None and related.changes.baseline is not None
                )
                if has_change_baseline and not related.new_windows(directory):
                    change_waited = True
                    original = None
                    for candidate in parts:
                        if (
                            candidate.target.root_window, candidate.target.surface, candidate.target.instance
                        ) == (related.original.root_window, related.original.surface, related.original.instance):
                            original = candidate
                            break
                    if related.changes.needs_observation(original):
                        try:
                            changed = await related.changes.wait(
                                controller, change_directory,
                                collection_ms=int((time.monotonic() - collected_started) * 1000),
                            )
                            if changed:
                                directory = await refresh(controller, ui, accessibility=True)
                                permitted = content_members()
                                plan.update(directory, permitted)
                                if not related.new_windows(directory) and original is not None:
                                    identity = WindowIdentity(
                                        original.target.root_window, original.target.surface, original.target.instance
                                    )
                                    plan.changed(identity, components(view))
                                continue
                        except Exception as error:  # noqa: BLE001 - the first capture remains available
                            collection_gaps.append(_collection_gap("changes", error))
                capture_changes = (
                    {part.image.target for part in parts if part.image is not None and part.image.scope_changed}
                    if related is None and view != "tree" else set()
                )
                capture_recheck = bool(capture_changes) and reconciled_captures is None
                if not recheck_pending and not capture_recheck:
                    if reconciled_captures is not None and capture_changes - reconciled_captures:
                        collection_gaps.append(ObservationGap(
                            "directory", "observation_incomplete",
                            "Window scope changed again after its bounded capture refresh.",
                        ))
                    break
                # Reconcile once after the currently known component batch,
                # not once for every child toolbar/tree/image. Any new member
                # or newly available AX root becomes concrete missing work.
                recheck_pending = False
                if capture_recheck:
                    reconciled_captures = capture_changes
                try:
                    directory = await refresh(controller, ui, accessibility=related is not None or view != "image")
                    permitted = content_members()
                    plan.update(directory, permitted)
                except Exception as error:  # noqa: BLE001 - optional recheck cannot erase captured content
                    await self._reconcile_close_application(related, directory, error)
                    collection_gaps.append(_collection_gap("directory", error))
                    break
                continue
            identity, member_view = work
            key = identity.window
            item = permitted[key]
            has_initial_selection = related is not None and related.selection_target is not None and initial
            original_selected_member = (
                has_initial_selection and principal_error is None and identity == related.original_window
            )
            same_selection_scope = (
                original_selected_member and not related.new_windows(directory)
                and directory.get("focusedWindow") in (None, key)
            )
            if same_selection_scope and not any(
                other["window"] != key and (other["modal"] is True or transient_content(other))
                for other in permitted.values()
            ):
                selected = await self._selected_item(ui, item, related.selection_target)
                if selected is not None:
                    parts.append(selected)
                    plan.captured(item, selected)
                    remember(controller, ui, selected)
                    initial, recheck_pending, change_waited = False, True, True
                    continue
            # The App reader can follow another modal from its working root.
            # A scoped close and menu evidence need the scheduled member's own root.
            # A new independent modal selected as its own root needs image-first
            # collection. Its exact AX root can precede a visual catalogue mapping.
            new_modal_root = (
                related is not None and member_view == "both" and key == anchor == plan.principal
                and directory["platform"] == "macos" and directory["session"] == related.original.session
                and item["modal"] is True and item["parentState"] == "none" and item["node"] is not None
                and identity not in related.known_windows
            )
            # pylint: disable-next=protected-access
            initial_app_root = initial and not window_family_only and principal_error is None and ui._anchor is None
            if initial_app_root and identity != plan.menu_origin and not new_modal_root:
                initial = False
                selected = controller.reader.select_working_window(
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
                    fixed_failure = isinstance(error, (FixedWindowInvalidated, FixedWindowUnconfirmed))
                    changed_directory = getattr(error, "directory", None)
                    app_reselection = (
                        fixed_failure and changed_directory is not None
                        and changed_directory["session"] == instance.session
                        and changed_directory["application"] == instance.selector
                    )
                    if related is None and not app_reselection:
                        raise
                    observed, principal_error = None, error
                    if fixed_failure:
                        # This exact selector already proved a lifetime or
                        # membership change. Preserve that evidence when the
                        # accompanying catalogue merely omits the old window.
                        retain_fixed_failure(instance, selected, error)
                    if changed_directory is not None:
                        directory = changed_directory
                        adopt(controller, ui, directory)
                        permitted = observation_members(ui, related)
                    if related is None and app_reselection:
                        # This fixed selector was an internal acquisition for
                        # an App read, not the public handle's identity. Reuse
                        # the newly returned catalogue to observe its current
                        # primary; old Window handles retain the failed binding.
                        instance.anchor = display_selection(controller, instance, directory)
                        if instance.anchor is not None:
                            anchor = plan.anchor = instance.anchor.root.root_window
                            principal, principal_error = anchor, None
                            # pylint: disable-next=protected-access
                            ui._context = anchor
                if observed is not None:
                    adopt(controller, ui, observed.directory, instance.anchor)
                    directory, permitted = observed.directory, observation_members(ui, related)
                plan.update(directory, permitted)
                if observed is not None:
                    source = observed.source_directory
                    source_members = []
                    if source["application"] == directory["application"] == observed.selection.application:
                        source_members = [
                            member for member in source["surfaces"]
                            if _matches_observed_member(source, member, observed.target)
                        ]
                    item = None
                    if len(source_members) == 1:
                        original = source_members[0]
                        identity = WindowIdentity.of(original)
                        current = permitted.get(identity.window)
                        if (
                            current is not None and WindowIdentity.of(current) == identity
                            and _matches_observed_member(directory, current, observed.target)
                        ):
                            item = current
                        elif not observed.scope_confirmed and window_lifecycle(identity, directory) in (
                            "unknown", "closed",
                        ):
                            # An omitted or ended exact member can still be
                            # shown as history, without granting current scope.
                            item = original
                    if item is not None:
                        part = WindowPart(
                            observed.target, observed.tree, observed.image,
                            observed.menus, observed.gaps, observed,
                            image_directory=observed.source_directory if observed.image is not None else None,
                            snapshots_numbered=observed.snapshots_numbered, display_identity=WindowIdentity.of(item),
                        )
                        parts.append(part)
                        plan.captured(item, part)
                        current_member = permitted.get(item["window"])
                        if current_member is not None and WindowIdentity.of(current_member) == WindowIdentity.of(item):
                            remember(controller, ui, part)
                        # pylint: disable-next=protected-access
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
            companion = (
                companion_capture(item, directory, parts)
                # pylint: disable-next=protected-access
                if ui._anchor is None and not full and identity != plan.menu_origin else None
            )
            part = None
            if companion is not None:
                from computer_use.interaction.read.facts.capture_companion import (
                    SHARING_CONTROL_FIELDS,
                    sharing_control_signature,
                )

                probe = await self.member(
                    ui, item, "tree", max_nodes=8, max_depth=2, fields=list(SHARING_CONTROL_FIELDS),
                    component_budget_ms=2000 if related is not None else None,
                )
                signature = (
                    sharing_control_signature(probe.tree.native, probe.target.node)
                    if probe.tree is not None and probe.snapshots_numbered else None
                )
                if signature is not None:
                    probe.capture_companion = CapturedSharingControl(companion, *signature)
                    plan.fold_capture_status(identity)
                    part = probe
            if part is None:
                part = await self.member(
                    ui, item, member_view,
                    max_nodes=DEFAULT_WINDOW_MAX_NODES if not parts or identity == plan.menu_origin else 200,
                    component_budget_ms=2000 if related is not None else None,
                )
            previous = next((old for old in parts if old.identity == part.identity), None)
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
                    previous.snapshots_numbered = part.snapshots_numbered
                previous.tree = part.tree if part.tree is not None else previous.tree
                previous.image = part.image if part.image is not None else previous.image
                if part.image is not None:
                    previous.image_directory = part.image_directory
                previous.gaps += part.gaps
                part = previous
            else:
                parts.append(part)
            plan.captured(item, part)
            remember(controller, ui, part)
            if related is not None:
                # Only automatic related reads follow member transitions.
                recheck_pending = True
        empty_related_catalogue = (
            # pylint: disable-next=protected-access
            related is not None and ui._anchor is None
            and directory.get("rootDiscoveryComplete") is True
            and not directory["surfaces"] and not directory["stopReasons"]
        )
        if empty_related_catalogue and not collection_gaps and not any(
            part.tree is not None or part.image is not None for part in parts
        ):
            # A closing window can lose AX before its WindowServer animation
            # ends. The final complete catalogue is now empty: failed attempts
            # contain no historical body to retain as a phantom current card.
            # This is a presentation fact, never proof of native destruction.
            ui._context, ui._image, instance.anchor = None, None, None
            lifetime = original_lifecycle(related.original, directory, related.original_window)
            lines = [
                DisplayText("interface_header", (capture_header(controller, ui, directory, parts=()),)),
                "Current catalogue: no observable windows.",
                "Original window lifetime: closed." if lifetime == "closed"
                else "Original window lifetime: unconfirmed.",
            ]
            previous_failures = tuple(dict.fromkeys(
                f"{gap.component} [{gap.code}]: {gap.detail}" for part in parts for gap in part.gaps
            ))
            if previous_failures:
                lines.append("Earlier window reads during the transition: " + "; ".join(previous_failures))
            lines.append(related_window_summary(related, directory, instance.windows))
            return publish(
                controller, ui, DisplayDocument.join(lines), full=full,
                scope=directory["application"] + ":app:" + view, directory=directory,
                preparation=preparation, content_available=True,
                window_coverage=plan.coverage(directory, parts),
                context_owner=related.context_owner, on_observation=on_observation,
            )
        if related is not None:
            # Only the newly focused principal can use this one optional
            # capture, after the related read's existing directory rechecks.
            member = permitted.get(plan.principal)
            if member is not None and plan.selection_error is None and plan.principal == directory.get("focusedWindow"):
                identity = WindowIdentity.of(member)
                if identity not in related.known_windows:
                    part = next((item for item in parts if item.identity == identity), None)
                    if part is not None:
                        await self._refresh_new_native_image(part, directory)
            notes.append(related_window_summary(
                related, directory, instance.windows,
                refresh_incomplete=any(gap.component == "directory" for gap in collection_gaps),
            ))
        captured = {part.identity for part in parts}
        current_identities = {WindowIdentity.of(item) for item in directory["surfaces"]}
        if related is not None and not any(
            (part.tree is not None or part.image is not None)
            and part.identity in current_identities
            for part in parts
        ):
            # Historical content proves acquisition, not a current App view.
            # A popup can remain alive while leaving the current directory.
            # Keep its facts and observe the actual surviving scope using the
            # same deadline and original requested components, without input replay.
            unavailable = frozenset(part.identity.window for part in parts)
            old = permitted.get(related.recovery_window(directory, unavailable))
            if old is not None and WindowIdentity.of(old) not in captured and len(parts) < 6:
                fallback_view = "both" if view != "tree" and related.new_windows(directory) else view
                fallback = await self.member(ui, old, fallback_view, component_budget_ms=2000)
                identity = WindowIdentity.of(old)
                plan.requested[identity] = components(fallback_view)
                plan.attempted[identity] = components(fallback_view)
                parts.append(fallback)
                plan.captured(old, fallback)
                plan.order = tuple(dict.fromkeys((*plan.order, identity)))
                remember(controller, ui, fallback)
                if fallback.tree is not None or fallback.image is not None:
                    # pylint: disable-next=protected-access
                    ui._context = old["window"]
                    # pylint: disable-next=protected-access
                    if ui._anchor is None:
                        instance.anchor = controller.reader.select_working_window(
                            directory["application"], instance.name, directory, old["window"]
                        )
                    principal_error = None
                    plan.anchor, plan.principal, plan.selection_error = old["window"], old["window"], None
                    notes.append(
                        "The action's original window closed; the current related window is shown."
                        if original_lifecycle(related.original, directory, related.original_window) == "closed"
                        else
                        "Preferred related-window detail was unavailable; the original window is shown."
                        if old["window"] == related.original.root_window
                        else "The previous detail could not be delivered; the surviving fixed Window is shown."
                        if related.context_root is not None and old["window"] == related.context_root.window
                        else "The previous detail could not be delivered; the App's current window is shown."
                    )
        # Convenience context follows locally bound content. Ambiguity still
        # leaves every explicitly bound window readable.
        current_member = permitted.get(plan.principal)
        current_identity = WindowIdentity.of(current_member) if current_member is not None else None
        current_part = next((part for part in parts if part.identity == current_identity), None)
        if current_part is not None and (current_part.tree is not None or current_part.image is not None):
            principal, principal_error = plan.principal, plan.selection_error
            # pylint: disable-next=protected-access
            if principal_error is None and (related is not None or ui._anchor is not None):
                # pylint: disable-next=protected-access
                ui._context = principal
                # pylint: disable-next=protected-access
                if related is not None and ui._anchor is None:
                    instance.anchor = controller.reader.select_working_window(
                        directory["application"], instance.name, directory, principal
                    )
        if principal_error is not None:
            # pylint: disable-next=protected-access
            ui._context = None
            if not window_family_only or permitted:
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
        shown: set[WindowIdentity] = set()
        for part in parts:
            identity = part.identity
            current = permitted.get(identity.window)
            key = identity.window if current is not None and WindowIdentity.of(current) == identity else None
            if key is None and part.source is not None and not part.source.scope_confirmed:
                retained = next((
                    item for item in part.source.source_directory["surfaces"]
                    if WindowIdentity.of(item) == identity
                ), None)
                if (
                    retained is not None
                    and window_lifecycle(identity, directory) in ("unknown", "closed")
                ):
                    key = identity.window
            if key is not None and identity not in shown:
                shown.add(identity)
                displayed_parts.append((key, part))
        displayed_parts.sort(key=lambda entry: entry[0] != plan.principal)
        has_current_capture = any(
            (part.tree is not None or part.image is not None)
            and part.identity in current_identities
            for _, part in displayed_parts
        )
        summarized_history = (
            {id(part) for _, part in displayed_parts if part.identity not in current_identities}
            if has_current_capture else set()
        )
        delivered_gaps = tuple(
            (part.source.directory, part.gaps)
            for _, part in displayed_parts
            if view != "image" and part.tree is not None and part.source is not None
        )
        lines: list[str | DisplayText | DisplayDocument] = []
        screenshot, included_images = None, ()
        image_notes = []
        desktop_image = False
        image_parts = [
            part for _, part in displayed_parts
            if part.image is not None or "image" in plan.requested.get(part.identity, ())
        ]
        if image_parts:
            from computer_use.interaction.read.acquisition.images.composite.pipeline import InterfaceImagePipeline

            try:
                image, image_notes = InterfaceImagePipeline(controller).run(
                    ui, image_parts, on_gap=collection_gaps.append
                )
                ui._image, screenshot = image, image.png if image is not None else None
                if image is not None:
                    included_images = tuple(piece.window for piece in getattr(image, "pieces", ()))
                    if not included_images:
                        included_images = tuple(
                            part.identity.window for part in image_parts
                            if part.image is not None and image.png is part.image.image
                        )
            except Exception as error:  # noqa: BLE001 - image composition must not discard independent text content
                collection_gaps.append(_collection_gap("image", error))
        desktop_fallback = screenshot is None and bool(image_parts) and related is None
        if desktop_fallback and directory.get("platform") == "windows" and not getattr(ui, "_strict_window", False):
            # Reuse the desktop capture and its delivery-owned coordinate map.
            # Pixels of the visible desktop do not satisfy window image coverage.
            controller.port.capture_references = controller.interfaces.windows.capture_references()
            desktop, gap = await collect_component(controller.port, "desktop image", controller.desktop_reader.read)
            if gap is not None:
                collection_gaps.append(gap)
            if desktop is not None and desktop["screenshot"] is not None:
                controller.desktop.record(desktop)
                ui._image, screenshot = None, desktop["screenshot"]
                desktop_image = True
                lines.extend(("Window image unavailable. Showing the visible desktop.", desktop["document"]))
        if not desktop_image:
            lines.extend(image_notes)
        header = capture_header(
            controller, ui, directory, parts=displayed_parts, delivered_gaps=delivered_gaps,
            image_windows=included_images,
        )
        lines.insert(0, DisplayText("interface_header", (header,)))
        if isinstance(first, AppObservation) and first.launch:
            lines.insert(1, template("Launch: {0}", first.launch["detail"]))
        trees = []
        member_trees = {}
        presentation_exhausted = any(not part.snapshots_numbered for _, part in displayed_parts)
        tree_heading = len(lines)
        if any(part.tree is not None for _, part in displayed_parts):
            lines.append("Tree: full")
        elif window_family_only and not permitted:
            lines.append("Tree: not expanded; directory only.")
        elif any("tree" in requested for requested in plan.requested.values()):
            lines.append("Tree: unavailable")
        else:
            lines.append("Tree: not requested")
        recovery_prefix = 0
        capture_statuses = []
        for key, part in displayed_parts:
            ref = controller.interfaces.windows.reference(
                part.target.session, directory["application"],
                part.identity,
            )
            if len(header.scoped) != 1 or header.scoped[0].native_window != key:
                lines.append(
                    DisplayText("member_heading", (next(window for window in header.windows if window.ref == ref),))
                )
            if part.source is not None:
                lines.extend(part.source.context_notes)
            if part.capture_companion is not None:
                from computer_use.interaction.read.presentation.rendering.document.capture_context import (
                    CaptureCompanion,
                )

                proof = part.capture_companion
                aliases = {node: ref for ref, node in part.tree.refs.items()}
                parent_ref = controller.interfaces.windows.reference(
                    part.target.session, directory["application"], proof.context.parent,
                )
                lines.append(DisplayText("capture_status", (CaptureCompanion(
                    ref, parent_ref, aliases[proof.root], aliases[proof.button],
                    proof.context.capture, proof.context.before, proof.context.after,
                ),)))
            capture_statuses.append((len(lines), key, part))
            lines.append("")
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
            if part.tree is not None and id(part) in summarized_history:
                lines.append(template(
                    "Captured AX source retained: {0}, intervalNs={1}; outside current read scope, tree not expanded.",
                    part.tree.native.id, part.tree.native.interval_ns,
                ))
            elif part.tree is not None and part.snapshots_numbered:
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
                prepared = None
                if not presentation_exhausted:
                    try:
                        prepared = self.preparation.run(
                            SnapshotRenderRequest(
                                part.tree, compact=True, public_api=True, window_context=window_context,
                                recovery_prefix_units=recovery_prefix,
                            ), presentation_control,
                        )
                    except OperationDeadlineExceeded:
                        # Bindings and capture already completed. Keep prepared
                        # siblings and pixels; never full-render an unprepared
                        # snapshot after the original operation deadline.
                        presentation_exhausted = True
                if prepared is None:
                    collection_gaps.append(ObservationGap(
                        "presentation", "observation_incomplete",
                        f"Window {ref}: facts were captured but its tree presentation exceeded "
                        "the original operation budget.",
                    ))
                else:
                    trees.append((part.target, part.tree))
                    member_trees[part.identity] = part.tree
                    root_groups = (root.groups for root in prepared.roots if root.groups is not None)
                    recovery_prefix = max(
                        (group.recovery_units for groups in root_groups for group in groups.groups),
                        default=recovery_prefix,
                    )
                    same_action_window = (
                        related is not None and part.target.session == related.original.session
                        and part.identity == current_identity == related.original_window
                    )
                    exact_scope = (
                        same_action_window and window_context.scope_confirmed and part.identity == WindowIdentity(
                            part.target.root_window, part.target.surface, part.target.instance
                        )
                    )
                    if exact_scope and directory.get("focusedWindow") == key:
                        focus = related_focus_summary(part.target, prepared, ref)
                        if focus is not None:
                            lines.append(focus)
                    lines.append(tree_document(controller, part.target, part.tree, prepared=prepared))
            if part.menus is not None and part.snapshots_numbered:
                lines.append("Application menu:")
                owners, menu_trees = remember_menu(controller, ui, part.menus, key)
                ui._lookup_scopes += owners
                for target, tree in menu_trees:
                    prepared = None
                    if not presentation_exhausted:
                        try:
                            prepared = self.preparation.run(
                                SnapshotRenderRequest(tree, compact=True, public_api=True), presentation_control
                            )
                        except OperationDeadlineExceeded:
                            presentation_exhausted = True
                    if prepared is not None:
                        trees.append((target, tree))
                        lines.append(tree_document(controller, target, tree, prepared=prepared))
                    else:
                        collection_gaps.append(ObservationGap(
                            "presentation", "observation_incomplete",
                            f"Window {ref}: its captured menu tree was not prepared within the original budget.",
                        ))
            lines += [_gap_line(gap, concise=True) for gap in part.gaps]
            if part.image is not None:
                from computer_use.protocol.captures import capture_metadata

                lines += [
                    template("Image note: {0}", note)
                    for note in capture_metadata(part.image.image).get("stopReasons", ())
                ]
        content, content_gap = await application_content()
        content_owner = None
        if content is not None:
            target, tree, document, content_owner = content
            if not trees:
                lines[tree_heading] = "Tree: full"
            trees.append((target, tree))
            lines.extend(("Application content (observation only)", document))
        if content_gap is not None:
            collection_gaps.append(content_gap)
        # Reused content is available through its host, not a failed capture.
        # Decide after presentation so an exhausted host render cannot claim
        # that the model received the reused menu's tree.
        for index, key, part in capture_statuses:
            identity = part.identity
            requested = plan.requested.get(identity, ())
            reused_tree, reused_image = plan.reused_content.get(identity), plan.reused_images.get(identity)
            ax_status = (
                "collected" if part.tree is not None else "included in host tree"
                if reused_tree is not None and WindowIdentity.of(reused_tree) in member_trees
                else "unavailable" if "tree" in requested else "not requested"
            )
            image_status = (
                "collected" if key in included_images else "included in host image"
                if reused_image is not None and reused_image["window"] in included_images
                else "unavailable" if "image" in requested else "not requested"
            )
            lines[index] = f"AX: {ax_status}; Image: {image_status}."
        try:
            coverage = plan.coverage(directory, parts, included_images, included_trees=member_trees)
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
        from .presentations import collect_presentations, presentation_notices

        presentations = collect_presentations(instance.content_presentations, directory, parts)
        instance.content_presentations = {item["id"]: item for item in presentations}
        lines.extend(presentation_notices(presentations, instance.windows))
        if presentation_exhausted:
            lines[tree_heading] = (
                "Tree: partial (presentation budget exhausted; unprepared captured facts not expanded)"
            )
            prepared_windows = {identity.window for identity in member_trees}
            header = replace(header, windows=tuple(
                replace(member, expanded=member.expanded and (
                    member.native_window in prepared_windows or member.native_window in included_images
                )) for member in header.windows
            ))
            headings = {member.ref: member for member in header.windows}
            lines[0] = DisplayText("interface_header", (header,))
            lines = [
                DisplayText("member_heading", (headings[line.values[0].ref],))
                if isinstance(line, DisplayText) and line.kind == "member_heading" else line
                for line in lines
            ]
        # pylint: disable-next=protected-access
        scope = directory["application"] + (":app" if ui._anchor is None else ":window:" + ui._anchor) + ":" + view
        if any(part.local_detail for _, part in displayed_parts):
            scope += ":selection"
        elif plan.focused_editor is not None and plan.focused_editor[0] not in member_trees:
            scope += ":editor"
        transient_window = None
        if related is not None and len(displayed_parts) == 1:
            identity = displayed_parts[0][1].identity
            displayed_current_member = identity in current_identities and (
                identity in member_trees or identity.window in included_images
            )
            if displayed_current_member and identity.window in related.new_windows(directory) and all(
                (target.root_window, target.surface, target.instance)
                == (identity.window, identity.surface, identity.instance) for target, _ in trees
            ):
                ref = controller.interfaces.windows.reference(directory["session"], directory["application"], identity)
                if ref is not None:
                    transient_window = (identity, ref)
        return publish(
            controller,
            ui,
            DisplayDocument.join(lines),
            screenshot,
            trees=tuple(trees),
            captured_trees=tuple((part.target, part.tree) for part in parts if part.tree is not None),
            full=full,
            scope=scope,
            directory=directory,
            preparation=preparation,
            content_available=bool(trees) or screenshot is not None,
            gaps=tuple(gap for part in parts for gap in part.gaps) + tuple(collection_gaps),
            window_coverage=coverage,
            presentations=presentations,
            member_trees=member_trees,
            lookup_context=LookupContext(None) if desktop_image and not trees else None,
            context_owner=related.context_owner if related is not None else None,
            content_owner=content_owner,
            partial=bool(
                principal_error and (not window_family_only or permitted)
                or directory["stopReasons"]
                or any(part.gaps for part in parts)
                or collection_gaps
            ),
            on_observation=on_observation,
            transient_window=transient_window,
        )

    async def _refresh_new_native_image(self, part: WindowPart, directory: NativeDirectory) -> None:
        """Recover a changed capture after the same member gains a native binding."""
        image, source = part.image, part.image_directory
        if image is None or source is None or part.source is None:
            return
        if part.source.scope_confirmed or image.rechecked_scope is not None or not image.scope_changed:
            return
        if image.target.capture is None or image.target.root_window != part.identity.window:
            return
        if window_binding(image.target) != window_binding(part.target):
            return
        scope = image.native_upgrade_scope(source, directory)
        if scope is None:
            return
        budget = current_operation_budget()
        available = budget.available_ms() if budget is not None else None
        if available is not None and available < IMAGE_REFRESH_MINIMUM_MS:
            return
        # One native read under the caller's remaining deadline. Native
        # capture revalidates its window and geometry before returning.
        target = window_binding(image.target)
        refreshed, _ = await collect_component(
            self.controller.port, "image",
            lambda: self.controller.reader.images.window(target, timeout_ms=600),
        )
        if refreshed is None or refreshed.scope_changed:
            return
        if (
            refreshed.target.capture is not None and refreshed.target.capture != image.target.capture
            and window_binding(refreshed.target) == target
        ):
            part.image = replace(refreshed, rechecked_scope=scope)
            part.image_directory = directory

    async def _selected_item(self, ui, item, target: Binding) -> WindowPart | None:
        """Prove the exact selected control using the existing node collector."""
        # pylint: disable-next=protected-access
        if window_binding(member_target(ui._instance, item)) != window_binding(target):
            return None
        part = await self.member(
            ui, item, "tree", max_nodes=4, max_depth=1, component_budget_ms=500,
            fields=[
                "AXRole", "AXTitle", "AXDescription", "AXIdentifier", "AXValue", "AXURL", "AXSelected", "AXExpanded",
            ],
            detail=target,
        )
        if part.tree is None or not part.snapshots_numbered or part.tree.native.roots != (target.node,):
            return None
        node = part.tree.native.nodes.get(target.node)
        if node is None or node.states.selected is not True or node.states.expanded == "expanded":
            return None
        part.gaps += (ObservationGap(
            "tree", "observation_incomplete", "Selected item observed. Other window content was not expanded.",
        ),)
        return part

    async def _related_editor(self, ui, plan, directory, permitted) -> WindowPart | None:
        """Collect a newly surfaced, exactly attached editor ahead of its host."""
        host = permitted.get(plan.principal)
        if host is None or WindowIdentity.of(host) != plan.related.original_window:
            return None
        candidates = []
        for item in permitted.values():
            if WindowIdentity.of(item) in plan.related.known_windows:
                continue
            if item["node"] is None or content_kind(item) != "unknown" or item["modal"] is True:
                continue
            if interaction_contains(directory, item["window"], host["window"]):
                candidates.append(item)
        if len(candidates) != 1:
            return None
        (item,) = candidates
        if directory.get("focusedWindow") not in (None, host["window"], item["window"]):
            return None
        part = await self.member(ui, item, "tree", max_nodes=4, max_depth=3, component_budget_ms=500)
        if part.tree is None or not part.snapshots_numbered:
            return None
        for root in part.tree.native.roots:
            node = part.tree.native.nodes.get(root)
            if node is None or node.role not in ("text_field", "text_area") or node.states.focused is not True:
                continue
            if node.states.editable is True or any(
                action.available is True and action.kind in ("replace_selection", "insert_text", "set_value")
                for action in node.actions or ()
            ):
                break
        else:
            return None
        plan.focused_editor = (WindowIdentity.of(host), part.identity)
        plan.update(directory, permitted)
        part.gaps += (ObservationGap(
            "tree", "observation_incomplete", "Focused editor observed. Other window content was not expanded.",
        ),)
        return part

    async def member(
        self, ui: UIHandle, item: SurfaceInfo, view: str, *, max_nodes: int = 200,
        component_budget_ms: int | None = None,
        fields: list[str] | None = None, max_depth: int | None = None, detail: Binding | None = None,
    ) -> WindowPart:
        """Collect one confirmed window member of the interface scope.

        Args:
            ui: App or request scope whose instance and observation bounds constrain the
                operation.
            view: Requested observation components: tree, image, or both.
            max_nodes: Maximum number of native objects this collection may read.
        """
        controller = self.controller
        # pylint: disable-next=protected-access
        target = detail or member_target(ui._instance, item)
        # pylint: disable-next=protected-access
        source_directory = ui._instance.directory
        tree = image = None
        snapshots_numbered = True
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
        # An isolated provider timeout retires the worker that owns capture
        # coordinates. Acquire this member's pixels after the capped tree lease.
        image_first = (
            component_budget_ms is not None and view == "both"
            and "nativeReadIsolation" not in controller.port.connection.capabilities
        )
        if image_first:
            await capture_image()
        if view != "image":
            try:
                options = {"strict_window": True} if getattr(ui, "_strict_window", False) else {}
                # This exact-member component must leave room for pixels and
                # directory reconciliation within the enclosing call deadline.
                with (
                    controller.port.calls.limit(component_budget_ms)
                    if component_budget_ms is not None else nullcontext()
                ):
                    tree, gap = await collect_component(
                        controller.port, "tree",
                        lambda: controller.reader.reader.read(
                            target, max_nodes=max_nodes, scope_kind="window" if detail is None else None,
                            fields=fields, max_depth=max_depth, **options,
                        ),
                    )
                if gap:
                    gaps.append(gap)
                if tree is not None:
                    budget = current_operation_budget()
                    try:
                        (tree,) = controller.reader.number_snapshots((tree,), _control=Control(
                            remaining=budget.remaining if budget is not None else None,
                            cancelled=budget.cancelled if budget is not None else None,
                            call_id=budget.operation_id if budget is not None else None,
                        ))
                    except OperationDeadlineExceeded:
                        snapshots_numbered = False
                        gaps.append(ObservationGap(
                            "presentation", "observation_incomplete",
                            "Captured window facts retained with their original scope and times; display numbering "
                            "did not complete within the original operation budget.",
                        ))
            except Exception as error:  # noqa: BLE001 - preserve pixels and independently collected windows
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
        return WindowPart(
            target, tree, image, gaps=tuple(gaps), image_directory=source_directory if image is not None else None,
            snapshots_numbered=snapshots_numbered, local_detail=detail is not None,
        )
