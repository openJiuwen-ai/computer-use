# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Validate structural table plans independently of their eventual text spelling."""

from __future__ import annotations

from computer_use.interaction.read.presentation.interface.collections.model import CollectionDocument
from computer_use.interaction.read.presentation.interface.collections.validation import validate_collections
from computer_use.pipeline import Contract, Effect, Pass
from computer_use.pipeline.core import Control


def execute(document: CollectionDocument, control: Control) -> CollectionDocument:
    """Check exact member uniqueness and logical extents before handing plans to rendering."""
    validate_collections(document)
    return document


stage = Pass.define(
    name="validate_table_layouts",
    function=execute,
    contract=Contract(
        CollectionDocument,
        CollectionDocument,
        {Effect.PURE},
        requires=("complete frozen table plans",),
        writes=(),
        failures=("presentation_contract",),
    ),
)
