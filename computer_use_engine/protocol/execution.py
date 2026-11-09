# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Strict wire DTOs for protocol v6 execution; no platform or policy imports."""

import json
import math

from .action_context import decode_action_context
from .captures import capture_metadata, decode_capture, decode_stamp
from .desktop import decode_desktop_observation
from .directory import _application, app_change, decode_directory
from .observation.adapters import validate_driver_payload
from .primitives import required_item
from .schema import array, boolean, enum, identity, number, obj, string, union, unknown
from .text import decode_text_page

knowledge = enum("yes", "no", "unknown")
nullable_id = identity.nullable()
actuator = union(
    obj(dict(node=identity, relation=enum("containedEditor", "controlPart"))),
    obj(dict(node=identity, region=identity, bar=identity, relation=enum("scrollPage"))),
    obj(
        dict(
            node=identity,
            region=identity,
            relation=enum("scrollPointer"),
            contentPath=array(identity, min=2, max=40).optional(),
        )
    ),
)


def validate_actuator_path(selected):
    path = selected.get("contentPath") if selected is not None else None
    if path is not None and (path[0] != selected["node"] or len(set(path)) != len(path)):
        raise ValueError("protocol: a scroll content path must start at its hit and contain distinct nodes")


binding = obj(
    dict(
        session=identity,
        instance=nullable_id,
        rootWindow=nullable_id,
        surface=nullable_id,
        node=nullable_id,
        capture=nullable_id,
    )
)
application_binding = obj(dict(session=identity, application=identity))
global_target = obj({"session": identity, "global": boolean()})
operation_target = union(binding, application_binding, global_target)
screen_point = obj({"x": number(), "y": number()})


def decode_point_location_request(value):
    """Location is a read-only conversion, not input or target preparation."""
    return obj({"target": binding, "point": screen_point.nullable()}).parse(value)


def decode_observation_preparation_request(value):
    result = obj(dict(operation=identity, target=binding)).parse(value)
    target = result["target"]
    if any(target[key] is None for key in ("instance", "rootWindow", "surface")) or any(
        target[key] is not None for key in ("node", "capture")
    ):
        raise ValueError("Observation preparation requires an exact window scope")
    return result


current_receiver = obj(
    dict(
        state=enum("confirmed", "absent", "unavailable", "conflict"),
        node=nullable_id,
        ancestors=array(identity, max=128),
        detail=string().optional(),
    )
)
context = obj(
    dict(
        foregroundSurface=nullable_id,
        keyboardSurface=nullable_id,
        focusedNode=nullable_id,
        focusAncestors=array(identity, max=128),
        editor=nullable_id,
        editorOwner=nullable_id,
        selectionKnown=knowledge,
        geometryCurrent=knowledge,
        windowRoute=knowledge,
        pointerRoute=knowledge.optional(),
        pointerHit=nullable_id.optional(),
        currentReceiver=current_receiver.optional(),
    )
)
intent_kinds = ("click", "focus", "insert", "replace", "paste", "key", "openMenu", "action", "scroll", "drag", "launch")
intents = enum(*intent_kinds)
pending_conditions = ("receiver", "selection", "geometry", "foreground", "enabled")
click_offer = obj(
    dict(
        routeFamily=identity,
        interaction=enum("semantic", "pointer"),
        delivery=enum("object_action", "object_selection", "surface_pointer", "desktop_pointer"),
        admission=enum("ready", "preparationRequired", "unavailable", "insufficientEvidence"),
        reason=string().nullable(),
        preparationOptions=array(enum("background", "foreground"), max=2),
        preferenceProfile=enum("default", "physicalRequired"),
    )
)
click_dispatch = enum("notDispatched", "dispatched", "partial", "unknown")
click_receipt = obj(
    dict(
        preparationEvidence=array(
            obj(
                dict(
                    kind=enum(
                        "focusNotification",
                        "windowSelection",
                        "activation",
                        "pointerPreparation",
                        "keyboardPreparation",
                    ),
                    scope=binding,
                    dispatch=click_dispatch,
                    effect=enum("contextOnly", "contentPossible", "unknown"),
                    continuation=enum("sameCandidate", "withinScope", "observe"),
                    observations=array(string(), max=128),
                    wait=enum("notNeeded", "satisfied", "exhausted", "unavailable"),
                )
            ),
            max=128,
        ),
        transportEvidence=obj(
            dict(
                kind=enum("none", "nativeAction", "nativeSelection", "posted", "inserted", "xtest"),
                callEntered=boolean(),
                callReturned=boolean(),
                submitted=number(integer=True, min=0).nullable(),
                expected=number(integer=True, min=0).nullable(),
            )
        ),
        continuation=obj(
            dict(
                kind=enum("sameCandidateRefresh", "reselectWithinScope", "observe", "stop"),
                target=binding,
                routeFamily=identity,
            )
        ),
    )
)
primitive = obj(
    dict(
        id=identity,
        unit=enum(
            "native_action",
            "native_selection",
            "native_text_edit",
            "native_value_replace",
            "key_sequence",
            "clipboard_paste",
            "pointer_sequence",
            "native_scroll",
            "prepare_receiver",
            "launch_application",
        ),
        channel=enum("object", "editor", "keyboard", "pointer"),
        supported=knowledge,
        readiness=knowledge,
        pending=array(enum(*pending_conditions), max=5),
        backgroundPreparation=knowledge,
        globalInput=knowledge.optional(),
        keyboardEditing=enum("insert", "replace").optional(),
        preparationImpact=obj(
            dict(mouse=knowledge, keyboard=knowledge, activation=knowledge, clipboard=knowledge)
        ).optional(),
        action=obj(dict(name=identity, description=string().optional())).optional(),
        click=click_offer.optional(),
    )
)

