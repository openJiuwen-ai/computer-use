# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Normalize complete read-only AX text ranges independently from AXValue."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Mapping

if TYPE_CHECKING:
    from .normalize import NodeBuilder


def captured_content(
    node: NodeBuilder, name: str, arguments: tuple[Any, ...], value: Any, read: Mapping[str, Any]
) -> bool:
    """Attach one stable, complete UTF-16 capture while preserving its native value.

    Args:
        node: Native node builder whose ordinary fields were already interpreted.
        name: Exact method selector used for source provenance and read gaps.
        arguments: Captured parameterized attribute arguments.
        value: Actual AXStringForRange result.
        read: Getter status and character-count confirmation from the driver.

    Returns:
        Whether the result supplies a complete independent text-content channel.
    """
    details = read.get("details", {})
    count = details.get("character_count_before")
    requested = arguments[0] if len(arguments) == 1 and isinstance(arguments[0], dict) else {}
    subrole = node.values.get("AXSubrole")
    subrole_status = node.row["fieldStatus"].get("AXSubrole", {}).get("state")
    if (
        node.backend != "macos"
        or node.values.get("AXValue") != ""
        or node.groups.get("states", {}).get("protected") is True
        or subrole == "AXSecureTextField"
        or not (isinstance(subrole, str) or subrole_status in ("absent", "unsupported"))
        or any(field in node.row["fieldStatus"] for field in ("AXValue", "AXNumberOfCharacters", name))
        or read.get("complete") is not True
        or read.get("issues")
        or read.get("api") != "AXUIElementCopyParameterizedAttributeValue"
        or type(count) is not int
        or not 0 < count <= 4096
        or set(requested) != {"location", "length"}
        or type(requested.get("location")) is not int
        or requested["location"] != 0
        or type(requested.get("length")) is not int
        or requested["length"] != count
        or not isinstance(value, str)
        or len(value.encode("utf-16-le", errors="surrogatepass")) // 2 != count
        or type(details.get("character_count_after")) is not int
        or details["character_count_after"] != count
        or any(
            type(details.get(field)) is not int or details[field] != 0
            for field in ("character_count_before_status", "character_count_after_status", "native_status")
        )
    ):
        return False
    info = node.text_info()
    info.pop("content_field", None)
    info["character_count"] = count
    node.put(name, ("text", "content"), value)
    return True
