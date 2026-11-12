# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Describe the source of a retained text field."""

from typing import Any

from computer_use.interaction.read.presentation.formatting import json_string


def text_source_label(source: Any) -> str:
    observed = json_string(source["observed"]["id"]) if source.get("observed") else "unknown"
    target = f"element {source['ref']}" if source.get("ref") is not None else "query result"
    return f"Captured text: {target}; observation={observed}; snapshot, not a live read."
