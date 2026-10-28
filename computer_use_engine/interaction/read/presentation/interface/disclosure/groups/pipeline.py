# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Select recoverable content and blocked-window overviews with exact original sources."""

from computer_use.pipeline import Pipeline, Sequence
from computer_use.pipeline.core import Step
from .model import GroupDisclosurePlan, GroupDisclosureRequest
from .passes.classify_groups import stage as classify_groups
from .passes.choose_groups import stage as choose_groups
from .passes.validate_catalogue import stage as validate_catalogue


class GroupDisclosurePipeline(Pipeline[GroupDisclosureRequest, GroupDisclosurePlan]):
    """Assess window blocking, protect active content, and freeze beneficial captured overviews."""

    def __init__(self) -> None:
        super().__init__(root=self.build())

    def build(self) -> Step[GroupDisclosureRequest, GroupDisclosurePlan]:
        return Sequence(
            name="GroupDisclosurePipeline",
            steps=[
                classify_groups(),
                choose_groups(),
                validate_catalogue(),
            ],
        )
