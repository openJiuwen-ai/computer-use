# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Read the existing driver transport without constructing native object wrappers."""

from __future__ import annotations

import base64
import math
from types import MappingProxyType
from typing import Any, Mapping, NamedTuple
from urllib.parse import urljoin, urlsplit, urlunsplit

_SOURCES = {"macos": "macos.ax", "windows": "windows.uia", "linux": "linux.atspi"}
_STATES = {"value", "absent", "unsupported", "not_read", "error", "timeout", "unavailable", "redacted"}
_KINDS = {"element", "text_range", "text_marker", "text_marker_range", "native_value", "unknown"}
_VALUE_REQUIRED = frozenset(("nativeType", "data"))
_VALUE_OPTIONAL = frozenset(("metadata", "raw"))
_READ_FIELDS = frozenset(("api", "producer", "cached", "intervalNs", "complete", "issues", "details"))
_ISSUE_REQUIRED = frozenset(("reason",))
_ISSUE_OPTIONAL = frozenset(("path", "detail", "nativeStatus", "continuation"))
_PROPERTY_REQUIRED = frozenset(("key", "value"))
_PROPERTY_OPTIONAL = frozenset(("state", "arguments", "read"))
_METADATA_REFERENCE = frozenset(("metadataValue",))
_EMPTY_FIELDS = frozenset()


def record(
    value: Any, required: set[str] | frozenset[str], optional: set[str] | frozenset[str] = _EMPTY_FIELDS
) -> dict[str, Any]:
    """Require one transport record with its declared fields."""
    # Exact types preserve scalar validation and identity contracts.
    # pylint: disable-next=unidiomatic-typecheck
    if type(value) is not dict or not required.issubset(value):
        raise ValueError("driver observation: unexpected or missing record fields")
    # Most native values have exactly their required fields. Avoid allocating
    # two transient sets for every scalar in a large fact batch.
    if len(value) != len(required) and any(key not in required and key not in optional for key in value):
        raise ValueError("driver observation: unexpected or missing record fields")
    return value


def text(value: Any) -> str:
    """Require a nonempty transport identifier."""
    if not isinstance(value, str) or not value:
        raise ValueError("driver observation: expected a nonempty string")
    return value


def array(value: Any) -> list[Any]:
    """Require a JSON array rather than an arbitrary iterable."""
    # pylint: disable-next=unidiomatic-typecheck
    if type(value) is not list:
        raise ValueError("driver observation: expected an array")
    return value


def _freeze_metadata(value: Any) -> Any:
    """Detach one validated pool value into request-local immutable containers."""
    # pylint: disable-next=unidiomatic-typecheck
    if type(value) is dict:
        return MappingProxyType({key: _freeze_metadata(item) for key, item in value.items()})
    # pylint: disable-next=unidiomatic-typecheck
    if type(value) is list:
        return tuple(_freeze_metadata(item) for item in value)
    return value


class DecodedProperty(NamedTuple):
    """Decoded field identity, payload, status, arguments, and read evidence."""

    key: tuple[str, str | int]
    value: Any
    state: str
    arguments: list[Any]
    read: dict[str, Any]


