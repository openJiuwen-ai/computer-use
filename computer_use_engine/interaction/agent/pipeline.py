# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Translate public UI handles into scoped session reads, queries, and input."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from string import ascii_lowercase
from time import monotonic
from typing import TYPE_CHECKING, cast

from computer_use.diagnostics import local_failure
from computer_use.errors import ComputerUseError
from computer_use.identity import FrozenRecord
from computer_use.interaction.query.instances.membership import ancestor, ancestry, in_scope
from computer_use.interaction.query.instances.pipeline import ApplicationDiscoveryPipeline
from computer_use.interaction.query.instances.scope import (
    allowed,
    display_selection,
    refresh,
    retain_fixed_failure,
    validate_session,
)
from computer_use.interaction.read.presentation.content import DisplayText, template
from computer_use.interaction.read.presentation.document import DisplayDocument
from computer_use.interaction.read.presentation.ir import freeze
from computer_use.interaction.read.views.pipeline import InterfaceReadPipeline, member_target
from computer_use.interaction.read.views.publication import publish, strict_public_directory
from computer_use.interaction.requests import ExecuteAction, FindElement
from computer_use.interaction.write.ui.pipeline import InterfacePointerPipeline
from computer_use.native.contracts import Binding, Intent, Knowledge
from computer_use.native.scopes import (
    FixedWindowInvalidated,
    FixedWindowUnconfirmed,
    QueryRootUnavailable,
    RunningApplication,
    window_binding,
)
from computer_use.pipeline import Contract, Effect
from computer_use.pipeline.core import Node
from computer_use.protocol.element_path import element_path
from computer_use.session.cursors import TextCursors
from computer_use.session.events import explicit_observation_completed
from computer_use.session.interfaces import (
    ApplicationEntry,
    ApplicationReferences,
    WindowIdentity,
    WindowLifetime,
)
from computer_use.session.operations import OperationDeadlineExceeded, current_operation_budget
from computer_use.session.windows import WindowReferences

from .requests import (
    ActOnElement,
    CloseWindow,
    DiscoverApps,
    DiscoverWindows,
    ObserveDesktop,
    ObserveElement,
    ObserveText,
    ObserveUI,
    ResolveElement,
    UIKeyboard,
    UIPointer,
)

if TYPE_CHECKING:
    from typing import Sequence

    from computer_use.interaction.agent.requests import InterfaceRequest
    from computer_use.interaction.controller import SessionController
    from computer_use.interaction.records import ElementSnapshot, UIHandle
    from computer_use.pipeline.core import Control, Step
    from computer_use.protocol.records import Record
    from computer_use.types import ActionResult, AppInfo, ElementPath, Observation, Point, TextPage, WindowCatalogue


