"""Frozen v5 read output/call order, with the main CR/LF display fix applied.

The 2026-10-20 merge updates only four presentation strings in the SDK-menu
case; raw text, bindings and the native request sequence remain frozen.
"""

import asyncio
import json
import sys
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path

from repository import ROOT as REPO_ROOT

from computer_use.errors import ComputerUseError
from computer_use.interaction.read.app import NativeAppReader
from computer_use.native.connection import Connection
from computer_use.native.contracts import HostPolicy
from computer_use.native.transport import ConnectionPort

ROOT = REPO_ROOT
BASELINE = ROOT / "tests/fixtures/v6-read-baseline.json"
CASES = (
    "observation",
    "observation-loading",
    "observation-loading-scope-first",
    "observation-tree-error",
    "observation-sdk-menu",
    "observation-loading-refresh-error",
)


def stable(value):
    if isinstance(value, dict):
        return {
            (key if isinstance(key, str) else json.dumps(key)): stable(item)
            for key, item in value.items()
            if key != "timeoutMs"
        }
    if isinstance(value, (tuple, list)):
        return [stable(item) for item in value]
    return value


def public_contract(value):
    """Compare behavior while the internal fact schema migrates independently."""
    import copy

    result = copy.deepcopy(value)
    output = result["result"]
    output.pop("tree", None)  # Normalized graph preservation has dedicated codec/adapter tests.
    rendered = output.get("rendered")
    if rendered is not None:
        # Native field-path accounting changes to normalized paths. Visible text,
        # references and child reference ownership remain frozen.
        output["rendered"] = {key: rendered[key] for key in ("text", "refs", "inlineChildRefs")}
    return result


async def capture(mode):
    with tempfile.TemporaryDirectory(prefix="read-pipeline-parity-") as temporary:
        journal = Path(temporary) / "requests.jsonl"
        connection = Connection(
            lambda: None,
            dict(
                command=sys.executable,
                args=[str(ROOT / "tests/fixtures/v5-driver.py"), mode, str(journal)],
                timeoutMs=3000,
            ),
            protocol_version=6,
        )
        reader = NativeAppReader(ConnectionPort(connection, HostPolicy()))
        scopes = []
        try:
            observed = await reader.read("Native fixture", on_scope=lambda scope: scopes.append(asdict(scope)))
            result = dict(
                text=observed.text,
                directory=observed.directory,
                scope_confirmed=observed.scope_confirmed,
                refs={ref: asdict(binding) for ref, binding in observed.refs.items()},
                windows=dict(observed.windows),
                tree=observed.tree.facts if observed.tree is not None else None,
                rendered=observed.tree.render(compact=True) if observed.tree is not None else None,
                gaps=[asdict(gap) for gap in observed.gaps],
                selection=asdict(observed.selection),
                scopes=scopes,
            )
        except ComputerUseError as error:
            result = dict(
                error={name: getattr(error, name) for name in ("code", "effect", "target", "detail", "next")},
                scopes=scopes,
            )
        finally:
            connection.close()
            await connection.waitClosed()
        requests_value = [
            record_value
            for line_value in journal.read_text().splitlines()
            if "method" in (record_value := json.loads(line_value))
        ]
        return stable(dict(result=result, requests=requests_value))


class ReadPipelineParityTests(unittest.IsolatedAsyncioTestCase):
    async def test_outputs_bindings_scope_publication_and_native_sequence_match(self):
        expected = json.loads(BASELINE.read_text())
        for mode in CASES:
            with self.subTest(mode=mode):
                reference = expected[mode]
                self.assertTrue(all(request["version"] == 5 for request in reference["requests"]))
                # Only the explicitly breaking transport version changes;
                # outputs, native bindings and request order retain the oracle.
                migrated = dict(reference, requests=[dict(request, version=6) for request in reference["requests"]])
                if mode == "observation-sdk-menu":
                    # The normalized graph distinguishes an unexpanded child from
                    # a partly enumerated edge. Both keep the same read continuation.
                    migrated["result"] = dict(
                        migrated["result"],
                        text=migrated["result"]["text"].replace(
                            "Children: partial: native children partly collected; see Relation details; total unknown",
                            "Children: partial: child facts have not been collected; total unknown",
                        ),
                    )
                self.assertEqual(public_contract(await capture(mode)), public_contract(migrated))


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--capture":

        async def record():
            return {mode: await capture(mode) for mode in CASES}

        Path(sys.argv[2]).write_text(json.dumps(asyncio.run(record()), ensure_ascii=False, indent=2) + "\n")
    else:
        unittest.main()
