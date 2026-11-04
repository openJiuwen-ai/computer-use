# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Pure excerpt eligibility and conservative representation-cost estimation."""

from __future__ import annotations

from math import ceil

from .model import CapturedTextField, DisclosurePolicy, TextExcerpt, TextFragment


def estimate_units(value: str) -> int:
    """Estimate display cost from UTF-8 bytes without binding to one model tokenizer."""
    return ceil(len(value.encode("utf-8", errors="surrogatepass")) / 4)


def choose_excerpt(source: CapturedTextField, policy: DisclosurePolicy) -> TextExcerpt | None:
    """Choose exact head and tail fragments only when the net saving is substantial.

    Args:
        source: Complete scalar whose same-observation detail route was verified.
        policy: Local thresholds, including the generated recovery notice allowance.

    Returns:
        A deterministic excerpt plan, or None when the value should remain inline.
    """
    value = source.value
    if (
        source.protections
        or len(value) < policy.minimum_characters
        or len(value) <= policy.head_characters + policy.tail_characters
    ):
        return None
    spans = list(source.required_ranges)
    if policy.head_characters:
        spans.append((0, policy.head_characters))
    if policy.tail_characters:
        spans.append((len(value) - policy.tail_characters, len(value)))
    combined: list[tuple[int, int]] = []
    for start, end in sorted(spans):
        if not 0 <= start < end <= len(value):
            return None
        if combined and start <= combined[-1][1]:
            combined[-1] = (combined[-1][0], max(combined[-1][1], end))
        else:
            combined.append((start, end))
    fragments = tuple(TextFragment(start, end, value[start:end]) for start, end in combined)
    full_cost = estimate_units(value)
    shortened_cost = sum(estimate_units(fragment.text) for fragment in fragments) + ceil(policy.notice_characters / 4)
    saved = full_cost - shortened_cost
    if saved < policy.minimum_saved_units or saved < full_cost * policy.minimum_saved_fraction:
        return None
    return TextExcerpt(source, fragments, saved)


def validate_excerpt(excerpt: TextExcerpt) -> None:
    """Check exact ranges and a real numeric detail target before freezing a plan."""
    source = excerpt.source
    if not source.ref.isdecimal() or str(int(source.ref)) != source.ref:
        raise ValueError("Disclosure requires a published integer element reference")
    if not source.native_token or not source.native_field or len(source.capture) != 3:
        raise ValueError("Disclosure requires an exact captured native field")
    if source.protections or not excerpt.fragments:
        raise ValueError("A protected field cannot be excerpted")
    previous_end = 0
    for fragment in excerpt.fragments:
        if (
            not 0 <= fragment.start < fragment.end <= len(source.value)
            or fragment.start < previous_end
            or fragment.text != source.value[fragment.start : fragment.end]
        ):
            raise ValueError("Disclosure fragments must preserve ordered original characters")
        previous_end = fragment.end
    if excerpt.omitted <= 0 or excerpt.estimated_saved_units <= 0:
        raise ValueError("Disclosure must defer content with a positive benefit")
    for start, end in source.required_ranges:
        if not any(part.start <= start < end <= part.end for part in excerpt.fragments):
            raise ValueError("Disclosure must retain every required selection and caret span")