class AgentInterfacePipeline:
    """Translate public UI operations into scoped session execution.

    Apps and request scopes supply instance membership; Element snapshots supply
    observed native bindings. This adapter resolves those scopes and retains
    the public references and cursors used by later calls.

    Attributes:
        controller: Session readers, native connection, and write entry point.
        references: Registry mapping public application numbers to application identities.
        text_cursors: Registry linking text continuation tokens to captured sources.
        discovery: Flow publishing application choices.
        read_pipeline: Flow observing App/window views and publishing their results.
        write: Shared action flow, entered after public-scope validation.
        lookup: Shared lookup flow, entered once for each observed scope.
    """

    def __init__(
        self,
        controller: SessionController,
        write: Step[ExecuteAction, ActionResult],
        lookup: Step[FindElement, ElementSnapshot],
    ) -> None:
        self.controller = controller
        self.write = write
        self.lookup = lookup
        self.references = ApplicationReferences()
        self.windows = WindowReferences()
        controller.reader.window_registry = self.windows
        self.text_cursors = TextCursors()
        self.discovery = ApplicationDiscoveryPipeline(controller.reader.directory, self.references, self.windows)
        self.read_pipeline = InterfaceReadPipeline(controller)

    def application(self, ref: int) -> Record:
        """Create App initialization state from a published application choice.

        Args:
            ref: Integer application reference returned by discovery.

        Returns:
            Selector and native state for constructing the corresponding App handle.
        """
        from computer_use.session.state import AppState

        entry = self.references.resolve(ref, self.controller.transport.session)
        state = AppState(entry.selector)
        if entry.running:
            state.running = RunningApplication(entry.selector, entry.display_name, entry.session)
        return dict(selector=entry.selector, _nativeState=state)

    async def list_applications(self) -> list[AppInfo]:
        return await self.discovery.run()

    async def list_windows(self, app_name: str) -> WindowCatalogue:
        catalogue = await self.controller.reader.directory.window_catalogue(app_name)
        items = catalogue["windows"]
        refs = self._publish_window_catalogue(items, catalogue["observed"], app_name=app_name)
        windows = []
        for index, item in enumerate(items):
            ref = refs.get(str(index))
            if ref is not None:
                app_entry = ApplicationEntry(catalogue["session"], item["selector"], app_name, True)
                app_id = self.references.publish(app_entry)
                windows.append(FrozenRecord(dict(
                    app_id=app_id,
                    window_ref=ref,
                    title=item["window"]["title"] or None,
                    app_is_foreground=item["appIsForeground"],
                )))
        reasons = tuple(catalogue["stopReasons"])
        if len(windows) != len(items):
            reasons += ("Some matching windows no longer have a usable reference.",)
        return FrozenRecord(dict(windows=tuple(windows), stop_reasons=reasons))

    def _publish_window_catalogue(self, items, observed, *, app_name=None):
        # Both desktop pixels and metadata-only discovery publish the same
        # positive visible-window subset, never a complete AX directory.
        catalogues = {}
        names = {}
        for item in items:
            if "window" not in item or not item.get("selector"):
                continue
            selector = item["selector"]
            if selector not in catalogues:
                names[selector] = app_name if app_name is not None else item["app"]
                catalogues[selector] = dict(
                    kind="surfaces",
                    session=self.controller.transport.session,
                    platform=self.controller.transport.platform,
                    application=selector,
                    observed=observed,
                    surfaces=[],
                    focusedWindow=None,
                    rootDiscoveryComplete=False,
                    stopReasons=["desktop catalogue is a visible-window subset"],
                    provider=dict(status="notRequested", attribute=None, writeStatus=None),
                )
            catalogues[selector]["surfaces"].append(item["window"])
        by_selector = {}
        for selector, directory in catalogues.items():
            registered = self.windows.register_directory(directory, names[selector], identity_only=app_name is not None)
            by_selector[selector] = {window: int(ref) for ref, window in registered.items()}
        refs = {}
        for index, item in enumerate(items):
            if "window" not in item:
                continue
            registered = by_selector.get(item.get("selector"))
            if registered is None:
                continue
            ref = registered.get(item["window"]["window"])
            if ref is not None:
                refs[str(index)] = ref
        return refs

    async def read(
        self,
        ui: UIHandle,
        view: str,
        full: bool,
        menu_path: Sequence[str] | None,
        allow_foreground: bool = False,
        *,
        related=None,
        explicit_read: bool = False,
    ) -> Observation:
        try:
            return await self.read_pipeline.run(
                ui, view, full, menu_path, allow_foreground=allow_foreground, related=related,
                on_observation=explicit_observation_completed if explicit_read else None,
            )
        except (FixedWindowInvalidated, FixedWindowUnconfirmed) as error:
            # A principal read performs its own later directory collection;
            # consume its exact root evidence just like the initial refresh.
            # pylint: disable-next=protected-access
            selection = self.controller.state(ui).selection or ui._instance.anchor
            if selection is not None:
                # pylint: disable-next=protected-access
                retain_fixed_failure(ui._instance, selection, error)
            raise

    async def read_desktop(self, view: str, full: bool) -> Observation:
        """Publish a desktop image or a selectable application directory.

        Args:
            view: Image/combined view, or tree for the application directory.
            full: Request a full observation from the publication service.

        Returns:
            Desktop observation assembled from the requested view.
        """
        screenshot, lines = None, []
        if view != "tree":
            try:
                self.controller.port.capture_references = self.windows.capture_references()
                observed = await self.controller.desktop_reader.read()
                self.controller.desktop.record(observed)
            except BaseException:
                self.controller.desktop.reset()
                raise
            screenshot = observed["screenshot"]
            # Publish the same public reference namespace as list_apps, without
            # another directory scan or an accessibility-tree prerequisite.
            applications = {}
            desktop = observed["observation"]
            for item in [desktop["frontmost"], *desktop["windows"]]:
                selector = item.get("selector") if item is not None else None
                if selector:
                    name = item.get("app", item.get("name"))
                    applications[selector] = self.references.publish(
                        ApplicationEntry(self.controller.transport.session, selector, name, True)
                    )
            window_refs = self._publish_window_catalogue(desktop["windows"], observed["observed"])
            document = observed["document"]
            lines.append(
                replace(
                    document,
                    parts=tuple(
                        replace(part, values=(*part.values, freeze(applications), freeze(window_refs)))
                        if isinstance(part, DisplayText) and part.kind == "desktop"
                        else part
                        for part in document.parts
                    ),
                )
            )
        if view == "tree":
            apps = await self.list_applications()
            lines.append("Desktop application directory (not a recursive system tree):")
            lines.extend(
                template(
                    "{0} {1!r} ({2}); computer.app({0})",
                    app.id,
                    app.display_name,
                    "running" if app.is_running else "installed",
                )
                for app in apps
            )
            if not apps:
                lines.append("No applications were discovered in the completed native directory.")
        return publish(
            self.controller, None, DisplayDocument.join(lines), screenshot, full=full, view="desktop",
            on_observation=explicit_observation_completed,
        )

    def validate_snapshot(self, ui: UIHandle, snapshot: ElementSnapshot, *, allow_unknown: bool = False) -> None:
        """Check that a retained element still belongs to the handle's scope.

        Args:
            ui: App/window view constraining the element's allowed membership.
            snapshot: Element identity and the scope recorded when it was observed.
            allow_unknown: Permit reference retention before a fresh directory
                query, never native observation or input authorization.
        """
        validate_session(self.controller, ui)
        target = snapshot["binding"]
        self.controller.validate_element(target)
        if getattr(ui, "_strict_window", False) and (
            # pylint: disable-next=protected-access
            window_binding(target) != ui._window_identity or snapshot.get("native_window_outside", False)
        ):
            raise local_failure("this element is outside the exact titled window", "stale_target")
        identity = snapshot.get("window_identity")
        # pylint: disable-next=protected-access
        evidence = ui._instance.surface_evidence(target.surface, identity)
        invalid_identity = (
            True
            if (identity is not None and evidence is None)
            or (evidence is not None and evidence.lifetime is WindowLifetime.RETIRED)
            else False
        )
        if invalid_identity:
            raise local_failure(
                "the observed element window identity was retired",
                "stale_target",
            )
        if in_scope(ui, target.surface) and (evidence is None or evidence.membership is Knowledge.YES):
            if evidence is not None and identity is None:
                snapshot["window_identity"] = evidence.identity
            return
        # pylint: disable-next=protected-access
        if ui._anchor is None and target.root_window is None and snapshot.get("scope_kind") == "applicationContent":
            # The reference came from this App. Native read admission proves
            # the original application and the node's content membership.
            return
        if snapshot.get("scope_kind") == "applicationMenu" and "menu_window_identity" in snapshot:
            menu_window = snapshot["menu_window_identity"]
            if menu_window is None:
                # pylint: disable-next=protected-access
                if ui._anchor is None:
                    return
            else:
                # The containing window is part of this snapshot's provenance,
                # not whatever another menu read last recorded for the surface.
                from computer_use.interaction.query.instances.scope import require_identity

                require_identity(ui, menu_window, current=not allow_unknown)
                # pylint: disable-next=protected-access
                if ui._anchor is None or ancestor(ui._instance.directory, menu_window.window, ui._anchor):
                    return
        membership = evidence.membership if evidence is not None else Knowledge.NO
        # pylint: disable-next=protected-access
        if membership is Knowledge.YES and ui._anchor is not None:
            # pylint: disable-next=protected-access
            membership = ancestry(cast("DirectoryFacts", ui._instance.directory), evidence.identity.window, ui._anchor)
        if membership is Knowledge.UNKNOWN:
            if allow_unknown:
                return
            raise local_failure(
                "the retained element window membership is temporarily unconfirmed",
                "observation_incomplete",
            )
        raise local_failure(
            "this element is outside the allowed App/window scope",
            "stale_target",
        )

    async def element(
        self, ui: UIHandle, target: int | str | ElementPath, timeout_ms: int, control: Control, *, fresh: bool = False
    ) -> ElementSnapshot:
        """Resolve an observed number, exact name, or path within this UI view.

        Args:
            ui: App/window view constraining lookup and reference ownership.
            target: Integer reference, exact accessible name, or semantic path.
            timeout_ms: Total wait allowed for path lookup, in milliseconds.
            control: Parent execution metadata passed to each lookup child.

        Returns:
            The uniquely matched element's binding and retained observation sources.

        Raises:
            ComputerUseError: The reference is stale, lookup is ambiguous, or an
                incomplete scope prevents confirming a unique result.
        """
        # Exact types preserve scalar validation and identity contracts.
        # pylint: disable-next=unidiomatic-typecheck
        if type(target) is int:
            try:
                # pylint: disable-next=protected-access
                snapshot = cast("ElementSnapshot", ui._instance.refs[str(target)])
            except KeyError:
                raise local_failure(
                    "element reference was not observed in this application",
                    "stale_target",
                ) from None
            self.validate_reference_owner(ui, snapshot)
            return snapshot
        if not isinstance(target, (str, list)):
            raise local_failure(
                "element expects an integer, exact name, or path",
            )
        try:
            path = element_path([target] if isinstance(target, str) else target)
        except Exception as error:
            raise local_failure(str(error)) from error
        validate_session(self.controller, ui)
        # pylint: disable-next=protected-access
        instance = ui._instance
        # Application aliases share the delivered lookup scope, or a semantic
        # result already returned within this host execution. This continuation
        # never advances model-delivery, image or default keyboard-input state.
        # A window scope uses its own menu view and always bounds window
        # lookup by its original root. Unreturned/background reads grant no
        # new name/path context. A retired popup is not
        # the App's lifetime: a fresh name query may use the App's already
        # actually delivered current primary. Fixed handles and numbered references do
        # not acquire this recovery, and no input/image delivery is advanced.
        from computer_use.session.lookup_continuation import current_lookup_continuation

        # pylint: disable-next=protected-access
        continuation = current_lookup_continuation(instance) if ui._anchor is None else None
        lookup_context = (
            continuation.context if continuation is not None
            # pylint: disable-next=protected-access
            else instance.lookup_context if ui._anchor is None else ui._lookup_context
        )
        lookup_windows = instance.delivered_lookup_windows | (
            continuation.windows if continuation is not None else frozenset()
        )
        # pylint: disable-next=protected-access
        if ui._anchor is None and lookup_context is None:
            raise local_failure(
                "this App has no delivered lookup scope",
                "stale_target",
            )
        # pylint: disable-next=protected-access
        if ui._anchor is not None:
            from .scope import lookup_window

            # Acquire only the specified root's query facts inside this lookup.
            # No publication or change to the App's delivered default context.
            query_window = lookup_window(ui)
            source_owner = instance.owners.get(query_window)
            query = self.controller.state(source_owner).query_target if source_owner is not None else None
            if query is None or query.node is None:
                if instance.members.get(query_window, {}).get("node") is None:
                    await refresh(self.controller, ui, accessibility=True)
                for key, item in allowed(ui).items():
                    if item.get("node") is not None:
                        native = self.controller.state(instance.owners[key])
                        native.query_target = member_target(instance, item)
                        native.query_scope_kind = "window"
                        native.selection, native.observation = None, None
        menu_only = lookup_context.menu_only if lookup_context is not None else False
        content_owner = lookup_context.application_content if lookup_context is not None else None

        def lookup_remaining():
            # This adapter also owns pure-Python polling. A loop with no native
            # query must not escape the parent operation's original clock.
            cancelled = getattr(control, "cancelled", None)
            if cancelled is not None and cancelled():
                raise asyncio.CancelledError("Element lookup cancelled")
            remaining = getattr(control, "remaining", None)
            if remaining is not None:
                available = remaining()
            else:
                budget = current_operation_budget()
                if budget is None:
                    return None
                available = budget.remaining()
            if available is not None and available < 1:
                raise OperationDeadlineExceeded("Element lookup exhausted its original operation budget")
            return available

        def observed_query(item):
            owner = instance.owners.get(item["window"])
            query = self.controller.state(owner).query_target if owner is not None else None
            return query is not None and query.node is not None and (
                query.session, query.instance, query.root_window, query.surface
            ) == (instance.session, item["instance"], item["window"], item["surface"])

        def lookup_sources():
            context = (
                # pylint: disable-next=protected-access
                lookup_window(ui) if ui._anchor is not None
                else lookup_context.window.window if lookup_context.window is not None else None
            )
            owners = list(lookup_context.menus) if lookup_context is not None else []

            # pylint: disable-next=protected-access
            if ui._anchor is None and not menu_only:
                reselected = False
                old = instance.members.get(context)
                previous_matches = (
                    lookup_context.window is not None
                    and old is not None
                    and WindowIdentity.of(old) == lookup_context.window
                )
                if not previous_matches or not observed_query(old):
                    # A delivered popup image/old child is not the App's lifetime.
                    # Use a current display root only if its semantic query scope
                    # was actually delivered, never an unseen pending observation.
                    current = display_selection(self.controller, instance, instance.directory)
                    replacement = instance.members.get(current.root.root_window) if current is not None else None
                    if (
                        replacement is not None
                        and WindowIdentity.of(replacement) in lookup_windows
                        and observed_query(replacement)
                    ):
                        context = replacement["window"]
                        owners = []
                        reselected = True
                if not reselected and lookup_context.window is not None:
                    from computer_use.interaction.query.instances.scope import require_identity

                    require_identity(ui, lookup_context.window)
            choices = {} if menu_only else allowed(ui)
            choices = {
                key: item
                for key, item in choices.items()
                if context is not None and ancestor(instance.directory, key, context)
            }
            incomplete = []
            if not menu_only and context is None and content_owner is None:
                incomplete.append("application")
            if not menu_only and context is not None and context not in choices:
                incomplete.append(context)
            for key, item in choices.items():
                owner = instance.owners.get(key)
                # A visual catalogue reports window geometry and identity without
                # asking AX for a node. Its node=None does not revoke the observed
                # query root held by this exact window lifetime. Native lookup
                # revalidates that root and still searches every relevant scope.
                query_available = observed_query(item)
                if query_available:
                    owners.append(owner)
                elif key == context and content_owner is None:
                    incomplete.append(key)
                # Ancillary name scopes come from owned AX query evidence. A new
                # child seen only as pixels is not a new required AX search scope;
                # every previously AX-observed descendant still participates.
            # pylint: disable-next=protected-access
            if content_owner is not None and ui._anchor is None:
                owners.append(content_owner)
            return owners, incomplete

        lookup_remaining()
        owners, incomplete = lookup_sources()
        if not fresh and owners and not incomplete:
            from computer_use.session.observed_lookup import resolve_observed

            cached = await resolve_observed(
                self.controller, instance, owners, path, lookup_remaining, current_lookup_continuation(instance),
            )
            if cached is not None:
                self.validate_snapshot(ui, cached)
                return cached
        from computer_use.session.observed_lookup import invalidate_lookup_result

        matched = {}
        end = monotonic() + timeout_ms / 1000
        # Waiting belongs to the complete lookup, not its first member. A
        # toolbar with no match must not consume the deadline before the
        # already-observed document has even been searched.
        reconciled = False
        while True:
            lookup_remaining()
            invalidate_lookup_result(self.controller, instance, owners, path, current_lookup_continuation(instance))
            matched.clear()
            recovery = None
            for owner in dict.fromkeys(owners):
                lookup_remaining()
                try:
                    snapshot = await self.lookup.arun(
                        FindElement(owner, path, 0, False, strict_window=getattr(ui, "_strict_window", False)), control
                    )
                except QueryRootUnavailable as error:
                    failed = self.controller.state(owner).query_target
                    # pylint: disable-next=protected-access
                    if reconciled or ui._anchor is not None or menu_only:
                        raise
                    failed_binding = error.binding
                    if failed != failed_binding or instance.owners.get(failed_binding.root_window) is not owner:
                        raise
                    if failed_binding.session != instance.session:
                        raise
                    recovery = owner, error
                    break
                except ComputerUseError as error:
                    if error.code == "element_not_found":
                        continue
                    raise
                binding = snapshot["binding"]
                matched[(binding.session, binding.surface, binding.node)] = snapshot
            if recovery is not None:
                reconciled = True
                owner, error = recovery
                # Disprove only this AX proxy, not its physical Window. The
                # pure lookup has dispatched no input; reuse the original
                # budget for one current App catalogue and exact-root retry.
                self.controller.state(owner).query_target = None
                lookup_remaining()
                await refresh(self.controller, ui, accessibility=True)
                current = instance.members.get(error.binding.root_window)
                if current is not None and current.get("node") is not None and current["node"] != error.binding.node:
                    same_surface = (current["instance"], current["surface"]) == (
                        error.binding.instance, error.binding.surface,
                    )
                    if same_surface and WindowIdentity.of(current) in lookup_windows:
                        self.controller.state(owner).query_target = member_target(instance, current)
                owners, incomplete = lookup_sources()
                if not owners or incomplete:
                    raise error
                continue
            stop_search = bool(matched or incomplete) or not owners
            if stop_search or monotonic() >= end:
                break
            delay = min(0.025, max(0, end - monotonic()))
            remaining = lookup_remaining()
            if remaining is not None:
                delay = min(delay, remaining / 1000)
            await asyncio.sleep(delay)
        if len(matched) > 1:
            raise local_failure(
                "the name/path matches controls in several member windows",
                "ambiguous_target",
            )
        if incomplete:
            raise local_failure(
                "some member lookup scopes have not been observed or have no AX root",
                "observation_incomplete",
            )
        if not matched:
            raise local_failure(
                "no matching element in the completed observed scope",
                "element_not_found",
            )
        snapshot = next(iter(matched.values()))
        self.validate_snapshot(ui, snapshot)
        from computer_use.session.observed_lookup import remember_lookup_result

        remember_lookup_result(
            self.controller, instance, owners, path, snapshot, current_lookup_continuation(instance),
        )
        return snapshot

    def validate_reference_owner(self, ui: UIHandle, snapshot: ElementSnapshot) -> None:
        """Retrieve a known handle without claiming its target is still alive.

        The application's reference table establishes where the number came
        from. Construction grants neither input nor live read access. Those
        boundaries check current Window scope and lifetime, including ended
        descendants whose former ancestry is no longer in the directory. A
        failed write can then return its related observation in the same call.
        """
        target = snapshot["binding"]
        self.controller.validate_element(target)
        # pylint: disable-next=protected-access
        instance = ui._instance
        if instance.session is not None and instance.session != target.session:
            raise local_failure("element belongs to another session", "stale_target")

    @staticmethod
    def keyboard_context(ui: UIHandle, *, require_observed: bool = True) -> Binding:
        """Pin explicit Window or the last application context actually delivered."""
        from computer_use.interaction.query.instances.scope import require_identity

        # pylint: disable-next=protected-access
        instance = ui._instance
        fixed = getattr(ui, "_window_identity", None)
        if fixed is not None:
            # pylint: disable-next=protected-access
            root_identity = WindowIdentity(ui._anchor, fixed.surface, fixed.instance)
            require_identity(ui, root_identity)
            # pylint: disable-next=protected-access
            identity = ui._input_context or root_identity
            evidence = instance.evidence.get(identity)
            if evidence is not None and evidence.lifetime is WindowLifetime.RETIRED:
                # A completed close can end a delivered child before the
                # next observation is delivered. Keep the explicitly pinned
                # root; the normal write path still proves its live receiver.
                identity = root_identity
        else:
            identity = instance.input_context
        if identity is None:
            raise local_failure(
                "this App has no delivered input context",
                "stale_target",
            )
        require_identity(ui, identity)
        if require_observed and identity.window not in instance.observed:
            raise local_failure(
                "the selected input window has not been observed",
                "stale_target",
            )
        return Binding(instance.session, identity.instance, identity.window, identity.surface, None)

    def _application_command_target(self, ui: UIHandle, key: object) -> Binding | None:
        """Use the delivered menu scope for a windowless Command-letter shortcut."""
        if "nativeApplicationCommand" not in self.controller.transport.capabilities:
            return None
        if not isinstance(key, str) or len(key) != 7 or not key.startswith("super+"):
            return None
        if (
            key[-1] not in ascii_lowercase
            # pylint: disable-next=protected-access
            or ui._anchor is not None
            or getattr(ui, "_window_identity", None) is not None
        ):
            return None
        # pylint: disable-next=protected-access
        instance = ui._instance
        state = self.controller.state(ui)
        running, lookup = state.running, instance.lookup_context
        # StartingApplication and SelectedApplicationWindow represent other
        # lifecycle states and cannot admit a windowless command.
        # pylint: disable-next=unidiomatic-typecheck
        if type(running) is not RunningApplication or running.session != instance.session:
            return None
        if state.selection is not None or instance.anchor is not None or instance.input_context is not None:
            return None
        if instance.members or instance.evidence or instance.delivered_lookup_windows:
            return None
        if lookup is None or lookup.window is not None:
            return None
        if not lookup.menu_only or len(lookup.menus) != 1:
            return None
        source = self.controller.state(lookup.menus[0])
        target = source.query_target
        if source.running != running or source.query_scope_kind != "applicationMenu":
            return None
        if source.menu_window_identity is not None or target is None or target.session != running.session:
            return None
        if target.instance is None or target.surface is None:
            return None
        if target.root_window is not None or target.capture is not None:
            return None
        return replace(target, node=None)

    @staticmethod
    def _refreshed_key_root(ui: UIHandle, selected: WindowIdentity, directory: DirectoryFacts) -> Binding | None:
        """Use positive root focus when a delivered child no longer has a current window entry."""
        fixed = getattr(ui, "_window_identity", None)
        if directory["platform"] != "macos" or fixed is None:
            return None
        if fixed.root_window == selected.window or directory.get("focusedWindow") != fixed.root_window:
            return None
        # pylint: disable-next=protected-access
        evidence = ui._instance.evidence.get(selected)
        if (
            evidence is None or evidence.lifetime is not WindowLifetime.RETAINED
            or evidence.membership is not Knowledge.UNKNOWN
        ):
            return None
        if any(item["window"] == selected.window for item in directory["surfaces"]):
            return None
        # pylint: disable-next=protected-access
        root = ui._instance.members.get(fixed.root_window)
        if root is None or not root["nativeWindow"] or not root["accessibility"]:
            return None
        if root["parentState"] == "none" and WindowIdentity.of(root) == WindowIdentity(
            fixed.root_window, fixed.surface, fixed.instance,
        ):
            return fixed
        return None

    async def keyboard(
        self, ui: UIHandle, method: str, parameters: Record, control: Control, *, global_input=False, target=None
    ) -> ActionResult:
        from computer_use.interaction.query.instances.scope import require_identity

        # Capture before a new query: neither focus changes nor hidden sampling
        # may turn this operation into input in another document.
        validate_session(self.controller, ui)
        intent = Intent({"type_text": "insert", "press_key": "key"}.get(method, method), parameters, global_input)
        select_key_context = target is None and method == "press_key" and not global_input
        if select_key_context:
            command_target = self._application_command_target(ui, parameters.get("key"))
            if command_target is not None:
                return await self.write.arun(ExecuteAction(ui, intent, command_target), control)
        # pylint: disable-next=protected-access
        target = target or self.keyboard_context(ui, require_observed=ui._anchor is None)
        identity = WindowIdentity(target.root_window, target.surface, target.instance)
        directory = await refresh(self.controller, ui)
        if select_key_context:
            root = self._refreshed_key_root(ui, identity, directory)
            if root is not None:
                target = root
                identity = WindowIdentity(root.root_window, root.surface, root.instance)
        require_identity(ui, identity, current=True)
        # pylint: disable-next=protected-access
        if ui._anchor is not None and not ancestor(ui._instance.directory, identity.window, ui._anchor):
            raise local_failure(
                "the delivered receiver is no longer confirmed inside this fixed Window",
                "observation_incomplete",
            )
        return await self.write.arun(ExecuteAction(ui, intent, target), control)

    async def act_element(
        self, ui: UIHandle, snapshot: ElementSnapshot, intent: str, parameters: Record, control: Control,
        *, global_input=False,
    ) -> ActionResult:
        """Validate an element and dispatch direct or current-container input.

        Args:
            ui: App/window view owning the element and its member-window scope.
            snapshot: Bound element and its container classification.
            intent: Requested operation, such as insert, replace, click, or key.
            parameters: Text, key, or other parameters for the operation.
            control: Parent execution metadata passed to the write child.

        Returns:
            Execution outcome reported by the session's write branch.
        """
        if snapshot.get("container") and intent == "replace":
            raise local_failure(
                "full replacement requires a concrete input control, not a container",
            )
        forward = (
            snapshot.get("container")
            and intent in ("insert", "paste", "key", "focus")
            and not (intent in ("insert", "paste") and parameters.get("text") == "")
        )
        return await self.write.arun(
            ExecuteAction(
                ui, Intent(intent, parameters, global_input), snapshot["binding"],
                forward_current=bool(forward), snapshot=snapshot
            ),
            control,
        )

    async def pointer(
        self, ui: UIHandle, intent: str, points: tuple[Point, ...], options: Record, control: Control,
        *, global_input=False, image=None,
    ) -> ActionResult:
        if intent == "click" and "pointerOnly" in options and self.controller.transport.platform != "macos":
            # Other adapters already use pointer delivery for coordinates. Keep
            # their existing strict wire schemas unchanged by this macOS option.
            options = {key: value for key, value in options.items() if key != "pointerOnly"}
        return await InterfacePointerPipeline(self.controller, self.write).run(
            ui, intent, points, options, control, global_input=global_input, image=image
        )

    async def read_element(self, ui: UIHandle, snapshot: ElementSnapshot, cursor: str | None) -> Observation:
        from computer_use.interaction.read.element.agent import AgentElementReadPipeline

        self.validate_snapshot(ui, snapshot, allow_unknown=True)
        if self._snapshot_needs_refresh(ui, snapshot):
            await refresh(self.controller, ui)
        return await AgentElementReadPipeline(self.controller).run(ui, snapshot, cursor)

    async def read_text(self, ui: UIHandle, snapshot: ElementSnapshot, cursor: str | None, length: int) -> TextPage:
        from computer_use.interaction.read.text.agent import AgentTextReadPipeline

        self.validate_snapshot(ui, snapshot, allow_unknown=True)
        if self._snapshot_needs_refresh(ui, snapshot):
            await refresh(self.controller, ui)
        return await AgentTextReadPipeline(self.controller, self.text_cursors).run(ui, snapshot, cursor, length)

    @staticmethod
    def _snapshot_needs_refresh(ui: UIHandle, snapshot: ElementSnapshot) -> bool:
        """Use the saved lifetime's evidence, not another window's surface slot."""
        # pylint: disable-next=protected-access
        if ui._instance.session is None:
            return False
        surface = snapshot["binding"].surface
        # pylint: disable-next=protected-access
        evidence = ui._instance.surface_evidence(surface, snapshot.get("window_identity"))
        return evidence is not None and evidence.membership is Knowledge.UNKNOWN or not in_scope(ui, surface)


