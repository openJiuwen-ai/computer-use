# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Select complete native description diagnostics retained by snapshot details."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING

from computer_use.interaction.read.presentation.ir import FactKey, SourceKey
from computer_use.interaction.read.presentation.accounting.native_details import NATIVE_DIAGNOSTIC_FIELDS

if TYPE_CHECKING:
    from computer_use.interaction.read.presentation.ir import ProjectionResult
    from computer_use.interaction.read.presentation.records import SnapshotFacts
    from computer_use.protocol.records import Record


@dataclass(frozen=True)
class NativeDescriptionField:
    """One complete original scalar whose public diagnostic may be deferred.

    Attributes:
        ref: Original observation-local element number.
        native: Driver-owned object supplying the field.
        scope: Captured session, observation and original presentation root.
        property: Original native fact name used by field accounting.
        field: Normalized presentation attribute.
        path: Original native field path.
        value: Exact original scalar retained in captured native details.
    """

    ref: str
    native: str
    scope: tuple[str, str, str]
    property: str
    field: str
    path: tuple[str, ...]
    value: str

    def destination(self) -> Record:
        """Describe this field's unchanged native destination for final accounting."""
        return dict(
            ref=self.ref,
            native=self.native,
            scope=self.scope,
            property=self.property,
            field=self.field,
            path=self.path,
            value=self.value,
            readState="complete",
            status=None,
        )


@dataclass(frozen=True)
class NativeRoleAlias:
    """A familiar role spelling and its exact original description destination.

    Attributes:
        description: Complete native description retained in captured details.
        role: Unchanged presentation role represented by the short spelling.
        subrole: Specific subtype also represented by that spelling, when present.
        label: Complete English role label for this exact native tuple.
    """

    description: NativeDescriptionField
    role: str
    subrole: str | None
    label: str

    @property
    def fact(self) -> FactKey:
        """Return the original description fact requiring a detail destination."""
        return FactKey(SourceKey(self.description.ref), "roleDescription")


# A role description may be application-defined. Only these complete, exact
# generic descriptions have an equivalent spelling; subtypes keep their meaning.
_COMMON_ROLE_LABELS = MappingProxyType(
    {
        ("button", None, "按钮"): "button",
        ("static_text", None, "文本"): "text",
        ("window", "standard", "标准窗口"): "standard window",
        ("button", "close", "关闭按钮"): "close button",
        ("button", "minimize", "最小化按钮"): "minimize button",
        ("button", "zoom", "缩放按钮"): "zoom button",
        ("button", "fullscreen", "全屏幕按钮"): "full screen button",
        ("menu_bar", None, "菜单栏"): "menu bar",
        ("menu_bar_item", None, "菜单栏项"): "menu bar item",
    }
)


def capture_common_role_aliases(
    snapshot: SnapshotFacts,
    projection: ProjectionResult,
) -> Mapping[str, NativeRoleAlias]:
    """Select exact generic macOS role descriptions without changing source facts.

    A retained node must still represent its original role and subtype. Unknown
    subtypes, custom descriptions and restrictions on these fields retain their
    original spelling. The enclosing renderer records an alias only when the
    original description is actually delivered in the same capture's details.
    """
    if snapshot.native.backend != "macos":
        return MappingProxyType({})
    issues: dict[str, list[tuple[str | int, ...]]] = {}
    for issue in snapshot.native.issues:
        if issue.path == ("nodes",) and issue.state == "partial":
            continue
        if len(issue.path) < 2 or issue.path[0] != "nodes":
            return MappingProxyType({})
        path = issue.path[2:]
        # An optional subtype's confirmed absence adds no subtype meaning. The
        # adapter retains a missing AXSubrole at its selector's supplement path.
        if path in (("role_detail",), ("supplements", "AXSubrole")) and issue.state in ("absent", "unsupported"):
            token = str(issue.path[1])
            original = snapshot.native.nodes.get(token)
            if (
                original is not None
                and original.role_detail is None
                and not issue.alternatives
                and not any(item.key == "provider.role_detail" for item in original.supplements)
            ):
                continue
        issues.setdefault(str(issue.path[1]), []).append(path)
    native = {node["node"]: node for node in snapshot.facts["nodes"]}
    counts = Counter(snapshot.refs.values())
    result: dict[str, NativeRoleAlias] = {}
    pending = list(projection["roots"])
    while pending:
        node = pending.pop()
        pending.extend(node["children"])
        ref = node["ref"]
        token = snapshot.refs.get(ref)
        original = snapshot.native.nodes.get(token)
        if token is None or original is None or counts[token] != 1 or ref not in projection.source.scopes:
            continue
        label = _COMMON_ROLE_LABELS.get((original.role, original.role_detail, original.role_description))
        if label is None or any(item.key == "provider.role_detail" for item in original.supplements):
            continue
        if any(
            not path
            or path[0] in ("role", "role_detail", "role_description")
            or path[0] == "supplements"
            and (len(path) == 1 or path[1] in ("AXSubrole", "provider.role_detail"))
            for path in issues.get(token, ())
        ):
            continue
        fields = ("role", "role_description") + (("role_detail",) if original.role_detail is not None else ())
        valid = True
        for field in fields:
            source = snapshot.fields.get((ref, field))
            attribute = {"role": "role", "role_description": "roleDescription", "role_detail": "subrole"}[field]
            actual = getattr(original, field)
            if (
                source is None
                or source.get("representation") != "field"
                or source.get("node") != token
                or source.get("field") != attribute
                or tuple(source.get("path", ())) != (field,)
                or type(actual) is not str
                or native[token]["values"].get(field) != actual
                or type(source.get("normalized")) is not str
                or source["normalized"] != node.get(attribute)
                or field != "role"
                and source["normalized"] != actual
            ):
                valid = False
                break
        if not valid or node.get("subrole") != original.role_detail:
            continue
        source_node = projection.source.native_nodes.get(ref)
        if (
            source_node is None
            or source_node.get("roleDescription") != original.role_description
            or source_node.get("role") != node["role"]
            or source_node.get("subrole") != original.role_detail
        ):
            continue
        description = NativeDescriptionField(
            ref,
            token,
            (snapshot.native.session, snapshot.native.id, projection.source.scopes[ref]),
            "role_description",
            "roleDescription",
            ("role_description",),
            original.role_description,
        )
        result[ref] = NativeRoleAlias(description, node["role"], original.role_detail, label)
    return MappingProxyType(result)


