# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""The acquired Rust graph, with its aliases owned by the existing reader."""

from __future__ import annotations

from abc import abstractmethod
from dataclasses import dataclass, field
from functools import cached_property
from types import MappingProxyType
from typing import TYPE_CHECKING, NotRequired, Protocol, TypeAlias, TypedDict


if TYPE_CHECKING:
    from collections.abc import Callable, Hashable, Mapping, Sequence

    from computer_use.native.contracts import Binding
    from computer_use.protocol.records import Record


CaptureTarget: TypeAlias = tuple[str, str | None, str | None, str | None, str | None]


class RetainedText(TypedDict):
    value: NotRequired[str]
    text_ref: NotRequired[str]
    total_units: NotRequired[int | None]


class RetainedCursor(TypedDict):
    cursor: str
    offset: int
    total: int


class RetainedElement(TypedDict):
    reference: str
    node: str
    container: bool
    scope_kind: str | None
    observed: dict
    text: RetainedText | None
    children: RetainedCursor | None
    native_window_outside: NotRequired[bool]


class RetainedPage(RetainedCursor):
    reference: str
    kind: str
    node: str
    property: str


ProjectionEvent: TypeAlias = tuple[str, str, str | None, int | None]


class NumberedReadHandle(Protocol):
    def preview_tokens(
        self, checkpoint: Callable[[], int | None] | None = None,
        observer: Callable[[Sequence[ProjectionEvent]], None] | None = None,
    ) -> Sequence[str]:
        ...

    @abstractmethod
    def retained_bindings(
        self, target: CaptureTarget, include_public_pages: bool
    ) -> tuple[list[RetainedElement], list[RetainedPage]]:
        raise NotImplementedError

    def element_details(self, reference: str, scope: tuple[Hashable, ...]) -> str:
        ...

    def delivery_coverage(self) -> tuple[list[tuple[str, str | None]], list[str]]:
        ...


class ReadCaptureHandle(Protocol):
    backend: str
    session: str
    id: str
    roots: Sequence[str]
    interval_ns: tuple[int, int]
    node_count: int
    surface: str | None
    scope_kind: str | None
    change_token: str | None
    source_tokens: Sequence[str]
    focused_element: str | None
    observed: Record

    def with_aliases(self, aliases: dict[str, str]) -> NumberedReadHandle:
        ...

    def contains_observed(self, token: str) -> bool:
        ...

    def busy_nodes(self) -> Sequence[str]:
        ...

    def node_view(self, token: str, fields: Sequence[str] | None = None) -> Record | None:
        ...

    def lookup_evidence(self, fields: Sequence[str]) -> Record:
        ...

    def collection_issues(self) -> list[tuple[str, str | None]]:
        ...

    def complete_content(self, root: str) -> bool:
        ...

    def selected_click_target(self, token: str) -> str | None:
        ...

    def selected_copy_target(self) -> str | None:
        ...

    def menu_presentations(self) -> list[tuple[str, str]]:
        ...

    def menu_removed(self, opener: str, content: str) -> bool:
        ...

    def new_local_menu(self, after: ReadCaptureHandle, target: str, root: str, published: list[str]) -> bool:
        ...

    def sharing_control_signature(self, root: str) -> tuple[str, str] | None:
        ...

    def capture_companion_evidence(self, root: str) -> tuple[str, str] | None:
        ...

    def refreshable(self) -> bool:
        ...

    def window_content_unexposed(self) -> bool:
        ...

    def same_content_scope(self, after: ReadCaptureHandle) -> bool:
        ...

    def has_content_progress(self, after: ReadCaptureHandle) -> bool:
        ...

    def prefer_earlier_budget_capture(self, after: ReadCaptureHandle) -> bool:
        ...

    def resolve_observed(
        self, target: CaptureTarget, path: list[dict[str, str]], capabilities: tuple[str, ...],
        remaining: Callable[[], int],
    ) -> tuple[str, str | None, str | None]:
        ...


@dataclass(frozen=True, eq=False)
class NativeSnapshot:
    """Keep Rust evidence opaque and make only the ordered reference map public to host stages."""

    capture: ReadCaptureHandle
    numbered: NumberedReadHandle
    aliases: Mapping[str, str]
    _page_rows: dict[Binding, list[RetainedPage]] = field(default_factory=dict, init=False, repr=False)

    @classmethod
    def number(cls, capture: ReadCaptureHandle, aliases: dict[str, str]) -> NativeSnapshot:
        return cls(capture, capture.with_aliases(aliases), MappingProxyType(aliases))

    @property
    def session(self) -> str:
        return self.capture.session

    @property
    def backend(self) -> str:
        return self.capture.backend

    @property
    def id(self) -> str:
        return self.capture.id

    @cached_property
    def lookup_facts(self):
        """Read only the fields consumed by the provider's retained name matcher."""
        from computer_use.native.query.observed import capture_lookup_facts, lookup_fields

        return capture_lookup_facts(self.capture.lookup_evidence(sorted(lookup_fields(self.backend))))

    @cached_property
    def roots(self) -> tuple[str, ...]:
        return tuple(self.capture.roots)

    @property
    def interval_ns(self) -> tuple[int, int]:
        return self.capture.interval_ns

    @property
    def node_count(self) -> int:
        return self.capture.node_count

    @property
    def surface(self) -> str | None:
        return self.capture.surface

    @property
    def scope_kind(self) -> str | None:
        return self.capture.scope_kind

    @property
    def change_token(self) -> str | None:
        return self.capture.change_token

    @cached_property
    def source_tokens(self) -> tuple[str, ...]:
        return tuple(self.capture.source_tokens)

    @cached_property
    def refs(self) -> Mapping[str, str]:
        return MappingProxyType({self.aliases[token]: token for token in self.source_tokens})

    def preview_tokens(
        self, checkpoint: Callable[[], int | None] | None = None,
        observer: Callable[[Sequence[ProjectionEvent]], None] | None = None,
    ) -> Sequence[str]:
        if observer is None:
            return self.numbered.preview_tokens(checkpoint)
        return self.numbered.preview_tokens(checkpoint, observer)

    def retained(self, target: Binding, *, include_public_pages: bool = True) -> list[RetainedElement]:
        """Export numbered binding rows once and keep only the reusable page selectors."""
        scope = (target.session, target.instance, target.root_window, target.surface, target.node)
        elements, pages = self.numbered.retained_bindings(scope, include_public_pages)
        if include_public_pages:
            self._page_rows.clear()
            self._page_rows[target] = pages
        return elements

    def public_pages(self, target: Binding) -> Sequence[RetainedPage]:
        """Read the frozen original continuation selectors without assigning aliases."""
        if target not in self._page_rows:
            self.retained(target)
        return self._page_rows[target]

    def with_aliases(self, aliases: dict[str, str]) -> NativeSnapshot:
        return self.number(self.capture, aliases)


ReadSnapshot: TypeAlias = NativeSnapshot
