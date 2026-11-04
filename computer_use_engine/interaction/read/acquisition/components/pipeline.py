# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Read leases are explicit scopes; isolated facts finish before pixel capture."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, cast

from computer_use.errors import ComputerUseError
from computer_use.interaction.read.acquisition.components.helpers import image_notes, native_lease
from computer_use.interaction.read.acquisition.components.model import (
    ComponentLease,
    ComponentParts,
    ComponentRequest,
)
from computer_use.interaction.read.acquisition.components.passes.assemble_components import (
    stage as assemble_components,
)
from computer_use.interaction.read.acquisition.components.passes.image_requested import (
    stage as image_requested,
)
from computer_use.interaction.read.acquisition.components.passes.retain_tree_components import (
    stage as retain_tree_components,
)
from computer_use.interaction.read.acquisition.components.passes.select_read_isolation import (
    stage as select_read_isolation,
)
from computer_use.interaction.read.acquisition.components.support import (
    collect_component,
    observation_gap,
)
from computer_use.interaction.read.acquisition.facts.pipeline import (
    FactCollectionPipeline as NativeFactCollectionPipeline,
)
from computer_use.interaction.read.acquisition.images.passes.isolated_components_pipeline_collect_pixels import (
    stage as collect_pixels,
)
from computer_use.interaction.read.acquisition.images.pipeline import ImageCollectionPipeline
from computer_use.pipeline import Contract, Pipeline, Scope, Select, Sequence
from computer_use.pipeline.core import Node

if TYPE_CHECKING:
    from computer_use.interaction.read.app import NativeAppReader
    from computer_use.interaction.read.facts.snapshot import SemanticSnapshot
    from computer_use.pipeline.core import Control, Step


