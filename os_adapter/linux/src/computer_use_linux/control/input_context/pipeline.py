"""Explicit query-only input declaration stages, using shared native units."""

from computer_use_linux.contracts.protocol import require
from computer_use_linux.control.click.passes.project_click_offers import project_click_offers

from .passes.attach_selected_context import attach_selected_context
from .passes.collect_current_receiver import collect_current_receiver
from .passes.collect_keyboard_contracts import collect_keyboard_contracts
from .passes.collect_pointer_contracts import collect_pointer_contracts
from .passes.collect_unit_contracts import collect_unit_contracts
from .passes.resolve_context_selection import resolve_context_selection
from .passes.select_context_unit import select_context_unit


class NativeInputContextPipeline:
    def __init__(self, units, capabilities, desktop, current_receiver=None):
        self.units, self.capabilities, self.desktop = units, capabilities, desktop
        self.current_receiver = current_receiver

    def run(self, scope, ctx):
        require(scope.operation.writable, "A read operation cannot select an input contract.")
        if scope.request.get("currentReceiver") is True:
            require(
                self.current_receiver is not None, "Current receiver observation is unavailable.", "mode_unsupported"
            )
            return collect_current_receiver(scope, ctx, self.current_receiver)
        unit = select_context_unit(scope, self.units, self.capabilities)
        selected = resolve_context_selection(scope)
        result = collect_unit_contracts(unit, scope, selected, ctx)
        if unit is self.units.text and getattr(self.desktop, "can_input", False):
            options = {} if selected is None else dict(selection=selected)
            result = collect_keyboard_contracts(scope, options, result, ctx, self.units.typed_text)
        matches_selected = (
            True
            if (
                selected is None
                and scope.target["capture"] is None
                and scope.operation.intent["kind"] == "click"
                and "nativeElementPointer" in self.capabilities
            )
            else False
        )
        if matches_selected:
            result = collect_pointer_contracts(scope, result, ctx, self.units.element_pointer)
        result = attach_selected_context(scope.target, selected, result)
        if scope.operation.click_contract == "v1":
            result = project_click_offers(scope.operation, result)
        return result
