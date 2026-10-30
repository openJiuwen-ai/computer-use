# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Complete text-selection summaries and exact inline-range coverage proofs."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import json
from types import MappingProxyType
from typing import Any

from computer_use.interaction.read.facts.groups import reference_set
from computer_use.interaction.read.presentation.fallback import literal
from computer_use.interaction.read.presentation.ir import thaw
from computer_use.interaction.read.presentation.text_context import selection_suffix, supplements, unit_label
from computer_use.interaction.read.presentation.policy import editing


def short_collapsed_selection(references: str, span: Mapping[str, Any], unit: str | None) -> str | None:
    """Render the exact interval of an already-proven complete collapsed selection."""
    return f"Selection [{references}]: [{span['start']},{span['end']}) {unit_label(unit)}"


@dataclass(frozen=True)
class SelectionSummary:
    """One source's selection statement and its optional complete inline-range proof.

    Attributes:
        ref: Original display reference, never a presentation parent's identity.
        source: Native source token associated with ref in this snapshot.
        unit: Declared text offset unit; no unit conversion is performed.
        read_state: Selection collection status shown by the statement.
        spans: Complete source spans retained for coverage audit, including full selected text.
        coverage: Source collection coverage metadata, separate from rendered previews.
        compact: Compact statement suffix, including state and text availability.
        detailed: Expanded statement suffix used by diagnostic views.
        interval: Exact source start/length when the native range, unit and read state agree.
        group: Same-scope identity of an entirely equal collapsed-selection statement.
        inactive: Complete empty selection whose own focus report is explicitly false.
            Public compact observations may retain this position in captured details.
    """

    ref: str
    source: str
    unit: str | None
    read_state: str
    spans: tuple[Mapping[str, Any], ...]
    coverage: Mapping[str, Any]
    compact: str
    detailed: str
    interval: tuple[int, int] | None
    group: tuple[str, str] | None
    inactive: bool = False

    def detail_statement(self) -> str:
        """Return the complete same-capture statement used by diagnostic delivery."""
        return "Text selection of " + self.ref + self.detailed

    def statement(self, references: str, *, concise: bool = False) -> str:
        """Format this source's ranges, preserving the original collection knowledge.

        A complete collapsed range without reported text has a shorter whole-view
        spelling. Other states and range coverage retain their existing statement.
        """
        if (
            concise
            and self.read_state == "complete"
            and self.interval is not None
            and not self.coverage
            and len(self.spans) == 1
        ):
            span = self.spans[0]
            if span.get("collapsed") is True and span.get("text") is None:
                short = short_collapsed_selection(references, span, self.unit)
                if short is not None:
                    return short
                return (
                    f"Selection [{references}]: complete; {unit_label(self.unit)} "
                    f"[{span['start']},{span['end']}), collapsed; text unknown"
                )
        return "Text selection [" + references + "]" + self.compact


@dataclass(frozen=True)
class SelectionSummaryPlan:
    """Statements delivered with a snapshot and their per-source range coverage.

    A formatter may omit an inline range only when the parent composition promises
    to publish these statements. Standalone node formatters receive no plan.
    """

    entries: Mapping[str, SelectionSummary]

    def covers(self, ref: str, value: Mapping[str, Any]) -> bool:
        """Require exact integer coordinates; bools and foreign fields are not ranges."""
        entry = self.entries.get(ref)
        return (
            entry is not None
            and entry.interval is not None
            and set(value) == {"location", "length"}
            and type(value["location"]) is int
            and type(value["length"]) is int
            and entry.interval == (value["location"], value["length"])
        )

    def declarations(self, *, concise: bool = False, defer_inactive: bool = False) -> Mapping[str, str]:
        """Locate each source's exact public compact statement, including shared refs."""
        grouped: dict[tuple[str, str], list[SelectionSummary]] = {}
        result = {}
        for entry in self.entries.values():
            if defer_inactive and entry.inactive:
                continue
            if entry.group is None:
                result[entry.ref] = entry.statement(entry.ref, concise=concise)
            else:
                grouped.setdefault(entry.group, []).append(entry)
        for entries in grouped.values():
            line = entries[0].statement(reference_set(entry.ref for entry in entries), concise=concise)
            result.update((entry.ref, line) for entry in entries)
        return MappingProxyType(result)

    def grouped_lines(self, *, concise: bool = False, defer_inactive: bool = False) -> list[str]:
        """Share identical statements only inside their proven original window/root."""
        grouped: dict[tuple[str, str], list[SelectionSummary]] = {}
        for entry in self.entries.values():
            if defer_inactive and entry.inactive:
                continue
            if entry.group is not None:
                grouped.setdefault(entry.group, []).append(entry)
        return [
            entries[0].statement(reference_set(entry.ref for entry in entries), concise=concise)
            for entries in grouped.values()
        ]


def _scopes(roots: Sequence[Mapping[str, Any]]) -> dict[str, str]:
    """Retain native window/menu boundaries; ambiguous shared membership is ungrouped."""
    result: dict[str, str] = {}
    ambiguous: set[str] = set()
    pending = [(root, root["ref"]) for root in roots]
    while pending:
        node, scope = pending.pop()
        ref = node["ref"]
        if node.get("role") in ("window", "sheet", "menuBar"):
            scope = ref
        if ref in result and result[ref] != scope:
            ambiguous.add(ref)
        result[ref] = scope
        pending.extend((child, scope) for child in node.get("children", ()))
    return {ref: scope for ref, scope in result.items() if ref not in ambiguous}