class DriverWire:
    """Validate a bounded response and decode transient query evidence.

    The original JSON remains owned by this call. Decoded values are ordinary
    Python values used by the query boundary and the platform normalizer. Shared
    ReadMetadata pool entries are detached, immutable request-local values.
    """

    def __init__(self, value: Any, *, semantic_values: bool = False) -> None:
        self.raw = record(value, {"kind", "session", "platform", "roots", "nodes", "read"}, {"metadataValues"})
        if (
            self.raw["kind"] != "observation"
            or not isinstance(self.raw["platform"], str)
            or self.raw["platform"] not in _SOURCES
        ):
            raise ValueError("driver observation: unsupported transport format")
        self.platform = self.raw["platform"]
        self.semantic_values = semantic_values
        self.has_semantic_urls = False
        self.session = text(self.raw["session"])
        self.nodes: dict[str, dict[str, Any]] = {}
        self.remaining = 2_000_000
        rows = array(self.raw["nodes"])
        if len(rows) > 65536:
            raise ValueError("driver observation: object budget exceeded")
        for raw in rows:
            row = record(raw, {"id", "source", "kind", "nativeType", "properties"}, {"identity", "read"})
            identity = text(row["id"])
            if identity in self.nodes or row["kind"] not in _KINDS:
                raise ValueError("driver observation: duplicate identity or invalid object kind")
            text(row["source"])
            text(row["nativeType"])
            self.nodes[identity] = row
        roots = [text(item) for item in array(self.raw["roots"])]
        if len(roots) != len(set(roots)) or any(x not in self.nodes for x in roots):
            raise ValueError("driver observation: missing or duplicate root")
        if any(self.nodes[x]["kind"] != "element" for x in roots):
            raise ValueError("driver observation: an observation root must be an element")
        # Entries contain full NativeValue records, never metadata references.
        # Decode each once after all native node references have been declared;
        # shared read slots then perform only the exact index/budget check.
        self._metadata_depth = -1
        metadata_values, metadata_depths = [], []
        for item in array(self.raw["metadataValues"]) if "metadataValues" in self.raw else ():
            self._metadata_depth = 0
            metadata_values.append(_freeze_metadata(self.value(item)))
            metadata_depths.append(self._metadata_depth)
        self._metadata_depth = -1
        self.metadata_values = tuple(metadata_values)
        self.metadata_depths = tuple(metadata_depths)
        self._attribute_catalogues: set[int] = set()
        self.read = self.read_info(self.raw["read"])
        self.details = self.read["details"]
        self.interval = self.read.get("intervalNs")
        if self.interval is None or not isinstance(self.details.get("observation_id"), str):
            raise ValueError("driver observation: capture identity and interval are required")
        text(self.details["observation_id"])
        text(self.details.get("surface"))

    def pairs(self, values: Any, depth: int = 0, *, metadata_values: bool = False) -> list[tuple[str, Any]]:
        """Decode ordered metadata fields while rejecting malformed pairs."""
        result = []
        decode = self.metadata_value if metadata_values else self.value
        for pair in array(values):
            # pylint: disable-next=unidiomatic-typecheck
            if type(pair) is not list or len(pair) != 2:
                raise ValueError("driver observation: invalid metadata pair")
            result.append((text(pair[0]), decode(pair[1], depth + 1)))
        return result

    def metadata_value(self, value: Any, depth: int = 0) -> Any:
        """Resolve a pool index in a declared sharing slot."""
        # A metadata index is recognized only in a built-in wire record.
        # pylint: disable-next=unidiomatic-typecheck
        if type(value) is dict and "metadataValue" in value:
            self.remaining -= 1
            if self.remaining < 0 or depth > 96:
                raise ValueError("driver observation: value budget exceeded")
            index = record(value, _METADATA_REFERENCE)["metadataValue"]
            if type(index) is not int or not 0 <= index < len(self.metadata_values):
                raise ValueError("driver observation: invalid metadata value reference")
            if depth + self.metadata_depths[index] > 96:
                raise ValueError("driver observation: value budget exceeded")
            return self.metadata_values[index]
        return self.value(value, depth)

    def _attribute_catalogue(self, marker: dict[str, Any]) -> list[str]:
        """Keep the per-property list while sharing one verified catalogue decode."""
        value = self.metadata_value(marker)
        index = marker["metadataValue"]
        if index not in self._attribute_catalogues:
            native = record(self.raw["metadataValues"][index], _VALUE_REQUIRED)
            if native["nativeType"] != "CFArray":
                raise ValueError("driver observation: invalid shared attribute catalogue")
            for item in array(record(native["data"], {"items"})["items"]):
                item = record(item, _VALUE_REQUIRED)
                name = item["data"]
                if item["nativeType"] != "CFString" or not isinstance(name, str) or not name.isascii():
                    raise ValueError("driver observation: invalid shared attribute name")
            self._attribute_catalogues.add(index)
        return list(value)

    def value(self, value: Any, depth: int = 0) -> Any:
        """Decode one wire value to request-local query evidence."""
        self.remaining -= 1
        if self.remaining < 0 or depth > 96:
            raise ValueError("driver observation: value budget exceeded")
        if self._metadata_depth >= 0 and depth > self._metadata_depth:
            self._metadata_depth = depth
        v = record(value, _VALUE_REQUIRED, _VALUE_OPTIONAL)
        native_type = text(v["nativeType"])
        data = v["data"]
        scalar = type(data)
        if "metadata" not in v and "raw" not in v and native_type != "CFDate":
            if scalar in (str, bool, int, type(None)):
                return data
            if scalar is float:
                if not math.isfinite(data):
                    raise ValueError("driver observation: special float needs a tag")
                return data
        metadata = dict(self.pairs(v["metadata"], depth + 1)) if "metadata" in v else {}
        raw_bytes = self.bytes(v["raw"]) if "raw" in v else None
        # pylint: disable-next=unidiomatic-typecheck
        if type(data) is dict:
            if len(data) != 1:
                raise ValueError("driver observation: ambiguous value tag")
            tag, payload = next(iter(data.items()))
            if tag == "ref":
                identity = text(payload)
                if identity not in self.nodes:
                    raise ValueError("driver observation: undeclared reference")
                data = {"nativeType": self.nodes[identity]["nativeType"], "node": identity}
            elif tag == "items":
                data = [self.item(x, depth + 1) for x in array(payload)]
            elif tag in ("fields", "entries"):
                pairs = []
                for pair in array(payload):
                    # pylint: disable-next=unidiomatic-typecheck
                    if type(pair) is not list or len(pair) != 2:
                        raise ValueError("driver observation: invalid record pair")
                    key, item = pair
                    if tag == "entries":
                        key = self.value(key, depth + 1)
                    elif key is not None and type(key) not in (str, int):
                        raise ValueError("driver observation: invalid record key")
                    pairs.append((key, self.item(item, depth + 1)))
                if all(isinstance(key, str) for key, _ in pairs) and len({key for key, _ in pairs}) == len(pairs):
                    data = dict(pairs)
                else:
                    data = {"nativeType": native_type, tag: [[key, item] for key, item in pairs]}
            elif tag == "bytes":
                data = self.bytes(payload)
            elif tag == "integer":
                if not isinstance(payload, str) or not payload or not payload.lstrip("-").isdigit():
                    raise ValueError("driver observation: invalid integer")
                data = int(payload)
            elif tag == "float" and payload in ("nan", "inf", "-inf", "-0"):
                data = -0.0 if payload == "-0" else float(payload)
            else:
                raise ValueError("driver observation: unknown value tag")
        elif data is not None and type(data) not in (str, bool, int, float):
            raise ValueError("driver observation: invalid scalar")
        # pylint: disable-next=unidiomatic-typecheck
        elif type(data) is float and not math.isfinite(data):
            raise ValueError("driver observation: special float needs a tag")
        if "toolkit_value" in metadata:
            return metadata["toolkit_value"]
        if "enum_name" in metadata:
            return metadata["enum_name"]
        if native_type == "CFDate" and type(data) in (int, float):
            return {"nativeType": "CFDate", "absoluteTime": data}
        if native_type == "CFURL" and isinstance(data, str) and isinstance(metadata.get("base"), str):
            self.has_semantic_urls = True
            if not self.semantic_values:
                return data
            base = urlsplit(metadata["base"])
            # urljoin's scheme allowlist excludes custom application URL schemes.
            # Resolve their path using the same hierarchical rules, then restore it.
            if base.scheme and not urlsplit(data).scheme:
                joined = urlsplit(urljoin(urlunsplit(base._replace(scheme="http")), data))
                return urlunsplit(joined._replace(scheme=base.scheme))
            return urljoin(metadata["base"], data)
        if isinstance(data, bytes):
            if metadata.get("string_encoding") in ("utf-16le", "utf-16-le"):
                return data.decode("utf-16le", "surrogatepass")
            return {"nativeType": native_type, "base64": base64.b64encode(data).decode("ascii")}
        if raw_bytes is not None and metadata.get("raw_encoding") in ("utf-16le", "utf-16-le"):
            return raw_bytes.decode("utf-16le", "surrogatepass")
        # pylint: disable-next=unidiomatic-typecheck
        if type(data) is float and not math.isfinite(data):
            return {
                "nativeType": native_type,
                "value": "NaN" if math.isnan(data) else "+Infinity" if data > 0 else "-Infinity",
            }
        return data

    def item(self, value: Any, depth: int) -> Any:
        """Keep an unsuccessful collection slot at its original index."""
        # pylint: disable-next=unidiomatic-typecheck
        if type(value) is dict and len(value) == 1 and "issue" in value:
            return {"issue": self.issue(value["issue"], depth + 1)}
        return self.value(value, depth + 1)

    @staticmethod
    def bytes(value: Any) -> bytes:
        """Validate base64 transport data before any semantic interpretation."""
        if not isinstance(value, str):
            raise ValueError("driver observation: expected base64 text")
        try:
            return base64.b64decode(value, validate=True)
        except ValueError as error:
            raise ValueError("driver observation: invalid base64 data") from error

    def issue(self, value: Any, depth: int = 0, *, metadata_values: bool = False) -> dict[str, Any]:
        """Decode acquisition status while retaining its existing query meaning."""
        v = record(value, _ISSUE_REQUIRED, _ISSUE_OPTIONAL)
        result = {"reason": text(v["reason"])}
        if "path" in v:
            path = array(v["path"])
            if any(type(x) not in (str, int) for x in path):
                raise ValueError("driver observation: invalid issue path")
            result["path"] = list(path)
        if v.get("detail") is not None:
            if not isinstance(v["detail"], str):
                raise ValueError("driver observation: invalid issue detail")
            result["detail"] = v["detail"]
        for key in ("nativeStatus", "continuation"):
            if v.get(key) is not None:
                decode = self.metadata_value if metadata_values else self.value
                result[key] = decode(v[key], depth + 1)
        return result

    def read_info(self, value: Any) -> dict[str, Any]:
        """Decode read scope and coverage separately from element properties."""
        if value is None:
            return {"details": {}, "issues": []}
        v = record(value, _EMPTY_FIELDS, _READ_FIELDS)
        for key in ("api", "producer"):
            if v.get(key) is not None:
                text(v[key])
        for key in ("cached", "complete"):
            # pylint: disable-next=unidiomatic-typecheck
            if v.get(key) is not None and type(v[key]) is not bool:
                raise ValueError("driver observation: invalid read state")
        if v.get("intervalNs") is not None:
            interval = array(v["intervalNs"])
            # pylint: disable-next=unidiomatic-typecheck
            if len(interval) != 2 or any(type(x) is not int or x < 0 for x in interval) or interval[1] < interval[0]:
                raise ValueError("driver observation: invalid capture interval")
        return {
            **v,
            "details": dict(self.pairs(v["details"], metadata_values=True)) if "details" in v else {},
            "issues": [self.issue(item, metadata_values=True) for item in array(v["issues"])] if "issues" in v else [],
        }

    def property(self, raw: Any) -> DecodedProperty:
        """Validate a field result and decode its query-local components."""
        prop = record(raw, _PROPERTY_REQUIRED, _PROPERTY_OPTIONAL)
        key = array(prop["key"])
        if len(key) != 2 or type(key[1]) not in (str, int):
            raise ValueError("driver observation: invalid property key")
        state = prop.get("state", "value")
        value = prop["value"]
        if state not in _STATES or (state == "value") != (value is not None):
            raise ValueError("driver observation: invalid property state or payload")
        if value is not None:
            attribute_catalogue = self.platform == "macos" and len(prop) == 2 and key == ["toolkit", "attribute_names"]
            if attribute_catalogue and isinstance(value, dict) and "metadataValue" in value:
                value = self._attribute_catalogue(value)
            else:
                value = self.value(value)
        return DecodedProperty(
            (text(key[0]), key[1]),
            value,
            state,
            [self.value(x) for x in array(prop["arguments"])] if "arguments" in prop else [],
            self.read_info(prop.get("read")),
        )


