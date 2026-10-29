# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from dataclasses import dataclass


@dataclass(frozen=True)
class NativeQueryScope:
    operation: object
    operation_token: str
    target: dict
    request: dict
    kind: str
    launch: bool
    directory: bool
