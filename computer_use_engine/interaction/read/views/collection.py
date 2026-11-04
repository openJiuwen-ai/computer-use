# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Per-call observation work, keyed by existing native window identities.

This plan only schedules reads. Missing relationships or exhausted work never
reject input, invalidate an object, or authorize a new input receiver.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from computer_use.errors import ComputerUseError
from computer_use.interaction.query.instances.membership import ancestor
from computer_use.native.query.directory import select_surface
from computer_use.session.interfaces import WindowIdentity

from .related import RelatedRead

if TYPE_CHECKING:
    from computer_use.protocol.records import NativeDirectory, SurfaceInfo


def components(view: str) -> frozenset[str]:
    return frozenset(("tree", "image")) if view == "both" else frozenset((view,))


def component_view(requested: frozenset[str]) -> str:
    return "both" if len(requested) == 2 else next(iter(requested))


@dataclass
class CollectionPlan:
    """Reconcile known local work without requiring a complete app catalogue."""

    anchor: str
    view: str
    related: RelatedRead | None = None
    limit: int = 6
    requested: dict[WindowIdentity, frozenset[str]] = field(default_factory=dict)
    attempted: dict[WindowIdentity, frozenset[str]] = field(default_factory=dict)
    order: tuple[WindowIdentity, ...] = ()
    principal: str | None = None
    selection_error: ComputerUseError | None = None

    def update(self, directory: NativeDirectory, permitted: dict[str, SurfaceInfo]) -> None:
        anchor = self.related.preferred(directory, self.anchor) if self.related is not None else self.anchor
        self.selection_error = None
        try:
            selected = select_surface(directory, anchor)
            self.principal = next(
                (key for key, item in permitted.items() if item["surface"] == selected.surface), anchor
            )
        except ComputerUseError as error:
            # Ambiguity affects convenience selection only. Each independently
            # bound member remains a read candidate, including both modals.
            self.principal, self.selection_error = anchor, error
        attached = [key for key in permitted if key != self.principal and ancestor(directory, key, anchor)]
        newly_seen = self.related.new_windows(directory) if self.related is not None else ()
        # Prioritize actual blockers before ordinary attached or new peers.
        attached.sort(key=lambda key: permitted[key].get("modal") is not True)
        keys = dict.fromkeys((self.principal, *attached, *newly_seen))
        self.order = tuple(WindowIdentity.of(permitted[key]) for key in keys if key in permitted)
        for identity in self.order:
            requested = components(self.view)
            if self.related is not None and (
                identity not in self.related.known_windows
                or identity.window == self.principal
                and identity != self.related.original_window
            ):
                requested = components("both")
            self.requested[identity] = self.requested.get(identity, frozenset()) | requested

    def next(self) -> tuple[WindowIdentity, str] | None:
        for identity in self.order:
            attempted = self.attempted.get(identity, frozenset())
            if identity not in self.attempted and len(self.attempted) >= self.limit:
                continue
            missing = self.requested[identity] - attempted
            if missing:
                self.attempted[identity] = attempted | missing
                return identity, component_view(missing)
        return None

    def captured(self, item, part) -> None:
        """An inner collector may already have captured the newly selected modal."""
        identity = WindowIdentity.of(item)
        captured = frozenset(name for name in ("tree", "image") if getattr(part, name) is not None)
        self.attempted[identity] = self.attempted.get(identity, frozenset()) | captured

    def coverage(self, directory, parts, included_images=()):
        """Account for each requested object; parent content cannot cover a child."""
        current = {WindowIdentity.of(item) for item in directory["surfaces"]}
        records = []
        for identity, requested in self.requested.items():
            part = next(
                (
                    part
                    for part in parts
                    if (part.target.root_window, part.target.surface, part.target.instance)
                    == (identity.window, identity.surface, identity.instance)
                ),
                None,
            )
            captured = tuple(name for name in ("tree", "image") if part is not None and getattr(part, name) is not None)
            included = tuple(
                name
                for name in captured
                if identity in current and (name != "image" or identity.window in included_images)
            )
            lifecycle = "observed" if identity in current else "unknown" if directory["stopReasons"] else "closed"
            records.append(
                {
                    "window": identity.window,
                    "surface": identity.surface,
                    "instance": identity.instance,
                    "requested": tuple(sorted(requested)),
                    "attempted": tuple(sorted(self.attempted.get(identity, ()))),
                    "captured": captured,
                    "included": included,
                    "delivered": (),
                    "required": identity in self.order,
                    "missing": tuple(sorted(requested - set(included)))
                    if identity in self.order and lifecycle != "closed"
                    else (),
                    "lifecycle": lifecycle,
                }
            )
        return tuple(records)
