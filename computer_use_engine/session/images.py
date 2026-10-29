# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Image identity and delivery authority; formatting is supplied by the client."""

import hashlib
import weakref

from computer_use.identity import WeakIdentityMap
from computer_use.protocol.captures import capture_metadata

_sources = WeakIdentityMap()
_byte_sources = {}
# Private variables hold mutable session state.
# pylint: disable-next=invalid-name
_delivery_required = False
# pylint: disable-next=invalid-name
_delivery_epoch = 0


def _hash(png):
    return hashlib.sha256(png).hexdigest()


def require_image_delivery():
    global _delivery_required
    _delivery_required = True


def image_delivery_required():
    return _delivery_required


def image_delivery_epoch():
    return _delivery_epoch


def reset_image_delivery():
    global _delivery_epoch
    _delivery_epoch += 1


def register_image(png, source, summarize):
    previous = _sources.get(png)
    if previous is not None and "bytes" in previous:
        _byte_sources.pop(id(previous["bytes"]), None)
    _sources.set(png, dict(source=source, hash=_hash(png), metadata=capture_metadata(png)))
    # The pipeline shares retained bindings with their owning service.
    # pylint: disable-next=protected-access
    png._bytes_copied = _bytes_copied
    # pylint: disable-next=protected-access
    png._displaySummary = summarize(png)


def _bytes_copied(original, value):
    known = _sources.get(original)
    if known is None or known["hash"] != _hash(value):
        return value
    # Retain one immutable conversion per live source, not a content-addressed
    # cache. Equal pixels from different captures never share delivery authority.
    if "bytes" not in known:
        key = id(value)

        def remove(reference):
            if _byte_sources.get(key) is reference:
                _byte_sources.pop(key, None)

        _byte_sources[key] = weakref.ref(original, remove)
        known["bytes"] = value
    return known["bytes"]


def _source(png):
    known = _sources.get(png)
    if known is None:
        original = _byte_sources.get(id(png))
        known = _sources.get(original()) if original is not None else None
        if known is None or known.get("bytes") is not png:
            return None
    if known["hash"] != _hash(png):
        _sources.delete(png)
        return None
    return known


def image_metadata(png):
    known = _source(png)
    return known["metadata"] if known is not None else None


def image_delivered(png):
    known = _source(png)
    if known is not None and known["source"].get("current", lambda: True)() is not False:
        known["source"].get("delivered", lambda: None)()
