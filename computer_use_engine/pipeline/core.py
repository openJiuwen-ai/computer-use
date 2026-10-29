# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Typed execution nodes for composing and tracing hierarchical flows."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, replace
from enum import Enum
from inspect import isawaitable, signature as inspect_signature
from typing import (
    TYPE_CHECKING,
    Any,
    Awaitable,
    Callable,
    Generic,
    Hashable,
    Iterable,
    Mapping,
    ParamSpec,
    Protocol,
    TypeVar,
    cast,
)

if TYPE_CHECKING:
    from contextlib import AbstractAsyncContextManager, AbstractContextManager
    from inspect import Signature
    from typing import AbstractSet

P = ParamSpec("P")
T = TypeVar("T")
U = TypeVar("U")
V = TypeVar("V")
# API, protocol, and native binding names retain their established spelling.
# pylint: disable-next=invalid-name
TIn = TypeVar("TIn")
# pylint: disable-next=invalid-name
TOut = TypeVar("TOut")
StepInput = TypeVar("StepInput", contravariant=True)
StepOutput = TypeVar("StepOutput", covariant=True)


class Effect(str, Enum):
    """Categories of work declared by a step's contract."""

    PURE = "pure"
    QUERY = "query"
    PREPARE = "prepare"
    DISPATCH = "dispatch"
    RESOURCE = "resource"


@dataclass(frozen=True)
class Contract:
    """Describe a step's data boundary, effects, and required facts.

    Attributes:
        input_type: Input descriptor used in runtime diagnostics.
        output_type: Output descriptor used in runtime diagnostics.
        effects: Declared work categories, frozen on construction.
        requires: Facts the caller must establish before this step.
        writes: Local fields or resources this step may update.
        failures: Failure conditions documented for this step.
    """

    input_type: Any
    output_type: Any
    effects: AbstractSet[Effect] = frozenset({Effect.PURE})
    requires: tuple[str, ...] = ()
    writes: tuple[str, ...] = ()
    failures: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "effects", frozenset(self.effects))


@dataclass(frozen=True)
class Event:
    """One diagnostic event emitted along the declared flow path.

    Attributes:
        path: Diagnostic path through the declared flow nodes.
        phase: Diagnostic phase such as enter, exit, error, or cleanup.
        effects: Declared side-effect categories for diagnostics and composition.
        error_type: Exception class name, or None for an event without an error.
        call_id: Original call identity shared by every nested stage.
        attempt: Attempt number supplied by the enclosing Repeat or caller.
        branch: Label of the selected branch.
        cleanup: Reported cleanup progress, when this event describes native resource release.
        dispatch: Reported input-dispatch progress, independent of visible effect confirmation.
    """

    path: tuple[str, ...]
    phase: str
    effects: AbstractSet[Effect]
    error_type: str | None = None
    call_id: str | None = None
    attempt: int | None = None
    branch: str | int | bool | None = None
    cleanup: str | None = None
    dispatch: str | None = None


