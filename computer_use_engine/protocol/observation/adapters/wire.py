"""Read the existing driver transport without constructing native object wrappers."""

from __future__ import annotations

import base64
import math
from typing import Any, Mapping
from urllib.parse import urljoin, urlsplit, urlunsplit

_SOURCES = {"macos": "macos.ax", "windows": "windows.uia", "linux": "linux.atspi"}
_STATES = {"value", "absent", "unsupported", "not_read", "error", "timeout", "unavailable", "redacted"}
_KINDS = {"element", "text_range", "text_marker", "text_marker_range", "native_value", "unknown"}


def record(value: Any, required: set[str], optional: set[str] = frozenset()) -> dict[str, Any]:
    """Require one transport record with its declared fields."""
    if type(value) is not dict or not required <= value.keys() or value.keys() - required - optional:
        raise ValueError("driver observation: unexpected or missing record fields")
    return value


def text(value: Any) -> str:
    """Require a nonempty transport identifier."""
    if not isinstance(value, str) or not value:
        raise ValueError("driver observation: expected a nonempty string")
    return value


def array(value: Any) -> list[Any]:
    """Require a JSON array rather than an arbitrary iterable."""
    if type(value) is not list:
        raise ValueError("driver observation: expected an array")
    return value


class DriverWire:
    """Validate a bounded response and decode transient query evidence.

    The original JSON remains owned by this call. Decoded values are ordinary
    Python values used by the query boundary and the platform normalizer.
    """

    def __init__(self, value: Any, *, semantic_values: bool = False) -> None:
        self.raw = record(value, {"kind", "session", "platform", "roots", "nodes", "read"})
        if (
            self.raw["kind"] != "observation"
            or not isinstance(self.raw["platform"], str)
            or self.raw["platform"] not in _SOURCES
        ):
            raise ValueError("driver observation: unsupported transport format")
        self.platform = self.raw["platform"]
        self.semantic_values = semantic_values
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
        self.read = self.read_info(self.raw["read"])
        self.details = self.read["details"]
        self.interval = self.read.get("intervalNs")
        if self.interval is None or not isinstance(self.details.get("observation_id"), str):
            raise ValueError("driver observation: capture identity and interval are required")
        text(self.details["observation_id"])
        text(self.details.get("surface"))

    def pairs(self, values: Any, depth: int = 0) -> list[tuple[str, Any]]:
        """Decode ordered metadata fields while rejecting malformed pairs."""
        result = []
        for pair in array(values):
            if type(pair) is not list or len(pair) != 2:
                raise ValueError("driver observation: invalid metadata pair")
            result.append((text(pair[0]), self.value(pair[1], depth + 1)))
        return result

    def value(self, value: Any, depth: int = 0) -> Any:
        """Decode one wire value to request-local query evidence."""
        self.remaining -= 1
        if self.remaining < 0 or depth > 96:
            raise ValueError("driver observation: value budget exceeded")
        v = record(value, {"nativeType", "data"}, {"metadata", "raw"})
        native_type = text(v["nativeType"])
        metadata = dict(self.pairs(v.get("metadata", []), depth + 1))
        raw_bytes = self.bytes(v["raw"]) if "raw" in v else None
        data = v["data"]
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
        elif type(data) is float and not math.isfinite(data):
            raise ValueError("driver observation: special float needs a tag")
        if "toolkit_value" in metadata:
            return metadata["toolkit_value"]
        if "enum_name" in metadata:
            return metadata["enum_name"]
        if native_type == "CFDate" and type(data) in (int, float):
            return {"nativeType": "CFDate", "absoluteTime": data}
        if (
            self.semantic_values
            and native_type == "CFURL"
            and isinstance(data, str)
            and isinstance(metadata.get("base"), str)
        ):
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
        if type(data) is float and not math.isfinite(data):
            return {
                "nativeType": native_type,
                "value": "NaN" if math.isnan(data) else "+Infinity" if data > 0 else "-Infinity",
            }
        return data

    def item(self, value: Any, depth: int) -> Any:
        """Keep an unsuccessful collection slot at its original index."""
        if type(value) is dict and set(value) == {"issue"}:
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

    def issue(self, value: Any, depth: int = 0) -> dict[str, Any]:
        """Decode acquisition status while retaining its existing query meaning."""
        v = record(value, {"reason"}, {"path", "detail", "nativeStatus", "continuation"})
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
                result[key] = self.value(v[key], depth + 1)
        return result

    def read_info(self, value: Any) -> dict[str, Any]:
        """Decode read scope and coverage separately from element properties."""
        if value is None:
            return {"details": {}, "issues": []}
        v = record(value, set(), {"api", "producer", "cached", "intervalNs", "complete", "issues", "details"})
        for key in ("api", "producer"):
            if v.get(key) is not None:
                text(v[key])
        for key in ("cached", "complete"):
            if v.get(key) is not None and type(v[key]) is not bool:
                raise ValueError("driver observation: invalid read state")
        if v.get("intervalNs") is not None:
            interval = array(v["intervalNs"])
            if len(interval) != 2 or any(type(x) is not int or x < 0 for x in interval) or interval[1] < interval[0]:
                raise ValueError("driver observation: invalid capture interval")
        return {
            **v,
            "details": dict(self.pairs(v.get("details", []))),
            "issues": [self.issue(item) for item in array(v.get("issues", []))],
        }

    def property(self, raw: Any) -> tuple[tuple[str, str | int], Any, str, list[Any], dict[str, Any]]:
        """Validate a field result and decode its query-local components."""
        prop = record(raw, {"key", "value"}, {"state", "arguments", "read"})
        key = array(prop["key"])
        if len(key) != 2 or type(key[1]) not in (str, int):
            raise ValueError("driver observation: invalid property key")
        state = prop.get("state", "value")
        if state not in _STATES or (state == "value") != (prop["value"] is not None):
            raise ValueError("driver observation: invalid property state or payload")
        return (
            (text(key[0]), key[1]),
            self.value(prop["value"]) if prop["value"] is not None else None,
            state,
            [self.value(x) for x in array(prop.get("arguments", []))],
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
        if isinstance(issue.get("nativeStatus"), dict):
            result.update(issue["nativeStatus"])
        if issue.get("detail") is not None:
            result.setdefault("detail", issue["detail"])
        if isinstance(issue.get("continuation"), dict):
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
    if (
        type(offset) is not int
        or offset < 0
        or total is not None
        and (type(total) is not int or total < 0)
        or certainty not in ("confirmed", "inferred", "unknown", "conflicting")
        or state not in ("complete", "partial", "absent", "unsupported", "error", "timeout")
    ):
        raise ValueError("driver observation: invalid relation range or evidence")
    end = offset + len(items)
    if total is not None and end > total:
        raise ValueError("driver observation: relation count exceeds its range")
    if (offset or "cursor" in details) and total is None:
        raise ValueError("driver observation: relation continuation needs a total")
    if state == "complete" and (offset or "cursor" in details or total is not None and end != total):
        raise ValueError("driver observation: complete relation has an unread remainder")
    if state == "absent" and (end or total or "cursor" in details):
        raise ValueError("driver observation: absent relation contains targets")
    if "cursor" in details and (
        state != "partial" or not isinstance(details["cursor"], str) or not details["cursor"] or end >= total
    ):
        raise ValueError("driver observation: invalid relation continuation")
    return {
        "source": node,
        "property": key,
        "targets": [item["node"] for item in items],
        "certainty": certainty,
        "state": state,
        **{name: details[name] for name in ("offset", "total", "cursor", "detail") if name in details},
    }


def query_facts(wire: DriverWire) -> dict[str, Any]:
    """Build transient field evidence for native queries and the entry adapter."""
    rows, relations, unread = [], [], []
    for identity, raw in wire.nodes.items():
        if raw.get("identity") is not None:
            wire.value(raw["identity"])
        wire.read_info(raw.get("read"))
        if raw["kind"] != "element":
            for prop in array(raw["properties"]) if raw["properties"] is not None else ():
                wire.property(prop)
            continue
        if raw["properties"] is None:
            unread.append(identity)
            continue
        values, statuses, requested = {}, {}, []
        actions = {"state": "unavailable", "items": [], "detail": "Action catalogue was not read"}
        for index, raw_property in enumerate(array(raw["properties"])):
            key, value, state, arguments, read = wire.property(raw_property)
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
                    or type(total) is not int
                    or total < len(value)
                    or actions["state"] == "complete"
                    and total != len(value)
                ):
                    raise ValueError("driver observation: invalid action coverage")
                for action in value:
                    if not isinstance(action, dict) or not isinstance(action.get("name"), str) or not action["name"]:
                        raise ValueError("driver observation: malformed action declaration")
                continue
            name = key[1]
            if key[0] != _SOURCES[wire.platform] or not isinstance(name, str) or arguments or name in requested:
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
    if not isinstance(requested_relations, list) or any(not isinstance(x, str) for x in requested_relations):
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
        "roots": list(wire.raw["roots"]),
        "nodes": rows,
        "relations": relations,
        "requestedRelations": requested_relations,
        "unread": unread,
        "stopReasons": [issue.get("detail") or issue["reason"] for issue in wire.read["issues"]],
        **({"changeToken": wire.details["change_token"]} if "change_token" in wire.details else {}),
    }


def decode_query_facts(value: Any) -> dict[str, Any]:
    """Decode request-local native query facts without publishing a second graph."""
    return query_facts(DriverWire(value))


def validate_driver_payload(value: Any) -> None:
    """Validate acquisition data before the transport accepts its native evidence."""
    decode_query_facts(value)
