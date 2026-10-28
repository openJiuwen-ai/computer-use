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
    def text(self) -> str:
        pass

    @property
    def screenshot(self) -> bytearray | None:
        pass


class AppInfo(Protocol):
    """Discovered application with a public ID, display name, and running state."""

    @property
    def id(self) -> int:
        pass

    @property
    def display_name(self) -> str:
        pass

    @property
    def is_running(self) -> bool:
        pass


class ActionResult(Protocol):
    @property
    def status(self) -> Literal["dispatched", "verified"]:
        pass

    @property
    def detail(self) -> str:
        pass


class TextPage(Protocol):
    @property
    def text(self) -> str:
        pass

    @property
    def total(self) -> int:
        pass

    @property
    def next(self) -> str | None:
        pass
