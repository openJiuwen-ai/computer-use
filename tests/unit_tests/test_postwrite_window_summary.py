# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Post-write catalogue facts must survive delivery without claiming a menu's outcome."""

import asyncio
from unittest.mock import AsyncMock

import pytest
from computer_use.interaction.read.presentation.document import DisplayDocument
from computer_use.interaction.read.views.pipeline import InterfaceReadPipeline, member_target
from computer_use.interaction.read.views.publication import related_window_summary
from computer_use.interaction.read.views.related import RelatedRead
from computer_use.session.interfaces import WindowIdentity
from test_d34_read_contracts import directory, setup, window
from test_dynamic_observation import deliver_through_runtime, parent_result


def summary(before, after, aliases, **options):
    _, _, state = setup(before)
    related = RelatedRead(
        member_target(state, before["surfaces"][0]),
        tuple(WindowIdentity.of(item) for item in before["surfaces"]),
    )
    return DisplayDocument.join((related_window_summary(related, after, aliases, **options),)).text


@pytest.mark.parametrize("reasons, complete, expected", [
    ((), True, "complete"),
    (("AX discovery timed out",), False, "partial"),
    (("attached surface enumeration failed",), True, "partial"),
    ((), None, "unconfirmed"),
])
def test_absence_summary_distinguishes_directory_coverage_without_claiming_menu_failure(reasons, complete, expected):
    parent = window("A")
    before = directory(parent)
    after = {**directory(parent, reasons=reasons), "rootDiscoveryComplete": complete}
    text = summary(before, after, {17: "A"})
    assert f"no newly observed window entries; catalogue {expected}" in text
    assert "menu opened" not in text
    assert "menu did not open" not in text


def test_reappeared_retained_popup_is_not_reported_as_a_created_window():
    parent, popup = window("A"), window("Popup")
    # Popup's public ref already existed before hiding, but the immediately
    # pre-action catalogue contained only the parent. Its return is new evidence.
    text = summary(directory(parent), directory(parent, popup), {17: "A", 29: "Popup"})
    assert "newly observed window entries: window=29" in text
    assert "created" not in text
    still_known = summary(directory(parent, popup), directory(parent, popup), {17: "A", 29: "Popup"})
    assert "no newly observed window entries" in still_known


def test_menu_catalogue_entry_is_not_silently_omitted_and_failed_refresh_is_partial():
    parent, popup = window("A"), {**window("Menu"), "kind": "menu"}
    text = summary(directory(parent), directory(parent, popup), {17: "A", 29: "Menu"}, refresh_incomplete=True)
    assert "window=29" in text and "catalogue partial" in text


def test_replacement_native_identity_is_a_new_observation_not_the_old_window():
    parent = window("A")
    replacement = {**parent, "instance": "replacement", "surface": "replacement-surface"}
    text = summary(directory(parent), directory(replacement), {29: "A"})
    assert "newly observed window entries: window=29" in text


def test_same_window_postwrite_summary_reaches_actual_mcp_text():
    parent = window("A")
    before = directory(parent)
    controller, app, state = setup(before)
    controller.reader.read = AsyncMock(return_value=parent_result(state, parent, before, before))
    related = RelatedRead(member_target(state, parent), (WindowIdentity.of(parent),))
    events = []
    result = asyncio.run(InterfaceReadPipeline(controller)._collect(
        app, "tree", False, None, before, None, None, related, on_observation=events.append,
    ))
    delivered = deliver_through_runtime(events)
    assert not delivered.get("isError"), delivered
    received = [block["text"] for block in delivered["content"] if block["type"] == "text"]
    assert "Parent content" in result.text
    assert "After action: no newly observed window entries; catalogue complete" in "\n".join(received)
