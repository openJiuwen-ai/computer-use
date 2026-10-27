from computer_use.diagnostics import (
    record_structured_observations as record_structured_observations,
)
from computer_use.diagnostics import (
    set_diagnostic_handler as set_diagnostic_handler,
)
from computer_use.native.preview import set_preview_handler as set_preview_handler
from computer_use.session.events import (
    set_action_handler as set_action_handler,
)
from computer_use.session.events import (
    set_observation_handler as set_observation_handler,
)
from computer_use.session.operations import set_execution_guard as set_execution_guard
from computer_use.session.results import set_text_page_handler as set_text_page_handler

from .handles import set_handle_handler as set_handle_handler


def computer_host(factory=None):
    """Own one fresh public Computer and its session lifecycle for a host task."""
    from .._identity import Record

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
