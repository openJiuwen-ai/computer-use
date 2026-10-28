"""Resolve container forwarding from scope-bounded native focus evidence.

New drivers report the current recipient and its live parent path in one query.
The legacy exhaustive lookup exists only for drivers without that capability;
an unavailable or conflicting new observation never starts a second search.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from computer_use.native.contracts import Knowledge
from computer_use.native.query.graph import UNKNOWN, NativeGraph
from computer_use.native.semantics import EDITING_FIELDS, LAYOUT_ROLES, editing_evidence, role_name

if TYPE_CHECKING:
    from computer_use.interaction.records import Remaining
    from computer_use.native.contracts import Binding, Intent
    from computer_use.native.port import NativePort


class CurrentReceiverQuery(NativeGraph):
    "Find the single focused keyboard receiver within an explicitly addressed container."

    def __init__(self, port: NativePort, operation: str, target: Binding, remaining: Remaining) -> None:
        super().__init__(
            port, operation, target, remaining, max_nodes=256, max_depth=8, fields=("role", "focused", *EDITING_FIELDS)
        )

    async def resolve(self, intent: Intent) -> tuple[Binding, tuple[str, ...]]:
        """Resolve the container's single focused receiver and its ancestor constraints.

        Args:
            intent: Operation whose insertion or paste semantics require an editable receiver.

        Returns:
            The receiver binding and its path of containing native tokens.

        Raises:
            ComputerUseError: Focus is missing, ambiguous, outside the container, or
                incompatible with the requested text input.
        """
        if "nativeCurrentReceiver" in getattr(self.port, "capabilities", ()):
            return await self._native_receiver(intent)
        return await self._legacy_receiver(intent)

    async def _native_receiver(self, intent: Intent) -> tuple[Binding, tuple[str, ...]]:
        facts = await self.port.inspect(self.operation, self.root, self.remaining(), current_receiver=True)
        if facts.binding != self.root or facts.identity != Knowledge.YES or facts.within_scope != Knowledge.YES:
            self.fail(
                "observation_incomplete", "Current receiver discovery did not confirm the requested container boundary."
            )
        current = facts.input.current_receiver
        if current is None:
            self.fail("observation_incomplete", "The driver did not return its declared current receiver evidence.")
        if current.state != "confirmed":
            code = "observation_incomplete" if current.state == "unavailable" else "input_unavailable"
            self.fail(code, current.detail or "The container has no confirmed current keyboard receiver.")
        if not current.confirms_scope(self.root):
            self.fail("observation_incomplete", "Current receiver evidence does not reach the requested container.")
        token = current.node
        # Only the receiver's editing semantics matter. No children/relations
        # enter the decision or its native selection dependencies.
        await self._node(token, depth=0)
        raw = await self._field(token, "role")
        if raw is UNKNOWN:
            self.fail("observation_incomplete", "The current receiver role is unknown.")
        role = role_name(self.platform, raw)
        if role in LAYOUT_ROLES | {"window", "sheet", "dialog"}:
            self.fail("input_unavailable", "The reported focus is a container, not a confirmed keyboard recipient.")
        if intent.kind in ("insert", "paste"):
            values = await self._control_values(token)
            if editing_evidence(role, values, platform=self.platform) is not True:
                self.fail("input_unavailable", "The current receiver does not declare text-editing semantics.")
        # Freeze the requested boundary, not every incidental wrapper in the
        # observation path. Native inspection and dispatch recheck containment.
        return replace(self.root, node=token), (() if token == self.root.node else (self.root.node,))

    async def _legacy_receiver(self, intent: Intent) -> tuple[Binding, tuple[str, ...]]:
        "Compatibility for old drivers with no native current-receiver query."
        found, seen = [], set()

        async def visit(token: str, path: tuple[str, ...]) -> None:
            "Collect focus reports within the container, stopping at separate window boundaries."
            if token in seen:
                self.fail("observation_incomplete", "Current receiver containment is cyclic or shared.")
            if len(path) > self.max_depth or len(seen) >= self.max_nodes:
                self.fail("observation_incomplete", "Current receiver lookup exceeded its bounded scope.")
            seen.add(token)
            raw = await self._field(token, "role")
            if raw is UNKNOWN:
                self.fail("observation_incomplete", "A receiver role is unknown.")
            role = role_name(self.platform, raw)
            if token != self.root.node and role in ("window", "sheet", "dialog"):
                return  # A window Element does not follow separate modal windows.
            if path:
                self._check_parent(token, path)
            focused = await self._field(token, "focused")
            if focused is True and role not in LAYOUT_ROLES | {"window", "sheet", "dialog"}:
                found.append((token, path, role))
            for child in await self._relation(token, "children"):
                await visit(child, path + (token,))

        await visit(self.root.node, ())
        leaves = []
        for entry in found:
            if not any(entry[0] in other[1] for other in found):
                leaves.append(entry)
        if len(leaves) != 1:
            self.fail(
                "ambiguous_target" if len(leaves) > 1 else "input_unavailable",
                (
                    "The container has no single confirmed current keyboard receiver; no "
                    "descendant was chosen by position or "
                    "count."
                ),
            )
        token, path, role = leaves[0]
        if intent.kind in ("insert", "paste"):
            values = await self._control_values(token)
            if editing_evidence(role, values, platform=self.platform) is not True:
                self.fail("input_unavailable", "The current receiver does not declare text-editing semantics.")
        return replace(self.root, node=token), path
