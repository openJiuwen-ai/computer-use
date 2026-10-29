# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Targets, observations, and execution results shared with native adapters.

Python selects routes from observed facts. Native execution units check the
target and route again at dispatch time.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Literal, Mapping

from computer_use.protocol.execution import validate_primitive_contract

if TYPE_CHECKING:
    pass


class Knowledge(str, Enum):
    YES = "yes"
    NO = "no"
    UNKNOWN = "unknown"


class Channel(str, Enum):
    OBJECT = "object"
    EDITOR = "editor"
    KEYBOARD = "keyboard"
    POINTER = "pointer"


class Dispatch(str, Enum):
    NOT_DISPATCHED = "notDispatched"
    DISPATCHED = "dispatched"
    PARTIAL = "partial"
    UNKNOWN = "unknown"


class Confirmation(str, Enum):
    NOT_REQUESTED = "notRequested"
    CONFIRMED = "confirmed"
    CONTRADICTED = "contradicted"
    UNAVAILABLE = "unavailable"


class Cleanup(str, Enum):
    COMPLETE = "complete"
    FAILED = "failed"
    UNKNOWN = "unknown"


INTENTS = frozenset(
    ("click", "focus", "insert", "replace", "paste", "key", "openMenu", "action", "scroll", "drag", "launch")
)


@dataclass(frozen=True)
class Intent:
    """An operation's semantic kind and immutable parameter mapping."""

    kind: str
    parameters: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if self.kind not in INTENTS:
            raise ValueError(f"Unknown execution intent: {self.kind}")
        object.__setattr__(self, "parameters", MappingProxyType(dict(self.parameters)))


@dataclass(frozen=True)
class Binding:
    """Exact session, window, and element or capture identity for native work."""

    session: str
    instance: str | None
    root_window: str | None
    surface: str | None
    node: str | None = None
    capture: str | None = None

    @property
    def is_desktop(self) -> bool:
        return (
            self.capture is not None
            and self.instance is None
            and self.root_window is None
            and self.surface is None
            and self.node is None
        )

    def same_target(self, other: object) -> bool:
        # Capture identity is part of the binding because it owns the pixel map.
        return self == other

    def canonical(self, value: "Binding") -> "Binding":
        invalid_value = (
            True
            if (
                not isinstance(value, Binding)
                or value.session != self.session
                or value.instance != self.instance
                or value.surface != self.surface
                or value.node != self.node
                or value.capture != self.capture
                or (self.root_window is not None or self.surface is None)
                and value.root_window != self.root_window
            )
            else False
        )
        if invalid_value:
            raise ValueError("Native begin changed an already bound target")
        return value


@dataclass(frozen=True)
class ApplicationBinding:
    """One installed application identified within a native driver session."""

    session: str
    application: str

    def same_target(self, other: object) -> bool:
        return self == other

    def canonical(self, value):
        if value != self:
            raise ValueError("Native begin changed the selected application installation")
        return value


OperationTarget = Binding | ApplicationBinding


@dataclass(frozen=True)
class OpenedOperation:
    """Driver admission token paired with the operation's canonical target."""

    token: str
    target: OperationTarget
    click_contract: str | None = None


@dataclass(frozen=True)
class SelectionDependency:
    """Fields, actions, and relations supporting a semantic route selection."""

    observed: str
    node: str
    fields: tuple[str, ...] = ()
    actions: bool = False
    relations: tuple[str, ...] = ()

    def wire(self):
        return dict(
            observed=self.observed,
            node=self.node,
            fields=list(self.fields),
            actions=self.actions,
            relations=list(self.relations),
        )


@dataclass(frozen=True)
class Actuator:
    """Contained editor selected to receive input for the requested control."""

    node: str
    relation: str = "containedEditor"

    def __post_init__(self):
        from computer_use.protocol.execution import actuator

        actuator.parse(dict(node=self.node, relation=self.relation))

    def wire(self):
        return dict(node=self.node, relation=self.relation)


@dataclass(frozen=True)
class ScrollActuator:
    """An exact native page button and its observed scrolling relationships."""

    node: str
    region: str
    bar: str
    relation: str = field(default="scrollPage", init=False)

    def __post_init__(self):
        from computer_use.protocol.execution import actuator

        actuator.parse(self.wire())
        if self.node in (self.region, self.bar):
            raise ValueError("A page button must be distinct from its scrolling region and bar")

    def wire(self):
        return dict(node=self.node, region=self.region, bar=self.bar, relation=self.relation)


@dataclass(frozen=True)
class ScrollPointerActuator:
    """The nearest scroll area and its current, separately confirmed hit object."""

    node: str
    region: str
    content_path: tuple[str, ...] = ()
    relation: str = field(default="scrollPointer", init=False)

    def __post_init__(self):
        from computer_use.protocol.execution import actuator

        if not isinstance(self.content_path, tuple):
            raise ValueError("A scroll content path must be immutable")
        actuator.parse(self.wire())
        if self.content_path and (
            self.content_path[0] != self.node or len(set(self.content_path)) != len(self.content_path)
        ):
            raise ValueError("A scroll content path must start at its hit and contain distinct nodes")

    def wire(self):
        result = dict(node=self.node, region=self.region, relation=self.relation)
        if self.content_path:
            result["contentPath"] = list(self.content_path)
        return result


