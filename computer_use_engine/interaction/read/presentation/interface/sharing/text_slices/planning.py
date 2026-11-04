# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Select profitable exact ancestor ranges without constructing interface text."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import replace
from types import MappingProxyType

from computer_use.interaction.read.presentation.interface.disclosure.model import TextFragment
from .model import SelectedTextSlices, TextSliceDocument, TextSliceEvidence, TextSlicePlan


def scalar_bytes(value: str) -> int:
    """Estimate the exact escaped UTF-8 scalar cost used by the public terminal."""
    needs_quote = (
        not value
        or value != value.strip()
        or any(c in value for c in ',">\\|;{}')
        or value.startswith(("<", "+", "~", "|"))
        or "(partial:" in value
        or "(showing " in value
        or any(ord(c) < 32 or 127 <= ord(c) <= 159 for c in value)
    )
    if not needs_quote:
        return len(value.encode("utf-8"))
    return 2 + sum(
        2 if c in '"\\\n\r\t\b\f' else 6 if ord(c) < 32 or 127 <= ord(c) <= 159 else len(c.encode("utf-8"))
        for c in value
    )


def _quoted_bytes(value: str) -> int:
    """Count a quoted excerpt without serializing an interface line."""
    return 2 + sum(
        2 if c in '"\\\n\r\t\b\f' else 6 if ord(c) < 32 or 127 <= ord(c) <= 159 else len(c.encode("utf-8"))
        for c in value
    )


def replacement_saving(plan: TextSlicePlan, *, new_anchor: bool) -> int:
    """Include excerpts, recovery instructions and explicit labels in net saving."""
    source, anchor = plan.source, plan.anchor
    if plan.mode == "same":
        reference = len("same text as element .") + len(anchor.ref) + len(anchor.slot)
    else:
        reference = (
            len("excerpt  [part of element .; full: state.read_details(element=)]")
            + len(anchor.ref)
            + len(anchor.slot)
            + len(source.ref)
            + sum(_quoted_bytes(fragment.text) for fragment in plan.fragments)
            + len(" … ".encode("utf-8")) * (len(plan.fragments) - 1)
        )
    if source.atom_slot.label is None:
        reference += len("Text: ")
    if new_anchor and anchor.slot == "name":
        reference += len("Name: ")
    return scalar_bytes(source.value) - reference


def _fragments(value: str, ranges: list[tuple[int, int]]) -> tuple[TextFragment, ...]:
    """Merge overlapping ranges while keeping omitted source spans explicit."""
    merged: list[tuple[int, int]] = []
    for start, end in sorted(ranges):
        if start == end:
            continue
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return tuple(TextFragment(start, end, value[start:end]) for start, end in merged)


def _signature(plan: TextSlicePlan) -> tuple[str, ...]:
    """Represent the content the reader actually sees, without hidden offsets."""
    return tuple(fragment.text for fragment in plan.fragments)


def _edge_ranges(value: str) -> list[tuple[int, int]]:
    """Keep readable first/last text within the same multilingual byte budget."""
    head, used = 0, 0
    for character in value:
        cost = len(character.encode("utf-8"))
        if used + cost > 36:
            break
        head, used = head + 1, used + cost
    tail, used = len(value), 0
    for character in reversed(value):
        cost = len(character.encode("utf-8"))
        if used + cost > 24:
            break
        tail, used = tail - 1, used + cost
    return [(0, head), (tail, len(value))]


def _distinguish(plans: dict[tuple[str, str], TextSlicePlan]) -> dict[tuple[str, str], TextSlicePlan]:
    """Add original differing text when sibling excerpts would look identical.

    Additional spans come only from the captured source. If distinct sources
    still produce the same visible excerpt, those sources stay literal.
    """
    collisions: dict[tuple[str, str, tuple[str, ...]], list[tuple[str, str]]] = defaultdict(list)
    for key, plan in plans.items():
        if plan.mode == "excerpt":
            collisions[(plan.anchor.ref, plan.anchor.slot, _signature(plan))].append(key)
    result = dict(plans)
    for keys in collisions.values():
        if len({plans[key].source.value for key in keys}) < 2:
            continue
        ranges = {key: [(fragment.start, fragment.end) for fragment in plans[key].fragments] for key in keys}
        for index, left in enumerate(keys):
            for right in keys[index + 1 :]:
                first, second = plans[left].source.value, plans[right].source.value
                if first == second:
                    continue
                difference = next(
                    (i for i, (a, b) in enumerate(zip(first, second)) if a != b), min(len(first), len(second))
                )
                for key in (left, right):
                    length = len(plans[key].source.value)
                    ranges[key].append((max(0, difference - 12), min(length, difference + 24)))
        for key in keys:
            result[key] = replace(plans[key], fragments=_fragments(plans[key].source.value, ranges[key]))
        signatures: dict[tuple[str, ...], set[str]] = defaultdict(set)
        for key in keys:
            signatures[_signature(result[key])].add(result[key].source.value)
        for key in keys:
            if len(signatures[_signature(result[key])]) > 1:
                del result[key]
    return result


