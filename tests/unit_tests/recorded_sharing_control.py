# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Load the captured sharing-control reply and its recorded property projection."""

import json
from copy import deepcopy
from pathlib import Path

_CAPTURE = Path(__file__).parents[1] / "fixtures" / "recorded" / "offscreen-mute-sharing-control.json"
_TOOLKIT_KEYS = {
    ("toolkit", "actions"),
    ("toolkit", "attribute_names"),
    ("toolkit", "action_names"),
    ("toolkit", "action_description"),
}


def load_sharing_control_recording():
    fixture = json.loads(_CAPTURE.read_text())
    provenance, raw = fixture["provenance"], fixture["raw_reply"]
    keys = _TOOLKIT_KEYS | {
        ("macos.ax", name) for name in (*provenance["narrow_fields"], *provenance["retained_relation_fields"])
    }
    nodes = [
        node if node["properties"] is None else {
            **node, "properties": [prop for prop in node["properties"] if tuple(prop["key"]) in keys],
        }
        for node in raw["nodes"]
    ]
    # The old serialized replies were independent mutable inputs to the decoder.
    fixture["narrow_raw_reply"] = deepcopy({**raw, "nodes": nodes})
    return fixture
