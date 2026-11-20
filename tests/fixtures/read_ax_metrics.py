# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Validate the native AX counter layout and measured read intervals."""

PUBLIC_AX_METHODS = (
    "attributeValue",
    "multipleAttributeValues",
    "attributeNames",
    "attributeValueCount",
    "attributeValues",
    "actionNames",
    "actionDescription",
    "attributeSettable",
    "parameterizedAttributeNames",
    "parameterizedAttributeValue",
    "elementAtPosition",
    "getPid",
    "setMessagingTimeout",
)

AX_METHODS = (*PUBLIC_AX_METHODS, "nativeWindowID", "actualAccessibilityPid")

AX_LEDGER_WORDS = 3 + 2 * len(AX_METHODS)


def validate_sample(raw):
    before = raw.pop("before")
    after = raw.pop("after")
    if len(before) != AX_LEDGER_WORDS or len(after) != AX_LEDGER_WORDS:
        raise RuntimeError("AX benchmark probe ledger layout mismatch.")
    if before[1] != after[1]:
        raise RuntimeError("Driver PID changed during the measured read.")
    if before[2] != after[2]:
        raise RuntimeError("AX calls overlapped during the measured read; leaf durations cannot be summed.")
    methods = {
        name: {"calls": after[3 + 2 * i] - before[3 + 2 * i], "ns": after[4 + 2 * i] - before[4 + 2 * i]}
        for i, name in enumerate(AX_METHODS)
    }
    if any(method["calls"] < 0 or method["ns"] < 0 for method in methods.values()):
        raise RuntimeError("AX ledger counters decreased.")
    public_leaf_ns = sum(methods[name]["ns"] for name in PUBLIC_AX_METHODS[:-2])
    leaf_ns = public_leaf_ns + methods["nativeWindowID"]["ns"]
    if not raw["trees"] or not 0 < public_leaf_ns <= leaf_ns <= raw["readNs"]:
        raise RuntimeError("Invalid sample: missing tree or AX time outside the measured read interval.")
    external_ns = raw["readNs"] - leaf_ns
    public_external_ns = raw["readNs"] - public_leaf_ns
    return {
        **raw,
        "driverPid": before[1],
        "leafAxNs": leaf_ns,
        "externalNs": external_ns,
        "externalRatio": external_ns / raw["readNs"],
        "publicLeafAxNs": public_leaf_ns,
        "publicExternalNs": public_external_ns,
        "publicExternalRatio": public_external_ns / raw["readNs"],
        "axMethods": methods,
    }
