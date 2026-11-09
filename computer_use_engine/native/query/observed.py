# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Run the existing path matcher over immutable, already acquired query facts.

This source cannot query the desktop. Missing evidence is a local cache miss;
the caller retains the ordinary fresh query and its original wait semantics.
"""

from dataclasses import dataclass
from types import MappingProxyType

from computer_use.native.query.path import TargetQuery
from computer_use.native.semantics import PROPERTIES


class LookupFactsUnavailable(Exception):
    """The captured source cannot supply one dependency of the existing matcher."""


def _freeze(value):
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


@dataclass(frozen=True)
class CapturedLookupFacts:
    """Only path-query inputs, detached from the wire and full semantic tree."""

    session: str
    platform: str
    surface: str
    roots: tuple
    nodes: object
    edges: object
    node_sources: object
    edge_sources: object


def capture_lookup_facts(facts):
    """Retain narrow immutable query inputs, without another full fact graph.

    The native field selectors come from TargetQuery itself. No normalized
    labels, merged display names or inferred relationship completeness enter
    this source. Strings/scalars are shared; only these small records detach.
    """
    properties = PROPERTIES[facts["platform"]]
    selected = frozenset(
        properties[key] for key in (*TargetQuery.fields_for_path, "windowContentScope") if key in properties
    )
    nodes = {}
    for row in facts["nodes"]:
        nodes[row["node"]] = MappingProxyType(
            {
                "node": row["node"],
                "requestedFields": tuple(key for key in row["requestedFields"] if key in selected),
                "values": MappingProxyType(
                    {key: _freeze(value) for key, value in row["values"].items() if key in selected}
                ),
                "fieldStatus": MappingProxyType(
                    {key: _freeze(value) for key, value in row["fieldStatus"].items() if key in selected}
                ),
                "actions": MappingProxyType(
                    {
                        "state": row["actions"]["state"],
                        "items": tuple(
                            MappingProxyType({"name": action["name"]}) for action in row["actions"]["items"]
                        ),
                    }
                ),
            }
        )
    edges = {(edge["source"], edge["property"]): _freeze(edge) for edge in facts["relations"]}
    observed = facts["observed"]["id"]
    return CapturedLookupFacts(
        facts["session"],
        facts["platform"],
        facts["surface"],
        tuple(facts["roots"]),
        MappingProxyType(nodes),
        MappingProxyType(edges),
        MappingProxyType({token: observed for token in nodes}),
        MappingProxyType({key: (observed,) for key in edges}),
    )


class _CapturedPort:
    def __init__(self, platform, capabilities):
        self.platform, self.capabilities = platform, capabilities

    async def query_relation(self, *args, **kwargs):
        raise LookupFactsUnavailable()


class ObservedTargetQuery(TargetQuery):
    """Same matcher, bounded to a captured source with no OS fallback inside it."""

    def __init__(self, facts, root, remaining, capabilities):
        super().__init__(_CapturedPort(facts.platform, capabilities), None, root, remaining)
        if (facts.session, facts.surface) != (root.session, root.surface) or root.node not in facts.roots:
            raise LookupFactsUnavailable()
        # NativeGraph owns query-local traversal/dependency state. Its facts
        # are read-only here and shared by every query of this observation.
        self.nodes, self.edges = facts.nodes, facts.edges
        self.node_sources, self.edge_sources = facts.node_sources, facts.edge_sources

    async def _collect_nodes(self, *args, **kwargs):
        raise LookupFactsUnavailable()
