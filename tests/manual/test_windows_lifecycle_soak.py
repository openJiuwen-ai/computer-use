# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from types import SimpleNamespace

import pytest
import windows_lifecycle_soak as soak


def fake_census(monkeypatch, frames):
    frames = iter(frames)
    calls, records = [], []
    sampler = object.__new__(soak.Sampler)
    sampler.census = None
    sampler.record_census = records.append

    def query(job, info_class, pointer, size, size_returned):
        state = pointer._obj
        assert (job, info_class) == (17, 3)
        assert state.assigned == state.returned == state.pids[0] == 0
        frame = next(frames)
        assigned, returned, pids, winerror = frame
        state.assigned, state.returned = assigned, returned
        state.pids[: len(pids)] = pids
        calls.append(frame)
        return not winerror

    monkeypatch.setattr(soak.time, "sleep", lambda _: None)
    monkeypatch.setattr(soak.ctypes, "get_last_error", lambda: calls[-1][3], raising=False)
    monkeypatch.setattr(soak.ctypes, "WinError", OSError, raising=False)
    sampler.kernel = SimpleNamespace(QueryInformationJobObject=query)
    return sampler, calls, records


@pytest.mark.parametrize("partial", [(), (999,)])
def test_recovers_only_from_a_fresh_complete_census(monkeypatch, partial):
    frames = [(2, len(partial), partial, 0), (2, 2, (41, 42), 0)]
    sampler, calls, records = fake_census(monkeypatch, frames)

    assert sampler.pids(17) == {41, 42}
    assert calls == frames
    assert records == [
        {"attempt": 1, "assigned": 2, "returned": len(partial), "winerror": None, "status": "incomplete"},
        {"attempt": 2, "assigned": 2, "returned": 2, "winerror": None, "status": "complete"},
    ]


def test_incomplete_empty_census_refuses_after_bounded_attempts(monkeypatch):
    frames = [(1, 0, (), 0)] * 4
    sampler, calls, records = fake_census(monkeypatch, frames)

    with pytest.raises(RuntimeError, match=r"incomplete.*assigned=1, returned=0"):
        sampler.pids(17)
    assert calls == frames
    assert [row["attempt"] for row in records] == [1, 2, 3, 4]
    assert all(row["status"] == "incomplete" for row in records)


def test_fresh_successful_zero_counts_prove_empty(monkeypatch):
    frames = [(1, 0, (), 0), (0, 0, (), 0)]
    sampler, calls, records = fake_census(monkeypatch, frames)

    assert sampler.pids(17) == set()
    assert calls == frames
    assert records[-1] == {"attempt": 2, "assigned": 0, "returned": 0, "winerror": None, "status": "complete"}


@pytest.mark.parametrize(
    ("frame", "status", "error"),
    [
        ((1, 2, (41, 42), 0), "invalid_counts", RuntimeError),
        ((4097, 2, (41, 42), 0), "capacity_exceeded", RuntimeError),
        ((2, 2, (41, 41), 0), "invalid_pids", RuntimeError),
        ((1, 1, (0,), 0), "invalid_pids", RuntimeError),
        ((0, 0, (), 5), "native_error", OSError),
    ],
)
def test_malformed_or_native_failure_is_never_retried(monkeypatch, frame, status, error):
    sampler, calls, records = fake_census(monkeypatch, [frame])

    with pytest.raises(error):
        sampler.pids(17)
    assert calls == [frame]
    assert records == [
        {
            "attempt": 1,
            "assigned": frame[0],
            "returned": frame[1],
            "winerror": frame[3] or None,
            "status": status,
        }
    ]
