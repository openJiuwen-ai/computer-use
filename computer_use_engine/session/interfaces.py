# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Application references, instance membership, and public window handles."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Iterator
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any
from weakref import WeakSet

from computer_use.diagnostics import local_failure
from computer_use.native.contracts import Knowledge
from computer_use.native.scopes import WorkingWindow

if TYPE_CHECKING:
    from computer_use.protocol.records import NativeDirectory, Record, SurfaceInfo
    from computer_use.session.interface_images import InterfaceImage


@dataclass(frozen=True)
class ApplicationEntry:
    """An observed application identity; window choices have their own registry."""

    session: str
    selector: str
    display_name: str
    running: bool

    @property
    def key(self):
        return self.session, self.selector


class ApplicationReferences:
    """Assign stable session-scoped application numbers, independently of caches."""

    def __init__(self):
        self._next = 0
        self._entries, self._aliases = OrderedDict(), {}

    def publish(self, entry):
        key = entry.key
        ref = self._aliases.get(key)
        if ref is None:
            ref = self._next
            self._next += 1
            self._aliases[key] = ref
        self._entries[ref] = entry
        self._entries.move_to_end(ref)
        return ref

    def resolve(self, ref, session):
        entry = self._entries.get(ref)
        if entry is None or entry.session != session:
            raise local_failure(
                "application reference is unknown or belongs to an expired session",
                "stale_target",
            )
        return entry


class SurfaceOwner:
    """Register a member surface's private read and input scope with the session."""

    def __init__(self, controller, state):
        controller.register(self, {"selector": state.selector, "_nativeState": state})


@dataclass(frozen=True)
class WindowIdentity:
    """A window lifetime, independent of its latest observed parent or AX root."""

    window: str
    surface: str
    instance: str

    @classmethod
    def of(cls, item: SurfaceInfo) -> WindowIdentity:
        return cls(item["window"], item["surface"], item["instance"])


@dataclass(frozen=True)
class LookupContext:
    """Exact delivered sources available to name/path lookup."""

    window: WindowIdentity | None
    menu_only: bool = False
    menus: tuple[object, ...] = ()
    application_content: object | None = None


class WindowLifetime(str, Enum):
    RETAINED = "retained"
    RETIRED = "retired"


@dataclass(frozen=True)
class WindowMembership:
    """An acquired relationship fact has no authority to change object lifetime."""

    identity: WindowIdentity
    membership: Knowledge


@dataclass(frozen=True)
class WindowEvidence(WindowMembership):
    """Membership evidence never implicitly changes a retained object's lifetime.

    UNKNOWN permits another observation, never input. NO records a confirmed
    scope exclusion. Confirmed native end or replacement retires a lifetime;
    absence from an on-screen catalogue does not. Retirement is irreversible.
    """

    lifetime: WindowLifetime = WindowLifetime.RETAINED


@dataclass(frozen=True)
class DirectoryMembership:
    """Pure, local membership conclusions from one directory acquisition.

    Each observed identity has YES/NO/UNKNOWN ancestry relative to the fixed
    root. Complete describes catalogue coverage, not relationship quality.
    These facts authorize no input until the state owner reconciles them.
    """

    complete: bool
    windows: tuple[WindowMembership, ...]
    ended: tuple[WindowIdentity, ...] = ()


@dataclass(frozen=True)
class DeliveredWindowContext:
    """Actually delivered receiver and query scope for one exact window lifetime."""

    input: WindowIdentity | None
    lookup: LookupContext
    generation: int


