# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Computer Use agents and tools for openJiuwen DeepAgent."""

from importlib import import_module
from typing import TYPE_CHECKING

from .config import AgentConfig, McpConfig

if TYPE_CHECKING:
    from .agent import ComputerUseAgent, create_agent
    from .toolkit import ComputerUseToolkit

__all__ = ["AgentConfig", "ComputerUseAgent", "ComputerUseToolkit", "McpConfig", "create_agent"]

_MODULES = {
    "ComputerUseAgent": ".agent",
    "create_agent": ".agent",
    "ComputerUseToolkit": ".toolkit",
}


def __getattr__(name):
    if name not in _MODULES:
        raise AttributeError(name)
    value = getattr(import_module(_MODULES[name], __name__), name)
    globals()[name] = value
    return value
