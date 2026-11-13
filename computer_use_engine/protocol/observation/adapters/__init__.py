# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Decode native query evidence used for exact target and action resolution."""

from .wire import decode_query_facts, validate_driver_payload

__all__ = ["decode_query_facts", "validate_driver_payload"]
