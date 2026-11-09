# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Observation-owned name sources, separate from live input and delivery state."""

from collections import OrderedDict
from dataclasses import dataclass, field
from types import MappingProxyType

from computer_use.errors import ComputerUseError
from computer_use.native.contracts import Binding
from computer_use.native.query.observed import LookupFactsUnavailable, ObservedTargetQuery


def scope_key(target):
    return target.session, target.instance, target.root_window, target.surface, target.node


def _path_key(path):
    # The original matcher evaluates conditions by their meaning, not the
    # caller's dictionary insertion order. Positive and invalidation indexes
    # must describe that same query.
    return tuple(tuple(sorted(step.items())) for step in path)


@dataclass(eq=False)
class ObservedLookupScope:
    target: object
    facts: object
    snapshots: object
    generation: int
    capabilities: tuple
    # Successful results only; absence never suppresses a future live query.
    matches: OrderedDict = field(default_factory=OrderedDict, repr=False)
    invalidated: set = field(default_factory=set, repr=False)
    released: bool = False

    def release(self):
        """End a captured cache lifetime without changing numbered bindings."""
        self.facts = None
        self.snapshots = MappingProxyType({})
        self.matches.clear()
        self.invalidated.clear()
        self.released = True

    async def resolve(self, path, remaining):
        remaining()
        if self.facts is None:
            raise LookupFactsUnavailable()
        key = _path_key(path)
        if key in self.invalidated:
            raise LookupFactsUnavailable()
        if key in self.matches:
            self.matches.move_to_end(key)
            return self.matches[key]
        query = ObservedTargetQuery(self.facts, self.target, remaining, self.capabilities)
        target = await query.resolve([dict(step) for step in path])
        snapshot = self.snapshots.get(target.node)
        if snapshot is None or snapshot["binding"] != target:
            raise LookupFactsUnavailable()
        self.matches[key] = snapshot
        if len(self.matches) > 128:
            self.matches.popitem(last=False)
        return snapshot


def captured_lookup_scopes(instance, trees, shown, generation, capabilities, window_coverage=()):
    """Retain original numbered snapshots only from returned semantic trees."""
    scopes = []
    for target, tree in trees:
        if not any(source is tree.native for source in shown):
            continue
        snapshots = {}
        for element_ref, token in tree.refs.items():
            snapshot = instance.refs.get(element_ref)
            if snapshot is not None and scope_key(snapshot["binding"])[:4] == scope_key(target)[:4]:
                snapshots[token] = snapshot
        scopes.append(
            ObservedLookupScope(
                target,
                getattr(tree.collection, "lookup_facts", None),
                MappingProxyType(snapshots),
                generation,
                tuple(capabilities),
            )
        )
    for record in window_coverage:
        if "tree" in record.get("attempted", ()) and "tree" not in record.get("included", ()):
            # A returned failed/omitted tree is a newer knowledge gap, not
            # permission to fill it with an older complete tree. Image-only
            # observations do not replace semantic facts.
            target = Binding(instance.session, record["instance"], record["window"], record["surface"], None)
            if not any(scope_key(source.target)[:4] == scope_key(target)[:4] for source in scopes):
                scopes.append(ObservedLookupScope(target, None, MappingProxyType({}), generation, tuple(capabilities)))
    instance.lookup_captures.update(scopes)
    return tuple(scopes)


def merge_lookup_scopes(previous, sources):
    """Replace only the captured scope, without merging old gaps into new facts."""
    result = OrderedDict(previous)
    for lookup_scope in sources:
        if lookup_scope.released:
            continue
        key = scope_key(lookup_scope.target)
        related = tuple((prior_key, value) for prior_key, value in result.items() if prior_key[:4] == key[:4])
        if any(value.generation > lookup_scope.generation for _, value in related):
            continue
        for prior_key, value in related:
            if value.generation < lookup_scope.generation:
                result.pop(prior_key)
        existing = result.get(key)
        if existing is None or lookup_scope.generation >= existing.generation:
            result[key] = lookup_scope
            result.move_to_end(key)
    while len(result) > 128:
        result.popitem(last=False)
    return result


