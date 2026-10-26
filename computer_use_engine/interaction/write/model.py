"""Write-stage records for fixed targets, resolution evidence, route plans, and native receipts."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING

from computer_use.native.contracts import (
    ClickSelectionDecision,
    Facts,
    HostPolicy,
    Intent,
    OperationTarget,
    Primitive,
    SelectionDependency,
    StepResult,
    UnitCommand,
)

if TYPE_CHECKING:
    from typing import Any

    from computer_use.interaction.records import ElementPath as LookupPath
    from computer_use.interaction.write.routes.policy import RouteKey
    from computer_use.native.contracts import Binding


if TYPE_CHECKING:
    from computer_use.native.lifecycle import OperationLease


@dataclass(frozen=True)
class WriteRequest:
    """Parsed intent and the lookup or forwarding scope supplied by the caller.

    Attributes:
        intent: Requested operation and its validated parameters.
        target: Original native target or observed application installation.
        query_root: Observed root from which an optional element path is resolved.
        path: Ordered semantic lookup components under query_root.
        query_timeout_ms: Maximum lookup wait in milliseconds, within the call deadline.
        forward_scope: Container whose current keyboard receiver supplies the input target.
    """

    intent: Intent
    target: OperationTarget
    query_root: OperationTarget | None = None
    path: LookupPath | None = None
    query_timeout_ms: int = 0
    forward_scope: Binding | None = None


@dataclass(frozen=True)
class OpenedWrite:
    """Write request paired with its active native operation lease.

    Attributes:
        request: Parsed intent and lookup bounds.
        lease: Native operation token, original target, and remaining-budget owner.
    """

    request: WriteRequest
    lease: "OperationLease"


@dataclass(frozen=True)
class TargetBinding:
    """Resolved target and ancestor constraints within the opened write operation.

    Attributes:
        opened: Request and native lease that own this resolution.
        target: Exact target selected by path lookup or current-receiver resolution.
        within: Ancestor tokens that must still contain the target at dispatch.
    """

    opened: OpenedWrite
    target: OperationTarget
    within: tuple[str, ...]


@dataclass(frozen=True)
class OperationSpec:
    """Fixed operation meaning and target bounds shared by every attempt.

    Attributes:
        intent: Requested operation and parameters.
        target: Pinned native identity for this write.
        within: Confirmed ancestor tokens constraining native input.
        policy: Host desktop mode and foreground-assistance authorization.
        forward_scope: Original container boundary for current-receiver forwarding.
    """

    intent: Intent
    target: OperationTarget
    within: tuple[str, ...]
    policy: HostPolicy
    forward_scope: Binding | None = None


@dataclass(frozen=True)
class BoundWrite:
    """Pinned operation with the token and budget of its enclosing native lease.

    Attributes:
        spec: Fixed operation intent, identity, scope, and host policy.
        operation: Active native operation token.
        remaining: Callback returning milliseconds left on the original call.
    """

    spec: OperationSpec
    operation: str
    remaining: Callable[[], int]


@dataclass(frozen=True)
class ResolutionMemory:
    """Evidence retained between permitted resolution attempts of one write.

    Attributes:
        within: Confirmed ancestor tokens constraining the target.
        menu_context: Menu ancestry and whether a submenu exists, once established.
        pointer_member: Previously qualified native hit token inside a clicked control.
        scroll_member: Previously qualified wheel-hit token inside a scrolling region.
    """

    within: tuple[str, ...] = ()
    menu_context: tuple[tuple[str, ...], bool] | None = None
    pointer_member: str | None = None
    scroll_member: str | None = None


@dataclass(frozen=True)
class AttemptView:
    """Immutable route-attempt history supplied to resolution and route selection.

    Attributes:
        unavailable: Semantic routes refused for the rest of this write.
        background_unavailable: Routes whose background preparation was unavailable.
        prepared: Route and foreground-mode pairs already prepared.
        precondition_changes: Number of pre-dispatch precondition changes consumed so far.
    """

    unavailable: frozenset[RouteKey] = frozenset()
    background_unavailable: frozenset[RouteKey] = frozenset()
    prepared: frozenset[tuple[RouteKey, bool]] = frozenset()
    precondition_changes: int = 0
    click_refresh: RouteKey | None = None
    failed_steps: Mapping[RouteKey, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "failed_steps", MappingProxyType(dict(self.failed_steps)))


@dataclass(frozen=True)
class ResolutionInput:
    """Fixed operation plus the history and evidence for one resolution attempt.

    Attributes:
        bound: Pinned operation and shared native call budget.
        history: Snapshot of unavailable and prepared routes.
        memory: Receiver, pointer, and menu evidence retained by the write owner.
    """

    bound: BoundWrite
    history: AttemptView
    memory: ResolutionMemory


@dataclass(frozen=True)
class ResolutionFrame:
    """Working evidence accumulated while resolving one write target.

    Attributes:
        request: Fixed operation, attempt history, and retained evidence.
        scope: Inspection arguments such as ancestor bounds, actuator, receiver, and basis.
        facts: Current native target facts, populated by inspection.
        needs_editing_evidence: Whether native preparation or an editing contract must establish
            editing capability.
    """

    request: ResolutionInput
    scope: Mapping[str, Any] = field(default_factory=dict)
    facts: Facts | None = None
    needs_editing_evidence: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "scope", MappingProxyType(dict(self.scope)))


@dataclass(frozen=True)
class ResolvedTarget:
    """Target facts and selection evidence supplied to route selection and dispatch.

    Attributes:
        operation: Fixed semantic target, intent, and input bounds.
        facts: Inspected target capabilities, readiness, and current input state.
        basis: Native fact dependencies supporting the selected actuator or receiver.
        needs_editing_evidence: Whether route choice still requires proof of editing capability.
    """

    operation: OperationSpec
    facts: Facts
    basis: tuple[SelectionDependency, ...]
    needs_editing_evidence: bool


@dataclass(frozen=True)
class ResolvedWrite:
    """Resolved target and evidence retained alongside the original write bounds.

    Attributes:
        bound: Pinned operation token, intent, and remaining budget.
        target: Current native facts and selection dependencies for the fixed target.
        memory: Updated evidence for later attempts within this write.
    """

    bound: BoundWrite
    target: ResolvedTarget
    memory: ResolutionMemory


@dataclass(frozen=True)
class RoutePlan:
    """One selected native primitive and its preparation or input mode.

    Attributes:
        resolved: Resolved target and original write bounds.
        primitive: Driver-declared native unit chosen by route selection.
        prepare: Whether to prepare the receiver rather than send the requested input.
        foreground: Whether this unit uses authorized foreground preparation.
    """

    resolved: ResolvedWrite
    primitive: Primitive
    prepare: bool
    foreground: bool
    click_selection: ClickSelectionDecision | None = None

    def command(self) -> UnitCommand:
        target = self.resolved.target
        return UnitCommand(
            self.primitive,
            target.operation.target,
            target.operation.intent,
            target.facts.actuator,
            self.prepare,
            self.foreground,
            target.facts.receiver,
            self.click_selection,
        )


@dataclass(frozen=True)
class PlannedUnit:
    """Selected route paired with the command sent to the native driver.

    Attributes:
        plan: Route choice and resolved target.
        command: Exact primitive, target, intent, actuator, and receiver for execution.
    """

    plan: RoutePlan
    command: UnitCommand


@dataclass(frozen=True)
class NativeInvocation:
    """Native command paired with the budget sampled on entering its execution scope.

    Attributes:
        unit: Planned command and its resolved target.
        budget: Remaining execution budget in milliseconds.
    """

    unit: PlannedUnit
    budget: int


@dataclass(frozen=True)
class AttemptOutcome:
    """Result of one native unit or an already-complete empty input.

    Attributes:
        resolved: Resolved target used for this attempt.
        command: Executed command, absent for immediate completion.
        step: Native dispatch, preparation, confirmation, and cleanup receipt.
        completed: Completed public receipt, used when empty input needs no native unit.
    """

    resolved: ResolvedWrite
    command: UnitCommand | None = None
    step: StepResult | None = None
    completed: Mapping[str, Any] | None = None
