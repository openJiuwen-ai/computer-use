# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Explicit v6 subtree reads, with fixed targets and opaque relation cursors."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from computer_use.diagnostics import local_failure
from computer_use.interaction.read.presentation.content import template
from computer_use.interaction.read.presentation.document import DisplayDocument
from computer_use.interaction.read.presentation.model import SnapshotRenderRequest
from computer_use.interaction.read.presentation.pipeline import PrepareSnapshotPipeline
from computer_use.interaction.read.views.publication import publish, tree_document
from computer_use.session.events import explicit_observation_completed
from computer_use.session.interfaces import LookupContext, SurfaceOwner
from computer_use.session.state import AppState

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
            ui: App or request scope whose instance and observation bounds constrain the
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
                raise local_failure("this cursor is not a tree continuation")
            page = controller.pages.resolve(cursor, target)
            if "nativeRelationPages" in controller.transport.capabilities:
                options: Record = dict(relation_page=page)
            else:
                if page["property"] not in ("AXChildren", "Children", "children"):
                    raise local_failure(
                        "this provider cannot page that native relation",
                        "unsupported_operation",
                    )
                options: Record = dict(children_page={key: page[key] for key in ("cursor", "limit")})
        else:
            options: Record = (
                dict(children_page=dict(cursor=None, limit=min(128, max_nodes - 1)))
                if max_nodes > 1 and "nativeNodePages" in controller.transport.capabilities
                else {}
            )
        if getattr(ui, "_strict_window", False):
            options["strict_window"] = True
        tree = await controller.reader.reader.read(
            target, scope_kind=snapshot.get("scope_kind"), max_nodes=max_nodes, **options
        )
        (tree,) = controller.reader.number_snapshots((tree,))
        selected = next((ref for ref, node in tree.refs.items() if node == target.node), None)
        if selected is None or target.node in tree.facts["unread"]:
            raise local_failure("the retained element was unconfirmed in this observation", "observation_incomplete")
        state = controller.state(ui)
        # The pipeline shares retained bindings with their owning service.
        # pylint: disable-next=protected-access
        controller.remember_observation(state, target, tree)
        for field in ("window_identity", "menu_window_identity"):
            if field in snapshot:
                for ref in tree.refs:
                    state.refs[ref][field] = snapshot[field]
        return tree, state.refs[selected]

    async def run(self, ui: UIHandle, snapshot: ElementSnapshot, cursor: str | None) -> Observation:
        tree, fresh = await self.collect(ui, snapshot, cursor)
        prepared = self.preparation.run(
            SnapshotRenderRequest(tree, compact=True, public_api=True, share_descriptions=False)
        )
        document = DisplayDocument.join(
            (
                template("Element {0}:", fresh["source"]["ref"]),
                "Tree: full",
                tree_document(self.controller, snapshot["binding"], tree, prepared=prepared),
            )
        )
        # An explicit subtree belongs to its snapshot, even when the App
        # wrapper last staged an unrelated window/menu observation.
        menus = ()
        content_owner = None
        menu_only = fresh.get("scope_kind") == "applicationMenu"
        identity = fresh.get("menu_window_identity") if menu_only else fresh.get("window_identity")
        content = fresh.get("scope_kind") == "applicationContent"
        if menu_only or content:
            state = self.controller.state(ui)
            source = AppState(
                state.selector,
                running=state.running,
                # UIHandle exposes the separate instance catalogue to internal pipelines.
                # pylint: disable-next=protected-access
                refs=ui._instance.refs,
                query_target=fresh["binding"],
                query_scope_kind=fresh["scope_kind"],
                menu_window_identity=identity,
            )
            source_owner = SurfaceOwner(self.controller, source)
            if menu_only:
                menus = (source_owner,)
            else:
                content_owner = source_owner
        lookup_context = LookupContext(identity, menu_only, menus, content_owner)
        return publish(
            self.controller,
            ui,
            document,
            trees=((snapshot["binding"], tree),),
            full=True,
            scope="element:" + repr(snapshot["binding"]),
            view="element",
            lookup_context=lookup_context,
            on_observation=explicit_observation_completed if cursor is None else None,
        )


if TYPE_CHECKING:
    from computer_use.protocol.records import Record
