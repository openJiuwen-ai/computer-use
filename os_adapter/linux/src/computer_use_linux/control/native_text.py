"""Addressed EditableText units, without candidate search or fallback policy.

The SDK chooses a primitive. This unit checks its original object and native
window immediately before each mutation and journals partial/unknown effects.
"""

from __future__ import annotations

from copy import deepcopy

from computer_use_linux.contracts.protocol import Failure, fields, integer, require, string
from computer_use_linux.control.native_target import NativeTarget
from computer_use_linux.control.native_text_codec import text_codec


def text_intent(value):
    fields(value, ("kind", "parameters"))
    require(
        value["kind"] in ("insert", "replace"),
        "This Linux v5 driver currently implements only native text insertion/replacement.",
        "mode_unsupported",
    )
    parameters = fields(value["parameters"], ("text",))
    text = string(parameters["text"], empty=True)
    require(len(text.encode("utf-8")) <= 1024 * 1024, "Text exceeds 1 MiB.")
    require(
        not any(ord(c) < 32 or 127 <= ord(c) <= 159 or ord(c) in (0x2028, 0x2029) for c in text),
        "Use an explicit paste operation for control characters or multiline content.",
    )
    return deepcopy(value)


class NativeText:
    # Replacement, deletion and insertion use the same local mutation pipeline;
    # deletion is journalled partial before a second guarded insertion.

    def __init__(self, backend, store, directory):
        self.b, self.ax, self.store = backend, backend.ax, store
        self.target = NativeTarget(backend, store, directory)
        self.call = self.target.call

    def execute(self, operation, command, ctx):
        from .text.pipeline import NativeTextPipeline

        return NativeTextPipeline(self.ax, self.target, self.guard, self.selection, self.call).run(
            operation, command, ctx
        )

    def guard(self, target, within, ctx, selection=None, *, check=False):
        target, within = self.target.selected(target, within, selection, ctx, check=check, require_editable=True)
        obj, states, interfaces, role, enabled = self.target.read(target, within, ctx)
        editable = (
            "EditableText" in interfaces
            and states.contains(self.ax.A.StateType.EDITABLE)
            and not self.target.read_only(states)
            and role != "password text"
        )
        return obj, enabled, editable, interfaces, role

    def selection(self, obj):
        count = self.call("Text", "get_character_count", obj)
        integer(count, 0, 2147483647)
        selected = self.call("Text", "get_n_selections", obj)
        # Exact types preserve scalar validation and identity contracts.
        # pylint: disable-next=unidiomatic-typecheck
        if type(selected) is not int or selected not in (0, 1):
            raise Failure("The control does not expose one unambiguous insertion point.", "input_unavailable")
        if selected:
            bounds = self.call("Text", "get_selection", obj, 0)
            start, end = getattr(bounds, "start_offset", None), getattr(bounds, "end_offset", None)
            integer(start, 0, count)
            integer(end, start, count)
            return start, end
        position = self.call("Text", "get_caret_offset", obj)
        integer(position, 0, count)
        return position, position

    def inspect(self, operation, target, within, ctx, selection=None):
        obj, enabled, editable, interfaces, role = self.guard(target, within, ctx, selection)
        operation.bind(target, within, self.store)
        kind = operation.intent["kind"]
        supported = editable and (kind == "replace" or "Text" in interfaces)
        selection_known = "unknown"
        codec = None
        ready = enabled and supported
        pending = [] if enabled else ["enabled"]
        if supported and kind == "insert":
            try:
                codec = text_codec(self.ax, obj)
                codec.insertion_length(operation.intent["parameters"]["text"])
            except (Failure, self.ax.GLib.Error):
                ctx.check()
                supported = False
        if supported and kind == "insert":
            try:
                self.selection(obj)
                selection_known = "yes"
            except (Failure, self.ax.GLib.Error):
                ctx.check()
                ready = False
                pending.append("selection")
        primitives = []
        if supported:
            identifier = operation.contract(
                "native_text_edit", target, within, kind=kind, codec=codec, selection=selection
            )
            primitive = dict(
                id=identifier,
                unit="native_text_edit",
                channel="object",
                supported="yes",
                readiness="yes" if ready else "no",
                pending=pending,
                backgroundPreparation="no",
            )
            primitives.append(primitive)
        return self.target.context(target, within, primitives, enabled, role, selection=selection_known)
