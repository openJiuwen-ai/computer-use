"""Image identity and delivery authority; formatting is supplied by the client."""

import hashlib
import weakref

from computer_use._identity import WeakIdentityMap
from computer_use.protocol.captures import captureMetadata

_sources = WeakIdentityMap()
_byteSources = {}
_deliveryRequired = False
_deliveryEpoch = 0


def _hash(png):
    return hashlib.sha256(png).hexdigest()


def requireImageDelivery():
    global _deliveryRequired
    _deliveryRequired = True


def imageDeliveryRequired():
    return _deliveryRequired


def imageDeliveryEpoch():
    return _deliveryEpoch


def resetImageDelivery():
    global _deliveryEpoch
    _deliveryEpoch += 1


def registerImage(png, source, summarize):
    previous = _sources.get(png)
    if previous is not None and "bytes" in previous:
        _byteSources.pop(id(previous["bytes"]), None)
    _sources.set(png, dict(source=source, hash=_hash(png), metadata=captureMetadata(png)))
    png._bytes_copied = _bytes_copied
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
            if _byteSources.get(key) is reference:
                _byteSources.pop(key, None)

        _byteSources[key] = weakref.ref(original, remove)
        known["bytes"] = value
    return known["bytes"]


def _source(png):
    known = _sources.get(png)
    if known is None:
        original = _byteSources.get(id(png))
        known = _sources.get(original()) if original is not None else None
        if known is None or known.get("bytes") is not png:
            return None
    if known["hash"] != _hash(png):
        _sources.delete(png)
        return None
    return known


def imageMetadata(png):
    known = _source(png)
    return known["metadata"] if known is not None else None


def imageDelivered(png):
    known = _source(png)
    if known is not None and known["source"].get("current", lambda: True)() is not False:
        known["source"].get("delivered", lambda: None)()
