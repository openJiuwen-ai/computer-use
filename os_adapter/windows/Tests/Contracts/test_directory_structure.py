"""Source/Pass boundaries and a portable C# stage extension (no Windows UI)."""

import importlib.util
import json
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("windows_portable_sources", BASE / "Build/portable_sources.py")
source_sets = importlib.util.module_from_spec(spec)
spec.loader.exec_module(source_sets)


class NativeSourceStructureTests(unittest.TestCase):
    def test_native_test_sources_mirror_existing_source_responsibilities(self):
        tests = BASE / "Tests"
        self.assertFalse(list(tests.glob("*.cs")))
        self.assertFalse(list(tests.glob("test*.py")))
        for path in tests.rglob("*.cs"):
            relative = path.relative_to(tests)
            self.assertTrue((BASE / "Sources" / relative.parts[0]).is_dir(), str(path))
        self.assertTrue((tests / "Control/Object/NativeObjectExecutionTests.cs").is_file())
        self.assertTrue((tests / "Read/Query/NativeReadProtocolTests.cs").is_file())
        self.assertTrue((tests / "Runtime/NativePipelineStagesTests.cs").is_file())

    def test_main_sources_and_workers_are_separate_targets(self):
        self.assertFalse(list(BASE.glob("*.cs")))
        self.assertTrue((BASE / "Workers/Capture/Program.cs").is_file())
        self.assertFalse(list((BASE / "Sources").rglob("*Worker*")))
        self.assertTrue((BASE / "Build/ImportUia.cs").is_file())

    def test_each_pass_is_one_named_stage_with_effect_and_narrow_borrows(self):
        passes = list((BASE / "Sources").rglob("Passes/*Pass.cs"))
        self.assertGreaterEqual(len(passes), 18)
        for path in passes:
            source = path.read_text()
            self.assertEqual(re.findall(r"\bclass\s+(\w+Pass)\b", source), [path.stem], str(path))
            self.assertEqual(len(re.findall(r"\bstatic\s+[^\n]*\bRun\(", source)), 1, str(path))
            self.assertIn("Effects = ", source, str(path))
            # Method groups and explicit operation/provider contracts are
            # permitted; the whole Runtime/executor must never be injectable.
            code = re.sub(r"//[^\n]*", "", source)
            self.assertNotRegex(code, r"\b(?:NativeRuntime|NativeExecution)\b", str(path))
            self.assertNotRegex(code, r"\b\w+Pass\.Run\(", str(path))

    @unittest.skipUnless(os.environ.get("COMPUTER_USE_WINDOWS_TEST_DOTNET"), "standalone .NET SDK not configured")
    def test_isolated_stage_and_extension_probe(self):
        sdk = Path(os.environ["COMPUTER_USE_WINDOWS_TEST_DOTNET"])
        compiler = sorted((sdk / "sdk").glob("*/Roslyn/bincore/csc.dll"))[-1]
        refs = sorted((sdk / "packs/Microsoft.NETCore.App.Ref").glob("*/ref/net8.0"))[-1]
        runtime = sorted((sdk / "shared/Microsoft.NETCore.App").glob("8.*"))[-1]
        names = (
            "Wire.cs",
            "NativeRuntime.cs",
            "NativeExecution.cs",
            "NativeKeyboard.cs",
            "NativeText.cs",
            "NativeCapturedText.cs",
            "NativeReadValues.cs",
            "NativeClipboard.cs",
            "NativePaste.cs",
            "NativeCaptureGeometry.cs",
            "NativePointerCapture.cs",
            "NativePointer.cs",
            "NativeScroll.cs",
        )
        sources = [next((BASE / "Sources").rglob(name)) for name in names]
        sources += [
            BASE / "Tests" / name
            for name in (
                "Runtime/NativePipelineStagesTests.cs",
                "Control/Object/NativeObjectExecutionTests.cs",
                "Read/Query/NativeReadProtocolTests.cs",
            )
        ]
        sources = source_sets.expanded_sources(sources)
        with tempfile.TemporaryDirectory(prefix="native-stage-probe-") as tmp:
            out = Path(tmp)
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
                "/main:ComputerUse.Windows.NativePipelineStagesTests",
                "/out:" + str(out / "probe.dll"),
            ]
            command += ["/reference:" + str(p) for p in sorted(refs.glob("*.dll"))] + list(map(str, sources))
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
            self.assertGreaterEqual(json.loads(result.stdout)["assertions"], 12)


if __name__ == "__main__":
    unittest.main()
