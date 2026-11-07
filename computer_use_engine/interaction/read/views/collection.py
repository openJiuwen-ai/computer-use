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
from computer_use.interaction.read.acquisition.loading import window_content_unexposed
from computer_use.native.query.directory import select_surface
from computer_use.session.interfaces import WindowIdentity

from .related import RelatedRead, window_lifecycle

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
    captured_components: dict[WindowIdentity, frozenset[str]] = field(default_factory=dict)
    tree_roots: dict[WindowIdentity, str | None] = field(default_factory=dict)
    attempted_tree_roots: dict[WindowIdentity, str | None] = field(default_factory=dict)
    tree_upgrades: set[WindowIdentity] = field(default_factory=set)
    pending_upgrades: set[WindowIdentity] = field(default_factory=set)
    changed_components: dict[WindowIdentity, frozenset[str]] = field(default_factory=dict)
    order: tuple[WindowIdentity, ...] = ()
    principal: str | None = None
    selection_error: ComputerUseError | None = None
    capture_status: set[WindowIdentity] = field(default_factory=set)
    menu_origin: WindowIdentity | None = None

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
        self.menu_origin = None
        origin = self.related.menu_origin if self.related is not None else None
        original = permitted.get(origin.window) if origin is not None else None
        if (
            original is not None and original.get("node") is not None
            and WindowIdentity.of(original) == origin and directory["session"] == self.related.original.session
        ):
            self.menu_origin = origin
        priority = (self.menu_origin.window,) if self.menu_origin is not None else ()
        self.order = tuple(
            WindowIdentity.of(permitted[key]) for key in dict.fromkeys((*priority, *keys)) if key in permitted
        )
        for identity in self.order:
            item = permitted[identity.window]
            root = item.get("node")
            self.tree_roots[identity] = root
            # A visual catalogue deliberately has no AX root. One attempted
            # read under that evidence does not satisfy a tree obligation when
            # this same lifetime subsequently supplies its accessible root.
            if (
                root is not None
                and identity in self.attempted_tree_roots
                and self.attempted_tree_roots[identity] is None
                and "tree" not in self.captured_components.get(identity, ())
                and identity not in self.tree_upgrades
            ):
                self.tree_upgrades.add(identity)
                self.pending_upgrades.add(identity)
            requested = components(self.view)
            if self.related is not None and (
                identity not in self.related.known_windows
                or item["modal"] is True and identity.window == self.principal
                and identity != self.related.original_window
            ):
                requested = components("both")
            if identity in self.capture_status:
                requested = components("tree")
            if identity == self.menu_origin:
                requested = requested | {"tree"} if identity.window in keys else frozenset({"tree"})
            self.requested[identity] = self.requested.get(identity, frozenset()) | requested

    def next(self) -> tuple[WindowIdentity, str] | None:
        for identity in self.order:
            attempted = self.attempted.get(identity, frozenset())
            if identity not in self.attempted and len(self.attempted) >= self.limit:
                continue
            missing = self.requested[identity] - attempted
            missing |= self.changed_components.pop(identity, frozenset())
            if identity in self.pending_upgrades and "tree" in self.requested[identity]:
                missing |= {"tree"}
            if missing:
                if identity == self.menu_origin and "tree" in missing:
                    missing = frozenset({"tree"})
                self.attempted[identity] = attempted | missing
                if "tree" in missing:
                    self.attempted_tree_roots[identity] = self.tree_roots.get(identity)
                    self.pending_upgrades.discard(identity)
                return identity, component_view(missing)
        return None

    def changed(self, identity: WindowIdentity, requested: frozenset[str]) -> None:
        """Schedule only event-invalidated components, preserving attempt history."""
        if identity in self.order:
            self.changed_components[identity] = self.requested[identity] & requested

    def fold_capture_status(self, identity: WindowIdentity) -> None:
        """A positively classified automatic status companion needs its tree summary.

        Its Window entry and explicit read capabilities remain intact. The
        planner had scheduled both components before the small source probe;
        no image was attempted, and accounting must not claim otherwise.
        """
        self.requested[identity] = frozenset({"tree"})
        self.capture_status.add(identity)
        self.attempted[identity] = self.attempted.get(identity, frozenset()) & {"tree"}

    def captured(self, item, part) -> None:
        """An inner collector may already have captured the newly selected modal."""
        identity = WindowIdentity.of(item)
        captured = frozenset(name for name in ("tree", "image") if getattr(part, name) is not None)
        self.attempted[identity] = self.attempted.get(identity, frozenset()) | captured
        self.captured_components[identity] = self.captured_components.get(identity, frozenset()) | captured
        if self.related is not None and "tree" in self.attempted[identity] and (
            part.tree is None or window_content_unexposed(part.tree)
        ):
            # A fresh automatic tree can lose its body after a key or click.
            # Add the missing visual component within this same collection;
            # a prior rich tree must not suppress observation of a new canvas.
            self.requested[identity] = self.requested.get(identity, frozenset()) | {"image"}
        if "tree" in captured:
            self.pending_upgrades.discard(identity)

    def coverage(self, directory, parts, included_images=(), *, included_trees=None):
        """Account for each requested object; parent content cannot cover a child."""
        current = {WindowIdentity.of(item) for item in directory["surfaces"]}
        records = []
        for identity, requested in self.requested.items():
            part = next((part for part in parts if part.identity == identity), None)
            captured = tuple(name for name in ("tree", "image") if part is not None and getattr(part, name) is not None)
            lifecycle = window_lifecycle(identity, directory)
            included = tuple(
                name
                for name in captured
                if (name != "image" or identity.window in included_images)
                and (name != "tree" or included_trees is None or identity in included_trees)
            )
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
                    **({
                        "treeCollection": {
                            "session": part.tree.native.session,
                            "observation": part.tree.native.id,
                            "intervalNs": part.tree.native.interval_ns,
                        },
                    } if part is not None and part.tree is not None and "tree" not in included else {}),
                    **({"historical": included} if identity not in current and included else {}),
                }
            )
        return tuple(records)
