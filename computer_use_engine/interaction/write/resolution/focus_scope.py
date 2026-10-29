# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Interpret coarse macOS focus reports using the requested editor's ancestry.

A provider may report a layout container as FocusedUIElement while the editor
reports local focus. The selected relation is sampled again by the native unit.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.native.contracts import FocusScope
from computer_use.native.query.graph import NativeGraph
from computer_use.native.semantics import EDITING_FIELDS, UNKNOWN, editing_evidence, role_name

if TYPE_CHECKING:
    from computer_use.interaction.records import Remaining
    from computer_use.native.contracts import Binding
    from computer_use.native.port import NativePort


class FocusScopeQuery(NativeGraph):
    """Relate a requested editor to a coarse macOS focus report on its layout ancestor."""

    def __init__(self, port: NativePort, operation: str, editor: Binding, remaining: Remaining) -> None:
        super().__init__(
            port, operation, editor, remaining, max_nodes=16, max_depth=8, fields=("role", *EDITING_FIELDS)
        )

    async def _role(self, token: str) -> str | object:
        value = await self._field(token, "role")
        return UNKNOWN if value is UNKNOWN or value is None else role_name(self.platform, value)

    async def resolve(self, focused: str | None) -> FocusScope | None:
        """Confirm that a coarse macOS focus report names a layout ancestor of the editor."""
        editor = self.root.node
        if self.platform != "macos" or editor is None or focused is None:
            return None
        if focused == editor:
            return None
        await self._node(editor, depth=0)
        role, values = await self._role(editor), await self._control_values(editor)
        if role not in ("textField", "textArea") or editing_evidence(role, values, platform=self.platform) is not True:
            return None
        windows = await self._relation(editor, "window")
        if len(windows) != 1:
            return None
        current, seen = editor, {editor}
        for _ in range(self.max_depth):
            parents = await self._relation(current, "parent", read_missing=True)
            if len(parents) != 1 or parents[0] in seen:
                return None
            current = parents[0]
            seen.add(current)
            await self._node(current, depth=0)
            # Window, document editors and independent controls are boundaries.
            # A writable Focused attribute alone does not turn layout into text.
            if await self._role(current) not in ("group", "scrollArea", "splitGroup", "layoutArea"):
                return None
            values = await self._control_values(current)
            if values["enabled"] is False or any(
                values[k] is not False and values[k] is not None for k in ("editable", "valueWritable", "textWritable")
            ):
                return None
            if await self._relation(current, "window") != windows:
                return None
            if current == focused:
                return FocusScope(focused)
        return None
