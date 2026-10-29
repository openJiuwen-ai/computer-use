# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Route identity, attempt history, and target-boundary checks shared by write selection."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, cast

from computer_use.errors import ComputerUseError
from computer_use.native.contracts import (
    ApplicationBinding,
    Binding,
    Channel,
    Facts,
    HostPolicy,
    Intent,
    Knowledge,
    OperationTarget,
    SelectedActuator,
    SelectedReceiver,
    UnitCommand,
)

if TYPE_CHECKING:
    from typing import NoReturn

    from computer_use.native.contracts import Primitive


def refuse(
    code: str,
    facts: Facts,
    detail: str,
    next_step: str = "read the target and choose a supported operation",
) -> NoReturn:
    raise ComputerUseError(dict(code=code, effect="none", target=facts.target_label, detail=detail, next=next_step))


@dataclass
class Attempts:
    """Route history and receiver-stability budget owned by one write call.

    Attributes:
        unavailable: Semantic routes that failed before dispatch and are excluded thereafter.
        background_unavailable: Routes whose background preparation failed.
        prepared: Route and foreground-mode pairs already prepared.
        precondition_changes: Consumed pre-dispatch precondition-change allowance.
        actuators: Distinct selected actuator/receiver pairs observed during the write.
    """

    unavailable: set["RouteKey"] = field(default_factory=set)
    background_unavailable: set["RouteKey"] = field(default_factory=set)
    prepared: set[tuple["RouteKey", bool]] = field(default_factory=set)
    precondition_changes: int = 0
    actuators: set[tuple[SelectedActuator | None, SelectedReceiver | None]] = field(default_factory=set)
    click_refresh: RouteKey | None = None
    failed_steps: dict[RouteKey, int] = field(default_factory=dict)

    def observe(self, facts: Facts) -> None:
        """Record the actuator/receiver pair and bound editor churn during one write."""
        # Preparation can replace a field editor, but an endless stream of new
        # native objects cannot keep granting fresh preparation attempts.
        self.actuators.add((facts.actuator, facts.receiver))
        if len(self.actuators) > 2:
            refuse(
                "input_unavailable",
                facts,
                "The selected receiver kept changing during this operation.",
                "read the original target after its editor settles",
            )


@dataclass(frozen=True)
class RouteKey:
    """Semantic route identity used to share attempt limits across refreshed native contracts.

    Attributes:
        target: Fixed native target of the write.
        intent: Requested operation kind.
        unit: Native unit family, such as key_sequence or pointer_sequence.
        channel: Object, editor, keyboard, or pointer delivery channel.
        actuator: Selected control part or internal editor, when applicable.
        receiver: Selected focus-member, focus-scope, or virtual-focus relation.
    """

    target: OperationTarget
    intent: str
    unit: str
    channel: Channel
    actuator: SelectedActuator | None
    receiver: SelectedReceiver | None
    click_family: str | None = None
    activation: bool | None = None


def route_key(command: UnitCommand) -> RouteKey:
    """Identify a semantic route across changing native contract IDs.

    The key retains the target, intent, unit/channel, and selected receiver.
    Refreshing an opaque primitive ID therefore preserves the attempt history.
    """
    return RouteKey(
        command.target,
        command.intent.kind,
        command.primitive.unit,
        command.primitive.channel,
        command.actuator,
        command.receiver,
        command.primitive.click.route_family if command.primitive.click is not None else None,
        command.foreground if command.primitive.click is not None else None,
    )


def _candidates(facts: Facts, intent: Intent, policy: HostPolicy | None = None) -> list[Primitive]:
    # Compatibility entry for internal probes; one candidate implementation.
    from computer_use.interaction.write.routes.selection.candidates import enumerate_candidates

    return list(enumerate_candidates(facts, intent, policy))


def validate_target(facts: Facts, policy: HostPolicy) -> None:
    """Validate target identity, working-window scope, modal state, and desktop-mode bounds."""
    installation = isinstance(facts.binding, ApplicationBinding)
    if (
        not installation
        and cast("Binding", facts.binding).surface is None
        and not cast("Binding", facts.binding).is_desktop
    ):
        refuse(
            "invalid_request", facts, "Session discovery has no input target; select a working window before writing."
        )
    if facts.identity == Knowledge.NO or facts.within_scope == Knowledge.NO:
        refuse("stale_target", facts, "The original target identity or working-window boundary changed.")
    if facts.identity != Knowledge.YES or facts.within_scope != Knowledge.YES:
        refuse("input_unavailable", facts, "Current target identity or input scope cannot be confirmed.")
    if facts.blocked_by:
        # Native surface identities are diagnostic facts, not public window or
        # element references. A fresh read supplies the actionable references.
        refuse(
            "blocked_by_modal",
            facts,
            "A modal dialog blocks the requested target.",
            "call app.read() to observe the dialog, then use the window or element references returned by that read",
        )
    matches_installation = (
        True
        if (
            not installation
            and cast("Binding", facts.binding).instance is None
            and cast("Binding", facts.binding).root_window is None
            and policy.mode != "exclusive"
        )
        else False
    )
    if matches_installation:
        refuse("mode_unsupported", facts, "Desktop-wide input requires exclusive mode.")


def has_independent_text_route(facts: Facts, intent: Intent, attempts: RouteHistory | None = None) -> bool:
    """Check for an object-editing route ready independently of the input receiver."""
    for primitive in _candidates(facts, intent):
        if primitive.unit != "native_text_edit" or primitive.channel != Channel.OBJECT:
            continue
        if any(condition in primitive.pending for condition in ("receiver", "selection", "geometry")):
            continue
        if attempts is not None:
            command = UnitCommand(primitive, facts.binding, intent, facts.actuator, receiver=facts.receiver)
            if route_key(command) in attempts.unavailable:
                continue
        return True
    return False


def has_ready_route(facts: Facts, intent: Intent) -> bool:
    """Check for a ready native route other than property replacement."""
    return any(p.readiness == Knowledge.YES and p.unit != "native_value_replace" for p in _candidates(facts, intent))


def needs_pointer_member(facts: Facts, intent: Intent, attempts: RouteHistory) -> bool:
    """Check whether every remaining click route requires pointer geometry."""

    def _iter_p(_source_items: Iterator[Any]) -> Iterator[Any]:
        for p in _source_items:
            if (
                route_key(UnitCommand(p, facts.binding, intent, facts.actuator)) not in attempts.unavailable
                and "enabled" not in p.pending
            ):
                yield p

    candidates = list(_iter_p(iter(_candidates(facts, intent))))
    return any(p.unit == "pointer_sequence" and "geometry" in p.pending for p in candidates) and not any(
        "geometry" not in p.pending for p in candidates
    )


def choose(
    facts: Facts,
    intent: Intent,
    policy: HostPolicy,
    attempts: Attempts,
    *,
    needs_editing_evidence: bool = False,
) -> UnitCommand:
    """Select a route through the staged selection implementation."""
    from computer_use.interaction.write.routes.selection.pipeline import choose_route

    return choose_route(facts, intent, policy, attempts, needs_editing_evidence=needs_editing_evidence)


if TYPE_CHECKING:
    from computer_use.interaction.write.routes.selection.model import RouteHistory
