"""Explicit v6 subtree reads, with fixed targets and opaque relation cursors."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from computer_use.diagnostics import localFailure
from computer_use.interaction.read.presentation.content import template
from computer_use.interaction.read.presentation.document import DisplayDocument
from computer_use.interaction.read.presentation.model import SnapshotRenderRequest
from computer_use.interaction.read.presentation.pipeline import PrepareSnapshotPipeline
from computer_use.interaction.read.views.publication import publish, tree_document

if TYPE_CHECKING:
    from computer_use.interaction.controller import SessionController
    from computer_use.interaction.read.facts.snapshot import SemanticSnapshot
    from computer_use.interaction.records import ElementSnapshot, UIHandle
    from computer_use.types import Observation


class AgentElementReadPipeline:
    """Collect and publish explicit subtrees for public element reads.

    Validate the retained binding and resolve its requested page, then read, number, and retain
    the native snapshot. Expose that fresh snapshot to internal callers or publish its complete
    subtree presentation for the public read.

    Attributes:
        controller: Session services for handle validation, native facts, numbering, and publication.
    """

    def __init__(self, controller: SessionController) -> None:
        self.controller = controller
        self.preparation = PrepareSnapshotPipeline()

    async def collect(
        self,
        ui: UIHandle,
        snapshot: ElementSnapshot,
        cursor: str | None = None,
        *,
        max_nodes: int = 1000,
    ) -> tuple[SemanticSnapshot, ElementSnapshot]:
        """Read the element request through the session controller and deliver its observation.

        Args:
            ui: App or Window handle whose instance and observation bounds constrain the
                operation.
            snapshot: Facts captured for this observation; display edits do not mutate native
                evidence.
            cursor: Opaque continuation token from the same source observation, or None
                initially.
            max_nodes: Maximum number of native objects this collection may read.
        """
        controller = self.controller
        controller.interfaces.validate_snapshot(ui, snapshot)
        target = snapshot["binding"]
        if cursor is not None:
            if not re.fullmatch(r"p[1-9][0-9]*", cursor):
                raise localFailure("this cursor is not a tree continuation", "use the cursor printed by Element.read()")
            page = controller.pages.resolve(cursor, target)
            if "nativeRelationPages" in controller.transport.capabilities:
                options: Record = dict(relation_page=page)
            else:
                if page["property"] not in ("AXChildren", "Children", "children"):
                    raise localFailure(
                        "this provider cannot page that native relation",
                        "read its explicit source subtree",
                        "mode_unsupported",
                    )
                options: Record = dict(children_page={key: page[key] for key in ("cursor", "limit")})
        else:
            options: Record = (
                dict(children_page=dict(cursor=None, limit=min(128, max_nodes - 1)))
                if max_nodes > 1 and "nativeNodePages" in controller.transport.capabilities
                else {}
            )
        tree = await controller.reader.reader.read(
            target, scope_kind=snapshot.get("scope_kind"), max_nodes=max_nodes, **options
        )
        (tree,) = controller.reader.number_snapshots((tree,))
        state = controller.state(ui)
        controller._remember(state, target, tree)
        if (identity := snapshot.get("window_identity")) is not None:
            for ref in tree.refs:
                state.refs[ref]["window_identity"] = identity
        ref = next(ref for ref, node in tree.refs.items() if node == target.node)
        return tree, state.refs[ref]

    async def run(self, ui: UIHandle, snapshot: ElementSnapshot, cursor: str | None) -> Observation:
        tree, fresh = await self.collect(ui, snapshot, cursor)
        prepared = self.preparation.run(SnapshotRenderRequest(tree, compact=True, public_api=True))
        document = DisplayDocument.join(
            (
                template("Element {0}:", fresh["source"]["ref"]),
                "Tree: full",
                tree_document(self.controller, snapshot["binding"], tree, prepared=prepared, details=True),
            )
        )
        return publish(
            self.controller,
            ui,
            document,
            trees=((snapshot["binding"], tree),),
            full=True,
            scope="element:" + repr(snapshot["binding"]),
            view="element",
        )


if TYPE_CHECKING:
    from computer_use.protocol.records import Record
