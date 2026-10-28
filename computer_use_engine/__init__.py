"""Computer Use SDK: public Python API and cross-platform desktop semantics."""

from .client.computer import UI, App, Computer, Element, Window
from .errors import ComputerUseError
from .types import (
    ActionResult,
    AppInfo,
    Button,
    ClickOption,
    Direction,
    ElementMatch,
    ElementPath,
    ElementSelector,
    Observation,
    Point,
    TextPage,
    View,
)

__all__ = [
    "UI",
    "ActionResult",
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
    "Point",
    "TextPage",
    "View",
    "Window",
    "__version__",
]

__version__ = "0.0.1"
