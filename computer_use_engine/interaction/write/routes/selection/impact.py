# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Pure desktop-impact ordering for already eligible native commands."""

from collections.abc import Sequence

from computer_use.native.contracts import Binding, HostPolicy, Knowledge, UnitCommand


def requires_global_input(command: UnitCommand) -> bool:
    """Known global delivery from exact target/offer evidence, never channel alone.

    A generic keyboard or pointer unit can still be process-directed. Its native
    mechanism checks the permission when resolving the actual delivery.
    """
    if isinstance(command.target, Binding) and command.target.is_desktop:
        return True
    if command.primitive.global_input == Knowledge.YES:
        return True
    offer = command.primitive.click
    return offer is not None and offer.delivery == "desktop_pointer"


def global_input_denied(command: UnitCommand, policy: HostPolicy | None, platform: str | None = None) -> bool:
    # This selector belongs only to object execution. Explicit Computer input
    # has a separate primitive branch, never a privilege on an object candidate.
    scoped_macos = platform == "macos"
    return requires_global_input(command) and (scoped_macos or not (policy is not None and policy.allow_global_input))


def global_input_failure(policy: HostPolicy | None, platform: str):
    if platform == "macos":
        return (
            "input_unavailable",
            (
                "This macOS object operation has no remaining non-global input route. "
                "Use an explicit Computer input method if global input is intended. "
                "Check recorded input progress before another action."
            ),
        )
    return "permission_required", "The available route requires global input, which this session has not authorized."


def input_impact_rank(command: UnitCommand) -> int:
    """Prefer proven non-global routes before unresolved and known global input.

    The middle rank is uncertainty, not a claim of tier-one or tier-two impact.
    Native qualification and final authorization still apply to that candidate.
    Unit or channel names alone do not establish its actual transport.
    """
    if requires_global_input(command):
        return 2
    if command.primitive.global_input == Knowledge.NO:
        return 0
    offer = command.primitive.click
    return 0 if offer is not None else 1


def least_disruptive_group(*groups: Sequence[UnitCommand]) -> list[UnitCommand]:
    """Prefer evidenced low impact, retaining each caller's semantic tiers."""
    for impact in (0, 1, 2):
        for group in groups:
            choices = [command for command in group if input_impact_rank(command) == impact]
            if choices:
                return choices
    return []
