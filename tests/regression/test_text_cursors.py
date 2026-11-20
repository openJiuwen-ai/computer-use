# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Continuation pages retain their captured text, source and element binding."""

import asyncio
from collections import deque
from types import SimpleNamespace

import pytest

from computer_use.client import result_presentation
from computer_use.errors import ComputerUseError
from computer_use.interaction.read.acquisition.reader import CapturedField
from computer_use.interaction.read.text import agent
from computer_use.session.cursors import TextCursors

ORIGINAL = {"observed": {"id": "old-observation"}, "ref": "7"}
FALLBACK = {"observed": {"id": "new-observation"}, "ref": "9"}
TEXT = "A🙂\ud800BC"


def test_captured_text_source_label_and_client_page_delivery_remain_exact(monkeypatch):
    source = {"observed": {"id": 'capture"\ud800'}, "ref": 0}
    expected = 'Captured text: element 0; observation="capture\\"\\ud800"; snapshot, not a live read.'
    assert result_presentation.text_source_label(source) == expected
    assert result_presentation.text_source_label({}) == (
        "Captured text: query result; observation=unknown; snapshot, not a live read."
    )

    class Page(dict):
        def __repr__(self):
            return "page-object"

    published = []
    monkeypatch.setattr(
        result_presentation, "text_page_completed", lambda evidence, page: published.append((evidence, page))
    )
    page = Page(text=TEXT, next=None)
    result = result_presentation.captured_text(page, source)
    assert repr(result) == expected + "\n" + repr(dict(page))
    assert published == [(source, result)]
    assert published[0][0] is source and published[0][1] is result


def build(monkeypatch, fields, *, max_characters=128, capacity=128, native_pages=(), source=ORIGINAL):
    fields = deque(fields)
    native_pages = deque(native_pages)
    events = []
    requests = []

    class Element:
        def __init__(self, controller):
            assert controller.port == "port"

        async def collect(self, _ui, snapshot, *, max_nodes):
            requests.append(("collect", snapshot["binding"], max_nodes))
            return None, {"text": fields.popleft(), "source": source}

    class Capture:
        def __init__(self, port):
            assert port == "port"

        async def arun(self, request):
            requests.append((request.source.text_ref, request.offset, request.length))
            return native_pages.popleft()

    def diagnostic(evidence, page):
        events.append((evidence, page))

    monkeypatch.setattr(agent, "AgentElementReadPipeline", Element)
    monkeypatch.setattr(agent, "CapturedTextPipeline", Capture)
    monkeypatch.setattr(agent, "text_page_completed", diagnostic)
    controller = SimpleNamespace(
        port="port",
        interfaces=SimpleNamespace(validate_snapshot=lambda _ui, _snapshot: None),
    )
    cursors = TextCursors(capacity=capacity, max_characters=max_characters)
    pipeline = agent.AgentTextReadPipeline(controller, cursors)

    def read(cursor=None, *, binding="initial-window/element", length=2):
        return asyncio.run(pipeline.run(None, {"binding": binding, "source": FALLBACK}, cursor, length))

    return read, requests, events


def test_text_cursor_uses_original_code_points_and_capture_without_a_second_read(monkeypatch):
    read, requests, events = build(monkeypatch, [CapturedField("session", value=TEXT)], max_characters=5)
    first = read()
    second = read(first.next)
    third = read(second.next)
    replay = read(first.next)
    assert [item.text for item in [first, second, third, replay]] == ["A🙂", "\ud800B", "C", "\ud800B"]
    assert [item.total for item in [first, second, third, replay]] == [5, 5, 5, 5]
    assert first.next.startswith("text_") and len(first.next) == 37
    assert third.next is None
    assert 'element 7; observation="old-observation"' in repr(first)
    assert all(source is ORIGINAL for source, _ in events)
    assert requests == [("collect", "initial-window/element", 1)]


