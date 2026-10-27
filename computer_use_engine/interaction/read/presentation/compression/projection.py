"""Full display projection; rules and ordering live in projection_rules.

These compatibility entry points return the same native-source/provenance
contract used by rendering and observation coverage. They never route input.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.read.presentation.compression.pipeline import project as projectTree

if TYPE_CHECKING:
    from computer_use.interaction.read.presentation.ir import ProjectionResult
    from computer_use.interaction.read.presentation.records import SemanticNode
    from computer_use.protocol.records import Record


def prepare_tree(root: SemanticNode, context: Record | None = None) -> ProjectionResult:
    return projectTree(root, context)["roots"]
