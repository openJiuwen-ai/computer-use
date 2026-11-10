# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Path executors: external HTTP API (stdlib), shell CLI (argv, never a shell), engine SDK via the computer_use session.

Transport timeouts and cancellations are reported as effect-unknown; callers must not replay them blindly.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
import urllib.error
import urllib.request
from collections.abc import Awaitable, Callable
from typing import Any

from .paths import ExecutionStatus, RouteExecution, TaskPath

CodeRunner = Callable[[str], Awaitable[tuple[bool, str]]]


class PathArgumentError(ValueError):
    pass


_TOKEN = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)(!r)?\}")


def substitute(template: str, arguments: dict[str, Any]) -> str:
    """Replace {name} / {name!r} tokens for declared arguments only.

    Unlike str.format this leaves every other brace intact, so Python code
    templates with dict literals or f-strings survive substitution.
    """

    tokens = {match.group(1) for match in _TOKEN.finditer(template)}
    missing = sorted(tokens - set(arguments))
    if missing:
        raise PathArgumentError(f"missing arguments: {missing}")

    def replace(match):
        name, conversion = match.group(1), match.group(2)
        value = arguments[name]
        return repr(value) if conversion else str(value)

    return _TOKEN.sub(replace, template)


def validate_arguments(path: TaskPath, arguments: dict[str, Any]):
    """Minimal JSON-Schema check: required properties, declared types and additionalProperties."""

    schema = path.arguments or {}
    required = schema.get("required", [])
    missing = [name for name in required if name not in arguments]
    if missing:
        raise PathArgumentError(f"missing required arguments {missing} for path {path.id}")
    properties = schema.get("properties", {})
    if schema.get("additionalProperties") is False:
        unexpected = sorted(set(arguments) - set(properties))
        if unexpected:
            raise PathArgumentError(f"unexpected arguments {unexpected} for path {path.id}")
    for name, value in arguments.items():
        declared = properties.get(name)
        if not isinstance(declared, dict):
            continue
        expected = declared.get("type")
        if expected == "string" and not isinstance(value, str):
            raise PathArgumentError(f"argument {name!r} must be a string")
        if expected == "number" and not isinstance(value, (int, float)):
            raise PathArgumentError(f"argument {name!r} must be a number")
        if expected == "integer" and not isinstance(value, int):
            raise PathArgumentError(f"argument {name!r} must be an integer")
        if expected == "boolean" and not isinstance(value, bool):
            raise PathArgumentError(f"argument {name!r} must be a boolean")
        if expected == "array" and not isinstance(value, list):
            raise PathArgumentError(f"argument {name!r} must be an array")


async def execute_api(path: TaskPath, arguments: dict[str, Any]) -> RouteExecution:
    if path.api is None:
        raise ValueError(f"path {path.id} has no api spec")
    started = time.monotonic()
    url = substitute(path.api.url, arguments)
    body_arguments = {key: value for key, value in arguments.items() if "{" + key + "}" not in path.api.url}
    data = None
    if path.api.method.upper() not in ("GET", "HEAD") and body_arguments:
        data = json.dumps(body_arguments).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        method=path.api.method.upper(),
        headers={"Content-Type": "application/json", **path.api.headers},
    )

    def send() -> tuple[int, str]:
        with urllib.request.urlopen(request, timeout=path.api.timeout_s) as response:
            return response.status, response.read().decode("utf-8", "replace")

    try:
        status, text = await asyncio.to_thread(send)
    except urllib.error.HTTPError as error:
        text = error.read().decode("utf-8", "replace") if error.fp else ""
        return RouteExecution(
            path_id=path.id,
            status=ExecutionStatus.FAILED,
            dispatched=True,
            latency_s=time.monotonic() - started,
            output=text[:4096],
            error=f"http {error.code}: {error.reason}",
            detail=json.dumps({"status": error.code}),
        )
    except OSError as error:
        return RouteExecution(
            path_id=path.id,
            status=ExecutionStatus.EFFECT_UNKNOWN,
            dispatched=False,
            latency_s=time.monotonic() - started,
            error=f"transport failure: {error}",
            detail="request may not have reached the server; do not replay blindly",
        )
    cost = _usage_cost(text)
    return RouteExecution(
        path_id=path.id,
        status=ExecutionStatus.COMPLETED,
        dispatched=True,
        latency_s=time.monotonic() - started,
        cost=cost,
        output=text[:4096],
        detail=json.dumps({"status": status}),
    )


def _usage_cost(body: str) -> float | None:
    try:
        payload = json.loads(body)
    except ValueError:
        return None
    usage = payload.get("usage") if isinstance(payload, dict) else None
    if not isinstance(usage, dict):
        return None
    total_tokens = usage.get("total_tokens") or usage.get("totalTokens")
    if isinstance(total_tokens, (int, float)):
        return float(total_tokens) * 0.000002  # rough blended rate; refine per provider via hints
    return None


async def execute_cli(path: TaskPath, arguments: dict[str, Any]) -> RouteExecution:
    if path.cli is None:
        raise ValueError(f"path {path.id} has no cli spec")
    started = time.monotonic()
    argv = [substitute(part, arguments) for part in path.cli.argv]
    try:
        process = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    except OSError as error:
        return RouteExecution(
            path_id=path.id,
            status=ExecutionStatus.FAILED,
            dispatched=False,
            latency_s=time.monotonic() - started,
            error=f"failed to start {argv[0]!r}: {error}",
            detail=json.dumps({"returncode": 127}),
        )
    try:
        output, _ = await asyncio.wait_for(process.communicate(), timeout=path.cli.timeout_s)
    except TimeoutError:
        process.kill()
        await process.wait()
        return RouteExecution(
            path_id=path.id,
            status=ExecutionStatus.EFFECT_UNKNOWN,
            dispatched=True,
            latency_s=time.monotonic() - started,
            error=f"timeout after {path.cli.timeout_s}s",
            detail="process was killed; partial side effects possible; do not replay blindly",
        )
    text = output.decode("utf-8", "replace") if output else ""
    returncode = process.returncode if process.returncode is not None else -1
    return RouteExecution(
        path_id=path.id,
        status=ExecutionStatus.COMPLETED,
        dispatched=True,
        latency_s=time.monotonic() - started,
        output=text[:4096],
        error="" if returncode == 0 else f"exit code {returncode}",
        detail=json.dumps({"returncode": returncode, "output": text[:4096]}),
    )


async def execute_sdk(path: TaskPath, arguments: dict[str, Any], run_code: CodeRunner) -> RouteExecution:
    if path.sdk is None:
        raise ValueError(f"path {path.id} has no sdk spec")
    started = time.monotonic()
    code = substitute(path.sdk.code_template, arguments)
    # CancelledError is a BaseException and propagates untouched; only genuine
    # runner failures are converted, and those are effect-unknown by contract.
    try:
        success, output = await run_code(code)
    except Exception as error:  # transport/session failure: effect unknown per project rules
        return RouteExecution(
            path_id=path.id,
            status=ExecutionStatus.EFFECT_UNKNOWN,
            dispatched=False,
            latency_s=time.monotonic() - started,
            error=str(error),
            detail="session failure; previous actions may have executed",
        )
    status = ExecutionStatus.COMPLETED if success else ExecutionStatus.FAILED
    return RouteExecution(
        path_id=path.id,
        status=status,
        dispatched=True,
        latency_s=time.monotonic() - started,
        output=output[:4096],
        error="" if success else "computer_use reported failure",
        detail=json.dumps({"output": output[:4096]}),
    )
