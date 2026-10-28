"""Select an input route, then execute its family through the native-unit scope."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.errors import ComputerUseError
from computer_use.interaction.write.model import ResolvedWrite, RoutePlan
from computer_use.interaction.write.progress import failure
from computer_use.interaction.write.routes.selection.model import SelectionRequest
from computer_use.interaction.write.routes.selection.pipeline import RouteChoicePipeline
from computer_use.native.contracts import Knowledge
from computer_use.pipeline import Contract, Pipeline, Select, Sequence

if TYPE_CHECKING:
    from computer_use.interaction.write.model import AttemptOutcome
    from computer_use.interaction.write.progress import WriteRunState
    from computer_use.native.port import NativePort
    from computer_use.pipeline.core import Control, Step


class RouteSelectionPipeline(Sequence["Any", "Any"]):
    """Select a native command for the resolved write.

    Combine current target facts with a snapshot of the write's attempt history.
    Return the chosen primitive and preparation mode as a RoutePlan, using the
    last route failure to explain selection exhaustion when applicable.

    Attributes:
        native: Supplies the host policy used by route selection.
        state: Owns the route history, accumulated effects, and last route failure.
    """

    def __init__(self, native: NativePort, state: WriteRunState) -> None:
        self.native, self.state = native, state
        # The child is an explicit pure graph: candidate generation, history
        # filtering, ready/background/property/foreground branches and refusal.
        super().__init__(name="RouteSelectionPipeline", steps=[RouteChoicePipeline()])
        self.contract = Contract(
            ResolvedWrite,
            RoutePlan,
            requires=("current native contracts", "read-only attempts"),
            failures=("no route", "ambiguous action", "target boundary"),
        )

    def _run(self, resolved: ResolvedWrite, control: Control) -> RoutePlan:
        """Select a command using a frozen view of this write's route history."""
        # Snapshot history once so selection and failure reporting use the same
        # attempt and dispatch state.
        view = self.state.view()
        target, spec = resolved.target, resolved.bound.spec
        request = SelectionRequest(
            target.facts, spec.intent, self.native.policy, view.history, target.needs_editing_evidence
        )
        try:
            command = super()._run(request, control)
        except ComputerUseError as unavailable:
            matches_unavailable = (
                True
                if (
                    unavailable.code == "input_unavailable"
                    and view.last_route_failure is not None
                    and not target.needs_editing_evidence
                    and target.facts.enabled != Knowledge.NO
                )
                else False
            )
            if matches_unavailable:
                last = view.last_route_failure
                raise failure(
                    last.error_code, target.facts.target_label, last.detail, view.dispatched, view.preparation
                ) from unavailable
            raise
        return RoutePlan(resolved, command.primitive, command.prepare, command.foreground, command.click_selection)

    async def _arun(self, resolved: ResolvedWrite, control: Control) -> RoutePlan:
        return self._run(resolved, control)


class RouteExecutionPipeline(Pipeline["RoutePlan", "AttemptOutcome"]):
    """Execute the native unit belonging to the chosen route family.

    Translate the RoutePlan into a command and send it through the shared
    NativeUnitPipeline. Return the unit's AttemptOutcome for progress tracking.

    Attributes:
        native: Driver port used by the selected execution branch.
        state: Write state shared by native-unit execution and progress tracking.
    """

    def __init__(self, native: NativePort, state: WriteRunState) -> None:
        self.native, self.state = native, state
        super().__init__(root=self.build())

    def build(self) -> Step[RoutePlan, AttemptOutcome]:
        native, state = self.native, self.state
        from computer_use.interaction.write.routes.native_unit.passes.build_unit_command import (
            stage as build_unit_command,
        )
        from computer_use.interaction.write.routes.native_unit.pipeline import NativeUnitPipeline
        from computer_use.interaction.write.routes.passes.select_route_family import stage as select_route_family

        # Every family borrows this same native scope and the operation's state.
        unit = NativeUnitPipeline(native, state)
        family_units = {
            "ObjectActionPipeline": ("native_action", "native_selection"),
            "TextEditPipeline": ("native_text_edit", "native_value_replace"),
            "KeyboardPipeline": ("key_sequence",),
            "ClipboardPipeline": ("clipboard_paste",),
            "PointerPipeline": ("pointer_sequence",),
            "NativeScrollPipeline": ("native_scroll",),
            "FocusPreparationPipeline": ("prepare_receiver",),
            "LaunchPipeline": ("launch_application",),
        }
        family = {unit_name: name for name, units in family_units.items() for unit_name in units}
        branches = {name: Sequence(name=name, steps=[build_unit_command(), unit]) for name in family_units}
        return Select(name="RouteExecutionPipeline", selector=select_route_family(family=family), branches=branches)


if TYPE_CHECKING:
    pass