def capture_native_description_fields(
    snapshot: SnapshotFacts,
    projection: ProjectionResult,
) -> Mapping[FactKey, NativeDescriptionField]:
    """Find complete empty descriptions and audit-only roles of already elided sources.

    This selection changes no compression qualification or source fact. A folded role
    is eligible only when its existing typed declaration identifies the original field;
    retained controls keep their role labels. Unknown reads keep their public diagnostics.
    """
    native = {node["node"]: node for node in snapshot.facts["nodes"]}
    counts = Counter(snapshot.refs.values())
    issues: dict[str, list[tuple[str | int, ...]]] = {}
    for issue in snapshot.native.issues:
        if not issue.path or issue.path == ("nodes",) and issue.state != "partial":
            return MappingProxyType({})
        if len(issue.path) >= 2 and issue.path[0] == "nodes":
            issues.setdefault(str(issue.path[1]), []).append(issue.path[2:])
    visible = set()
    role_facts: set[FactKey] = set()
    pending = list(projection["roots"])
    while pending:
        node = pending.pop()
        visible.add(node["ref"])
        for group in node.get("sourceRoles", ()):
            role_facts.update(group.facts)
        pending.extend(node["children"])
    result: dict[FactKey, NativeDescriptionField] = {}
    for ref, scope in projection.source.scopes.items():
        token = snapshot.refs.get(ref)
        source_node = projection["nativeNodes"].get(ref)
        original = native.get(token)
        if token is None or source_node is None or original is None or counts[token] != 1:
            continue
        for field, (property, path) in NATIVE_DIAGNOSTIC_FIELDS.items():
            fact = FactKey(SourceKey(ref), field)
            if field in ("role", "roleDescription") and (ref in visible or fact not in role_facts):
                continue
            source = snapshot.fields.get((ref, property))
            value = original["values"].get(property)
            actual_node = snapshot.native.nodes.get(token)
            actual = getattr(actual_node, path[0], None)
            if len(path) == 2:
                actual = getattr(actual, path[1], None)
            normalized = source_node.get(field) if field == "role" else value
            if (
                type(value) is not str
                or type(actual) is not str
                or actual != value
                or field in ("valueDescription", "rangeDescription")
                and value != ""
                or source is None
                or source.get("representation") != "field"
                or source.get("node") != token
                or source.get("field") != field
                or tuple(source.get("path", ())) != path
                or type(source.get("normalized")) is not str
                or source["normalized"] != normalized
                or type(source_node.get(field)) is not str
                or source_node[field] != normalized
                or any(
                    key == property or key.startswith(property + ".") or property.startswith(key + ".")
                    for key in original["fieldStatus"]
                )
                or any(
                    not failed or failed[: len(path)] == path or path[: len(failed)] == failed
                    for failed in issues.get(token, ())
                )
            ):
                continue
            result[fact] = NativeDescriptionField(
                ref, token, (snapshot.native.session, snapshot.native.id, scope), property, field, path, value
            )
    return MappingProxyType(result)
