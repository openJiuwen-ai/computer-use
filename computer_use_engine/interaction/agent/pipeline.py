"""Translate public UI handles into scoped session reads, queries, and input."""

from __future__ import annotations

from dataclasses import replace
from time import monotonic
from typing import TYPE_CHECKING, cast

from computer_use.diagnostics import local_failure
from computer_use.errors import ComputerUseError
from computer_use.interaction.query.instances.membership import ancestor, ancestry, in_scope
from computer_use.interaction.query.instances.pipeline import ApplicationDiscoveryPipeline
from computer_use.interaction.query.instances.scope import allowed, refresh, retain_fixed_failure, validate_session
from computer_use.interaction.read.presentation.content import DisplayText, template
from computer_use.interaction.read.presentation.document import DisplayDocument
from computer_use.interaction.read.presentation.ir import freeze
from computer_use.interaction.read.views.pipeline import InterfaceReadPipeline, member_target
from computer_use.interaction.read.views.publication import publish
from computer_use.native.contracts import Intent, Knowledge
from computer_use.native.query.directory import select_surface
from computer_use.native.scopes import FixedWindowInvalidated, FixedWindowUnconfirmed
from computer_use.pipeline import Contract, Effect
from computer_use.pipeline.core import Node
from computer_use.protocol.element_path import element_path
from computer_use.session.cursors import TextCursors
from computer_use.session.interfaces import ApplicationEntry, ApplicationReferences, WindowLifetime

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
    from computer_use.native.contracts import Binding
    from computer_use.pipeline.core import Control
    from computer_use.protocol.records import Record
    from computer_use.types import ActionResult, AppInfo, ElementPath, Observation, Point, TextPage


