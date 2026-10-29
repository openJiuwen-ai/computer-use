# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Shared adapter interface for native observation and input execution."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from .contracts import (
    Binding,
    Cleanup,
    Facts,
    HostPolicy,
    Intent,
    OpenedOperation,
    OperationTarget,
    SelectedActuator,
    SelectedReceiver,
    SelectionDependency,
    StepResult,
    UnitCommand,
)

if TYPE_CHECKING:
    from computer_use.protocol.observation.adapters import DriverObservationReply
    from computer_use.protocol.records import Record, RelationPage, TextPage

    from .connection import Connection
    from .lifecycle import CallLifecycle


class NativePort(Protocol):
    """Async driver operations used by the shared read and write pipelines.

    Attributes:
        policy: Host-owned desktop/foreground policy.
        calls: Sole owner of native call admission, deadlines, and cleanup.
        connection: Driver session and capability metadata used by scope checks.
    """

    policy: HostPolicy
    calls: "CallLifecycle"
    connection: "Connection"

    @property
    def platform(self) -> str:
        pass

    @property
    def capabilities(self) -> frozenset[str]:
        pass

    async def connect(self, timeout_ms: int) -> Binding:
        pass

    async def directory(self, operation: str, target: Binding, query: "Record", timeout_ms: int) -> "Record":
        pass

    async def begin_read(self, target: OperationTarget, timeout_ms: int) -> OpenedOperation:
        pass

    async def begin(self, intent: Intent, target: OperationTarget, timeout_ms: int) -> OpenedOperation:
        pass

    async def inspect(
        self,
        operation: str,
        target: OperationTarget,
        timeout_ms: int,
        *,
        within: tuple[str, ...] = (),
        actuator: SelectedActuator | None = None,
        receiver: SelectedReceiver | None = None,
        basis: tuple[SelectionDependency, ...] = (),
        current_receiver: bool = False,
    ) -> Facts:
        pass

    # The port keeps fact budgets and continuation scopes explicit in the observation contract.
    # pylint: disable-next=too-many-arguments
    async def nodes(
        self,
        operation: str,
        target: Binding,
        fields: list[str],
        max_nodes: int,
        max_depth: int,
        timeout_ms: int,
        *,
        scope_kind: str | None = None,
        children_page: "RelationPage | None" = None,
        relation_page: "RelationPage | None" = None,
        advertised_fields: list[str] | None = None,
        follow_relations: list[str] | None = None,
        focused_fields: list[str] | None = None,
        primary_fields: list[str] | None = None,
    ) -> "DriverObservationReply":
        pass

    async def query_nodes(
        self,
        operation: str,
        target: Binding,
        fields: list[str],
        max_nodes: int,
        max_depth: int,
        timeout_ms: int,
        **options: object,
    ) -> "Record":
        pass

    async def wait_for_change(
        self, operation: str, target: Binding, after: str, delay_ms: int, timeout_ms: int
    ) -> "Record":
        pass

    async def relation(
        self, operation: str, target: Binding, relation_property: str, cursor: str | None, limit: int, timeout_ms: int
    ) -> "DriverObservationReply":
        pass

    async def query_relation(
        self, operation: str, target: Binding, relation_property: str, cursor: str | None, limit: int, timeout_ms: int
    ) -> "Record":
        pass

    async def captured_text(
        self, operation: str, target: Binding, text_ref: str, offset: int, length: int, timeout_ms: int
    ) -> "TextPage":
        pass

    async def capture(
        self, operation: str, target: Binding, max_dimension: int, timeout_ms: int, *, kind: str = "window"
    ) -> dict:
        pass

    async def execute(self, operation: str, step: int, command: UnitCommand, timeout_ms: int) -> StepResult:
        pass

    async def finish(self, operation: str) -> Cleanup:
        pass
