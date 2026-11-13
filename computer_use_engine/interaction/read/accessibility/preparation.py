# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Prepare and retain the output of a Rust accessibility capture."""

from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property
from typing import TYPE_CHECKING, Any

from computer_use.pipeline import Contract, Effect, Pass, Pipeline

if TYPE_CHECKING:
    from computer_use.interaction.read.accessibility.snapshot import NativeSnapshot
    from computer_use.pipeline.core import Control


PURE = frozenset({Effect.PURE})


@dataclass(frozen=True)
class WindowDisclosureContext:
    """Explicit directory evidence for assessing a blocked background window.

    The App read composer owns these facts. Per-tree parentage or screen geometry
    cannot establish them. A current modal window always remains expanded.
    ``scope_confirmed`` checks the collected root against its fixed native binding;
    ``directory_complete`` separately records whether other windows may be missing.

    Attributes:
        window_ref: Published window-directory reference for this capture.
        current: Whether this is the currently observed member window.
        modal: Reported native modal state, or None when unknown.
        confirmed_blockers: Confirmed modal descendant-window references blocking this window.
        directory_complete: Whether the source window directory was completely collected.
        scope_confirmed: Whether the collected root matches its original fixed window binding.
    """

    window_ref: int
    current: bool
    modal: bool | None
    confirmed_blockers: tuple[int, ...] = ()
    directory_complete: bool = False
    scope_confirmed: bool = False


@dataclass(frozen=True)
class SnapshotRenderRequest:
    """Presentation options and read-only source facts for one snapshot.

    Attributes:
        snapshot: Facts captured for this observation; display edits do not mutate native
            evidence.
        trace: Whether to record per-pass presentation diagnostics for this observation.
        compact: Whether to use compact task-facing text while retaining full source details.
        public_api: Whether continuation hints use the current public element API syntax.
        share_descriptions: Whether compact whole-view output may share proven empty
            description fields; element reads retain their local inline fields.
    """

    snapshot: NativeSnapshot
    trace: bool = False
    compact: bool = False
    public_api: bool = False
    share_descriptions: bool = True
    window_context: WindowDisclosureContext | None = None
    recovery_prefix_units: int = 0


@dataclass(frozen=True)
class PreparedSnapshot:
    """Opaque, source-preserving preparation for an admitted native whole read."""

    request: SnapshotRenderRequest
    snapshot: NativeSnapshot
    handle: Any

    @cached_property
    def refs(self) -> tuple[str, ...]:
        return tuple(self.handle.refs())

    @cached_property
    def inline_child_refs(self) -> tuple[str, ...]:
        return tuple(self.handle.inline_child_refs())

    @property
    def recovery_units(self) -> int:
        return self.handle.recovery_units()


def _prepare(request: SnapshotRenderRequest, control: Control) -> PreparedSnapshot:
    """Prepare opaque roots while retaining the enclosing pipeline's original budget."""
    snapshot = request.snapshot
    context = request.window_context
    window = None if context is None else (
        context.window_ref, context.current, context.modal, context.confirmed_blockers,
        context.directory_complete, context.scope_confirmed,
    )
    checkpoint = control.remaining
    if checkpoint is not None:
        checkpoint()
    handle = snapshot.numbered.prepare(
        window, request.recovery_prefix_units, checkpoint,
        request.compact, request.public_api, request.share_descriptions, request.trace,
    )
    if checkpoint is not None:
        checkpoint()
    return PreparedSnapshot(request, snapshot, handle)


class PrepareSnapshotPipeline(Pipeline[SnapshotRenderRequest, PreparedSnapshot]):
    """Prepare captured roots inside the originating call and budget."""

    def __init__(self) -> None:
        super().__init__(root=self.build())

    def build(self):
        return Pass("PrepareSnapshotRoots", _prepare, Contract(SnapshotRenderRequest, PreparedSnapshot, PURE))


def prepare_snapshot(
    snapshot: NativeSnapshot,
    *,
    compact: bool = True,
    public_api: bool = True,
    trace: bool = False,
    _control: Control | None = None,
) -> PreparedSnapshot:
    return PrepareSnapshotPipeline().run(SnapshotRenderRequest(snapshot, trace, compact, public_api), _control)
