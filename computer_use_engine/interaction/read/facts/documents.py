# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Expose document addresses independently of an element's navigation URL."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from computer_use.interaction.read.facts.index import data
from computer_use.protocol.observation import Supplement


@dataclass(frozen=True)
class DocumentProjection:
    """A document field with the exact supplementary records it represents.

    Attributes:
        field: Public semantic field, or unresolved reports retaining raw values.
        value: One complete address, or the original records keyed by source path.
        sources: All supplement fields represented, including agreeing duplicates.
    """

    field: Literal["documentUrl", "documentReports"]
    value: str | Mapping[str, Any]
    sources: frozenset[str]


def document_projection(source: Mapping[str, Any], supplements: Sequence[Supplement]) -> DocumentProjection | None:
    """Interpret only the normalized document URI supplement without rewriting it.

    A complete string identifies the represented document, independently of URL,
    name or value. Conflicting duplicates, extra metadata, invalid types and read
    gaps retain every original report instead of choosing a document address.

    Args:
        source: Indexed fields and read statuses for one immutable native node.
        supplements: That node's original supplementary records. These retain an
            explicit null value omitted by the generic optional-field index.

    Returns:
        A complete address or unresolved source reports; None if none were read.
    """
    reports = {
        key: {**item, "value": data(supplements[source["fieldPaths"][key][1]].value)}
        for key, item in source["values"].items()
        if key.startswith("supplements[") and item["key"] == "document_uri"
    }
    if not reports:
        return None
    paths = [source["fieldPaths"][key] for key in reports]
    incomplete = any(
        path[: len(status_path)] == status_path or status_path[: len(path)] == path
        for key in source["fieldStatus"]
        for status_path in (source["fieldPaths"][key],)
        for path in paths
    )
    values = [record["value"] for record in reports.values()]
    complete = (
        not incomplete
        and all(set(record) == {"key", "value"} for record in reports.values())
        and all(type(value) is str for value in values)
        and len(set(values)) == 1
    )
    return DocumentProjection(
        "documentUrl" if complete else "documentReports", values[0] if complete else reports, frozenset(reports)
    )
