"""Capture identities are object-local; never recovered from equal PNG bytes."""

import weakref

from .png import PNG as PNG, decodePNG, pngSize
from .primitives import frozen
from .schema import array, enum, identity, number, obj, string

_interval = dict(startedAt=number(min=0), completedAt=number(min=0))
_stamp = obj(dict(id=identity, **_interval))
_window_state = obj(
    dict(sampledAt=number(min=0), onScreen=enum(True, False).nullable(), ownerForeground=enum(True, False).nullable())
)
_visual_text = obj(
    dict(
        source=enum("windowsOCR"),
        status=enum("complete", "partial", "unavailable"),
        language=string(nonblank=True, max=64).nullable(),
        truncated=enum(True, False),
        lines=array(
            obj(
                dict(
                    text=string(nonblank=True, max=256),
                    rect=obj(
                        dict(
                            x=number(min=0),
                            y=number(min=0),
                            width=number(min=0, exclusiveMin=True),
                            height=number(min=0, exclusiveMin=True),
                        )
                    ),
                )
            ),
            max=64,
        ),
        reason=string(nonblank=True, max=512).optional(),
    )
)
_capture = obj(
    dict(
        id=identity,
        kind=enum("window", "overview", "desktop"),
        scope=identity,
        **_interval,
        width=number(integer=True, min=0, exclusiveMin=True),
        height=number(integer=True, min=0, exclusiveMin=True),
        png=string(),
        stopReasons=array(string(nonblank=True, max=4096), max=16).optional(),
        windowState=_window_state.optional(),
        visualText=_visual_text.optional(),
    )
)
_captures = {}


def decodeStamp(value):
    stamp = _stamp.parse(value)
    if stamp["completedAt"] < stamp["startedAt"]:
        raise ValueError("protocol: reversed observation interval")
    return frozen(stamp)


def decodeCapture(value, kind, scope=None, observed=None):
    metadata = _capture.parse(value)
    base64 = metadata.pop("png")
    if "stopReasons" in metadata:
        metadata["stopReasons"] = tuple(metadata["stopReasons"])
    if metadata["kind"] != kind or (scope is not None and metadata["scope"] != scope):
        raise ValueError("protocol: image belongs to a different scope or kind")
    if metadata["completedAt"] < metadata["startedAt"] or (
        observed is not None
        and (metadata["startedAt"] < observed["startedAt"] or metadata["completedAt"] > observed["completedAt"])
    ):
        raise ValueError("protocol: image capture is outside its observation interval")
    if "windowState" in metadata:
        state = metadata["windowState"]
        if kind != "window" or not metadata["startedAt"] <= state["sampledAt"] <= metadata["completedAt"]:
            raise ValueError("protocol: window state is outside its window capture interval")
        metadata["windowState"] = frozen(state)
    png = decodePNG(base64)
    size = pngSize(png)
    if metadata["width"] != size["width"] or metadata["height"] != size["height"]:
        raise ValueError("protocol: image dimensions do not match PNG")
    if "visualText" in metadata:
        visual = metadata["visualText"]
        if visual["status"] == "unavailable" and (visual["lines"] or visual["truncated"]):
            raise ValueError("protocol: unavailable OCR cannot declare recognized lines")
        if sum(len(line["text"].encode("utf-16-le")) // 2 for line in visual["lines"]) > 8192:
            raise ValueError("protocol: OCR text exceeds the image annotation budget")
        for line in visual["lines"]:
            box = line["rect"]
            if box["x"] + box["width"] > size["width"] + 1e-6 or box["y"] + box["height"] > size["height"] + 1e-6:
                raise ValueError("protocol: OCR rectangle is outside the encoded image")
        metadata["visualText"] = frozen(
            {**visual, "lines": tuple(frozen({**line, "rect": frozen(line["rect"])}) for line in visual["lines"])}
        )
    key = id(png)

    def release(ref):
        item = _captures.get(key)
        if item is not None and item[0] is ref:
            _captures.pop(key, None)

    _captures[key] = (weakref.ref(png, release), frozen(metadata))
    return png


def captureMetadata(png):
    item = _captures.get(id(png))
    return item[1] if item is not None and item[0]() is png else None
