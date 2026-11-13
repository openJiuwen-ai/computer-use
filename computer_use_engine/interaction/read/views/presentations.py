# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Captured menu presentation facts; no input gates or native lifetime changes."""

from computer_use.session.interfaces import WindowIdentity
from computer_use.session.surface_content import content_host, content_kind

OPENERS = frozenset({"menu_button", "popup_button", "combo_box", "menu_bar_item"})
CONTENTS = frozenset({"menu", "list"})


def source(target):
    return {"window": target.root_window, "surface": target.surface, "instance": target.instance}


def _removed(snapshot, opener, content):
    return snapshot.capture.menu_removed(opener, content)


def _presentations(snapshot):
    return snapshot.capture.menu_presentations()


def collect_presentations(previous, directory, parts):
    """Reconcile only the exact scopes sampled here, retaining unknown separately.

    AX children under a menu opener describe its content. They neither prove
    a new physical window nor require one. Missing directory rows alone never
    prove a menu ended. Application menu command trees are not visible menus.
    """
    found = {}
    current = {WindowIdentity.of(item) for item in directory["surfaces"]}
    confirmed_parts = []
    for part in parts:
        identity = WindowIdentity(part.target.root_window, part.target.surface, part.target.instance)
        if identity in current and (part.source is None or part.source.scope_confirmed):
            confirmed_parts.append(part)
    parts = confirmed_parts
    trees = {part.target.surface: part for part in parts
             if part.target.root_window is not None and part.tree is not None}
    sampled_windows = {part.target.root_window for part in parts}
    members = {item["window"]: item for item in directory["surfaces"]}
    for part in trees.values():
        for opener, key in _presentations(part.tree):
            identity = (part.target.session, part.target.surface, key)
            found[identity] = {
                "id": identity, "host": part.target.root_window, "node": key, "opener": opener,
                "state": "observed", "kind": "menu", "evidence": "openerChildren",
                "sources": (source(part.target),),
            }
    for item in members.values():
        if content_kind(item) not in ("menu", "popup"):
            continue
        host = content_host(item)
        host_node = item.get("content", {}).get("hostNode")
        key = (directory["session"], members[host]["surface"], host_node) if host in members and host_node else (
            directory["session"], item["surface"], item["node"] or item["window"]
        )
        own_source = {field: item[field] for field in ("window", "surface", "instance")}
        if key in found:
            found[key]["sources"] += (own_source,)
            continue
        for part in parts:
            identity = WindowIdentity(part.target.root_window, part.target.surface, part.target.instance)
            if identity == WindowIdentity.of(item):
                break
        else:
            part = None
        if part is None or part.tree is None and part.image is None:
            continue
        found[key] = {
            "id": key, "host": host, "node": key[2], "opener": None,
            "state": "observed", "kind": content_kind(item),
            "evidence": item.get("content", {}).get("evidence", "nativeMenu"),
            "sources": (own_source,),
        }
    ended = {WindowIdentity.of(item) for item in directory.get("endedWindows", ())}
    result = {}
    for key, value in previous.items():
        if key in found:
            continue
        part = trees.get(key[1])
        state, evidence = value["state"], value["evidence"]
        if state == "ended":
            pass  # Only fresh positive presentation evidence reopens this record.
        elif part is not None and value.get("opener") is not None:
            state = "ended" if _removed(part.tree, value["opener"], value["node"]) else "unconfirmed"
            evidence = "openerChildrenRemoved" if state == "ended" else "notObservedInRead"
        elif any(WindowIdentity.of(item) in ended for item in value["sources"]):
            state, evidence = "ended", "nativeLifetimeEnded"
        elif part is not None or value["host"] is not None and value["host"] in sampled_windows:
            # A host read does not confirm that its separately captured popup
            # remains presented. Catalogue absence alone still cannot end it.
            state, evidence = "unconfirmed", "notObservedInRead"
        result[key] = {**value, "state": state, "evidence": evidence,
                       "transition": state if state != value["state"] else None}
    for key, value in found.items():
        old = previous.get(key)
        transition = "reappeared" if old is not None and old["state"] == "ended" else (
            "observed" if old is None or old["state"] != "observed" else None
        )
        result[key] = {**value, "transition": transition}
    # This is a small acquisition ledger, not a lifetime registry or a history
    # of all commands. No input route depends on retaining these notices.
    return tuple(result.values())[-256:]


def presentation_notices(records, aliases):
    refs = {key: ref for ref, key in aliases.items()}
    notices = []
    for item in records:
        if item.get("transition") is None:
            continue
        ref = refs.get(item["host"])
        scope = f" for window={ref}" if ref is not None else " (host unconfirmed)"
        label = "Popup" if item.get("kind") == "popup" else "Menu"
        if item["state"] == "observed":
            notices.append(label + " contents observed" + scope + ".")
        elif item["state"] == "ended":
            notices.append(label + " presentation ended" + scope + ".")
        else:
            notices.append(label + " presentation not confirmed in this read" + scope + ".")
    return tuple(dict.fromkeys(notices))
