# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Translate public UI handles into scoped session reads, queries, and input."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from time import monotonic
from typing import TYPE_CHECKING, cast

from computer_use.diagnostics import local_failure
from computer_use.errors import ComputerUseError
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
from computer_use.interaction.read.views.pipeline import InterfaceReadPipeline
from computer_use.interaction.read.views.publication import publish
from computer_use.interaction.requests import ExecuteAction, FindElement
from computer_use.interaction.write.ui.pipeline import InterfacePointerPipeline
from computer_use.native.contracts import Binding, Intent, Knowledge
from computer_use.native.scopes import FixedWindowInvalidated, FixedWindowUnconfirmed
from computer_use.pipeline import Contract, Effect
from computer_use.pipeline.core import Node
from computer_use.protocol.element_path import element_path
from computer_use.session.cursors import TextCursors
from computer_use.session.interfaces import (
    ApplicationEntry,
    ApplicationReferences,
    WindowEvidence,
    WindowIdentity,
    WindowLifetime,
)
from computer_use.session.windows import WindowReferences

from .requests import (
    ActOnElement,
    DiscoverApps,
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
    from computer_use.types import ActionResult, AppInfo, ElementPath, Observation, Point, TextPage


class AgentInterfacePipeline:
    """Translate public UI operations into scoped session execution.

    App and Window handles supply instance membership; Element snapshots supply
    observed native bindings. This adapter resolves those scopes and retains
    the public references and cursors used by later calls.

    Attributes:
        controller: Session readers, native connection, and write entry point.
        references: Registry mapping public application numbers to application identities.
        text_cursors: Registry linking text continuation tokens to captured sources.
        discovery: Flow publishing application choices.
        read_pipeline: Flow observing App/Window views and publishing their results.
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
        self.discovery = ApplicationDiscoveryPipeline(controller.reader.directory, self.references)
        self.read_pipeline = InterfaceReadPipeline(controller)

    def application(self, ref: int) -> Record:
        """Create App initialization state from a published application choice.

        Args:
            ref: Integer application reference returned by discovery.

        Returns:
            Selector and native state for constructing the corresponding App handle.
        """
        from computer_use.native.scopes import RunningApplication
        from computer_use.session.state import AppState

        entry = self.references.resolve(ref, self.controller.transport.session)
        state = AppState(entry.selector)
        if entry.running:
            state.running = RunningApplication(entry.selector, entry.display_name, entry.session)
        return dict(selector=entry.selector, _nativeState=state)

    async def list_applications(self) -> list[AppInfo]:
        return await self.discovery.run()

    def window(self, app: UIHandle | None, ref: int) -> UIHandle:
        """Select the canonical fixed Window, optionally checking App ownership."""
        from computer_use.interaction.query.instances.scope import adopt, require_identity
        from computer_use.native.scopes import RunningApplication
        from computer_use.session.state import AppState

        entry = self.windows.resolve(ref, self.controller.transport.session)
        instance = self.windows.application(entry.session, entry.application, entry.name)
        if app is not None:
            validate_session(self.controller, app)
            running = self.controller.state(app).running
            application = running.application if running is not None else app._instance.selector
            if not self.windows.belongs(ref, application):
                raise local_failure(
                    "window does not belong to this App's confirmed application",
                    "stale_target",
                )
        if app is None or app._instance.selector != entry.application:
            state = AppState(
                entry.application, running=RunningApplication(entry.application, entry.name, entry.session)
            )
            app = self.controller.adapters.create_app(
                self.controller, dict(selector=entry.application, _nativeState=state)
            )
        app._instance = instance
        # A desktop catalogue is sufficient to select a weak-AX Window. It is
        # partial and cannot retire other windows; subsequent reads refresh it.
        if instance.directory is None:
            adopt(self.controller, app, entry.directory)
        if entry.identity not in instance.evidence:
            instance.evidence[entry.identity] = WindowEvidence(entry.identity, Knowledge.UNKNOWN)
        require_identity(app, entry.identity)
        identity = entry.identity
        key = (identity.window, identity.surface, identity.instance)
        value = instance.views.get(key)
        if value is None:
            selection = self.controller.reader._selection(
                entry.application, entry.name, entry.directory, identity.window
            )
            state = AppState(
                entry.application,
                running=RunningApplication(entry.application, entry.name, entry.session),
                selection=selection,
                refs=instance.refs,
                directory=instance.directory,
                windows={str(k): v for k, v in instance.windows.items()},
            )
            value = self.controller.adapters.create_window(
                self.controller, dict(selector=entry.application, _nativeState=state), app, identity.window
            )
            instance.views[key] = value
        self.windows.attach_image(instance, identity, value)
        return value

    async def read(
        self,
        ui: UIHandle,
        view: str,
        full: bool,
        menu_path: Sequence[str] | None,
        allow_foreground: bool = False,
        *,
        related=None,
    ) -> Observation:
        try:
            return await self.read_pipeline.run(
                ui, view, full, menu_path, allow_foreground=allow_foreground, related=related
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
            window_refs = {}
            catalogues = {}
            for index, item in enumerate(desktop["windows"]):
                if "window" not in item or not item.get("selector"):
                    continue
                selector = item["selector"]
                if selector not in catalogues:
                    catalogues[selector] = dict(
                        kind="surfaces",
                        session=self.controller.transport.session,
                        platform=self.controller.transport.platform,
                        application=selector,
                        observed=observed["observed"],
                        surfaces=[],
                        focusedWindow=None,
                        rootDiscoveryComplete=False,
                        stopReasons=["desktop catalogue is a visible-window subset"],
                        provider=dict(status="notRequested", attribute=None, writeStatus=None),
                    )
                catalogues[selector]["surfaces"].append(item["window"])
            aliases = {
                selector: self.windows.register_directory(
                    directory, next(item["app"] for item in desktop["windows"] if item.get("selector") == selector)
                )
                for selector, directory in catalogues.items()
            }
            for index, item in enumerate(desktop["windows"]):
                if "window" in item and item.get("selector") in aliases:
                    by_window = {window: int(ref) for ref, window in aliases[item["selector"]].items()}
                    reference = by_window.get(item["window"]["window"])
                    if reference is not None:
                        window_refs[str(index)] = reference
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
        return publish(self.controller, None, DisplayDocument.join(lines), screenshot, full=full, view="desktop")

    def validate_snapshot(self, ui: UIHandle, snapshot: ElementSnapshot, *, allow_unknown: bool = False) -> None:
        """Check that a retained element still belongs to the handle's scope.

        Args:
            ui: App/Window view constraining the element's allowed membership.
            snapshot: Element identity and the scope recorded when it was observed.
            allow_unknown: Permit reference retention before a fresh directory
                query, never native observation or input authorization.
        """
        validate_session(self.controller, ui)
        target = snapshot["binding"]
        self.controller.validate_element(target)
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
        if snapshot.get("scope_kind") == "applicationMenu" and "menu_window_identity" in snapshot:
            menu_window = snapshot["menu_window_identity"]
            if menu_window is None:
                if ui._anchor is None:
                    return
            else:
                # The containing window is part of this snapshot's provenance,
                # not whatever another menu read last recorded for the surface.
                from computer_use.interaction.query.instances.scope import require_identity

                require_identity(ui, menu_window, current=not allow_unknown)
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
            "this element is outside the allowed App/Window scope",
            "stale_target",
        )

    async def element(
        self, ui: UIHandle, target: int | str | ElementPath, timeout_ms: int, control: Control
    ) -> ElementSnapshot:
        """Resolve an observed number, exact name, or path within this UI view.

        Args:
            ui: App/Window view constraining lookup and reference ownership.
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
        # Application aliases share the last actually delivered lookup scope.
        # A fixed Window retains its own menu view and always bounds window
        # lookup by its original root. Pending reads grant neither alias a new
        # name/path context.
        lookup_context = instance.lookup_context if ui._anchor is None else ui._lookup_context
        if ui._anchor is None and lookup_context is None:
            raise local_failure(
                "this App has no delivered lookup scope",
                "stale_target",
            )
        context = ui._anchor or (lookup_context.window.window if lookup_context.window is not None else None)
        menu_only = lookup_context.menu_only if lookup_context is not None else False
        owners = list(lookup_context.menus) if lookup_context is not None else []

        def observed_query(item):
            owner = instance.owners.get(item["window"])
            query = self.controller.state(owner).query_target if owner is not None else None
            return query is not None and query.node is not None and (
                query.session, query.instance, query.root_window, query.surface
            ) == (instance.session, item["instance"], item["window"], item["surface"])

        if ui._anchor is None and not menu_only and lookup_context.window is not None:
            reselected = False
            old = instance.members.get(context)
            if old is None or WindowIdentity.of(old) != lookup_context.window or not observed_query(old):
                # A delivered popup image/old child is not the App's lifetime.
                # Use a current display root only if its semantic query scope
                # was actually delivered, never an unseen pending observation.
                current = display_selection(self.controller, instance, instance.directory)
                replacement = instance.members.get(current.root.root_window) if current is not None else None
                if (
                    replacement is not None
                    and WindowIdentity.of(replacement) in instance.delivered_lookup_windows
                    and observed_query(replacement)
                ):
                    context = replacement["window"]
                    owners = []
                    reselected = True
            if not reselected:
                from computer_use.interaction.query.instances.scope import require_identity

                require_identity(ui, lookup_context.window)
        choices = {} if menu_only else allowed(ui)
        choices = {
            key: item
            for key, item in choices.items()
            if context is not None and ancestor(instance.directory, key, context)
        }
        matched, incomplete = {}, []
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
            elif key == context:
                incomplete.append(key)
            # Ancillary name scopes come from owned AX query evidence. A new
            # child seen only as pixels is not a new required AX search scope;
            # every previously AX-observed descendant still participates.
        end = monotonic() + timeout_ms / 1000
        # Waiting belongs to the complete lookup, not its first member. A
        # toolbar with no match must not consume the deadline before the
        # already-observed document has even been searched.
        while True:
            matched.clear()
            for owner in dict.fromkeys(owners):
                try:
                    snapshot = await self.lookup.arun(FindElement(owner, path, 0, False), control)
                except ComputerUseError as error:
                    if error.code == "element_not_found":
                        continue
                    raise
                binding = snapshot["binding"]
                matched[(binding.session, binding.surface, binding.node)] = snapshot
            if matched or incomplete or monotonic() >= end:
                break
            await asyncio.sleep(min(0.025, max(0, end - monotonic())))
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
        instance = ui._instance
        if instance.session is not None and instance.session != target.session:
            raise local_failure("element belongs to another session", "stale_target")

    @staticmethod
    def keyboard_context(ui: UIHandle, *, require_observed: bool = True) -> Binding:
        """Pin explicit Window or the last application context actually delivered."""
        from computer_use.interaction.query.instances.scope import require_identity

        instance = ui._instance
        fixed = getattr(ui, "_window_identity", None)
        if fixed is not None:
            root_identity = WindowIdentity(ui._anchor, fixed.surface, fixed.instance)
            require_identity(ui, root_identity)
            identity = ui._input_context or root_identity
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

    async def keyboard(self, ui: UIHandle, method: str, parameters: Record, control: Control) -> ActionResult:
        from computer_use.interaction.query.instances.scope import require_identity

        # Capture before a new query: neither focus changes nor hidden sampling
        # may turn this operation into input in another document.
        validate_session(self.controller, ui)
        target = self.keyboard_context(ui)
        identity = WindowIdentity(target.root_window, target.surface, target.instance)
        await refresh(self.controller, ui)
        require_identity(ui, identity, current=True)
        if ui._anchor is not None and not ancestor(ui._instance.directory, identity.window, ui._anchor):
            raise local_failure(
                "the delivered receiver is no longer confirmed inside this fixed Window",
                "observation_incomplete",
            )
        intent = Intent(dict(type_text="insert", press_key="key").get(method, method), parameters)
        return await self.write.arun(ExecuteAction(ui, intent, target), control)

    async def act_element(
        self, ui: UIHandle, snapshot: ElementSnapshot, intent: str, parameters: Record, control: Control
    ) -> ActionResult:
        """Validate an element and dispatch direct or current-container input.

        Args:
            ui: App/Window view owning the element and its member-window scope.
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
                ui, Intent(intent, parameters), snapshot["binding"], forward_current=bool(forward), snapshot=snapshot
            ),
            control,
        )

    async def pointer(
        self, ui: UIHandle, intent: str, points: tuple[Point, ...], options: Record, control: Control
    ) -> ActionResult:
        return await InterfacePointerPipeline(self.controller, self.write).run(ui, intent, points, options, control)

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
        if isinstance(request, ObserveUI):
            return await api.read(
                request.owner, request.view, request.full, request.menu_path, request.allow_foreground
            )
        if isinstance(request, ObserveDesktop):
            return await api.read_desktop(request.view, request.full)
        if isinstance(request, ObserveElement):
            return await api.read_element(request.owner, request.snapshot, request.cursor)
        if isinstance(request, ObserveText):
            return await api.read_text(request.owner, request.snapshot, request.cursor, request.length)
        if isinstance(request, DiscoverApps):
            return await api.list_applications()
        if isinstance(request, ResolveElement):
            return await api.element(request.owner, request.target, request.timeout_ms, control)
        if isinstance(request, ActOnElement):
            return await api.act_element(request.owner, request.snapshot, request.intent, request.parameters, control)
        if isinstance(request, UIKeyboard):
            return await api.keyboard(request.owner, request.method, request.parameters, control)
        if isinstance(request, UIPointer):
            return await api.pointer(request.owner, request.intent, request.points, request.options, control)
        raise TypeError("Unknown agent interface request")

    def _run(self, value: InterfaceRequest, control: Control) -> AgentResult:
        raise TypeError("Agent interface requests require arun()")


if TYPE_CHECKING:
    from computer_use.protocol.records import NativeDirectory as DirectoryFacts
if TYPE_CHECKING:
    from computer_use.interaction.records import AgentResult