cleanup = enum("complete", "failed", "unknown")
input_impact = obj(
    dict(
        coverage=enum("complete", "partial"),
        effects=array(
            obj(dict(
                stage=enum("preparation", "input", "cleanup"),
                device=enum("mouse", "keyboard", "focus", "element", "clipboard", "application"),
                transport=enum(
                    "globalHID", "process", "accessibility", "activation", "windowServer", "clipboard", "launch"
                ),
                mechanism=string(min=1, max=512),
                state=enum("invoked", "possible", "notInvoked"),
            )),
            max=128,
        ),
        foregroundChanged=knowledge,
        clipboardRestoration=enum("notUsed", "restored", "ownershipLost", "failed", "unknown"),
        foregroundRestoration=enum(
            "notUsed", "notNeeded", "restored", "ownershipLost", "retained", "unavailable", "failed", "unknown"
        ).optional(),
        omittedEffects=number(integer=True, min=0).optional(),
    )
)
# These are wire-contract meanings, not a fallback policy. Both decoded driver
# declarations and in-process NativePort implementations obey the same rules.
unit_contracts = {
    "native_action": ({"object"}, {"click", "openMenu", "action"}),
    "native_selection": ({"object"}, {"click"}),
    "native_text_edit": ({"object", "editor"}, {"insert", "replace"}),
    # Whole-property replacement does not promise ordinary editor callbacks.
    "native_value_replace": ({"object"}, {"replace"}),
    "key_sequence": ({"keyboard"}, {"insert", "replace", "key"}),
    "clipboard_paste": ({"keyboard"}, {"paste"}),
    "pointer_sequence": ({"pointer"}, {"click", "openMenu", "scroll", "drag"}),
    "native_scroll": ({"object"}, {"scroll"}),
    # A preparation-only declaration can establish context before an editor
    # exposes its editing capability. It never authorizes content dispatch.
    "prepare_receiver": ({"object", "editor", "keyboard", "pointer"}, {"focus", "insert", "replace", "paste"}),
    "launch_application": ({"object"}, {"launch"}),
}

_intent = obj(dict(kind=enum("read", *intent_kinds), parameters=unknown(), globalInput=boolean().optional()))
_begin = obj(
    dict(
        mode=enum("read", "write"),
        target=operation_target,
        intent=_intent,
        clickContract=enum("v1", "v2").optional(),
    )
)
_unit_command = obj(
    dict(
        kind=enum(*unit_contracts, "global_input"), primitiveId=identity, target=operation_target, foreground=boolean()
    )
)
_capture_request = obj(
    dict(
        operation=identity,
        target=binding,
        kind=enum("window", "desktop"),
        maxDimension=number(integer=True, min=1, max=4096),
        retainedCaptures=array(identity).optional(),
    )
)


def decode_capture_request(value):
    result = _capture_request.parse(value)
    target = result["target"]
    if target["node"] is not None or target["capture"] is not None:
        raise ValueError("protocol: capture needs a read scope, not an element or old image")
    window = result["kind"] == "window"
    if any((target[key] is not None) != window for key in ("instance", "rootWindow", "surface")):
        raise ValueError("protocol: capture kind and target scope disagree")
    return result


