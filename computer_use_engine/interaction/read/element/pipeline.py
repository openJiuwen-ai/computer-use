"""Explicit element subtree reads, numbering and exact-fact publication."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, cast

from computer_use.interaction.query.pipeline import FindElementPipeline
from computer_use.interaction.read.acquisition.facts.pipeline import FactCollectionPipeline
from computer_use.interaction.read.element.model import ElementReadPlan
from computer_use.interaction.read.element.passes.format_element_observation import (
    stage as format_element_observation,
)
from computer_use.interaction.read.element.passes.plan_element_continuation import (
    stage as plan_element_continuation,
)
from computer_use.interaction.read.element.passes.remember_element_observation import (
    stage as remember_element_observation,
)
from computer_use.interaction.read.element.passes.return_element_observation import (
    stage as return_element_observation,
)
from computer_use.interaction.read.numbering.pipeline import NumberSnapshotsPipeline
from computer_use.interaction.read.presentation.document import DisplayDocument
from computer_use.interaction.read.presentation.model import SnapshotRenderRequest
from computer_use.interaction.read.presentation.pipeline import PrepareSnapshotPipeline
from computer_use.interaction.read.publication.pipeline import ObservationPublicationPipeline
from computer_use.interaction.requests import FindElement, ReadElement
from computer_use.pipeline import Contract, Effect, Pipeline, Sequence
from computer_use.pipeline.core import Node

if TYPE_CHECKING:
    from computer_use.interaction.controller import SessionController
    from computer_use.interaction.records import ElementSnapshot
    from computer_use.pipeline.core import Control, Step
    from computer_use.types import Observation


class ResolveElementRead(Node["ReadElement", "ElementReadPlan"]):
    """Resolve the native identity requested by an element read.

    Use the retained element snapshot, or run the session lookup for a new query's first page.
    Pair the selected binding and captured metadata with the request for continuation planning.

    Attributes:
        query: Session element-lookup flow used to resolve a queried first page.
    """

    def __init__(self, controller: SessionController) -> None:
        self.query = FindElementPipeline(controller)
        self.children = (self.query,)
        super().__init__("ResolveElementRead", Contract(ReadElement, ElementReadPlan, self.query.contract.effects))

    async def _arun(self, request: ReadElement, control: Control) -> ElementReadPlan:
        selected = request.previous or request.snapshot
        if not request.offset and request.cursor is None and (request.query is not None):
            selected = await self.query.arun(
                FindElement(request.owner, request.query["path"], request.query["timeoutMs"], False), control
            )
        return ElementReadPlan(request, selected)

    def _run(self, value: ReadElement, control: Control) -> ElementReadPlan:
        raise TypeError("Element reads require arun()")


class CollectElementFacts(Node["ElementReadPlan", "ElementReadPlan"]):
    """Collect native facts for the resolved element read.

    Open the selected binding under the request budget and collect the requested fields and
    relation page. Attach the resulting semantic snapshot to the element-read plan.

    Attributes:
        controller: Session services providing the native read lease and fact collector.
    """

    def __init__(self, controller: SessionController) -> None:
        self.controller = controller
        self.children = (FactCollectionPipeline(controller.reader.reader),)
        super().__init__(
            "CollectElementFacts",
            Contract(ElementReadPlan, ElementReadPlan, self.children[0].contract.effects),
        )

    async def _arun(self, plan: ElementReadPlan, control: Control) -> ElementReadPlan:
        tree = await self.controller.reader.reader.read(
            plan.selected["binding"],
            **cast("Record", plan.options),
            scope_kind=plan.selected.get("scope_kind"),
            _control=control,
        )
        return replace(plan, tree=tree)

    def _run(self, value: ElementReadPlan, control: Control) -> ElementReadPlan:
        raise TypeError("Element reads require arun()")


class NumberElementFacts(Node["ElementReadPlan", "ElementReadPlan"]):
    """Apply display numbering to an element's collected snapshot.

    Use the reader's shared reference allocator to number the native facts. Replace the
    plan's tree with its numbered snapshot before rendering and retention.

    Attributes:
        controller: Session services exposing the application reader's reference allocator.
    """

    def __init__(self, controller: SessionController) -> None:
        self.controller, self.children = (controller, (NumberSnapshotsPipeline(),))
        super().__init__("NumberElementFacts", Contract(ElementReadPlan, ElementReadPlan, {Effect.RESOURCE}))

    def _run(self, plan: ElementReadPlan, control: Control) -> ElementReadPlan:
        (tree,) = self.controller.reader.number_snapshots((cast("SemanticSnapshot", plan.tree),), _control=control)
        return replace(plan, tree=tree)

    async def _arun(self, value: ElementReadPlan, control: Control) -> ElementReadPlan:
        return self._run(value, control)


class PrepareElementDocument(Node["ElementReadPlan", "ElementReadPlan"]):
    """Prepare the numbered subtree and attach it to the publication document.

    Freeze its compact projection and source facts without formatting text.
    The publication flow retains the structure until explicit output.

    Attributes:
        preparation: Pure projection flow for the numbered element subtree.
    """

    def __init__(self) -> None:
        self.preparation = PrepareSnapshotPipeline()
        self.children = (self.preparation,)
        super().__init__("PrepareElementDocument", Contract(ElementReadPlan, ElementReadPlan))

    def _run(self, plan: ElementReadPlan, control: Control) -> ElementReadPlan:
        prepared = self.preparation.run(
            SnapshotRenderRequest(cast("SemanticSnapshot", plan.tree), compact=True), control
        )
        publication = cast("PublicationRequest", plan.publication)
        document = (
            publication.document
            if isinstance(publication.document, DisplayDocument)
            else DisplayDocument.join((publication.document,))
        )
        return replace(
            plan,
            rendered=prepared,
            publication=replace(
                publication, document=DisplayDocument.join((document, DisplayDocument.tree(prepared)), separator="")
            ),
        )

    async def _arun(self, value: ElementReadPlan, control: Control) -> ElementReadPlan:
        return self._run(value, control)


class PublishElementObservation(Node["ElementReadPlan", "ElementReadPlan"]):
    """Publish the prepared element-subtree observation.

    Run the common observation publication flow and attach the public observation to the
    element-read plan. The remaining stage returns that result with the refreshed element
    binding.

    Attributes:
        publication: Common publication flow that retains sources and emits the observation
            event.
    """

    def __init__(self, controller: SessionController) -> None:
        self.publication = ObservationPublicationPipeline(controller)
        self.children = (self.publication,)
        super().__init__(
            "PublishElementObservation",
            Contract(ElementReadPlan, ElementReadPlan, self.publication.contract.effects),
        )

    def _run(self, plan: ElementReadPlan, control: Control) -> ElementReadPlan:
        return replace(plan, result=self.publication.run(cast("PublicationRequest", plan.publication), control))

    async def _arun(self, value: ElementReadPlan, control: Control) -> ElementReadPlan:
        return self._run(value, control)


class ControllerElementReadPipeline(Pipeline["ReadElement", "tuple[Observation, ElementSnapshot]"]):
    """Read and publish an element subtree or relationship continuation.

    Resolve the element and continuation options, collect and number its facts, then retain
    and render the snapshot. Return the published observation with the element's updated
    snapshot metadata.

    Attributes:
        controller: Session owner for element lookup, native collection, reference
            retention, and publication.
    """

    def __init__(self, controller: SessionController) -> None:
        self.controller = controller
        super().__init__(root=self.build())

    def build(self) -> Step[ReadElement, tuple[Observation, ElementSnapshot]]:
        controller = self.controller
        return Sequence(
            name="ControllerElementReadPipeline",
            steps=[
                ResolveElementRead(controller),
                plan_element_continuation(
                    capabilities_of=lambda: controller.transport.capabilities,
                    resolve_page=lambda cursor, target: controller.pages.resolve(cursor, target),
                    validate_element=controller.validate_element,
                ),
                CollectElementFacts(controller),
                NumberElementFacts(controller),
                # The pipeline shares retained bindings with their owning service.
                # pylint: disable-next=protected-access
                remember_element_observation(remember_snapshot=controller._remember, state_of=controller.state),
                format_element_observation(),
                PrepareElementDocument(),
                PublishElementObservation(controller),
                return_element_observation(element_snapshot=controller.element_snapshot),
            ],
        )


if TYPE_CHECKING:
    from computer_use.interaction.read.facts.snapshot import SemanticSnapshot
    from computer_use.interaction.read.publication.model import PublicationRequest
if TYPE_CHECKING:
    from computer_use.protocol.records import Record
