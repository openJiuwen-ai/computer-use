"""Validate the existing UIA text-anchor and endpoint evidence at normalization."""

from __future__ import annotations

import hashlib
from typing import Any, Mapping

Record = Mapping[str, Any]


def safe_integer(value: Any) -> bool:
    """Accept exactly represented transport integers."""
    # Exact types preserve scalar validation and identity contracts.
    # pylint: disable-next=unidiomatic-typecheck
    return type(value) is int and abs(value) <= 9007199254740991


def _position(value: Any, units: int) -> bool:
    """Require ordered UTF-16 endpoints within the declared text anchor."""
    return (
        isinstance(value, dict)
        and set(value) == {"start", "end", "degenerate"}
        and safe_integer(value["start"])
        and safe_integer(value["end"])
        and 0 <= value["start"] <= value["end"] <= units
        # pylint: disable-next=unidiomatic-typecheck
        and type(value["degenerate"]) is bool
        and (not value["degenerate"] or value["start"] == value["end"])
    )


def _anchor(value: Any) -> bool:
    """Require the immutable prefix length and hash used by the driver."""
    return (
        isinstance(value, dict)
        and set(value) == {"source", "unit", "units", "complete", "sha256"}
        and value["source"] == "Text.DocumentRange"
        and value["unit"] == "utf16"
        and safe_integer(value["units"])
        and 0 <= value["units"] <= 65536
        # pylint: disable-next=unidiomatic-typecheck
        and type(value["complete"]) is bool
        and isinstance(value["sha256"], str)
        and len(value["sha256"]) == 64
        and all(c in "0123456789abcdef" for c in value["sha256"])
    )


def _statuses(value: Any) -> bool:
    return isinstance(value, dict) and all(
        isinstance(key, str)
        and isinstance(item, dict)
        and item.get("state") in ("absent", "unsupported", "error", "timeout", "truncated")
        and set(item) <= {"state", "detail"}
        and ("detail" not in item or isinstance(item["detail"], str))
        for key, item in value.items()
    )


def object_ranges(value: Any) -> bool:
    """Validate embedded-object membership and endpoint coverage independently."""
    if (
        not isinstance(value, dict)
        or set(value)
        - {"source", "anchor", "objects", "total", "state", "order", "detail", "anchorStatus", "orderStatus"}
        or value.get("source") != "IUIAutomation.TextPattern.RangeFromChild"
    ):
        return False
    if (
        not _anchor(value.get("anchor"))
        or value.get("state") not in ("complete", "partial")
        or value.get("order") not in ("nativeEndpoints", "unconfirmed", "overlapping")
    ):
        return False
    if not safe_integer(value.get("total")) or value["total"] < 0 or not isinstance(value.get("objects"), list):
        return False
    if len(value["objects"]) > min(32, value["total"]):
        return False
    if "detail" in value and not isinstance(value["detail"], str):
        return False
    if "anchorStatus" in value and not _statuses({"anchor": value["anchorStatus"]}):
        return False
    if "orderStatus" in value and not _statuses({"order": value["orderStatus"]}):
        return False
    if value["state"] == "complete" and (len(value["objects"]) != value["total"] or "anchorStatus" in value):
        return False
    for item in value["objects"]:
        if not isinstance(item, dict) or set(item) - {"node", "values", "fieldStatus"}:
            return False
        if "node" in item and (not isinstance(item["node"], str) or not item["node"]):
            return False
        if not _statuses(item.get("fieldStatus")) or not isinstance(item.get("values"), dict):
            return False
        if item["values"] and not _position(item["values"], value["anchor"]["units"]):
            return False
        if not item["values"] and not item["fieldStatus"]:
            return False
        if value["state"] == "complete" and ("node" not in item or item["fieldStatus"]):
            return False
    return True


def caret_range(value: Any) -> bool:
    """Validate a separately observed UIA caret and its document anchor."""
    if not isinstance(value, dict):
        return False
    if value.get("state") == "notProbed":
        return set(value) == {"state", "detail"} and isinstance(value["detail"], str)
    if (
        set(value) != {"source", "values", "fieldStatus", "state"}
        or value["source"] != "IUIAutomationTextPattern2.GetCaretRange"
        or value["state"] not in ("complete", "partial")
    ):
        return False
    if (
        not _statuses(value["fieldStatus"])
        or not isinstance(value["values"], dict)
        or set(value["values"]) - {"isActive", "anchor", "range"}
    ):
        return False
    # pylint: disable-next=unidiomatic-typecheck
    if type(value["values"].get("isActive")) is not bool:
        return False
    values = value["values"]
    if value["state"] == "complete" and (set(values) != {"isActive", "anchor", "range"} or value["fieldStatus"]):
        return False
    return ("anchor" not in values or _anchor(values["anchor"])) and (
        "range" not in values
        or _anchor(values.get("anchor"))
        and _position(values["range"], values["anchor"]["units"])
        and values["range"]["degenerate"]
    )


