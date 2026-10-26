from computer_use.diagnostics import (
    recordStructuredObservations as recordStructuredObservations,
    setDiagnosticHandler as setDiagnosticHandler,
)
from computer_use.native.preview import setPreviewHandler as setPreviewHandler
from computer_use.session.events import (
    setActionHandler as setActionHandler,
    setObservationHandler as setObservationHandler,
)
from computer_use.session.operations import setExecutionGuard as setExecutionGuard
from computer_use.session.results import setTextPageHandler as setTextPageHandler

from .handles import setHandleHandler as setHandleHandler


def computerHost(factory=None):
    """Own one fresh public Computer and its session lifecycle for a host task."""
    from .._identity import Record

    if factory is None:
        from .. import Computer

        factory = Computer
    computer = factory()

    async def close():
        transport = computer._controller.transport
        if hasattr(transport, "close"):
            transport.close()
        if hasattr(transport, "waitClosed"):
            await transport.waitClosed()

    return Record(computer=computer, close=close)
