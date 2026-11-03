# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Encode SDK result evidence without embedding screenshot bytes in JSON.

Public observations are mappings: retain their captured text and preparation
alongside image identity and dimensions. This materializes retained text only;
it does not read the desktop again or mark an image delivered to a model.
"""

import hashlib
import io
import json
from collections.abc import Mapping

from PIL import Image


async def captured_interface_metadata(observation):
    """Read one observation's retained window catalogue without another UI read."""
    prefix = "Captured interface metadata: "
    content, cursor = "", None
    for _ in range(16):
        page = await observation.read_details(cursor=cursor, length=16384)
        content += page.text
        if prefix in content:
            try:
                value, _ = json.JSONDecoder().raw_decode(content.split(prefix, 1)[1])
            except json.JSONDecodeError:
                pass
            else:
                return value
        cursor = page.next
        if cursor is None:
            break
    raise RuntimeError("No complete captured interface metadata within the bounded detail budget; no input sent")


def encode_artifact(value):
    if isinstance(value, Mapping):
        return dict(value)
    if callable(getattr(value, "read_details", None)):
        # Consume the public Observation interface without a native read.
        return {"text": value.text, "preparation": value.preparation, "screenshot": value.screenshot}
    if isinstance(value, (bytes, bytearray, memoryview)):
        payload = bytes(value)
        with Image.open(io.BytesIO(payload)) as image:
            return {
                "sha256": hashlib.sha256(payload).hexdigest(),
                "byte_length": len(payload),
                "width": image.width,
                "height": image.height,
                "format": image.format,
            }
    raise TypeError(f"Unsupported artifact value: {type(value).__name__}")
