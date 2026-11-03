# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Canonical window references and application catalogues for one SDK session."""

from collections import OrderedDict
from dataclasses import dataclass

from computer_use.diagnostics import local_failure
from computer_use.identity import FrozenRecord, WeakIdentityMap
from computer_use.session.interfaces import InstanceState, WindowIdentity, WindowLifetime


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
    """Publish one stable reference per native window lifetime, never per label."""

    def __init__(self, capacity=16384):
        self.capacity = capacity
        self._next = 0
        self._entries = OrderedDict()
        self._aliases = {}
        self._applications = {}
        self._physical = {}
        self._variants = {}
        self._shared_images = WeakIdentityMap()

    def application(self, session, application, name):
        key = (session, application)
        state = self._applications.get(key)
        if state is None:
            state = InstanceState(application, session=session, name=name)
            self._applications[key] = state
        elif name:
            state.name = name
        return state

    def register_directory(self, directory, name=""):
        aliases = {}
        for item in directory["surfaces"]:
            entry = WindowEntry(
                directory["session"], directory["application"], name, WindowIdentity.of(item), directory
            )
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
            if not name and previous is not None:
                entry = WindowEntry(entry.session, entry.application, previous.name, entry.identity, directory)
            self._variants[ref][entry.application] = entry
            if self._entries[ref].application == entry.application:
                self._entries[ref] = entry
            self._entries.move_to_end(ref)
            aliases[str(ref)] = item["window"]
            while len(self._entries) > self.capacity:
                expired_ref, expired = self._entries.popitem(last=False)
                for variant in self._variants.pop(expired_ref).values():
                    self._aliases.pop(variant.key, None)
                physical_key = (expired.session, expired.identity.window)
                if self._physical.get(physical_key) == expired_ref:
                    self._physical.pop(physical_key, None)
        return aliases

    def _retired(self, entry):
        state = self._applications.get((entry.session, entry.application))
        evidence = state.evidence.get(entry.identity) if state is not None else None
        return evidence is not None and evidence.lifetime is WindowLifetime.RETIRED

    def belongs(self, ref, application):
        """Application ownership comes from observed catalogues, never labels."""
        return application in self._variants.get(ref, {})

    def resolve(self, ref, session):
        entry = self._entries.get(ref)
        if entry is None or entry.session != session:
            raise local_failure(
                "window reference is unknown or belongs to an expired session",
                "read the desktop or App and choose a published Window reference",
                "stale_target",
            )
        return entry

    def retain_image(self, state, image, generation=0):
        """Reuse only an unchanged single-window canvas and its delivery proof."""
        from computer_use.protocol.captures import capture_metadata

        if image.expired or image.unavailable_mapping or len(image.pieces) != 1:
            return
        piece = image.pieces[0]
        if image.png != piece.image.image:
            return
        metadata = capture_metadata(image.png)
        if piece.box != (0, 0, metadata["width"], metadata["height"]):
            return
        target = piece.image.target
        identity = WindowIdentity(piece.window, target.surface, target.instance)
        evidence = state.evidence.get(identity)
        if (
            target.session != state.session
            or target.root_window != piece.window
            or evidence is None
            or evidence.lifetime is WindowLifetime.RETIRED
        ):
            return
        if generation < state.window_image_generations.get(identity, -1):
            return
        state.window_images[identity] = image
        state.window_image_generations[identity] = generation
        view = state.views.get((identity.window, identity.surface, identity.instance))
        if view is not None and (view._image is None or view._image is self._shared_images.get(view)):
            view._image = image
            self._shared_images.set(view, image)

    def attach_image(self, state, identity, view):
        """Bootstrap an unobserved Window from an exact actually delivered map."""
        image = state.window_images.get(identity)
        if image is not None and not image.expired and view._image is None:
            view._image = image
            self._shared_images.set(view, image)

    def choices(self, directory, name=""):
        aliases = self.register_directory(directory, name)
        items = {item["window"]: item for item in directory["surfaces"]}
        return [FrozenRecord(dict(id=int(ref), title=items[key]["title"])) for ref, key in aliases.items()]
