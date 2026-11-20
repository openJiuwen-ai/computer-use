# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Synthetic macOS wire captures exercised by the public main read engine."""

import json

from computer_use.interaction.read.accessibility._native import require_read_engine
from computer_use.interaction.read.accessibility.snapshot import NativeSnapshot
from computer_use.interaction.read.numbering.references import DisplayReferences


def scalar(value):
    return {"nativeType": "provider", "data": value}


def element(token, role, title):
    return {
        "id": token,
        "kind": "element",
        "nativeType": "AXUIElement",
        "source": "macos.ax",
        "properties": [
            {"key": ["macos.ax", "AXRole"], "value": scalar(role)},
            {"key": ["macos.ax", "AXTitle"], "value": scalar(title)},
        ],
    }


def children(tokens):
    return {
        "key": ["macos.ax", "AXChildren"],
        "value": {
            "nativeType": "CFArray",
            "data": {"items": [{"nativeType": "AXUIElement", "data": {"ref": token}} for token in tokens]},
        },
        "read": {"complete": True, "details": [
            [key, scalar(value)]
            for key, value in {"relation": True, "certainty": "confirmed", "state": "complete",
                               "total": len(tokens)}.items()
        ]},
    }


def snapshot(
    target, *, nodes=None, observation_id="read", references=None, scope="window", issues=(), interval=(1, 2),
):
    """Admit and number a genuine native capture, without a desktop provider."""
    if nodes is None:
        nodes = [element(target.node, "AXWindow", "Observed form")]
    raw = {
        "kind": "observation",
        "session": target.session,
        "platform": "macos",
        "roots": [target.node],
        "nodes": list(nodes),
        "read": {"intervalNs": list(interval), "details": [
            ["surface", scalar(target.surface)], ["observation_id", scalar(observation_id)],
        ]},
    }
    if issues:
        raw["read"].update(complete=False, issues=list(issues))
    return snapshot_from_wire(raw, references=references, scope=scope)


def snapshot_from_wire(raw, *, references=None, scope="window"):
    body = json.dumps(raw).encode()
    frame = require_read_engine().open_read_frame("json", body, len(body))
    capture = frame.capture(scope, [])
    refs = references if references is not None else DisplayReferences(raw["session"])
    aliases = refs.scoped_batch(capture.surface, scope, tuple(capture.source_tokens))
    return NativeSnapshot.number(capture, dict(zip(capture.source_tokens, aliases, strict=True)))
