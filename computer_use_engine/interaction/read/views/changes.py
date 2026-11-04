# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""One event-assisted suffix, retaining the first capture and original deadline."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from computer_use.diagnostics import diagnostic
from computer_use.interaction.read.acquisition.content import has_content_progress
from computer_use.session.operations import current_operation_budget

if TYPE_CHECKING:
    from collections.abc import Mapping

    from computer_use.interaction.read.facts.snapshot import SemanticSnapshot
    from computer_use.protocol.png import PNG


@dataclass(frozen=True)
class PostwriteChanges:
    """Captured pre-input content and an optional pre-armed native watcher."""

    baseline: Mapping[str, str] | None
    before_tree: SemanticSnapshot | None = None
    before_image: PNG | None = None
    input_sampled: bool = False

    def needs_observation(self, part):
        """Unknown capture coverage cannot discharge an unconfirmed write's event debt.

        This is advisory read scheduling, not identity equivalence or input
        permission. Only positive UI-content progress suppresses the suffix.
        The native wait independently validates the exact App incarnation.
        """
        if part is None:
            return True
        if part.tree is not None:
            progressed = has_content_progress(self.before_tree, part.tree)
            diagnostic({
                "kind": "postwrite_change_decision", "contentProgress": progressed,
                "beforeRoots": self.before_tree.native.roots if self.before_tree is not None else (),
                "afterRoots": part.tree.native.roots,
                "beforeNodes": len(self.before_tree.native.nodes) if self.before_tree is not None else 0,
                "afterNodes": len(part.tree.native.nodes),
                "reason": "observed_content_progress" if progressed else "no_confirmed_content_progress",
            })
            return not progressed
        if self.before_image is not None and part.image is not None:
            return self.before_image == part.image.image
        return True

    async def wait(self, controller, directory, *, collection_ms):
        """Wake only for a real later event; unsupported/no-change never polls UI."""
        budget = current_operation_budget()
        sampled = directory.get("appChange")
        unavailable = (
            "missing_operation_budget" if budget is None else "missing_write_epoch" if self.baseline is None
            else "missing_read_epoch" if sampled is None
            else "different_app_incarnation" if any(
                sampled.get(key) != self.baseline.get(key) for key in ("session", "application", "incarnation")
            ) else None
        )
        if unavailable is not None:
            diagnostic({"kind": "postwrite_change_skipped", "reason": unavailable})
            return ()
        if self.input_sampled and sampled["token"] != self.baseline["token"]:
            # This exact App incarnation advanced after actual requested input,
            # and the first read sampled that later generation. Do not wait for
            # a second notification just because focus/coverage is not content.
            diagnostic({
                "kind": "postwrite_change_skipped", "operationId": budget.operation_id,
                "reason": "input_generation_already_observed",
                "inputToken": self.baseline["token"], "readToken": sampled["token"],
            })
            return ()
        # Optional settling must not grow merely because a slow but useful
        # collection may finish after the one-second latency target.
        available = budget.target_available_ms()
        # Retain at least the actual first collection's cost, and half the
        # remaining call for new facts/pixels/publication. This is a reserve,
        # never a duration we deliberately sleep or a fresh operation deadline.
        reserve = max(collection_ms, available // 2)
        delay = min(400, available - reserve)
        if delay < 1:
            diagnostic({
                "kind": "postwrite_change_skipped", "operationId": budget.operation_id,
                "reason": "reserve_for_captured_content", "availableMs": available, "recollectionReserveMs": reserve,
            })
            return ()
        with controller.port.calls.claim(available) as remaining:
            async with controller.port.calls.operation(None, remaining) as read:
                changed = await controller.port.app_changes(
                    read.token, read.target, sampled["application"], read.remaining(),
                    after=sampled["token"], delay_ms=delay,
                )
        events = ()
        if changed["reason"] == "event" and all(
            changed[key] == sampled[key] for key in ("session", "application", "incarnation")
        ):
            events = tuple(changed["events"])
        diagnostic({
            "kind": "postwrite_change_wait", "operationId": budget.operation_id,
            "availableMs": available, "recollectionReserveMs": reserve, "waitLimitMs": delay,
            "reason": changed["reason"], "events": events,
        })
        return events