@dataclass(frozen=True)
class Control:
    """Carry immutable call metadata through nested steps.

    Child controls extend the diagnostic path or update attempt and branch
    labels. The call owner supplies budget and cancellation callbacks, which
    individual stages consult when needed.

    Attributes:
        remaining: Callback returning milliseconds left on the call; may raise
            on expiration.
        cancelled: Optional cancellation predicate supplied by the call owner.
        diagnostic: Optional event sink whose exceptions are isolated from execution.
        path: Diagnostic path through the declared flow nodes.
        call_id: Original call identity shared by every nested stage.
        attempt: Attempt number supplied by the enclosing Repeat or caller.
        branch: Label of the selected branch.
    """

    remaining: Callable[[], int] | None = None
    cancelled: Callable[[], bool] | None = None
    diagnostic: Callable[[Event], None] | None = None
    path: tuple[str, ...] = ()
    call_id: str | None = None
    attempt: int | None = None
    branch: str | int | bool | None = None

    def child(self, name: str) -> Control:
        return replace(self, path=(*self.path, name))

    def with_remaining(self, remaining: Callable[[], int]) -> Control:
        """Use a component budget while retaining the enclosing call metadata."""
        return replace(self, remaining=remaining)

    def with_attempt(self, attempt: int) -> Control:
        return replace(self, attempt=attempt)

    def with_branch(self, branch: Hashable) -> Control:
        # Use type names for complex keys to keep diagnostics free of payload data.
        label = branch if type(branch) in (str, int, bool) else type(branch).__name__
        return replace(self, branch=label)

    def emit(
        self,
        phase: str,
        effects: AbstractSet[Effect],
        error: BaseException | None = None,
        *,
        cleanup: str | None = None,
        dispatch: str | None = None,
    ) -> None:
        """Send a lifecycle event to the optional diagnostic sink.

        Args:
            phase: Diagnostic phase such as enter, exit, error, or cleanup.
            effects: Declared side-effect categories for diagnostics and composition.
            error: Failure whose class name is included in the event.
            cleanup: Reported cleanup progress, when this event describes native resource
                release.
            dispatch: Reported input-dispatch progress, independent of visible effect
                confirmation.
        """
        if self.diagnostic is not None:
            try:
                self.diagnostic(
                    Event(
                        self.path,
                        phase,
                        effects,
                        type(error).__name__ if error else None,
                        self.call_id,
                        self.attempt,
                        self.branch,
                        cleanup,
                        dispatch,
                    )
                )
            except BaseException:
                # Diagnostics are best-effort and separate from the step result.
                pass


class Step(Protocol[StepInput, StepOutput]):
    """Common interface for typed steps with sync and async execution methods."""

    @property
    def name(self) -> str:
        pass

    @property
    def contract(self) -> Contract:
        pass

    def run(self, value: StepInput, control: Control | None = None) -> StepOutput:
        pass

    async def arun(self, value: StepInput, control: Control | None = None) -> StepOutput:
        pass


class Pipeline(Generic[T, U], ABC):
    """Compose and execute a reusable flow of typed steps.

    Subclasses implement ``build()`` to combine passes and child pipelines with
    Sequence, Select, Repeat, and Scope. After binding their dependencies, they
    supply the resulting root to the constructor. ``run()`` and ``arun()``
    execute that root using its name and contract.

    Attributes:
        root: Retained step defining the flow's execution and data boundary.
    """

    def __init__(self, *, root: Step[T, U]) -> None:
        self._root = root

    @abstractmethod
    def build(self) -> Step[T, U]:
        """Compose the root step using the subclass's initialized dependencies.

        Returns:
            A step representing the flow's order, branches, and resource scopes.
        """
        raise NotImplementedError

    @property
    def root(self) -> Step[T, U]:
        return self._root

    @property
    def name(self) -> str:
        return self._root.name

    @property
    def contract(self) -> Contract:
        return self._root.contract

    def run(self, value: T, control: Control | None = None) -> U:
        """Execute the retained root synchronously with the supplied input."""
        return self._root.run(value, control)

    async def arun(self, value: T, control: Control | None = None) -> U:
        """Execute the retained root asynchronously with the supplied input."""
        return await self._root.arun(value, control)


def _sync(value: T) -> T:
    """Accept a synchronous result, closing accidental coroutines before refusal.

    Raises:
        TypeError: The handler returned an awaitable and requires ``arun()``.
    """
    if isawaitable(value):
        close = getattr(value, "close", None)
        if close is not None:
            close()
        raise TypeError("An asynchronous stage requires arun()")
    return value


async def _await(value: T | Awaitable[T]) -> T:
    """Resolve a handler result, including custom awaitable implementations."""
    return await cast(Awaitable[T], value) if isawaitable(value) else cast(T, value)


