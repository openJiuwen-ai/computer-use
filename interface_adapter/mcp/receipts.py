# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Structured action-receipt evidence for tool-result metadata."""

from collections.abc import Mapping


def receipt_summary(receipt):
    """Trimmed identity fields of one action receipt, safe for text and metadata."""
    return {
        "status": receipt["status"],
        "target": receipt["target"][:256],
        "intent": receipt.get("intent"),
        "detail": receipt["detail"][:400],
    }


def plain(value):
    """Thaw FrozenRecord/Mapping evidence into JSON-safe plain data."""
    if isinstance(value, Mapping):
        return {key: plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [plain(item) for item in value]
    return value


def receipt_evidence(receipt):
    """Structured receipt for tool-result metadata: identity fields plus impact diagnostics."""
    evidence = receipt_summary(receipt)
    evidence["impact"] = plain(receipt.get("impact"))
    return evidence
