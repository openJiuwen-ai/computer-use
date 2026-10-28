# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Pure candidate phases shared by selection and narrow resolver probes."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, cast

from computer_use.interaction.write.routes.policy import refuse
from computer_use.interaction.write.routes.selection.model import CandidateSet, SelectionRequest
from computer_use.native.contracts import Channel, Knowledge
from computer_use.native.semantics import action_for_intent, role_name
from computer_use.protocol.execution import unit_contracts

if TYPE_CHECKING:
    from collections.abc import Sequence

    from computer_use.native.contracts import Facts, HostPolicy, Intent, Primitive


_DATETIME_LITERAL = re.compile(
    r"(?:[0-9]{1,4}([/.-])[0-9]{1,2}\1[0-9]{1,4}"
    r"(?: [0-9]{1,2}:[0-9]{2}(?::[0-9]{2})?)?"
    r"|[0-9]{1,2}:[0-9]{2}(?::[0-9]{2})?)"
)


def _exclusive_element_intent(request: SelectionRequest, kind: str, *, platform: str | None = None) -> bool:
    """Restrict a preference to the existing host mode and exact element intent."""
    policy = request.policy
    if policy is None or policy.mode != "exclusive":
        return False
    if platform is not None and request.facts.platform != platform:
        return False
    return request.intent.kind == kind and cast("Binding", request.facts.binding).node is not None


def _physical_click_available(primitive: Primitive, platform: str) -> bool:
    """A physical preference needs an offered pointer route with usable geometry."""
    if primitive.unit != "pointer_sequence" or primitive.supported != Knowledge.YES or "geometry" in primitive.pending:
        return False
    if primitive.click is not None:
        return primitive.click.preference_profile == "physicalWhenAvailableInExclusive"
    return platform == "windows"


def _contextual_replacement(request: SelectionRequest, candidates: Sequence[Primitive]) -> bool:
    """Keep editor callbacks and segmented-date semantics on the offered keyboard route."""
    if not _exclusive_element_intent(request, "replace", platform="windows"):
        return False
    facts = request.facts
    if facts.native_role == "ControlType.ComboBox":
        has_editor = facts.input.editor_owner == cast("Binding", facts.binding).node and facts.input.editor is not None
        if not has_editor and not any(p.unit == "key_sequence" and "selection" in p.pending for p in candidates):
            return False
    elif facts.native_role == "ControlType.Spinner":
        if not _DATETIME_LITERAL.fullmatch(request.intent.parameters.get("text", "")):
            return False
    else:
        return False
    return any(
        p.unit == "key_sequence" and p.supported == Knowledge.YES and "enabled" not in p.pending for p in candidates
    )


def enumerate_intent_units(request: SelectionRequest) -> CandidateSet:
    """Collect native units whose declared intent set includes the requested operation."""
    facts, intent = request.facts, request.intent
    candidates = [
        p
        for p in facts.primitives
        if intent.kind in unit_contracts[p.unit][1] and (p.unit != "native_action" or intent.kind == "action")
    ]
    return CandidateSet(request, tuple(candidates))


