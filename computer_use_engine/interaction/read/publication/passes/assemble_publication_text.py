"""Assemble publication text."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.read.presentation.content import DisplayText, template
from computer_use.interaction.read.presentation.content import json_value as quoted
from computer_use.interaction.read.presentation.document import DisplayDocument
from computer_use.interaction.read.publication.model import PublicationSources, PublicationText
from computer_use.pipeline import Contract, Effect, Pass

if TYPE_CHECKING:
    from typing import Callable

    from computer_use.pipeline.core import Control


def create(
    *, capabilities_of: Callable[[], frozenset[str]], page_hints: Callable[..., DisplayText]
) -> Callable[[PublicationSources, Control], PublicationText]:

    def text(value: PublicationSources, control: Control) -> PublicationText:
        """Assemble observation text, continuation hints, and retained native details.

        When the driver supports relation pages, append the applicable continuation commands.
        Keep complete per-snapshot detail text alongside the model-facing observation text.
        """
        request = value.request
        document = (
            request.document
            if isinstance(request.document, DisplayDocument)
            else DisplayDocument.join((request.document,))
        )
        if "nativeRelationPages" in capabilities_of():
            for (binding, snapshot), presentation in zip(value.observations, value.rendered):
                document = DisplayDocument.join(
                    (
                        document,
                        page_hints(binding, snapshot, compact=True, inline_children=presentation.inline_child_refs),
                    ),
                    separator="",
                )
        details = [document]
        for (_, snapshot), presentation in zip(value.observations, value.rendered):
            details.append(
                DisplayDocument.join(
                    (
                        template("Captured native details {}:", quoted(snapshot.facts["observed"]["id"])),
                        DisplayDocument.tree(presentation, details=True),
                    )
                )
            )
        if value.observations:
            document = DisplayDocument.join(
                (
                    document,
                    "Details: observation.details contains original fields, actions and relations from this read.",
                )
            )
        return PublicationText(value, document, DisplayDocument.join(details, separator="\n\n"))

    return text


stage = Pass.factory(
    name="assemble_publication_text",
    factory=create,
    contract=Contract(PublicationSources, PublicationText, {Effect.RESOURCE}),
)
