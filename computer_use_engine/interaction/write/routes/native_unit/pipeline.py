# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""One shared native invocation lease for prepare and dispatch branches."""

from __future__ import annotations

from contextlib import contextmanager
from typing import TYPE_CHECKING

from computer_use.errors import ComputerUseError
from computer_use.interaction.write.model import NativeInvocation
from computer_use.interaction.write.routes.native_unit.passes.dispatch_native_unit import stage as dispatch_native_unit
from computer_use.interaction.write.routes.native_unit.passes.execute_click_attempt import (
    stage as execute_click_attempt,
)
from computer_use.interaction.write.routes.native_unit.passes.prepare_native_context import (
    stage as prepare_native_context,
)
from computer_use.interaction.write.routes.native_unit.passes.select_unit_effect import stage as select_unit_effect
from computer_use.native.contracts import Dispatch
from computer_use.pipeline import Pipeline, Scope, Select

if TYPE_CHECKING:
    from typing import Iterator

    from computer_use.interaction.write.model import AttemptOutcome, PlannedUnit
    from computer_use.interaction.write.progress import WriteRunState
    from computer_use.native.port import NativePort
    from computer_use.pipeline.core import Step


class NativeUnitPipeline(Pipeline["PlannedUnit", "AttemptOutcome"]):
    """Execute one native preparation or input command.

    Sample the remaining budget and mark the unit in flight, then send the selected
    preparation or dispatch command. Return its outcome; an uncertain exception
    records possible effects and retires the native call session.

    Attributes:
        native: Driver port used for command execution and call retirement.
        state: Tracks in-flight work and effects for the enclosing write.
        invocation: Execution scope that samples budget and reconciles in-flight state.
    """

    def __init__(self, native: NativePort, state: WriteRunState) -> None:
        @contextmanager
        def invocation(unit: PlannedUnit, _: object) -> Iterator[NativeInvocation]:
            """Track one in-flight native unit and retain uncertain effects if execution raises."""
            plan = unit.plan
            budget = plan.resolved.bound.remaining()
            if plan.click_selection is not None:
                state.selections.append(plan.click_selection)
            state.in_flight = True
            try:
                yield NativeInvocation(unit, budget)
            except BaseException as error:
                if not (isinstance(error, ComputerUseError) and error.effect == "none"):
                    native.calls.retired = True
                    if plan.prepare:
                        state.preparation.append("unknown")
                    else:
                        state.dispatched = Dispatch.UNKNOWN
                raise
            finally:
                state.in_flight = False

        self.native, self.state, self.invocation = native, state, invocation
        super().__init__(root=self.build())

    def build(self) -> Step[PlannedUnit, AttemptOutcome]:
        native, state, invocation = self.native, self.state, self.invocation
        return Scope(
            name="NativeUnitScope",
            using=invocation,
            body=Select(
                name="PrepareOrDispatch",
                selector=select_unit_effect(),
                branches={
                    "click": execute_click_attempt(native=native, state=state),
                    "prepare": prepare_native_context(native=native, state=state),
                    "dispatch": dispatch_native_unit(native=native, state=state),
                },
            ),
        )
