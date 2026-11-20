# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Fixture accounting stays mouse-down-only and records changes, not poll volume."""

import importlib.util
from pathlib import Path

MODULE = Path(__file__).parents[1] / "fixtures/mouse_button_counter.py"
SPEC = importlib.util.spec_from_file_location("mouse_button_counter_fixture", MODULE)
HELPER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(HELPER)


def test_only_changes_are_journaled_without_keyboard_or_movement_queries():
    calls = []

    def read(state, event):
        calls.append((state, event))
        return 7

    counter = HELPER.MouseButtonCounters(read=read)
    for _ in range(50):
        counter.sample()
    assert len(counter.changes) == 1 and counter.changes[0]["delta"] == {}
    assert set(calls) == {(state, event) for state in (0, 1) for event in (1, 3, 25)}


def test_click_count_records_adjacent_delta_and_sampling_bound():
    values = {}
    clock = iter((1.0, 2.0, 3.0))
    counter = HELPER.MouseButtonCounters(read=lambda s, e: values.get((s, e), 0), clock=lambda: next(clock))
    counter.sample("initial")
    counter.sample()
    values[0, 1] = values[1, 1] = 1
    counter.sample("qt:Hide")
    assert len(counter.changes) == 2
    changed = counter.changes[-1]
    assert changed["sampled_between"] == [2.0, 3.0]
    assert changed["reason"] == "qt:Hide"
    assert changed["delta"] == {"combined_session": {"left_down": 1}, "hid_system": {"left_down": 1}}


def test_counter_wrap_is_a_single_event_not_a_negative_count():
    value = 0xFFFFFFFF
    counter = HELPER.MouseButtonCounters(read=lambda _s, _e: value)
    counter.sample()
    value = 0
    counter.sample()
    assert all(delta == 1 for changes in counter.changes[-1]["delta"].values() for delta in changes.values())
