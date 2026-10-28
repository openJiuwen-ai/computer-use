from computer_use.diagnostics import record_structured_observations, set_diagnostic_handler
from computer_use.native.preview import set_preview_handler
from computer_use.session.events import set_action_handler, set_observation_handler
from computer_use.session.operations import set_execution_guard
from computer_use.session.results import set_text_page_handler

from .handles import set_handle_handler

__all__ = [
    "computer_host",
    "record_structured_observations",
    "set_action_handler",
    "set_diagnostic_handler",
    "set_execution_guard",
    "set_handle_handler",
    "set_observation_handler",
    "set_preview_handler",
    "set_text_page_handler",
]


def computer_host(factory=None):
    """Own one fresh public Computer and its session lifecycle for a host task."""
    from ..identity import Record

    if factory is None:
        from .. import Computer

        factory = Computer
    computer = factory()

    async def close():
        # The pipeline shares retained bindings with their owning service.
        # pylint: disable-next=protected-access
        transport = computer._controller.transport
        if hasattr(transport, "close"):
            transport.close()
        if hasattr(transport, "waitClosed"):
            await transport.wait_closed()

    return Record(computer=computer, close=close)