class AgentDispatch(Node["InterfaceRequest", "AgentResult"]):
    """Dispatch public requests to the session's UI adapter.

    The request record selects an observation, lookup, or input operation.
    Its owner, target, and options become arguments to the corresponding adapter.

    Attributes:
        interfaces: Adapter with explicitly shared write and lookup child flows.
    """

    def __init__(self, interfaces: AgentInterfacePipeline) -> None:
        self.interfaces = interfaces
        self.children = (interfaces.write, interfaces.lookup)
        super().__init__("AgentInterface", Contract(object, object, frozenset(Effect)))

    async def _arun(self, request: InterfaceRequest, control: Control) -> AgentResult:
        api = self.interfaces
        owner = getattr(request, "owner", None)
        if (
            isinstance(request, (ObserveUI, ResolveElement, CloseWindow, UIKeyboard, UIPointer))
            or getattr(owner, "_window_title", None) is not None
        ):
            from .scope import request_scope

            owner = await request_scope(
                api.controller, owner, getattr(request, "window", None), discover_title=isinstance(request, ObserveUI),
            )
        try:
            return await self._dispatch(request, control, owner)
        except (FixedWindowInvalidated, FixedWindowUnconfirmed) as error:
            if getattr(owner, "_strict_window", False) and error.directory is not None:
                error.directory = strict_public_directory(error.directory, owner.identity)
            raise

    async def _dispatch(self, request: InterfaceRequest, control: Control, owner: UIHandle | None) -> AgentResult:
        api = self.interfaces
        if isinstance(request, ObserveUI):
            return await api.read(
                owner, request.view, request.full, request.menu_path, request.allow_foreground, explicit_read=True,
            )
        if isinstance(request, ObserveDesktop):
            return await api.read_desktop(request.view, request.full)
        if isinstance(request, ObserveElement):
            return await api.read_element(owner, request.snapshot, request.cursor)
        if isinstance(request, ObserveText):
            return await api.read_text(owner, request.snapshot, request.cursor, request.length)
        if isinstance(request, DiscoverApps):
            return await api.list_applications()
        if isinstance(request, DiscoverWindows):
            return await api.list_windows(request.app_name)
        if isinstance(request, ResolveElement):
            snapshot = await api.element(owner, request.target, request.timeout_ms, control, fresh=request.fresh)
            if (request.window is not None or getattr(owner, "_strict_window", False)) and type(request.target) is int:
                api.validate_snapshot(owner, snapshot, allow_unknown=True)
            return snapshot
        if isinstance(request, ActOnElement):
            return await api.act_element(
                owner, request.snapshot, request.intent, request.parameters, control,
                global_input=request.global_input,
            )
        if isinstance(request, CloseWindow):
            # pylint: disable-next=protected-access
            return await api.write.arun(ExecuteAction(owner, Intent("closeWindow"), owner._window_identity), control)
        if isinstance(request, UIKeyboard):
            return await api.keyboard(
                owner, request.method, request.parameters, control,
                global_input=request.global_input, target=request.target,
            )
        if isinstance(request, UIPointer):
            return await api.pointer(
                owner, request.intent, request.points, request.options, control,
                global_input=request.global_input, image=request.image,
            )
        raise TypeError("Unknown agent interface request")

    def _run(self, value: InterfaceRequest, control: Control) -> AgentResult:
        raise TypeError("Agent interface requests require arun()")


if TYPE_CHECKING:
    from computer_use.protocol.records import NativeDirectory as DirectoryFacts
if TYPE_CHECKING:
    from computer_use.interaction.records import AgentResult
