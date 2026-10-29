# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Shared observation envelope formatting; no native collection/input."""

import math

from .protocol import now, token


def stamp(start):
    # Millisecond envelopes round outwards so contained fractional image times
    # cannot appear to complete after the observation that contains them.
    return dict(id=token("observation"), startedAt=start, completedAt=max(start, math.ceil(now())))