SelectedActuator = Actuator | ScrollActuator | ScrollPointerActuator


@dataclass(frozen=True)
class VirtualReceiver:
    """One selected editor's popup; active-item changes remain live facts."""

    popup: str
    via: str
    relation: str = field(default="virtualFocus", init=False)

    def __post_init__(self):
        from computer_use.protocol.execution import receiver

        receiver.parse(self.wire())

    def wire(self):
        return dict(relation=self.relation, popup=self.popup, via=self.via)


@dataclass(frozen=True)
class FocusMember:
    """One current focus object proved to be a part of the selected control."""

    node: str
    relation: str = field(default="containedFocus", init=False)

    def __post_init__(self):
        from computer_use.protocol.execution import receiver

        receiver.parse(self.wire())

    def wire(self):
        return dict(relation=self.relation, node=self.node)


@dataclass(frozen=True)
class FocusScope:
    """The reported focus container of an explicitly addressed native editor."""

    node: str
    relation: str = field(default="ancestorFocus", init=False)

    def __post_init__(self):
        from computer_use.protocol.execution import receiver

        receiver.parse(self.wire())

    def wire(self):
        return dict(relation=self.relation, node=self.node)


SelectedReceiver = VirtualReceiver | FocusMember | FocusScope


@dataclass(frozen=True)
class CurrentReceiverEvidence:
    """Query-only current focus and its evidenced path to the requested scope.

    ``ancestors`` runs from the immediate parent to the requested container,
    inclusive. It says nothing about unrelated children or parents above that
    container. A confirmed result is selection evidence, never dispatch authority.
    Other states affect this forwarding lookup only; they do not retire its scope.
    """

    state: Literal["confirmed", "absent", "unavailable", "conflict"]
    node: str | None = None
    ancestors: tuple[str, ...] = ()
    detail: str = ""

    def __post_init__(self):
        if self.state not in ("confirmed", "absent", "unavailable", "conflict"):
            raise ValueError("Invalid current receiver evidence state")
        if not isinstance(self.ancestors, tuple) or len(set(self.ancestors)) != len(self.ancestors):
            raise ValueError("Current receiver ancestry must be an immutable distinct path")
        if self.state == "confirmed":
            if not self.node or self.node in self.ancestors:
                raise ValueError("Confirmed receiver evidence requires a distinct receiver")
        elif self.node is not None or self.ancestors:
            raise ValueError("Unconfirmed receiver evidence cannot select a target")

    def confirms_scope(self, scope: Binding) -> bool:
        """Require only the path prefix that proves the addressed container."""
        return (
            self.state == "confirmed"
            and scope.node is not None
            and (
                self.node == scope.node
                and not self.ancestors
                or bool(self.ancestors)
                and self.ancestors[-1] == scope.node
            )
        )


@dataclass(frozen=True)
class InputContext:
    """Observed focus, receiver relationships, geometry, and input readiness."""

    foreground_surface: str | None = None
    keyboard_surface: str | None = None
    focused_node: str | None = None
    focus_ancestors: tuple[str, ...] = ()
    editor: str | None = None
    editor_owner: str | None = None
    selection_known: Knowledge = Knowledge.UNKNOWN
    geometry_current: Knowledge = Knowledge.UNKNOWN
    window_route: Knowledge = Knowledge.UNKNOWN
    pointer_route: Knowledge = Knowledge.UNKNOWN
    pointer_hit: str | None = None
    current_receiver: CurrentReceiverEvidence | None = None


@dataclass(frozen=True)
class ClickOffer:
    """Fixed native delivery and route-local admission for the bound click intent."""

    route_family: str
    interaction: Literal["semantic", "pointer"]
    delivery: Literal["object_action", "object_selection", "surface_pointer", "desktop_pointer"]
    admission: Literal["ready", "preparationRequired", "unavailable", "insufficientEvidence"]
    reason: str | None = None
    preparation_options: tuple[str, ...] = ()
    preference_profile: str = "default"
    # A query-local ordering hint; never excludes another valid delivery.
    preference_hint: Literal["readyPhysical"] | None = None

    def wire(self):
        value = dict(
            routeFamily=self.route_family,
            interaction=self.interaction,
            delivery=self.delivery,
            admission=self.admission,
            reason=self.reason,
            preparationOptions=list(self.preparation_options),
            preferenceProfile=self.preference_profile,
        )
        if self.preference_hint is not None:
            value["preferenceHint"] = self.preference_hint
        return value


@dataclass(frozen=True)
class ClickPreparationEvidence:
    """Preparation progress is independent of the requested gesture's progress."""

    kind: str
    scope: Binding
    dispatch: Dispatch
    effect: str
    continuation: str
    observations: tuple[str, ...] = ()
    wait: str = "notNeeded"


@dataclass(frozen=True)
class ClickTransportEvidence:
    """System-call evidence; submission never proves a control consumed the click."""

    kind: str = "none"
    call_entered: bool = False
    call_returned: bool = False
    submitted: int | None = None
    expected: int | None = None


