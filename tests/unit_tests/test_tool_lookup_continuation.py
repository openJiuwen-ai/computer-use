"""A returned SDK observation can guide the rest of its MCP script without being delivered yet."""

import asyncio
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

from computer_use.client.host import (
    set_action_handler,
    set_diagnostic_handler,
    set_execution_guard,
    set_observation_handler,
    set_preview_handler,
    set_text_page_handler,
)
from computer_use.errors import ComputerUseError
from computer_use.interaction.query.instances.scope import adopt
from computer_use.interaction.read.views.pipeline import WindowPart, member_target, remember
from computer_use.interaction.read.views.publication import publish
from computer_use.session import images as image_state
from unit_tests.test_app_current_scope_recovery import rendered_tree
from unit_tests.test_application_window_lifetimes import directory, setup, surface


def prepared_app():
    form, confirmation = surface("form"), surface("confirmation")
    controller, app = setup(form, confirmation)
    queries = []

    def observe(key):
        current = directory(form, confirmation, focus=key)
        adopt(controller, app, current)
        target = member_target(app._instance, app._instance.members[key])
        remember(controller, app, WindowPart(target))
        app._context = key
        tree, document = rendered_tree(target)
        return publish(controller, app, document, directory=current, trees=((target, tree),))

    async def lookup(request, _control):
        target = controller._query_root(request.owner)
        queries.append(target.root_window)
        if target.root_window != "confirmation":
            raise ComputerUseError({"code": "element_not_found", "detail": "not in form", "effect": "none", "target": "form"})
        return {"binding": replace(target, node="delete")}

    controller.interfaces.lookup = SimpleNamespace(arun=AsyncMock(side_effect=lookup))
    return controller, app, observe, queries


def test_same_tool_returned_confirmation_is_lookup_scope_without_early_delivery():
    from computer_use_mcp.runtime import Runtime

    async def run():
        controller, app, observe, queries = prepared_app()
        observe("form")
        original = app._instance.lookup_context
        generation = app._instance.delivered_observation_generation
        old_image = app._instance.delivered_image
        old_image_generation = app._instance.delivered_image_generation
        baseline = image_state._delivery_required
        runtime = Runtime(lambda _event: None)
        runtime.task_namespace.update(controller=controller, app=app, observe=observe, original=original,
                                      generation=generation, old_image=old_image,
                                      old_image_generation=old_image_generation)
        try:
            result = await runtime.execute({"executionId": "confirmation", "code": '''
import asyncio
observe("confirmation")
assert app._instance.lookup_context == original
assert app._instance.delivered_observation_generation == generation
assert app._instance.delivered_image is old_image
assert app._instance.delivered_image_generation == old_image_generation
found = asyncio.run(controller.interfaces.element(app, "Delete", 0, None))
assert found["binding"].root_window == "confirmation"
assert app._instance.lookup_context == original
assert app._instance.delivered_observation_generation == generation
'''})
            assert not result.get("isError"), result
            assert queries == ["confirmation"]
            assert app._instance.lookup_context.window.window == "confirmation"
            assert app._instance.delivered_observation_generation > generation
        finally:
            await runtime.close()
            for setter in (set_action_handler, set_diagnostic_handler, set_execution_guard,
                           set_observation_handler, set_preview_handler, set_text_page_handler):
                setter(None)
            image_state._delivery_required = baseline

    asyncio.run(run())


def test_lookup_continuation_is_per_app_and_cleared_in_copied_context_after_error():
    from contextvars import copy_context

    import pytest
    from computer_use.session.lookup_continuation import current_lookup_continuation, lookup_continuation_scope

    controller, app, observe, queries = prepared_app()
    other_controller, other, other_observe, other_queries = prepared_app()
    observe("form")
    other_observe("form")
    original = app._instance.lookup_context
    set_observation_handler(lambda _event: None)
    try:
        with pytest.raises(RuntimeError, match="script stopped"), lookup_continuation_scope():
            observe("confirmation")
            copied = copy_context()
            pending = current_lookup_continuation(app._instance)
            assert pending.context.window.window == "confirmation"
            assert current_lookup_continuation(other._instance) is None
            assert app._instance.lookup_context == original
            assert other._instance.lookup_context.window.window == "form"
            with pytest.raises(ComputerUseError, match="no matching element"):
                asyncio.run(other_controller.interfaces.element(other, "Delete", 0, None))
            assert other_queries == ["form"]
            raise RuntimeError("script stopped")
        assert current_lookup_continuation(app._instance) is None
        assert copied.run(current_lookup_continuation, app._instance) is None
        with pytest.raises(ComputerUseError, match="no matching element"):
            asyncio.run(controller.interfaces.element(app, "Delete", 0, None))
        assert queries == ["form"]
    finally:
        set_observation_handler(None)


def test_image_only_or_failed_publication_does_not_change_local_lookup():
    import pytest
    from computer_use.session.lookup_continuation import current_lookup_continuation, lookup_continuation_scope

    controller, app, observe, _queries = prepared_app()
    observe("form")
    set_observation_handler(lambda _event: None)
    try:
        with lookup_continuation_scope():
            observe("confirmation")
            expected = current_lookup_continuation(app._instance)
            app._context = "form"
            publish(controller, app, "image only", directory=app._instance.directory)
            assert current_lookup_continuation(app._instance) is expected

            def fail(_event):
                raise RuntimeError("publication failed")

            set_observation_handler(fail)
            with pytest.raises(RuntimeError, match="publication failed"):
                observe("form")
            assert current_lookup_continuation(app._instance) is expected
            app._instance.session = "restarted-session"
            assert current_lookup_continuation(app._instance) is None
    finally:
        set_observation_handler(None)
