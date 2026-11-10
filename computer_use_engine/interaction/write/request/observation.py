# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""One bounded read suffix; its outcome never replaces the write receipt."""

from __future__ import annotations

import asyncio
import weakref
from dataclasses import replace
from typing import TYPE_CHECKING

from computer_use.errors import ComputerUseError
from computer_use.identity import FrozenRecord
from computer_use.interaction.query.instances.scope import require_identity
from computer_use.interaction.read.views.changes import PostwriteChanges
from computer_use.interaction.read.views.related import RelatedRead, original_lifecycle
from computer_use.interaction.requests import ExecuteAction
from computer_use.interaction.write.request.menu_observation import observed_menu_window
from computer_use.interaction.write.request.model import ObservedAction
from computer_use.native.contracts import Binding
from computer_use.native.scopes import FixedWindowInvalidated, FixedWindowUnconfirmed, window_binding
from computer_use.pipeline import Contract, Effect
from computer_use.pipeline.core import Node
from computer_use.session.interfaces import WindowIdentity
from computer_use.session.observation_data import observation_data
from computer_use.session.operations import current_operation_budget
from computer_use.session.surface_content import content_host

POSTWRITE_OBSERVATION_MS = 5000
WRITE_WITH_OBSERVATION_MS = 30000
TARGET_OBSERVATION_ERRORS = frozenset({"stale_target", "observation_incomplete", "blocked_by_modal"})

if TYPE_CHECKING:
    from computer_use.interaction.controller import SessionController
    from computer_use.pipeline.core import Control, Step
    from computer_use.protocol.records import Record


def selection_target(request, before_tree, before_target) -> Binding | None:
    """Choose a proved selectable source to check after a click or copy chord."""
    target, intent = request.target, request.intent
    if before_tree is None or before_target is None or not isinstance(target, Binding):
        return None
    if window_binding(before_target) != window_binding(target) or before_tree.native.session != target.session:
        return None
    if intent.kind == "click" and intent.parameters == {"button": "left", "count": 1} and target.node is not None:
        nodes = before_tree.native.nodes
        source = nodes.get(target.node)
        if source is None or source.states.has_popup is True:
            return None
        # A file name or caption can be the clicked part of a selectable row.
        # Reobserve the one containing cell/row only through native child edges.
        if source.role in ("text_field", "text", "image"):
            parents = []
            for ref, node in nodes.items():
                if node.role not in ("cell", "row"):
                    continue
                children = node.relations.get("children")
                if children is not None and children.evidence != "candidate" and target.node in children.targets:
                    parents.append(ref)
            if len(parents) == 1 and nodes[parents[0]].states.selected is not None:
                return replace(target, node=parents[0]) if nodes[parents[0]].states.selected is False else None
        if source.states.selected is False:
            return target
    is_copy = intent.kind == "key" and intent.parameters.get("key") == "super+c"
    if is_copy and before_tree.scope_kind is None and len(before_tree.native.roots) == 1:
        (node,) = before_tree.native.roots
        source = before_tree.native.nodes.get(node)
        if source is not None and source.states.selected is True:
            return replace(target, node=node)
    return None


