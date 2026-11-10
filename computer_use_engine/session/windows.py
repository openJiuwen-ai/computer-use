# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Canonical window references and application catalogues for one SDK session."""

from collections import OrderedDict
from dataclasses import dataclass
from weakref import WeakKeyDictionary

from computer_use.diagnostics import local_failure
from computer_use.identity import FrozenRecord
from computer_use.native.contracts import Knowledge
from computer_use.session.interfaces import (
    DirectoryMembership,
    InstanceState,
    WindowIdentity,
    WindowLifetime,
    WindowMembership,
)
from computer_use.session.surface_content import content_host, content_kind


@dataclass(frozen=True)
class WindowEntry:
    session: str
    application: str
    name: str
    identity: WindowIdentity
    directory: dict

    @property
    def key(self):
        return self.session, self.application, self.identity


class WindowReferences:
    """Retain canonical App scopes and stable references for exact native windows."""

    def __init__(self):
        self._next = 0
        self._entries = OrderedDict()
        self._aliases = {}
        self._applications = {}
        self._application_selectors = {}
        self._ended_applications = set()
        self._physical = {}
        self._variants = {}
        self._target_captures = WeakKeyDictionary()

    def application(self, session, application, name):
        key = (session, application)
        state = self._applications.get(key)
        if state is None:
            state = InstanceState(application, session=session, name=name)
            self._applications[key] = state
        elif name:
            state.name = name
        return state

    def retire_applications(self, session, selectors):
        """Record exact ended lifetimes from the native application catalogue."""
        self._ended_applications.update((session, selector) for selector in selectors)

    def application_ended(self, session, application):
        return (session, application) in self._ended_applications

    def remember_application_selector(self, selector, state):
        """Reuse the first exact native instance until its lifetime ends."""
        key = (state.session, selector)
        previous = self._application_selectors.get(key)
        if previous is None or self.application_ended(state.session, previous):
            self._application_selectors[key] = state.selector

    def application_for_selector(self, session, selector):
        """Reuse verified identity across wrappers without looking up another process."""
        application = self._application_selectors.get((session, selector))
        if application is None or self.application_ended(session, application):
            return None
        return self._applications.get((session, application))

    def clear_lookup_caches(self):
        """Release optional name facts when this driver's session is invalidated."""
        from computer_use.session.lookup_continuation import discard_lookup_continuation

        for state in self._applications.values():
            for capture in tuple(state.lookup_captures):
                capture.release()
            state.lookup_captures.clear()
            state.lookup_observations.clear()
            state.lookup_results.clear()
            discard_lookup_continuation(state)

    def register_directory(self, directory, name="", *, identity_only=False):
        # App reads and desktop rows feed the same lifetime authority. A
        # metadata-only catalogue publishes references without replacing it.
        state = self.application(directory["session"], directory["application"], name)
        if not identity_only:
            state.reconcile(
                directory,
                DirectoryMembership(
                    not directory.get("stopReasons", ()),
                    tuple(WindowMembership(WindowIdentity.of(item), Knowledge.YES) for item in directory["surfaces"]),
                    tuple(WindowIdentity.of(item) for item in directory.get("endedWindows", ())),
                ),
            )
        aliases = {}
        for item in directory["surfaces"]:
            entry = WindowEntry(
                directory["session"], directory["application"], name, WindowIdentity.of(item), directory
            )
            if self._retired(entry):
                continue
            ref = self._aliases.get(entry.key)
            physical = (entry.session, item["window"]) if item["nativeWindow"] else None
            if ref is None and physical is not None:
                candidate = self._physical.get(physical)
                if candidate is not None and not self._retired(self._entries[candidate]):
                    # A physical native window may be proven through a helper
                    # and its application owner. This aliases their directory
                    # evidence only; retained native bindings remain unchanged.
                    variant = self._variants[candidate].get(entry.application)
                    if variant is None or variant.identity == entry.identity:
                        ref = candidate
            if ref is None:
                ref = self._next
                self._next += 1
                self._entries[ref] = entry
                self._variants[ref] = {}
                if physical is not None:
                    self._physical[physical] = ref
            self._aliases[entry.key] = ref
            previous = self._variants[ref].get(entry.application)
            if identity_only:
                state.retain_visible_window_identity(entry.identity)
                if previous is not None:
                    aliases[str(ref)] = item["window"]
                    continue
            if not name and previous is not None:
                entry = WindowEntry(entry.session, entry.application, previous.name, entry.identity, directory)
            self._variants[ref][entry.application] = entry
            if self._entries[ref].application == entry.application:
                self._entries[ref] = entry
            self._entries.move_to_end(ref)
            aliases[str(ref)] = item["window"]
        return aliases

    def _retired(self, entry):
        state = self._applications.get((entry.session, entry.application))
        evidence = state.evidence.get(entry.identity) if state is not None else None
        return evidence is not None and evidence.lifetime is WindowLifetime.RETIRED

    def belongs(self, ref, application):
        """Application ownership comes from observed catalogues, never labels."""
        return application in self._variants.get(ref, {})

    def reference(self, session, application, identity):
        """Return an existing exact alias without publishing scope or reviving a lifetime."""
        return self._aliases.get((session, application, identity))

    def resolve(self, ref, session, *, application=None):
        entry = self._entries.get(ref)
        if entry is None or entry.session != session:
            raise local_failure(
                "window reference is unknown or belongs to an expired session",
                "stale_target",
            )
        if application is not None:
            entry = self._variants[ref].get(application)
            if entry is None:
                raise local_failure("window does not belong to this App's confirmed application", "stale_target")
        return entry

    @staticmethod
    def retain_image(state, image, generation=0):
        """Retain exact delivered pixels independently for every fixed window.

        A member view uses the same canvas coordinates as the model saw. It
        cannot click a peer's pixels and does not become an unseen crop/local
        image. Resolving a new request scope therefore never revives an older map.
        """
        if image.expired or image.unavailable_mapping or image.delivered is None:
            return
        generation = generation or image.generation
        identities = {
            WindowIdentity(piece.window, piece.image.target.surface, piece.image.target.instance)
            for piece in image.pieces if piece.scope_confirmed and piece.image.target.session == state.session
        }
        aliases = {host for _, host in image.content_hosts} | {source for source, _ in image.shared_pixels}
        identities.update(identity for identity in image.identities if identity.window in aliases)
        for identity in identities:
            evidence = state.evidence.get(identity)
            if (
                evidence is None
                or evidence.lifetime is WindowLifetime.RETIRED
                or generation < state.window_image_generations.get(identity, -1)
            ):
                continue
            retained = image.for_window(identity.window)
            if retained is None:
                continue
            state.window_images[identity] = retained
            state.window_image_generations[identity] = generation

    @staticmethod
    def delivered_image(state, view):
        """Pending sampling does not replace a fixed window's delivered view."""
        binding = getattr(view, "_window_identity", None)
        if binding is not None:
            identity = WindowIdentity(binding.root_window, binding.surface, binding.instance)
            image = state.window_images.get(identity)
            if image is not None:
                return image
        # The fixed view owns these delivered pixels; App state may name another view.
        # pylint: disable-next=protected-access
        return view._image

    def capture_references(self):
        """Live delivered/pending canvas maps, piggybacked on native captures."""
        images = [
            image
            for state in self._applications.values()
            for image in (*state.window_images.values(), *state.sampled_window_images.values())
        ]
        captures = set()
        for image in images:
            if not image.expired:
                captures.update(
                    piece.image.target.capture for piece in image.pieces
                    if piece.scope_confirmed and piece.image.target.capture is not None
                )
        return frozenset(captures) | frozenset(self._target_captures.values())

    def retain_target_capture(self, target, capture):
        """Keep native capture mappings while a public spatial target is live."""
        self._target_captures[target] = capture

    def choices(self, directory, name=""):
        aliases = self.register_directory(directory, name)
        items = {item["window"]: item for item in directory["surfaces"]}
        return [
            FrozenRecord({"id": int(ref), "title": items[key]["title"]})
            for ref, key in aliases.items()
            if content_host(items[key]) not in items and content_kind(items[key]) != "application"
        ]
