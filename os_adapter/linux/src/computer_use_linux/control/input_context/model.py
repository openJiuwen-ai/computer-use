# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from dataclasses import dataclass


@dataclass(frozen=True)
class ContextUnits:
    desktop_input: object
    pointer: object
    paste: object
    keyboard: object
    text: object
    typed_text: object
    element_pointer: object
    action: object


@dataclass(frozen=True)
class InputUnits(ContextUnits):
    launch: object
