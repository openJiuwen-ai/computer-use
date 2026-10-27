"""Windows click intent preserves physical callbacks, shared policy and dispatch accounting."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from repository import ROOT as REPO_ROOT
from sdk.native.query.test_target_query import ROOT
from windows_sources import portableSources

from computer_use.errors import ComputerUseError
from computer_use.interaction.write.routes.policy import Attempts, choose, route_key
from computer_use.native.contracts import (
    Channel,
    ClickOffer,
    Facts,
    HostPolicy,
    InputContext,
    Intent,
    Knowledge as K,
    Primitive,
)


class WindowsElementPolicyTests(unittest.TestCase):
    @staticmethod
    def facts(*, ready=True, geometry=True):
        native = Primitive("native", "native_action", Channel.OBJECT, K.YES, K.YES, action="Invoke.Invoke")
        pointer = Primitive(
            "pointer",
            "pointer_sequence",
            Channel.POINTER,
            K.YES,
            K.YES if ready else K.NO,
            pending=() if ready else ("foreground",) if geometry else ("geometry",),
            background_preparation=K.NO,
        )
        return Facts(
            ROOT,
            "now",
            "element",
            K.YES,
            K.YES,
            K.YES,
            (),
            (native, pointer),
            InputContext(),
            platform="windows",
            native_role="ControlType.Button",
            actions_complete=True,
        )

    def test_exclusive_click_uses_physical_callbacks_and_prepares_before_dispatch(self):
        for ready in (True, False):
            command = choose(self.facts(ready=ready), Intent("click"), HostPolicy("exclusive"), Attempts())
            self.assertEqual(command.primitive.unit, "pointer_sequence")
            self.assertEqual(command.prepare, not ready)
            self.assertEqual(command.foreground, not ready)

    def test_shared_keeps_a_usable_background_native_action(self):
        for assist in (False, True):
            command = choose(self.facts(ready=False), Intent("click"), HostPolicy("shared", assist), Attempts())
            self.assertEqual(command.primitive.unit, "native_action")
            self.assertFalse(command.foreground)

    def test_explicit_native_action_and_missing_pointer_geometry_keep_the_object_route(self):
        for intent, facts in (
            (Intent("action", {"action": "Invoke.Invoke"}), self.facts()),
            (Intent("click"), self.facts(ready=False, geometry=False)),
        ):
            command = choose(facts, intent, HostPolicy("exclusive"), Attempts())
            self.assertEqual(command.primitive.unit, "native_action")

    def test_v1_exclusive_profile_does_not_fall_back_to_uia_after_pointer_preparation_failed(self):
        facts = self.facts(ready=False)
        native, pointer = facts.primitives
        native = replace(native, click=ClickOffer("windows.uia.Invoke.Invoke", "semantic", "object_action", "ready"))
        pointer = replace(
            pointer,
            click=ClickOffer(
                "windows.sendInput",
                "pointer",
                "desktop_pointer",
                "preparationRequired",
                preparation_options=("foreground",),
                preference_profile="physicalWhenAvailableInExclusive",
            ),
        )
        facts = replace(facts, primitives=(native, pointer))
        intent, policy, attempts = Intent("click"), HostPolicy("exclusive"), Attempts()
        chosen = choose(facts, intent, policy, attempts)
        self.assertEqual(chosen.primitive.id, pointer.id)
        self.assertTrue(chosen.foreground)
        self.assertFalse(chosen.prepare)
        attempts.unavailable.add(route_key(chosen))
        facts = replace(facts, primitives=(native, replace(pointer, id="refreshed-opaque-id")))
        with self.assertRaises(ComputerUseError):
            choose(facts, intent, policy, attempts)
        for assist in (False, True):
            self.assertEqual(
                choose(facts, intent, HostPolicy("shared", assist), Attempts()).primitive.unit, "native_action"
            )


@unittest.skipUnless(sys.platform == "win32", "requires Windows .NET Framework compiler")
class WindowsElementRuntimeTests(unittest.TestCase):
    def test_element_count_scope_revalidation_and_replacement_readback(self):
        root = REPO_ROOT
        framework = Path(os.environ["WINDIR"]) / "Microsoft.NET/Framework64/v4.0.30319"
        native = root / "os_adapter/windows"
        with tempfile.TemporaryDirectory(prefix="windows-element-pointer-") as temporary:
            exe = Path(temporary) / "probe.exe"
            command = [
                str(framework / "csc.exe"),
                "/nologo",
                "/utf8output",
                "/target:exe",
                "/platform:x64",
                "/warnaserror+",
                "/nowarn:0649",
                "/main:ComputerUse.Windows.NativeElementPointerTests",
                f"/out:{exe}",
            ]
            command += [
                "/reference:" + str(framework / name)
                for name in ("System.dll", "System.Core.dll", "System.Web.Extensions.dll")
            ]
            command += [
                str(native / name)
                for name in (
                    "Sources/Contracts/Wire.cs",
                    "Sources/Runtime/NativeRuntime.cs",
                    "Sources/Control/NativeExecution.cs",
                    "Sources/Control/NativeKeyboard.cs",
                    "Sources/Control/NativeText.cs",
                    "Sources/Control/NativeClipboard.cs",
                    "Sources/Control/NativePaste.cs",
                    "Sources/Read/NativeCapturedText.cs",
                    "Sources/Read/NativeReadValues.cs",
                    "Sources/Capture/NativeCaptureGeometry.cs",
                    "Sources/Control/NativePointerCapture.cs",
                    "Sources/Control/NativePointer.cs",
                    "Sources/Control/NativeScroll.cs",
                    "Tests/Control/NativeElementPointerTests.cs",
                )
            ]
            compiled = subprocess.run(portableSources(command), capture_output=True, text=True, timeout=30)
            self.assertEqual(compiled.returncode, 0, compiled.stdout + compiled.stderr)
            run = subprocess.run([str(exe)], capture_output=True, text=True, timeout=15)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            report = json.loads(run.stdout)
            self.assertTrue(report["passed"])
            self.assertGreaterEqual(report["assertions"], 40)


if __name__ == "__main__":
    unittest.main()
