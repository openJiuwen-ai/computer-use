from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from computer_use.native.contracts import Binding


def window_binding(binding: Binding) -> Binding:
    return replace(binding, node=None, capture=None)