def discard_lookup_results(instance, groups, path=None):
    """Discard affected proofs, including earlier smaller/larger scope sets."""
    for key in tuple(instance.lookup_results):
        if path is not None and key[1] != path:
            continue
        if any(scope_key(root)[:4] in groups for root, _generation in key[0]):
            instance.lookup_results.pop(key, None)


def publish_lookup_scopes(instance, sources):
    """Publish facts and retire superseded/evicted proofs in the same boundary."""
    previous = instance.lookup_observations
    merged = merge_lookup_scopes(previous, sources)
    changed = {key[:4] for key in previous.keys() | merged.keys() if previous.get(key) is not merged.get(key)}
    discard_lookup_results(instance, changed)
    instance.lookup_observations = merged


def _sources(instance, continuation):
    if continuation is None:
        return instance.lookup_observations
    return merge_lookup_scopes(instance.lookup_observations, continuation.sources.values())


def _result_key(controller, owners, path, sources):
    roots = []
    for owner in dict.fromkeys(owners):
        target = controller.state(owner).query_target
        if target is None:
            return None
        source = sources.get(scope_key(target))
        roots.append((target, source.generation if source is not None else None))
    return tuple(roots), _path_key(path)


def remember_lookup_result(controller, instance, owners, path, snapshot, continuation=None):
    """Retain only the exact positive fact proven by the complete live lookup."""
    key = _result_key(controller, owners, path, _sources(instance, continuation))
    if key is not None:
        instance.lookup_results[key] = snapshot
        instance.lookup_results.move_to_end(key)
        while len(instance.lookup_results) > 128:
            instance.lookup_results.popitem(last=False)


def invalidate_lookup_result(controller, instance, owners, path, continuation=None):
    """A new live attempt supersedes its old proof, never caches absence.

    Even a failed fresh lookup must not resurrect an older positive result.
    Invalidated paths always perform the original live query again. Exhausting
    the optional index budget discards that source, not its numbered bindings.
    """
    path_key = _path_key(path)
    groups = set()
    for owner in dict.fromkeys(owners):
        target = controller.state(owner).query_target
        if target is not None:
            groups.add(scope_key(target)[:4])
    discard_lookup_results(instance, groups, path_key)
    # Include pending delivery sources as well as visible/continuation facts.
    # The weak registry grants no query access and owns no captured payload;
    # it only prevents later delivery from reviving already-disproved facts.
    for capture in tuple(instance.lookup_captures):
        if scope_key(capture.target)[:4] in groups:
            capture.matches.pop(path_key, None)
            capture.invalidated.add(path_key)
            if len(capture.invalidated) > 128:
                capture.facts = None
                capture.matches.clear()
                capture.invalidated.clear()


async def resolve_observed(controller, instance, owners, path, remaining, continuation=None):
    """Resolve every original scope or fall back as a whole to the live query.

    Old ambiguity/incompleteness is not a current failure. Only a fully
    supported unique positive match may avoid the existing lookup pipeline.
    Cancellation and unexpected implementation failures still propagate.
    """
    sources = _sources(instance, continuation)
    key = _result_key(controller, owners, path, sources)
    if key is not None and key in instance.lookup_results:
        remaining()
        instance.lookup_results.move_to_end(key)
        return instance.lookup_results[key]
    matches = {}
    for owner in dict.fromkeys(owners):
        remaining()
        target = controller.state(owner).query_target
        source = sources.get(scope_key(target)) if target is not None else None
        if source is None or source.target != target:
            return None
        try:
            snapshot = await source.resolve(path, remaining)
        except LookupFactsUnavailable:
            return None
        except ComputerUseError as error:
            if error.code == "element_not_found":
                continue
            if error.code in ("observation_incomplete", "ambiguous_target"):
                return None
            raise
        binding = snapshot["binding"]
        matches[(binding.session, binding.surface, binding.node)] = snapshot
    return next(iter(matches.values())) if len(matches) == 1 else None
