"""One bounded native operation, one selected target, one input barrier."""

from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
from dataclasses import dataclass, field

from computer_use_linux.contracts.protocol import Failure, require, token


@dataclass
class NativeOperation:
    target: dict
    deadline: float
    intent: dict
    writable: bool = False
    token: str = field(default_factory=lambda: token("operation"))
    release: object = None
    closed: bool = False
    requested: dict | None = None
    within: tuple = ()
    dispatched: bool = False
    cleanup: str = "complete"
    selection_facts: object = None
    contracts: dict = field(default_factory=dict)
    steps: OrderedDict = field(default_factory=OrderedDict)
    high_step: int = 0
    background_unavailable: set = field(default_factory=set)
    click_contract: str | None = None
    click_attempt: object = None

    def contract(self, unit, target, within, **native):
        identifier = token("primitive")
        self.contracts[identifier] = dict(unit=unit, target=dict(target), within=tuple(within), **native)
        # Retain a complete context: up to 128 native actions plus input
        # channels. Every primitive in the latest reply must stay executable.
        while len(self.contracts) > 256:
            self.contracts.pop(next(iter(self.contracts)))
        return identifier

    def bind(self, target, within, store):
        require(
            self.writable and not self.closed and not self.dispatched,
            "The operation cannot acquire another input contract.",
            "invalid_request",
        )
        require(
            all(target[key] == self.target[key] for key in self.target if key != "node")
            and (self.target["node"] is None or self.target == target)
            and (target["node"] is not None or not within),
            "Input target is outside the original request.",
            "stale_target",
        )
        if self.requested is not None:
            require(
                self.requested == target and self.within == tuple(within),
                "The operation already selected another control or scope.",
                "stale_target",
            )
        else:
            # The caller already checked the exact native parent path. Pinning
            # retains references; it does not freeze the application or focus.
            self.requested, self.within = dict(target), tuple(within)
            store.pins.update(ref for ref in (target["node"], *within) if ref is not None)

    def step(self, number, command, execute):
        existing = self.steps.get(number)
        if existing is not None:
            require(existing[0] == command, "Step id reused with different input.", "request_conflict")
            return deepcopy(existing[1])
        require(number > self.high_step, "Step result expired; input will not be repeated.", "request_conflict")
        if self.closed or self.dispatched:
            raise Failure(
                "This operation already ended or dispatched input.",
                "execution_unknown",
                effect="possible" if self.dispatched else "none",
            )
        self.high_step = number
        result = execute()
        self.steps[number] = (deepcopy(command), deepcopy(result))
        while len(self.steps) > 32:
            self.steps.popitem(last=False)
        return result

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.contracts.clear()
        if self.selection_facts is not None:
            self.selection_facts.clear()
        if self.release is not None:
            release, self.release = self.release, None
            release()
