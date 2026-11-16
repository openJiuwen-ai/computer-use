# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import ctypes
import json
import os
import subprocess
import sys
from ctypes import wintypes
from importlib.util import find_spec
from pathlib import Path
from uuid import uuid4

import pytest
from computer_use import Computer, paths
from computer_use.errors import ComputerUseError

WINDOW_PROBE = """
using System;
using System.Diagnostics;
using System.IO;
using System.Windows.Forms;

static class LaunchProbe
{
    [STAThread]
    static void Main()
    {
        string path = Application.ExecutablePath;
        File.AppendAllLines(Path.ChangeExtension(path, ".pids"),
                            new[] { Process.GetCurrentProcess().Id.ToString() });
        using (var window = new Form { Text = Path.GetFileNameWithoutExtension(path) })
        {
            window.Controls.Add(new Label { Text = "Windows launch verified", AutoSize = true });
            Application.Run(window);
        }
    }
}
"""


@pytest.mark.skipif(sys.platform != "win32", reason="requires a Windows desktop")
def test_explicit_read_launches_executable_and_retains_its_window(tmp_path):
    compiler = Path(os.environ.get("WINDIR", "C:/Windows")) / "Microsoft.NET/Framework64/v4.0.30319/csc.exe"
    driver = Path(paths.native_driver()["command"])
    if not compiler.is_file() or not driver.is_file():
        pytest.skip("requires the built Windows driver and its C# compiler")
    if find_spec("computer_use.interaction.read.accessibility._ax_read") is None:
        pytest.skip("requires the Rust accessibility extension")
    permissions = subprocess.check_output([driver, "--check-permissions"], timeout=5, encoding="utf-8")
    if not json.loads(permissions)["ready"]:
        pytest.skip("requires an unlocked interactive desktop")

    source = tmp_path / "LaunchProbe.cs"
    source.write_text(WINDOW_PROBE, encoding="utf-8")
    executable = tmp_path / f"CuaLaunchProbe-{uuid4().hex}.exe"
    pids = executable.with_suffix(".pids")
    subprocess.run(
        [compiler, "/nologo", "/target:winexe", "/reference:System.Windows.Forms.dll", f"/out:{executable}", source],
        check=True,
        capture_output=True,
        timeout=20,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )

    user32 = ctypes.WinDLL("user32")
    user32.FindWindowW.argtypes = (wintypes.LPCWSTR, wintypes.LPCWSTR)
    user32.FindWindowW.restype = wintypes.HWND
    user32.PostMessageW.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
    try:
        with Computer(allow_global_input=False) as computer:
            app = computer.app(str(executable))
            with pytest.raises(ComputerUseError):
                app.element("Windows launch verified", fresh=True)
            assert not pids.exists()

            result = app.read(view="tree")
            assert "Windows launch verified" in result.text
            assert "Launch: The Windows desktop Shell opened a matching application window." in result.text
            # A different textual selector must also reuse the live instance.
            result = computer.app(str(executable).upper()).read(view="tree")
            assert "Windows launch verified" in result.text
            assert "already open" in result.text

        assert user32.FindWindowW(None, executable.stem)
        assert len(pids.read_text().splitlines()) == 1
    finally:
        hwnd = user32.FindWindowW(None, executable.stem)
        if hwnd:
            user32.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE
