# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Per-call observation work, keyed by existing native window identities.

This plan only schedules reads. Missing relationships or exhausted work never
reject input, invalidate an object, or authorize a new input receiver.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from computer_use.errors import ComputerUseError
from computer_use.interaction.query.instances.membership import interaction_contains
from computer_use.interaction.query.instances.scope import allowed
from computer_use.interaction.read.acquisition.loading import window_content_unexposed
from computer_use.native.query.directory import select_surface
from computer_use.session.interfaces import WindowIdentity
from computer_use.session.surface_content import content_host, transient_content

from .related import RelatedRead, window_lifecycle

if TYPE_CHECKING:
    from computer_use.protocol.records import NativeDirectory, SurfaceInfo


def components(view: str) -> frozenset[str]:
    return frozenset(("tree", "image")) if view == "both" else frozenset((view,))


def observation_members(ui, related: RelatedRead | None):
    """Include new/unknown content without widening the fixed input scope.

    A newly discovered dialog or an unparented current menu must be visible to
    the caller even when its relationship to the requested window is unknown.
    Old unrelated document windows remain directory entries, not extra reads.
    """
    permitted = allowed(ui)
    # pylint: disable-next=protected-access
    if ui._anchor is None:
        return permitted
    # pylint: disable-next=protected-access
    instance = ui._instance
    newly_seen = related.new_surfaces(instance.directory) if related is not None else ()
    for key, item in instance.members.items():
        include_member = key in newly_seen or (
            transient_content(item) and content_host(item) is None and item["parentState"] != "known"
        )
        if include_member:
            permitted[key] = item
    return permitted


def component_view(requested: frozenset[str]) -> str:
    return "both" if len(requested) == 2 else next(iter(requested))


def complete_content(snapshot, root):
    """An exact shared subtree can cover content; a label or root alone cannot."""
    pending, seen = [root], set()
    while pending:
        key = pending.pop()
        if key in seen:
            continue
        seen.add(key)
        node = snapshot.native.nodes.get(key)
        if node is None:
            return False
        relations = [item for item in snapshot.facts["relations"]
                     if item["source"] == key and item["kind"] == "children"]
        if not relations:
            if node.role in ("list", "menu", "window", "group", "tree", "table"):
                return False
            continue
        if len(relations) != 1:
            return False
        children = relations[0]
        if children["state"] != "complete" or children["certainty"] != "confirmed":
            return False
        count = len(children["targets"])
        if children.get("total", count) != count or children.get("offset", 0) != 0:
            return False
        pending.extend(children["targets"])
    return True