@dataclass
class InstanceState:
    """Retain an application catalogue, window lifetimes and delivered references.

    Attributes:
        selector: Canonical application selector after discovery.
        anchor: Current observation primary; never defines application lifetime.
        directory: Latest member catalogue, or None before first observation.
        members: Only members confirmed by the current directory; prior unknown
            members remain in evidence without input authority.
        windows: Public window numbers mapped to those native identities.
        owners: Registered private scope handles owned by this session.
        refs: Retained element snapshots; their read-specific contents are opaque here.
        observed: Members already observed through this instance.
        window_contexts: Delivered per-window lookup/input context, independent of public handles.
        evidence: Retained window lifetimes and independently changing membership.
        owner_identities: Exact lifetimes of private scopes, preventing a reused
            window slot from retaining its previous owner's selection.
    """

    selector: str
    session: str | None = None
    name: str = ""
    anchor: WorkingWindow | None = None
    input_context: WindowIdentity | None = None
    lookup_context: LookupContext | None = None
    # Semantic query roots actually delivered to the caller. A sampled or
    # image-only window cannot become an unseen fallback name-lookup scope.
    delivered_lookup_windows: set[WindowIdentity] = field(default_factory=set)
    # Latest returned facts for name/path lookup; never live-input authority.
    lookup_observations: OrderedDict = field(default_factory=OrderedDict, repr=False)
    # Exact successful fresh queries, scoped to their original roots and the
    # latest publication versions. They never fabricate full-tree coverage.
    lookup_results: OrderedDict = field(default_factory=OrderedDict, repr=False)
    # Also sees pending delivery sources for invalidation, but never grants
    # query access or owns their lifetime/payload.
    lookup_captures: WeakSet = field(default_factory=WeakSet, repr=False)
    delivered_image: InterfaceImage | None = None
    image_generation: int = 0
    delivered_image_generation: int = 0
    window_images: dict[WindowIdentity, Any] = field(default_factory=dict)
    window_image_generations: dict[WindowIdentity, int] = field(default_factory=dict)
    sampled_window_images: dict[WindowIdentity, Any] = field(default_factory=dict)
    observation_generation: int = 0
    delivered_observation_generation: int = 0
    directory: NativeDirectory | None = None
    members: dict[str, SurfaceInfo] = field(default_factory=dict)
    windows: dict[int, str] = field(default_factory=dict)
    owners: dict[str, object] = field(default_factory=dict)
    refs: OrderedDict[str, Record] = field(default_factory=OrderedDict)
    observed: set[str] = field(default_factory=set)
    window_contexts: dict[WindowIdentity, DeliveredWindowContext] = field(default_factory=dict)
    evidence: dict[WindowIdentity, WindowEvidence] = field(default_factory=dict)
    # Index the retained generation in each native/public slot. Observation
    # work depends on the active catalogue, not every window ever discovered.
    _window_lifetimes: dict[str, WindowIdentity] = field(default_factory=dict, repr=False)
    owner_identities: dict[str, WindowIdentity] = field(default_factory=dict)
    # Acquired menu presentation facts, independent of native lifetimes and
    # actual model-delivery baselines. They never authorize or reject input.
    content_presentations: dict[tuple, dict] = field(default_factory=dict)

    def retain_visible_window_identity(self, identity: WindowIdentity) -> None:
        """Remember a selectable identity without replacing current membership or AX facts."""
        self.evidence.setdefault(identity, WindowEvidence(identity, Knowledge.UNKNOWN))

    def reconcile(self, directory: NativeDirectory, facts: DirectoryMembership) -> None:
        """Replace current authorization facts while retaining unknown lifetimes.

        This is the sole owner of membership/lifetime transitions. A failed or
        partial observation cannot revive a retired identity or grant scope
        from an earlier directory. Unrelated member failures stay local.
        """
        current = {item.identity: item for item in facts.windows}
        by_window = {item.identity.window: item.identity for item in facts.windows}
        ended = set(facts.ended)
        absence_ends_lifetime = facts.complete and directory.get("platform") == "linux"
        changed = set(current) | ended | {WindowIdentity.of(item) for item in self.members.values()}
        changed.update(self._window_lifetimes[key] for key in by_window if key in self._window_lifetimes)
        if absence_ends_lifetime:
            changed.update(self.evidence)
        retired = set()
        for identity in changed:
            previous = self.evidence.get(identity)
            if previous is None or previous.lifetime is WindowLifetime.RETIRED:
                continue
            if identity in ended:
                self.evidence[identity] = WindowEvidence(identity, Knowledge.NO, WindowLifetime.RETIRED)
                retired.add(identity)
            elif identity in current:
                self.evidence[identity] = WindowEvidence(identity, current[identity].membership)
            elif identity.window in by_window or absence_ends_lifetime:
                self.evidence[identity] = WindowEvidence(identity, Knowledge.NO, WindowLifetime.RETIRED)
                retired.add(identity)
            else:
                self.evidence[identity] = WindowEvidence(identity, Knowledge.UNKNOWN)
        for identity, item in current.items():
            # Unknown or independent windows were never part of this instance;
            # do not manufacture retained ownership for them.
            if identity not in self.evidence and item.membership is Knowledge.YES and identity not in ended:
                self.evidence[identity] = WindowEvidence(identity, item.membership)
            known = self.evidence.get(identity)
            if known is not None and known.lifetime is WindowLifetime.RETAINED:
                self._window_lifetimes[identity.window] = identity
        for identity in ended:
            if identity not in self.evidence:
                self.evidence[identity] = WindowEvidence(identity, Knowledge.NO, WindowLifetime.RETIRED)
        self.directory = directory

        def _iter_item(_source_items: Iterator[Any]) -> Iterator[Any]:
            for item in _source_items:
                known = self.evidence.get(WindowIdentity.of(item))
                if (
                    known is not None
                    and known.lifetime is WindowLifetime.RETAINED
                    and known.membership is Knowledge.YES
                ):
                    yield (item["window"], item)

        self.members = dict(_iter_item(iter(directory["surfaces"])))
        self.release_retired_content(retired)

    def retire(self, identity: WindowIdentity) -> None:
        """Consume explicit fixed-window invalidation without guessing from errors."""
        self.evidence[identity] = WindowEvidence(identity, Knowledge.NO, WindowLifetime.RETIRED)
        current = self.members.get(identity.window)
        if current is not None and WindowIdentity.of(current) == identity:
            self.members.pop(identity.window, None)
        self.release_retired_content({identity})

    def release_retired_content(self, identities: set[WindowIdentity]) -> None:
        """Release payloads after authoritative retirement, retaining error provenance.

        Previously held Elements own their old snapshots. Keep minimal numbered
        bindings so a later action can explain the ended window and return the
        current observation instead of failing with an unknown integer.
        """
        if not identities:
            return
        for capture in tuple(self.lookup_captures):
            target = capture.target
            if WindowIdentity(target.root_window, target.surface, target.instance) in identities or any(
                snapshot.get("menu_window_identity") in identities for snapshot in capture.snapshots.values()
            ):
                capture.release()
        for key, lookup_scope in tuple(self.lookup_observations.items()):
            target = lookup_scope.target
            identity = WindowIdentity(target.root_window, target.surface, target.instance)
            if lookup_scope.released or identity in identities or any(
                snapshot.get("menu_window_identity") in identities for snapshot in lookup_scope.snapshots.values()
            ):
                self.lookup_observations.pop(key, None)
        for key, snapshot in tuple(self.lookup_results.items()):
            if any(
                WindowIdentity(target.root_window, target.surface, target.instance) in identities
                for target, _generation in key[0]
            ) or snapshot.get("menu_window_identity") in identities:
                self.lookup_results.pop(key, None)
        for element_ref, snapshot in self.refs.items():
            binding = snapshot.get("binding")
            identity = snapshot.get("window_identity")
            if identity is None and binding is not None:
                identity = WindowIdentity(binding.root_window, binding.surface, binding.instance)
            if identity in identities or snapshot.get("menu_window_identity") in identities:
                self.refs[element_ref] = dict(snapshot, text=None, children=None)
        for identity in identities:
            self.delivered_lookup_windows.discard(identity)
            self.window_contexts.pop(identity, None)
            self.window_images.pop(identity, None)
            self.window_image_generations.pop(identity, None)
            self.sampled_window_images.pop(identity, None)

    def unconfirm(self, identity: WindowIdentity | None = None) -> None:
        """Suspend current membership after an incomplete fixed-root observation."""
        if identity is not None:
            previous = self.evidence.get(identity)
            if previous is None or previous.lifetime is WindowLifetime.RETAINED:
                self.evidence[identity] = WindowEvidence(identity, Knowledge.UNKNOWN)
                self.members.pop(identity.window, None)
            return
        for key, item in tuple(self.evidence.items()):
            if item.lifetime is WindowLifetime.RETAINED:
                self.evidence[key] = WindowEvidence(key, Knowledge.UNKNOWN)
        self.members = {}

    def surface_evidence(self, surface: str | None, identity: WindowIdentity | None = None) -> WindowEvidence | None:
        if identity is not None:
            return self.evidence.get(identity) if identity.surface == surface else None
        # Fresh captures/lookup snapshots can bind the current member identity;
        # saved snapshots supply their own exact identity above. A retired
        # generation of the same surface cannot veto a newly observed object.
        current = next((item for item in self.members.values() if item["surface"] == surface), None)
        if current is not None:
            return self.evidence.get(WindowIdentity.of(current))
        records = [item for item in self.evidence.values() if item.identity.surface == surface]
        return next((item for item in records if item.lifetime is WindowLifetime.RETAINED), next(iter(records), None))

    def contains(self, surface):
        return any(item["surface"] == surface for item in self.members.values())
