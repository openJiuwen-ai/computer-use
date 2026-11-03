# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from dataclasses import dataclass
from types import TracebackType
from typing import Literal, Mapping, TypedDict

__all__ = [
    "UI",
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
    "TextPage",
    "View",
    "Window",
    "WindowInfo",
    "__version__",
]

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
    def __init__(self, *, allow_global_input: bool = True) -> None: ...
    def close(self) -> None: ...
    def __enter__(self) -> Computer: ...
    def __exit__(
        self, exc_type: type[BaseException] | None, exc_value: BaseException | None,
        traceback: TracebackType | None
    ) -> None: ...
    def list_apps(self) -> list[AppInfo]: ...
    def app(self, selector: int | str) -> App: ...
    def window(self, ref: int) -> Window: ...
    def read(self, *, view: View = "both", full: bool = False) -> Observation: ...

    # 整桌面鼠标操作；位置来自对应的桌面截图。
    def click(self, options: ClickOption | None = None, *, at: Point) -> ActionResult: ...
    def scroll(self, direction: Direction, *, at: Point, pages: float = 1) -> ActionResult: ...
    def drag(self, start: Point, end: Point) -> ActionResult: ...

    # 向当前桌面接收者输入；桌面坐标操作使用全局输入路线。
    def type_text(self, text: str) -> ActionResult: ...
    def press_key(self, key: str) -> ActionResult: ...

class UI:
    # App 与 Window 的共同接口基类，不供模型直接创建。
    def element(self, target: ElementSelector, *, timeout_ms: int = 3000) -> Element: ...
    def read(
        self, *, view: View = "tree", full: bool = False, menu_path: list[str] | None = None,
        allow_foreground: bool = False
    ) -> Observation: ...

    # 位置来自当前 App／Window 对象交付的截图，可包含相关弹窗。
    def click(self, options: ClickOption | None = None, *, at: Point) -> ActionResult: ...
    def scroll(self, direction: Direction, *, at: Point, pages: float = 1) -> ActionResult: ...
    def drag(self, start: Point, end: Point) -> ActionResult: ...

    # App 使用最近一次明确交付的输入上下文；Window 使用固定窗口。
    # 后端准备该目标的接收链，不因执行瞬间焦点变化改选窗口。
    def type_text(self, text: str) -> ActionResult: ...
    def paste(self, text: str) -> ActionResult: ...
    def press_key(self, key: str) -> ActionResult: ...

class App(UI):
    # App 表示应用范围。窗口编号与 Computer.window() 共用，额外检查应用归属。
    # 选择窗口不激活、不改变 App 范围。
    def window(self, ref: int) -> Window: ...

class Window(UI):
    # 由 Computer.window() 或 App.window() 取得，固定窗口身份。
    # 原窗口关闭后失效，不改投同应用其他窗口；只继承 UI 的共同操作。
    ...

class Element:
    def read(self, *, cursor: str | None = None) -> Observation: ...
    def read_text(self, *, cursor: str | None = None, length: int = 4096) -> TextPage: ...

    # 操作这个控件；不转发给碰巧获得焦点的另一个控件。
    def click(self, options: ClickOption | None = None) -> ActionResult: ...
    def scroll(self, direction: Direction, *, pages: float = 1) -> ActionResult: ...

    # 具体控件准备自身；范围容器按第 4 节转发。
    def type_text(self, text: str, *, replace: bool = False) -> ActionResult: ...
    def paste(self, text: str) -> ActionResult: ...
    def press_key(self, key: str) -> ActionResult: ...

    # 按需使用，普通输入不要求预先调用 focus。
    def focus(self) -> ActionResult: ...
    def open_menu(self) -> ActionResult: ...
    def perform_action(self, action: str) -> ActionResult: ...

# 以下是只读返回数据，不是模型需要创建的操作对象。
class Observation:
    def read_details(
        self, *, element: int | None = None, cursor: str | None = None, length: int = 4096
    ) -> TextPage: ...
    text: str
    screenshot: bytearray | None
    preparation: Mapping[str, object] | None

class AppInfo:
    id: int
    display_name: str
    is_running: bool

class WindowInfo:
    id: int
    title: str

class ActionResult:
    status: Literal["dispatched", "verified"]
    detail: str
    observation: Observation | None
    observation_status: Literal["available", "partial", "unavailable", "not_requested"]
    observation_error: ObservationError | None
    window_lifecycle: Literal["observed", "closed", "unknown", "not_applicable"]
    choices: tuple[WindowInfo, ...]
    impact: ActionImpact | None

class PreparationImpact:
    mouse: Literal["yes", "no", "unknown"]
    keyboard: Literal["yes", "no", "unknown"]
    activation: Literal["yes", "no", "unknown"]
    clipboard: Literal["yes", "no", "unknown"]

class InputEffect:
    stage: Literal["preparation", "input", "cleanup"]
    device: Literal["mouse", "keyboard", "focus", "element", "clipboard", "application"]
    transport: Literal["globalHID", "process", "accessibility", "activation", "windowServer", "clipboard", "launch"]
    mechanism: str
    state: Literal["invoked", "possible", "notInvoked"]

class ActualInputImpact:
    coverage: Literal["complete", "partial"]
    effects: tuple[InputEffect, ...]
    foreground_changed: Literal["yes", "no", "unknown"]
    clipboard_restoration: Literal["notUsed", "restored", "ownershipLost", "failed", "unknown"]
    foreground_restoration: Literal[
        "notUsed", "notNeeded", "restored", "ownershipLost", "retained", "unavailable", "failed", "unknown"
    ]
    omitted_effects: int

class InputAttempt:
    route: str
    declared_global_input: Literal["yes", "no", "unknown"]
    preparation_potential: PreparationImpact | None
    foreground_requested: bool
    preparation_only: bool
    preparation: tuple[str, ...] | None
    dispatch: Literal["notDispatched", "dispatched", "partial", "unknown"]
    cleanup: Literal["complete", "failed", "unknown"]
    actual: ActualInputImpact | None

class ActionImpact:
    global_input_allowed: bool
    attempts: tuple[InputAttempt, ...]

class ObservationError:
    code: str
    detail: str
    next: str
    session_usable: bool

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
    impact: ActionImpact | None
    observation: Observation | None
    observation_status: Literal["available", "partial", "unavailable", "not_requested"]
    observation_error: ObservationError | None
    window_lifecycle: Literal["observed", "closed", "unknown", "not_applicable"]
    choices: tuple[WindowInfo, ...]

__version__: str
