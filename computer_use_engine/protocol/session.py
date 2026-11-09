# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Session negotiation and structured failures for the native protocol."""

from ..errors import ComputerUseError, public_error_code
from .action_context import decode_action_context
from .primitives import finite, nonempty, record, safe_integer

NATIVE_PROTOCOL_VERSION = 6  # Public Computer and host setup use the same protocol.


class NativeRequestDeadline(ComputerUseError):
    """The native driver acknowledged expiry of this request."""


def remember_failure_context(error, context=None):
    if context is not None:
        # The pipeline shares retained bindings with their owning service.
        # pylint: disable-next=protected-access
        error._failure_context = context


def failure_context(error):
    return getattr(error, "_failure_context", None) if isinstance(error, BaseException) else None


def decode_failure(value):
    has_context = isinstance(value, dict) and "context" in value
    error = record(value, ["code", "target", "effect", "detail", "next", *(["context"] if has_context else [])])
    for key in ("code", "target", "detail", "next"):
        nonempty(error[key], "protocol error " + key)
    if error["effect"] not in ("none", "possible", "partial"):
        raise ValueError("protocol: invalid error effect")
    if has_context:
        error["context"] = decode_action_context(error["context"])
    return error


def failure_error(method, failure):
    error_type = NativeRequestDeadline if failure["code"] == "deadline_exceeded" else ComputerUseError
    error = error_type({**failure, "code": public_error_code(failure["code"])}, method=method)
    remember_failure_context(error, failure.get("context"))
    return error


def decode_hello(value, allow_global_input, version=NATIVE_PROTOCOL_VERSION):
    if allow_global_input is not True and allow_global_input is not False:
        raise TypeError("allow_global_input must be a bool")
    if version != NATIVE_PROTOCOL_VERSION:
        raise ValueError("Unsupported native protocol version")
    error_reply = isinstance(value, dict) and "error" in value
    reply = record(value, ["version", "session", "id", "error" if error_reply else "result"])
    invalid_version = (
        True
        if not finite(reply["version"]) or reply["version"] != version or not finite(reply["id"]) or reply["id"] != 0
        else False
    )
    if invalid_version:
        raise ValueError("protocol: incompatible hello version or id")
    nonempty(reply["session"], "protocol session")
    if error_reply:
        raise failure_error("hello", decode_failure(reply["error"]))
    result = record(reply["result"], ["platform", "globalInputAllowed", "maxTimeoutMs", "capabilities"])
    nonempty(result["platform"], "protocol platform")
    if result["globalInputAllowed"] is not allow_global_input:
        raise ValueError("protocol: backend global input permission does not match the requested permission")
    if not safe_integer(result["maxTimeoutMs"]) or not 1 <= result["maxTimeoutMs"] <= 120_000:
        raise ValueError("protocol: invalid maximum request timeout")
    capabilities = result["capabilities"]
    if (
        not isinstance(capabilities, list)
        or any(not isinstance(v, str) or not v for v in capabilities)
        or len(set(capabilities)) != len(capabilities)
    ):
        raise ValueError("protocol: invalid capabilities")
    required = ("cancel", "requestDedup", "nativeOperations")
    if "nativeBoundContracts" in capabilities:
        required += ("desktopCoordination",)
    for capability in required:
        if capability not in capabilities:
            raise ValueError(f"protocol: backend is missing {capability}")
    return dict(
        session=reply["session"],
        platform=result["platform"],
        globalInputAllowed=allow_global_input,
        maxTimeoutMs=result["maxTimeoutMs"],
        capabilities=tuple(capabilities),
    )
