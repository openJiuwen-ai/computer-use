# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from datetime import datetime, timezone

from computer_use.errors import ComputerUseError

SDK_VERSION = "0.0.1"
# Private variables hold mutable session state.
# pylint: disable-next=invalid-name
_handler = None
# pylint: disable-next=invalid-name
_structured = False


def set_diagnostic_handler(handler):
    global _handler
    _handler = handler


def record_structured_observations(enabled):
    global _structured
    _structured = enabled


def wants_structured_observations():
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


def pipeline_diagnostic(event):
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


# The error contract exposes a next field and accepts the matching keyword.
# pylint: disable-next=redefined-builtin
def local_failure(message, next, code="invalid_request", target="request"):
    failure = dict(code=code, target=target, effect="none", detail=message, next=next)
    error = ComputerUseError(failure)
    diagnostic(dict(kind="local_error", stage="validation", error=str(error), failure=failure))
    return error
