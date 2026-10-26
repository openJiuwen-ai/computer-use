"""Collect publication sources."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.read.publication.model import PublicationRequest, PublicationSources
from computer_use.pipeline import Contract, Pass

if TYPE_CHECKING:
    from computer_use.pipeline.core import Control


def sources(request: PublicationRequest, control: Control) -> PublicationSources:
    """Order the optional primary tree and additional target/snapshot pairs for publication."""
    observations = (((request.target, request.tree),) if request.tree is not None else ()) + request.extra_trees
    return PublicationSources(request, observations)


stage = Pass.define(
    name="collect_publication_sources",
    function=sources,
    contract=Contract(PublicationRequest, PublicationSources),
)
