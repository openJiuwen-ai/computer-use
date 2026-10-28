# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Immutable disclosure choices over captured fields and their exact detail routes."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from collections.abc import Mapping


@dataclass(frozen=True)
class DisclosurePolicy:
    """Thresholds for high-benefit excerpts of complete captured text.

    Attributes:
        minimum_characters: Small fields remain inline without extra navigation.
        head_characters: Python characters retained from the field's beginning.
        tail_characters: Python characters retained from the field's end.
        minimum_saved_units: Minimum estimated token saving, including the detail notice.
        minimum_saved_fraction: Minimum saving relative to the original field estimate.
        notice_characters: Allowance for the excerpt label, range and recovery instruction.
    """

    minimum_characters: int = 2048
    head_characters: int = 320
    tail_characters: int = 160
    minimum_saved_units: int = 256
    minimum_saved_fraction: float = 0.2
    notice_characters: int = 160

    def __post_init__(self) -> None:
        integers = (
            self.minimum_characters,
            self.head_characters,
            self.tail_characters,
            self.minimum_saved_units,
            self.notice_characters,
        )
        if any(type(value) is not int or value < 0 for value in integers):
            raise ValueError("Disclosure counts must be nonnegative integers")
        if not 0 < self.minimum_saved_fraction <= 1:
            raise ValueError("Disclosure saving fraction must be in (0, 1]")
        if self.head_characters + self.tail_characters == 0:
            raise ValueError("A text excerpt must retain at least one character")


@dataclass(frozen=True)
class CapturedTextField:
    """One exact scalar available from a captured element detail document.

    Attributes:
        ref: Published element alias whose native binding remains unchanged.
        attribute: Projected field slot, such as name or value.
        native_field: Exact requested native field printed by element details.
        value: Complete string shared by the native and projected field.
        native_token: Original native object retained by the observation.
        capture: Backend, session and observation identifiers of the source.
        protections: Local reasons to leave this field fully expanded.
        required_ranges: Exact Python-character spans retained around a reported
            selection or caret in this same native text source.
    """

    ref: str
    attribute: str
    native_field: str
    value: str
    native_token: str
    capture: tuple[str, str, str]
    protections: tuple[str, ...] = ()
    required_ranges: tuple[tuple[int, int], ...] = ()


@dataclass(frozen=True)
class TextFragment:
    """An exact half-open Python-character range of a captured string.

    Attributes:
        start: Inclusive Python Unicode-code-point offset in the original string.
        end: Exclusive Python Unicode-code-point offset in the original string.
        text: Exact captured substring for this span.
    """

    start: int
    end: int
    text: str


@dataclass(frozen=True)
class TextExcerpt:
    """Selected field fragments with a precise same-observation recovery route.

    The original value is retained separately from the fragments. Consumers render
    an explicit excerpt notice and use ref for read_details(element=...).

    Attributes:
        source: Complete captured scalar and its same-observation recovery identity.
        fragments: Retained exact source spans in source order.
        estimated_saved_units: Estimated token-cost reduction after presentation and recovery allowances.
    """

    source: CapturedTextField
    fragments: tuple[TextFragment, ...]
    estimated_saved_units: int

    @property
    def total(self) -> int:
        """Return the original captured length in Python characters."""
        return len(self.source.value)

    @property
    def shown(self) -> int:
        """Return the retained character count, excluding generated separators."""
        return sum(fragment.end - fragment.start for fragment in self.fragments)

    @property
    def omitted(self) -> int:
        """Return the number of captured characters deferred to details."""
        return self.total - self.shown


@dataclass(frozen=True)
class DisclosureRequest:
    """Verified fields and the local policy for one pure disclosure decision.

    Attributes:
        fields: Complete captured scalar fields eligible for excerpt assessment.
        enabled: Whether this request enables the optional presentation transformation.
        policy: Thresholds and recovery allowances used to choose candidates.
    """

    fields: tuple[CapturedTextField, ...]
    enabled: bool = False
    policy: DisclosurePolicy = field(default_factory=DisclosurePolicy)


@dataclass(frozen=True)
class ClassifiedDisclosure:
    """Candidates permitted by field protection, before benefit estimation.

    Attributes:
        request: Immutable inputs and policy for this local planning stage.
        candidates: Complete fields satisfying local source-protection rules.
    """

    request: DisclosureRequest
    candidates: tuple[CapturedTextField, ...]


@dataclass(frozen=True)
class SelectedDisclosure:
    """Chosen excerpts awaiting exact range and source-route validation.

    Attributes:
        excerpts: Chosen source fragments awaiting final source and range validation.
    """

    excerpts: tuple[TextExcerpt, ...]


@dataclass(frozen=True)
class DisclosurePlan:
    """Read-only excerpt lookup with every exact source payload retained.

    Attributes:
        excerpts: Decisions keyed by the original element and projected field slot.
    """

    excerpts: Mapping[tuple[str, str], TextExcerpt] = field(default_factory=lambda: MappingProxyType({}))

    def get(self, ref: str, attribute: str) -> TextExcerpt | None:
        """Return the chosen excerpt for an original field, or None."""
        return self.excerpts.get((ref, attribute))
