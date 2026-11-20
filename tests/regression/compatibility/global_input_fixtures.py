# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Synthetic global-input driver with a record of every command."""

import asyncio
import json
import sys
from pathlib import Path

from computer_use.native.connection import Connection


class PrimitiveConnection(Connection):
    def __init__(
        self,
        journal,
        *,
        platform="macos",
        dispatch="dispatched",
        cleanup="complete",
        confirmation="notRequested",
        global_input=True,
    ):
        script = Path(__file__).resolve().parents[2] / "fixtures/global_input_driver.py"
        configuration = (platform, dispatch, cleanup, confirmation, global_input)
        super().__init__(
            lambda: None,
            {"command": sys.executable, "args": [str(script), str(journal), json.dumps(configuration)]},
        )
        self.journal = journal

    @property
    def requests(self):
        if not self.journal.exists():
            return []
        return [json.loads(line) for line in self.journal.read_text(encoding="utf-8").splitlines()]

    def clear_requests(self):
        self.journal.unlink(missing_ok=True)

    async def aclose(self):
        self.close()
        await self.wait_closed()

    def run(self, operation):
        async def run():
            try:
                return await operation
            finally:
                await self.aclose()

        return asyncio.run(run())