def decode_begin(value):
    """Bind the original payload once, before observing or preparing a route."""
    result = _begin.parse(value)
    intent, mode = result["intent"], result["mode"]
    if "global" in result["target"]:
        explicit_global_write = (
            result["target"]["global"] is True and mode == "write" and intent.get("globalInput") is True
        )
        if (
            not explicit_global_write
            or intent["kind"] not in ("click", "scroll", "drag", "insert", "paste", "key")
            or "clickContract" in result
        ):
            raise ValueError("protocol: global scope requires an explicit primitive input")
    if intent.get("globalInput") and result["target"].get("global") is not True:
        raise ValueError("protocol: explicit global input requires the global session scope")
    if "clickContract" in result and (mode != "write" or intent["kind"] != "click"):
        raise ValueError("protocol: click contract requires a write click operation")
    if not isinstance(intent["parameters"], dict):
        raise ValueError("protocol: native intent parameters must be an object")
    try:
        json.dumps(intent["parameters"], allow_nan=False)
    except (TypeError, ValueError) as error:
        raise ValueError("protocol: native intent parameters must be JSON values") from error
    if (mode == "read") != (intent["kind"] == "read"):
        raise ValueError("protocol: native intent and operation mode disagree")
    if intent["kind"] in ("read", "launch", "focus") and intent["parameters"]:
        raise ValueError("protocol: this operation intent has no parameters")
    if ("application" in result["target"]) != (intent["kind"] == "launch"):
        raise ValueError("protocol: an installation target permits only application launch")
    return result


def decode_unit_command(value):
    """Only the selected contract crosses the write boundary.

    InputContext is an observation, never client-supplied dispatch authority.
    Parameters are validated by the chosen native unit before preparation.
    """
    result = _unit_command.parse(value)
    if "global" in result["target"] or result["kind"] == "global_input":
        direct_global_input = result["target"].get("global") is True and result["kind"] == "global_input"
        if not direct_global_input or result["primitiveId"] != "global" or result["foreground"]:
            raise ValueError("protocol: global input has no object selection or foreground preparation")
        return result
    installation = "application" in result["target"]
    if installation != (result["kind"] == "launch_application"):
        raise ValueError("protocol: an installation target permits only application launch")
    if installation and result["foreground"]:
        raise ValueError("protocol: application launch has no activation or arbitrary parameters")
    return result


# Request shapes are checked as well as responses. In particular an empty
# constraint list is data, not an omitted optional field in one adapter.
receiver = union(
    obj(dict(relation=enum("virtualFocus"), popup=identity, via=enum("owns", "linked"))),
    obj(dict(relation=enum("containedFocus", "ancestorFocus"), node=identity)),
)
selection_basis = array(
    obj(
        dict(
            observed=identity,
            node=identity,
            fields=array(identity, max=32),
            actions=boolean(),
            relations=array(identity, max=16),
        )
    ),
    min=1,
    max=512,
)


def decode_selection_basis(value):
    result = selection_basis.parse(value)
    seen = set()
    for entry in result:
        key = entry["observed"], entry["node"]
        if key in seen or not (entry["fields"] or entry["actions"] or entry["relations"]):
            raise ValueError("protocol: duplicate or empty selection dependency")
        seen.add(key)
        if any(len(entry[k]) != len(set(entry[k])) for k in ("fields", "relations")):
            raise ValueError("protocol: duplicate selection dependency property")
    return result


