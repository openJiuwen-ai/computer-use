"""Plan element continuation."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from computer_use.diagnostics import localFailure
from computer_use.interaction.read.element.model import ElementReadPlan
from computer_use.pipeline import Contract, Effect, Pass

if TYPE_CHECKING:
    from typing import Callable

    from computer_use.native.contracts import Binding
    from computer_use.pipeline.core import Control
    from computer_use.protocol.records import Record


def create(
    *,
    capabilities_of: Callable[[], frozenset[str]],
    resolve_page: Callable[[str, Binding], Record],
    validate_element: Callable[[Binding], None],
) -> Callable[[ElementReadPlan, Control], ElementReadPlan]:

    def options(plan: ElementReadPlan, control: Control) -> ElementReadPlan:
        """Resolve the requested tree page against the selected element binding.

        Use a retained relation cursor when supplied; otherwise validate the child offset
        against its captured continuation. Attach the resulting native page options to the
        element plan.
        """
        request, selected = (plan.request, plan.selected)
        if selected is None:
            raise localFailure(
                "element continuation needs an observed first page",
                "read this element before using a continuation offset",
                "snapshot_expired",
            )
        target = selected["binding"]
        validate_element(target)
        if request.cursor is not None:
            if "nativeRelationPages" not in capabilities_of():
                raise localFailure(
                    "this driver does not support content continuations",
                    "use supported element reads or update the driver",
                    "mode_unsupported",
                )
            options = dict(relation_page=resolve_page(request.cursor, target))
        else:
            continuation = selected.get("children")
            if request.offset and (continuation is None or continuation.offset != request.offset):
                raise localFailure(
                    "offset does not match this element's observed continuation",
                    "read the element again and use its reported continuation",
                    "snapshot_expired",
                )
            options = dict(children_page=dict(cursor=continuation.cursor if request.offset else None, limit=128))
        return replace(plan, options=options)

    return options


stage = Pass.factory(
    name="plan_element_continuation",
    factory=create,
    contract=Contract(ElementReadPlan, ElementReadPlan, {Effect.RESOURCE}),
)
