from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from computer_use.interaction.read.presentation.ir import PresentationIR, ProjectionEditor, ProjectionView

if TYPE_CHECKING:
    from computer_use.interaction.read.presentation.ir import ViewNode


@dataclass(frozen=True)
class Scope:
    """Local compression traversal context; independent regions retain their boundaries.

    Attributes:
        parent: Current display parent in the compression traversal.
        in_table: Whether the current source path is inside a table.
    """

    parent: ViewNode | None = None
    in_table: bool = False


@dataclass(frozen=True)
class RuleInvocation:
    """One compression rule's read-only view, local editor, and arguments.

    Attributes:
        view: Read-only local presentation view; edits go through its paired editor.
        edit: Local presentation editor; cannot alter native identity or capabilities.
        arguments: Local rule arguments; their concrete shape is defined by that rule.
    """

    view: ProjectionView
    edit: ProjectionEditor
    arguments: tuple[object, ...] = ()


@dataclass(frozen=True)
class CompressionRun:
    """Presentation IR and its narrow read/edit interfaces for one traversal.

    Attributes:
        ir: Locally owned presentation IR, with native source facts kept immutable.
        view: Read-only local presentation view; edits go through its paired editor.
        edit: Local presentation editor; cannot alter native identity or capabilities.
    """

    ir: PresentationIR
    view: ProjectionView
    edit: ProjectionEditor
