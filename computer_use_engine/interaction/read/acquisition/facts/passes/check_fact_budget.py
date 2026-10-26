"""Check fact budget."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from computer_use.interaction.read.acquisition.facts.model import QUERY
from computer_use.pipeline import Contract, Pass

if TYPE_CHECKING:
    from computer_use.interaction.read.facts.snapshot import SemanticSnapshot
    from computer_use.pipeline.core import Control


def check_budget(result: SemanticSnapshot, control: Control) -> SemanticSnapshot:
    """Check the enclosing deadline before returning the normalized native snapshot."""
    # The same post-normalization check stays after alias registration.
    cast("Callable[[], int]", control.remaining)()
    return result


stage = Pass.define(
    name="check_fact_budget",
    function=check_budget,
    contract=Contract(object, object, QUERY, requires=("original remaining budget",)),
)
if TYPE_CHECKING:
    from typing import Callable
