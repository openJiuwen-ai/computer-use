# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Every trace survives batching and is published before tool completion."""

import asyncio
import json
import os
import tempfile
from pathlib import Path
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

from computer_use.json import dumps as original_dumps, loads
from computer_use_mcp.diagnostics import dumps
from computer_use_mcp.diagnostics import Journal


class JournalBatchingTests(IsolatedAsyncioTestCase):
    async def test_order_and_utf16_values_survive_batching_before_completion(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, COMPUTER_USE_LOG_DIR=directory):
            journal = Journal()
            original_open = Path.open
            writes = []

            def opened(path, *args, **kwargs):
                if path == journal.path:
                    writes.append(1)
                return original_open(path, *args, **kwargs)

            with patch.object(Path, "open", opened):
                for i in range(1000):
                    journal.record(dict(kind="pipeline", attempt=i, path=["中文🙂", "\ud800", "\ud83d\ude42"]))
                journal.record(dict(kind="execution_end", executionId=1))
            lines = [loads(line) for line in journal.path.read_text().splitlines()]
            self.assertEqual([e["attempt"] for e in lines[1:-1]], list(range(1000)))
            self.assertEqual(lines[-1]["kind"], "execution_end")
            self.assertEqual(lines[1]["path"], ["中文🙂", "\ud800", "🙂"])
            self.assertLess(len(writes), 20)
            self.assertFalse(journal._pending)
            self.assertIsNone(journal._scheduled)

    async def test_yield_and_metadata_flush_pending_traces(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, COMPUTER_USE_LOG_DIR=directory):
            journal = Journal()
            journal.record(dict(kind="pipeline", attempt=1))
            await asyncio.sleep(0)
            self.assertIn('"attempt":1', journal.path.read_text())
            journal.record(dict(kind="pipeline", attempt=2))
            journal.metadata()
            self.assertIn('"attempt":2', journal.path.read_text())

    def test_fast_trace_encoding_preserves_scalar_values_and_custom_fallback(self):
        for event in (
            dict(kind="pipeline", path=["中文", "\ud800", "\ud83d\ude42"], attempt=2, branch=None),
            dict(kind="pipeline", branch=1e-7),
            dict(kind="pipeline", branch=float("nan")),
        ):
            value = dict(diagnostic=event, executionId=1)
            self.assertEqual(json.loads(dumps(value)), json.loads(original_dumps(value)))
            self.assertEqual(dumps(value), original_dumps(value))
        for value in (
            dict(diagnostic=dict(kind="pipeline"), elapsed=-0.0),
            dict(diagnostic={"kind": "pipeline", "2": "two", "1": "one"}),
        ):
            self.assertEqual(dumps(value), original_dumps(value))