_inspect_queries = {
    "launch": obj(dict(kind=enum("launch"))),
    "applications": obj(
        {
            "kind": enum("applications"), "path": identity.optional(), "installation": identity.optional(),
            "includeEnded": boolean().optional(), "windowTitle": identity.optional(),
        }
    ),
    "windowCatalogue": obj(dict(kind=enum("windowCatalogue"), appName=identity)),
    "surfaceBatch": obj(
        dict(kind=enum("surfaceBatch"), applications=array(identity, min=1, max=64), profile=enum("visual").optional())
    ),
    "surfaces": obj(
        dict(
            kind=enum("surfaces"),
            application=identity,
            includeAccessibility=boolean().optional(),
            profile=enum("visual").optional(),
            rootWindow=identity.optional(),
        )
    ),
    "roots": obj(dict(kind=enum("roots"), application=identity, applicationContent=boolean().optional())),
    "appChanges": obj({"kind": enum("appChanges"), "application": identity}),
    "waitAppChange": obj(
        {
            "kind": enum("waitAppChange"),
            "application": identity,
            "after": identity,
            "timeoutMs": number(integer=True, min=1, max=400),
        }
    ),
    "inputContext": obj(
        dict(
            kind=enum("inputContext"),
            within=array(identity, max=16),
            actuator=actuator.optional(),
            receiver=receiver.optional(),
            basis=selection_basis.optional(),
            currentReceiver=boolean().optional(),
        )
    ),
    "nodes": obj(
        dict(
            kind=enum("nodes"),
            fields=array(identity, min=1, max=128),
            advertisedFields=array(identity, min=1, max=128).optional(),
            textContent=boolean().optional(),
            focusedElement=boolean().optional(),
            shareAttributeCatalogues=enum(True).optional(),
            actionDetail=enum("none", "names", "full").optional(),
            followRelations=array(identity, min=1, max=16).optional(),
            focusedFields=array(enum("Text.CaretRange"), min=1, max=1).optional(),
            primaryFields=array(enum("Text.LineRanges"), min=1, max=1).optional(),
            strictWindow=boolean().optional(),
            maxNodes=number(integer=True, min=1, max=2000),
            maxDepth=number(integer=True, min=0, max=2000),
            childrenPage=obj(dict(cursor=nullable_id, limit=number(integer=True, min=1, max=1999))).optional(),
            relationPage=obj(
                dict(property=identity, cursor=nullable_id, limit=number(integer=True, min=1, max=1999))
            ).optional(),
        )
    ),
    "relation": obj(
        dict(kind=enum("relation"), property=identity, cursor=nullable_id, limit=number(integer=True, min=1, max=2000))
    ),
    "capturedText": obj(
        dict(
            kind=enum("capturedText"),
            textRef=identity,
            offset=number(integer=True, min=0, max=9_007_199_254_740_991),
            length=number(integer=True, min=2, max=16_384),
        )
    ),
    "readChange": obj(dict(kind=enum("readChange"), after=identity, timeoutMs=number(integer=True, min=1, max=400))),
}


def decode_inspect_query(value):
    if not isinstance(value, dict) or not isinstance(value.get("kind"), str) or value["kind"] not in _inspect_queries:
        raise ValueError("protocol: unknown native inspection query")
    result = _inspect_queries[value["kind"]].parse(value)
    exact_application_path = result["kind"] == "applications" and "path" in result
    if exact_application_path and ("installation" in result or not result["path"].startswith("/")):
        raise ValueError("protocol: invalid exact application query")
    if "includeEnded" in result:
        include_ended = result["includeEnded"] is True
        if not include_ended or "path" in result or "installation" in result:
            raise ValueError("protocol: ended lifetimes require a full application query")
    if "windowTitle" in result and any(key in result for key in ("path", "installation", "includeEnded")):
        raise ValueError("protocol: window caption and application scope queries cannot be combined")
    if "profile" in result and "includeAccessibility" in result:
        raise ValueError("protocol: surface profile and legacy accessibility flag cannot be combined")
    if "currentReceiver" in result and (
        result["currentReceiver"] is not True or any(key in result for key in ("actuator", "receiver", "basis"))
    ):
        raise ValueError("protocol: current receiver discovery requires an unselected scope")
    if "advertisedFields" in result and (
        len(set(result["advertisedFields"])) != len(result["advertisedFields"])
        or not set(result["advertisedFields"]) <= set(result["fields"])
    ):
        raise ValueError("protocol: advertised fields must be a distinct subset of the requested fields")
    if "focusedFields" in result and not set(result["focusedFields"]) <= set(result["fields"]):
        raise ValueError("protocol: focused fields must belong to the requested fields")
    if "primaryFields" in result and not set(result["primaryFields"]) <= set(result["fields"]):
        raise ValueError("protocol: primary fields must belong to the requested fields")
    if "childrenPage" in result and "relationPage" in result:
        raise ValueError("protocol: exactly one root relation page is required")
    page = result.get("relationPage", result.get("childrenPage"))
    if page is not None and (result["maxDepth"] < 1 or page["limit"] >= result["maxNodes"]):
        raise ValueError("protocol: a relation page needs depth and space for its root and targets")
    if "basis" in result:
        decode_selection_basis(result["basis"])
        if "actuator" not in result and "receiver" not in result:
            raise ValueError("protocol: selection dependencies require a selected actuator or receiver")
    selected = result.get("actuator")
    validate_actuator_path(selected)
    invalid_result = (
        True
        if "receiver" in result
        and ("basis" not in result or selected is not None and selected["relation"] != "containedEditor")
        else False
    )
    if invalid_result:
        raise ValueError("protocol: a related receiver requires selection dependencies")
    if selected is not None and selected["relation"] == "scrollPointer" and not result.get("basis"):
        raise ValueError("protocol: an element scroll pointer requires selection dependencies")
    if (
        selected is not None
        and selected["relation"] == "scrollPage"
        and selected["node"] in (selected["region"], selected["bar"])
    ):
        raise ValueError("protocol: a page button must be distinct from its region and bar")
    for key in ("within", "fields", "followRelations", "applications"):
        if key in result and len(set(result[key])) != len(result[key]):
            raise ValueError("protocol: duplicate native query " + key)
    return result


