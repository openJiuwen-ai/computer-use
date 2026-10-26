"""Request routing retained beside, rather than inside, the observation graph."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Mapping

from ..model import FieldPath, NativeObservation, NodeId


@dataclass(frozen=True, slots=True)
class DriverReadMetadata:
    """Native query selectors and request scope for bounded follow-up reads.

    Attributes:
        surface: Existing driver interaction surface that owns this request.
        change_token: Provider token used by the existing change-wait endpoint.
        relation_fields: Original selectors indexed by node and public relation key.
        requested_fields: Original field selectors included in this acquisition.
        requested_relations: Original relationship selectors included in this acquisition.
        field_sources: Original selectors indexed by node and normalized field path.
        operation_capabilities: SDK route hints, separate from provider-declared actions.
        input_containers: Native input-scope classification; true forwards keyboard input
            to an evidenced current receiver, false retains the named element target.
    """

    surface: str
    change_token: str | None
    relation_fields: Mapping[tuple[NodeId, str], str]
    requested_fields: Mapping[NodeId, tuple[str, ...]]
    requested_relations: tuple[str, ...]
    field_sources: Mapping[tuple[NodeId, FieldPath], str]
    operation_capabilities: Mapping[NodeId, Mapping[str, str]] = field(default_factory=dict)
    input_containers: Mapping[NodeId, bool] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for key in ("relation_fields", "requested_fields", "field_sources", "input_containers"):
            object.__setattr__(self, key, MappingProxyType(dict(getattr(self, key))))
        object.__setattr__(
            self,
            "operation_capabilities",
            MappingProxyType(
                {identity: MappingProxyType(dict(hints)) for identity, hints in self.operation_capabilities.items()}
            ),
        )


@dataclass(frozen=True, slots=True)
class DriverObservationReply:
    """A normalized graph and the independent scope of its driver query."""

    observation: NativeObservation
    metadata: DriverReadMetadata
