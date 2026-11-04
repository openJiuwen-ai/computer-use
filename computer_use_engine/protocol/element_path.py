# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from .primitives import frozen, trim, utf16_length
from .schema import role


def element_path(value):
    if not isinstance(value, list) or not 1 <= len(value) <= 16:
        raise ValueError("find expects a path of 1–16 name strings or { role?, name?, identifier? } segments")
    result = []
    for segment in value:
        item = {"name": segment} if isinstance(segment, str) else segment
        invalid = (
            not isinstance(item, dict)
            or not item
            or any(k not in ("role", "name", "identifier") for k in item)
            or any(not isinstance(text, str) or not trim(text) or utf16_length(text) > 256 for text in item.values())
        )
        if not invalid and "role" in item:
            try:
                # Rendered role words and camelCase are the same selector
                # vocabulary. Normalize once at the public argument boundary;
                # native queries continue to compare exact canonical roles.
                words = item["role"].split(" ")
                normalized = words[0] + "".join(word[:1].upper() + word[1:] for word in words[1:])
                if any(not word for word in words):
                    raise ValueError("empty role word")
                role.parse(normalized)
                item = {**item, "role": normalized}
            except ValueError:
                raise ValueError(
                    f"Invalid role {item['role']!r}; use a role such as 'menu item' or 'menuItem'"
                ) from None
        if invalid:
            raise ValueError(
                (
                    "find segments use nonempty name/identifier and a role (for example "
                    "button, textField or 'text field'), up to 256 characters "
                    "each"
                )
            )
        result.append(frozen(item))
    return tuple(result)