class Node(Generic[T, U]):
    """Base execution node that traces entry, completion, and errors.

    Subclasses implement ``_run()`` and ``_arun()`` for their execution behavior.
    Public methods add the node's name to the control path and emit events
    around those hooks.
    """

    if TYPE_CHECKING:

        @abstractmethod
        def _run(self, value: T, control: Control) -> U:
            pass

        @abstractmethod
        async def _arun(self, value: T, control: Control) -> U:
            pass

    def __init__(self, name: str, contract: Contract) -> None:
        self.name, self.contract = name, contract

    def run(self, value: T, control: Control | None = None) -> U:
        """Execute the synchronous hook and report its lifecycle events.

        Args:
            value: Input accepted by this node's handler.
            control: Enclosing call metadata; omitted for a standalone execution.

        Returns:
            The handler's output.
        """
        current = (control or Control()).child(self.name)
        current.emit("enter", self.contract.effects)
        try:
            result = self._run(value, current)
        except BaseException as error:
            current.emit("error", self.contract.effects, error)
            raise
        current.emit("exit", self.contract.effects)
        return result

    async def arun(self, value: T, control: Control | None = None) -> U:
        """Await the asynchronous hook and report its lifecycle events.

        Args:
            value: Input accepted by this node's handler.
            control: Enclosing call metadata; omitted for a standalone execution.

        Returns:
            The handler's output.
        """
        current = (control or Control()).child(self.name)
        current.emit("enter", self.contract.effects)
        try:
            result = await self._arun(value, current)
        except BaseException as error:
            current.emit("error", self.contract.effects, error)
            raise
        current.emit("exit", self.contract.effects)
        return result


class Pass(Node[T, U]):
    """Execute a bound processing function as a typed pipeline step.

    The handler receives stage input and call metadata and produces the output
    consumed by the caller or next step. Node supplies lifecycle tracing;
    ``run()`` uses a synchronous handler and ``arun()`` resolves a synchronous
    or asynchronous handler.

    Attributes:
        function: Handler receiving the stage input and current Control.
        name: Diagnostic stage name inherited from Node.
        contract: Declared input, output, effects, and prerequisites.
    """

    def __init__(self, name: str, function: Callable[[T, Control], U | Awaitable[U]], contract: Contract) -> None:
        super().__init__(name, contract)
        self.function = function

    @staticmethod
    def define(
        *, name: str, function: Callable[[TIn, Control], TOut | Awaitable[TOut]], contract: Contract
    ) -> PassDefinition[[], TIn, TOut]:
        """Declare a handler that is ready to bind directly to a Pass."""
        return PassDefinition(name, lambda: function, contract)

    @staticmethod
    def factory(
        *,
        name: str,
        factory: Callable[P, Callable[[TIn, Control], TOut | Awaitable[TOut]]],
        contract: Contract,
    ) -> PassDefinition[P, TIn, TOut]:
        """Declare a dependency factory whose result becomes the Pass handler."""
        return PassDefinition(name, factory, contract)

    def _run(self, value: T, control: Control) -> U:
        return cast(U, _sync(self.function(value, control)))

    async def _arun(self, value: T, control: Control) -> U:
        return await _await(self.function(value, control))


@dataclass(frozen=True)
class PassDefinition(Generic[P, T, U]):
    """Reusable Pass declaration retaining its dependency factory's signature.

    Calling the definition binds the supplied dependencies and creates a Pass.
    The signature property describes those dependencies for explicit inspection.

    Attributes:
        name: Stage name included in diagnostic paths.
        factory: Function binding dependencies to a processing handler.
        contract: Input, output, effects, and prerequisites attached to each Pass.
    """

    name: str
    factory: Callable[P, Callable[[T, Control], U | Awaitable[U]]]
    contract: Contract

    @property
    def signature(self) -> Signature:
        """Describe dependency parameters without binding or executing the factory."""
        return inspect_signature(self.factory)

    def __call__(self, *args: P.args, **kwargs: P.kwargs) -> Pass[T, U]:
        return Pass(self.name, self.factory(*args, **kwargs), self.contract)


def _composite(steps: Iterable[Step[Any, Any]], *, resource: bool = False) -> Contract:
    effects = frozenset(effect for step in steps for effect in step.contract.effects)
    if resource:
        effects |= {Effect.RESOURCE}
    return Contract(object, object, effects)


