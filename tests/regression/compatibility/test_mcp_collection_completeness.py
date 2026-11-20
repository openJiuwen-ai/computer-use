# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Exercise collection completeness through SDK publication and MCP delivery."""

import inspect
import json
import os
import sys
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


pytestmark = pytest.mark.usefixtures("native_read")


def native_capture_sample(name):
    """Represent the original recording's budget and optional-field states in the driver protocol."""
    import json

    from computer_use.interaction.read.accessibility._native import require_read_engine
    from computer_use.interaction.read.accessibility.snapshot import NativeSnapshot

    def entity(token, role, title):
        return {
            "id": token,
            "kind": "element",
            "source": "macos.ax",
            "nativeType": "AXUIElement",
            "properties": [
                {"key": ["macos.ax", key], "value": {"nativeType": "CFString", "data": value}}
                for key, value in (("AXRole", role), ("AXTitle", title))
            ],
        }

    root = entity("root", "AXWindow", "Schedule launcher")
    read = {
        "intervalNs": [1, 2],
        "details": [
            [key, {"nativeType": "CFString", "data": value}]
            for key, value in (("surface", "surface"), ("observation_id", name))
        ],
    }
    nodes = [root]
    if name == "before_budget":
        read.update(complete=False, issues=[{"reason": "collection_budget", "detail": "collection time budget"}])
    else:
        root["properties"] += [
            {
                "key": ["macos.ax", "AXChildren"],
                "value": {
                    "nativeType": "CFArray",
                    "data": {"items": [{"nativeType": "AXUIElement", "data": {"ref": "button"}}]},
                },
                "read": {
                    "complete": True,
                    "details": [
                        [key, {"nativeType": "provider", "data": value}]
                        for key, value in (
                            ("relation", True),
                            ("certainty", "confirmed"),
                            ("state", "complete"),
                            ("total", 1),
                        )
                    ],
                },
            },
            {
                "key": ["macos.ax", "AXHelp"],
                "state": "unsupported",
                "value": None,
                "read": {"complete": False, "issues": [{"reason": "unsupported", "detail": "AXError -25205"}]},
            },
        ]
        nodes.append(entity("button", "AXButton", "Open schedule"))
    data = json.dumps(
        {
            "kind": "observation",
            "session": "sample",
            "platform": "macos",
            "roots": ["root"],
            "nodes": nodes,
            "read": read,
        }
    ).encode()
    frame = require_read_engine().open_read_frame("json", data, len(data))
    capture = frame.capture("window", [])
    return NativeSnapshot.number(capture, {token: str(i) for i, token in enumerate(capture.source_tokens)})


def replay_setup():
    from types import SimpleNamespace

    from computer_use.client.execution import session_executor
    from computer_use.interaction.agent.requests import ObserveUI
    from computer_use.interaction.controller import SessionController
    from computer_use.interaction.read.presentation.document import DisplayDocument
    from computer_use.interaction.read.accessibility.preparation import prepare_snapshot
    from computer_use.interaction.read.views.publication import publish
    from computer_use.native.contracts import Binding
    from computer_use.session.observation_data import observation_data

    binding = Binding("sample", "instance", "root", "surface", "root")
    controller = SessionController.__new__(SessionController)

    async def read(request, control):
        name = request.owner.case
        snapshot = native_capture_sample(name)
        prepared = prepare_snapshot(snapshot, _control=control)
        document = DisplayDocument.tree(prepared).scoped((binding.session, binding.instance, binding.root_window))
        coverage = (
            {
                "window": binding.root_window,
                "surface": binding.surface,
                "instance": binding.instance,
                "requested": ("tree",),
                "attempted": ("tree",),
                "captured": ("tree",),
                "included": ("tree",),
                "delivered": (),
                "required": True,
                "missing": (),
                "lifecycle": "observed",
            },
        )
        return publish(
            controller,
            None,
            document,
            trees=((binding, snapshot),),
            scope="recorded-launcher",
            view="window",
            window_coverage=coverage,
        )

    controller.pipeline = SimpleNamespace(arun=read)

    def replay(name):
        request = ObserveUI(SimpleNamespace(case=name), "tree", False, None)
        result = session_executor().run(lambda: controller.run(request))
        metadata = observation_data(result)
        print(  # noqa: T201 - exercise model-facing metadata through the actual MCP print hook
            {
                "case": name,
                "partial": metadata["partial"],
                "content_available": metadata["content_available"],
                "gap_codes": tuple(gap.code for gap in metadata["gaps"]),
                "window_coverage": tuple(dict(record) for record in metadata["window_coverage"]),
            }
        )
        return result

    globals()["replay"] = replay


