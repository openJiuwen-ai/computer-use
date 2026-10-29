# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Compose application discovery and scoped element lookup."""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import replace
from typing import TYPE_CHECKING, cast

from computer_use.interaction.query.model import FindLease
from computer_use.interaction.query.passes.build_lookup_snapshot import (
    stage as build_lookup_snapshot,
)
from computer_use.interaction.query.passes.find_element import stage as find_element
from computer_use.interaction.query.passes.prepare_element_lookup import (
    stage as prepare_element_lookup,
)
from computer_use.interaction.query.scope import app_context
from computer_use.interaction.read.acquisition.facts.pipeline import FactCollectionPipeline
from computer_use.interaction.read.acquisition.scope.pipeline import WorkingScopeDiscoveryPipeline
from computer_use.native.scopes import window_binding
from computer_use.pipeline import Contract, Pipeline, Scope, Sequence
from computer_use.pipeline.core import Node

if TYPE_CHECKING:
    from typing import AsyncIterator, Callable

    from computer_use.interaction.controller import SessionController
    from computer_use.interaction.query.model import FindPlan
    from computer_use.interaction.records import ElementSnapshot
    from computer_use.interaction.requests import FindElement, InteractionRequest
    from computer_use.pipeline.core import Control, Step


class AppScopeReconciliation(Node["FindPlan", "FindPlan"]):
    """Reconcile a query or input target with the App's current window context.

    When the supplied predicate requests reconciliation, recheck the previously
    observed modal-parent chain and update the plan's target.
    Otherwise the prepared plan continues unchanged.

    Attributes:
        controller: Session services providing the working-window reader.
        required: Predicate selecting plans that need their App context reconciled.
    """

    def __init__(self, controller: SessionController, required: Callable[[FindPlan], bool]) -> None:
        self.controller, self.required = (controller, required)
        self.children = (WorkingScopeDiscoveryPipeline(controller.reader),)
        super().__init__("ReconcileAppScope", Contract(object, object, self.children[0].contract.effects))

    async def _arun(self, plan: FindPlan, control: Control) -> FindPlan:
        if not self.required(plan):
            return plan
        state = plan.state
        target = await app_context(
            self.controller.reader,
            cast("WorkingWindow", state.selection),
            cast("AppObservation", state.observation),
            plan.target,
            cast("Remaining", plan.remaining),
            _control=control,
        )
        return replace(plan, target=target)

    def _run(self, value: FindPlan, control: Control) -> FindPlan:
        raise TypeError("Scope reconciliation requires arun()")


class FindFacts(Node["FindLease", "FindLease"]):
    """Collect facts beneath the element selected by path lookup.

    Collection uses the lookup's open native lease and query-scope kind.
    The resulting semantic snapshot is attached to the lease record for
    element-snapshot construction.

    Attributes:
        controller: Session fact reader used to collect the selected subtree.
    """

    def __init__(self, controller: SessionController) -> None:
        self.controller = controller
        self.children = (FactCollectionPipeline(controller.reader.reader),)
        super().__init__("ReadLookupFacts", Contract(FindLease, FindLease, self.children[0].contract.effects))

    async def _arun(self, value: FindLease, control: Control) -> FindLease:
        tree = await self.controller.reader.reader.collect(
            value.read,
            node=cast("Binding", value.selected).node,
            scope_kind=value.plan.state.query_scope_kind,
            _control=control,
        )
        return replace(value, tree=tree)

    def _run(self, value: FindLease, control: Control) -> FindLease:
        raise TypeError("Lookup facts require arun()")


class FindElementPipeline(Pipeline["FindElement", "ElementSnapshot"]):
    """Resolve an element path and construct its retained observation data.

    Lookup runs within the owning handle's observed scope and a shared call
    budget. The result pairs the matched native binding with captured text
    and child-relation sources for subsequent element operations.

    Attributes:
        controller: Session query roots, handle state, native leases, and fact reader.
    """

    def __init__(self, controller: SessionController) -> None:
        self.controller = controller
        super().__init__(root=self.build())

    def build(self) -> Step[FindElement, ElementSnapshot]:
        controller = self.controller

        @asynccontextmanager
        async def call(plan: FindPlan, control: Control) -> AsyncIterator[FindPlan]:
            with controller.port.calls.claim(30000) as remaining:
                yield replace(plan, remaining=remaining)

        @asynccontextmanager
        async def lease(plan: FindPlan, control: Control) -> AsyncIterator[FindLease]:
            async with controller.port.calls.operation(
                window_binding(plan.target), cast("Remaining", plan.remaining)
            ) as read:
                yield FindLease(plan, read)

        return Sequence(
            name="FindElementPipeline",
            steps=[
                # The pipeline shares retained bindings with their owning service.
                # pylint: disable-next=protected-access
                prepare_element_lookup(query_root=controller._query_root, state_of=controller.state),
                Scope(
                    name="LookupCallScope",
                    using=call,
                    body=Sequence(
                        name="LookupInCall",
                        steps=[
                            AppScopeReconciliation(
                                controller,
                                lambda plan: (
                                    plan.state.selection is not None
                                    and plan.state.observation is not None
                                    and (plan.target == plan.state.observation.target)
                                ),
                            ),
                            Scope(
                                name="LookupNativeScope",
                                using=lease,
                                body=Sequence(
                                    name="LookupFactCollection",
                                    steps=[find_element(port=controller.port), FindFacts(controller)],
                                ),
                            ),
                        ],
                    ),
                ),
                # pylint: disable-next=protected-access
                build_lookup_snapshot(snapshots=controller._snapshots),
            ],
        )


class QueryPipeline(Pipeline["InteractionRequest", "QueryResult"]):
    """Enter the session's element lookup flow.

    Element queries resolve a path and return the matched snapshot.

    Attributes:
        controller: Session services passed to the lookup pipeline.
    """

    def __init__(self, controller: SessionController) -> None:
        self.controller = controller
        super().__init__(root=self.build())

    def build(self) -> Step[InteractionRequest, QueryResult]:
        controller = self.controller
        return FindElementPipeline(controller)


if TYPE_CHECKING:
    from computer_use.interaction.records import Remaining
    from computer_use.native.contracts import Binding
    from computer_use.native.scopes import WorkingWindow
if TYPE_CHECKING:
    from computer_use.interaction.records import QueryResult
if TYPE_CHECKING:
    from computer_use.interaction.read.model import AppObservation
if TYPE_CHECKING:
    pass
