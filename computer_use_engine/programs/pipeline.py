# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Compose one authorized workspace program; single-step execution stays in SDK."""

import asyncio
import inspect
import sys
import types
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import asdict
from pathlib import Path
from time import monotonic
from types import MappingProxyType

from computer_use import Computer
from computer_use.client.execution import ExecutionCancellation, execution_scope
from computer_use.errors import ComputerUseError

from .admission import Registry, fingerprint
from .artifact import Artifact
from .model import Check, ProgramResult


class _Refused(Exception):
    pass


class ProgramContext:
    """Own only this invocation's checks, deadline and cross-step evidence.

    Task code declares read-only checks and wraps synchronous SDK calls in steps.
    A reviewed Python program is trusted code, not a security-isolated
    interpreter. Sink errors stop execution; the host's checkpoint must raise
    on delivery failure.
    """

    def __init__(self, artifact, parameters, timeout_s, emit, checkpoint, observe_environment, executor):
        self.started = monotonic()
        self.deadline = self.started + timeout_s
        self.id, self.program = str(uuid.uuid4()), artifact.definition
        self.scope, self.expected_sha256 = artifact.expectation(parameters)
        self.emit, self.checkpoint = emit, checkpoint
        self.observe_environment = observe_environment
        self._executor = executor
        self.steps, self.checks, self.confirmed = [], [], []
        self.current, self.write_started = None, False
        self._failure, self._owner = None, asyncio.current_task()

    @property
    def first_failure(self) -> BaseException | None:
        """Expose the latched cause without transferring failure-state ownership."""
        return self._failure

    def _remember(self, error):
        if self._failure is None:
            self._failure = error

    def ensure_active(self):
        if self._failure is not None:
            raise self._failure
        if asyncio.current_task() is not self._owner:
            error = RuntimeError("Program steps must run serially in their owning task")
            self._remember(error)
            raise error

    def event(self, kind, **data):
        self.ensure_active()
        try:
            self.emit({"kind": kind, "execution_id": self.id, "program": self.program, "step": self.current, **data})
            self.checkpoint()
        except BaseException as error:
            self._remember(error)
            raise

    def check(self, condition, value, **evidence):
        self.ensure_active()
        try:
            item = Check(condition, value, self.scope, evidence, monotonic())
            self.checks.append(item)
            self.event("program_check", check=asdict(item))
            if value != "TRUE":
                raise _Refused(condition + ": " + value)
        except BaseException as error:
            self._remember(error)
            raise
        self.confirmed.append(condition)

    async def _invoke(self, operation):
        if inspect.iscoroutinefunction(operation):
            return await operation()
        cancellation = ExecutionCancellation()

        def call():
            with execution_scope(self._executor, cancellation):
                return operation()

        # Synchronous SDK operations must leave their owning event loop free.
        worker = asyncio.create_task(asyncio.to_thread(call))
        try:
            value = await asyncio.shield(worker)
        except asyncio.CancelledError:
            cancellation.cancel()
            # Repeated cancellation must still wait for native retirement.
            while not worker.done():
                try:
                    await asyncio.wait((worker,))
                except asyncio.CancelledError:
                    continue
            if not worker.cancelled():
                worker.exception()
            raise
        return await value if inspect.isawaitable(value) else value

    async def step(self, name, operation, *, write=False):
        self.ensure_active()
        self.current = name
        row = {"name": name, "kind": "write" if write else "query", "status": "started"}
        self.steps.append(row)
        start = monotonic()
        try:
            self.event("program_step_start")
            if monotonic() >= self.deadline:
                raise TimeoutError("Program deadline exhausted before next step")
            self.write_started |= write
            value = await self._invoke(operation)
            row["status"] = "returned"
            if write:
                row["receipt"] = {"status": value.status, "detail": value.detail}
            row["duration_s"] = monotonic() - start
            self.event("program_step_end", result=dict(row))
            return value
        except BaseException as error:
            row.update(status="error", error_type=type(error).__name__)
            self._remember(error)
            raise
        finally:
            row["duration_s"] = monotonic() - start


async def execute(
    artifact_path: Path,
    computer: Computer,
    parameters: dict,
    observe_environment: Callable[[], Awaitable[dict]],
    *,
    emit: Callable[[dict], None],
    checkpoint: Callable[[], None],
    registry: Registry | None = None,
    development: bool = False,
    timeout_s: float = 30,
) -> ProgramResult:
    """Load exact reviewed bytes only after validation/admission and recording.

    development=True is an explicit host authorization for candidate testing;
    it is never inferred from a missing registry or a failed admission lookup.
    Final acceptance stays with the independent host and cannot resume actions.
    """
    if not 0 < timeout_s <= 120:
        raise ValueError("Program deadline must be in (0, 120] seconds")
    artifact = Artifact.read(artifact_path)
    parameters = artifact.parameters(parameters)
    run = ProgramContext(
        artifact, parameters, timeout_s, emit, checkpoint, observe_environment, getattr(computer, "_executor", None)
    )
    status, failure = "awaiting_acceptance", None
    module_name = "_computer_use_program_" + run.id.replace("-", "")
    try:
        run.event("program_start", parameters_sha256=fingerprint(parameters), development=development)
        if not development:
            if registry is None:
                raise _Refused("Explicit admission registry required")
            selection = registry.select(artifact.definition)
            run.event("program_selection", selection=asdict(selection))
            if not selection.allowed:
                raise _Refused("Program admission denied: " + selection.reason)
        remaining = run.deadline - monotonic()
        if remaining <= 0:
            raise TimeoutError("Program deadline exhausted before entry")
        async with asyncio.timeout(remaining):
            module = types.ModuleType(module_name)
            module.__file__ = str(artifact.path / "program.py")
            sys.modules[module_name] = module
            # Execute the exact byte snapshot whose digest passed the gate.
            exec(compile(artifact.source, module.__file__, "exec"), module.__dict__)
            entry = getattr(module, "run", None)
            if not inspect.iscoroutinefunction(entry):
                raise ValueError("A program must define async run(context, computer, parameters)")
            await entry(run, computer, MappingProxyType(parameters))
            # Task code cannot turn a swallowed failed check/operation into a
            # successful execution or authorize another context step.
            run.ensure_active()
    except BaseException as error:
        termination = error
        error = run.first_failure or error
        status = (
            "cancelled"
            if isinstance(termination, asyncio.CancelledError)
            else "timeout"
            if isinstance(termination, TimeoutError)
            else "refused"
            if isinstance(error, _Refused) and not run.write_started
            else "stopped"
        )
        failure = {
            "step": run.current,
            "error_type": type(error).__name__,
            "termination_error_type": type(termination).__name__,
            "code": getattr(error, "code", None),
            "target": getattr(error, "target", run.scope),
            "detail": str(error),
            "effect": error.effect
            if isinstance(error, ComputerUseError)
            else "unknown"
            if run.write_started
            else "none",
            "effect_scope": "failed_operation",
            "write_started": run.write_started,
            "next": "Observe the original target and let the Agent decide; do not replay the program.",
        }
    finally:
        sys.modules.pop(module_name, None)
    return ProgramResult(
        execution_id=run.id,
        program=run.program,
        scope=run.scope,
        expected_sha256=run.expected_sha256,
        acceptance_condition=artifact.manifest["acceptance"]["condition"],
        status=status,
        checks=tuple(run.checks),
        steps=tuple(run.steps),
        confirmed_progress=tuple(run.confirmed),
        failure=failure,
        deadline_at_s=run.deadline,
        returned_at_s=monotonic(),
        duration_s=monotonic() - run.started,
        observation_required=True,
    )