def line_ranges(value: Any) -> bool:
    """Validate provider line ranges, preview limits and ordered coverage."""
    if not isinstance(value, dict):
        return False
    if value.get("state") == "notProbed":
        return set(value) == {"state", "detail"} and isinstance(value["detail"], str)
    if (
        set(value)
        - {
            "source",
            "requestedUnit",
            "unitSupport",
            "anchor",
            "ranges",
            "state",
            "documentEndReached",
            "detail",
            "anchorStatus",
        }
        or value.get("source")
        not in (
            "IUIAutomation.TextPattern.TextUnit.Line",
            "System.Windows.Automation.TextPattern; exact HWND/runtime read-only supplement.TextUnit.Line",
        )
        or value.get("requestedUnit") != "Line"
    ):
        return False
    if (
        value.get("unitSupport") != "unconfirmed"
        or not _anchor(value.get("anchor"))
        or value.get("state") not in ("complete", "partial")
    ):
        return False
    if (
        # pylint: disable-next=unidiomatic-typecheck
        type(value.get("documentEndReached")) is not bool
        or not isinstance(value.get("detail"), str)
        or not isinstance(value.get("ranges"), list)
    ):
        return False
    if len(value["ranges"]) > 32 or "anchorStatus" in value and not _statuses({"anchor": value["anchorStatus"]}):
        return False
    matches_value = (
        True
        if value["state"] == "complete"
        and (not value["documentEndReached"] or not value["anchor"]["complete"] or "anchorStatus" in value)
        else False
    )
    if matches_value:
        return False
    cursor, preview_units, covered = 0, 0, True
    for item in value["ranges"]:
        if not isinstance(item, dict) or set(item) != {"values", "fieldStatus"} or not _statuses(item["fieldStatus"]):
            return False
        if set(item["fieldStatus"]) - {"position", "text"} or not isinstance(item["values"], dict):
            return False
        fields = item["values"]
        if not fields:
            if not item["fieldStatus"] or value["state"] == "complete":
                return False
            continue
        if (
            set(fields) != {"start", "end", "degenerate", "text"}
            or not isinstance(fields["text"], str)
            or not _position({k: fields[k] for k in ("start", "end", "degenerate")}, value["anchor"]["units"])
        ):
            return False
        if fields["start"] < cursor:
            return False
        covered = covered and fields["start"] == cursor
        try:
            length = len(fields["text"].encode("utf-16-le")) // 2
        except UnicodeError:
            return False
        if (
            length > fields["end"] - fields["start"]
            or length != fields["end"] - fields["start"]
            and "text" not in item["fieldStatus"]
        ):
            return False
        preview_units += length
        cursor = fields["end"]
        if value["state"] == "complete" and item["fieldStatus"]:
            return False
    return preview_units <= 16384 and (value["state"] != "complete" or covered and cursor == value["anchor"]["units"])


def text_agreement(anchor: Record, raw: Record, statuses: Record) -> str | None:
    """Compare a known prefix, not a substring match or a new provider read."""
    text = raw.get("Text.DocumentRange")
    if not isinstance(text, str):
        return "document snapshot unavailable"
    try:
        encoded = text.encode("utf-16-le")
    except UnicodeError:
        return "invalid document text encoding"
    end = anchor["units"] * 2
    if len(encoded) < end:
        captured = statuses.get("Text.DocumentRange", {})
        if (
            captured.get("state") == "truncated"
            and captured.get("textRef")
            and captured.get("totalUnits", 0) >= anchor["units"]
        ):
            # Native positions were checked against the full immutable capture,
            # whose prefix can be longer than this display preview.
            return None
        return "anchor extends beyond this preview; exact anchor in details"
    if anchor["complete"] and "Text.DocumentRange" not in statuses and len(encoded) != end:
        return "anchor conflicts with the complete document length"
    try:
        prefix = encoded[:end].decode("utf-16-le")
    except UnicodeDecodeError:
        return "anchor splits a UTF-16 character"
    if hashlib.sha256(prefix.encode("utf-8")).hexdigest() != anchor["sha256"]:
        return "anchor conflicts with this document snapshot"
    return None


def valid_text_boundaries(position: Record, raw: Record) -> bool:
    """Reject endpoints that split a known UTF-16 surrogate pair."""
    text = raw.get("Text.DocumentRange")
    if not isinstance(text, str):
        return True  # Anchor agreement separately records the missing source.
    try:
        encoded = text.encode("utf-16-le")
    except UnicodeError:
        return False
    return all(
        not 0xDC00 <= int.from_bytes(encoded[slice(offset * 2, offset * 2 + 2)], "little") <= 0xDFFF
        for offset in (position["start"], position["end"])
        if offset * 2 < len(encoded)
    )


def line_text_conflict(fields: Record, raw: Record) -> bool:
    """Line text conflict.

    Args:
        fields: Requested or accounted native fields; unknown fields remain explicit facts.
    """
    text = raw.get("Text.DocumentRange")
    if not isinstance(text, str):
        return False  # Missing source is separately reported by _agreement.
    try:
        original, preview = text.encode("utf-16-le"), fields["text"].encode("utf-16-le")
    except UnicodeError:
        return True
    start = fields["start"] * 2
    known = max(0, min(len(preview), len(original) - start))
    return original[slice(start, start + known)] != preview[:known]
