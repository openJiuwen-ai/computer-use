# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Structured visual interface plans with native identity and display layout separated."""

from computer_use.interaction.read.presentation.interface.model import (
    InterfaceBlock,
    InterfaceDocument,
    InterfaceRequest,
)
from computer_use.interaction.read.presentation.interface.pipeline import InterfacePresentationPipeline, build_interface

__all__ = [
    "InterfaceBlock",
    "InterfaceDocument",
    "InterfaceRequest",
    "InterfacePresentationPipeline",
    "build_interface",
]
