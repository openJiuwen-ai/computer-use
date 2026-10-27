"""Frozen protocol-3 codecs for historical fixtures; never packaged."""

from computer_use.protocol.action_context import decodeActionContext
from computer_use.protocol.captures import captureMetadata, decodeCapture, decodeStamp
from computer_use.protocol.desktop import decodeDesktopObservation
from computer_use.protocol.png import pngSize
from computer_use.protocol.presentation import decodeObservation
from computer_use.protocol.primitives import finite, frozen, nonempty, record, reference, safeInteger
from computer_use.protocol.session import decodeFailure, failureError
from computer_use.protocol.text import decodeTextPage

protocolVersion = 3


def decodeHello(value, mode, version=protocolVersion):
    error_reply = isinstance(value, dict) and "error" in value
    reply = record(value, ["version", "session", "id", "error" if error_reply else "result"])
    if not finite(reply["version"]) or reply["version"] != version or not finite(reply["id"]) or reply["id"] != 0:
        raise ValueError("protocol: incompatible hello version or id")
    nonempty(reply["session"], "protocol session")
    if error_reply:
        raise failureError("hello", decodeFailure(reply["error"]))
    result = record(reply["result"], ["platform", "desktopMode", "maxTimeoutMs", "capabilities"])
    nonempty(result["platform"], "protocol platform")
    if result["desktopMode"] != mode:
        raise ValueError("protocol: backend desktop mode does not match the requested mode")
    if not safeInteger(result["maxTimeoutMs"]) or not 1 <= result["maxTimeoutMs"] <= 120_000:
        raise ValueError("protocol: invalid maximum request timeout")
    capabilities = result["capabilities"]
    if (
        not isinstance(capabilities, list)
        or any(not isinstance(v, str) or not v for v in capabilities)
        or len(set(capabilities)) != len(capabilities)
    ):
        raise ValueError("protocol: invalid capabilities")
    required = (
        ("cancel", "requestDedup", "nativeOperations")
        if version == 5
        else (
            "structuredObservations",
            "cancel",
            "requestDedup",
            "captureMetadata",
            "retainedCaptures",
            "desktopCoordination",
        )
    )
    if version == 5 and "nativeBoundContracts" in capabilities:
        required += ("desktopCoordination",)
    for capability in required:
        if capability not in capabilities:
            raise ValueError(f"protocol: backend is missing {capability}")
    return dict(
        session=reply["session"],
        platform=result["platform"],
        desktopMode=mode,
        maxTimeoutMs=result["maxTimeoutMs"],
        capabilities=tuple(capabilities),
    )


def decodeResult(method, value):
    if method == "findElement":
        result = record(value, ["appRef", "scope", "ref", "token", "observed"])
        for key in ("appRef", "scope", "token"):
            nonempty(result[key], "protocol " + key)
        if not reference(result["ref"]):
            raise ValueError("protocol: invalid findElement reference")
        return {**result, "observed": decodeStamp(result["observed"])}
    if method == "readElementText":
        return decodeTextPage(value)
    if method == "readDesktop":
        return decodeDesktopObservation(value)
    if method == "listApps":
        if not isinstance(value, list):
            raise ValueError("protocol: invalid app list")
        ids, apps = set(), []
        for item in value:
            app = record(item, ["id", "displayName", "isRunning"])
            nonempty(app["id"], "protocol app id")
            nonempty(app["displayName"], "protocol app displayName")
            if type(app["isRunning"]) is not bool or app["id"] in ids:
                raise ValueError("protocol: invalid or duplicate app entry")
            ids.add(app["id"])
            apps.append(app)
        return apps
    if method == "readApp":
        has_overview = isinstance(value, dict) and "overview" in value
        has_catalog_scope = isinstance(value, dict) and "catalogScope" in value
        result = record(
            value,
            [
                "instance",
                "appRef",
                "observed",
                "scope",
                "windows",
                "window",
                "observation",
                "screenshot",
                "refs",
                *(["overview"] if has_overview else []),
                *(["catalogScope"] if has_catalog_scope else []),
            ],
        )
        nonempty(result["instance"], "protocol application instance")
        if has_catalog_scope:
            nonempty(result["catalogScope"], "protocol window catalogue scope")
        nonempty(result["appRef"], "protocol appRef")
        nonempty(result["scope"], "protocol window scope")
        observed = decodeStamp(result["observed"])
        if not isinstance(result["windows"], dict):
            raise ValueError("protocol: invalid window catalog")
        windows, scopes = result["windows"], set()
        for ref, scope in windows.items():
            if not reference(ref):
                raise ValueError("protocol: invalid window reference")
            nonempty(scope, "protocol window scope")
            if scope in scopes:
                raise ValueError("protocol: duplicate window scope")
            scopes.add(scope)
        window = result["window"]
        if window is not None and (
            not isinstance(window, str) or window not in windows or windows[window] != result["scope"]
        ):
            raise ValueError("protocol: selected window does not match its scope")
        if not isinstance(result["refs"], dict):
            raise ValueError("protocol: invalid readApp result")
        tokens = set()
        for name, token in result["refs"].items():
            if not reference(name):
                raise ValueError("protocol: invalid numeric element reference")
            nonempty(token, "protocol ref")
            if token in tokens:
                raise ValueError("protocol: duplicate element token")
            tokens.add(token)
        observation = decodeObservation(result["observation"])
        screenshot = (
            None
            if result["screenshot"] is None
            else decodeCapture(result["screenshot"], "window", result["scope"], observed)
        )
        overview = (
            None
            if not has_overview or result["overview"] is None
            else decodeCapture(result["overview"], "overview", observed=observed)
        )
        if (screenshot is not None) != ("screenshot" in observation) or (
            screenshot is not None and pngSize(screenshot) != observation["screenshot"]
        ):
            raise ValueError("protocol: observation screenshot dimensions do not match its capture")
        ids = [observed["id"], *[captureMetadata(png)["id"] for png in (screenshot, overview) if png is not None]]
        if len(set(ids)) != len(ids):
            raise ValueError("protocol: duplicate observation or image identity")
        if observation["current"] != window or sorted(w["ref"] for w in observation["windows"]) != sorted(windows):
            raise ValueError("protocol: inconsistent observation window catalog")
        return dict(
            instance=result["instance"],
            **({"catalogScope": result["catalogScope"]} if has_catalog_scope else {}),
            appRef=result["appRef"],
            observed=observed,
            scope=result["scope"],
            windows=windows,
            window=window,
            observation=observation,
            screenshot=screenshot,
            overview=overview,
            refs=result["refs"],
        )
    receipt = record(value, ["target", "status", "detail", "context"])
    nonempty(receipt["target"], "protocol action target")
    nonempty(receipt["detail"], "protocol action detail")
    if receipt["status"] not in ("dispatched", "verified"):
        raise ValueError("protocol: invalid action status")
    return frozen({**receipt, "context": decodeActionContext(receipt["context"])})
