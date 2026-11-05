# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Keep public presentation PNGs tied to their own prepared native capture."""

import hashlib
import weakref

from computer_use.identity import WeakIdentityMap
from computer_use.protocol.png import PNG


class PresentationImages:
    """Weak, identity-only aliases; matching pixels never transfer authority."""

    def __init__(self):
        self._images = WeakIdentityMap()
        self._bytes = {}

    def create(self, prepared):
        """Return the actual output pixels while retaining the original delivery."""
        delivery = prepared[1]
        png = PNG(delivery.png)
        self._images.set(png, {"prepared": prepared, "fingerprint": hashlib.sha256(png).digest()})
        png._bytes_copied = self._bytes_copied  # pylint: disable=protected-access
        width, height = delivery.coordinates.delivered_size
        png._displaySummary = f"PNG presentation {width}x{height}"  # pylint: disable=protected-access
        return png

    def _record(self, png):
        known = self._images.get(png)
        if known is None:
            reference = self._bytes.get(id(png))
            known = self._images.get(reference()) if reference is not None else None
            if known is None or known.get("bytes") is not png:
                return None
        return known if known["fingerprint"] == hashlib.sha256(png).digest() else None

    def prepared(self, png):
        known = self._record(png)
        return known["prepared"] if known is not None else None

    def _bytes_copied(self, original, value):
        known = self._record(original)
        if known is None or known["fingerprint"] != hashlib.sha256(value).digest():
            return value
        if "bytes" not in known:
            key = id(value)

            def remove(reference):
                if self._bytes.get(key) is reference:
                    self._bytes.pop(key, None)

            self._bytes[key] = weakref.ref(original, remove)
            known["bytes"] = value
        return known["bytes"]

    def clear(self):
        self._images.clear()
        self._bytes.clear()