class AgentInterfacePipeline:
    """Translate public UI operations into scoped session execution.

    App and Window handles supply instance membership; Element snapshots supply
    observed native bindings. This adapter resolves those scopes and retains
    the public references and cursors used by later calls.

    Attributes:
        controller: Session readers, native connection, and write entry point.
        references: Registry mapping public application numbers to instance choices.
        text_cursors: Registry linking text continuation tokens to captured sources.
        discovery: Flow publishing application and root-window choices.
        read_pipeline: Flow observing App/Window views and publishing their results.
    """

    def __init__(self, controller: SessionController) -> None:
        self.controller = controller
        self.references = ApplicationReferences()
        self.text_cursors = TextCursors()
        self.discovery = ApplicationDiscoveryPipeline(controller.reader.directory, self.references)
        self.read_pipeline = InterfaceReadPipeline(controller)

    def application(self, ref: int) -> Record:
        """Create App initialization state from a published application choice.

        Args:
            ref: Integer application/instance reference returned by discovery.

        Returns:
            Selector and native state for constructing the corresponding App handle.
        """
        from computer_use.native.scopes import RunningApplication, WorkingWindow, window_binding
        from computer_use.session.state import AppState

        entry = self.references.resolve(ref, self.controller.transport.session)
        name = entry.application_name or entry.display_name
        # A desktop selector may identify a particular window, rather than its
        # process. Resolve unanchored selectors through the ordinary directory
        # path; only a retained directory already proves the canonical owner.
        state = AppState(entry.selector)
        if entry.window is not None:
            state.running = RunningApplication(entry.selector, name, entry.session)
            state.selection = WorkingWindow(
                entry.selector, name, window_binding(select_surface(entry.directory, entry.window, follow_modal=False))
            )
            state.directory = entry.directory
        return dict(selector=entry.selector, _nativeState=state)

    async def list_applications(self) -> list[AppInfo]:
        return await self.discovery.run()

    def window(self, app: UIHandle, ref: int) -> UIHandle:
        """Get or create the Window view for a confirmed instance member.

        Args:
            app: App handle that owns the member-window catalogue.
            ref: Integer window reference published by an App observation.

        Returns:
            A Window handle anchored to that member's native identity.
        """
        from computer_use.native.scopes import RunningApplication
        from computer_use.session.state import AppState

        validate_session(self.controller, app)
        # The pipeline shares retained bindings with their owning service.
        # pylint: disable-next=protected-access
        instance = app._instance
        if instance.anchor is None or ref not in instance.windows:
            raise local_failure(
                "window is not in this instance member directory",
                "read the App and use an integer from Windows",
                "stale_target",
            )
        key = instance.windows[ref]
        item = instance.members[key]
        identity = (key, item["surface"], item["instance"])
        value = instance.views.get(identity)
        if value is None:
            # pylint: disable-next=protected-access
            selection = self.controller.reader._selection(
                instance.anchor.application, instance.anchor.name, cast("DirectoryFacts", instance.directory), key
            )
            state = AppState(
                str(instance.selector),
                running=RunningApplication(
                    instance.anchor.application, instance.anchor.name, instance.anchor.root.session
                ),
                selection=selection,
                refs=instance.refs,
                directory=instance.directory,
                windows={str(k): v for k, v in instance.windows.items()},
            )
            value = self.controller.adapters.create_window(
                self.controller, dict(selector=state.selector, _nativeState=state), app, key
            )
            instance.views[identity] = value
        return value

    async def read(self, ui: UIHandle, view: str, full: bool, menu_path: Sequence[str] | None) -> Observation:
        try:
            return await self.read_pipeline.run(ui, view, full, menu_path)
        except (FixedWindowInvalidated, FixedWindowUnconfirmed) as error:
            # A principal read performs its own later directory collection;
            # consume its exact root evidence just like the initial refresh.
            # pylint: disable-next=protected-access
            selection = ui._instance.anchor or self.controller.state(ui).selection
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
                from computer_use.session.events import pending_observations_discarded

                self.controller.desktop.reset()
                pending_observations_discarded(self.controller)
                raise
            screenshot = observed["screenshot"]
            # Publish the same public reference namespace as listApps, without
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
            document = observed["document"]
            lines.append(
                replace(
                    document,
                    parts=tuple(
                        replace(part, values=(*part.values, freeze(applications)))
                        if isinstance(part, DisplayText) and part.kind == "desktop"
                        else part
                        for part in document.parts
                    ),
                )
            )
        if view == "tree":
            apps = await self.list_applications()
            lines.append("Desktop application/window directory (not a recursive system tree):")
            lines.extend(
                template(
                    "{0} {1!r} ({2}); computer.app({0})",
                    app.id,
                    app.displayName,
                    "running" if app.isRunning else "installed",
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
                "read the intended App/Window and choose a current element",
                "stale_target",
            )
        if in_scope(ui, target.surface) and (evidence is None or evidence.membership is Knowledge.YES):
            if evidence is not None and identity is None:
                snapshot["window_identity"] = evidence.identity
            return
        # pylint: disable-next=protected-access
        context = ui._instance.menu_contexts.get(target.surface)
        matches_target = (
            True
            if (
                snapshot.get("scope_kind") == "applicationMenu"
                # pylint: disable-next=protected-access
                and target.surface in ui._instance.menu_contexts
                and (
                    # pylint: disable-next=protected-access
                    ui._anchor is None
                    or (
                        context is not None
                        # pylint: disable-next=protected-access
                        and ancestor(cast("DirectoryFacts", ui._instance.directory), context, ui._anchor)
                    )
                )
            )
            else False
        )
        if matches_target:
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
                "read this App/Window again to refresh its evidence; no input was dispatched",
                "observation_incomplete",
            )
        raise local_failure(
            "this element is outside the allowed App/Window scope",
            "read the intended instance or Window and choose its element",
            "stale_target",
        )

    async def element(self, ui: UIHandle, target: int | str | ElementPath, timeout_ms: int) -> ElementSnapshot:
        """Resolve an observed number, exact name, or path within this UI view.

        Args:
            ui: App/Window view constraining lookup and reference ownership.
            target: Integer reference, exact accessible name, or semantic path.
            timeout_ms: Total wait allowed for path lookup, in milliseconds.

        Returns:
            The uniquely matched element's binding and retained observation sources.

        Raises:
            ComputerUseError: The reference is stale, lookup is ambiguous, or an
                incomplete scope prevents confirming a unique result.
        """
        # Exact types preserve scalar validation and identity contracts.
        # pylint: disable-next=unidiomatic-typecheck
        if type(target) is int:
            ref = str(target)
            try:
                # pylint: disable-next=protected-access
                snapshot = cast("ElementSnapshot", ui._instance.refs[ref])
            except KeyError:
                raise local_failure(
                    "element reference was not observed in this instance",
                    "read this App/Window and use an integer element number",
                    "stale_target",
                ) from None
            self.validate_snapshot(ui, snapshot, allow_unknown=True)
            return snapshot
        if not isinstance(target, (str, list)):
            raise local_failure(
                "element expects an integer, exact name, or path",
                "use await ui.element(target); bool and floating references are invalid",
            )
        try:
            path = element_path([target] if isinstance(target, str) else target)
        except Exception as error:
            raise local_failure(
                str(error), "use an exact name or a list of names/role/name/identifier conditions"
            ) from error
        validate_session(self.controller, ui)
        # pylint: disable-next=protected-access
        instance = ui._instance
        # pylint: disable-next=protected-access
        if instance.anchor is None and not ui._lookup_scopes:
            raise local_failure(
                "this UI has no observed lookup scope",
                "read the App and select an instance before locating controls",
                "stale_target",
            )
        # pylint: disable-next=protected-access
        choices = {} if ui._menu_only else allowed(ui)
        matched, incomplete = {}, []
        # pylint: disable-next=protected-access
        owners = list(ui._lookup_scopes)
        for key, item in choices.items():
            if key not in instance.observed or item["node"] is None:
                incomplete.append(key)
            else:
                owners.append(instance.owners[key])
        end = monotonic() + timeout_ms / 1000
        for owner in owners:
            try:
                snapshot = await self.controller.find(owner, path, max(0, round((end - monotonic()) * 1000)), False)
            except ComputerUseError as error:
                if error.code == "element_not_found":
                    continue
                raise
            binding = snapshot["binding"]
            matched[(binding.session, binding.surface, binding.node)] = snapshot
        if len(matched) > 1:
            raise local_failure(
                "the name/path matches controls in several member windows",
                "choose app.window(ref) to narrow the lookup",
                "ambiguous_target",
            )
        if incomplete:
            raise local_failure(
                "some member lookup scopes have not been observed or have no AX root",
                "read a specific Window or use an observed image; uniqueness is not confirmed",
                "observation_incomplete",
            )
        if not matched:
            raise local_failure(
                "no matching element in the completed observed scope",
                "read this UI and check the exact name/path",
                "element_not_found",
            )
        snapshot = next(iter(matched.values()))
        self.validate_snapshot(ui, snapshot)
        return snapshot

    @staticmethod
    def keyboard_context(ui: UIHandle, *, require_observed: bool = True) -> Binding:
        """Choose the current member window for unqualified keyboard input.

        The focused member takes precedence; otherwise the fixed anchor or a
        previously confirmed member context supplies the receiver.

        Args:
            ui: App/Window view bounding the possible input windows.
            require_observed: Require the chosen member to have been observed.

        Returns:
            Window-level binding used for native keyboard preparation.
        """
        # pylint: disable-next=protected-access
        instance = ui._instance
        if instance.anchor is None:
            raise local_failure("this UI has no observed input scope", "read the App/Window first", "stale_target")
        choices = allowed(ui)
        focused = cast("DirectoryFacts", instance.directory).get("focusedWindow")
        # pylint: disable-next=protected-access
        anchor = ui._anchor or instance.anchor.root.root_window
        # A declared focus window wins only inside the selected scope. Native
        # validation still confirms the keyboard recipient immediately on send.
        start = focused if focused in choices else anchor
        target = select_surface(instance.directory, start)
        key = next((key for key, item in choices.items() if item["surface"] == target.surface), None)
        if key is None:
            raise local_failure(
                "the current receiver is outside this UI scope",
                "read this Window or specify an Element",
                "input_unavailable",
            )
        if focused not in choices and len(choices) > 1 and target.surface == instance.members[anchor]["surface"]:
            previous = getattr(ui, "_context", None)
            if previous in choices:
                key = previous
            # pylint: disable-next=protected-access
            elif ui._anchor is None:
                raise local_failure(
                    "the member input context is not uniquely confirmed",
                    "read the intended Window or specify an Element",
                    "ambiguous_target",
                )
            # An explicit Window already fixes the original input scope. Keep
            # its observed anchor; native preparation resolves an attached
            # editor without selecting an unobserved member here.
        if require_observed and key not in instance.observed:
            raise local_failure(
                "the current member window has not been observed",
                "read this App/Window before input to the new context",
                "stale_target",
            )
        return replace(member_target(instance, choices[key]), node=None)

    async def keyboard(self, ui: UIHandle, method: str, parameters: Record) -> ActionResult:
        await refresh(self.controller, ui)
        target = self.keyboard_context(ui)
        intent = Intent(dict(typeText="insert", pressKey="key").get(method, method), parameters)
        # pylint: disable-next=protected-access
        return await self.controller._execute(ui, intent, target)

    async def act_element(
        self, ui: UIHandle, snapshot: ElementSnapshot, intent: str, parameters: Record
    ) -> ActionResult:
        """Validate an element and dispatch direct or current-container input.

        Args:
            ui: App/Window view owning the element and its member-window scope.
            snapshot: Bound element and its container classification.
            intent: Requested operation, such as insert, replace, click, or key.
            parameters: Text, key, or other parameters for the operation.

        Returns:
            Execution outcome reported by the session's write branch.
        """
        self.validate_snapshot(ui, snapshot, allow_unknown=True)
        # pylint: disable-next=protected-access
        if ui._instance.anchor is not None:
            await refresh(self.controller, ui)
        self.validate_snapshot(ui, snapshot)
        if snapshot.get("container") and intent == "replace":
            raise local_failure(
                "full replacement requires a concrete input control, not a container",
                "select the intended input Element",
            )
        forward = (
            snapshot.get("container")
            and intent in ("insert", "paste", "key", "focus")
            and not (intent in ("insert", "paste") and parameters.get("text") == "")
        )
        if forward:
            # pylint: disable-next=protected-access
            return await self.controller._execute(
                ui, Intent(intent, parameters), snapshot["binding"], forward_current=True
            )
        return await self.controller.act_element(ui, snapshot, None, intent, parameters)

    async def pointer(self, ui: UIHandle, intent: str, points: tuple[Point, ...], options: Record) -> ActionResult:
        from computer_use.interaction.write.ui.pipeline import InterfacePointerPipeline

        return await InterfacePointerPipeline(self.controller).run(ui, intent, points, options)

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
        if ui._instance.anchor is None:
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
        controller: Session whose UI adapter handles the selected operation.
    """

    def __init__(self, controller: SessionController) -> None:
        self.controller = controller
        super().__init__("AgentInterface", Contract(object, object, frozenset(Effect)))

    async def _arun(self, request: InterfaceRequest, control: Control) -> AgentResult:
        api = self.controller.interfaces
        if isinstance(request, ObserveUI):
            return await api.read(request.owner, request.view, request.full, request.menu_path)
        if isinstance(request, ObserveDesktop):
            return await api.read_desktop(request.view, request.full)
        if isinstance(request, ObserveElement):
            return await api.read_element(request.owner, request.snapshot, request.cursor)
        if isinstance(request, ObserveText):
            return await api.read_text(request.owner, request.snapshot, request.cursor, request.length)
        if isinstance(request, DiscoverApps):
            return await api.list_applications()
        if isinstance(request, ResolveElement):
            return await api.element(request.owner, request.target, request.timeout_ms)
        if isinstance(request, ActOnElement):
            return await api.act_element(request.owner, request.snapshot, request.intent, request.parameters)
        if isinstance(request, UIKeyboard):
            return await api.keyboard(request.owner, request.method, request.parameters)
        if isinstance(request, UIPointer):
            return await api.pointer(request.owner, request.intent, request.points, request.options)
        raise TypeError("Unknown agent interface request")

    def _run(self, value: InterfaceRequest, control: Control) -> AgentResult:
        raise TypeError("Agent interface requests require arun()")


if TYPE_CHECKING:
    from computer_use.protocol.records import NativeDirectory as DirectoryFacts
if TYPE_CHECKING:
    from computer_use.interaction.records import AgentResult
