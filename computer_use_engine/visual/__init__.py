# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Frame-scoped visual references derived from captured image evidence.

This package turns provider evidence (Windows OCR or macOS Vision lines) into
public references that pointer actions can address without an accessibility
identity.
It never replaces accessibility-first lookup and never supplies keyboard input.
"""

from .model import VisualElement, VisualElementInfo, VisualIndex, VisualRef

__all__ = ["VisualElement", "VisualElementInfo", "VisualIndex", "VisualRef"]
