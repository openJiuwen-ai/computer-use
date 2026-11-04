# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Pure desktop-impact ordering for already eligible native commands."""

from collections.abc import Sequence

from computer_use.native.contracts import Channel, UnitCommand


def uses_global_pointer(command: UnitCommand) -> bool:
    """Integrated offers distinguish addressed mouse input from the system pointer.

    Non-click pointer units have no addressed-delivery declaration. Conservatively
    order them after independent object/editor/keyboard preparation; this changes
    no route's admission or ability to dispatch.
    """
    offer = command.primitive.click
    return offer.delivery == "desktop_pointer" if offer is not None else command.primitive.channel == Channel.POINTER


def least_disruptive_group(*groups: Sequence[UnitCommand]) -> list[UnitCommand]:
    """Prefer addressed routes, retaining each caller's readiness/semantic tiers."""
    for global_pointer in (False, True):
        for group in groups:
            choices = [command for command in group if uses_global_pointer(command) == global_pointer]
            if choices:
                return choices
    return []
