"""Frozen protocol-3 transport for offline historical fixtures only.

Current transport, native-v5 deadlines and cancellation use native.connection;
this copy never enters an installed package or a production fallback.
"""

import asyncio
import atexit
import codecs
import json
import math
import os
import subprocess
import sys
import uuid
from collections.abc import Mapping
from pathlib import Path

from legacy_protocol import decodeHello, protocolVersion
from legacy_results import decodeResult as decodeWireResult

from computer_use.diagnostics import diagnostic, localFailure, sdkVersion, wantsStructuredObservations
from computer_use.errors import ComputerUseError
from computer_use.native.preview import getPreviewHandler, readPreviews
from computer_use.paths import nativeDriver
from computer_use.protocol.captures import captureMetadata
from computer_use.protocol.execution import (
    decodeBegin,
    decodeCaptureRequest,
    decodeExecutionResult,
    decodeInspectQuery,
    decodeUnitCommand,
)
from computer_use.protocol.primitives import finite, record, safeInteger, trim
from computer_use.protocol.session import decodeFailure, desktopMode, failureError

requestCleanupGraceMs, driverTerminationGraceMs = 750, 1000
_PURE = {"listApps", "readDesktop", "readElementText", "findElement"}


def _json(value):
    def fallback(value):
        if isinstance(value, Mapping):
            return dict(value)
        raise TypeError(f"Cannot serialize {type(value).__name__}")

    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), allow_nan=False, default=fallback) + "\n"


def _now():
    return asyncio.get_running_loop().time() * 1000