def validate_primitive_contract(unit, channel, supported, readiness, pending, action):
    channels, _ = unit_contracts[unit]
    if supported not in ("yes", "no", "unknown") or readiness not in ("yes", "no", "unknown"):
        raise ValueError("protocol: invalid native support or readiness evidence")
    if channel not in channels:
        raise ValueError("protocol: native unit cannot implement the declared channel")
    if len(set(pending)) != len(pending) or not set(pending) <= set(pending_conditions):
        raise ValueError("protocol: invalid or duplicate native readiness condition")
    invalid_pending = (
        True if readiness == "yes" and pending or supported == "yes" and readiness != "yes" and not pending else False
    )
    if invalid_pending:
        raise ValueError("protocol: native readiness and pending conditions disagree")
    if (unit == "native_action") != (action is not None):
        raise ValueError("protocol: inconsistent native action declaration")
    if unit == "launch_application" and pending:
        raise ValueError("protocol: application launch cannot acquire UI input requirements")


def _validate_primitive(p):
    validate_primitive_contract(p["unit"], p["channel"], p["supported"], p["readiness"], p["pending"], p.get("action"))
    if "keyboardEditing" in p:
        keyboard_primitive = p["unit"] == "key_sequence" and p["channel"] == "keyboard"
        if not keyboard_primitive or p.get("globalInput") != "no":
            raise ValueError("protocol: keyboard editing evidence requires an addressed keyboard primitive")
    if p["unit"] == "native_selection" and "click" not in p:
        raise ValueError("protocol: native selection requires an integrated click offer")
    if "click" in p:
        validate_click_offer(p["click"], p["unit"])


def validate_click_offer(value, unit):
    click_offer.parse(value)
    semantic = value["interaction"] == "semantic"
    expected = {
        "object_action": "native_action",
        "object_selection": "native_selection",
        "surface_pointer": "pointer_sequence",
        "desktop_pointer": "pointer_sequence",
    }
    if semantic != (value["delivery"] in ("object_action", "object_selection")) or unit != required_item(
        expected, value["delivery"]
    ):
        raise ValueError("protocol: click delivery and native unit disagree")
    options = value["preparationOptions"]
    if len(set(options)) != len(options) or value["admission"] == "preparationRequired" and not options:
        raise ValueError("protocol: invalid click preparation options")


def validate_click_receipt(value, target, dispatch, cleanup_result):
    click_receipt.parse(value)
    continuation, transport = value["continuation"], value["transportEvidence"]
    if continuation["target"] != target or any(p["scope"] != target for p in value["preparationEvidence"]):
        raise ValueError("protocol: click evidence changed the original target")
    if transport["callReturned"] and not transport["callEntered"]:
        raise ValueError("protocol: a transport cannot return before entering")
    if dispatch in ("dispatched", "partial") and not transport["callEntered"]:
        raise ValueError("protocol: submitted click requires transport entry evidence")
    if dispatch == "notDispatched" and transport["callEntered"] and not transport["callReturned"]:
        raise ValueError("protocol: zero dispatch conflicts with an unresolved input call")
    if (
        dispatch == "notDispatched"
        and transport["callEntered"]
        and not (transport["kind"] == "inserted" and transport["submitted"] == 0)
    ):
        raise ValueError(
            "protocol: an entered input call requires explicit zero-insertion evidence to remain notDispatched"
        )
    invalid_transport = (
        True
        if transport["kind"] == "none"
        and (
            transport["callEntered"]
            or transport["callReturned"]
            or transport["submitted"] is not None
            or transport["expected"] is not None
        )
        else False
    )
    if invalid_transport:
        raise ValueError("protocol: absent transport cannot report submission")
    if (
        transport["submitted"] is not None
        and transport["expected"] is not None
        and transport["submitted"] > transport["expected"]
    ):
        raise ValueError("protocol: click submissions exceed the frozen gesture")
    if continuation["kind"] in ("sameCandidateRefresh", "reselectWithinScope"):
        if dispatch != "notDispatched" or cleanup_result != "complete":
            raise ValueError("protocol: uncertain click cannot continue")
    if dispatch == "notDispatched" and (transport["submitted"] or 0) > 0:
        raise ValueError("protocol: submitted request input cannot be notDispatched")


