# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Preserve native stage timestamps across both pipeline diagnostic routes."""

import asyncio
import json
from io import BytesIO
from unittest.mock import Mock

import pytest

from computer_use import diagnostics
from computer_use.interaction.read.numbering.pipeline import _emit_projection_trace
from computer_use.pipeline.core import Control
from computer_use_mcp.worker_transport import WorkerMessageWriter, read_message


@pytest.mark.parametrize("encoded", [False, True], ids=["ordinary", "canonical"])
def test_buffered_native_trace_retains_rust_stage_times(encoded, monkeypatch):
    clock = Mock(return_value="2026-11-04T03:00:30.000Z")

    async def read():
        frames = BytesIO()
        timestamps = []

        def collect(row):
            timestamps.append(row["timestamp"])

        monkeypatch.setattr(diagnostics, "_handler", collect)
        monkeypatch.setattr(diagnostics, "_pipeline_transport", None)
        monkeypatch.setattr(diagnostics, "_pipeline_timestamp", clock)
        monkeypatch.setattr("computer_use_mcp.worker_transport._pipeline_timestamp", clock)
        if encoded:
            writer = WorkerMessageWriter(frames, canonical_pipeline=True)
            diagnostics._register_pipeline_transport(collect, lambda event: writer.send_pipeline(event, 12))
        _emit_projection_trace(
            Control(diagnostic=diagnostics.pipeline_diagnostic),
            (
                ("PresentationPipeline", "enter", None, 1_793_761_201_125),
                ("PresentationPipeline", "exit", None, 1_793_761_202_375),
                ("PresentationPipeline", "enter", None, None),
                ("PresentationPipeline", "exit", None, 1_793_761_240_500),
            ),
        )
        if encoded:
            writer.flush()
            reader = asyncio.StreamReader()
            reader.feed_data(frames.getvalue())
            reader.feed_eof()
            while (record := await read_message(reader, encoded_pipeline=True)) is not None:
                timestamps.append(json.loads(record.line)["timestamp"])
        return timestamps

    assert asyncio.run(read()) == [
        "2026-11-04T03:00:01.125Z",
        "2026-11-04T03:00:02.375Z",
        "2026-11-04T03:00:30.000Z",
        "2026-11-04T03:00:40.500Z",
    ]
    clock.assert_called_once_with()
