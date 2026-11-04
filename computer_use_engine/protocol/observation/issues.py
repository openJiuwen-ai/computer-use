# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Observation-local lookups for exact and overlapping native read issues."""

from __future__ import annotations

from collections.abc import Iterable

from computer_use.protocol.observation.model import FieldPath, ReadIssue


class ReadIssueIndex:
    """Partition immutable issues without changing their order or field scope."""

    def __init__(self, issues: Iterable[ReadIssue]) -> None:
        self.paths: dict[FieldPath, list[tuple[int, ReadIssue]]] = {}
        self.nodes: dict[str, list[tuple[int, ReadIssue]]] = {}
        self.general: list[tuple[int, ReadIssue]] = []
        for order, issue in enumerate(issues):
            self.paths.setdefault(issue.path, []).append((order, issue))
            if len(issue.path) >= 2 and issue.path[0] == "nodes":
                self.nodes.setdefault(issue.path[1], []).append((order, issue))
            else:
                self.general.append((order, issue))

    def applicable(self, path: FieldPath) -> list[ReadIssue]:
        """Resolve exact and ancestor facts; collection gaps do not erase members."""
        if len(path) < 2:
            return [issue for _, issue in self.paths.get(path, ())]
        matching = [item for length in range(2, len(path) + 1) for item in self.paths.get(path[:length], ())]
        matching.sort(key=lambda item: item[0])
        return [issue for _, issue in matching]

    def for_node(self, token: str) -> tuple[ReadIssue, ...]:
        """Return only restrictions explicitly attached to this original node."""
        return tuple(issue for _, issue in self.nodes.get(token, ()))

    def overlapping(self, path: FieldPath) -> tuple[ReadIssue, ...]:
        """Retain ancestor and descendant restrictions in original capture order."""
        candidates = list(self.general)
        if len(path) >= 2 and path[0] == "nodes":
            candidates.extend(self.nodes.get(path[1], ()))
        else:
            candidates.extend(item for group in self.nodes.values() for item in group)
        return tuple(
            issue
            for _, issue in sorted(candidates, key=lambda item: item[0])
            if issue.path[: len(path)] == path or path[: len(issue.path)] == issue.path
        )
