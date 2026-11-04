# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Structured exact-text references with unchanged independent source targets."""

from .model import TextSliceDocument, TextSlicePlan, TextSliceRequest
from .pipeline import TextSlicePipeline

__all__ = ["TextSliceDocument", "TextSlicePlan", "TextSliceRequest", "TextSlicePipeline"]
