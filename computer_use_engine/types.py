"""Small public values; native identities and wire options stay private."""

from dataclasses import dataclass
from typing import Literal, Protocol, TypeAlias, TypedDict

Point: TypeAlias = tuple[float, float]
View: TypeAlias = Literal["tree", "image", "both"]
Direction: TypeAlias = Literal["up", "down", "left", "right"]
Button: TypeAlias = Literal["left", "right", "middle"]


@dataclass(frozen=True, kw_only=True)
class ClickOption:
    button: Button = "left"
    count: Literal[1, 2] = 1


class ElementMatch(TypedDict, total=False):
    role: str
    name: str
    identifier: str


ElementPath: TypeAlias = list[str | ElementMatch]
ElementSelector: TypeAlias = int | str | ElementPath


class Observation(Protocol):
    @property
    def text(self) -> str: ...
    @property
    def screenshot(self) -> bytearray | None: ...


class AppInfo(Protocol):
    @property
    def id(self) -> int: ...
    @property
    def displayName(self) -> str: ...
    @property
    def isRunning(self) -> bool: ...


class ActionResult(Protocol):
    @property
    def status(self) -> Literal["dispatched", "verified"]: ...
    @property
    def detail(self) -> str: ...


class TextPage(Protocol):
    @property
    def text(self) -> str: ...
    @property
    def total(self) -> int: ...
    @property
    def next(self) -> str | None: ...
