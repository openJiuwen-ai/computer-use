from datetime import datetime, timezone

from computer_use.errors import ComputerUseError

sdkVersion = "0.0.1"
_handler = None
_structured = False


def setDiagnosticHandler(handler):
    global _handler
    _handler = handler


def recordStructuredObservations(enabled):
    global _structured
    _structured = enabled


def wantsStructuredObservations():
    return _structured


def diagnostic(event):
    try:
        if _handler:
            _handler(
                {
                    "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
                    **event,
                }
            )
    except Exception:
        pass  # Diagnostic failure must not change or replay desktop actions.


def pipelineDiagnostic(event):
    """Serialize only the runner's small control fields, never IR/body/pixels."""
    diagnostic(
        dict(
            kind="pipeline",
            callId=event.call_id,
            path=list(event.path),
            phase=event.phase,
            effects=sorted(effect.value for effect in event.effects),
            attempt=event.attempt,
            branch=event.branch,
            cleanup=event.cleanup,
            dispatch=event.dispatch,
            errorType=event.error_type,
        )
    )


def localFailure(message, next, code="invalid_request", target="request"):
    failure = dict(code=code, target=target, effect="none", detail=message, next=next)
    error = ComputerUseError(failure)
    diagnostic(dict(kind="local_error", stage="validation", error=str(error), failure=failure))
    return error
