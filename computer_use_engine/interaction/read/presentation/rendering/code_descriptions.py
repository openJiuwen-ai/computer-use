# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Select supplementary code-shaped descriptions for same-capture details."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
import re
from types import MappingProxyType
from typing import TYPE_CHECKING

from computer_use.interaction.read.presentation.compression.evidence import CompressionEvidence
from computer_use.interaction.read.presentation.ir import FactKey, SourceKey
from computer_use.interaction.read.presentation.rendering.native_descriptions import NativeDescriptionField

if TYPE_CHECKING:
    from computer_use.interaction.read.presentation.ir import ProjectionResult
    from computer_use.interaction.read.presentation.records import SnapshotFacts


_QUALIFIED_CODE = re.compile(r"[a-z][a-z0-9_]*(?:__[a-z][a-z0-9_]*){2,}\Z")
_CAPTIONS = ("name", "value")
_SUPPLEMENT_ROLES = frozenset(
    ("button", "checkbox", "radio_button", "switch", "popup_button", "menu_button", "static_text", "image", "link")
)


def _caption(node: Mapping[str, object]) -> str | None:
    """Find a complete ordinary caption independent of the technical description."""
    for key in _CAPTIONS:
        value = node.get(key)
        if (
            isinstance(value, str)
            and value.strip()
            and len(value) <= 256
            and not _QUALIFIED_CODE.fullmatch(value)
            and (
                any(character.isalpha() and not character.isascii() for character in value)
                or re.fullmatch(r"[A-Za-z][A-Za-z '\u2019.?!\u2026-]*", value)
            )
        ):
            return value
    return None


def capture_code_descriptions(
    snapshot: SnapshotFacts,
    projection: ProjectionResult,
) -> Mapping[FactKey, NativeDescriptionField]:
    """Keep complete technical supplements behind the original element's detail route.

    A description qualifies only with a separately complete ordinary caption.
    Different descriptions distinguishing otherwise equal controls remain inline.
    Business prose, sole labels, errors, edits and uncertain source mappings keep
    their existing representation. Original properties and capabilities are retained.

    Args:
        snapshot: Captured facts and per-element native detail sources.
        projection: Source-preserving display projection before block organization.

    Returns:
        Exact source fields selected for details in this captured whole view.
    """
    evidence = projection.source.context.get("compressionEvidence")
    if not isinstance(evidence, CompressionEvidence) or any(
        not issue.path or issue.path == ("nodes",) and issue.state != "partial" for issue in snapshot.native.issues
    ):
        return MappingProxyType({})
    counts = Counter(snapshot.refs.values())
    records = {node["node"]: node for node in snapshot.facts["nodes"]}
    error_sources: set[str] = set()
    for token, original in snapshot.native.nodes.items():
        for relation in original.relations.values():
            if relation.kind == "error":
                error_sources.add(token)
                error_sources.update(relation.targets)
    peers: dict[tuple[str, str, str], set[object]] = {}
    for ref, node in projection["nativeNodes"].items():
        caption = _caption(node)
        scope = projection.source.scopes.get(ref)
        if caption is not None and scope is not None:
            peers.setdefault((scope, str(node["role"]), caption), set()).add(node.get("description"))
    result = {}
    for ref, node in projection["nativeNodes"].items():
        value, caption = node.get("description"), _caption(node)
        token, scope = snapshot.refs.get(ref), projection.source.scopes.get(ref)
        native = snapshot.native.nodes.get(token)
        record = records.get(token, {})
        source = snapshot.fields.get((ref, "description"))
        caption_key = next((key for key in _CAPTIONS if node.get(key) == caption), None)
        if (
            not isinstance(value, str)
            or _QUALIFIED_CODE.fullmatch(value) is None
            or caption is None
            or caption_key is None
            or scope is None
            or native is None
            or native.role not in _SUPPLEMENT_ROLES
            or token in error_sources
            or counts[token] != 1
            or native.description != value
            or source is None
            or source.get("representation") != "field"
            or source.get("node") != token
            or source.get("field") != "description"
            or tuple(source.get("path", ())) != ("description",)
            or type(source.get("normalized")) is not str
            or source["normalized"] != value
            or record.get("observed") is False
            or record.get("values", {}).get("description") != value
            or "description" in record.get("fieldStatus", {})
            or (ref, "description") not in evidence.complete_text
            or (ref, caption_key) not in evidence.complete_text
            or len(peers.get((scope, str(node["role"]), caption), ())) != 1
            or native.states.editable is True
            or native.states.protected is True
            or node.get("errorRelations")
            or any(node.get(key) for key in ("invalid", "error", "documentText"))
        ):
            continue
        if node["role"] in ("textField", "textArea", "comboBox", "document"):
            continue
        fact = FactKey(SourceKey(ref), "description")
        result[fact] = NativeDescriptionField(
            ref,
            token,
            (snapshot.native.session, snapshot.native.id, scope),
            "description",
            "description",
            ("description",),
            value,
        )
    return MappingProxyType(result)
