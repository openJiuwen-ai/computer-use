# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Small public values; native identities and wire options stay private."""

from dataclasses import dataclass
from typing import Literal, Mapping, Protocol, TypeAlias, TypedDict

Point: TypeAlias = tuple[float, float]
View: TypeAlias = Literal["tree", "image", "both"]
Direction: TypeAlias = Literal["up", "down", "left", "right"]
Button: TypeAlias = Literal["left", "right", "middle"]
ImpactKnowledge: TypeAlias = Literal["yes", "no", "unknown"]


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
    def preparation(self) -> Mapping[str, object] | None:
        """Explicit foreground preparation evidence; None for an ordinary read."""
        pass

    def read_details(
        self, *, element: int | None = None, cursor: str | None = None, length: int = 4096
    ) -> "TextPage":
        ...

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


class WindowChoice(Protocol):
    """A visible window belonging to one exact application instance."""

    app_id: int
    window_ref: int
    title: str | None
    app_is_foreground: bool | None


class WindowCatalogue(Protocol):
    """Visible matches and any limits encountered while collecting metadata."""

    windows: tuple[WindowChoice, ...]
    stop_reasons: tuple[str, ...]


class WindowInfo(Protocol):
    """A specific observed window, selectable with App methods' window=id parameter."""

    id: int
    title: str


class ActionResult(Protocol):
    """Execution receipt and its independent, bounded subsequent observation."""

    target: str
    intent: str | None
    observation: Observation | None
    observation_status: Literal["available", "partial", "unavailable", "not_requested"]
    observation_error: "ObservationError | None"
    window_lifecycle: Literal["observed", "closed", "unknown", "not_applicable"]
    choices: tuple[WindowInfo, ...]
    impact: "ActionImpact | None"

    @property
    def status(self) -> Literal["dispatched", "verified"]:
        pass

    @property
    def detail(self) -> str:
        pass


class ObservationError(Protocol):
    code: str
    detail: str
    session_usable: bool


class PreparationImpact(Protocol):
    """Potential recipe effects: yes means may use; absent evidence is unknown."""

    mouse: ImpactKnowledge
    keyboard: ImpactKnowledge
    activation: ImpactKnowledge
    clipboard: ImpactKnowledge


class InputEffect(Protocol):
    """Grouped native invocation evidence, not proof the application consumed it."""

    stage: Literal["preparation", "input", "cleanup"]
    device: Literal["mouse", "keyboard", "focus", "element", "clipboard", "application"]
    transport: Literal["globalHID", "process", "accessibility", "activation", "windowServer", "clipboard", "launch"]
    mechanism: str
    state: Literal["invoked", "possible", "notInvoked"]


class ActualInputImpact(Protocol):
    """SDK invocation evidence in one step, not external or later UI consequences.

    Partial coverage means absence from effects does not prove no impact.
    """

    coverage: Literal["complete", "partial"]
    effects: tuple[InputEffect, ...]
    foreground_changed: ImpactKnowledge
    clipboard_restoration: Literal["notUsed", "restored", "ownershipLost", "failed", "unknown"]
    # unavailable includes platforms which do not report this mechanism yet.
    foreground_restoration: Literal[
        "notUsed", "notNeeded", "restored", "ownershipLost", "retained", "unavailable", "failed", "unknown"
    ]
    omitted_effects: int


class InputAttempt(Protocol):
    route: str
    declared_global_input: ImpactKnowledge
    preparation_potential: PreparationImpact | None
    foreground_requested: bool
    preparation_only: bool
    preparation: tuple[str, ...] | None
    dispatch: Literal["notDispatched", "dispatched", "partial", "unknown"]
    cleanup: Literal["complete", "failed", "unknown"]
    actual: ActualInputImpact | None


class ActionImpact(Protocol):
    global_input_allowed: bool
    attempts: tuple[InputAttempt, ...]


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
