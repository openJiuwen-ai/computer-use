"""Readable range and ordering summaries from normalized text-space facts."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from computer_use.interaction.read.presentation.fallback import literal


def unit_label(unit: str | None) -> str:
    """Name the text offset unit without interpreting provider-specific positions."""
    return {"utf16_code_unit": "UTF-16", "unicode_codepoint": "Unicode code points", "utf8_byte": "UTF-8 bytes"}.get(
        unit, unit or "unspecified unit"
    )


def _content(node: Mapping[str, Any]) -> str | None:
    values = node["values"]
    if "text.content" in values:
        return values["text.content"]
    location = values.get("text.content_field")
    if location is not None:
        return next(
            (
                values[key]
                for key, path in node["fieldPaths"].items()
                if list(path) == list(location) and key in values and isinstance(values[key], str)
            ),
            None,
        )
    return None


def _slice(text: str, start: int, end: int, unit: str | None) -> str | None:
    """Slice only standard text units with exact, complete code-point boundaries."""
    if unit == "unicode_codepoint":
        return text[start:end] if 0 <= start <= end <= len(text) else None
    encoding = {"utf16_code_unit": "utf-16-le", "utf8_byte": "utf-8"}.get(unit)
    if encoding is None:
        return None
    try:
        encoded = text.encode(encoding)
    except UnicodeEncodeError:
        return None
    width = 2 if unit == "utf16_code_unit" else 1
    if not 0 <= start <= end <= len(encoded) // width:
        return None
    try:
        return encoded[start * width : end * width].decode(encoding)
    except UnicodeDecodeError:
        return None


def _length(text: str, unit: str | None) -> int | None:
    if unit == "unicode_codepoint":
        return len(text)
    try:
        if unit == "utf16_code_unit":
            return len(text.encode("utf-16-le")) // 2
        if unit == "utf8_byte":
            return len(text.encode("utf-8"))
    except UnicodeEncodeError:
        return None
    return None


def supplements(node: Mapping[str, Any]) -> dict[str, Any]:
    """Index agreeing metadata; conflicting duplicates remain explicit and unusable."""
    result = {}
    conflicts = set()
    for path, item in node["values"].items():
        if not path.startswith("supplements[") or not isinstance(item, Mapping):
            continue
        key, value = item["key"], item["value"]
        if key.startswith("text.") and key.endswith(".coverage") and not isinstance(value, Mapping):
            conflicts.add(key)
        if key in result and result[key] != value:
            conflicts.add(key)
        result[key] = value
    for key in conflicts:
        result[key] = {"conflicting": True}
    return result


def embedded_summaries(node: Mapping[str, Any], ref: str, aliases: Mapping[str, str]) -> list[str]:
    """Describe text membership and construct a sequence only from proven ranges."""
    values, output = node["values"], []
    embedded = values.get("text.embedded")
    if not embedded:
        return output
    unit, pieces = values.get("text.unit"), []
    for token, span in embedded:
        position = (
            f"@{span['start']}..{span['end']}" if "start" in span and "end" in span else " (position unconfirmed)"
        )
        pieces.append(aliases[token] + position)
    output.append(
        f"Embedded objects of {ref} ({unit_label(unit)}): "
        + ", ".join(pieces)
        + "; text-range membership is separate from containment."
    )
    text = _content(node)
    coverage = supplements(node).get("text.embedded.coverage", {})
    if (
        text is None
        or any(name.startswith("text.embedded") for name in node["fieldStatus"])
        or coverage.get("conflicting")
    ):
        return output
    if type(coverage.get("total")) is int and coverage["total"] != len(embedded):
        return output
    length = _length(text, unit)
    if length is None:
        return output
    parts, previous, previous_span, remaining, clipped = [], 0, None, 640, False

    def fragment(value: str) -> str:
        nonlocal remaining, clipped
        shown = value[: min(128, remaining)]
        remaining -= len(shown)
        clipped |= len(shown) != len(value)
        return literal(shown + ("…" if len(shown) != len(value) else ""))

    for token, span in embedded:
        start, end = span.get("start"), span.get("end")
        if type(start) is not int or type(end) is not int or start < previous:
            return output
        if end < start or previous_span == (start, end) and start == end:
            return output
        before, content = _slice(text, previous, start, unit), _slice(text, start, end, unit)
        if before is None or content is None:
            return output
        if before:
            parts.append(fragment(before))
        parts.append("element " + aliases[token] + (" " + fragment(content) if content else ""))
        previous, previous_span = end, (start, end)
    trailing = _slice(text, previous, length, unit)
    if trailing is None:
        return output
    if trailing:
        parts.append(fragment(trailing))
    output.append(
        f"Reading sequence of {ref}: "
        + " → ".join(parts)
        + (" (preview; full text and ranges remain in details)" if clipped else "")
    )
    return output


def line_summaries(node: Mapping[str, Any], ref: str) -> list[str]:
    """Show provider line spans, explicit gaps and unconfirmed remaining layout."""
    values = node["values"]
    spans = values.get("text.line_ranges")
    if spans is None:
        return []
    metadata = supplements(node).get("text.line_ranges.coverage", {})
    unit = values.get("text.unit")
    pieces, previous, remaining = [], 0, 1000
    for span in spans:
        start, end, content = span.get("start"), span.get("end"), span.get("text")
        if type(start) is not int or type(end) is not int:
            pieces.append("span unconfirmed" + (": " + literal(content) if content is not None else ""))
            continue
        if previous < start:
            pieces.append(f"unsegmented source {previous}..{start}")
        shown = content[: min(remaining, 96)] if content is not None else None
        remaining -= len(shown) if shown is not None else 0
        pieces.append(
            f"{start}..{end}"
            + (
                " " + literal(shown + ("…" if len(shown) != len(content) else ""))
                if shown is not None
                else " text unavailable"
            )
        )
        previous = max(previous, end)
    if metadata.get("conflicting"):
        pieces.append("provider coverage declarations conflict")
    if metadata.get("unit_support") == "unconfirmed":
        pieces.append("provider may substitute a larger unit")
    if metadata.get("document_end_observed") is True:
        pieces.append("document end observed")
    elif metadata.get("document_end_observed") is False:
        pieces.append("remaining layout unconfirmed")
    if any(name.startswith("text.line_ranges") for name in node["fieldStatus"]):
        pieces.append("range coverage or anchor unconfirmed; see field status")
    return [f"Native line-unit spans of {ref} ({unit_label(unit)}): " + "; ".join(pieces)]


def relation_summaries(facts: Mapping[str, Any], aliases: Mapping[str, str]) -> list[str]:
    """Expose descriptive and flow relationships without reordering containment."""
    output, flows, unsafe = [], {}, False
    observed = {node["node"] for node in facts["nodes"] if node["observed"]}
    names = {node["node"]: node["values"].get("name") for node in facts["nodes"]}
    for edge in facts["relations"]:
        if edge["state"] in ("absent", "unsupported"):
            continue
        ref = aliases[edge["source"]]
        if edge["kind"] == "described_by":
            output.append(
                f"Description of {ref}: elements "
                + ", ".join(
                    aliases[target]
                    + (
                        " " + literal(names[target][:160] + ("…" if len(names[target]) > 160 else ""))
                        if names.get(target)
                        else ""
                    )
                    for target in edge["targets"]
                )
                + ("" if edge["state"] == "complete" and edge["certainty"] == "confirmed" else " (partial/ambiguous)")
            )
        if edge["kind"] in ("flows_to", "flows_from"):
            partial = edge["state"] != "complete" or edge["certainty"] != "confirmed"
            unsafe |= partial or len(edge["targets"]) > 1 or any(target not in observed for target in edge["targets"])
            if partial:
                output.append(
                    f"Reading order of {ref} ({edge['kind']}): {edge['state']}, {edge['certainty']}; order unconfirmed."
                )
            for target in edge["targets"]:
                source, dest = (ref, aliases[target]) if edge["kind"] == "flows_to" else (aliases[target], ref)
                flows.setdefault(source, set()).add(dest)
    if not flows:
        return output
    incoming = {}
    for source, targets in flows.items():
        for target in targets:
            incoming.setdefault(target, set()).add(source)
    unsafe |= any(len(targets) > 1 for targets in flows.values()) or any(
        len(sources) > 1 for sources in incoming.values()
    )
    degrees = {ref: len(incoming.get(ref, ())) for ref in set(flows) | set(incoming)}
    pending = [ref for ref, degree in degrees.items() if degree == 0]
    visited = 0
    while pending:
        source = pending.pop()
        visited += 1
        for target in flows.get(source, ()):
            degrees[target] -= 1
            if degrees[target] == 0:
                pending.append(target)
    cyclic = visited != len(degrees)
    label = "cyclic/conflicting" if cyclic else "partial/ambiguous" if unsafe else "confirmed provider hints"
    edges = "; ".join(source + " → " + ", ".join(sorted(targets, key=int)) for source, targets in flows.items())
    output.append(f"Reading order ({label}): {edges}; containment order is unchanged.")
    return output
