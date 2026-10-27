"""Exercise the production Windows graph encoder without native UIA calls."""

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from windows_sources import native_source

BASE = Path(__file__).resolve().parents[4] / "os_adapter/windows"


@unittest.skipUnless(os.environ.get("COMPUTER_USE_WINDOWS_TEST_DOTNET"), "standalone .NET SDK not configured")
class NativeObservationGraphTests(unittest.TestCase):
    def test_graph_preserves_native_values_resources_and_partial_reads(self):
        sdk = Path(os.environ["COMPUTER_USE_WINDOWS_TEST_DOTNET"])
        compiler = sorted((sdk / "sdk").glob("*/Roslyn/bincore/csc.dll"))[-1]
        refs = sorted((sdk / "packs/Microsoft.NETCore.App.Ref").glob("*/ref/net8.0"))[-1]
        runtime = sorted((sdk / "shared/Microsoft.NETCore.App").glob("8.*"))[-1]
        with tempfile.TemporaryDirectory(prefix="windows-observation-graph-") as temporary:
            out = Path(temporary)
            env = dict(
                os.environ,
                DOTNET_ROOT=str(sdk),
                DOTNET_CLI_HOME=str(out / "home"),
                DOTNET_CLI_TELEMETRY_OPTOUT="1",
                DOTNET_SKIP_FIRST_TIME_EXPERIENCE="1",
            )
            command = [
                str(sdk / "dotnet"),
                "exec",
                str(compiler),
                "/nologo",
                "/noconfig",
                "/nostdlib+",
                "/langversion:5",
                "/target:exe",
                "/warnaserror+",
                "/nowarn:0649",
                "/out:" + str(out / "probe.dll"),
            ]
            command += ["/reference:" + str(path) for path in sorted(refs.glob("*.dll"))]
            command += [
                str(native_source(path))
                for path in (
                    "Sources/Contracts/Wire.cs",
                    "Sources/Read/NativeObservationGraph.cs",
                    "Sources/Read/NativeCachedPropertyGate.cs",
                    "Tests/Read/NativeObservationGraphTests.cs",
                )
            ]
            built = subprocess.run(command, capture_output=True, text=True, env=env, timeout=60)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            (out / "probe.runtimeconfig.json").write_text(
                json.dumps(
                    dict(
                        runtimeOptions=dict(
                            tfm="net8.0", framework=dict(name="Microsoft.NETCore.App", version=runtime.name)
                        )
                    )
                )
            )
            result = subprocess.run(
                [str(sdk / "dotnet"), str(out / "probe.dll")], capture_output=True, text=True, env=env, timeout=30
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            report = json.loads(result.stdout)
            self.assertGreaterEqual(report["assertions"], 13)
            from computer_use.protocol.observation.adapters import decode_driver_observation, decode_query_facts

            raw_graph = report["graph"]
            graph = decode_driver_observation(raw_graph)
            self.assertEqual(graph.roots, ("root",))
            self.assertIn("root", graph.nodes)
            self.assertNotIn("range", graph.nodes, "Text ranges stay resources rather than UI nodes")
            self.assertNotIn("opaque", graph.nodes, "Diagnostic resources are not UI nodes")
            raw_root = next(node for node in raw_graph["nodes"] if node["id"] == "root")
            self.assertEqual(tuple(value["data"] for value in raw_root["identity"]["data"]["items"]), (42, 5, -8))
            projected = decode_query_facts(raw_graph)
            self.assertEqual(projected["nodes"][0]["values"]["UTF16"], "prefix\ud800suffix\udfff")
            self.assertEqual(projected["nodes"][0]["values"]["Unknown.Fact"][:3], [False, 0, ""])
            failed = next(prop for prop in raw_root["properties"] if prop["key"][1] == "Malformed")
            raw = dict(failed["read"]["issues"][0]["nativeStatus"]["data"]["fields"])["rawResult"]["data"]["items"]
            self.assertEqual(raw[1]["data"]["ref"], "opaque")
            self.assertEqual(raw[2]["data"]["fields"][0][1]["issue"]["reason"], "native_resource_budget_exhausted")
            cached = next(
                issue for issue in raw_graph["read"]["issues"] if issue["reason"] == "cached_property_decode_failed"
            )
            evidence = dict(cached["nativeStatus"]["data"]["fields"])
            self.assertTrue(evidence["cached"]["data"])
            self.assertEqual(tuple(value["data"] for value in evidence["rawResult"]["data"]["items"]), (1.0, 2.0))
            self.assertEqual(evidence["api"]["data"], "IUIAutomationElement.GetCachedPropertyValueEx")
            self.assertEqual(
                projected["nodes"][0]["values"]["CachedProperty"], dict(x=-1920.5, y=-10.25, width=0, height=32.5)
            )


if __name__ == "__main__":
    unittest.main()
