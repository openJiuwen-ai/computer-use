"""Read one locally confirmed gaze target through the existing MCP session."""

import asyncio
import json
import math
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener
from uuid import uuid4

from openjiuwen.core.foundation.tool import Tool, ToolCard
from openjiuwen.core.foundation.tool.schema import ToolOutput


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class _GazeError(ValueError):
    pass


class _Refused(_GazeError):
    pass


class GazeReadTool(Tool):
    def __init__(self, toolkit, connection_file):
        super().__init__(
            ToolCard(
                id=f"{toolkit.id}.read_gaze_target",
                name="read_gaze_target",
                description=(
                    "Read the macOS window the user freshly confirmed with gaze and a gesture or voice. "
                    "For a voice request, pass its exact expected_selection_id. Returns the normal App snapshot, "
                    "including confirmed attached dialogs. Fails on missing, stale or ambiguous targets; never "
                    "substitute another window. This tool only reads; it does not authorize actions or restrict "
                    "the separate computer_use tool. Ask for a fresh confirmation when the selection expires."
                ),
                input_params={
                    "type": "object",
                    "properties": {"expected_selection_id": {"type": "string", "minLength": 1, "maxLength": 100}},
                    "additionalProperties": False,
                },
                parallel_safe=False,
                stateless=False,
                idempotent=False,
            )
        )
        data = json.loads(Path(connection_file).read_text(encoding="utf-8"))
        url = urlsplit(data["url"])
        if (
            url.scheme != "http"
            or url.hostname != "127.0.0.1"
            or not url.port
            or url.username is not None
            or url.password is not None
            or url.path
            or url.query
            or url.fragment
        ):
            raise _GazeError("gaze connection must use http://127.0.0.1 with an explicit port and no path")
        if not isinstance(data.get("token"), str) or not data["token"] or any(c in data["token"] for c in "\r\n"):
            raise _GazeError("gaze connection requires a session token")
        self.toolkit, self.url, self.token = toolkit, data["url"], data["token"]
        self._busy = self._unresolved = False
        self._active = self._cleanup = None

    async def _request(self, action, owner, selection_id=None, **fields):
        def send():
            body = dict(action=action, owner=owner, selection_id=selection_id, **fields)
            request = Request(
                self.url + "/api/target",
                data=json.dumps(body).encode(),
                headers={"X-Session-Token": self.token, "Content-Type": "application/json"},
            )
            try:
                with build_opener(ProxyHandler({}), _NoRedirect()).open(request, timeout=2) as response:
                    raw = response.read(16385)
            except HTTPError as error:
                error.close()
                if error.code in (400, 401, 403):
                    raise _Refused(
                        "Gaze service refused the request; confirm a fresh target and check the connection."
                    ) from None
                raise _GazeError("Gaze service response was unavailable; no automatic retry.") from None
            except OSError:
                raise _GazeError("Gaze service could not be reached; no automatic retry.") from None
            if len(raw) > 16384:
                raise _GazeError("Gaze service response exceeded the size limit.")
            try:
                result = json.loads(raw)
            except (ValueError, UnicodeError):
                raise _GazeError("Gaze service returned invalid JSON.") from None
            if not isinstance(result, dict):
                raise _GazeError("Gaze service returned an invalid receipt.")
            return result

        return await asyncio.to_thread(send)

    @staticmethod
    def _selection_id(receipt):
        value = receipt.get("selection_id")
        return value if isinstance(value, str) and 1 <= len(value) <= 100 else None

    @classmethod
    def _validate(cls, receipt, expected=None):
        selection = cls._selection_id(receipt)
        remaining = receipt.get("expires_in_seconds")
        if (
            selection is None
            or receipt.get("source") != "camera"
            or any(type(receipt.get(key)) is not int or receipt[key] <= 0 for key in ("pid", "window_id"))
            or receipt.get("read_only") is not True
            or receipt.get("confirmation_method") not in ("thumb", "double_blink", "voice")
            or type(remaining) not in (int, float)
            or not math.isfinite(remaining)
            or remaining <= 0
            or (expected is not None and selection != expected)
            or (receipt["confirmation_method"] == "voice" and selection != expected)
        ):
            raise _GazeError("Gaze selection is missing, stale or mismatched; confirm the intended window again.")
        return tuple(
            receipt[key] for key in ("selection_id", "pid", "window_id", "source", "read_only", "confirmation_method")
        )

    async def invoke(self, inputs, **kwargs):
        if self.toolkit._closing is not None:
            return ToolOutput(success=False, error="Computer Use toolkit is closed.")
        if self._busy:
            return ToolOutput(success=False, error="A gaze read is already running.")
        if self._unresolved:
            return ToolOutput(
                success=False,
                error=(
                    "Previous gaze lease cleanup is unconfirmed. Wait for its 120-second expiry, "
                    "then recreate the toolkit and confirm a new target. No read sent."
                ),
            )
        if (
            not isinstance(inputs, dict)
            or set(inputs) - {"expected_selection_id"}
            or (
                "expected_selection_id" in inputs
                and (
                    not isinstance(inputs["expected_selection_id"], str)
                    or not 1 <= len(inputs["expected_selection_id"]) <= 100
                )
            )
        ):
            return ToolOutput(success=False, error="Only an optional nonempty expected_selection_id is accepted.")
        self._busy = self._unresolved = True
        self._active = asyncio.current_task()
        owner, selection = uuid4().hex, None
        expected = inputs.get("expected_selection_id")
        result = None
        try:
            claim = asyncio.create_task(self._request("claim", owner, expected_selection_id=expected, read_only=True))
            try:
                receipt = await asyncio.shield(claim)
            except asyncio.CancelledError:
                # A cancelled HTTP await can still acquire a lease; collect its reply before releasing.
                try:
                    selection = self._selection_id(await claim)
                except _Refused:
                    self._unresolved = False
                except Exception:
                    pass
                raise
            except _Refused:
                self._unresolved = False
                raise
            selection = self._selection_id(receipt)
            identity = self._validate(receipt, expected)
            if self._validate(await self._request("validate", owner, selection), expected) != identity:
                raise _GazeError("Gaze selection changed before capture; no read sent.")
            result = await self.toolkit.call(
                "computer_use",
                {
                    "title": "Read the confirmed gaze window",
                    "code": (
                        "from computer_use.integrations.macos import app_for_window\n"
                        f"_gaze_app = await app_for_window(computer, process_id={receipt['pid']}, window_id={receipt['window_id']})\n"
                        'await _gaze_app.read(view="both")'
                    ),
                },
            )
            if self._validate(await self._request("validate", owner, selection), expected) != identity:
                raise _GazeError("Gaze selection changed during capture; snapshot discarded. Confirm again.")
            if not result.success:
                raise _GazeError(
                    "The engine could not read the confirmed window; no observation acknowledged. Confirm again."
                )
            if receipt["confirmation_method"] == "voice":
                acknowledged = await self._request("observed", owner, selection)
                if acknowledged.get("observed") is not True:
                    raise _GazeError("Gaze service did not acknowledge the voice observation.")
        except Exception as error:
            # Service and engine failures may contain private window contents or connection details.
            message = str(error) if isinstance(error, _GazeError) else "Gaze read failed; no snapshot delivered."
            result = ToolOutput(success=False, error=message)
        finally:
            if selection is not None:

                async def release():
                    try:
                        released = await self._request("release", owner, selection)
                        self._unresolved = released.get("released") is not True
                    except Exception:
                        self._unresolved = True

                self._cleanup = asyncio.create_task(release())
                try:
                    await asyncio.shield(self._cleanup)
                except asyncio.CancelledError:
                    await self._cleanup
                    raise
                finally:
                    self._busy = False
                    self._active = None
            else:
                self._busy = False
                self._active = None
        if self._unresolved:
            return ToolOutput(
                success=False, error="Gaze lease cleanup is unconfirmed; snapshot withheld. Restart after lease expiry."
            )
        return result

    async def close(self):
        if self._active is not None:
            self._active.cancel()
            await asyncio.gather(self._active, return_exceptions=True)
        if self._cleanup is not None:
            await asyncio.shield(self._cleanup)

    async def stream(self, inputs, **kwargs):
        yield await self.invoke(inputs, **kwargs)

    def render_for_llm(self, output):
        return output.data.get("content", "") if output.success else output.error