@dataclass
class CollectionPlan:
    """Reconcile known local work without requiring a complete app catalogue."""

    anchor: str
    view: str
    related: RelatedRead | None = None
    limit: int = 6
    strict_window: bool = field(default=False, kw_only=True)
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
    reused_content: dict[WindowIdentity, dict] = field(default_factory=dict)
    reused_images: dict[WindowIdentity, dict] = field(default_factory=dict)
    menu_origin: WindowIdentity | None = None
    focused_editor: tuple[WindowIdentity, WindowIdentity] | None = None

    def update(self, directory: NativeDirectory, permitted: dict[str, SurfaceInfo]) -> None:
        anchor = self.related.preferred(directory, self.anchor) if self.related is not None else self.anchor
        self.selection_error = None
        try:
            options = {"follow_modal": False} if self.strict_window else {}
            selected = select_surface(directory, anchor, **options)
            self.principal = next(
                (key for key, item in permitted.items() if item["surface"] == selected.surface), anchor
            )
        except ComputerUseError as error:
            # Ambiguity affects convenience selection only. Each independently
            # bound member remains a read candidate, including both modals.
            self.principal, self.selection_error = anchor, error
        attached = [key for key in permitted if key != self.principal and interaction_contains(directory, key, anchor)]
        newly_seen = self.related.new_surfaces(directory) if self.related is not None else ()
        if self.related is not None and self.related.original.root_window not in permitted:
            # Recover the surviving form after a popup ends. Unchanged auxiliary
            # siblings are not missing result content; keep their directory
            # entries without rereading every old child of the recovered form.
            attached = [
                key for key in attached
                if key in newly_seen or permitted[key]["modal"] is True or transient_content(permitted[key])
            ]
        # Prioritize actual blockers before ordinary attached or new peers.
        attached.sort(key=lambda key: permitted[key].get("modal") is not True)
        presented = [key for key, item in permitted.items() if transient_content(item)]
        keys = dict.fromkeys((self.principal, *attached, *newly_seen, *presented))
        if self.focused_editor is not None:
            host, editor = self.focused_editor
            if self.principal == host.window and host.window in permitted and editor.window in permitted:
                exact_host = WindowIdentity.of(permitted[host.window]) == host
                if exact_host and WindowIdentity.of(permitted[editor.window]) == editor:
                    attached_editor = interaction_contains(directory, editor.window, host.window)
                    if attached_editor and directory.get("focusedWindow") in (None, host.window, editor.window):
                        keys.pop(host.window, None)
                        self.requested.pop(host, None)
        self.menu_origin = None
        origin = self.related.menu_origin if self.related is not None else None
        original = permitted.get(origin.window) if origin is not None else None
        has_origin = original is not None and original.get("node") is not None
        if (
            has_origin and WindowIdentity.of(original) == origin
            and directory["session"] == self.related.original.session
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
            first_ax_root = (
                root is not None and identity in self.attempted_tree_roots
                and self.attempted_tree_roots[identity] is None
            )
            if (
                first_ax_root and "tree" not in self.captured_components.get(identity, ())
                and identity not in self.tree_upgrades
            ):
                self.tree_upgrades.add(identity)
                self.pending_upgrades.add(identity)
            requested = components(self.view)
            if self.view != "tree" and self.related is not None:
                needs_both = identity not in self.related.known_windows or (
                    item["modal"] is True and identity.window == self.principal
                    and identity != self.related.original_window
                )
                if needs_both:
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

    def reuse_attached_content(self, parts, members) -> None:
        """Avoid rereading a menu that is fully present in the same host capture."""
        for identity in self.order:
            item = members.get(identity.window)
            content = item.get("content", {}) if item is not None else {}
            node, host = content.get("hostNode"), content.get("host")
            if host not in members:
                continue
            for source in parts:
                source_identity = WindowIdentity(
                    source.target.root_window, source.target.surface, source.target.instance,
                )
                if source_identity == WindowIdentity.of(members[host]):
                    break
            else:
                source = None
            if source is None or source.source is not None and not source.source.scope_confirmed:
                continue
            if content.get("evidence") == "nativeWindowID" and source.image is not None:
                self.reused_images[identity] = {"window": host, "surface": source.target.surface}
                self.attempted[identity] = self.attempted.get(identity, frozenset()) | {"image"}
                self.captured_components[identity] = self.captured_components.get(identity, frozenset()) | {"image"}
            if node is None or identity in self.reused_content or source.tree is None:
                continue
            if not complete_content(source.tree, node):
                continue
            self.reused_content[identity] = {
                "window": host, "surface": source.target.surface, "instance": source.target.instance,
                "node": node,
                "observation": source.tree.native.id,
            }
            self.attempted[identity] = self.attempted.get(identity, frozenset()) | {"tree"}
            self.captured_components[identity] = self.captured_components.get(identity, frozenset()) | {"tree"}

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
        can_add_image = self.view != "tree" and self.related is not None and "tree" in self.attempted[identity]
        if can_add_image and (part.tree is None or window_content_unexposed(part.tree)):
            # A fresh automatic tree can lose its body after a key or click.
            # Add the missing visual component within this same collection;
            # a prior rich tree must not suppress observation of a new canvas.
            self.requested[identity] = self.requested.get(identity, frozenset()) | {"image"}
        if "tree" in captured:
            self.pending_upgrades.discard(identity)

    def coverage(self, directory, parts, included_images=(), *, included_trees=None):
        """Account for exact members, including content delivered through a proven host."""
        current = {WindowIdentity.of(item) for item in directory["surfaces"]}
        records = []
        for identity, requested in self.requested.items():
            part = next((part for part in parts if part.identity == identity), None)
            captured = tuple(name for name in ("tree", "image") if part is not None and getattr(part, name) is not None)
            reused = self.reused_content.get(identity)
            if reused is not None and "tree" not in captured:
                captured = ("tree", *captured)
            shared_image = self.reused_images.get(identity)
            if shared_image is not None and "image" not in captured:
                captured = (*captured, "image")
            lifecycle = window_lifecycle(identity, directory)
            included_components = []
            for name in captured:
                needs_shared_image = name == "image" and identity.window not in included_images
                if needs_shared_image and (shared_image is None or shared_image["window"] not in included_images):
                    continue
                needs_shared_tree = name == "tree" and included_trees is not None and identity not in included_trees
                if needs_shared_tree and (reused is None or WindowIdentity.of(reused) not in included_trees):
                    continue
                included_components.append(name)
            included = tuple(included_components)
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
                    **({"reused": {
                        **({"tree": reused} if reused is not None else {}),
                        **({"image": shared_image} if shared_image is not None else {}),
                    }} if reused is not None or shared_image is not None else {}),
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
