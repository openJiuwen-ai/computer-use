"""Public synchronous calls keep eager resolution, sequencing and cleanup."""

import asyncio
import inspect
import threading
from types import SimpleNamespace

import pytest
from computer_use import App, Computer, Element
from computer_use.client.computer import computer_for_controller
from computer_use.client.execution import ExecutionCancellation, SessionExecutor, execution_scope
from computer_use.errors import ComputerUseError
from computer_use.interaction.agent.requests import ActOnElement, ObserveUI, ResolveElement, UIKeyboard
from computer_use.interaction.requests import ComputerInput
from computer_use.session import operations
from computer_use.session.observation import CapturedTextDocument, LazyObservation, lazy_observation


class Transport:
    def __init__(self):
        self.close_events = []

    def close(self):
        self.close_events.append(("close", threading.get_ident()))

    async def wait_closed(self):
        await asyncio.sleep(0)
        self.close_events.append(("closed", threading.get_ident()))


class Controller:
    """The existing client/engine port, with no native desktop side effects."""

    def __init__(self):
        self.transport = Transport()
        self.current = object()
        self.resolutions = []
        self.actions = []
        self.contexts = []
        self.error = ComputerUseError(
            {
                "code": "element_not_found",
                "effect": "none",
                "target": "missing",
                "detail": "No match",
                "next": "Observe",
            }
        )

    def register(self, owner, scope):
        pass

    def state(self, owner):
        return SimpleNamespace(running=None)

    async def run(self, request):
        task = asyncio.current_task()
        before = (asyncio.get_running_loop(), task, threading.get_ident())
        await asyncio.sleep(0)
        after = (asyncio.get_running_loop(), asyncio.current_task(), threading.get_ident())
        self.contexts.append((before, after))
        if isinstance(request, ResolveElement):
            self.resolutions.append(request.target)
            if request.target == "missing":
                raise self.error
            return self.current
        if isinstance(request, ActOnElement):
            self.actions.append((request.snapshot, request.intent, request.parameters))
            return SimpleNamespace(status="dispatched", snapshot=request.snapshot)
        if isinstance(request, ObserveUI):
            return lazy_observation(CapturedTextDocument("Captured field"))
        raise AssertionError(type(request))


@pytest.fixture(autouse=True)
def standalone_guard(monkeypatch):
    # Other tests install host-only guards; standalone SDK calls have no host.
    monkeypatch.setattr(operations, "_guard", None)


def test_chain_executes_now_and_saved_element_keeps_original_binding():
    controller = Controller()
    caller = threading.get_ident()
    with computer_for_controller(controller) as computer:
        app = computer.app("Editor")
        original = controller.current
        field = app.element("Field")
        assert isinstance(field, Element)
        assert controller.resolutions == ["Field"]
        controller.current = object()
        replacement = controller.current

        receipt = field.type_text("value", replace=True)
        field.press_key("Return")
        newer = app.element("Field").click()
        state = app.read()

        assert receipt.status == "dispatched"
        assert receipt.snapshot is original
        assert newer.snapshot is replacement
        assert [entry[:2] for entry in controller.actions] == [
            (original, "replace"),
            (original, "key"),
            (replacement, "click"),
        ]
        assert controller.actions[0][2] == {"text": "value"}
        assert controller.resolutions == ["Field", "Field"]
        assert state.text == "Captured field"
        assert all(before == after for before, after in controller.contexts)
        assert len({before[0] for before, _ in controller.contexts}) == 1
        assert all(before[2] != caller for before, _ in controller.contexts)

    assert [event for event, _ in controller.transport.close_events] == ["close", "closed"]
    assert len({thread for _, thread in controller.transport.close_events}) == 1
    assert controller.transport.close_events[0][1] != caller


def test_operation_error_propagates_at_the_call_and_closed_handles_do_not_execute():
    controller = Controller()
    computer = computer_for_controller(controller)
    app = computer.app("Editor")
    with pytest.raises(ComputerUseError) as caught:
        app.element("missing").click()
    assert caught.value is controller.error
    assert caught.value.effect == "none"
    assert controller.actions == []
    computer.close()
    computer.close()
    with pytest.raises(RuntimeError, match="closed"):
        app.read()
    assert controller.transport.close_events == [
        ("close", controller.transport.close_events[0][1]),
        ("closed", controller.transport.close_events[0][1]),
    ]