def test_text_cursor_binds_target_and_expires_oldest_when_distinct_text_needs_space(monkeypatch):
    duplicate = (TEXT + "!")[:-1]
    assert duplicate == TEXT and duplicate is not TEXT
    read, requests, _ = build(
        monkeypatch,
        [CapturedField("session", value=TEXT), CapturedField("session", value=duplicate)],
        max_characters=5,
    )
    first = read()
    second = read(first.next)
    with pytest.raises(ComputerUseError) as wrong_target:
        read(first.next, binding="different-window/element")
    assert wrong_target.value.code == "invalid_request"
    assert wrong_target.value.detail == "text cursor belongs to a different element or window"
    latest = read(binding="different-window/element")
    assert read(latest.next, binding="different-window/element").text == "\ud800B"
    for old in (first.next, second.next):
        with pytest.raises(ComputerUseError) as expired:
            read(old)
        assert expired.value.code == "snapshot_expired"
    assert len(requests) == 2


def test_text_cursor_keeps_fifo_and_falls_back_to_current_source_when_original_was_empty(monkeypatch):
    read, requests, events = build(monkeypatch, [CapturedField("session", value=TEXT)], capacity=1, source={})
    first = read()
    following = read(first.next)
    assert events[1][0] is FALLBACK
    assert 'element 9; observation="new-observation"' in repr(following)
    assert read(following.next).text == "C"
    for key, code in (
        ("relation_abc", "invalid_request"),
        ("text_\ud800", "snapshot_expired"),
        (first.next, "snapshot_expired"),
    ):
        with pytest.raises(ComputerUseError) as denied:
            read(key)
        assert denied.value.code == code
    assert len(requests) == 1


def test_native_text_pages_use_provider_offsets_then_public_code_point_positions(monkeypatch):
    read, requests, _ = build(
        monkeypatch,
        [CapturedField("session", text_ref="text-ref", total_units=5)],
        native_pages=[
            {"total": 5, "text": "A🙂", "next": 3},
            {"total": 5, "text": "\ud800Z", "next": None},
        ],
    )
    first = read(length=1)
    assert (first.text, first.total) == ("A", 4)
    assert read(first.next, length=4).text == "🙂\ud800Z"
    assert requests == [
        ("collect", "initial-window/element", 1),
        ("text-ref", 0, 16384),
        ("text-ref", 3, 16384),
    ]


@pytest.mark.parametrize(
    ("total", "native_page", "maximum", "code", "detail"),
    [
        (
            4,
            {"total": 5, "text": "hello", "next": None},
            3,
            "snapshot_expired",
            "captured text changed its declared range",
        ),
        (
            5,
            {"total": 5, "text": "hello", "next": 0},
            3,
            "observation_incomplete",
            "text exceeds the bounded public capture cache",
        ),
        (5, {"total": 5, "text": "", "next": 0}, 3, "snapshot_expired", "native text continuation made no progress"),
    ],
)
def test_invalid_native_text_page_preserves_error_and_never_publishes_partial_result(
    monkeypatch,
    total,
    native_page,
    maximum,
    code,
    detail,
):
    read, requests, events = build(
        monkeypatch,
        [CapturedField("session", text_ref="text-ref", total_units=total)],
        native_pages=[native_page],
        max_characters=maximum,
    )
    with pytest.raises(ComputerUseError) as failure:
        read()
    assert (failure.value.code, failure.value.detail, failure.value.effect) == (code, detail, "none")
    assert requests == [("collect", "initial-window/element", 1), ("text-ref", 0, 16384)]
    assert not events


def test_native_text_capture_does_not_narrow_provider_continuation_offsets(monkeypatch):
    next_offset = 2**72
    read, requests, _ = build(
        monkeypatch,
        [CapturedField("session", text_ref="text-ref", total_units=next_offset + 1)],
        native_pages=[
            {"total": next_offset + 1, "text": "前", "next": next_offset},
            {"total": next_offset + 1, "text": "后", "next": None},
        ],
    )
    page = read(length=16)
    assert (page.text, page.total, page.next) == ("前后", 2, None)
    assert requests[-1] == ("text-ref", next_offset, 16384)
