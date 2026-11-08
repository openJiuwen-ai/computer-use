# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Computer Use SDK: public Python API and cross-platform desktop semantics."""

from .client.computer import App, Computer, Element
from .client.targets import Target
from .errors import ComputerUseError
from .types import (
    ActionResult,
    ActionImpact,
    ActualInputImpact,
    InputAttempt,
    InputEffect,
    PreparationImpact,
    AppInfo,
    Button,
    ClickOption,
    Direction,
    ElementMatch,
    ElementPath,
    ElementSelector,
    Observation,
    ObservationError,
    Point,
    TextPage,
    View,
    WindowCatalogue,
    WindowChoice,
    WindowInfo,
)

__all__ = [
    "ActionResult",
    "ActionImpact",
    "ActualInputImpact",
    "InputAttempt",
    "InputEffect",
    "PreparationImpact",
    "App",
    "AppInfo",
    "Button",
    "ClickOption",
    "Computer",
    "ComputerUseError",
    "Direction",
    "Element",
    "ElementMatch",
    "ElementPath",
    "ElementSelector",
    "Observation",
    "ObservationError",
    "Point",
    "Target",
    "TextPage",
    "View",
    "WindowCatalogue",
    "WindowChoice",
    "WindowInfo",
    "__version__",
]

__version__ = "0.0.1"