@dataclass(frozen=True)
class ClickContinuation:
    """Native constraint consumed by the sole public replanning Gate."""

    kind: Literal["sameCandidateRefresh", "reselectWithinScope", "observe", "stop"]
    target: Binding
    route_family: str


@dataclass(frozen=True)
class ClickReceipt:
    """Typed click evidence accompanying the existing dispatch/cleanup axes."""

    preparation_evidence: tuple[ClickPreparationEvidence, ...]
    transport_evidence: ClickTransportEvidence
    continuation: ClickContinuation

    @property
    def permits_replanning(self) -> bool:
        return self.continuation.kind in ("sameCandidateRefresh", "reselectWithinScope") and all(
            p.effect == "contextOnly"
            and (
                p.continuation == "withinScope"
                or self.continuation.kind == "sameCandidateRefresh"
                and p.continuation == "sameCandidate"
            )
            for p in self.preparation_evidence
        )


@dataclass(frozen=True)
class ClickSelectionExclusion:
    """Evidence retained with a selection; opaque contract IDs are diagnostic only."""

    route_family: str
    reason: str
    observed: str
    step: int | None = None


@dataclass(frozen=True)
class ClickSelectionDecision:
    """Why background alternatives were ruled out before foreground authority."""

    route_family: str
    foreground: bool
    exclusions: tuple[ClickSelectionExclusion, ...] = ()


@dataclass(frozen=True)
class Primitive:
    """Driver-declared execution unit with its support and readiness evidence."""

    id: str
    unit: str
    channel: Channel
    supported: Knowledge
    readiness: Knowledge = Knowledge.UNKNOWN
    pending: tuple[str, ...] = ()
    background_preparation: Knowledge = Knowledge.UNKNOWN
    action: str | None = None
    action_description: str | None = None
    click: ClickOffer | None = None

    def __post_init__(self):
        from computer_use.protocol.execution import unit_contracts

        if self.unit not in unit_contracts or not self.id:
            raise ValueError("Invalid native primitive contract")
        validate_primitive_contract(self.unit, self.channel, self.supported, self.readiness, self.pending, self.action)
        if self.unit == "native_selection" and self.click is None:
            raise ValueError("Native selection requires an integrated click offer")
        if self.click is not None:
            from computer_use.protocol.execution import validate_click_offer

            validate_click_offer(self.click.wire(), self.unit)


@dataclass(frozen=True)
class Facts:
    """Target identity, available primitives, and input evidence used in planning."""

    binding: OperationTarget
    observed: str
    target_label: str
    identity: Knowledge
    within_scope: Knowledge
    enabled: Knowledge
    blocked_by: tuple[str, ...]
    primitives: tuple[Primitive, ...]
    input: InputContext = field(default_factory=InputContext)
    platform: str | None = None
    native_role: str | None = None
    actions_complete: bool = False
    actuator: SelectedActuator | None = None
    # Structural evidence derived by the common semantic layer.
    has_submenu: bool = False
    # None requests the ancestry-based menu classification.
    application_menu: bool | None = None
    receiver: SelectedReceiver | None = None

    unavailable_reason: str | None = None

    def __post_init__(self):
        if len({p.id for p in self.primitives}) != len(self.primitives):
            raise ValueError("Duplicate primitive id")


@dataclass(frozen=True)
class HostPolicy:
    """Host-selected desktop mode and permission for foreground assistance."""

    mode: str = "shared"
    foreground_assist: bool = False

    def __post_init__(self):
        if self.mode not in ("shared", "exclusive"):
            raise ValueError("Unknown desktop mode")

    @property
    def foreground_allowed(self):
        return self.mode == "exclusive" or self.foreground_assist


@dataclass(frozen=True)
class UnitCommand:
    """Selected primitive, target, and preparation flags sent to the driver."""

    primitive: Primitive
    target: OperationTarget
    intent: Intent
    actuator: SelectedActuator | None = None
    prepare: bool = False
    foreground: bool = False
    receiver: SelectedReceiver | None = None
    click_selection: ClickSelectionDecision | None = None


@dataclass(frozen=True)
class StepResult:
    """Native outcome separating dispatch, preparation, confirmation, and cleanup."""

    target: OperationTarget
    dispatch: Dispatch
    detail: str
    preparation: tuple[str, ...] = ()
    confirmation: Confirmation = Confirmation.NOT_REQUESTED
    cleanup: Cleanup = Cleanup.COMPLETE
    error_code: str | None = None
    # Only an explicit pre-dispatch native result permits another route.
    retry_reason: str | None = None
    context: Mapping[str, Any] | None = None
    application: Mapping[str, Any] | None = None
    click: ClickReceipt | None = None

    def __post_init__(self):
        if self.retry_reason not in (None, "routeUnavailable", "preparationUnavailable", "preconditionChanged"):
            raise ValueError("Invalid replanning reason")
        if self.retry_reason is not None and (
            self.dispatch != Dispatch.NOT_DISPATCHED or self.cleanup != Cleanup.COMPLETE
        ):
            raise ValueError("A possibly dispatched or unclean step cannot authorize retry")