class WriteWithObservation(Node[ExecuteAction, ObservedAction]):
    """Compose the existing write and read branches around the same original owner.

    The native write and its event publication finish before this optional read.
    Reading uses ordinary binding and image delivery services with no activation.
    There is no replay branch. Related windows are observed through the owning
    App, while the receipt and lifecycle remain bound to the original target.
    """

    def __init__(self, controller: SessionController, body: Step[ExecuteAction, Record]) -> None:
        self.controller, self.body = controller, body
        self.children = (body,)
        super().__init__(
            "WriteWithObservation",
            Contract(ExecuteAction, ObservedAction, body.contract.effects | {Effect.QUERY, Effect.RESOURCE}),
        )

    async def _arun(self, request: ExecuteAction, control: Control) -> ObservedAction:
        # A new read claim must not grant a new deadline after a failed write.
        # limit() also covers element scope refresh before native admission.
        with self.controller.port.calls.limit(WRITE_WITH_OBSERVATION_MS):
            return await self.run_observed(request, control)

    async def run_observed(self, request: ExecuteAction, control: Control) -> ObservedAction:
        session_identity = self.controller.transport.session_identity
        instance = getattr(request.owner, "_instance", None)
        prior = getattr(instance, "directory", None)
        fixed = getattr(request.owner, "_window_identity", None)
        context_root = WindowIdentity(fixed.root_window, fixed.surface, fixed.instance) if fixed is not None else None
        window_family_only = request.intent.kind == "closeWindow"
        if (context_root is None or window_family_only) and prior is not None:
            # A closing child can disappear while its confirmed parent remains.
            # Preserve that exact parent, including for a fixed close request.
            identity = WindowIdentity(request.target.root_window, request.target.surface, request.target.instance)
            original = next((item for item in prior["surfaces"] if WindowIdentity.of(item) == identity), None)
            parent = None
            if original is not None:
                parent_key = (
                    content_host(original) or (original["parent"] if original["parentState"] == "known" else None)
                )
                parent = next((item for item in prior["surfaces"] if item["window"] == parent_key), None)
            context_root = WindowIdentity.of(parent) if parent is not None else None
        source_owner = getattr(instance, "owners", {}).get(getattr(request.target, "root_window", None))
        source = self.controller.state(source_owner) if source_owner is not None else None
        before_tree = source.query_snapshot if source is not None else None
        before_target = source.query_target if source is not None else None
        related = (
            RelatedRead(
                request.target,
                tuple(WindowIdentity.of(item) for item in prior["surfaces"]),
                weakref.ref(request.owner) if fixed is not None else None,
                context_root,
                changes=(
                    PostwriteChanges(None, before_tree)
                    if before_tree is not None else None
                ),
                window_family_application=prior["application"] if window_family_only else None,
            )
            if prior is not None
            else None
        )
        menu_origin = None
        target = request.target
        observe_menu = related is not None and prior["platform"] == "macos" and request.intent.kind == "openMenu"
        window_menu = isinstance(target, Binding) and target.root_window is not None
        if observe_menu and window_menu and prior["session"] == target.session:
            origin = WindowIdentity(target.root_window, target.surface, target.instance)
            application = prior["application"]

            def remember_pre_input_directory(directory):
                nonlocal related, menu_origin
                if (directory["session"], directory["application"]) != (target.session, application):
                    return
                known = tuple(WindowIdentity.of(item) for item in directory["surfaces"])
                if origin in known:
                    related = replace(related, known_windows=tuple(dict.fromkeys((*related.known_windows, *known))))
                    menu_origin = origin

            request = replace(request, on_pre_input_directory=remember_pre_input_directory)
        try:
            receipt = await self.body.arun(request, control)
        except ComputerUseError as error:
            close_unavailable = request.intent.kind == "closeWindow" and error.code == "input_unavailable"
            refresh_needed = error.code in TARGET_OBSERVATION_ERRORS or close_unavailable
            # Internal failure metadata requests observation without changing the public error.
            # pylint: disable-next=protected-access
            if error.effect not in ("partial", "possible") and not refresh_needed and error._observation_reason is None:
                raise
            # This is still the original exception, not a fabricated successful
            # receipt. Preparation/dispatch evidence and its original cause stay
            # authoritative while the shared read suffix may add current facts.
            # Internal dispatch evidence preserves the invoked menu target for failure observation.
            # pylint: disable-next=protected-access
            if menu_origin is not None and error._invoked_menu_target == request.target:
                related = replace(related, menu_origin=menu_origin)
            outcome = await self.observe(
                request,
                ObservedAction(None),
                f"Action effect remains {error.effect}: {error.detail}",
                session_identity,
                related,
            )
            for field in ("observation", "observation_status", "observation_error", "window_lifecycle", "choices"):
                setattr(error, field, getattr(outcome, field))
            # Internal dispatch evidence preserves the invoked menu target for failure observation.
            # pylint: disable-next=protected-access
            invoked_menu = request.intent.kind == "openMenu" and error._invoked_menu_target == request.target
            has_observation = instance is not None and outcome.observation is not None
            if invoked_menu and has_observation and self.session_usable(request, session_identity):
                error.observed_menu_window = observed_menu_window(
                    request.target, before_target, before_tree, outcome.observation, instance.windows,
                )
            if outcome.observation_error is not None:
                from computer_use.session.events import action_observation_completed

                action_observation_completed(error)
            raise
        baseline = receipt.pop("appChangeBaseline", None)
        input_baseline = receipt.pop("appChangeInputBaseline", None)
        if related is not None and baseline is not None and receipt["status"] == "dispatched":
            related = replace(related, changes=PostwriteChanges(
                input_baseline or baseline, before_tree, input_sampled=input_baseline is not None,
            ))
        if related is not None and prior["platform"] == "macos" and not request.intent.global_input:
            related = replace(
                related,
                selection_target=selection_target(request, before_tree, before_target),
                seek_editor=request.intent.kind == "key" and request.intent.parameters.get("key") == "Return",
            )
        return await self.observe(
            request,
            ObservedAction(receipt),
            f"Action already {receipt['status']}: {receipt['detail']}",
            session_identity,
            related,
        )

    async def observe(
        self,
        request: ExecuteAction,
        outcome: ObservedAction,
        progress: str,
        session_identity: tuple[int, str] | None,
        related: RelatedRead | None = None,
    ) -> ObservedAction:
        """Publish a bounded related read for a completed or uncertain write.

        This suffix reports the captured post-input facts. Unchanged content
        does not authorize a fixed wait, another full read or input replay.
        """
        owner = request.owner
        # Internal non-UI owners and unobserved launch results have no acquired
        # application scope. The suffix never launches an application.
        instance = getattr(owner, "_instance", None)
        if instance is None or (
            getattr(instance, "directory", None) is None and getattr(instance, "anchor", None) is None
        ):
            return replace(outcome, observation_status="not_requested")
        strict_window = getattr(owner, "_strict_window", False)
        related_owner = owner if strict_window else getattr(owner, "_app", owner)
        if not self.session_usable(request, session_identity):
            return replace(
                outcome,
                observation_error=FrozenRecord(
                    dict(
                        code="session_unavailable",
                        detail="The original native session is retired or changed; no follow-up read was started.",
                        session_usable=False,
                    )
                ),
            )
        try:
            with (
                self.controller.transport.retain_session(session_identity),
                self.controller.port.calls.limit(POSTWRITE_OBSERVATION_MS),
            ):
                options = {"related": related} if related is not None and not strict_window else {}
                try:
                    observed = await self.controller.interfaces.read(related_owner, "tree", False, None, **options)
                except ComputerUseError as error:
                    if not self.close_confirmation_pending(request, outcome, error, session_identity):
                        raise
                    budget = current_operation_budget()
                    if budget is not None:
                        budget.checkpoint()
                    # A Windows close can remove the window from discovery
                    # before its retained native lifetime is proved ended.
                    # Recheck the exact binding in the same budget.
                    observed = await self.controller.interfaces.read(owner, "tree", False, None)
            metadata = observation_data(observed) or {}
            partial = metadata.get("partial", False)
            directory = metadata.get("directory")
            lifecycle = original_lifecycle(
                request.target, directory, related.original_window if related is not None else None
            )
            choices = self.window_choices(related_owner, directory)
            refresh_gap = next((gap for gap in metadata.get("gaps", ()) if gap.component == "refresh"), None)
            gaps = metadata.get("gaps", ())
            directory_only = (
                related is not None and related.window_family_only and directory is not None
                and directory["application"] == related.window_family_application
                and directory["session"] == request.target.session
                and not related.content_members(directory)
            )
            if not metadata.get("content_available", True) and not directory_only:
                return replace(
                    outcome,
                    observation=observed,
                    window_lifecycle=lifecycle,
                    choices=choices,
                    observation_error=FrozenRecord(
                        {
                            "code": gaps[0].code if gaps else "observation_incomplete",
                            "detail": "; ".join(gap.detail for gap in gaps)
                            or "The requested observation content is unavailable; only scope metadata was captured.",
                            "session_usable": self.session_usable(request, session_identity),
                        }
                    ),
                )
            return replace(
                outcome,
                observation=observed,
                observation_status="partial" if partial else "available",
                observation_error=(
                    FrozenRecord(
                        {
                            "code": refresh_gap.code,
                            "detail": refresh_gap.detail,
                            "session_usable": self.session_usable(request, session_identity),
                        }
                    )
                    if refresh_gap is not None
                    else None
                ),
                window_lifecycle=lifecycle,
                choices=choices,
            )
        except asyncio.CancelledError as error:
            error.add_note(progress)
            raise
        except Exception as error:  # noqa: BLE001 - preserve the already-published input on every read failure
            closed = isinstance(error, FixedWindowInvalidated)
            code = (
                "window_closed"
                if closed
                else error.code
                if isinstance(error, ComputerUseError)
                else "observation_timeout"
                if isinstance(error, TimeoutError)
                else "observation_failed"
            )
            detail = error.detail if isinstance(error, ComputerUseError) else str(error) or type(error).__name__
            choices = ()
            directory = getattr(error, "directory", None)
            if isinstance(error, (FixedWindowInvalidated, FixedWindowUnconfirmed)) and directory is not None:
                # Publish choices from the exact failed read's current catalogue;
                # no extra query, stale catalogue substitution, or rebind.
                try:
                    choices = self.window_choices(related_owner, directory)
                except Exception as choice_error:  # noqa: BLE001 - publishing choices is also an optional read result
                    # A presentation failure cannot erase native lifecycle evidence.
                    detail += f" Current choices could not be published: {choice_error}"
            return replace(
                outcome,
                observation_error=FrozenRecord(
                    dict(
                        code=code,
                        detail=detail,
                        session_usable=self.session_usable(request, session_identity),
                    )
                ),
                window_lifecycle=(
                    "closed"
                    if closed and window_binding(error.binding) == window_binding(request.target)
                    else original_lifecycle(
                        request.target, directory, related.original_window if related is not None else None
                    )
                ),
                choices=choices,
            )

    def close_confirmation_pending(self, request, outcome, error, session_identity):
        owner = request.owner
        fixed = getattr(owner, "_window_identity", None)
        if request.intent.kind != "closeWindow" or getattr(self.controller.port, "platform", None) != "windows":
            return False
        if outcome.receipt is None or outcome.receipt["status"] != "dispatched":
            return False
        if fixed is None or window_binding(fixed) != window_binding(request.target):
            return False
        if not self.session_usable(request, session_identity):
            return False
        if isinstance(error, FixedWindowUnconfirmed):
            if window_binding(error.binding) != window_binding(fixed):
                return False
        elif (error.code, error.effect, error.target) != ("stale_target", "none", "request"):
            # The Windows directory can report request-scoped staleness
            # before it has positive lifetime evidence for any window.
            return False
        try:
            require_identity(owner, WindowIdentity(fixed.root_window, fixed.surface, fixed.instance))
        except ComputerUseError:
            return False
        return True

    def window_choices(self, owner, directory):
        if directory is None or owner is None or getattr(owner, "_strict_window", False):
            return ()
        # UIHandle exposes the separate instance catalogue to internal pipelines.
        # pylint: disable-next=protected-access
        name = self.controller.state(owner).name or str(owner._instance.selector)
        return tuple(self.controller.interfaces.windows.choices(directory, name))

    def session_usable(self, request: ExecuteAction, session_identity: tuple[int, str] | None) -> bool:
        return (
            not self.controller.port.calls.retired
            and session_identity is not None
            and session_identity == self.controller.transport.session_identity
            and request.target.session == self.controller.transport.session
        )

    def _run(self, request: ExecuteAction, control: Control) -> ObservedAction:
        raise TypeError("Writes and related observations require arun()")
