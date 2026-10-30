# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Action receipts share text capacity while retaining image boundaries."""

import re
import unittest

from computer_use.client.host import (
    set_action_handler,
    set_diagnostic_handler,
    set_execution_guard,
    set_observation_handler,
    set_preview_handler,
    set_text_page_handler,
)
from computer_use.session import images as images_state
from computer_use_mcp.runtime import Runtime


class ActionReceiptTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.previous_delivery = images_state._delivery_required
        self.events = []
        self.runtime = Runtime(self.events.append)
        self.runtime.task_namespace.update(
            receipts=[dict(target="Fixture", status="dispatched", detail="Outcome unconfirmed.") for _ in range(150)],
            emit_receipt=self.runtime.action,
            emit_image=lambda: self.runtime.emit(
                dict(
                    type="image",
                    mimeType="image/png",
                    data="iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j7l0AAAAASUVORK5CYII=",
                )
            ),
        )

    async def asyncTearDown(self):
        await self.runtime.close()
        for setter in (
            set_action_handler,
            set_diagnostic_handler,
            set_execution_guard,
            set_observation_handler,
            set_preview_handler,
            set_text_page_handler,
        ):
            setter(None)
        images_state._delivery_required = self.previous_delivery

    async def execute(self, code):
        return await self.runtime.execute(
            dict(
                code=code,
                executionId=1,
                history=dict(resetText=False, resetImages=False, incremental=True),
            )
        )

    async def test_many_receipts_fit_without_losing_actions_or_stopping_the_suffix(self):
        result = await self.execute("for receipt in receipts:\n    emit_receipt(receipt)\nprint('suffix reached')")
        self.assertFalse(result.get("isError"), result)
        self.assertEqual(len(result["content"]), 1)
        text = result["content"][0]["text"]
        self.assertEqual(re.findall(r"Action \[dispatched\]: #(\d+) ", text), [str(i) for i in range(1, 151)])
        self.assertTrue(text.endswith("suffix reached"))

    async def test_image_separates_two_groups_of_merged_action_text(self):
        result = await self.execute(
            "for receipt in receipts[:2]:\n    emit_receipt(receipt)\n"
            "emit_image()\n"
            "for receipt in receipts[2:4]:\n    emit_receipt(receipt)"
        )
        self.assertFalse(result.get("isError"), result)
        self.assertEqual([block["type"] for block in result["content"]], ["text", "image", "text"])
        self.assertEqual(result["content"][0]["text"].count("Action [dispatched]"), 2)
        self.assertEqual(result["content"][2]["text"].count("Action [dispatched]"), 2)