class Sequence(Node[T, U]):
    """Run child steps in order, passing each output to the next step.

    An empty sequence returns its input. The contract combines the effects
    declared by all children.
    """

    def __init__(
        self,
        name: str,
        *positional_steps: Step[Any, Any],
        steps: Iterable[Step[Any, Any]] | None = None,
    ) -> None:
        """Store the ordered child steps and their combined contract.

        Args:
            name: Diagnostic name for the sequence.
            steps: Ordered child steps, supplied as an iterable.
            *positional_steps: Ordered child steps, mutually exclusive with steps=.
        """
        if steps is not None and positional_steps:
            raise TypeError("Use either positional steps or steps=, not both")
        self.steps = tuple(positional_steps if steps is None else steps)
        super().__init__(name, _composite(self.steps))

    def _run(self, value: Any, control: Control) -> U:
        for step in self.steps:
            value = step.run(value, control)
        return value

    async def _arun(self, value: Any, control: Control) -> U:
        for step in self.steps:
            value = await step.arun(value, control)
        return value


class Select(Node[T, U]):
    """Use a pure selector to choose the branch receiving the original input.

    The selector returns a key from ``branches``. Each branch may accept its own
    request type; the enclosing Pipeline declares the overall input and output.
    """

    def __init__(self, name: str, selector: Step[T, Hashable], branches: Mapping[Hashable, Step[Any, Any]]) -> None:
        if selector.contract.effects - {Effect.PURE}:
            raise ValueError("A branch selector must be pure")
        self.selector, self.branches = selector, dict(branches)
        super().__init__(name, _composite((selector, *self.branches.values())))

    def _run(self, value: T, control: Control) -> U:
        key = self.selector.run(value, control)
        selected = control.with_branch(key)
        selected.emit("branch", self.contract.effects)
        return self.branches[key].run(value, selected)

    async def _arun(self, value: T, control: Control) -> U:
        key = self.selector.run(value, control)
        selected = control.with_branch(key)
        selected.emit("branch", self.contract.effects)
        return await self.branches[key].arun(value, selected)


@dataclass(frozen=True)
class Continue(Generic[T]):
    """Gate decision requesting another iteration with a new body input.

    Attributes:
        value: Input to the next body execution.
        reason: Evidence passed to the decision callback.
    """

    value: T
    reason: object | None = None


@dataclass(frozen=True)
class Done(Generic[U]):
    """Gate decision ending repetition and returning the final output.

    Attributes:
        value: Final result returned by Repeat.
        reason: Evidence passed to the decision callback.
    """

    value: U
    reason: object | None = None


