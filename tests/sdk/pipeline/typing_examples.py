"""Static examples for IDE/type-checker contracts; no desktop is opened.

Run Pyright with tests/sdk/pipeline/typing_config.json. These examples check
concrete stage/IR boundaries rather than claiming that Sequence verifies all
heterogeneous intermediate edges.
"""

from typing import assert_type

from computer_use.interaction.read.acquisition.facts.model import (
    CollectedFacts,
    FactProfile,
    FactReadRequest,
)
from computer_use.interaction.read.acquisition.facts.passes.collect_native_facts import (
    stage as collect_native_facts,
)
from computer_use.interaction.read.acquisition.facts.pipeline import FactCollectionPipeline
from computer_use.interaction.read.acquisition.reader import NativeReader
from computer_use.interaction.read.facts.snapshot import SemanticSnapshot
from computer_use.interaction.read.presentation.ir import (
    ProjectionEditor,
    ProjectionInput,
    ProjectionView,
    ViewNode,
)
from computer_use.interaction.read.presentation.records import NodeView
from computer_use.interaction.write.model import (
    AttemptOutcome,
    NativeInvocation,
    PlannedUnit,
    ResolutionInput,
    ResolvedWrite,
)
from computer_use.interaction.write.resolution.pipeline import TargetResolutionPipeline
from computer_use.interaction.write.routes.native_unit.pipeline import NativeUnitPipeline
from computer_use.native.contracts import Binding
from computer_use.native.lifecycle import OperationLease
from computer_use.pipeline import Control, Pass, PassDefinition, Step
from computer_use.protocol.records import NativeFacts, NativeNode, Record


def read_types(
    reader: NativeReader,
    lease: OperationLease[Binding],
    profile: FactProfile,
    control: Control,
) -> None:
    """Check a read from its admitted lease through its typed stage factory."""
    request = FactReadRequest(lease)
    assert_type(request.read.target, Binding)
    assert_type(request.read.remaining(), int)
    assert_type(profile.scope.request, FactReadRequest)
    assert_type(profile.fields, list[str])
    assert_type(profile.options, Record)

    leaf = collect_native_facts(reader=reader)
    assert_type(leaf, Pass[FactProfile, CollectedFacts])
    assert_type(leaf.run(profile, control), CollectedFacts)
    pipeline = FactCollectionPipeline(reader)
    assert_type(pipeline.root, Step[FactReadRequest, SemanticSnapshot])
    assert_type(pipeline.run(request, control), SemanticSnapshot)


def native_facts(batch: NativeFacts) -> None:
    """Keep native object identity separate from display dictionaries."""
    assert_type(batch["nodes"][0], NativeNode)
    assert_type(batch["nodes"][0]["node"], str)
    assert_type(batch["nodes"][0]["actions"], Record)
    assert_type(batch["roots"], list[str])


def presentation_types(source: ProjectionInput, view: ProjectionView, editor: ProjectionEditor) -> None:
    """Check the immutable view/local editor boundary and generic rule output."""
    assert_type(view.view, ViewNode)
    assert_type(view.nodes["0"], ViewNode)
    assert_type(view.parents.get("0"), str | None)
    assert_type(source.native_nodes["0"], NodeView)

    def count(view: ProjectionView, editor: ProjectionEditor) -> int:
        return len(view.nodes)

    assert_type(editor.run(count, view), int)


def write_types(
    resolver: TargetResolutionPipeline,
    request: ResolutionInput,
    unit: NativeUnitPipeline,
    planned: PlannedUnit,
    invocation: NativeInvocation,
) -> None:
    """Check semantic resolution and the selected native-unit boundary."""
    assert_type(resolver.run(request), ResolvedWrite)
    assert_type(unit.run(planned), AttemptOutcome)
    assert_type(invocation.budget, int)
    assert_type(request.bound.remaining(), int)


def definition_types() -> None:
    """Retain factory keyword signatures and synchronous/asynchronous outputs."""

    def length(value: str, control: Control) -> int:
        return len(value)

    from computer_use.pipeline import Contract

    definition = Pass.define(name="Length", function=length, contract=Contract(str, int))
    assert_type(definition, PassDefinition[[], str, int])
    assert_type(definition(), Pass[str, int])