def field_status(state: str, read: Mapping[str, Any]) -> dict[str, Any] | None:
    """Keep the driver query status used by native input evidence."""
    if state != "value":
        result = {"state": state}
    elif read.get("complete") is False:
        result = {"state": "truncated"}
    else:
        return None
    for issue in read.get("issues", ()):
        if isinstance(issue.get("nativeStatus"), Mapping):
            result.update(issue["nativeStatus"])
        if issue.get("detail") is not None:
            result.setdefault("detail", issue["detail"])
        if isinstance(issue.get("continuation"), Mapping):
            result.update(issue["continuation"])
    return result


def relation_record(
    wire: DriverWire, node: str, key: str | int, value: Any, state: str, read: Mapping[str, Any]
) -> dict[str, Any]:
    """Validate relationship cardinality and page range before accepting evidence."""
    details = read["details"]
    items = [] if value is None else value if isinstance(value, list) else [value]
    if any(
        not isinstance(item, dict)
        or set(item) != {"nativeType", "node"}
        or wire.nodes[item["node"]]["kind"] != "element"
        for item in items
    ):
        raise ValueError("driver observation: invalid relation reference")
    offset, total = details.get("offset", 0), details.get("total")
    state = details.get("state", state)
    certainty = details.get("certainty")
    invalid_offset = (
        True
        if (
            type(offset) is not int  # pylint: disable=unidiomatic-typecheck
            or offset < 0
            or total is not None
            # pylint: disable-next=unidiomatic-typecheck
            and (type(total) is not int or total < 0)
            or certainty not in ("confirmed", "inferred", "unknown", "conflicting")
            or state not in ("complete", "partial", "absent", "unsupported", "error", "timeout")
        )
        else False
    )
    if invalid_offset:
        raise ValueError("driver observation: invalid relation range or evidence")
    inspected, slots = len(items), {}
    if "inspectedCount" in details or "unavailablePositions" in details:
        inspected, gaps = details.get("inspectedCount"), details.get("unavailablePositions")
        detail = details.get("detail")
        slot_error = "driver observation: invalid relation slot progress"
        if type(inspected) is not int or not isinstance(gaps, (list, tuple)) or not gaps:
            raise ValueError(slot_error)
        if (
            inspected != len(items) + len(gaps)
            or any(type(position) is not int or not offset <= position < offset + inspected for position in gaps)
            or list(gaps) != sorted(set(gaps))
        ):
            raise ValueError(slot_error)
        if "offset" not in details or total is None or state != "partial":
            raise ValueError(slot_error)
        if not isinstance(detail, str) or not detail:
            raise ValueError(slot_error)
        slots = {"inspectedCount": inspected, "unavailablePositions": list(gaps)}
    end = offset + inspected
    if total is not None and end > total:
        raise ValueError("driver observation: relation count exceeds its range")
    if (offset or "cursor" in details) and total is None:
        raise ValueError("driver observation: relation continuation needs a total")
    invalid_state = (
        True if state == "complete" and (offset or "cursor" in details or total is not None and end != total) else False
    )
    if invalid_state:
        raise ValueError("driver observation: complete relation has an unread remainder")
    invalid_state_condition = True if state == "absent" and (end or total or "cursor" in details) else False
    if invalid_state_condition:
        raise ValueError("driver observation: absent relation contains targets")
    invalid_details = (
        True
        if "cursor" in details
        and (state != "partial" or not isinstance(details["cursor"], str) or not details["cursor"] or end >= total)
        else False
    )
    if invalid_details:
        raise ValueError("driver observation: invalid relation continuation")
    membership = details.get("membershipObservedAt")
    if membership is not None:
        interval_error = "driver observation: captured relation membership interval is invalid"
        if (
            not isinstance(membership, Mapping)
            or set(membership) != {"id", "startedAt", "completedAt"}
            or not isinstance(membership["id"], str)
        ):
            raise ValueError(interval_error)
        if not membership["id"] or any(
            type(membership[name]) is not int or not 0 <= membership[name] <= 2**53 - 1
            for name in ("startedAt", "completedAt")
        ):
            raise ValueError(interval_error)
        if (
            membership["completedAt"] < membership["startedAt"]
            or membership["completedAt"] > wire.interval[1] // 1_000_000
        ):
            raise ValueError(interval_error)
        if state != "partial" or total is None or not details.get("detail"):
            raise ValueError(
                "driver observation: captured relation membership needs partial coverage, count and explanation"
            )
    return {
        "source": node,
        "property": key,
        "targets": [item["node"] for item in items],
        "certainty": certainty,
        "state": state,
        **{
            name: details[name]
            for name in ("offset", "total", "cursor", "detail", "membershipObservedAt")
            if name in details
        },
        **slots,
    }


