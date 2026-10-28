# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Public presentation of typed state reports without inferring input abilities."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


READ_ONLY_FIELDS = {
    "control_value.read_only": "readOnly",
    "text.read_only": "textReadOnly",
    "numeric_value.read_only": "rangeReadOnly",
}
PUBLIC_STATES = frozenset((*READ_ONLY_FIELDS.values(), "unsaved", "invalid"))


def supplement_states(source: Mapping[str, Any]) -> dict[str, tuple[str, object]]:
    """Project agreeing boolean state supplements while retaining source occurrences.

    These keys already belong to the normalized observation vocabulary. A missing,
    conflicting or incomplete report cannot establish a public boolean state; its
    original supplementary record remains available in captured details.
    """
    result: dict[str, tuple[str, object]] = {}
    for name in ("unsaved", "invalid"):
        reports = {
            key: value
            for key, value in source["values"].items()
            if key.startswith("supplements[") and value["key"] == name
        }
        if not reports or any(
            set(value) != {"key", "value"} or type(value["value"]) is not bool for value in reports.values()
        ):
            continue
        if len({value["value"] for value in reports.values()}) != 1:
            continue
        paths = tuple(source["fieldPaths"][key] for key in reports)
        if any(
            path[: len(status)] == status or status[: len(path)] == path
            for key in source["fieldStatus"]
            for status in (source["fieldPaths"][key],)
            for path in paths
        ):
            continue
        result.update((key, (name, value["value"])) for key, value in reports.items())
    return result


def error_relation(relation: Mapping[str, Any], aliases: Mapping[str, str]) -> dict[str, object]:
    """Retain the exact captured validation relationship with public target aliases.

    Its completeness and certainty stay explicit. Error association alone never
    creates an invalid state, and unconfirmed association never moves error text.
    """
    return {
        key: ([aliases[token] for token in value] if key == "targets" else value)
        for key, value in relation.items()
        if key not in ("source", "recordKey")
    }