class Repeat(Node[T, V]):
    """Repeat a body until its pure Gate returns Done.

    After each body execution, the Gate produces Continue or Done and the
    optional decision callback updates domain-owned attempt state. Repetition
    uses an iteration limit or a named budget enforced by the domain stages.
    """

    # The constructor ties the body's output type to the Gate's input type.
    body: Step[T, Any]
    gate: Step[Any, Continue[T] | Done[V]]
    advance: Callable[[Any, Continue[T] | Done[V], Control], None] | None

    def __init__(
        self,
        name: str,
        body: Step[T, U],
        gate: Pass[U, Continue[T] | Done[V]],
        advance: Callable[[U, Continue[T] | Done[V], Control], None] | None = None,
        *,
        on_decision: Callable[[U, Continue[T] | Done[V], Control], None] | None = None,
        max_iterations: int | None = None,
        bounded_by: str | None = None,
    ) -> None:
        """Bind the body, decision Gate, and repetition bound.

        Args:
            name: Diagnostic name for the repetition.
            body: Flow producing the result inspected by the Gate.
            gate: Pure Pass returning Continue with the next input or Done with
                the final result.
            advance: Synchronous callback receiving the body result, decision,
                and current Control after each Gate evaluation.
            on_decision: Keyword form of ``advance``; supply one of the two.
            max_iterations: Maximum body executions, or None for a domain budget.
            bounded_by: Name of the domain budget when no iteration limit is set.
        """
        if advance is not None and on_decision is not None:
            raise TypeError("Use either advance or on_decision=, not both")
        if on_decision is not None:
            advance = on_decision
        if gate.contract.effects - {Effect.PURE}:
            raise ValueError("A repetition Gate must be pure")
        # Exact types preserve scalar validation and identity contracts.
        # pylint: disable-next=unidiomatic-typecheck
        if max_iterations is not None and (type(max_iterations) is not int or max_iterations < 1):
            raise ValueError("max_iterations must be a positive integer")
        if max_iterations is None and not bounded_by:
            raise ValueError("Repeat requires a finite iteration limit or an existing domain budget")
        self.body, self.gate, self.advance = body, gate, advance
        self.max_iterations, self.bounded_by = max_iterations, bounded_by
        super().__init__(name, _composite((body, gate), resource=advance is not None))

    def _decision(self, output: Any, control: Control, iteration: int) -> Continue[T] | Done[V]:
        """Evaluate the Gate, notify its owner, and enforce the iteration bound."""
        decision = self.gate.run(output, control)
        if not isinstance(decision, (Continue, Done)):
            raise TypeError("A Gate must return Continue or Done")
        if self.advance is not None:
            _sync(self.advance(output, decision, control))
        if isinstance(decision, Continue) and self.max_iterations is not None and iteration >= self.max_iterations:
            raise RuntimeError("Pipeline repetition limit exhausted")
        return decision

    def _run(self, value: T, control: Control) -> V:
        iteration = 0
        while True:
            iteration += 1
            attempt = control.with_attempt(iteration)
            attempt.emit("attempt", self.contract.effects)
            decision = self._decision(self.body.run(value, attempt), attempt, iteration)
            if isinstance(decision, Done):
                return decision.value
            value = decision.value

    async def _arun(self, value: T, control: Control) -> V:
        iteration = 0
        while True:
            iteration += 1
            attempt = control.with_attempt(iteration)
            attempt.emit("attempt", self.contract.effects)
            decision = self._decision(await self.body.arun(value, attempt), attempt, iteration)
            if isinstance(decision, Done):
                return decision.value
            value = decision.value


class Scope(Node[T, U]):
    """Run a child flow inside a context manager's resource lifetime.

    The factory receives the incoming value and Control. Its entered value
    becomes the child's input; context exit performs cleanup on success or
    failure. ``run()`` uses a sync context manager; ``arun()`` supports both
    sync and async context managers.
    """

    def __init__(
        self,
        name: str,
        factory: (Callable[[T, Control], AbstractContextManager[Any] | AbstractAsyncContextManager[Any]] | None) = None,
        body: Step[Any, U] | None = None,
        *,
        using: (Callable[[T, Control], AbstractContextManager[Any] | AbstractAsyncContextManager[Any]] | None) = None,
    ) -> None:
        """Bind resource acquisition and the child flow using those resources.

        Args:
            name: Diagnostic name for the resource scope.
            factory: Function creating the context manager for each execution.
            body: Child flow receiving the context manager's entered value.
            using: Keyword form of ``factory``; supply one of the two.
        """
        if factory is not None and using is not None:
            raise TypeError("Use either factory or using=, not both")
        if using is not None:
            factory = using
        if factory is None or body is None:
            raise TypeError("Scope requires a resource factory and a body")
        self.factory, self.body = factory, body
        super().__init__(name, _composite((body,), resource=True))

    def _run(self, value: T, control: Control) -> U:
        with cast("AbstractContextManager[Any]", self.factory(value, control)) as scoped:
            return self.body.run(scoped, control)

    async def _arun(self, value: T, control: Control) -> U:
        manager = self.factory(value, control)
        if hasattr(manager, "__aenter__"):
            async with cast("AbstractAsyncContextManager[Any]", manager) as scoped:
                return await self.body.arun(scoped, control)
        with cast("AbstractContextManager[Any]", manager) as scoped:
            return await self.body.arun(scoped, control)
