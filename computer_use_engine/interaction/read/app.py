"""Python coordination of discovery, fixed-window observation and projection.

Used by the public v5 NativeController for a fixed working-window session.
Native operations collect facts; they neither select a new working window nor
combine an old tree with a newly discovered dialog on behalf of Python.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING

from computer_use.errors import ComputerUseError
from computer_use.interaction.read.acquisition.components.support import (
    ObservationGap,
    collect_component,
    observation_gap,
)
from computer_use.interaction.read.acquisition.directory.service import NativeDirectory, select_surface
from computer_use.interaction.read.acquisition.images.capture import NativeImages, WindowImage
from computer_use.interaction.read.acquisition.images.overview import NativeOverview
from computer_use.interaction.read.acquisition.images.overview import WindowOverview as WindowOverview
from computer_use.interaction.read.acquisition.menus.query import MenuRead as MenuRead
from computer_use.interaction.read.acquisition.menus.query import NativeMenus
from computer_use.interaction.read.acquisition.reader import NativeReader
from computer_use.interaction.read.delivery.image_caption import window_state_summary
from computer_use.interaction.read.facts.snapshot import SemanticSnapshot
from computer_use.interaction.read.model import AppObservation, MenuAppObservation, WindowSelectionRequired
from computer_use.interaction.read.numbering.pipeline import number_snapshots
from computer_use.interaction.read.numbering.references import DisplayReferences
from computer_use.interaction.read.presentation.content import (
    DisplayText,
    join,
    render_text,
    template,
)
from computer_use.interaction.read.presentation.content import (
    json_value as quoted,
)
from computer_use.interaction.read.presentation.document import DisplayDocument
from computer_use.native.contracts import Binding, Dispatch
from computer_use.native.scopes import (
    RunningApplication,
    WorkingWindow,
    window_binding,
)
from computer_use.native.scopes import (
    SelectedApplicationWindow as SelectedApplicationWindow,
)
from computer_use.native.scopes import (
    StartingApplication as StartingApplication,
)
from computer_use.protocol.captures import capture_metadata

if TYPE_CHECKING:
    from typing import Any, Awaitable, Callable, Iterable, Sequence

    from computer_use.interaction.read.presentation.preparation import PreparedSnapshot
    from computer_use.interaction.read.presentation.records import RenderedTree
    from computer_use.interaction.records import Remaining
    from computer_use.native.port import NativePort
    from computer_use.pipeline.core import Control, T
    from computer_use.protocol.records import ApplicationInfo
    from computer_use.protocol.records import NativeDirectory as DirectoryFacts


def failure(code: str, detail: str) -> ComputerUseError:
    return ComputerUseError(
        dict(
            code=code,
            effect="none",
            target="working window",
            detail=detail,
            next="read the window directory again; explicitly select a working window if needed",
        )
    )


def window_summary_content(
    directory: DirectoryFacts,
    windows: Mapping[str, str],
    *,
    selection: WorkingWindow | None = None,
    target: Binding | None = None,
    scope_confirmed: bool = False,
) -> DisplayText:
    """Organize window identities and relationships without formatting their labels."""
    aliases = {window: alias for alias, window in windows.items()}
    entries = []
    for item in directory["surfaces"]:
        flags: list[str | DisplayText] = []
        if selection is not None and item["window"] == selection.root.root_window:
            flags.append("root")
        if target is not None and item["surface"] == target.surface:
            flags.append("current" if scope_confirmed else "observed")
        if item["window"] == directory.get("focusedWindow"):
            flags.append("reported focus")
        if item["modal"] is True:
            flags.append("modal")
        if item["accessibility"] and not item["nativeWindow"]:
            flags.append("native window unmatched")
        if item["parentState"] == "known":
            flags.append(template("parent={}", aliases.get(item["parent"], quoted(item["parent"]))))
        elif item["parentState"] == "unknown":
            flags.append("parent unknown")
        entries.append(
            template(
                "{} {}{}",
                aliases[item["window"]],
                quoted(item["title"]),
                template(" ({})", join(flags, separator=", ")) if flags else "",
            )
        )
    return join(entries, separator="; ")


def window_summary(
    directory: DirectoryFacts,
    windows: Mapping[str, str],
    *,
    selection: WorkingWindow | None = None,
    target: Binding | None = None,
    scope_confirmed: bool = False,
) -> str:
    """Render window labels for explicit text callers."""
    return render_text(
        window_summary_content(directory, windows, selection=selection, target=target, scope_confirmed=scope_confirmed)
    )


class NativeAppReader:
    """Coordinate application discovery, native facts, menus, and window images."""

    _window_references: DisplayReferences | None
    _element_references: DisplayReferences | None

    def __init__(self, port: NativePort) -> None:
        self.port = port
        self.directory = NativeDirectory(port)
        self.reader = NativeReader(port)
        self.images = NativeImages(port)
        self.overviews = NativeOverview(port)
        self.menus = NativeMenus(self.directory, self.reader)
        self._window_references = None
        self._element_references = None

    def number_snapshots(
        self, snapshots: Iterable[SemanticSnapshot], *, _control: Control | None = None
    ) -> tuple[SemanticSnapshot, ...]:
        """Assign shared display references to the supplied native snapshots.

        The allocator is retained across reads and renewed when the driver session changes.

        Args:
            snapshots: Snapshots to number in their presentation order.
            _control: Execution metadata inherited from the enclosing read.
        """
        snapshots = tuple(snapshots)
        if not snapshots:
            return ()
        session = snapshots[0].facts["session"]
        if self._element_references is None or self._element_references.session != session:
            self._element_references = DisplayReferences(session)
        return number_snapshots(snapshots, self._element_references, _control=_control)

    def window_aliases(self, directory: DirectoryFacts) -> dict[str, str]:
        if self._window_references is None or self._window_references.session != directory["session"]:
            self._window_references = DisplayReferences(directory["session"])
        return {
            self._window_references.reference((item["window"], item["instance"], item["surface"])): item["window"]
            for item in directory["surfaces"]
        }

    async def read(
        self,
        selector: str | RunningApplication | WorkingWindow,
        *,
        window: str | None = None,
        fields: list[str] | None = None,
        max_nodes: int = 1000,
        max_depth: int | None = None,
        max_dimension: int = 2048,
        timeout_ms: int = 30000,
        allow_launch: bool = True,
        menu_path: Sequence[str] | None = None,
        on_scope: Callable[[RunningApplication | WorkingWindow], None] | None = None,
        view: str = "both",
        overview: bool = True,
    ) -> AppObservation | MenuAppObservation:
        """Read an application through request, acquisition and delivery pipelines.

        Args:
            fields: Requested or accounted native fields; unknown fields remain explicit facts.
            max_nodes: Maximum number of native objects this collection may read.
            max_depth: Maximum native relation depth; None uses the existing platform default.
            max_dimension: Maximum image edge length in pixels, preserving aspect ratio.
            timeout_ms: Maximum duration in milliseconds, bounded by the enclosing call deadline.
            allow_launch: Whether this request permits explicit startup when no running scope is selected.
            menu_path: Requested menu path; None keeps the ordinary window read.
            on_scope: Callback that retains the established running/working scope in its
                existing owner.
            view: Requested observation components: tree, image, or both.
        """
        from computer_use.interaction.read.app_read.model import ReadRequest
        from computer_use.interaction.read.pipeline import ApplicationObservationPipeline

        request = ReadRequest(
            selector,
            window,
            fields,
            max_nodes,
            max_depth,
            max_dimension,
            timeout_ms,
            allow_launch,
            menu_path,
            on_scope,
            view,
            overview,
        )
        return await ApplicationObservationPipeline(self).arun(request)

    async def _window_choices(
        self,
        error: ComputerUseError,
        application: str,
        name: str,
        directory: DirectoryFacts,
        remaining: Remaining,
        *,
        selection: WorkingWindow | None = None,
        _control: Control | None = None,
        include_overview: bool = True,
    ) -> WindowSelectionRequired:
        """Build a selection response when the read cannot choose one working window.

        Args:
            error: Original failure; effect/uncertainty information must be preserved.
            directory: Observed application/window catalogue, including incomplete
                relationships.
            remaining: Callback returning milliseconds left on the original call; may raise on
                expiration.
            selection: Fixed working-instance/root-window selection, when established.
            _control: Optional inherited control metadata; a subpipeline does not start a new
                call.
            include_overview: Whether the window-choice response includes independent window previews.
        """
        from computer_use.interaction.read.acquisition.choices.model import WindowChoiceRequest
        from computer_use.interaction.read.acquisition.choices.pipeline import WindowChoicePipeline

        control = _control or self.port.calls.control(remaining)
        return await WindowChoicePipeline(self).arun(
            WindowChoiceRequest(error, application, name, directory, selection, include_overview),
            control.with_remaining(remaining),
        )

    async def _menu_observation(
        self,
        application: ApplicationInfo,
        selection: WorkingWindow | None,
        directory: DirectoryFacts | None,
        path: Sequence[str],
        launch: Mapping[str, Any] | None,
        remaining: Remaining,
        _control: Control | None = None,
        **limits: Any,
    ) -> MenuAppObservation:
        """Collect and deliver menu evidence for the selected application context.

        Args:
            selection: Fixed working-instance/root-window selection, when established.
            directory: Observed application/window catalogue, including incomplete
                relationships.
            path: Ordered semantic lookup/menu components within the declared target scope.
            launch: Original startup receipt; retained even when later observation fails.
            remaining: Callback returning milliseconds left on the original call; may raise on
                expiration.
            _control: Optional inherited control metadata; a subpipeline does not start a new
                call.
        """
        from computer_use.interaction.control import scope_control
        from computer_use.interaction.read.delivery.menu.model import MenuObservationRequest
        from computer_use.interaction.read.delivery.menu.pipeline import MenuObservationPipeline
        from computer_use.pipeline import Control

        request = MenuObservationRequest(application, selection, directory, path, launch, limits)
        control = scope_control(self.port, remaining, _control or Control())
        return await MenuObservationPipeline(self).arun(request, control)

    async def _discover(
        self,
        selection: WorkingWindow,
        remaining: Remaining,
        *,
        accessibility: bool = True,
        _control: Control | None = None,
    ) -> tuple[WorkingWindow, DirectoryFacts]:
        """Resolve the existing working-window identity against the current directory.

        Args:
            selection: Fixed working-instance/root-window selection, when established.
            remaining: Callback returning milliseconds left on the original call; may raise on
                expiration.
            accessibility: Whether the directory request asks the provider for accessibility roots.
            _control: Optional inherited control metadata; a subpipeline does not start a new
                call.
        """
        from computer_use.interaction.read.acquisition.scope.model import WorkingScopeRequest
        from computer_use.interaction.read.acquisition.scope.pipeline import WorkingScopeDiscoveryPipeline

        control = _control or self.port.calls.control(remaining)
        return await WorkingScopeDiscoveryPipeline(self).arun(
            WorkingScopeRequest(selection, accessibility), control.with_remaining(remaining)
        )

    async def _startup_directory(
        self,
        application: ApplicationInfo,
        session: str,
        remaining: Remaining,
        *,
        launched: bool,
        installation: str | None = None,
        on_scope: Callable[[RunningApplication | WorkingWindow], None] | None = None,
        _control: Control | None = None,
    ) -> tuple[ApplicationInfo, DirectoryFacts]:
        """Observe the running scope associated with an application startup.

        Args:
            remaining: Callback returning milliseconds left on the original call; may raise on
                expiration.
            on_scope: Callback that retains the established running/working scope in its
                existing owner.
            _control: Optional inherited control metadata; a subpipeline does not start a new
                call.
        """
        from computer_use.interaction.read.app_read.startup.model import StartupDirectoryState
        from computer_use.interaction.read.app_read.startup.pipeline import StartupDirectoryPipeline

        control = _control or self.port.calls.control(remaining)
        return await StartupDirectoryPipeline(self).arun(
            StartupDirectoryState(application, session, launched, installation, on_scope),
            control.with_remaining(remaining),
        )

    @staticmethod
    def _selection(
        application: str,
        name: str,
        directory: DirectoryFacts,
        root_id: str,
        *,
        previous: WorkingWindow | None = None,
    ) -> WorkingWindow:
        """Establish the working-window scope from the observed application directory.

        Args:
            application: Native identity of the selected application.
            name: Application display name retained in the working scope.
            directory: Current surface catalogue used to resolve the root window.
            root_id: Native identity of the fixed root window.
            previous: Existing working scope whose root identity must still match.

        Returns:
            The existing working scope, or a scope anchored to the resolved root.
        """
        if root_id is not None and not any(item["window"] == root_id for item in directory["surfaces"]):
            if previous is not None:
                from computer_use.native.scopes import FixedWindowInvalidated, FixedWindowUnconfirmed

                if directory["stopReasons"]:
                    raise FixedWindowUnconfirmed(previous.root)
                raise FixedWindowInvalidated(
                    previous.root, "The fixed working window is absent from the complete current directory."
                )
            raise failure(
                "observation_incomplete" if directory["stopReasons"] else "stale_target",
                "The fixed working window is missing from the current directory; another document was not selected.",
            )
        root = window_binding(select_surface(directory, root_id, follow_modal=False))
        if previous is not None and previous.root != root:
            from computer_use.native.scopes import FixedWindowInvalidated

            raise FixedWindowInvalidated(previous.root, "The fixed working-window identity changed.")
        return previous or WorkingWindow(application, name, root)

    @staticmethod
    def _after_launch(error: BaseException, launch: Mapping[str, Any] | None) -> BaseException:
        """Attach the retained startup receipt to a later observation failure.

        Args:
            error: Original failure; effect/uncertainty information must be preserved.
            launch: Original startup receipt; retained even when later observation fails.
        """
        app = launch["application"]
        detail = launch["detail"] + " Running application: " + app["id"] + "."
        if not isinstance(error, Exception):
            error.add_note(detail + " The following observation was cancelled; launch was not undone.")
            error.launch = launch
            return error
        effect = error.effect if isinstance(error, ComputerUseError) else "none"
        if launch["dispatch"] != Dispatch.NOT_DISPATCHED.value and effect == "none":
            effect = "possible"
        failure_value = ComputerUseError(
            dict(
                code=error.code if isinstance(error, ComputerUseError) else "execution_failed",
                effect=effect,
                target=error.target if isinstance(error, ComputerUseError) else app["displayName"],
                detail=detail
                + " Observation failed: "
                + (error.detail if isinstance(error, ComputerUseError) else str(error)),
                next="read the same App again; inspect listApps() before requesting another launch",
            ),
            cause=error,
        )
        failure_value.launch = launch
        for note in getattr(error, "__notes__", ()):
            failure_value.add_note(note)
        if isinstance(error, WindowSelectionRequired):
            return WindowSelectionRequired(
                failure_value,
                error.application,
                error.display_name,
                error.directory,
                launch,
                error.overview,
                error.selection,
            )
        return failure_value

    async def _component(
        self, name: str, collect: Callable[[], Awaitable[T]]
    ) -> tuple[T | None, ObservationGap | None]:
        return await collect_component(self.port, name, collect)

    @staticmethod
    def _gap(component: str, error: ComputerUseError) -> ObservationGap:
        return observation_gap(component, error)

    @staticmethod
    def _complete_scope(directory: DirectoryFacts) -> bool:
        """Check that the surface catalogue and its parent chains are complete.

        Complete scope evidence requires known parents, reachable ancestors, and acyclic
        window relationships.
        """
        # A partial catalogue cannot prove that an old modal disappeared.
        entries = {item["window"]: item for item in directory["surfaces"]}
        if directory["stopReasons"] or any(item["parentState"] == "unknown" for item in entries.values()):
            return False
        for window in entries:
            seen = set()
            while window is not None:
                if window not in entries or window in seen:
                    return False
                seen.add(window)
                window = entries[window]["parent"]
        return True

    @staticmethod
    def _coverage(directory: DirectoryFacts) -> list[ObservationGap]:
        """Convert incomplete catalogue and parent evidence into observation gaps."""
        gaps = [
            ObservationGap(
                "directory",
                "observation_incomplete",
                reason,
                "inspect the directory again if the required window is missing",
            )
            for reason in directory["stopReasons"]
        ]
        if any(item["parentState"] == "unknown" for item in directory["surfaces"]):
            gaps.append(
                ObservationGap(
                    "directory",
                    "observation_incomplete",
                    "Some window parent relationships are unknown.",
                    "select an observed window explicitly when needed",
                )
            )
        return gaps

    @staticmethod
    def _document(
        selection: WorkingWindow,
        target: Binding,
        captured_directory: DirectoryFacts,
        directory: DirectoryFacts,
        windows: Mapping[str, str],
        prepared: PreparedSnapshot | DisplayDocument | None,
        image: WindowImage | None,
        gaps: tuple[ObservationGap, ...],
        scope_confirmed: bool,
        launch: Mapping[str, Any] | None,
        *,
        image_precedes_tree: bool = False,
    ) -> DisplayDocument:
        """Organize the captured scope, accessibility tree, image notes, and gaps.

        Field quoting and tree rendering remain deferred in the immutable document.
        Captured window and input-scope evidence determines which notes are included.
        """
        selected = next(item for item in captured_directory["surfaces"] if item["surface"] == target.surface)
        lines: list[str | DisplayText | DisplayDocument] = [
            template("Window: {}, App: {}.", quoted(selected["title"]), quoted(selection.name))
        ]
        if launch is not None:
            lines.append(template("Launch: {}", launch["detail"]))
        if len(directory["surfaces"]) > 1:
            lines.append(
                template(
                    "Windows: {}",
                    window_summary_content(
                        directory, windows, selection=selection, target=target, scope_confirmed=scope_confirmed
                    ),
                )
            )
        current = next(
            (
                item
                for item in directory["surfaces"]
                if item["surface"] == target.surface and item["window"] == selected["window"]
            ),
            None,
        )
        matches_scope_confirmed = (
            True
            if scope_confirmed and current is not None and current["accessibility"] and not current["nativeWindow"]
            else False
        )
        if matches_scope_confirmed:
            lines.append(
                "Interaction: this view was found through accessibility, with no matched native window "
                "in this observation."
            )
        focused = directory.get("focusedWindow")
        aliases = {window: ref for ref, window in windows.items()}
        matches_scope_confirmed_condition = (
            True
            if scope_confirmed
            and focused in aliases
            and selected["window"] in aliases
            and focused != selected["window"]
            else False
        )
        if matches_scope_confirmed_condition:
            lines.append(
                template(
                    "Interaction: the application reports focus in window {}; "
                    "this view is window {}. Use app.window({}).read() to inspect that window if relevant to the task.",
                    aliases[focused],
                    aliases[selected["window"]],
                    quoted(aliases[focused]),
                )
            )
        lines.append("Tree: full" if prepared is not None else "Tree: unavailable")
        if image is not None:
            metadata = capture_metadata(image.image)
            lines.append(
                template("Screenshot: window {}x{} px; origin=top-left", metadata["width"], metadata["height"])
            )
            lines.extend(template("Image note: {}", quoted(note)) for note in window_state_summary(metadata))
            if image_precedes_tree:
                lines.append("The screenshot was captured before the refreshed tree; their contents may differ.")
        else:
            lines.append("Screenshot: unavailable")
        if not scope_confirmed:
            lines.append("Current view is not confirmed; components describe the observed view.")
        if prepared is not None:
            lines.append(prepared if isinstance(prepared, DisplayDocument) else DisplayDocument.tree(prepared))
        lines.extend(
            template("{}: {}: {}; next: {}", gap.component, gap.code, quoted(gap.detail), quoted(gap.next))
            for gap in gaps
        )
        return DisplayDocument.join(lines)

    @staticmethod
    def _text(
        selection: WorkingWindow,
        target: Binding,
        captured_directory: DirectoryFacts,
        directory: DirectoryFacts,
        windows: Mapping[str, str],
        rendered: RenderedTree | None,
        image: WindowImage | None,
        gaps: tuple[ObservationGap, ...],
        scope_confirmed: bool,
        launch: Mapping[str, Any] | None,
        *,
        image_precedes_tree: bool = False,
    ) -> str:
        """Render an explicitly requested complete application observation."""
        document = DisplayDocument.join((rendered["text"],)) if rendered is not None else None
        return NativeAppReader._document(
            selection,
            target,
            captured_directory,
            directory,
            windows,
            document,
            image,
            gaps,
            scope_confirmed,
            launch,
            image_precedes_tree=image_precedes_tree,
        ).text
