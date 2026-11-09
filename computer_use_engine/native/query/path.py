# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Bounded semantic path resolution inside an already-open native operation.

This consumes native facts, never display text. Native adapters only collect
objects/relations; matching, wrapper boundaries and uniqueness live here.
"""

import asyncio
from dataclasses import replace

from computer_use.errors import ComputerUseError
from computer_use.native.query.graph import UNKNOWN, NativeGraph
from computer_use.native.semantics import RELATIONS, followed_relations, role_name
from computer_use.protocol.element_path import element_path


async def resolve_path(port, operation, root, remaining, path, *, timeout_ms=0, strict_window=False):
    """Wait only for absence, within the same lease and total call deadline.

    A zero timeout means one lookup, not zero time for native IPC. Each retry
    discards earlier matching facts; ambiguity, partial data and stale scopes
    are never made to look like an element that has merely not appeared yet.
    """
    # Exact types preserve scalar validation and identity contracts.
    # pylint: disable-next=unidiomatic-typecheck
    if type(timeout_ms) is not int or not 0 <= timeout_ms <= 10000:
        raise ValueError("Path wait must be an integer in 0..10000 milliseconds")
    deadline = asyncio.get_running_loop().time() + timeout_ms / 1000
    while True:
        query = TargetQuery(port, operation, root, remaining, strict_window=strict_window)
        try:
            return await query.resolve(path), query.within
        except ComputerUseError as error:
            delay = min(0.1, deadline - asyncio.get_running_loop().time())
            if error.code != "element_not_found" or error.effect != "none" or delay <= 0:
                raise
            await asyncio.sleep(min(delay, remaining() / 1000))


class TargetQuery(NativeGraph):
    failure_target = "element path"
    fields_for_path = (
        "role", "name", "description", "identifier", "subrole", "focused", "modal", "value", "nativeWindowHandle",
    )

    def __init__(self, port, operation, root, remaining, *, max_nodes=4096, max_depth=40, strict_window=False):
        contents = followed_relations(port.platform, getattr(port, "capabilities", ()), content_only=True)
        super().__init__(
            port,
            operation,
            root,
            remaining,
            max_nodes=max_nodes,
            max_depth=max_depth,
            fields=self.fields_for_path
            + (("windowContentScope",) if "nativeWindowContent" in getattr(port, "capabilities", ()) else ()),
            follow_relations=contents,
        )
        self.content_kinds = tuple(RELATIONS[self.platform][name] for name in contents)
        if strict_window:
            self.query_options["strict_window"] = True
        self.within = ()

    async def _name(self, token):
        if self.platform == "windows":
            # UIA Name is the provider's accessible name (including its label).
            # An observed empty Name is not an instruction to infer a new name
            # from HelpText or require optional LabeledBy on every layout node.
            # Failed/unread Name remains UNKNOWN and still prevents ambiguity
            # from being silently treated as a unique match.
            value = await self._field(token, "name")
            return "" if value is None else value
        for field in ("name", "description"):
            value = await self._field(token, field)
            if value is UNKNOWN or value:
                return value
        labels = await self._relation(token, "labelledBy")
        if not labels:
            return ""
        if len(labels) != 1:
            return UNKNOWN
        label = labels[0]
        title = await self._field(label, "name")
        if title is UNKNOWN or title:
            return title
        value = await self._field(label, "value")
        return "" if value is None else value

    async def _matches(self, token, condition):
        unknown = False
        for key in ("role", "identifier", "name"):
            if key not in condition:
                continue
            value = await self._name(token) if key == "name" else await self._field(token, key)
            if value is UNKNOWN:
                unknown = True
                continue
            if key == "role":
                value = role_name(self.platform, value)
            if value != condition[key]:
                return False
        return None if unknown else True

    async def _transparent(self, token, role):
        if role == "window":
            return await self._inline_window(token, role)
        if role == "menu":
            return True
        if role not in ("group", "unknown", "scrollArea"):
            return False
        values = [
            await self._name(token),
            await self._field(token, "identifier"),
            await self._field(token, "subrole"),
            await self._field(token, "focused"),
            await self._field(token, "modal"),
        ]
        if any(value is not UNKNOWN and bool(value) for value in values):
            return False
        actions = (await self._node(token))["actions"]
        # UIA ScrollItem only offers to reveal this object in its viewport;
        # it does not make an otherwise anonymous layout an action boundary.
        # Keep every other action and every non-Windows provider conservative.
        if any(
            self.platform != "windows" or action["name"] != "ScrollItem.ScrollIntoView" for action in actions["items"]
        ):
            return False
        return None if UNKNOWN in values or actions["state"] != "complete" else True

    async def _contents(self, token):
        children = list(await self._relation(token, "children"))
        for kind in self.content_kinds:
            children.extend(await self._relation(token, kind, read_missing=True, optional=True))
        # Rows/cells can be shared by several content relations or span several
        # coordinates. They are one native candidate, not repeated matches.
        unique = dict.fromkeys(children)
        if not self.outside_window_nodes:
            return tuple(unique)
        return tuple(child for child in unique if child not in self.outside_window_nodes)

    async def _next_relation(self, token, relation_property, cursor, limit):
        page, batch = await super()._next_relation(token, relation_property, cursor, limit)
        first_children_page = (
            self.platform == "macos" and relation_property == self.relations["children"] and cursor is None
        )
        if not first_children_page or batch is None:
            return page, batch
        if page["relation"]["state"] != "complete" or page["relation"]["certainty"] != "confirmed":
            return page, batch
        # A first Children page also follows its source's requested content
        # relations. Accept only complete same-source rows for relations whose
        # earlier depth-limited sample had a count and no consumed identities.
        collected = {node["node"] for node in batch["nodes"]}
        for edge in batch["relations"]:
            prop = edge["property"]
            key = token, prop
            previous = self.edges.get(key)
            if edge["source"] != token or prop not in self.follow_relations or key in self.completed_edges:
                continue
            if previous is None or previous["state"] != "partial" or previous["certainty"] != "confirmed":
                continue
            has_consumed_identities = (
                previous["targets"] or previous.get("offset", 0) or previous.get("cursor") is not None
            )
            if has_consumed_identities or previous.get("unavailablePositions") or previous.get("membershipObservedAt"):
                continue
            if edge["state"] != "complete" or edge["certainty"] != "confirmed" or not edge["targets"]:
                continue
            if len(edge["targets"]) != previous.get("total") or edge.get("total") != previous.get("total"):
                continue
            if any(ref not in self.nodes and ref not in collected for ref in edge["targets"]):
                continue
            self.edges[key] = edge
            self.edge_sources[key].append(batch["observed"]["id"])
        return page, batch

    async def _inline_window(self, token, role):
        if (
            self.platform == "windows"
            and role == "window"
            and "nativeWindowContent" in getattr(self.port, "capabilities", ())
        ):
            # The backend confirms native containment in this fixed working
            # window. An unknown/independent host is still a boundary.
            scope = await self._field(token, "windowContentScope")
            return True if scope == "content" else False if scope == "independent" else None
        # A windowless UIA modal (for example an HTML dialog) is content of
        # this already-bound native surface. ControlType.Window alone does
        # not establish an independent HWND. Unknown or real HWNDs retain
        # the window boundary; execution still checks live containment.
        if self.platform != "windows" or role != "window":
            return False
        if await self._field(token, "modal") is not True:
            return False
        handle = await self._field(token, "nativeWindowHandle")
        # pylint: disable-next=unidiomatic-typecheck
        return type(handle) is int and handle == 0

    async def resolve(self, path):
        path = element_path(path)
        if self.root.node is None or self.root.capture is not None:
            raise ValueError("A path query needs an observed accessible root")
        if self.platform == "macos" and "nativeQueryActionProfile" in getattr(self.port, "capabilities", ()):
            self.query_options["action_detail"] = "none" if len(path) == 1 else "names"
            if len(path) == 1:
                # The first path step explores every candidate directly. Only
                # later steps test anonymous wrappers through _transparent.
                fields = ["role"]
                if "name" in path[0]:
                    fields.extend(("name", "description", "value"))
                if "identifier" in path[0]:
                    fields.append("identifier")
                self.fields = tuple(fields)
        selected = self.root.node
        anchors = [selected]
        for step, condition in enumerate(path):
            candidates = [self.root.node] if step == 0 else await self._contents(selected)
            found, visited, incomplete = [], set(), False
            # Native depth limits each collection. The logical search shares
            # the graph's node and time budgets across all of its collections.
            active = set()
            stack = [(node, True) for node in reversed(candidates)]
            while stack:
                token, entering = stack.pop()
                if not entering:
                    active.remove(token)
                    continue
                self.remaining()
                if token in active:
                    self.fail("observation_incomplete", "Path containment is cyclic.")
                if token in visited:
                    continue  # One shared native object is still one candidate.
                if len(visited) >= self.max_nodes:
                    self.fail("observation_incomplete", "Path containment exceeds its node budget.")
                visited.add(token)
                if token in self.outside_window_nodes:
                    continue
                try:
                    raw_role = await self._field(token, "role")
                except ComputerUseError as error:
                    if self.query_options.get("strict_window") and error.code == "outside_window_scope":
                        continue
                    raise
                role = role_name(self.platform, raw_role) if raw_role is not UNKNOWN else "unknown"
                if token != self.root.node and role in ("window", "sheet"):
                    inline = await self._inline_window(token, role)
                    if inline is not True:
                        # Unknown containment can hide another matching control
                        # in this working window. It cannot prove uniqueness in
                        # another branch, or a complete negative lookup.
                        incomplete |= inline is None
                        continue  # Select independent surfaces via App methods' window= parameter.
                matched = await self._matches(token, condition)
                incomplete |= matched is None
                if matched is True:
                    found.append(token)
                    if len(found) > 1:
                        self.fail("ambiguous_target", f"Path step {step + 1} has more than one native match.")
                if step == 0:
                    descend = condition.get("role") not in ("window", "sheet", "menuBar")
                    if matched is False and role == "textField" and condition.get("role") == "textField":
                        # An addressed field query searches logical controls,
                        # not private editors inside other named fields. Their
                        # failed Children getter cannot hide a sibling field.
                        descend = False
                else:
                    descend = False if matched is True else await self._transparent(token, role)
                incomplete |= descend is None
                if descend is True:
                    children = await self._contents(token)
                    active.add(token)
                    stack.append((token, False))
                    stack.extend((child, True) for child in reversed(children))
            if incomplete:
                self.fail(
                    "observation_incomplete",
                    f"Path step {step + 1} cannot establish a unique match from the available facts.",
                )
            if not found:
                self.fail("element_not_found", f"No native element matches path step {step + 1}.")
            selected = found[0]
            if step > 0 and selected in anchors:
                self.fail("observation_incomplete", "Path steps cannot select an ancestor again.")
            if selected not in anchors:
                anchors.append(selected)
        # The root and explicit path anchors constrain the logical target.
        # Anonymous wrappers and mutable names are deliberately not guards.
        self.within = tuple(anchors[:-1])
        return replace(self.root, node=selected)
