# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Log how many images, and how many bytes of them, each model request carries.

With --snapshot-keep-last-k 0 nothing evicts screenshots, so a long run can reach
a provider limit on images or request size per call. One line per model call in
agent.log ("[request] images=... image_mb=...") tells such a failure apart from
a run that simply did worse. It runs after MultimodalContextSummarizerRail (85),
so it counts what is actually sent.
"""
from __future__ import annotations

from typing import Callable

from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, AgentRail


class RequestSizeRail(AgentRail):
    priority = 5  # after every rail that edits the context before the call

    def __init__(self, log: Callable[[str], None]) -> None:
        super().__init__()
        self._log = log
        self._calls = 0

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        if ctx.context is None:
            return
        messages = ctx.context.get_messages()
        images, image_bytes = 0, 0
        for msg in messages:
            content = getattr(msg, "content", None)
            if not isinstance(content, list):
                continue
            for block in content:
                if isinstance(block, dict) and block.get("type") == "image_url":
                    images += 1
                    url = (block.get("image_url") or {}).get("url", "")
                    image_bytes += len(url) if isinstance(url, str) else 0
        self._calls += 1
        self._log(f"[request] call={self._calls} messages={len(messages)} images={images} "
                  f"image_mb={image_bytes / 1e6:.1f}")
