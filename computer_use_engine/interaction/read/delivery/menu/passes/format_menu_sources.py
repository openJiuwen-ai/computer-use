# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.read.delivery.menu.model import PURE, RenderedMenuSources
from computer_use.interaction.read.presentation.content import join, template
from computer_use.interaction.read.presentation.document import DisplayDocument
from computer_use.pipeline import Contract, Pass

"Format menu sources."


if TYPE_CHECKING:
    from computer_use.pipeline.core import Control


def format_menu_sources(value: RenderedMenuSources, control: Control) -> DisplayDocument:
    """Format the ordered menu-source presentations and discovery gaps.

    Return a single complete source directly; otherwise identify each native source and its
    surface, then append notes describing incomplete discovery.
    """
    menu = value.request.menu
    if len(menu.parts) == 1 and not menu.discovery_gaps:
        return value.documents[0]
    lines = [
        template(
            ("Menu sources: {} observed. Native elements remain separate; use an observed element number for actions."),
            len(menu.parts),
        )
    ]
    if menu.discovery_gaps:
        lines.append(
            template(
                (
                    "Menu discovery incomplete: {} Other sources may exist; read the working "
                    "window or an observed element to "
                    "continue."
                ),
                join(menu.discovery_gaps, separator=" "),
            )
        )
    for index, document in enumerate(value.documents, 1):
        lines.extend((template("Menu source {}:", index), document))
    return DisplayDocument.join(lines)


stage = Pass.define(
    name="format_menu_sources",
    function=format_menu_sources,
    contract=Contract(RenderedMenuSources, DisplayDocument, PURE),
)