def query_facts(wire: DriverWire) -> dict[str, Any]:
    """Build transient field evidence for native queries and the entry adapter."""
    rows, relations, unread = [], [], []
    roots = wire.raw["roots"]
    focus_root = roots[0] if wire.platform == "macos" and len(roots) == 1 else None
    focus_seeds = []
    outside = [] if wire.platform == "windows" else None
    for identity, raw in wire.nodes.items():
        if raw.get("identity") is not None:
            wire.value(raw["identity"])
        node_read = wire.read_info(raw.get("read"))
        if raw["kind"] != "element":
            for prop in array(raw["properties"]) if raw["properties"] is not None else ():
                wire.property(prop)
            continue
        if raw["properties"] is None:
            unread.append(identity)
            if outside is not None and any(
                issue["reason"] == "outside_window_scope" for issue in node_read["issues"]
            ):
                outside.append(identity)
            continue
        values, statuses, requested = {}, {}, []
        actions = {"state": "unavailable", "items": [], "detail": "Action catalogue was not read"}
        for index, raw_property in enumerate(array(raw["properties"])):
            key, value, state, arguments, read = wire.property(raw_property)
            if identity == focus_root and key == ("toolkit", "focused_read_seed"):
                details = read["details"]
                focus_seeds.append(
                    value["node"]
                    if state == "value" and not arguments and isinstance(value, Mapping)
                    and value.get("nativeType") == "AXUIElement" and read.get("complete") is True
                    and not read["issues"] and details.get("source") == "application"
                    and details.get("attribute") == "AXFocusedUIElement" and details.get("state") == "confirmed"
                    else None
                )
            if read["details"].get("relation") is True:
                relations.append(relation_record(wire, identity, key[1], value, state, read))
                continue
            if key == ("toolkit", "actions"):
                if value is None:
                    value = []
                if not isinstance(value, list):
                    raise ValueError("driver observation: invalid action catalogue")
                detail = read["details"]
                actions = {
                    "state": detail.get("state", "complete" if read.get("complete") else "unavailable"),
                    "items": value,
                    **{key: detail[key] for key in ("total", "detail") if key in detail},
                }
                total = actions.get("total", len(value))
                invalid_total = (
                    True
                    if (
                        actions["state"]
                        not in (
                            "complete",
                            "partial",
                            "unavailable",
                            "unsupported",
                            "absent",
                            "error",
                            "timeout",
                            "not_read",
                        )
                        # pylint: disable-next=unidiomatic-typecheck
                        or type(total) is not int
                        or total < len(value)
                        or actions["state"] == "complete"
                        and total != len(value)
                    )
                    else False
                )
                if invalid_total:
                    raise ValueError("driver observation: invalid action coverage")
                for action in value:
                    if not isinstance(action, dict) or not isinstance(action.get("name"), str) or not action["name"]:
                        raise ValueError("driver observation: malformed action declaration")
                continue
            name = key[1]
            matches_arguments = (
                True
                if key[0] != _SOURCES[wire.platform] or not isinstance(name, str) or arguments or name in requested
                else False
            )
            if matches_arguments:
                name = f"{key[0]}::{name}#{index}"
            requested.append(name)
            if raw_property["value"] is not None:
                values[name] = value
            status = field_status(state, read)
            if status is not None:
                statuses[name] = status
        rows.append(
            {
                "node": identity,
                "requestedFields": requested,
                "values": values,
                "fieldStatus": statuses,
                "actions": actions,
            }
        )
    requested_relations = wire.details.get("requested_relations", [])
    if not isinstance(requested_relations, (list, tuple)) or any(not isinstance(x, str) for x in requested_relations):
        raise ValueError("driver observation: invalid requested relationships")
    seen = set()
    for relation in relations:
        key = relation["source"], relation["property"]
        if key in seen:
            raise ValueError("driver observation: duplicate query relationship")
        seen.add(key)
    if any((row["node"], name) not in seen for row in rows for name in requested_relations):
        raise ValueError("driver observation: requested relationship has no result")
    return {
        "kind": "nodes",
        "session": wire.session,
        "platform": wire.platform,
        "surface": wire.details["surface"],
        "observed": {
            "id": wire.details["observation_id"],
            "startedAt": wire.interval[0] // 1_000_000,
            "completedAt": wire.interval[1] // 1_000_000,
        },
        "roots": list(roots),
        "nodes": rows,
        "relations": relations,
        "requestedRelations": list(requested_relations),
        "unread": unread,
        "stopReasons": [issue.get("detail") or issue["reason"] for issue in wire.read["issues"]],
        **({"focusedElement": focus_seeds[0]} if len(focus_seeds) == 1 and focus_seeds[0] is not None else {}),
        **({"outsideWindowNodes": outside} if outside else {}),
        **({"changeToken": wire.details["change_token"]} if "change_token" in wire.details else {}),
    }


class DecodedDriverObservation(Mapping[str, Any]):
    """One validated response owned by its request, with reusable parsed evidence.

    Diagnostics retain the original wire record. Scope checks consume query
    facts, while the immutable semantic reply is built only when requested.
    """

    def __init__(self, value: Any) -> None:
        self.wire = DriverWire(value)
        self.facts = query_facts(self.wire)
        self.reply = None

    def __getitem__(self, key: str) -> Any:
        return self.wire.raw[key]

    def __iter__(self):
        return iter(self.wire.raw)

    def __len__(self) -> int:
        return len(self.wire.raw)


def decode_query_facts(value: Any) -> dict[str, Any]:
    """Decode request-local native query facts without publishing a second graph."""
    return value.facts if isinstance(value, DecodedDriverObservation) else query_facts(DriverWire(value))


def validate_driver_payload(value: Any) -> None:
    """Validate acquisition data before the transport accepts its native evidence."""
    decode_query_facts(value)
