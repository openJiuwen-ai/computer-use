# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Locate numbered object entries in the public tree and record-table grammar."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import re

from computer_use.interaction.read.presentation.rendering.layout.chains import split_chain
from computer_use.interaction.read.presentation.rendering.layout.multi_column_records import expand_multi_column_records
from computer_use.interaction.read.presentation.rendering.layout.row_records import expand_row_records


_NODE = re.compile(r"^(\t*)([0-9]+) (.+)\Z")
_HEADER = re.compile(r"^(\t*)Elements \[([^\[\]\r\n]+)\]:\Z")


@dataclass(frozen=True)
class NumberedEntry:
    """One object's public body and its exact position in the current layout.

    Attributes:
        ref: Display reference printed at the start of this entry.
        line: Zero-based line index in the supplied public text.
        start: Character offset of the reference in that line.
        body: Complete entry text, starting with its reference.
        heading: Role/state prefix supplied by an enclosing Elements heading.
        role_explicit: Whether the body's first word is an explicit role, rather
            than own content under an Elements or table-cell role heading.
    """

    ref: str
    line: int
    start: int
    body: str
    heading: str = ""
    role_explicit: bool = True

    @property
    def expanded(self) -> str:
        """Restore the body's role/state prefix when a shared heading supplies it."""
        if not self.heading:
            return self.body
        return self.ref + " " + self.heading + self.body[len(self.ref) :]


def unquoted_positions(text: str) -> set[int]:
    """Return positions outside JSON strings, including each opening quote."""
    result, quoted, escaped = set(), False, False
    for i, char in enumerate(text):
        if not quoted:
            result.add(i)
        if escaped:
            escaped = False
        elif quoted and char == "\\":
            escaped = True
        elif char == '"':
            quoted = not quoted
    return result


def _table_lines(lines: list[str]) -> dict[int, str]:
    """Locate complete public record-table blocks accepted by their grammar."""
    result = {}
    for index, line in enumerate(lines):
        stripped = line.lstrip("\t")
        if stripped.startswith("Rows: "):
            header, expand, kind = 3, expand_multi_column_records, "multi"
        elif stripped.startswith("Records: each row contains its cell;"):
            header, expand, kind = 2, expand_row_records, "single"
        else:
            continue
        indent = line[: len(line) - len(stripped)]
        end = index + header
        pattern = re.escape(indent) + (r"[0-9]+ \| " if kind == "multi" else r"[0-9]+ / [0-9]+ \| ")
        while end < len(lines) and re.match(pattern, lines[end]):
            end += 1
        block = lines[index:end]
        if end - index >= header + 3 and expand(block) != block:
            result.update({position: kind for position in range(index + header, end)})
    return result


def _split_children(text: str, offset: int) -> list[tuple[int, str]]:
    """Split explicit child operators in one validated public table cell."""
    outside = unquoted_positions(text)
    cuts = [m for m in re.finditer(r" > | ; |; ", text) if m.start() in outside]
    result, start = [], 0
    for cut in cuts:
        result.append((offset + start, text[start : cut.start()]))
        start = cut.end()
    result.append((offset + start, text[start:]))
    return result


def numbered_entries(lines: list[str]) -> list[NumberedEntry]:
    """Locate object bodies in trees, chains and validated record-table rows.

    Shared Elements headings remain attached to their members. Callers use
    their generated records and native evidence to verify each located object.
    """
    result: list[NumberedEntry] = []
    tables = _table_lines(lines)
    heading: re.Match[str] | None = None
    for index, line in enumerate(lines):
        if found := _HEADER.fullmatch(line):
            heading = found
            continue
        parts: list[tuple[int, str, str, bool]] = []
        if index in tables:
            indent = len(line) - len(line.lstrip("\t"))
            columns = line[indent:].split(" | ")
            selected = columns[2:] if tables[index] == "multi" else columns[3:]
            offset = indent + sum(len(value) + 3 for value in columns[: len(columns) - len(selected)])
            for column in selected:
                # Multi-column cells inherit their own role from the table
                # heading. Children after > or ; still print explicit roles.
                parts.extend(
                    (at, body, "", tables[index] != "multi" or part > 0)
                    for part, (at, body) in enumerate(_split_children(column, offset))
                )
                offset += len(column) + 3
        elif match := _NODE.fullmatch(line):
            if heading and match[1] == heading[1] + "\t":
                parts.append((len(match[1]), line[len(match[1]) :], heading[2], False))
            else:
                heading = None
                start = len(match[1])
                for body in split_chain(line[start:]):
                    parts.append((start, body, "", True))
                    start += len(body) + 3
        else:
            heading = None
        for start, body, shared, role_explicit in parts:
            match = re.fullmatch(r"([0-9]+) (.+)", body)
            if match is None or match[2].startswith("|"):
                continue
            result.append(NumberedEntry(match[1], index, start, body, shared, role_explicit))
    return result


def unique_numbered_entries(lines: list[str]) -> dict[str, NumberedEntry]:
    """Return entries whose display reference occurs exactly once in this text."""
    entries = numbered_entries(lines)
    counts = Counter(item.ref for item in entries)
    return {item.ref: item for item in entries if counts[item.ref] == 1}
