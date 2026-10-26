"""Computer Use SDK: public Python API and cross-platform desktop semantics."""

from .client.computer import UI as UI, App as App, Computer as Computer, Element as Element, Window as Window
from .errors import ComputerUseError as ComputerUseError
from .types import (
    ActionResult as ActionResult,
    AppInfo as AppInfo,
    Button as Button,
    ClickOption as ClickOption,
    Direction as Direction,
    ElementMatch as ElementMatch,
    ElementPath as ElementPath,
    ElementSelector as ElementSelector,
    Observation as Observation,
    Point as Point,
    TextPage as TextPage,
    View as View,
)

__version__ = "0.0.1"