def selection_summary_plan(
    facts: Mapping[str, Any], refs: Mapping[str, str], *, roots: Sequence[Mapping[str, Any]] = ()
) -> SelectionSummaryPlan:
    """Derive the exact summaries and safe range equivalents from captured facts.

    Partial, conflicting, stale and unknown-unit ranges keep their inline copy.
    Selected-text previews cannot cover the independent selectedText field. Group
    identity uses complete typed source values, never their shortened display text.
    """
    aliases = {token: ref for ref, token in refs.items()}
    scopes = _scopes(roots)
    uncertain_sources: set[str] = set()
    uncertain_capture = False
    for issue in facts.get("issues", ()):
        path = tuple(issue.get("path", ()))
        if not path or path == ("nodes",) and issue.get("state") != "partial":
            uncertain_capture = True
        elif len(path) == 2 and path[0] == "nodes":
            uncertain_sources.add(str(path[1]))
    inputs: set[str] = set()
    pending = list(roots)
    while pending:
        node = pending.pop()
        if editing(node):
            inputs.add(node["ref"])
        pending.extend(node.get("children", ()))
    entries: dict[str, SelectionSummary] = {}
    for node in facts["nodes"]:
        values = node["values"]
        selections = values.get("text.selections")
        if "text.source" not in values or selections is None:
            continue
        ref = aliases[node["node"]]
        incidental = (
            ref not in inputs
            and values.get("role") not in ("text_field", "text_area", "combo_box", "document", "web_area")
            and not values.get("states.focused")
            and not values.get("text.selected_text")
            and len(selections) == 1
            and selections[0].get("start") == 0
            and selections[0].get("end") == 0
            and selections[0].get("text") in (None, "")
            and "text.selections" not in node["fieldStatus"]
        )
        if incidental:
            continue
        ref, unit = aliases[node["node"]], values.get("text.unit")
        statuses = [status for name, status in node["fieldStatus"].items() if name.startswith("text.selections")]
        state = node["fieldStatus"].get("text.selections", {}).get("state", "partial" if statuses else "complete")
        coverage = supplements(node).get("text.selections.coverage", {})
        if coverage.get("conflicting"):
            state = "conflict"
        total = coverage.get("total")
        count = f"{len(selections)}/{total}" if type(total) is int else str(len(selections))
        pieces: list[str] = []
        for index, span in enumerate(selections):
            label = "empty range" if span.get("collapsed") is True else "range"
            content = span.get("text")
            excerpt = (
                "range text not reported"
                if content is None
                else literal(content[:160]) + ("…" if len(content) > 160 else "")
            )
            position = (
                f"; offsets [{span['start']}, {span['end']}) {unit or 'unspecified unit'}"
                if "start" in span and "end" in span
                else ""
            )
            pieces.append(f"{label} {index + 1}: {excerpt}{position}")
        compact = selection_suffix(selections, state, total, unit)
        detailed = (
            f" ({state}; {count} ranges): "
            + ("; ".join(pieces) or "none")
            + ". Keyboard focus and caret activity are separate facts."
        )
        coverage_paths = tuple(
            key
            for key, value in values.items()
            if key.startswith("supplements[")
            and isinstance(value, Mapping)
            and value.get("key") == "text.selections.coverage"
        )
        coverage_issue = any(
            name == "supplements" or any(name == path or name.startswith(path + ".") for path in coverage_paths)
            for name in node["fieldStatus"]
        )
        clean = (
            state == "complete"
            and not coverage_issue
            and not any(
                name == "text" or name in ("text.unit", "text.source") or name.startswith("text.selections")
                for name in node["fieldStatus"]
            )
            and not coverage.get("conflicting")
            and set(coverage).issubset({"total"})
            and (total is None or type(total) is int and total == len(selections))
        )
        interval = None
        if clean and unit in ("unicode_codepoint", "utf16_code_unit") and len(selections) == 1:
            span = selections[0]
            start, end = span.get("start"), span.get("end")
            if type(start) is int and type(end) is int and 0 <= start <= end:
                interval = (start, end - start)
        group = None
        if (
            clean
            and ref in scopes
            and selections
            and all(span.get("collapsed") is True and span.get("text") in (None, "") for span in selections)
        ):
            identity = json.dumps(
                [unit, thaw(selections), thaw(coverage)], ensure_ascii=False, sort_keys=True, allow_nan=False
            )
            group = (scopes[ref], identity)
        focus_paths = ("states.focused", "text.selected_text", "text.caret", "text.caret_active")
        focus_issues = "states" in node["fieldStatus"] or any(
            name == path or name.startswith(path + ".") for name in node["fieldStatus"] for path in focus_paths
        )
        inactive = (
            clean
            and not uncertain_capture
            and node["node"] not in uncertain_sources
            and interval is not None
            and interval[1] == 0
            and len(selections) == 1
            and values.get("states.focused") is False
            and not focus_issues
            and values.get("text.caret_active") is not True
            and ("text.caret" not in values or values.get("text.caret_active") is False)
            and values.get("text.selected_text") in (None, "")
            and selections[0].get("collapsed") is True
            and selections[0].get("text") in (None, "")
        )
        entries[ref] = SelectionSummary(
            ref,
            node["node"],
            unit,
            state,
            tuple(MappingProxyType(dict(span)) for span in selections),
            MappingProxyType(dict(coverage)),
            compact,
            detailed,
            interval,
            group,
            inactive,
        )
    return SelectionSummaryPlan(MappingProxyType(entries))
