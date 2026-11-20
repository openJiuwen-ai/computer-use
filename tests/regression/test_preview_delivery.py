# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""The SDK preview sink keeps a single pending frame while sending."""

import asyncio
import logging

from computer_use.native.preview import latest_preview_sink


def test_preview_retains_only_the_latest_pending_frame_when_send_fails(caplog):
    async def run():
        started = asyncio.Event()
        release = asyncio.Event()
        delivered = asyncio.Event()
        frames = []

        async def send(event):
            frames.append(event["name"])
            if event["name"] == "current":
                started.set()
                await release.wait()
                raise RuntimeError("preview send failed")
            delivered.set()

        sink = latest_preview_sink(send)
        sink({"kind": "frame", "name": "current", "png": None})
        await asyncio.wait_for(started.wait(), 2)
        sink({"kind": "frame", "name": "superseded", "png": None})
        sink({"kind": "frame", "name": "latest", "png": None})
        release.set()
        await asyncio.wait_for(delivered.wait(), 2)
        return frames

    with caplog.at_level(logging.DEBUG, logger="computer_use.native.preview"):
        assert asyncio.run(run()) == ["current", "latest"]

    delivery_errors = [record for record in caplog.records if record.name == "computer_use.native.preview"]
    assert len(delivery_errors) == 1
    assert delivery_errors[0].getMessage() == "Preview delivery did not complete."
    assert delivery_errors[0].exc_info[0] is RuntimeError
    assert str(delivery_errors[0].exc_info[1]) == "preview send failed"
