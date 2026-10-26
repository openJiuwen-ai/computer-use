from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.read.presentation.formatting import jsonString

if TYPE_CHECKING:
    from typing import Any


def textSourceLabel(source: Any) -> str:
    observed = jsonString(source["observed"]["id"]) if source.get("observed") else "unknown"
    target = f"element {source['ref']}" if source.get("ref") is not None else "query result"
    return f"Captured text: {target}; observation={observed}; snapshot, not a live read."
