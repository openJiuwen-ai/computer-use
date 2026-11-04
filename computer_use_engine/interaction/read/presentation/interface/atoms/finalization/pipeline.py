# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Finish atomic content decisions after structured disclosure planning."""

from __future__ import annotations

from computer_use.pipeline import Pipeline, Sequence
from computer_use.pipeline.core import Step
from ..model import AtomDocument, AtomFinalizeRequest
from .passes.finalize_native_urls import stage as finalize_native_urls
from .passes.share_index_convention import stage as share_index_convention
from .passes.choose_multiline_content import stage as choose_multiline_content
from .passes.share_field_labels import stage as share_field_labels


class FinalizeAtomContentPipeline(Pipeline[AtomFinalizeRequest, AtomDocument]):
    """Finalize field context, complete values and native details before text generation."""

    def __init__(self) -> None:
        super().__init__(root=self.build())

    def build(self) -> Step[AtomFinalizeRequest, AtomDocument]:
        return Sequence(
            name="FinalizeAtomContentPipeline",
            steps=[share_field_labels(), choose_multiline_content(), finalize_native_urls(), share_index_convention()],
        )