def resolve_action_semantics(value: CandidateSet) -> CandidateSet:
    """Match explicit action names and choose native actions for semantic clicks or menu opening."""
    request, candidates = value.request, list(value.primitives)
    facts, intent = request.facts, request.intent
    if intent.kind == "action":
        name = intent.parameters.get("action")
        exact = [p for p in candidates if p.action == name]
        matches = exact or [p for p in candidates if p.action_description == name]
        if len(matches) > 1:
            refuse("ambiguous_target", facts, "More than one native action matches this name.")
        if not matches:
            names = sorted({p.action for p in candidates if p.action is not None})
            observed = (
                " Observed names: "
                + ", ".join(names[:8])
                + ("; more in the element observation." if len(names) > 8 else ".")
                if names
                else ""
            )
            refuse(
                "input_unavailable",
                facts,
                f"No native action matches {name!r} on this element." + observed,
                "read the element and use a complete action name or description from that observation",
            )
        return CandidateSet(request, tuple(p for p in matches if p.supported == Knowledge.YES))
    # Coordinate requests mean pointer events, even when a semantic action exists.
    matches_key = (
        True
        if any(key in intent.parameters for key in ("x", "fromX"))
        or (
            intent.kind == "click"
            and (intent.parameters.get("button", "left") != "left" or intent.parameters.get("count", 1) != 1)
        )
        else False
    )
    if matches_key:
        candidates = [p for p in candidates if p.channel == Channel.POINTER]
    elif intent.kind in ("click", "openMenu"):
        actions = [p for p in facts.primitives if p.unit == "native_action" and p.action is not None]
        name = action_for_intent(
            facts.platform,
            facts.native_role,
            [p.action for p in actions],
            facts.actions_complete,
            intent.kind,
            has_submenu=facts.has_submenu,
        )
        matches = [p for p in actions if p.action == name]
        if len(matches) > 1:
            refuse("ambiguous_target", facts, "More than one native primitive declares the selected action.")
        # Keep the declared primitive unchanged. Python resolves semantics;
        # the driver receives its opaque contract ID and the original intent.
        candidates.extend(p for p in matches if p not in candidates)
    return CandidateSet(request, tuple(candidates))


def _windows_occluded_primary_action(value: CandidateSet, pointers: list[Primitive]) -> bool:
    """Narrow permission after native foreign-occlusion preference, never a fallback.

    Native Windows offers default here only after its query-only foreign-root
    occlusion proof, and admits Expand only under its fresh/final Collapsed
    contract. This phase retains the already resolved primary action;
    it neither infers occlusion from !Ready nor invents an action from the role.
    """
    request, facts = value.request, value.request.facts
    if (
        facts.platform != "windows"
        or facts.enabled != Knowledge.YES
        or not facts.actions_complete
        or role_name(facts.platform, facts.native_role) not in ("button", "link", "menuButton", "comboBox", "menuItem")
        or facts.input.geometry_current != Knowledge.YES
        or facts.input.pointer_route != Knowledge.NO
    ):
        return False
    history = request.history
    if history is not None and (
        history.unavailable
        or history.background_unavailable
        or history.prepared
        or history.click_refresh is not None
        or history.failed_steps
    ):
        return False
    if not any(
        p.click is not None
        and p.click.preference_profile == "default"
        and p.click.admission in ("preparationRequired", "unavailable")
        and p.readiness == Knowledge.NO
        for p in pointers
    ):
        return False
    # resolve_action_semantics already removed non-primary raw actions. In
    # particular raw Invoke beside primary Toggle/Select/Expand is insufficient.
    actions = [p for p in value.primitives if p.unit == "native_action"]
    return (
        len(actions) == 1
        and actions[0].action in ("Invoke.Invoke", "ExpandCollapse.Expand")
        and actions[0].supported == Knowledge.YES
        and actions[0].readiness == Knowledge.YES
        and not actions[0].pending
        and actions[0].click is not None
        and actions[0].click.interaction == "semantic"
        and actions[0].click.delivery == "object_action"
        and actions[0].click.admission == "ready"
    )


