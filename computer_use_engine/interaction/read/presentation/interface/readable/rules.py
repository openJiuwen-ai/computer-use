# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Pure predicates for exact status notation and conservative form annotations."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from computer_use.interaction.read.presentation.interface.atoms.model import AtomPlan


def identifier_caption(value: object) -> bool:
    """Recognize identifier-shaped form labels without interpreting their meaning.

    Callers restrict this test to already-established form fields. A match changes
    placement only: the exact value and its original reference remain in notes.
    """
    return isinstance(value, str) and bool(
        re.fullmatch(r"(?:_?Title|[A-Za-z][A-Za-z0-9_]*_(?:title|label|lable|name|widget|button|desc|line))", value)
    )


def toggle_glyph(role: str, value: object) -> str | None:
    """Use ASCII markers for explicit checkbox and radio checked states."""
    if role not in ("checkBox", "radioButton"):
        return None
    if role == "checkBox" and type(value) is str and value == "mixed":
        return "[-]"
    if value is True or type(value) is int and value == 1 or type(value) is str and value in ("on", "true", "1"):
        enabled = True
    elif value is False or type(value) is int and value == 0 or type(value) is str and value in ("off", "false", "0"):
        enabled = False
    else:
        return None
    return ("(*)" if enabled else "( )") if role == "radioButton" else ("[x]" if enabled else "[ ]")


def passive_note_source(atom: AtomPlan, keys: tuple[str, ...]) -> bool:
    """Require every field in a complete passive atom to have a numbered destination."""
    capabilities = atom.source_fields.get("capabilities", {})
    return (
        bool(keys)
        and set(keys) == {slot.key for slot in atom.slots}
        and atom.source_fields.get("role") in ("staticText", "image")
        and not atom.actions
        and not atom.actions_unavailable
        and not atom.states
        and not atom.children
        and not atom.declarations
        and not atom.coverage.partial
        and isinstance(capabilities, Mapping)
        and not any(value == "available" for value in capabilities.values())
    )
