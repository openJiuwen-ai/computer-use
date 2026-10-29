# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import asyncio
import codecs
import logging

from computer_use.json import loads as jsonLoads
from computer_use.protocol.primitives import utf16_length

# Private variables hold mutable session state.
# pylint: disable-next=invalid-name
_handler = None

_LOG = logging.getLogger(__name__)


def set_preview_handler(handler):
    global _handler
    _handler = handler


def get_preview_handler():
    return _handler


def is_preview_event(value):
    return isinstance(value, dict) and (
        value.get("kind") == "reset"
        or value.get("kind") == "frame"
        and isinstance(value.get("name"), str)
        and "png" in value
        and (value["png"] is None or isinstance(value["png"], str))
    )


def latest_preview_sink(send):
    busy, pending = False, None

    async def drain():
        nonlocal busy, pending
        busy = True
        while pending is not None:
            event, pending = pending, None
            try:
                await send(event)
            except Exception:
                _LOG.debug("Preview delivery did not complete.", exc_info=True)
        busy = False

    def sink(event):
        nonlocal pending, busy
        pending = event
        if not busy:
            busy = True
            asyncio.create_task(drain())

    return sink


async def read_previews(stream, receive):
    buffer, discard = "", False
    decoder = codecs.getincrementaldecoder("utf-8")("replace")
    try:
        while True:
            chunk = await stream.read(65536)
            if not chunk:
                break
            for index, part in enumerate(decoder.decode(chunk).split("\n")):
                if index:
                    if not discard and buffer:
                        try:
                            event = jsonLoads(buffer)
                            if is_preview_event(event):
                                receive(event)
                        except Exception:
                            _LOG.debug("Preview delivery did not complete.", exc_info=True)
                    buffer, discard = "", False
                if not discard:
                    buffer += part
                    if utf16_length(buffer) > 8 * 1024 * 1024:
                        buffer, discard = "", True
    except (OSError, ValueError):
        _LOG.debug("Preview delivery did not complete.", exc_info=True)