def apply_platform_preferences(value: CandidateSet) -> CandidateSet:
    """Apply desktop-mode and platform preferences while preserving the requested operation semantics."""
    request, candidates = value.request, list(value.primitives)
    facts, intent = request.facts, request.intent
    # Explicit native action matching historically returns before physical
    # interaction preferences. Keep that branch independent.
    if intent.kind == "action":
        return value
    if _exclusive_element_intent(request, "click"):
        pointers = [
            p
            for p in candidates
            if p.unit == "pointer_sequence" and p.supported == Knowledge.YES and "geometry" not in p.pending
        ]
        physical = any(_physical_click_available(p, facts.platform) for p in pointers)
        windows_default = facts.platform == "windows" and any(
            p.click is not None and p.click.preference_profile == "default" for p in pointers
        )
        if physical or windows_default and not _windows_occluded_primary_action(value, pointers):
            candidates = [p for p in candidates if p.channel == Channel.POINTER]
    if intent.kind == "paste":
        candidates = [p for p in candidates if p.unit in ("clipboard_paste", "prepare_receiver")]
    if intent.kind == "key":
        candidates = [p for p in candidates if p.unit == "key_sequence"]
    if _contextual_replacement(request, candidates):
        # A composite editor or selectable date/time spinner can drive normal
        # editing semantics, including segmented dates whose Value setter
        # accepts a string without applying it. Choose before any mutation;
        # never replay an object replacement through keys after dispatch.
        candidates = [p for p in candidates if p.unit == "key_sequence"]
    return CandidateSet(request, tuple(candidates))


def order_supported_candidates(value: CandidateSet) -> CandidateSet:
    """Order supported object, editor, keyboard, and pointer units for route selection."""
    request, candidates = value.request, value.primitives
    intent = request.intent
    if intent.kind == "action":
        return value  # Keep observed order for the explicit-action branch.
    # Independent object edits remain first. Contextual text setters can report
    # success without driving the editor's ordinary input/commit callbacks. If
    # a keyboard contract is already ready, use that editing path instead.
    # choose() still requires editing evidence before permitting keyboard input.
    order = {Channel.OBJECT: 0, Channel.EDITOR: 2, Channel.KEYBOARD: 3, Channel.POINTER: 4}

    def priority(primitive: Primitive) -> tuple[int, int, int, str]:
        channel_order = order.get(primitive.channel)
        if channel_order is None:
            raise KeyError(primitive.channel)
        if primitive.click is not None:
            offer = primitive.click
            return (
                channel_order,
                1 if primitive.unit == "native_selection" else 0,
                0 if offer.admission == "ready" and offer.delivery == "desktop_pointer" else 1,
                offer.route_family,
            )
        ready_text_keys = (
            intent.kind in ("insert", "replace")
            and primitive.unit == "key_sequence"
            and primitive.channel == Channel.KEYBOARD
            and primitive.readiness == Knowledge.YES
        )
        return (
            1 if ready_text_keys else channel_order,
            1 if primitive.unit == "native_value_replace" else 0,
            0,
            primitive.id,
        )

    # Click admission carries the reason even when a native route is unavailable.
    # The click selector owns eligibility; dropping these offers here erases that
    # evidence before it can explain the failure.
    return CandidateSet(
        request,
        tuple(sorted((p for p in candidates if p.supported == Knowledge.YES or p.click is not None), key=priority)),
    )


CANDIDATE_PHASES = (
    ("enumerate_intent_units", enumerate_intent_units, SelectionRequest, CandidateSet, ()),
    (
        "resolve_action_semantics",
        resolve_action_semantics,
        CandidateSet,
        CandidateSet,
        ("ambiguous action", "missing action"),
    ),
    ("apply_platform_preferences", apply_platform_preferences, CandidateSet, CandidateSet, ()),
    ("order_supported_candidates", order_supported_candidates, CandidateSet, CandidateSet, ()),
)


def enumerate_candidates(facts: Facts, intent: Intent, policy: HostPolicy | None = None) -> tuple[Primitive, ...]:
    """Run the pure candidate phases for resolver capability checks."""
    # This is a narrow predicate helper for resolver Passes as well as the
    # compatibility policy API; it must not start a nested registered graph.
    value = SelectionRequest(facts, intent, policy)
    for _, phase, _, _, _ in CANDIDATE_PHASES:
        value = phase(value)
    return cast("CandidateSet", value).primitives


if TYPE_CHECKING:
    from computer_use.native.contracts import Binding
