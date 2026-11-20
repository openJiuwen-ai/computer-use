# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Typed native preparation evidence preserves its kind, scope, and uncertainty."""

import pytest
from computer_use.native.contracts import Dispatch
from computer_use.native.transport import decode_click_receipt
from computer_use.protocol.execution import decode_execution_result
from computer_use.protocol.schema import SchemaError

TARGET = {
    "session": "session",
    "instance": "app",
    "rootWindow": "root",
    "surface": "surface",
    "node": "button",
    "capture": None,
}


def execution_result(
    kind="focusNotification", *, dispatch="dispatched", effect="contextOnly", continuation="withinScope"
):
    return {
        "target": dict(TARGET),
        "dispatch": "notDispatched",
        "detail": "Requested click was not sent.",
        "preparation": ["process key-focus notification"],
        "confirmation": "unavailable",
        "cleanup": "complete",
        "errorCode": "input_unavailable",
        "retryReason": None,
        "context": None,
        "click": {
            "preparationEvidence": [
                {
                    "kind": kind,
                    "scope": dict(TARGET),
                    "dispatch": dispatch,
                    "effect": effect,
                    "continuation": continuation,
                    "observations": [],
                    "wait": "notNeeded",
                }
            ],
            "transportEvidence": {
                "kind": "none",
                "callEntered": False,
                "callReturned": False,
                "submitted": None,
                "expected": None,
            },
            "continuation": {"kind": "observe", "target": dict(TARGET), "routeFamily": "macos.ax.action"},
        },
    }


@pytest.mark.parametrize(
    "kind",
    (
        "focusNotification",
        "windowSelection",
        "activation",
        "pointerPreparation",
        "keyboardPreparation",
    ),
)
def test_declared_preparation_kinds_decode_without_changing_evidence(kind):
    raw = execution_result(kind)
    decoded = decode_execution_result("execute", raw)
    assert decoded == raw
    typed = decode_click_receipt(decoded["click"])
    evidence = typed.preparation_evidence[0]
    assert evidence.kind == kind
    assert evidence.dispatch is Dispatch.DISPATCHED
    assert evidence.effect == "contextOnly" and evidence.continuation == "withinScope"
    assert evidence.scope.node == TARGET["node"] and evidence.scope.surface == TARGET["surface"]
    assert not typed.transport_evidence.call_entered


def test_focus_notification_does_not_promote_unknown_in_flight_preparation():
    raw = execution_result(dispatch="unknown", effect="unknown", continuation="observe")
    decoded = decode_execution_result("execute", raw)
    assert decoded["dispatch"] == "notDispatched", "preparation progress is separate from the requested click"
    evidence = decode_click_receipt(decoded["click"]).preparation_evidence[0]
    assert evidence.kind == "focusNotification"
    assert evidence.dispatch is Dispatch.UNKNOWN
    assert evidence.effect == "unknown" and evidence.continuation == "observe"


@pytest.mark.parametrize(
    "kind",
    (
        "focus_notification",
        "focusnotification",
        "FocusNotification",
        "focusNotification ",
        "processActivation",
        "other",
        "",
        None,
        1,
    ),
)
def test_unknown_or_malformed_preparation_kind_is_still_rejected(kind):
    with pytest.raises(SchemaError) as caught:
        decode_execution_result("execute", execution_result(kind))
    assert any(
        issue["code"] == "invalid_value" and issue["path"] == ["click", "preparationEvidence", 0, "kind"]
        for issue in caught.value.issues
    )


def test_preparation_evidence_does_not_allow_unknown_fields():
    raw = execution_result()
    raw["click"]["preparationEvidence"][0]["element"] = "replacement"
    with pytest.raises(SchemaError) as caught:
        decode_execution_result("execute", raw)
    assert any(
        issue["code"] == "unrecognized_keys" and issue["path"] == ["click", "preparationEvidence", 0]
        for issue in caught.value.issues
    )


def test_preparation_evidence_still_belongs_to_the_original_click_target():
    raw = execution_result()
    raw["click"]["preparationEvidence"][0]["scope"]["node"] = "other-button"
    with pytest.raises(ValueError, match="click evidence changed the original target"):
        decode_execution_result("execute", raw)