def choose(evidence: TextSliceEvidence) -> SelectedTextSlices:
    """Choose unique child-value occurrences in literal, complete native ancestors."""
    selected, anchors = {}, set()
    for key, source in evidence.fields.items():
        if source.slot != "value" or key in anchors:
            continue
        current, seen = evidence.native.nodes[source.ref].parent, {source.ref}
        while current is not None and current not in seen:
            seen.add(current)
            parent = evidence.native.nodes.get(current)
            if parent is None or parent.scope != source.scope:
                break
            found = False
            for slot in ("value", "name"):
                anchor_key = (current, slot)
                anchor = evidence.fields.get(anchor_key)
                if anchor is None or anchor_key in selected or anchor.atom_slot.label is None and slot == "value":
                    continue
                position = anchor.value.find(source.value)
                if position < 0 or anchor.value.find(source.value, position + 1) >= 0:
                    continue
                start = len(anchor.value[:position].encode("utf-16-le")) // 2
                end = start + len(source.value.encode("utf-16-le")) // 2
                mode = "same" if source.value == anchor.value else "excerpt"
                fragments = () if mode == "same" else _fragments(source.value, _edge_ranges(source.value))
                plan = TextSlicePlan(source, anchor, start, end, 0, mode, fragments)
                saving = replacement_saving(plan, new_anchor=anchor_key not in anchors)
                if saving < 64:
                    continue
                selected[key] = replace(plan, estimated_saved_bytes=saving)
                anchors.add(anchor_key)
                found = True
                break
            if found:
                break
            current = parent.parent
    selected, anchors = _distinguish(selected), set()
    profitable = {}
    for key, plan in selected.items():
        anchor_key = (plan.anchor.ref, plan.anchor.slot)
        saving = replacement_saving(plan, new_anchor=anchor_key not in anchors)
        if saving >= 64:
            profitable[key] = replace(plan, estimated_saved_bytes=saving)
            anchors.add(anchor_key)
    return SelectedTextSlices(evidence, TextSliceDocument(MappingProxyType(profitable), frozenset(anchors)))


def validate(selected: SelectedTextSlices) -> TextSliceDocument:
    """Verify exact native containment, internal ranges and distinguishable excerpts."""
    evidence, document = selected.evidence, selected.document
    anchors = set()
    for key, plan in document.plans.items():
        source, anchor = plan.source, plan.anchor
        if (
            key != (source.ref, source.slot)
            or evidence.fields.get(key) != source
            or evidence.fields.get((anchor.ref, anchor.slot)) != anchor
            or (anchor.ref, anchor.slot) in document.plans
            or source.scope != anchor.scope
            or plan.estimated_saved_bytes < 64
        ):
            raise ValueError("Text slice must preserve complete sources and a literal same-scope anchor")
        native = evidence.native.nodes[source.ref]
        current, seen = native.parent, {source.ref}
        while current is not None and current != anchor.ref and current not in seen:
            seen.add(current)
            current = evidence.native.nodes[current].parent
        if current != anchor.ref:
            raise ValueError("Text slice anchor is outside the original native ancestor chain")
        raw = anchor.value.encode("utf-16-le")
        if (
            type(plan.start_utf16) is not int
            or type(plan.end_utf16) is not int
            or not 0 <= plan.start_utf16 < plan.end_utf16 <= len(raw) // 2
            or raw[plan.start_utf16 * 2 : plan.end_utf16 * 2].decode("utf-16-le") != source.value
        ):
            raise ValueError("Text slice range does not restore the exact original scalar")
        if plan.mode == "same":
            if source.value != anchor.value or plan.fragments:
                raise ValueError("Same text reuse requires equal complete values")
        elif plan.mode == "excerpt":
            previous = -1
            for fragment in plan.fragments:
                if (
                    type(fragment.start) is not int
                    or type(fragment.end) is not int
                    or not 0 <= fragment.start < fragment.end <= len(source.value)
                    or fragment.start <= previous
                    or source.value[fragment.start : fragment.end] != fragment.text
                ):
                    raise ValueError("Text reuse excerpts must preserve ordered original source spans")
                previous = fragment.end
            if len(plan.fragments) < 2 or plan.fragments[0].start != 0 or plan.fragments[-1].end != len(source.value):
                raise ValueError("Partial text reuse must retain distinct first and last excerpts")
        else:
            raise ValueError("Unknown text reuse display mode")
        saving = replacement_saving(plan, new_anchor=(anchor.ref, anchor.slot) not in anchors)
        if plan.estimated_saved_bytes != saving or saving < 64:
            raise ValueError("Text reuse must include its actual recovery and excerpt costs")
        anchors.add((anchor.ref, anchor.slot))
    visible: dict[tuple[str, str, tuple[str, ...]], set[str]] = defaultdict(set)
    for plan in document.plans.values():
        if plan.mode == "excerpt":
            visible[(plan.anchor.ref, plan.anchor.slot, _signature(plan))].add(plan.source.value)
    if any(len(values) > 1 for values in visible.values()):
        raise ValueError("Distinct child values require distinguishable visible excerpts")
    if frozenset(anchors) != document.anchors:
        raise ValueError("Text slice document must declare exactly its literal anchors")
    return document
