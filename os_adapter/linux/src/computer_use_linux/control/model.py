# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from dataclasses import dataclass


@dataclass(frozen=True)
class NativeInputStep:
    operation: object
    step: int
    command: dict
