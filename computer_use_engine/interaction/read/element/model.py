from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from computer_use.interaction.requests import ReadElement

if TYPE_CHECKING:
    from computer_use.interaction.read.facts.snapshot import SemanticSnapshot
    from computer_use.interaction.read.presentation.preparation import PreparedSnapshot
    from computer_use.interaction.read.publication.model import PublicationRequest
    from computer_use.interaction.records import ElementSnapshot
    from computer_use.protocol.records import Record
    from computer_use.session.state import AppState
    from computer_use.types import Observation


@dataclass(frozen=True)
class ElementReadPlan:
    """Element-read phase data; optional outputs are filled only by their owner stage.

    Attributes:
        request: Parsed request carrying the original operation scope and options.
        selected: Resolved element binding, scope, and previously captured text metadata.
        options: Normalized collection options for the selected element.
        tree: Semantic snapshot with retained native facts and display-reference bindings.
        publication: Prepared inputs for publishing the element observation.
        result: Result produced by this read stage.
        rendered: Rendered text, references, and source-emission metadata.
        state: Session-owned observation state for the resolved element scope.
    """

    request: ReadElement
    selected: ElementSnapshot
    options: Record | None = None
    tree: SemanticSnapshot | None = None
    publication: PublicationRequest | None = None
    result: Observation | None = None
    rendered: PreparedSnapshot | None = None
    state: AppState | None = None
