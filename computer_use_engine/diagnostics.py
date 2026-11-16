# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from datetime import UTC, datetime
from sys import _getframe, getrecursionlimit, implementation, version_info

from computer_use.errors import ComputerUseError

SDK_VERSION = "0.0.1"
# Private variables hold mutable session state.
# pylint: disable-next=invalid-name
_handler = None
_observation_diagnostic = None
_pipeline_transport = None
# The shortcut skips ordinary Python and C serializer frames. Near either
# recursion boundary let the original path decide whether to emit the trace.
_PIPELINE_FRAME_MARGIN = 96
_pipeline_stack_probe = (str,)
for _level in range(39):
    _pipeline_stack_probe = (_pipeline_stack_probe,)
_PIPELINE_STACK_SUPPORTED = implementation.name == "cpython" and version_info[:2] >= (3, 11)
# pylint: disable-next=invalid-name
_structured = False


def set_diagnostic_handler(handler):
    global _handler, _observation_diagnostic, _pipeline_transport
    _handler = handler
    _observation_diagnostic = None
    _pipeline_transport = None


def _register_pipeline_transport(handler, send):
    """Bind the standard worker's primitive trace sink to its active callback."""
    global _pipeline_transport
    if _PIPELINE_STACK_SUPPORTED:
        _pipeline_transport = (handler, send)


def _register_observation_diagnostic(handler, encoded_sink):
    """Bind the worker's private encoded sink to its active diagnostic callback."""
    global _observation_diagnostic
    _observation_diagnostic = (handler, encoded_sink)


def record_structured_observations(enabled):
    global _structured
    _structured = enabled


def wants_structured_observations():
    return _structured


def _pipeline_timestamp():
    """Capture the worker's original diagnostic timestamp."""
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _pipeline_stack_has_room():
    try:
        _getframe(max(0, getrecursionlimit() - _PIPELINE_FRAME_MARGIN))
    except ValueError:
        isinstance(0, _pipeline_stack_probe)
        return True
    return False


def diagnostic(event, *, _native_observation=None):
    try:
        handler = _handler
        if handler:
            record = {
                "timestamp": _pipeline_timestamp(),
                **event,
            }
            if _native_observation is not None:
                encoded = _observation_diagnostic
                if encoded is not None and encoded[0] is handler:
                    encoded[1](record, _native_observation.diagnostic_bytes())
                    return
                record["result"] = _native_observation.raw_result()
            handler(record)
    except Exception:
        pass  # Diagnostic failure must not change or replay desktop actions.


def pipeline_diagnostic(event):
    """Serialize only the runner's small control fields, never IR/body/pixels."""
    binding = _pipeline_transport
    if binding is not None and binding[0] is _handler:
        try:
            has_room = _pipeline_stack_has_room()
        except Exception:
            # Stack inspection is optional (for example, an audit hook may
            # decline frame access). Let the original diagnostic path run.
            pass
        else:
            if has_room:
                try:
                    use_regular = not binding[1](event)
                except Exception:
                    # The writer may already have enqueued the trace.
                    use_regular = False
                if not use_regular:
                    return
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


def local_failure(message, code="invalid_request", target="request"):
    failure = dict(code=code, target=target, effect="none", detail=message)
    error = ComputerUseError(failure)
    diagnostic(dict(kind="local_error", stage="validation", error=str(error), failure=failure))
    return error
