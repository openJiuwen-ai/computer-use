"""Application references, instance membership, and public window handles."""

from __future__ import annotations

import weakref
from collections import OrderedDict
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any

from computer_use.diagnostics import localFailure
from computer_use.native.contracts import Knowledge
from computer_use.native.scopes import WorkingWindow

if TYPE_CHECKING:
    from computer_use.protocol.records import NativeDirectory, Record, SurfaceInfo


@dataclass(frozen=True)
class ApplicationEntry:
    """An observed application choice, optionally anchored to a working window."""

    session: str
    selector: str
    display_name: str
    running: bool
    directory: NativeDirectory | None = None
    window: str | None = None
    application_name: str | None = None

    @property
    def key(self):
        if self.window is None:
            return self.session, self.selector, None
        item = next(item for item in self.directory["surfaces"] if item["window"] == self.window)
        return self.session, self.selector, self.window, item["instance"], item["surface"]


class ApplicationReferences:
    """Assign session-scoped application numbers and retain recent choices."""

    def __init__(self, capacity=8192):
        self.capacity, self._next = capacity, 0
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
        while len(self._entries) > self.capacity:
            old, expired = self._entries.popitem(last=False)
            self._aliases.pop(expired.key, None)
        return ref

    def resolve(self, ref, session):
        entry = self._entries.get(ref)
        if entry is None or entry.session != session:
            raise localFailure(
                "application reference is unknown or belongs to an expired session",
                "call listApps() and choose a returned integer id",
                "stale_target",
            )
        return entry

    def window(self, ref, session):
        return self.resolve(ref, session).window


class SurfaceOwner:
    """Register a member surface's private read and input scope with the session."""

    def __init__(self, controller, state):
        controller.register(self, dict(selector=state.selector, _nativeState=state))


@dataclass(frozen=True)
class WindowIdentity:
    """A window lifetime, independent of its latest observed parent or AX root."""

    window: str
    surface: str
    instance: str

    @classmethod
    def of(cls, item: SurfaceInfo) -> WindowIdentity:
        return cls(item["window"], item["surface"], item["instance"])


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
    scope exclusion. Only complete absence or a changed native identity retires
    a lifetime, and retirement is irreversible for this App instance.
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


@dataclass
class InstanceState:
    """Retain an App instance's window membership and public references.

    Attributes:
        selector: Application/working-instance selector chosen by the caller.
        anchor: Root-window identity fixed when this instance is selected.
        directory: Latest member catalogue, or None before first observation.
        members: Only members confirmed by the current directory; prior unknown
            members remain in evidence without input authority.
        windows: Public window numbers mapped to those native identities.
        owners: Registered private scope handles owned by this session.
        refs: Retained element snapshots; their read-specific contents are opaque here.
        observed: Members already observed through this instance.
        menu_contexts: Confirmed window context for each application-menu surface.
        views: Weakly retained public window handles, opaque to this state owner.
        evidence: Retained window lifetimes and independently changing membership.
        owner_identities: Exact lifetimes of private scopes, preventing a reused
            window slot from retaining its previous owner's selection.
    """

    selector: str
    anchor: WorkingWindow | None = None
    directory: NativeDirectory | None = None
    members: dict[str, SurfaceInfo] = field(default_factory=dict)
    windows: dict[int, str] = field(default_factory=dict)
    owners: dict[str, object] = field(default_factory=dict)
    refs: OrderedDict[str, Record] = field(default_factory=OrderedDict)
    observed: set[str] = field(default_factory=set)
    menu_contexts: dict[str, str | None] = field(default_factory=dict)
    views: weakref.WeakValueDictionary[tuple[str, str, str], Any] = field(default_factory=weakref.WeakValueDictionary)
    evidence: dict[WindowIdentity, WindowEvidence] = field(default_factory=dict)
    owner_identities: dict[str, WindowIdentity] = field(default_factory=dict)

    def reconcile(self, directory: NativeDirectory, facts: DirectoryMembership) -> None:
        """Replace current authorization facts while retaining unknown lifetimes.

        This is the sole owner of membership/lifetime transitions. A failed or
        partial observation cannot revive a retired identity or grant scope
        from an earlier directory. Unrelated member failures stay local.
        """
        current = {item.identity: item for item in facts.windows}
        by_window = {item.identity.window: item.identity for item in facts.windows}
        for identity, previous in tuple(self.evidence.items()):
            if previous.lifetime is WindowLifetime.RETIRED:
                continue
            if identity in current:
                self.evidence[identity] = WindowEvidence(identity, current[identity].membership)
            elif identity.window in by_window or facts.complete:
                self.evidence[identity] = WindowEvidence(identity, Knowledge.NO, WindowLifetime.RETIRED)
            else:
                self.evidence[identity] = WindowEvidence(identity, Knowledge.UNKNOWN)
        for identity, item in current.items():
            # Unknown or independent windows were never part of this instance;
            # do not manufacture retained ownership for them.
            if identity not in self.evidence and item.membership is Knowledge.YES:
                self.evidence[identity] = WindowEvidence(identity, item.membership)
        self.directory = directory
        self.members = {
            item["window"]: item
            for item in directory["surfaces"]
            if (known := self.evidence.get(WindowIdentity.of(item))) is not None
            and known.lifetime is WindowLifetime.RETAINED
            and known.membership is Knowledge.YES
        }

    def retire(self, identity: WindowIdentity) -> None:
        """Consume explicit fixed-window invalidation without guessing from errors."""
        self.evidence[identity] = WindowEvidence(identity, Knowledge.NO, WindowLifetime.RETIRED)
        self.members.pop(identity.window, None)

    def unconfirm(self, identity: WindowIdentity | None = None) -> None:
        """Suspend current membership after an incomplete fixed-root observation."""
        if identity is not None and identity not in self.evidence:
            self.evidence[identity] = WindowEvidence(identity, Knowledge.UNKNOWN)
        for identity, item in tuple(self.evidence.items()):
            if item.lifetime is WindowLifetime.RETAINED:
                self.evidence[identity] = WindowEvidence(identity, Knowledge.UNKNOWN)
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
