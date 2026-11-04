# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Pure selection graph with explicit candidate and preference branches."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.write.routes.selection.model import SelectionRequest
from computer_use.interaction.write.routes.selection.passes.apply_platform_preferences import (
    stage as apply_platform_preferences,
)
from computer_use.interaction.write.routes.selection.passes.check_selection_target import (
    stage as check_selection_target,
)
from computer_use.interaction.write.routes.selection.passes.enumerate_intent_units import (
    stage as enumerate_intent_units,
)
from computer_use.interaction.write.routes.selection.passes.filter_route_history_policy import (
    stage as filter_route_history_policy,
)
from computer_use.interaction.write.routes.selection.passes.has_selected_route import stage as has_selected_route
from computer_use.interaction.write.routes.selection.passes.order_supported_candidates import (
    stage as order_supported_candidates,
)
from computer_use.interaction.write.routes.selection.passes.refuse_unavailable_route import (
    stage as refuse_unavailable_route,
)
from computer_use.interaction.write.routes.selection.passes.resolve_action_semantics import (
    stage as resolve_action_semantics,
)
from computer_use.interaction.write.routes.selection.passes.return_selected_route import stage as return_selected_route
from computer_use.interaction.write.routes.selection.passes.select_click_contract import stage as select_click_contract
from computer_use.interaction.write.routes.selection.passes.select_click_offer import stage as select_click_offer
from computer_use.interaction.write.routes.selection.passes.select_unit_route import stage as select_unit_route
from computer_use.pipeline import Pipeline, Select, Sequence

if TYPE_CHECKING:
    from computer_use.interaction.write.routes.selection.model import CandidateSet, RouteHistory
    from computer_use.native.contracts import Facts, HostPolicy, Intent, UnitCommand
    from computer_use.pipeline.core import Step


class CandidateGenerationPipeline(Pipeline["SelectionRequest", "CandidateSet"]):
    """Build ordered native candidates for an operation.

    Collect intent-compatible units, resolve action names and click semantics, and
    apply platform preferences. Return supported candidates in selection order.
    """

    def __init__(self) -> None:
        super().__init__(root=self.build())

    def build(self) -> Step[SelectionRequest, CandidateSet]:
        return Sequence(
            name="CandidateGenerationPipeline",
            steps=[
                enumerate_intent_units(),
                resolve_action_semantics(),
                apply_platform_preferences(),
                order_supported_candidates(),
            ],
        )


class RouteChoicePipeline(Pipeline["SelectionRequest", "UnitCommand"]):
    """Choose an input command or the preparation it requires.

    Filter generated candidates using target evidence and route history, then
    prefer addressed ready input and preparation before taking foreground or
    occupying the global pointer. Return the selected command or explain why
    these routes are unavailable.
    """

    def __init__(self) -> None:
        super().__init__(root=self.build())

    def build(self) -> Step[SelectionRequest, UnitCommand]:
        chosen = return_selected_route()
        refused = refuse_unavailable_route()

        unit = Sequence(
            name="UnitRouteChoicePipeline",
            steps=[
                filter_route_history_policy(),
                select_unit_route(),
                Select(
                    name="SelectedUnitOrRefusal",
                    selector=has_selected_route(),
                    branches={"selected": chosen, "fallback": refused},
                ),
            ],
        )
        return Sequence(
            name="RouteChoicePipeline",
            steps=[
                check_selection_target(),
                CandidateGenerationPipeline(),
                Select(
                    name="ClickOrUnitRoute",
                    selector=select_click_contract(),
                    branches={"click": select_click_offer(), "unit": unit},
                ),
            ],
        )


def choose_route(
    facts: Facts,
    intent: Intent,
    policy: HostPolicy | None,
    attempts: RouteHistory | None,
    *,
    needs_editing_evidence: bool = False,
) -> UnitCommand:
    return RouteChoicePipeline().run(SelectionRequest(facts, intent, policy, attempts, needs_editing_evidence))