class NativeFactCollectionAdapter(Node["ComponentLease", "SemanticSnapshot"]):
    """Collect window facts through the native reader's service boundary.

    Apply the component's field, depth, and node limits to the active lease and its bounded
    fact budget. Return the semantic snapshot supplied by the reader.

    Attributes:
        reader: Native fact reader used to collect and normalize the selected window scope.
    """

    def __init__(self, services: NativeAppReader) -> None:
        self.reader = services.reader
        self.children = (NativeFactCollectionPipeline(self.reader),)
        super().__init__("CollectWindowFacts", Contract(ComponentLease, object, self.children[0].contract.effects))

    async def _arun(self, value: ComponentLease, control: Control) -> SemanticSnapshot:
        request, read = value.request, value.read
        limits = request.limits
        query = read.bounded(max(1, cast("Callable[[], int]", control.remaining)() // 3))
        return await self.reader.collect(
            read,
            node=request.target.node,
            fields=limits["fields"],
            max_nodes=limits["max_nodes"],
            max_depth=limits["max_depth"],
            scope_kind="window",
            query_remaining=query.remaining,
            _control=control,
        )

    def _run(self, value: ComponentLease, control: Control) -> SemanticSnapshot:
        raise TypeError("Native facts require arun()")


class WindowFactCollectionPipeline(Node["ComponentLease", "ComponentLease"]):
    """Attach requested window facts to the component lease.

    When tree collection is enabled, run the native collector and convert supported
    collection failures to gaps. Return the lease with its tree result and accumulated
    component evidence.

    Attributes:
        services: Application reader whose native lifecycle governs component-failure
            handling.
        collector: Window-fact collector invoked for the current component lease.
    """

    def __init__(self, services: NativeAppReader) -> None:
        self.services = services
        self.collector = NativeFactCollectionAdapter(services)
        self.children = (self.collector,)
        super().__init__(
            "FactComponentRecovery",
            Contract(ComponentLease, ComponentLease, self.collector.contract.effects),
        )

    async def _arun(self, value: ComponentLease, control: Control) -> ComponentLease:
        if not value.request.include_tree:
            return value
        tree, gap = await collect_component(self.services.port, "tree", lambda: self.collector.arun(value, control))
        return replace(value, parts=replace(value.parts, tree=tree, gaps=value.parts.gaps + ((gap,) if gap else ())))

    def _run(self, value: ComponentLease, control: Control) -> ComponentLease:
        raise TypeError("Native facts require arun()")


class _SharedScopeRecovery(Node["ComponentRequest", "ComponentParts"]):
    """Collect window components under one native lease.

    Collect the requested tree and pixels sequentially and assemble their component result.
    Convert supported scope-read failures into a view gap while propagating other failures.

    Attributes:
        services: Application reader providing native scope and failure classification.
        scoped: Native lease containing tree collection, image collection, and component
            assembly.
    """

    def __init__(self, services: NativeAppReader) -> None:
        self.services = services
        self.scoped = Scope(
            name="SharedReadComponentScope",
            using=native_lease(services),
            body=Sequence(
                name="SharedComponentCollection",
                steps=[
                    WindowFactCollectionPipeline(services),
                    ImageCollectionPipeline(services),
                    assemble_components(),
                ],
            ),
        )
        super().__init__("SharedReadComponents", self.scoped.contract)

    async def _arun(self, request: ComponentRequest, control: Control) -> ComponentParts:
        try:
            return await self.scoped.arun(request, control)
        except ComputerUseError as error:
            if (
                self.services.port.calls.retired
                or error.effect != "none"
                or error.code not in ("stale_target", "observation_incomplete")
            ):
                raise
            return ComponentParts(gaps=(observation_gap("view", error),))

    def _run(self, value: ComponentRequest, control: Control) -> ComponentParts:
        raise TypeError("Native component collection requires arun()")


class _IsolatedTreeCollection(Node["ComponentRequest", "ComponentLease"]):
    """Collect the requested tree in an independent native lease.

    Run the tree scope when requested and preserve a supported collection failure as a gap.
    Return the resulting component data in a lease record for the optional image branch.

    Attributes:
        services: Application reader providing component-failure conversion.
        scope: Independent native lease and fact-collection flow.
    """

    def __init__(self, services: NativeAppReader, scope: Scope) -> None:
        self.services, self.scope, self.children = services, scope, (scope,)
        super().__init__("RecoverIsolatedFacts", Contract(ComponentRequest, ComponentLease, scope.contract.effects))

    async def _arun(self, request: ComponentRequest, control: Control) -> ComponentLease:
        if not request.include_tree:
            return ComponentLease(request, None, ComponentParts())
        tree, gap = await collect_component(self.services.port, "tree", lambda: self.scope.arun(request, control))
        return ComponentLease(request, None, ComponentParts(tree=tree, gaps=(gap,) if gap else ()))

    def _run(self, value: ComponentRequest, control: Control) -> ComponentLease:
        raise TypeError("Native facts require arun()")


class _IsolatedImageCollection(Node["ComponentLease", "ComponentParts"]):
    """Combine an independent image capture with collected tree evidence.

    Run the image scope and attach its capture, supported failure, and capture-state notes
    to the existing components. Return the combined component result.

    Attributes:
        services: Application reader providing component-failure conversion.
        scope: Independent native lease and image-capture flow.
    """

    def __init__(self, services: NativeAppReader, scope: Scope) -> None:
        self.services, self.scope, self.children = services, scope, (scope,)
        super().__init__("RecoverIsolatedPixels", Contract(ComponentLease, ComponentParts, scope.contract.effects))

    async def _arun(self, value: ComponentLease, control: Control) -> ComponentParts:
        image, gap = await collect_component(
            self.services.port, "image", lambda: self.scope.arun(value.request, control)
        )
        return replace(value.parts, image=image, gaps=value.parts.gaps + ((gap,) if gap else ()) + image_notes(image))

    def _run(self, value: ComponentLease, control: Control) -> ComponentParts:
        raise TypeError("Native pixels require arun()")


class IsolatedComponentsPipeline(Pipeline["ComponentRequest", "ComponentParts"]):
    """Collect tree and image components in independent native scopes.

    Complete the requested tree scope before selecting the optional image scope. Combine
    their outputs and collection gaps into one component result.

    Attributes:
        services: Application reader providing native fact and pixel collection.
    """

    def __init__(self, services: NativeAppReader) -> None:
        self.services = services
        super().__init__(root=self.build())

    def build(self) -> Step[ComponentRequest, ComponentParts]:
        services = self.services

        tree_scope = Scope(
            name="IsolatedTreeScope",
            using=native_lease(services),
            body=NativeFactCollectionAdapter(services),
        )
        image_scope = Scope(
            name="IsolatedImageScope",
            using=native_lease(services),
            body=collect_pixels(services=services),
        )
        return Sequence(
            name="IsolatedComponentsPipeline",
            steps=[
                _IsolatedTreeCollection(services, tree_scope),
                Select(
                    name="OptionalIsolatedImage",
                    selector=image_requested(),
                    branches={
                        False: retain_tree_components(),
                        True: _IsolatedImageCollection(services, image_scope),
                    },
                ),
            ],
        )


class ComponentPipeline(Pipeline["ComponentRequest", "ComponentParts"]):
    """Select how the driver leases tree and image collection.

    Use the native read-isolation capability to choose shared-scope or separate-scope
    component collection. Return the collected facts, pixels, and supported component gaps.

    Attributes:
        services: Application reader supplying driver capabilities and native component
            services.
    """

    def __init__(self, services: NativeAppReader) -> None:
        self.services = services
        super().__init__(root=self.build())

    def build(self) -> Step[ComponentRequest, ComponentParts]:
        services = self.services
        return Select(
            name="ComponentPipeline",
            selector=select_read_isolation(services=services),
            branches={
                False: _SharedScopeRecovery(services),
                True: IsolatedComponentsPipeline(services),
            },
        )


if TYPE_CHECKING:
    from typing import Callable
