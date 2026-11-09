# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Qualify a native pointer hit as a component of the requested control.

Passive labels and layout members may supply the hit path. Independent
controls form boundaries even when they lie inside the owner's rectangle.
"""

from __future__ import annotations

from copy import deepcopy
from typing import TYPE_CHECKING

from computer_use.native.contracts import Actuator
from computer_use.native.query.graph import NativeGraph
from computer_use.native.semantics import (
    LAYOUT_ROLES,
    POINTER_CONTROL_OWNERS,
    UNKNOWN,
    action_for_intent,
    control_member_role,
    role_name,
)

if TYPE_CHECKING:
    from typing import Sequence

    from computer_use.interaction.records import Remaining
    from computer_use.native.contracts import Binding, Intent
    from computer_use.native.port import NativePort


class ControlPartQuery(NativeGraph):
    """Prove that a native pointer hit is a member of the requested control."""

    def __init__(
        self,
        port: NativePort,
        operation: str,
        target: Binding,
        remaining: Remaining,
        *,
        max_nodes: int = 64,
        max_depth: int = 8,
        intent: Intent | None = None,
    ) -> None:
        self.intent = intent
        super().__init__(
            port,
            operation,
            target,
            remaining,
            max_nodes=max_nodes,
            max_depth=max_depth,
            fields=("role", "editable", "focusWritable", "valueWritable", "textWritable"),
        )

    async def resolve(self, hit: str | None, *, allow_passive_candidate: bool = False) -> Actuator | None:
        """Qualify a pointer member through its role, actions, and path to the control.

        Args:
            hit: Native token returned by pointer hit testing, or None when unavailable.
            allow_passive_candidate: Let initial compound text refinement use a passive label
                whose point the driver must still confirm.

        Returns:
            The confirmed control-part actuator, or None when the path crosses an
            independent control or lacks sufficient evidence.
        """
        owner = self.root.node
        if owner is None or hit is None or hit == owner:
            return None
        # Probe the owner before expanding anything. Once its control role is
        # established, NativeGraph can batch the small member subtree instead
        # of requiring a separate RPC for every wrapper and label.
        await self._node(owner, depth=0)
        role = await self._field(owner, "role")
        if role is UNKNOWN:
            return None
        owner_role = role_name(self.platform, role)
        if owner_role in {"textField", "textArea", "webArea"} and not any(
            [
                await self._field(owner, "editable") is True,
                await self._field(owner, "valueWritable") is True,
                await self._field(owner, "textWritable") is True,
            ]
        ):
            return None
        owner_actions = None
        text_context_owner = (
            self.platform == "macos"
            and owner_role == "staticText"
            and self.intent is not None
            and self.intent.kind == "click"
            and self.intent.parameters.get("button") == "right"
        )
        if owner_role not in POINTER_CONTROL_OWNERS | {"row", "cell", "textField", "textArea", "webArea"}:
            if owner_role not in LAYOUT_ROLES and not text_context_owner:
                return None
            owner_actions = await self._actions(owner)
            if text_context_owner and owner_actions["state"] != "complete":
                return None
            if not action_for_intent(
                self.platform,
                role,
                [a["name"] for a in owner_actions["items"]],
                owner_actions["state"] == "complete",
                "click",
            ):
                return None
        # Right click remains a pointer event. Context-menu/scroll support on
        # internal layout nodes is not an independent primary control action.
        support_actions = set()
        matches_owner_role = (
            True
            if self.platform == "macos"
            and (
                owner_role in {"row", "cell"}
                or self.intent is not None
                and self.intent.parameters.get("button") == "right"
            )
            else False
        )
        if matches_owner_role:
            owner_actions = owner_actions or await self._actions(owner)
            if owner_actions["state"] == "complete":
                support_actions = {"AXScrollToVisible"}
                if owner_role in {"row", "cell"} or any(a["name"] == "AXShowMenu" for a in owner_actions["items"]):
                    support_actions.add("AXShowMenu")

        async def visit(token: str, ancestors: tuple[str, ...], *, alternate: bool) -> str | None:
            """Check one member and retain the parent evidence on an accepted hit path."""
            if token in ancestors or len(ancestors) > self.max_depth:
                self.fail("observation_incomplete", "Control composition is cyclic or exceeds its depth budget.")
            raw_role = await self._field(token, "role")
            if raw_role is UNKNOWN:
                return None
            role = role_name(self.platform, raw_role)
            renameable_caption = (
                not alternate
                and self.platform == "macos"
                and owner_role in {"row", "cell"}
                and token == hit
                and role == "textField"
            )
            if renameable_caption:
                # Outline/table captions can be renameable text fields. A
                # single click at their confirmed native hit selects that item;
                # it does not confirm/open it or authorize typing. Do not
                # apply this to embedded editors or independent buttons.
                actions = await self._actions(token)
                names = {a["name"] for a in actions["items"]}
                matches_names = (
                    True
                    if (
                        actions["state"] == "complete"
                        and "AXConfirm" in names
                        and names <= support_actions | {"AXConfirm", "AXCancel"}
                        and await self._field(token, "textWritable") is False
                        and not await self._relation(token, "children")
                    )
                    else False
                )
                if matches_names:
                    self._check_parent(token, ancestors)
                    return token
            kind = control_member_role(role)
            # A cell is a logical item, not a transparent button wrapper.
            # A table row may expose its ordinary passive cell content as
            # the exact pointer hit. Keep cells as boundaries everywhere else,
            # and never accept the cell itself or its independent controls.
            row_cell = (
                self.platform == "macos"
                and owner_role == "row"
                and role == "cell"
                and ancestors == (owner,)
                and token != hit
            )
            if row_cell:
                row_cell = await self._field(hit, "role") in ("AXStaticText", "AXHeading", "AXImage")
            if kind is None:
                return None
            if kind == "layout" and role not in LAYOUT_ROLES and not row_cell:
                return None
            actions = await self._actions(token)
            allowed_actions = support_actions if kind == "layout" or text_context_owner and kind == "label" else set()
            if actions["state"] != "complete" or any(a["name"] not in allowed_actions for a in actions["items"]):
                return None
            for field in ("editable", "focusWritable", "valueWritable", "textWritable"):
                value = await self._field(token, field)
                if value is True or value is UNKNOWN:
                    return None
            children = await self._relation(token, "children")
            if kind != "layout" and children:
                return None
            found = token if (kind == "label" if alternate else token == hit) else None
            if not found and kind == "layout":
                found = await descend(children, ancestors + (token,), alternate=alternate)
            if found:
                self._check_parent(token, ancestors)
            return found

        async def descend(children: Sequence[str], ancestors: tuple[str, ...], *, alternate: bool) -> str | None:
            """Search the bounded child path and retain dependencies only for an accepted member."""
            for child in children:
                previous = deepcopy(self.dependencies)
                selected = await visit(child, ancestors, alternate=alternate)
                if selected:
                    return selected
                # The identity of the supplied hit is already fixed. An
                # unrelated sibling's role/action state is not a dependency.
                self.dependencies = previous
            return None

        children = await self._relation(owner, "children")
        owner_basis = deepcopy(self.dependencies)
        selected = await descend(children, (owner,), alternate=False)
        passive_menu_candidate = (
            allow_passive_candidate
            and text_context_owner
            and self.intent.parameters.get("count", 1) == 1
            and "AXShowMenu" in support_actions
        )
        if passive_menu_candidate and (
            selected is None or role_name(self.platform, await self._field(selected, "role")) in LAYOUT_ROLES
        ):
            hit_basis = self.dependencies
            self.dependencies = owner_basis
            preferred = await descend(children, (owner,), alternate=True)
            if preferred is None:
                self.dependencies = hit_basis
            else:
                selected = preferred
        return Actuator(selected, "controlPart") if selected is not None else None
