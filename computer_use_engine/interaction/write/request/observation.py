# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""One bounded read suffix; its outcome never replaces the write receipt."""

from __future__ import annotations

import asyncio
import weakref
from dataclasses import replace
from typing import TYPE_CHECKING

from computer_use.errors import ComputerUseError
from computer_use.identity import FrozenRecord
from computer_use.interaction.read.views.related import RelatedRead, original_lifecycle
from computer_use.interaction.requests import ExecuteAction
from computer_use.interaction.write.request.model import ObservedAction
from computer_use.native.scopes import FixedWindowInvalidated, FixedWindowUnconfirmed
from computer_use.pipeline import Contract, Effect
from computer_use.pipeline.core import Node
from computer_use.session.interfaces import WindowIdentity
from computer_use.session.observation_data import observation_data

POSTWRITE_OBSERVATION_MS = 5000
WRITE_WITH_OBSERVATION_MS = 30000
TARGET_OBSERVATION_ERRORS = frozenset({"stale_target", "observation_incomplete", "blocked_by_modal"})

if TYPE_CHECKING:
    from computer_use.interaction.controller import SessionController
    from computer_use.pipeline.core import Control, Step
    from computer_use.protocol.records import Record


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
        source_owner = getattr(instance, "owners", {}).get(getattr(request.target, "root_window", None))
        before_tree = self.controller.state(source_owner).query_snapshot if source_owner is not None else None
        related = (
            RelatedRead(
                request.target,
                tuple(WindowIdentity.of(item) for item in prior["surfaces"]),
                weakref.ref(request.owner) if fixed is not None else None,
                WindowIdentity(fixed.root_window, fixed.surface, fixed.instance) if fixed is not None else None,
                before_tree,
                request.intent.kind == "click",
            )
            if prior is not None
            else None
        )
        try:
            receipt = await self.body.arun(request, control)
        except ComputerUseError as error:
            if error.effect not in ("partial", "possible") and error.code not in TARGET_OBSERVATION_ERRORS:
                raise
            # This is still the original exception, not a fabricated successful
            # receipt. Preparation/dispatch evidence and its original cause stay
            # authoritative while the shared read suffix may add current facts.
            outcome = await self.observe(
                request,
                ObservedAction(None),
                f"Action effect remains {error.effect}: {error.detail}",
                session_identity,
                related,
            )
            for field in ("observation", "observation_status", "observation_error", "window_lifecycle", "choices"):
                setattr(error, field, getattr(outcome, field))
            if outcome.observation_error is not None:
                from computer_use.session.events import action_observation_completed

                action_observation_completed(error)
            raise
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

        The read branch may refresh unchanged first content once; this suffix
        never retries input or infers the application's business outcome.
        """
        owner = request.owner
        # Internal non-UI owners and unobserved launch results have no acquired
        # application scope. The suffix never launches an application.
        instance = getattr(owner, "_instance", None)
        if owner is not None and (
            instance is None
            or getattr(instance, "directory", None) is None
            and getattr(instance, "anchor", None) is None
        ):
            return replace(outcome, observation_status="not_requested")
        related_owner = getattr(owner, "_app", owner)
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
                if owner is None:
                    observed = await self.controller.interfaces.read_desktop("image", False)
                else:
                    visual = request.target.capture is not None or (
                        request.target.node is None and getattr(owner, "_image", None) is not None
                    )
                    options = dict(related=related) if related is not None else {}
                    observed = await self.controller.interfaces.read(
                        related_owner, "image" if visual else "tree", False, None, **options
                    )
            metadata = observation_data(observed) or {}
            partial = metadata.get("partial", False)
            directory = metadata.get("directory")
            lifecycle = original_lifecycle(
                request.target, directory, related.original_window if related is not None else None
            )
            choices = self.window_choices(related_owner, directory)
            refresh_gap = next((gap for gap in metadata.get("gaps", ()) if gap.component == "refresh"), None)
            if not metadata.get("content_available", True):
                gaps = metadata.get("gaps", ())
                return replace(
                    outcome,
                    observation=observed,
                    window_lifecycle=lifecycle,
                    choices=choices,
                    observation_error=FrozenRecord(
                        dict(
                            code=gaps[0].code if gaps else "observation_incomplete",
                            detail="; ".join(gap.detail for gap in gaps)
                            or "The requested observation content is unavailable; only scope metadata was captured.",
                            session_usable=self.session_usable(request, session_identity),
                        )
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
                window_lifecycle=original_lifecycle(
                    request.target, directory, related.original_window if related is not None else None
                )
                if directory is not None
                else "closed"
                if closed
                and error.binding.root_window == request.target.root_window
                and error.binding.instance == request.target.instance
                else "unknown",
                choices=choices,
            )

    def window_choices(self, owner, directory):
        if directory is None or owner is None:
            return ()
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
