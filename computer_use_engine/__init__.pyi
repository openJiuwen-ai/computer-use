from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, TypedDict

Point = tuple[float, float]
View = Literal["tree", "image", "both"]
Direction = Literal["up", "down", "left", "right"]
Button = Literal["left", "right", "middle"]

@dataclass(frozen=True, kw_only=True)
class ClickOption:
    button: Button = "left"
    count: Literal[1, 2] = 1

class ElementMatch(TypedDict, total=False):
    role: str
    name: str
    identifier: str

ElementPath = list[str | ElementMatch]
ElementSelector = int | str | ElementPath

class Computer:
    def __init__(self) -> None: ...
    async def listApps(self) -> list[AppInfo]: ...
    def app(self, selector: int | str) -> App: ...
    async def read(self, *, view: View = "both", full: bool = False) -> Observation: ...

    # 整桌面鼠标操作；位置来自对应的桌面截图。
    async def click(self, options: ClickOption | None = None, *, at: Point) -> ActionResult: ...
    async def scroll(self, direction: Direction, *, at: Point, pages: float = 1) -> ActionResult: ...
    async def drag(self, start: Point, end: Point) -> ActionResult: ...

    # 向当前桌面接收者输入；所有 Computer 写操作要求 exclusive。
    async def typeText(self, text: str) -> ActionResult: ...
    async def pressKey(self, key: str) -> ActionResult: ...

class UI:
    # App 与 Window 的共同接口基类，不供模型直接创建。
    async def element(self, target: ElementSelector, *, timeoutMs: int = 3000) -> Element: ...
    async def read(
        self, *, view: View = "tree", full: bool = False, menuPath: list[str] | None = None
    ) -> Observation: ...

    # 位置来自当前 App／Window 对象交付的截图，可包含相关弹窗。
    async def click(self, options: ClickOption | None = None, *, at: Point) -> ActionResult: ...
    async def scroll(self, direction: Direction, *, at: Point, pages: float = 1) -> ActionResult: ...
    async def drag(self, start: Point, end: Point) -> ActionResult: ...

    # 向这个 App／Window 范围内已确认的当前接收者输入。
    async def typeText(self, text: str) -> ActionResult: ...
    async def paste(self, text: str) -> ActionResult: ...
    async def pressKey(self, key: str) -> ActionResult: ...

class App(UI):
    # 从实例已观察的成员目录选择具体窗口，不激活、不改变 App 范围。
    def window(self, ref: int) -> Window: ...

class Window(UI):
    # 由 App.window() 取得，固定窗口身份；只继承 UI 的共同操作。
    ...

class Element:
    async def read(self, *, cursor: str | None = None) -> Observation: ...
    async def readText(self, *, cursor: str | None = None, length: int = 4096) -> TextPage: ...

    # 操作这个控件；不转发给碰巧获得焦点的另一个控件。
    async def click(self, options: ClickOption | None = None) -> ActionResult: ...
    async def scroll(self, direction: Direction, *, pages: float = 1) -> ActionResult: ...

    # 具体控件准备自身；范围容器按第 4 节转发。
    async def typeText(self, text: str, *, replace: bool = False) -> ActionResult: ...
    async def paste(self, text: str) -> ActionResult: ...
    async def pressKey(self, key: str) -> ActionResult: ...

    # 按需使用，普通输入不要求预先调用 focus。
    async def focus(self) -> ActionResult: ...
    async def openMenu(self) -> ActionResult: ...
    async def performAction(self, action: str) -> ActionResult: ...

# 以下是只读返回数据，不是模型需要创建的操作对象。
class Observation:
    text: str
    screenshot: bytearray | None

class AppInfo:
    id: int
    display_name: str
    is_running: bool

class ActionResult:
    status: Literal["dispatched", "verified"]
    detail: str

class TextPage:
    text: str
    total: int
    next: str | None

class ComputerUseError(Exception):
    code: str
    effect: Literal["none", "possible", "partial"]
    target: str
    detail: str
    next: str

__version__: str
