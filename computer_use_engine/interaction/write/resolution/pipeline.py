"""Compose target, receiver, focus, pointer, scrolling, and menu resolution."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.write.resolution.passes.direct_target import stage as direct_target
from computer_use.interaction.write.resolution.passes.enrich_focus import stage as enrich_focus
from computer_use.interaction.write.resolution.passes.ensure_facts import stage as ensure_facts
from computer_use.interaction.write.resolution.passes.open_frame import stage as open_frame
from computer_use.interaction.write.resolution.passes.refine_pointer import stage as refine_pointer
from computer_use.interaction.write.resolution.passes.refine_scroll import stage as refine_scroll
from computer_use.interaction.write.resolution.passes.resolve_menu import stage as resolve_menu
from computer_use.interaction.write.resolution.passes.resolve_replacement import stage as resolve_replacement
from computer_use.interaction.write.resolution.passes.resolve_scroll import stage as resolve_scroll
from computer_use.interaction.write.resolution.passes.resolve_text import stage as resolve_text
from computer_use.interaction.write.resolution.passes.revalidate_pointer import stage as revalidate_pointer
from computer_use.interaction.write.resolution.passes.seal import stage as seal
from computer_use.interaction.write.resolution.passes.select_initial import stage as select_initial
from computer_use.interaction.write.resolution.passes.validate_current_receiver import (
    stage as validate_current_receiver,
)
from computer_use.pipeline import Pipeline, Select, Sequence

if TYPE_CHECKING:
    from computer_use.interaction.write.model import ResolutionInput, ResolvedWrite
    from computer_use.native.port import NativePort
    from computer_use.pipeline.core import Step


class TargetResolutionPipeline(Pipeline["ResolutionInput", "ResolvedWrite"]):
    """Resolve a write's actuator, receiver, and input constraints.

    Select the initial resolver from the requested intent, then enrich its native
    facts with applicable focus, pointer, scrolling, and menu relations. Return the
    resolved target with the evidence retained for route selection and later attempts.

    Attributes:
        native: Driver port queried for capabilities, target facts, and relations.
    """

    def __init__(self, native: NativePort) -> None:
        self.native = native
        super().__init__(root=self.build())

    def build(self) -> Step[ResolutionInput, ResolvedWrite]:
        initial = Select(
            name="InitialResolver",
            selector=select_initial(native=self.native),
            branches={
                "direct": direct_target(),
                "pointer": revalidate_pointer(native=self.native),
                "scroll": resolve_scroll(native=self.native),
                "replace": resolve_replacement(native=self.native),
                "text": resolve_text(native=self.native),
            },
        )
        return Sequence(
            name="TargetResolutionPipeline",
            steps=[
                open_frame(),
                initial,
                ensure_facts(native=self.native),
                enrich_focus(native=self.native),
                validate_current_receiver(),
                refine_pointer(native=self.native),
                refine_scroll(native=self.native),
                resolve_menu(native=self.native),
                seal(),
            ],
        )