class Connection:
    def __init__(self, invalidate, driver=None, *, protocol_version=protocolVersion, response_decoder=None):
        if type(protocol_version) is not int or protocol_version not in (3, 5):
            raise ValueError("Unsupported native protocol version")
        self.protocol_version = protocol_version
        # Diagnostic adapters may add presentation after protocol validation.
        # Native transport itself never imports a read pipeline or renderer.
        self._response_decoder = response_decoder or decodeWireResult
        self.invalidate, self.driver = invalidate, driver
        self._child = self._pending = self._hello = self._handshake = self._retiring = None
        self._buffer = b""
        self._requesting = self._waitingForExit = False
        self._nextId, self._generation = 1, 0
        self._diagnostic = ""
        self._traces, self._observations, self._readers = {}, {}, set()
        self._previewTransport = None
        self._childUsesEOF = False

    @property
    def session(self):
        return self._hello["session"] if self._hello is not None else None

    @property
    def platform(self):
        return self._hello["platform"] if self._hello is not None else None

    @property
    def capabilities(self):
        return frozenset(self._hello["capabilities"]) if self._hello is not None else frozenset()

    @property
    def sessionIdentity(self):
        "An internal lease cannot migrate across process retirement/reconnect."
        return (self._generation, self.session) if self._hello is not None else None

    def _track(self, coroutine):
        task = asyncio.create_task(coroutine)
        self._readers.add(task)
        task.add_done_callback(self._readers.discard)
        return task

    async def _connect(self):
        if self._child is not None:
            return
        if self.driver is None and sys.platform not in ("darwin", "linux", "win32"):
            raise localFailure(
                f"platform {sys.platform} is not implemented",
                "run the MCP server on a supported system with its matching backend",
                "mode_unsupported",
                "connection",
            )
        driver = self.driver if self.driver is not None else nativeDriver()
        command = driver["command"]
        if self.driver is None and not Path(command).exists():
            raise localFailure(
                f"{sys.platform} driver missing",
                "build the native driver on the MCP host, then read the App again",
                "execution_failed",
                "connection",
            )
        self._buffer, self._diagnostic, self._nextId = b"", "", 1
        self._traces = {}
        windows = sys.platform == "win32"
        # Windows uses native stdio handles. The POSIX 3/4 descriptor bridge
        # and preview pipe are not part of its driver's transport contract.
        preview = getPreviewHandler() if not windows else None
        read_fd = write_fd = -1
        lifetime = 4 if os.environ.get("COMPUTER_USE_LIFETIME_FD") == "4" else -1
        if preview is not None:
            read_fd, write_fd = os.pipe()
        try:
            env = dict(os.environ)
            if windows:
                env.pop("COMPUTER_USE_PREVIEW_FD", None)
                env.pop("COMPUTER_USE_LIFETIME_FD", None)
                program = [command, *driver.get("args", [])]
                options = dict(creationflags=subprocess.CREATE_NO_WINDOW)
            else:
                env["COMPUTER_USE_PREVIEW_FD"] = "3" if preview is not None else ""
                program = [
                    sys.executable,
                    "-m",
                    "computer_use.native.driver_exec",
                    str(write_fd),
                    str(lifetime),
                    command,
                    *driver.get("args", []),
                ]
                options = dict(pass_fds=tuple(fd for fd in (write_fd, lifetime) if fd >= 0))
            child = await asyncio.create_subprocess_exec(
                *program,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
                **options,
            )
        except BaseException:
            if read_fd >= 0:
                os.close(read_fd)
            raise
        finally:
            if write_fd >= 0:
                os.close(write_fd)
        self._child = child
        self._childUsesEOF = windows
        if preview is not None:
            reader = asyncio.StreamReader()
            transport, _ = await asyncio.get_running_loop().connect_read_pipe(
                lambda: asyncio.StreamReaderProtocol(reader), os.fdopen(read_fd, "rb", buffering=0)
            )
            self._previewTransport = transport
            self._track(readPreviews(reader, preview))
        diagnostic(
            dict(
                kind="connection",
                sdkVersion=sdkVersion,
                platform=sys.platform,
                desktopMode=os.environ.get("COMPUTER_USE_DESKTOP", "shared"),
                executable=command,
                pid=child.pid,
            )
        )
        atexit.register(self._on_exit)
        self._track(self._read_stdout(child))
        self._track(self._read_stderr(child, self._traces))
        self._track(self._watch(child))

    def _on_exit(self):
        child = self._child
        if child is not None and child.returncode is None:
            # TerminateProcess cannot run the driver's finally blocks. EOF
            # lets its reader cancel native work and release paired input.
            try:
                if self._childUsesEOF:
                    child.stdin.close()
                else:
                    child.terminate()
            except ProcessLookupError:
                pass

    async def _read_stdout(self, child):
        try:
            while data := await child.stdout.read(65536):
                if self._child is child:
                    self._receive(data)
            if self._child is child:
                self.close(
                    RuntimeError(
                        "driver disconnected"
                        + (" with an incomplete frame" if self._buffer else "")
                        + (": " + trim(self._diagnostic) if self._diagnostic else "")
                    )
                )
        except (OSError, ValueError) as error:
            if self._child is child:
                self.close(error)

    async def _read_stderr(self, child, traces):
        decoder = codecs.getincrementaldecoder("utf-8")("replace")
        buffer = ""

        def emit(line, *, partial=False):
            try:
                value = json.loads(line)
            except ValueError:
                value = line
            envelope = value if isinstance(value, dict) else {}
            request_id = envelope.get("requestId")
            event = dict(kind="backend_diagnostic", pid=child.pid, value=envelope.get("message", value))
            if partial:
                event["partial"] = True
            if finite(request_id):
                event["requestId"] = request_id
                if request_id in traces:
                    event["traceId"] = traces[request_id]
            diagnostic(event)

        try:
            while data := await child.stderr.read(65536):
                text = decoder.decode(data)
                # Retired drivers still report cleanup. Their buffers/traces
                # stay local and never alter a successor's failure context.
                if self._child is child:
                    self._diagnostic = (self._diagnostic + text)[-4096:]
                buffer += text
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    emit(line)
                if len(buffer) > 65536:
                    emit(buffer, partial=True)
                    buffer = ""
        except (OSError, ValueError):
            pass
        buffer += decoder.decode(b"", final=True)
        if buffer:
            emit(buffer, partial=True)

    async def _watch(self, child):
        code = await child.wait()
        if self._child is child:
            self.close(
                RuntimeError(f"driver exited ({code})" + (": " + trim(self._diagnostic) if self._diagnostic else ""))
            )

    async def request(self, method, params, options=None):
        if self._requesting:
            raise localFailure("another system call is in progress", "await it before starting the next")
        self._requesting = True
        try:
            return await self._request(method, params, options.get("timeoutMs") if options is not None else None)
        finally:
            self._requesting = False

    async def connect(self, timeout_ms=30000):
        "Negotiate a session without sending a UI command or inventing a binding."
        return await self.request(None, {}, dict(timeoutMs=timeout_ms))

    def _write(self, value):
        if self._child is None or self._child.stdin.is_closing():
            raise RuntimeError("driver input channel closed")
        self._child.stdin.write(_json(value).encode("utf-8"))

    async def _negotiate(self, timeoutMs):
        if self._hello is not None:
            return
        mode, loop = desktopMode(), asyncio.get_running_loop()
        future = loop.create_future()
        timer = loop.call_later(
            timeoutMs / 1000, lambda: self.close(RuntimeError("protocol hello timed out; no system command was sent"))
        )
        self._handshake = dict(future=future, timer=timer)
        self._write(dict(version=self.protocol_version, id=0, method="hello", params=dict(desktopMode=mode)))
        # This future belongs to the caller. Cancellation closes the whole
        # connection; only driver retirement is independent cleanup work.
        await future

    async def _request(self, method, params, budget):
        if self._pending is not None or self._waitingForExit:
            raise localFailure("another system call is in progress", "await it before starting the next")
        if self.protocol_version == 5 and method == "begin":
            try:
                params = decodeBegin(params)
            except (TypeError, ValueError) as error:
                raise localFailure(
                    str(error), "correct the native operation intent", "invalid_request", method
                ) from error
        if self.protocol_version == 5 and method == "inspect":
            try:
                params = {**params, "query": decodeInspectQuery(params.get("query"))}
            except (TypeError, ValueError) as error:
                raise localFailure(
                    str(error), "correct the native inspection request", "invalid_request", method
                ) from error
        if self.protocol_version == 5 and method == "capture":
            try:
                params = decodeCaptureRequest(params)
            except (TypeError, ValueError) as error:
                raise localFailure(
                    str(error), "correct the native capture scope or budget", "invalid_request", method
                ) from error
        if self.protocol_version == 5 and method == "execute":
            try:
                params = {**params, "command": decodeUnitCommand(params.get("command"))}
            except (TypeError, ValueError) as error:
                raise localFailure(str(error), "correct the native unit command", "invalid_request", method) from error
        deadline = _now() + max(
            1,
            min(
                budget if budget is not None else 30000,
                self.driver.get("timeoutMs", 30000) if self.driver is not None else 30000,
            ),
        )

        def remaining():
            value = math.ceil(deadline - _now())
            if value < 1:
                raise localFailure(
                    "request budget exhausted before dispatch; no system command was sent",
                    "read the target again when ready",
                    "execution_failed",
                    "connection",
                )
            return value

        if self._retiring is not None:
            self._waitingForExit = True
            generation = self._generation
            try:
                try:
                    await asyncio.wait_for(asyncio.shield(self._retiring), remaining() / 1000)
                except TimeoutError:
                    raise localFailure(
                        "previous driver is still stopping",
                        "wait for it to exit before reading the app again; no replacement driver was started",
                        "execution_failed",
                        "connection",
                    ) from None
                if generation != self._generation:
                    raise localFailure(
                        "connection closed before the request started",
                        "read the app again when ready",
                        "execution_failed",
                        "connection",
                    )
            finally:
                self._waitingForExit = False
        try:
            desktopMode()
            remaining()
            await self._connect()
            await self._negotiate(remaining())
        except asyncio.CancelledError:
            self.close(RuntimeError("connection request cancelled before dispatch"))
            raise
        except Exception as error:
            failure = (
                error
                if isinstance(error, ComputerUseError)
                else ComputerUseError(
                    dict(
                        code="execution_failed",
                        effect="none",
                        target=method or "connection",
                        detail=str(error),
                        next="check the backend configuration and connect again; no system command was sent",
                    ),
                    cause=error,
                )
            )
            self.close(failure)
            if failure is error:
                raise
            raise failure from error
        if method is None:
            remaining()
            return dict(self._hello)
        if not safeInteger(self._nextId):
            error = localFailure(
                "request id space exhausted", "read the app in a new session", "execution_failed", method
            )
            self.close(error)
            raise error
        if method == "findElement" and "elementPathLookup" not in self._hello["capabilities"]:
            raise localFailure(
                "backend does not support element paths",
                "update the backend or use observed numeric elements",
                "mode_unsupported",
                "find",
            )
        capability = (
            "nativeApplicationRoots"
            if method == "inspect" and params.get("query", {}).get("kind") == "roots"
            else "nativeRelationPages"
            if method == "inspect" and "relationPage" in params.get("query", {})
            else "nativeNodePages"
            if method == "inspect" and "childrenPage" in params.get("query", {})
            else "nativeCapturedText"
            if method == "inspect" and params.get("query", {}).get("kind") == "capturedText"
            else "nativeRelations"
            if method == "inspect" and params.get("query", {}).get("kind") == "relation"
            else "nativeCapture"
            if method == "capture"
            else "nativeOperations"
            if method in ("begin", "inspect", "execute", "finish")
            else "desktopObservation"
            if method == "readDesktop"
            else "desktopInput"
            if "desktopRef" in params
            else "elementInput"
            if method
            in (
                "clickElement",
                "openElementMenu",
                "focusElement",
                "typeTextElement",
                "pasteElement",
                "pressKeyElement",
                "performElementAction",
            )
            or method == "scroll"
            and "ref" in params
            else "windowInput"
            if method in ("click", "scroll", "drag", "typeText", "paste", "pressKey")
            else None
        )
        if capability is not None and capability not in self._hello["capabilities"]:
            raise localFailure(
                f"backend does not provide {capability} in this desktop session",
                ("use supported native elements, or connect a desktop with the required capture/input capabilities"),
                "mode_unsupported",
                method,
            )
        if (
            method == "inspect"
            and "followRelations" in params.get("query", {})
            and "nativeFollowRelations" not in self._hello["capabilities"]
        ):
            raise localFailure(
                "backend does not provide nativeFollowRelations",
                "rebuild the driver before requesting additional relationship collection",
                "mode_unsupported",
                method,
            )
        if method == "capture" and "nativeCaptureKinds" not in self._hello["capabilities"]:
            raise localFailure(
                "backend does not provide nativeCaptureKinds",
                "rebuild the driver before using explicit native capture scopes",
                "mode_unsupported",
                method,
            )
        if (
            method == "begin" and params.get("mode") == "write" or method == "execute"
        ) and "nativeBoundContracts" not in self._hello["capabilities"]:
            raise localFailure(
                "backend does not provide nativeBoundContracts",
                "rebuild the native driver before using the contract-bound execution protocol",
                "mode_unsupported",
                method,
            )
        if (
            method == "begin"
            and params.get("mode") == "write"
            or method == "inspect"
            and params.get("query", {}).get("kind") in ("inputContext", "launch")
        ) and "nativeUnitReadinessV2" not in self._hello["capabilities"]:
            raise localFailure(
                "backend does not provide nativeUnitReadinessV2",
                "rebuild the driver before using per-unit execution readiness",
                "mode_unsupported",
                method,
            )
        if (
            method == "begin"
            and params.get("intent", {}).get("kind") == "launch"
            or method == "inspect"
            and params.get("query", {}).get("kind") == "launch"
            or method == "execute"
            and params.get("command", {}).get("kind") == "launch_application"
        ) and "nativeLaunch" not in self._hello["capabilities"]:
            raise localFailure(
                "backend does not provide nativeLaunch",
                "rebuild the native driver before launching through the execution pipeline",
                "mode_unsupported",
                method,
            )
        if (
            method == "begin"
            and params.get("intent", {}).get("kind") in ("click", "openMenu")
            and params.get("target", {}).get("capture") is None
            and "nativeActionIntents" not in self._hello["capabilities"]
        ):
            raise localFailure(
                "backend does not provide nativeActionIntents",
                "update the native driver before using this execution route",
                "mode_unsupported",
                method,
            )
        if (
            method == "begin"
            and params.get("intent", {}).get("kind") in ("focus", "key")
            and "nativeKeyboardIntents" not in self._hello["capabilities"]
        ):
            raise localFailure(
                "backend does not provide nativeKeyboardIntents",
                "update the native driver before preparing or sending keys",
                "mode_unsupported",
                method,
            )
        if (
            method == "begin"
            and params.get("intent", {}).get("kind") in ("insert", "paste")
            and "nativeTextInput" not in self._hello["capabilities"]
        ):
            raise localFailure(
                "backend does not provide nativeTextInput",
                "update the native driver before inserting or pasting text",
                "mode_unsupported",
                method,
            )
        if (
            method == "begin"
            and params.get("mode") == "write"
            and params.get("target", {}).get("node") is None
            and params.get("target", {}).get("surface") is not None
            and "nativeWindowScope" not in self._hello["capabilities"]
        ):
            raise localFailure(
                "backend does not provide nativeWindowScope",
                "update the driver before resolving a target inside a native operation",
                "mode_unsupported",
                method,
            )
        if (
            method == "inspect"
            and params.get("query", {}).get("within")
            and "nativeWindowScope" not in self._hello["capabilities"]
        ):
            raise localFailure(
                "backend does not provide nativeWindowScope",
                "update the driver before binding logical target constraints",
                "mode_unsupported",
                method,
            )
        if (
            method == "inspect"
            and "actuator" in params.get("query", {})
            and params["query"]["actuator"].get("relation") == "containedEditor"
            and "nativeActuators" not in self._hello["capabilities"]
        ):
            raise localFailure(
                "backend does not provide nativeActuators",
                "update the driver before selecting an internal editor",
                "mode_unsupported",
                method,
            )
        if (
            method == "inspect"
            and params.get("query", {}).get("actuator", {}).get("relation") == "scrollPage"
            and "nativeScroll" not in self._hello["capabilities"]
        ):
            raise localFailure(
                "backend does not provide nativeScroll",
                "update the driver before selecting native page scrolling",
                "mode_unsupported",
                method,
            )
        if (
            method == "execute"
            and params.get("command", {}).get("kind") == "native_scroll"
            and not {"nativeScroll", "nativeScrollPattern"} & set(self._hello["capabilities"])
        ):
            raise localFailure(
                "backend does not provide native scrolling",
                "update the driver before executing native scrolling",
                "mode_unsupported",
                method,
            )
        if (
            method == "inspect"
            and params.get("query", {}).get("actuator", {}).get("relation") == "scrollPointer"
            and "nativeElementScroll" not in self._hello["capabilities"]
        ):
            raise localFailure(
                "backend does not provide nativeElementScroll",
                "update the driver before selecting an element wheel route",
                "mode_unsupported",
                method,
            )
        if (
            method == "inspect"
            and params.get("query", {}).get("actuator", {}).get("contentPath")
            and "nativeScrollContentPath" not in self._hello["capabilities"]
        ):
            raise localFailure(
                "backend does not provide nativeScrollContentPath",
                "update the driver before selecting moving scroll content",
                "mode_unsupported",
                method,
            )
        if method == "begin" and params.get("mode") == "write" and params.get("target", {}).get("capture") is not None:
            capability = "nativeDesktopInput" if params["target"].get("surface") is None else "nativePointer"
            if capability not in self._hello["capabilities"]:
                raise localFailure(
                    "backend does not provide " + capability,
                    "update the driver before using this observed input scope",
                    "mode_unsupported",
                    method,
                )
        id__value, trace_id = self._nextId, str(uuid.uuid4())
        self._nextId += 1
        self._traces[id__value] = trace_id
        if len(self._traces) > 1024:
            self._traces.pop(next(iter(self._traces)))
        target_value, timeout_ms_local = (
            self._command_target(method, params),
            min(remaining(), self._hello["maxTimeoutMs"]),
        )
        diagnostic(
            dict(
                kind="command_start",
                traceId=trace_id,
                id=id__value,
                method=method,
                params=params,
                target=target_value,
                timeoutMs=timeout_ms_local,
            )
        )
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        pure = (
            method in _PURE
            or self.protocol_version == 5
            and method == "capture"
            or method == "inspect"
            and params.get("query", {}).get("kind") == "capturedText"
        )
        pending = dict(
            id=id__value,
            method=method,
            traceId=trace_id,
            target=target_value,
            future=future,
            cancelSent=False,
            startedAt=_now(),
            pure=pure,
        )

        def timeout():
            if self._pending is not pending:
                return
            diagnostic(
                dict(
                    kind="request_cancelling",
                    traceId=trace_id,
                    id=id__value,
                    method=method,
                    timeoutMs=timeout_ms_local,
                    cleanupGraceMs=requestCleanupGraceMs,
                )
            )
            grace = max(0, pending["startedAt"] + timeout_ms_local + requestCleanupGraceMs - _now())

            def terminalTimeout():
                if self._pending is pending:
                    self.close(
                        RuntimeError(
                            (
                                f"request timed out after {timeout_ms_local} ms; no terminal response within "
                                f"{requestCleanupGraceMs} ms cleanup grace"
                            )
                        )
                    )

            pending["timer"] = loop.call_later(grace / 1000, terminalTimeout)
            self._cancel_pending(pending)

        pending["timer"] = loop.call_later(timeout_ms_local / 1000, timeout)
        self._pending = pending
        try:
            self._write(
                dict(
                    version=self.protocol_version,
                    session=self._hello["session"],
                    id=id__value,
                    method=method,
                    params=params,
                    timeoutMs=timeout_ms_local,
                )
            )
        except Exception as error:
            self.close(error)
        try:
            return await future
        except asyncio.CancelledError:
            self.close(RuntimeError("host cancelled the request"))
            raise

    def _receive(self, data):
        try:
            self._buffer += data
            if len(self._buffer) > 64 * 1024 * 1024:
                raise ValueError("protocol: response frame too large")
            while b"\n" in self._buffer:
                line, self._buffer = self._buffer.split(b"\n", 1)
                if line.endswith(b"\r"):
                    line = line[:-1]
                if line.startswith(b"\xef\xbb\xbf"):
                    raise ValueError("protocol: BOM is not allowed")

                def invalidConstant(value):
                    raise ValueError("protocol: invalid JSON constant " + value)

                value = json.loads(line.decode("utf-8", errors="strict"), parse_constant=invalidConstant)
                if not isinstance(value, dict):
                    raise ValueError("protocol: expected response object")
                if self._handshake is not None:
                    if self._buffer:
                        raise ValueError("protocol: unsolicited data after hello")
                    self._hello = decodeHello(value, desktopMode(), self.protocol_version)
                    if (
                        os.environ.get("COMPUTER_USE_DEMO_FOREGROUND") == "1"
                        and "demoForeground" not in self._hello["capabilities"]
                    ):
                        raise localFailure(
                            "demoForeground is not supported by this native backend",
                            "rebuild the backend on the MCP host or disable demoForeground",
                            "mode_unsupported",
                            "connection",
                        )
                    handshake, self._handshake = self._handshake, None
                    handshake["timer"].cancel()
                    diagnostic(dict(kind="protocol_hello", version=self.protocol_version, **self._hello))
                    if not handshake["future"].done():
                        handshake["future"].set_result(None)
                    continue
                response = record(value, ["version", "session", "id", "error" if "error" in value else "result"])
                if (
                    not finite(response["version"])
                    or response["version"] != self.protocol_version
                    or self._hello is None
                    or response["session"] != self._hello["session"]
                ):
                    raise ValueError("protocol: response belongs to a different version or session")
                pending = self._pending
                if pending is None or not finite(response["id"]) or response["id"] != pending["id"]:
                    raise ValueError("protocol: unexpected response id")
                if self._buffer:
                    raise ValueError("protocol: unsolicited data after response")
                result = error = failure = None
                if "error" in response:
                    failure = decodeFailure(response["error"])
                    error = failureError(pending["method"], failure)
                else:
                    if self.protocol_version == 5:
                        result = decodeExecutionResult(pending["method"], response["result"])
                    else:
                        result = self._response_decoder(pending["method"], response["result"])
                if error is None and pending["method"] == "readApp" and wantsStructuredObservations():
                    raw = response["result"]
                    diagnostic(
                        dict(
                            kind="native_observation",
                            traceId=pending["traceId"],
                            target=pending["target"],
                            observation=raw["observation"],
                            observed=raw["observed"],
                            scope=raw["scope"],
                        )
                    )
                desktop_capture = (
                    pending["method"] == "capture" and result is not None and result.get("kind") == "desktopCapture"
                )
                if (
                    (pending["method"] == "readDesktop" or desktop_capture)
                    and result is not None
                    and result["observation"]["mode"] != self._hello["desktopMode"]
                ):
                    raise ValueError("protocol: desktop observation mode differs from the negotiated session")
                summary = result
                if pending["method"] == "readApp" and result is not None:
                    window = next(
                        (line for line in result.get("text", "").split("\n") if line.startswith("Window:")), None
                    )
                    if pending["target"] is not None and pending["target"].get("selector"):
                        target = dict(
                            selector=pending["target"]["selector"], **({"window": window} if window is not None else {})
                        )
                        pending["target"] = target
                        key = (result["instance"], result["scope"])
                        self._observations.pop(key, None)
                        self._observations[key] = dict(
                            appRef=result["appRef"], refs=set(result["refs"].values()), target=target
                        )
                        while len(self._observations) > 64:
                            self._observations.pop(next(iter(self._observations)))
                    summary = {
                        **result,
                        "screenshot": dict(source="window", **(captureMetadata(result["screenshot"]) or {}))
                        if result["screenshot"] is not None
                        else None,
                        "overview": dict(source="overview", **(captureMetadata(result["overview"]) or {}))
                        if result.get("overview") is not None
                        else None,
                    }
                elif (pending["method"] == "readDesktop" or desktop_capture) and result is not None:
                    summary = {
                        **result,
                        "screenshot": dict(source="desktop", **(captureMetadata(result["screenshot"]) or {}))
                        if result["screenshot"] is not None
                        else None,
                    }
                elif pending["method"] == "capture" and result is not None:
                    summary = {**result, "image": dict(source="window", **(captureMetadata(result["image"]) or {}))}
                if (
                    pending["method"] == "readApp"
                    and error is not None
                    and pending["target"] is not None
                    and pending["target"].get("selector")
                ):
                    for scope, observation in list(self._observations.items()):
                        if observation["target"]["selector"] == pending["target"]["selector"]:
                            self._observations.pop(scope)
                diagnostic(
                    dict(
                        kind="command_end",
                        traceId=pending["traceId"],
                        id=pending["id"],
                        method=pending["method"],
                        durationMs=_now() - pending["startedAt"],
                        target=pending["target"],
                        status="error" if error is not None else "ok",
                        **(
                            dict(error=str(error), failure=failure, stage="backend")
                            if error is not None
                            else dict(result=summary)
                        ),
                    )
                )
                pending["timer"].cancel()
                self._pending = None
                # A queued reply can arrive after task.cancel() but before
                # its coroutine resumes to close the connection.
                if not pending["future"].done():
                    if error is not None:
                        pending["future"].set_exception(error)
                    else:
                        pending["future"].set_result(result)
        except Exception as error:
            self.close(error)

    def _command_target(self, method, params):
        if method == "readDesktop" or "desktopRef" in params or method == "capture" and params.get("kind") == "desktop":
            return dict(screen="desktop")
        if method == "readApp":
            return dict(selector=params["selector"])
        return next(
            (
                o["target"]
                for o in self._observations.values()
                if params.get("appRef") == o["appRef"] or "ref" in params and params["ref"] in o["refs"]
            ),
            None,
        )

    def _cancel_pending(self, pending):
        if pending["cancelSent"]:
            return
        pending["cancelSent"] = True
        if self._child is None or self._hello is None:
            return
        try:
            self._write(dict(version=self.protocol_version, session=self._hello["session"], cancel=pending["id"]))
        except Exception:
            pass

    def close(self, error=None):
        error = error if error is not None else RuntimeError("connection closed")
        self._generation += 1
        child_value, pending_value, uses_eof = self._child, self._pending, self._childUsesEOF
        if pending_value is not None:
            self._cancel_pending(pending_value)
        self._child = self._hello = None
        handshake, self._handshake = self._handshake, None
        if handshake is not None:
            handshake["timer"].cancel()
            failure = (
                error
                if isinstance(error, ComputerUseError)
                else ComputerUseError(
                    dict(
                        code="execution_failed",
                        target="hello",
                        effect="none",
                        detail=f"{error}; no system command was sent; hello did not complete",
                        next="check the backend configuration before connecting again",
                    ),
                    cause=error,
                )
            )
            if not handshake["future"].done():
                handshake["future"].set_exception(failure)
        self._pending = None
        if pending_value is not None:
            pure, target = pending_value["pure"], pending_value["target"] or {}
            failure = ComputerUseError(
                dict(
                    code="execution_failed" if pure else "execution_unknown",
                    effect="none" if pure else "possible",
                    target=target.get("selector", target.get("screen", pending_value["method"])),
                    detail=(
                        f"{error}; {'read-only request failed' if pure else 'command may have partially executed'}"
                        f"; current observation invalidated"
                    ),
                    next="reconnect by reading the target; inspect its state before retrying an action",
                ),
                cause=error,
                method=pending_value["method"],
            )
            diagnostic(
                dict(
                    kind="command_end",
                    traceId=pending_value["traceId"],
                    id=pending_value["id"],
                    method=pending_value["method"],
                    durationMs=_now() - pending_value["startedAt"],
                    target=pending_value["target"],
                    status="error",
                    stage="connection",
                    error=str(failure),
                    failure={key: getattr(failure, key) for key in ("code", "effect", "target", "detail", "next")},
                )
            )
            pending_value["timer"].cancel()
            if not pending_value["future"].done():
                pending_value["future"].set_exception(failure)
        self._observations.clear()
        self.invalidate()
        self._buffer = b""
        atexit.unregister(self._on_exit)
        if self._previewTransport is not None:
            self._previewTransport.close()
            self._previewTransport = None
        if child_value is not None:
            child_value.stdin.close()
            if child_value.returncode is None and not uses_eof:
                try:
                    child_value.terminate()
                except ProcessLookupError:
                    pass

            async def retire():
                try:
                    try:
                        await asyncio.wait_for(child_value.wait(), driverTerminationGraceMs / 1000)
                    except TimeoutError:
                        if child_value.returncode is None:
                            diagnostic(
                                dict(
                                    kind="driver_forced_exit",
                                    pid=child_value.pid,
                                    terminationGraceMs=driverTerminationGraceMs,
                                )
                            )
                            try:
                                child_value.kill()
                            except ProcessLookupError:
                                pass
                        await child_value.wait()
                finally:
                    if self._retiring is asyncio.current_task():
                        self._retiring = None

            self._retiring = asyncio.create_task(retire())

    async def waitClosed(self):
        if self._retiring is not None:
            await asyncio.shield(self._retiring)
