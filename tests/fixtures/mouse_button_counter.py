# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Read mouse-down counters only; no event tap, input posting or keyboard capture."""

import ctypes
import time
from typing import ClassVar


class MouseButtonCounters:
    """Journal adjacent counter changes with their actual sampling bounds.

    CoreGraphics counts are evidence of events, not their source or exact event
    timestamp. Native SDK input diagnostics and Qt events supply that context.
    Movement cannot increment these three mouse-button-down event counters.
    """

    STATES: ClassVar = {"combined_session": 0, "hid_system": 1}
    EVENTS: ClassVar = {"left_down": 1, "right_down": 3, "other_down": 25}

    def __init__(self, read=None, clock=time.monotonic):
        self._library = None
        if read is None:
            self._library = ctypes.CDLL("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
            read = self._library.CGEventSourceCounterForEventType
            read.argtypes = [ctypes.c_int32, ctypes.c_uint32]
            read.restype = ctypes.c_uint32
        self._read, self._clock = read, clock
        self._counts = None
        self._sampled_at = None
        self.changes = []

    def sample(self, reason="publish"):
        sampled_at = self._clock()
        current = {
            state: {event: int(self._read(source, kind)) for event, kind in self.EVENTS.items()}
            for state, source in self.STATES.items()
        }
        delta = {
            state: {
                event: (count - self._counts[state][event]) & 0xFFFFFFFF
                for event, count in counters.items()
                if self._counts is not None and count != self._counts[state][event]
            }
            for state, counters in current.items()
        }
        delta = {state: counters for state, counters in delta.items() if counters}
        if self._counts is None or delta:
            self.changes.append({
                "sequence": len(self.changes) + 1,
                "reason": reason,
                "monotonic": sampled_at,
                "sampled_between": [self._sampled_at, sampled_at],
                "counts": current,
                "delta": delta,
            })
        self._counts, self._sampled_at = current, sampled_at
