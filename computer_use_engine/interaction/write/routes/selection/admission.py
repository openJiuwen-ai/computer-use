# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Stage admission shared by every write route, before history and host policy.

Native declarations distinguish dispatch readiness from available preparation.
Geometry pending means an unconfirmed coordinate binding. A covered or unfocused
point with valid geometry instead needs receiver/foreground preparation.
"""

from dataclasses import dataclass
from typing import Literal, cast

from computer_use.native.contracts import Channel, Knowledge, Primitive

PreparationMode = Literal["background", "foreground"]


@dataclass(frozen=True)
class StageAdmission:
    """Current execution readiness and preparations offered by the native route."""

    ready: bool
    preparation: tuple[PreparationMode, ...] = ()
    blockers: tuple[str, ...] = ()

    @property
    def available(self) -> bool:
        return self.ready or bool(self.preparation)


def stage_admission(primitive: Primitive) -> StageAdmission:
    """Project a native contract without preparing a target or choosing a route."""
    if primitive.click is not None:
        offer = primitive.click
        if offer.admission == "ready":
            return StageAdmission(True)
        if offer.admission == "preparationRequired":
            return StageAdmission(False, cast("tuple[PreparationMode, ...]", offer.preparation_options))
        return StageAdmission(False, blockers=(offer.reason or offer.admission,))
    if primitive.supported != Knowledge.YES:
        return StageAdmission(False, blockers=("support",))
    blockers = tuple(condition for condition in primitive.pending if condition in ("enabled", "geometry"))
    if blockers:
        return StageAdmission(False, blockers=blockers)
    if primitive.readiness == Knowledge.YES and primitive.unit == "prepare_receiver":
        return StageAdmission(False, ("background",))  # Confirm current context without requiring activation.
    if primitive.readiness == Knowledge.YES and primitive.unit != "prepare_receiver":
        return StageAdmission(True)
    if primitive.channel == Channel.OBJECT and primitive.unit in ("native_text_edit", "native_value_replace"):
        # Addressed text mutations have no receiver-preparation endpoint. Missing
        # selection must be established by a separately declared editor/keyboard
        # route, then refreshed; it does not invent an activation command here.
        return StageAdmission(False, blockers=primitive.pending or ("editing",))
    background = primitive.background_preparation != Knowledge.NO and "foreground" not in primitive.pending
    preparation: tuple[PreparationMode, ...] = ("background", "foreground") if background else ("foreground",)
    return StageAdmission(False, preparation)