_schemas = {
    "locatePoint": obj({"point": screen_point}),
    "begin": obj(
        dict(
            operation=identity,
            target=operation_target,
            remainingMs=number(integer=True, min=0, max=120000),
            clickContract=enum("v1", "v2").optional(),
            appChangeBaseline=app_change.nullable().optional(),
        )
    ),
    "inspect": obj(
        dict(
            kind=enum("inputContext"),
            binding=binding,
            observed=identity,
            targetLabel=identity,
            identity=knowledge,
            withinScope=knowledge,
            enabled=knowledge,
            blockedBy=array(identity, max=128),
            primitives=array(primitive, max=256),
            input=context,
            nativeRole=nullable_id.optional(),
            actionsComplete=boolean().optional(),
            applicationMenu=boolean().optional(),
            unavailableReason=identity.optional(),
            within=array(identity, max=16).optional(),
            actuator=actuator.nullable().optional(),
            basis=selection_basis.optional(),
            receiver=receiver.optional(),
        )
    ),
    "execute": obj(
        dict(
            target=operation_target,
            dispatch=enum("notDispatched", "dispatched", "partial", "unknown"),
            detail=identity,
            preparation=array(identity, max=128),
            confirmation=enum("notRequested", "confirmed", "contradicted", "unavailable"),
            cleanup=cleanup,
            errorCode=nullable_id,
            retryReason=enum("routeUnavailable", "preparationUnavailable", "preconditionChanged").nullable(),
            context=unknown(),
            application=_application.optional(),
            click=click_receipt.optional(),
            impact=input_impact.optional(),
            appChangeInputBaseline=app_change.nullable().optional(),
        )
    ),
    "finish": obj(dict(cleanup=cleanup)),
    "prepareObservation": obj(
        dict(
            kind=enum("observationPreparation"),
            binding=binding,
            activation=enum("notRequested", "requested"),
            raiseStatus=enum("notRequested", "requested", "unsupported", "unknown"),
            foreground=boolean(),
            redraw=enum("unknown"),
        )
    ),
    "capture": obj(
        dict(
            kind=enum("capture"),
            binding=binding,
            observed=unknown(),
            image=unknown(),
            windowScopeChanged=boolean().optional(),
            coordinates=obj(
                dict(
                    space=enum("window-pixels"),
                    origin=enum("top-left"),
                    nativeSpace=enum("screen-points", "screen-pixels"),
                    toNative=array(number(), min=6, max=6),
                )
            ),
        )
    ),
}

_launch_facts = obj(
    dict(
        kind=enum("launch"),
        binding=application_binding,
        observed=identity,
        targetLabel=identity,
        primitives=array(primitive, min=1, max=1),
    )
)
_captured_text = obj(dict(kind=enum("capturedText"), session=identity, textRef=identity, page=unknown()))
_app_changes = obj(
    {
        "kind": enum("appChanges"),
        "session": identity,
        "application": identity,
        "incarnation": nullable_id,
        "token": nullable_id,
        "available": boolean(),
        "notifications": array(identity, max=32),
    }
)
_wait_app_change = obj(
    {
        "kind": enum("waitAppChange"),
        "session": identity,
        "application": identity,
        "incarnation": nullable_id,
        "token": nullable_id,
        "reason": enum("event", "timeout", "unavailable"),
        "events": array(identity, max=32),
        "elapsedMs": number(min=0).optional(),
    }
)
_read_change = obj(
    dict(
        kind=enum("readChange"),
        session=identity,
        surface=identity,
        after=identity,
        reason=enum("event", "timeout", "unavailable"),
        events=array(enum("focus", "property", "structure", "text", "selection"), max=5),
        elapsedMs=number(integer=True, min=0, max=120_000),
    )
)
_desktop_capture = obj(
    dict(
        kind=enum("desktopCapture"),
        binding=binding,
        desktopRef=identity,
        scope=identity,
        observed=unknown(),
        observation=unknown(),
        screenshot=unknown(),
    )
)


