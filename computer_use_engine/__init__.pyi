# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from dataclasses import dataclass
from types import TracebackType
from typing import Literal, Mapping, TypedDict, overload

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
    # Canonical role words: 'menuItem' and 'menu item' select the same role.
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
        self, exc_type: type[BaseException] | None, exc_value: BaseException | None, traceback: TracebackType | None
    ) -> None: ...
    def list_apps(self) -> list[AppInfo]: ...
    def list_windows(self, *, app_name: str) -> WindowCatalogue: ...
    @overload
    def app(self, selector: int | str) -> App: ...
    @overload
    def app(self, *, window_title: str) -> App: ...
    def read(self, *, view: View = "both", full: bool = False) -> Observation: ...

    # 所有截图取点均使用显示图片像素，左上角为原点，无需换算：
    # p = computer.point((x, y)); computer.click(target=p)。App 图用 app.point()。
    # macOS 裸 at 元组为设备级系统屏幕点，不是截图输入；该入口不要求先读取。
    def point(self, at: Point) -> Target: ...
    def click(
        self,
        options: ClickOption | None = None,
        *,
        at: Point | Target | None = None,
        target: Element | Target | None = None,
    ) -> ActionResult: ...
    def scroll(
        self,
        direction: Direction,
        *,
        at: Point | Target | None = None,
        target: Element | Target | None = None,
        pages: float = 1,
    ) -> ActionResult: ...
    def drag(self, start: Point | Element | Target, end: Point | Element | Target) -> ActionResult: ...

    # macOS：向系统当前接收者输入，不选元素、不隐式 focus/replace，也不强制写后读取。
    def type_text(self, text: str) -> ActionResult: ...
    def press_key(self, key: str) -> ActionResult: ...
    # Computer.paste 支持 macOS/Windows；Linux 使用 App/Element.paste。
    def paste(self, text: str) -> ActionResult: ...

class Target:
    # 不可变定位值，由 Element.target 或 Computer/App.point() 取得。
    # 输入通道由调用方法决定，窗口坐标始终绑定原截图。
    ...

class App:
    # (x, y) 是图上看到的像素；SDK 负责缩放和窗口偏移，随后 app.click(at=p)。
    def point(self, at: Point, *, window: int | None = None) -> Target: ...
    # 正常请求关闭指定窗口，不自动确认保存、丢弃或关闭其他窗口。
    def close_window(self, window: int) -> ActionResult: ...
    # 应用范围；window= 可选地限定本次调用，同一应用共用元素编号。
    def element(
        self, target: ElementSelector, *, timeout_ms: int = 3000, window: int | None = None, fresh: bool = False
    ) -> Element: ...
    def read(
        self, *, view: View = "tree", full: bool = False, menu_path: list[str] | None = None,
        allow_foreground: bool = False, window: int | None = None
    ) -> Observation: ...

    # 位置来自指定窗口最近交付的截图，或 App 最近交付的截图，可包含相关弹窗。
    def click(
        self, options: ClickOption | None = None, *, at: Point | Target,
        window: int | None = None, force_pointer: bool = False,
    ) -> ActionResult: ...
    def scroll(self, direction: Direction, *, at: Point | Target, pages: float = 1, window: int | None = None) -> ActionResult: ...
    def drag(self, start: Point | Target, end: Point | Target, *, window: int | None = None) -> ActionResult: ...

    # 省略 window 使用最近明确交付的输入上下文；提供 window 则固定接收窗口。
    # 后端准备该目标的接收链，不因执行瞬间焦点变化改选窗口。
    def type_text(self, text: str, *, window: int | None = None) -> ActionResult: ...
    def paste(self, text: str, *, window: int | None = None) -> ActionResult: ...
    def press_key(self, key: str, *, window: int | None = None) -> ActionResult: ...

class Element:
    @property
    def target(self) -> Target: ...
    def read(self, *, cursor: str | None = None) -> Observation: ...
    def read_text(self, *, cursor: str | None = None, length: int = 4096) -> TextPage: ...

    # 操作这个控件；不转发给碰巧获得焦点的另一个控件。
    def click(self, options: ClickOption | None = None) -> ActionResult: ...
    def scroll(self, direction: Direction, *, pages: float = 1) -> ActionResult: ...

    # SDK 准备具体控件；replace=True 表达全文替换。
    def type_text(self, text: str, *, replace: bool = False) -> ActionResult: ...
    def paste(self, text: str) -> ActionResult: ...
    def press_key(self, key: str) -> ActionResult: ...

    # SDK 自动完成普通输入所需的焦点准备。
    def focus(self) -> ActionResult: ...
    def open_menu(self) -> ActionResult: ...
    def perform_action(self, action: str) -> ActionResult: ...

# 以下是只读返回数据，不是模型需要创建的操作对象。
class Observation:
    # 原始 native/AX 详情的几何保留其声明的 screen/AX 坐标系，不等同截图像素。
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

class WindowChoice:
    app_id: int
    window_ref: int
    title: str | None
    app_is_foreground: bool | None

class WindowCatalogue:
    windows: tuple[WindowChoice, ...]
    stop_reasons: tuple[str, ...]

class WindowInfo:
    id: int
    title: str

class _ActionReceipt:
    target: str
    intent: str | None
    status: Literal["dispatched", "verified"]
    detail: str
    impact: ActionImpact | None

class ActionResult(_ActionReceipt):
    observation: Observation | None
    observation_status: Literal["available", "partial", "unavailable", "not_requested"]
    observation_error: ObservationError | None
    window_lifecycle: Literal["observed", "closed", "unknown", "not_applicable"]
    choices: tuple[WindowInfo, ...]

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
    impact: ActionImpact | None
    action_receipt: _ActionReceipt | None
    scope_cleanup: Literal["unknown"] | None
    observation: Observation | None
    observation_status: Literal["available", "partial", "unavailable", "not_requested"]
    observation_error: ObservationError | None
    window_lifecycle: Literal["observed", "closed", "unknown", "not_applicable"]
    choices: tuple[WindowInfo, ...]

__version__: str
