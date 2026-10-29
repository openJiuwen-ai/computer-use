# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Session-owned native references and immutable read captures for protocol 6.

References identify retained GObject proxies within an observed surface. They
never resolve by name, and observing a relation does not grant input authority.
"""

from __future__ import annotations

from collections import OrderedDict
from contextlib import contextmanager
from dataclasses import dataclass

from computer_use_linux.contracts.protocol import Failure, token


@dataclass(frozen=True)
class Node:
    ref: str
    surface: str
    obj: object


@dataclass(frozen=True)
class RelationPage:
    node: str
    property: str
    total: int
    offset: int
    prefix: tuple = ()
    coordinate_basis: tuple | None = None


class NativeStore:
    def __init__(self, ax, *, node_limit=16384, text_limit=8 * 1024 * 1024):
        self.ax, self.node_limit, self.text_limit = ax, node_limit, text_limit
        self.nodes, self.keys = OrderedDict(), {}
        self.pins = set()
        self.texts, self.text_size = OrderedDict(), 0
        self.pages = OrderedDict()

    def keep(self, surface, obj):
        key = surface, self.ax.native_key(obj)
        ref = self.keys.get(key)
        if ref is None:
            while len(self.nodes) >= self.node_limit:
                oldest = next((r for r in self.nodes if r not in self.pins), None)
                if oldest is None:
                    raise Failure("Native reference budget exhausted.", "observation_incomplete")
                old = self.nodes.pop(oldest)
                self.keys.pop((old.surface, self.ax.native_key(old.obj)), None)
            ref = token("node")
            self.nodes[ref] = Node(ref, surface, obj)
            self.keys[key] = ref
        self.nodes.move_to_end(ref)
        return ref

    def get(self, ref, surface):
        node = self.nodes.get(ref)
        if node is None:
            raise Failure("Native reference expired; read its original surface again.", "stale_target", ref)
        if node.surface != surface:
            raise Failure("Native reference belongs to another surface.", "stale_target", ref)
        self.nodes.move_to_end(ref)
        return node

    @contextmanager
    def retaining(self):
        previous = self.pins
        self.pins = set(previous)
        try:
            yield self.pins
        finally:
            self.pins = previous

    def text(self, value):
        data = value.encode("utf-16-le")
        if len(data) > self.text_limit:
            return None
        while self.texts and (len(self.texts) >= 64 or self.text_size + len(data) > self.text_limit):
            _, old = self.texts.popitem(last=False)
            self.text_size -= len(old)
        ref = token("text")
        self.texts[ref] = data
        self.text_size += len(data)
        return ref

    def text_page(self, ref, offset, length):
        data = self.texts.get(ref)
        if data is None:
            raise Failure("The immutable text capture expired.", "snapshot_expired", ref)
        total = len(data) // 2
        if offset > total or (
            0 < offset < total and 0xDC00 <= int.from_bytes(data[slice(offset * 2, offset * 2 + 2)], "little") <= 0xDFFF
        ):
            raise Failure("Text offset is outside the capture or splits a surrogate pair.", "invalid_request", ref)
        end = min(total, offset + length)
        if end < total and 0xD800 <= int.from_bytes(data[slice(end * 2 - 2, end * 2)], "little") <= 0xDBFF:
            end -= 1
        return dict(
            text=data[slice(offset * 2, end * 2)].decode("utf-16-le"),
            offset=offset,
            total=total,
            next=end if end < total else None,
        )

    def page(self, node, relation_property, keys, total):
        return self._page(RelationPage(node, relation_property, total, len(keys), prefix=tuple(keys)))

    def coordinate_page(self, node, relation_property, basis, total, offset):
        return self._page(RelationPage(node, relation_property, total, offset, coordinate_basis=basis))

    def _page(self, page):
        ref = token("page")
        self.pages[ref] = page
        while len(self.pages) > 128:
            self.pages.popitem(last=False)
        return ref

    def clear(self):
        self.nodes.clear()
        self.keys.clear()
        self.pins.clear()
        self.texts.clear()
        self.pages.clear()
        self.text_size = 0