def decode_execution_result(method, value, *, retain_observation=False):
    if method == "inspect" and isinstance(value, dict) and value.get("kind") == "appChanges":
        result = _app_changes.parse(value)
        if result["available"] and any(result[key] is None for key in ("incarnation", "token")):
            raise ValueError("protocol: available application changes require an observer incarnation and token")
        return result
    if method == "inspect" and isinstance(value, dict) and value.get("kind") == "waitAppChange":
        result = _wait_app_change.parse(value)
        if len(set(result["events"])) != len(result["events"]) or (result["reason"] == "event") != bool(
            result["events"]
        ):
            raise ValueError("protocol: application event hints contradict their wake reason")
        if result["reason"] == "event" and any(result[key] is None for key in ("incarnation", "token")):
            raise ValueError("protocol: application events require an observer incarnation and token")
        return result
    if method == "inspect" and isinstance(value, dict) and value.get("kind") == "readChange":
        result = _read_change.parse(value)
        if len(set(result["events"])) != len(result["events"]) or (result["reason"] == "event") != bool(
            result["events"]
        ):
            raise ValueError("protocol: wait event hints contradict their wake reason")
        return result
    if method == "capture" and isinstance(value, dict) and value.get("kind") == "desktopCapture":
        result = _desktop_capture.parse(value)
        if any(result["binding"][key] is not None for key in ("instance", "rootWindow", "surface", "node", "capture")):
            raise ValueError("protocol: desktop capture requires a session read scope")
        decoded = decode_desktop_observation(
            {key: result[key] for key in ("desktopRef", "scope", "observed", "observation", "screenshot")}
        )
        if decoded["screenshot"] is not None and capture_metadata(decoded["screenshot"])["id"] != decoded["desktopRef"]:
            raise ValueError("protocol: desktop image and coordinate mapping have different identities")
        return {**result, **decoded}
    if method == "inspect" and isinstance(value, dict) and value.get("kind") == "capturedText":
        result = _captured_text.parse(value)
        result["page"] = decode_text_page(result["page"])
        return result
    if method == "inspect" and isinstance(value, dict) and value.get("kind") == "launch":
        result = _launch_facts.parse(value)
        p = result["primitives"][0]
        _validate_primitive(p)
        if p["unit"] != "launch_application":
            raise ValueError("protocol: inconsistent application launch contract")
        return result
    if (
        method == "inspect"
        and isinstance(value, dict)
        and value.get("kind") in ("applications", "surfaces", "roots", "surfaceBatch", "windowCatalogue")
    ):
        return decode_directory(value)
    if method == "inspect" and isinstance(value, dict) and value.get("kind") == "observation":
        if retain_observation:
            from .observation.adapters.wire import DecodedDriverObservation

            return DecodedDriverObservation(value)
        validate_driver_payload(value)
        return value
    result = _schemas[method].parse(value)
    bound = result.get("target") or result.get("binding")
    if bound is not None and "global" in bound and bound["global"] is not True:
        raise ValueError("protocol: invalid global input scope")
    invalid_bound = (
        True
        if (
            bound is not None
            and "application" not in bound
            and "global" not in bound
            and bound["surface"] is None
            and any(bound[key] is not None for key in ("instance", "rootWindow", "node"))
        )
        else False
    )
    if invalid_bound:
        raise ValueError("protocol: unbound discovery cannot contain window or element identities")
    baseline = result.get("appChangeBaseline") if method == "begin" else result.get("appChangeInputBaseline")
    if baseline is not None:
        session_changed = baseline["session"] != bound["session"]
        if session_changed or (
            "application" in bound and baseline["application"] != bound["application"]
        ):
            raise ValueError("protocol: application change baseline changed the operation scope")
    if method == "execute" and "application" in result:
        if "application" not in bound or not result["application"]["isRunning"]:
            raise ValueError("protocol: only an installation operation can return a running application")
    if method == "capture":
        if any(bound[key] is None for key in ("instance", "rootWindow", "surface")) or any(
            bound[key] is not None for key in ("node", "capture")
        ):
            raise ValueError("protocol: window capture requires a window binding, not an element or old image")
        result["observed"] = decode_stamp(result["observed"])
        result["image"] = decode_capture(result["image"], "window", bound["surface"], result["observed"])
        a, b, c, d, _, _ = result["coordinates"]["toNative"]
        determinant = a * d - b * c
        if not math.isfinite(determinant) or determinant == 0:
            raise ValueError("protocol: image coordinate transform is not finite and invertible")
    if method == "inspect":
        current = result["input"].get("currentReceiver")
        if current is not None:
            node, ancestors = current["node"], current["ancestors"]
            if len(set(ancestors)) != len(ancestors) or node in ancestors:
                raise ValueError("protocol: current receiver ancestry must be a distinct path")
            if current["state"] == "confirmed":
                scope = result["binding"]["node"]
                if (
                    node is None
                    or scope is None
                    or not (node == scope and not ancestors or ancestors and ancestors[-1] == scope)
                ):
                    raise ValueError("protocol: confirmed receiver path must reach the requested scope")
            elif node is not None or ancestors:
                raise ValueError("protocol: unconfirmed receiver evidence cannot select a target")
            if result["primitives"]:
                raise ValueError("protocol: current receiver discovery cannot declare input primitives")
        if "basis" in result:
            decode_selection_basis(result["basis"])
            if result.get("actuator") is None and result.get("receiver") is None:
                raise ValueError("protocol: selection dependencies require an actuator or receiver")
        within = result.get("within", [])
        if len(set(within)) != len(within) or result["binding"]["node"] in within:
            raise ValueError("protocol: target ancestry must have distinct proper ancestors")
        selected = result.get("actuator")
        validate_actuator_path(selected)
        if "receiver" in result:
            editor = selected["node"] if selected is not None else bound["node"]
            invalid_editor = (
                True
                if (
                    editor is None
                    or bound["capture"] is not None
                    or not result.get("basis")
                    or (result["receiver"].get("popup") or result["receiver"].get("node")) == editor
                    or selected is not None
                    and selected["relation"] != "containedEditor"
                )
                else False
            )
            if invalid_editor:
                raise ValueError("protocol: invalid receiver relation selection")
        if selected is not None:
            if result["binding"]["node"] is None or result["binding"]["capture"] is not None:
                raise ValueError("protocol: an actuator requires an element target")
            if (
                selected["relation"] in ("containedEditor", "controlPart")
                and selected["node"] == result["binding"]["node"]
            ):
                raise ValueError("protocol: a selected member requires a distinct requested owner")
            if selected["relation"] == "containedEditor" and (
                result["input"]["editor"] != selected["node"]
                or result["input"]["editorOwner"] != result["binding"]["node"]
            ):
                raise ValueError("protocol: the confirmed editor must match the selected actuator and requested owner")
            if selected["relation"] == "scrollPage" and (
                selected["node"] in (selected["region"], selected["bar"])
                or any(p["unit"] != "native_scroll" for p in result["primitives"])
            ):
                raise ValueError("protocol: invalid native scroll actuator contract")
            if selected["relation"] in ("controlPart", "scrollPointer") and any(
                p["unit"] != "pointer_sequence" for p in result["primitives"]
            ):
                raise ValueError("protocol: a selected pointer member only supports its pointer contract")
            if selected["relation"] == "scrollPointer" and not result.get("basis"):
                raise ValueError("protocol: an element scroll pointer requires selection dependencies")
        identifiers = [p["id"] for p in result["primitives"]]
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("protocol: duplicate native primitive id")
        for p in result["primitives"]:
            _validate_primitive(p)
    if method == "execute":
        impact = result.get("impact")
        if impact is not None and impact["coverage"] == "complete" and impact.get("omittedEffects", 0):
            raise ValueError("protocol: complete input impact cannot omit effects")
        if "click" in result:
            validate_click_receipt(result["click"], result["target"], result["dispatch"], result["cleanup"])
        if result["context"] is not None:
            result["context"] = decode_action_context(result["context"])
        if result["retryReason"] is not None and (
            result["dispatch"] != "notDispatched" or result["cleanup"] != "complete"
        ):
            raise ValueError("protocol: uncertain input cannot authorize another route")
    return result
