"""Recognize existing completion."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.write.model import ResolvedWrite
from computer_use.pipeline import Contract, Pass

if TYPE_CHECKING:
    pass


def completion_branch(resolved: ResolvedWrite, _: object) -> str:
    """Choose immediate completion for empty insertion or paste text."""
    intent = resolved.bound.spec.intent
    return "empty" if intent.kind in ("insert", "paste") and intent.parameters.get("text") == "" else "route"


stage = Pass.define(
    name="recognize_existing_completion",
    function=completion_branch,
    contract=Contract(ResolvedWrite, str),
)
