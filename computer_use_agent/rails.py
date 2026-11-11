# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Desktop context and failure guidance adapted from agent-core's CUA rails."""

from itertools import pairwise

from openjiuwen.core.single_agent.rail.base import AgentRail, ToolCallInputs


class ComputerUseRail(AgentRail):
    """Retain desktop evidence and guide recovery from actual SDK results."""

    def __init__(self):
        super().__init__()
        self._image_scopes = {}

    async def after_tool_call(self, ctx):
        inputs = ctx.inputs
        if not isinstance(inputs, ToolCallInputs) or inputs.tool_name != "computer_use":
            return
        data = getattr(inputs.tool_result, "data", None)
        if not isinstance(data, dict):
            return
        for item in data.get("multimodal", ()):
            scope = item.get("observation_scope")
            if scope:
                label = f"Image loaded from {item['source']}: {item['source_path']}"
                self._image_scopes[label] = (scope, item["data_url"])

        progress = data.get("execution_progress") or {}
        failure = progress.get("failure") or {}
        identity = (failure["code"], failure["target"]) if failure.get("effect") == "none" else None
        previous = ctx.extra.get("computer_use_failure")
        ctx.extra["computer_use_failure"] = identity
        if progress.get("completion") == "unknown" or failure.get("effect") in ("possible", "partial"):
            note = "Observe the affected state before retrying. This call may already have changed it."
        elif identity is not None and identity == previous and not progress.get("receipts"):
            note = "The same SDK failure recurred for this target. Use the available evidence to change approach."
        else:
            return
        if inputs.tool_msg is not None and isinstance(inputs.tool_msg.content, str):
            inputs.tool_msg.content += "\n\n" + note

    async def before_model_call(self, ctx):
        if ctx.context is None or not self._image_scopes:
            return
        messages = list(ctx.context.get_messages())
        scopes_seen = set()
        retained = {}
        changed = False
        for index in range(len(messages) - 1, 0, -1):
            message = messages[index]
            # DeepAgent puts tool images in a user message after the tool reply.
            if message.role != "user" or messages[index - 1].role != "tool" or not isinstance(message.content, list):
                continue
            blocks = message.content.copy()
            scopes = set()
            for position, (label, image) in enumerate(pairwise(blocks), start=1):
                if not isinstance(label, dict) or label.get("type") != "text":
                    continue
                if not isinstance(image, dict) or image.get("type") != "image_url":
                    continue
                text = label.get("text")
                if not isinstance(text, str):
                    continue
                capture = self._image_scopes.get(text)
                image_url = image.get("image_url")
                if capture is None or not isinstance(image_url, dict) or image_url.get("url") != capture[1]:
                    continue
                scope = capture[0]
                scopes.add(scope)
                if scope in scopes_seen:
                    blocks[position] = {
                        "type": "text",
                        "text": "[Screenshot omitted from history after a newer capture of the same scope.]",
                    }
                else:
                    retained[text] = capture
            # All screenshots in a single tool batch remain available together.
            scopes_seen.update(scopes)
            if blocks != message.content:
                messages[index] = message.model_copy(update={"content": blocks})
                changed = True
        if changed:
            ctx.context.set_messages(messages)
        self._image_scopes = retained
