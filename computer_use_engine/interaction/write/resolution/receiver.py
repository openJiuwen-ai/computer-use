# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Resolve internal text editors from editing declarations and native containment.

The original requested control remains the semantic target. The selected
actuator and its supporting facts accompany the native input command.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from computer_use.errors import ComputerUseError
from computer_use.native.contracts import Actuator
from computer_use.native.query.graph import UNKNOWN, NativeGraph
from computer_use.native.semantics import (
    COMPOSITE_OWNERS,
    EDITING_FIELDS,
    EDITOR_BOUNDARIES,
    LAYOUT_ROLES,
    SCALAR_EDITORS,
    composite_editor_owner,
    direct_editor,
    editing_evidence,
    preparable_editor,
    role_name,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from computer_use.interaction.records import Remaining
    from computer_use.native.contracts import Binding
    from computer_use.native.port import NativePort


def _scalar_proxy_declarations(values: Mapping[str, object]) -> bool:
    """A hidden scalar proxy must retain the original writable-focus contract."""
    return (
        values["enabled"] is not False
        and values["readOnly"] is not True
        and values["editable"] is not False
        and values["textWritable"] is not True
        and values["focusWritable"] is True
        and values["valueWritable"] is True
    )


@dataclass(frozen=True)
class ReceiverResolution:
    """Resolved editor and its remaining editing-evidence requirement.

    Attributes:
        actuator: Confirmed internal editor, or None when the requested control is used directly.
        needs_editing_evidence: Whether a native preparation or editing contract must establish
            editing capability.
    """

    actuator: Actuator | None = None
    # Native editing contracts can supply the missing evidence; keyboard and
    # paste delivery must wait until the chosen control's semantics are known.
    needs_editing_evidence: bool = False


class ReceiverQuery(NativeGraph):
    """Resolve a composite control's editor from editing declarations and containment evidence."""

    def __init__(
        self,
        port: NativePort,
        operation: str,
        target: Binding,
        remaining: Remaining,
        *,
        max_nodes: int = 256,
        max_depth: int = 8,
    ) -> None:
        super().__init__(
            port,
            operation,
            target,
            remaining,
            max_nodes=max_nodes,
            max_depth=max_depth,
            fields=("role", "subrole", *EDITING_FIELDS),
        )

    async def _role(self, token: str) -> str | object:
        raw = await self._field(token, "role")
        return UNKNOWN if raw is UNKNOWN or raw is None else role_name(self.platform, raw)

    async def resolve(
        self,
        *,
        require_editable: bool = False,
        allow_preparation: bool = False,
        focused: str | None = None,
        allow_internal: bool = True,
    ) -> ReceiverResolution | None:
        """Resolve an editor within the requested control's semantic boundary.

        Args:
            require_editable: Require confirmed text-editing semantics for insertion or
                replacement.
            allow_preparation: Allow a declared editable control to request native editor
                preparation.
            focused: Provider-reported focus token to check as a possible owned editor.
            allow_internal: Allow a confirmed descendant editor to become the actuator.

        Returns:
            The internal editor, when needed, and whether native editing evidence
            is still required before a keyboard route may be selected.
        """
        owner = self.root.node
        if owner is None:
            return ReceiverResolution()
        # Most requests are directly focusable text controls. Read their own
        # fields first; only prefetch subtrees for an actual composite lookup.
        await self._node(owner, depth=0)
        role = await self._role(owner)
        values = await self._control_values(owner)
        direct = editing_evidence(role, values, platform=self.platform)
        owns_editor = composite_editor_owner(role, values, platform=self.platform)
        matches_require_editable = (
            True
            if require_editable
            and (
                values["enabled"] is False
                or values["readOnly"] is True
                or values["editable"] is False
                and not owns_editor
            )
            else False
        )
        if matches_require_editable:
            self.fail("input_unavailable", "The requested control is disabled or read-only.")
        if not allow_internal:
            # A direct-only driver binds its keyboard contract to the exact
            # requested object and checks that object's live focus before
            # dispatch. Do not replace it with a structural child that the
            # driver cannot bind. This still requires editing declarations on
            # the owner itself; an arbitrary container cannot inherit them.
            if require_editable and direct is not True:
                self.fail("input_unavailable", "The requested control has no confirmed direct text-editing capability.")
            return ReceiverResolution()
        # Value/focus declarations do not establish that a scalar control is
        # its own editor. Inspect its bounded contents even before focus: a
        # combo can accept AXValue while only its child actually edits text.
        # Rich documents retain their own boundary; their nested inputs are
        # independent controls, not the document's internal editor.
        reverse = None
        if direct_editor(role, values, platform=self.platform):
            if role in SCALAR_EDITORS:
                reverse = await self._focused_receiver(focused)
            else:
                return ReceiverResolution()
        can_prepare = require_editable and allow_preparation and preparable_editor(role, values, platform=self.platform)
        if role not in COMPOSITE_OWNERS:
            if require_editable and direct is not True:
                self.fail("input_unavailable", "The requested element has no text-editing semantics.")
            return ReceiverResolution()
        if role in LAYOUT_ROLES and await self._field(owner, "editable") is not True:
            # One observed input inside an ordinary form is not that form's
            # editor. A container needs an explicit editing declaration.
            if require_editable:
                self.fail("input_unavailable", "An ordinary container is not a text input control.")
            return ReceiverResolution()
        if not owns_editor:
            return ReceiverResolution()
        # Focus only supplies a candidate. Its current parent path must prove
        # ownership without crossing another semantic control. A provider may
        # expose this relation while omitting the editor from Children.
        reverse = reverse or await self._focused_receiver(focused)
        if can_prepare and role not in SCALAR_EDITORS and reverse is None:
            return ReceiverResolution(needs_editing_evidence=True)
        found, boundaries, visited = [], [], set()

        async def visit(token: str, ancestors: tuple[str, ...]) -> None:
            """Collect editor boundaries through the control's layout wrappers."""
            self.remaining()
            if token in ancestors or len(ancestors) > self.max_depth:
                self.fail("observation_incomplete", "Input receiver containment is cyclic or exceeds its depth budget.")
            if token in visited:
                return
            visited.add(token)
            if len(visited) > self.max_nodes:
                self.fail("observation_incomplete", "Input receiver search exceeded its node budget.")
            role = await self._role(token)
            if role is UNKNOWN:
                self.fail("observation_incomplete", "An unknown node may contain a separate input control.")
            self._check_parent(token, ancestors)
            values = await self._control_values(token)
            editable = editing_evidence(role, values, platform=self.platform)
            if editable is True or role in EDITOR_BOUNDARIES:
                # Disabled/read-only fields remain independent controls. They
                # cannot make a multi-field owner look like one unique editor.
                boundaries.append(token)
                if len(boundaries) > 1:
                    self.fail("ambiguous_target", "The requested control contains multiple independent editors.")
            if editable is True:
                found.append(ReceiverResolution(Actuator(token)))
                return  # Do not enter a separate editor's children.
            if require_editable and allow_preparation and preparable_editor(role, values, platform=self.platform):
                found.append(ReceiverResolution(Actuator(token), needs_editing_evidence=True))
                return
            if editable is UNKNOWN:
                self.fail("observation_incomplete", "The input control has incomplete editing declarations.")
            # Only layout wrappers may be crossed. Tables, rows, cells, other
            # controls, documents and windows remain independent boundaries.
            if role not in LAYOUT_ROLES:
                return
            if values["enabled"] is False or values["readOnly"] is True:
                return
            actions = await self._actions(token)
            if actions["items"]:
                return
            if actions["state"] != "complete":
                self.fail("observation_incomplete", "A layout wrapper has incomplete action declarations.")
            for child in await self._relation(token, "children"):
                await visit(child, ancestors + (token,))

        for child in await self._relation(owner, "children"):
            await visit(child, (owner,))
        if reverse is not None:
            if any(token != reverse.actuator.node for token in boundaries):
                self.fail("ambiguous_target", "The focused editor is not the only editor of the requested control.")
            return reverse
        if found:
            return found[0]
        if require_editable and boundaries:
            self.fail("input_unavailable", "The requested control contains an editor that cannot accept text.")
        if can_prepare:
            return ReceiverResolution(needs_editing_evidence=True)
        if require_editable and not found and direct is not True:
            self.fail("input_unavailable", "No confirmed text editor belongs to the requested control.")
        return ReceiverResolution()

    async def _focused_receiver(self, focused: str | None) -> ReceiverResolution | None:
        """Prove ownership of the reported editor through its parent chain.

        Only a successfully established relation contributes dependencies to the
        containing receiver query.
        """
        if focused is None or focused == self.root.node:
            return None
        # Evidence about an unrelated current focus must not become a guard on
        # the requested control. Keep only a successfully proved association.
        previous, self.dependencies = self.dependencies, {}
        accepted = False
        try:
            await self._node(focused, depth=0)
            if not direct_editor(
                await self._role(focused), await self._control_values(focused), platform=self.platform
            ):
                return None
            current, seen = focused, set()
            for _ in range(self.max_depth):
                if current in seen:
                    return None
                seen.add(current)
                parents = await self._relation(current, "parent", read_missing=True)
                if len(parents) != 1:
                    return None
                parent = parents[0]
                if parent == self.root.node:
                    accepted = True
                    return ReceiverResolution(Actuator(focused))
                await self._node(parent, depth=0)
                role, values = await self._role(parent), await self._control_values(parent)
                matches_role = (
                    True
                    if (
                        self.platform == "macos"
                        and role in SCALAR_EDITORS
                        and await self._role(self.root.node) == "textField"
                        and not await self._relation(self.root.node, "children")
                        and not await self._relation(parent, "children")
                        and _scalar_proxy_declarations(values)
                    )
                    else False
                )
                if matches_role:
                    # AppKit can expose its live TextArea through Parent only:
                    # editor -> temporary text proxy -> observed scalar field.
                    # A published child editor remains an independent boundary;
                    # this exception only interprets an otherwise hidden proxy.
                    actions = await self._actions(parent)
                    if actions["state"] == "complete" and all(
                        a["name"] in ("AXPress", "AXCancel", "AXScrollToVisible") for a in actions["items"]
                    ):
                        current = parent
                        continue
                if role not in LAYOUT_ROLES or values["enabled"] is False or values["readOnly"] is True:
                    return None
                if values["editable"] is True or editing_evidence(role, values, platform=self.platform) is not False:
                    return None
                actions = await self._actions(parent)
                if actions["state"] != "complete" or actions["items"]:
                    return None
                current = parent
            return None
        except ComputerUseError as error:
            if error.effect != "none" or error.code not in (
                "observation_incomplete",
                "stale_target",
                "input_unavailable",
            ):
                raise
            return None
        finally:
            dependencies, self.dependencies = self.dependencies, previous
            if accepted:
                for (observed, node), value in dependencies.items():
                    self._use(observed, node, **value)