@pytest.mark.parametrize("key, canonical", [
    ("Backspace", "BackSpace"), ("BackSpace", "BackSpace"), ("shift+ctrl+Backspace", "shift+ctrl+BackSpace"),
    ("super+tab", "super+Tab"), ("tAb", "Tab"), ("shift+page_down", "shift+Page_Down"), ("f12", "F12"),
    ("ctrl+a", "ctrl+a"),
])
def test_public_key_methods_canonicalize_named_keys_without_changing_the_scope(key, canonical):
    class RecordingController(Controller):
        async def run(self, request):
            if isinstance(request, ComputerInput):
                self.actions.append(("desktop", request.intent.kind, request.intent.parameters))
                return None
            if isinstance(request, UIKeyboard):
                self.actions.append((request.owner, request.method, request.parameters))
                return None
            return await super().run(request)

    controller = RecordingController()
    with computer_for_controller(controller) as computer:
        app = computer.app("Editor")
        field = app.element("Field")
        for target in (computer, app, field):
            target.press_key(key)
            for invalid in ("ctrl+ctrl+Backspace", "Super+tab", "ctrl+A", "super+tabs"):
                with pytest.raises(ComputerUseError) as error:
                    target.press_key(invalid)
                assert error.value.code == "invalid_request" and error.value.effect == "none"
    assert controller.actions == [
        ("desktop", "key", {"key": canonical}),
        (app, "press_key", {"key": canonical}),
        (controller.current, "key", {"key": canonical}),
    ]


def test_all_model_methods_are_synchronous_including_retained_details():
    for cls in (Computer, App, Element):
        for name, method in inspect.getmembers(cls, inspect.isfunction):
            if not name.startswith("_"):
                assert not inspect.iscoroutinefunction(method), (cls.__name__, name)
    assert not inspect.iscoroutinefunction(LazyObservation.read_details)
    observation = lazy_observation(CapturedTextDocument("captured details"))
    page = observation.read_details()
    assert not inspect.isawaitable(page)
    assert "captured details" in str(page)


def test_cancel_waits_for_async_cleanup_and_denies_later_submission():
    async def scenario():
        executor = SessionExecutor(loop=asyncio.get_running_loop())
        cancellation = ExecutionCancellation()
        entered = asyncio.Event()
        cleanup = []

        async def operation():
            entered.set()
            try:
                await asyncio.Future()
            finally:
                await asyncio.sleep(0.01)
                cleanup.append("complete")

        with execution_scope(executor, cancellation):
            worker = asyncio.create_task(asyncio.to_thread(executor.run, operation))
        await entered.wait()
        cancellation.cancel()
        cancellation.cancel()
        await executor.drain(cancellation)
        with pytest.raises(asyncio.CancelledError):
            await worker
        assert cleanup == ["complete"]
        submitted = []

        def later():
            with execution_scope(executor, cancellation):
                return executor.call(lambda: submitted.append(True))

        with pytest.raises(asyncio.CancelledError):
            await asyncio.to_thread(later)
        assert submitted == []
        await executor.aclose()

    asyncio.run(scenario())


def test_close_failure_keeps_retryable_cleanup_but_blocks_input():
    class FailedCleanup(Transport):
        def __init__(self):
            super().__init__()
            self.attempts = 0

        async def wait_closed(self):
            self.attempts += 1
            if self.attempts == 1:
                raise RuntimeError("cleanup temporarily unavailable")
            await super().wait_closed()

    async def scenario():
        executor = SessionExecutor(loop=asyncio.get_running_loop())
        controller = Controller()
        controller.transport = FailedCleanup()
        with execution_scope(executor):
            computer = computer_for_controller(controller)
            app = await asyncio.to_thread(computer.app, "Editor")
            with pytest.raises(RuntimeError, match="cleanup temporarily unavailable"):
                await asyncio.to_thread(computer.close)
            with pytest.raises(RuntimeError, match="closed"):
                await asyncio.to_thread(app.read)
            await asyncio.to_thread(computer.close)
            assert controller.transport.attempts == 2
            assert controller.transport.close_events[-1][0] == "closed"
        await executor.aclose()

    asyncio.run(scenario())


def test_submission_queued_before_close_cannot_start_during_cleanup(monkeypatch):
    """A queued producer must not pass an old caller-side admission decision."""

    async def scenario():
        loop = asyncio.get_running_loop()
        executor = SessionExecutor(loop=loop)
        cleanup_entered = asyncio.Event()
        release_cleanup = asyncio.Event()
        queued = threading.Event()
        dispatched = []
        errors = []

        class ClosingTransport(Transport):
            async def wait_closed(self):
                cleanup_entered.set()
                await release_cleanup.wait()

        controller = Controller()
        controller.transport = ClosingTransport()
        executor.register(controller)
        original_submit = asyncio.run_coroutine_threadsafe

        def submit(coroutine, owner):
            future = original_submit(coroutine, owner)
            queued.set()
            return future

        monkeypatch.setattr(asyncio, "run_coroutine_threadsafe", submit)

        def producer():
            try:
                executor.call(lambda: dispatched.append("input"))
            except RuntimeError as error:
                errors.append(error)

        close_task = asyncio.create_task(executor.aclose())
        thread = threading.Thread(target=producer)
        thread.start()
        assert queued.wait(1), "producer did not reach the scheduling boundary"
        await cleanup_entered.wait()
        # Allow the already-queued submission to reach its owner-loop check.
        for _ in range(3):
            await asyncio.sleep(0)
        release_cleanup.set()
        await close_task
        await asyncio.to_thread(thread.join, 1)
        assert not thread.is_alive()
        assert dispatched == []
        assert len(errors) == 1 and isinstance(errors[0], RuntimeError)

    asyncio.run(scenario())
