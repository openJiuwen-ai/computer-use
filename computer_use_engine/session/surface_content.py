# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Observation content relations, independent of physical input ancestry."""


def content_kind(item):
    content = item.get("content")
    if content is not None:
        return content["kind"]
    # Providers without semantic content facts retain their native role facts.
    return "menu" if item["kind"] == "menu" else "window"


def content_host(item):
    content = item.get("content")
    return content.get("host") if content is not None else None


def transient_content(item):
    return content_kind(item) in ("menu", "popup")


def content_within(items, window, root):
    """Use confirmed logical and physical edges only for interaction scope.

    This is not permission to dispatch events across those edges. The chosen
    input mechanism still qualifies the exact bound receiver at its boundary.
    """
    pending, seen = [window], set()
    while pending:
        key = pending.pop()
        if key == root:
            return True
        if key in seen:
            continue
        seen.add(key)
        item = items.get(key)
        if item is None:
            continue
        host = content_host(item)
        if host is not None:
            pending.append(host)
        if item["parentState"] == "known" and item["parent"] is not None:
            pending.append(item["parent"])
    return False


def display_host(items, window):
    """Project a confirmed attached source to its public working scope."""
    seen = set()
    while window not in seen:
        seen.add(window)
        item = items.get(window)
        host = content_host(item) if item is not None else None
        if host is None or host not in items:
            return window
        window = host
    return None