async def run_replay(log_directory):
    repository = Path(__file__).resolve().parents[3]
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "computer_use_mcp"],
        cwd=repository,
        env={**os.environ, "COMPUTER_USE_LOG_DIR": str(log_directory)},
    )
    results = []
    async with (
        stdio_client(params) as (read, write),
        ClientSession(read, write, read_timeout_seconds=timedelta(seconds=20)) as client,
    ):
        await client.initialize()
        for index, name in enumerate(("before_budget", "before_full")):
            code = (
                inspect.getsource(native_capture_sample) + "\n" + inspect.getsource(replay_setup) + "\nreplay_setup()\n"
                if index == 0
                else ""
            )
            result = await client.call_tool("computer_use", {"code": code + f"replay({name!r})"})
            assert not result.isError, result
            text = "\n".join(block.text for block in result.content if block.type == "text")
            results.append({"case": name, "text": text})
    events = [
        json.loads(line) for path in Path(log_directory).glob("*.jsonl") for line in path.read_text().splitlines()
    ]
    ends = [event for event in events if event.get("kind") == "sdk_operation_end"]
    return results, ends, events


class RecordedCollectionCompletenessTests(unittest.IsolatedAsyncioTestCase):
    async def test_budget_root_stays_partial_and_unsupported_fields_do_not_create_false_gaps(self):
        with tempfile.TemporaryDirectory(prefix="recorded-partial-") as directory:
            results, ends, events = await run_replay(directory)
        self.assertEqual(len(ends), 2)
        self.assertTrue(all(event["operationType"] == "ObserveUI" for event in ends))
        self.assertTrue(ends[0]["observationPartial"])
        self.assertFalse(ends[0]["observationComplete"])
        self.assertEqual(ends[0]["observationGapCodes"], ["observation_incomplete"])
        self.assertIn("'partial': True", results[0]["text"])
        self.assertIn("Observation coverage: partial", results[0]["text"])
        self.assertIn("'partial': ('tree',)", results[0]["text"])
        self.assertIn("collection time budget", results[0]["text"])
        self.assertFalse(ends[1]["observationPartial"])
        self.assertTrue(ends[1]["observationComplete"])
        self.assertEqual(ends[1]["observationGapCodes"], [])
        self.assertIn("Open schedule", results[1]["text"])
        self.assertNotIn("Observation coverage: partial", results[1]["text"])
        captures = [event for event in events if event.get("kind") == "observation_member_coverage"]
        evidence = captures[0]["windows"][0]["treeCollection"]

        snapshot = native_capture_sample("before_budget")
        self.assertEqual(evidence["session"], snapshot.session)
        self.assertEqual(evidence["observation"], snapshot.id)
        self.assertEqual(evidence["intervalNs"], list(snapshot.interval_ns))

    def test_unshown_captured_tree_cannot_contribute_delivered_tree_coverage(self):
        from computer_use.identity import Record
        from computer_use.interaction.read.presentation.document import DisplayDocument
        from computer_use.interaction.read.views.publication import publish
        from computer_use.native.contracts import Binding
        from computer_use.session.observation_data import observation_data

        snapshot = native_capture_sample("before_budget")
        target = Binding("sample", "instance", "root", "surface", "root")
        state = publish(
            Record(),
            None,
            DisplayDocument.join(("Tree not included in this view.",)),
            trees=((target, snapshot),),
            view="image",
        )
        metadata = observation_data(state)
        self.assertFalse(metadata["partial"])
        self.assertEqual(metadata["gaps"], ())
        self.assertNotIn("Observation coverage: partial", state.text)
