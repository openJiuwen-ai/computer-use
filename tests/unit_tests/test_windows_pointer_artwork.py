# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import math
import os
import subprocess
import sys
from itertools import pairwise
from pathlib import Path

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
RENDER_PROBE = """
using System;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.Drawing.Imaging;
using System.IO;
using ComputerUse.Windows;

static class RenderProbe
{
    static void Main(string[] args)
    {
        for (int i = 1; i < args.Length; i++)
        {
            int dpiPercent = Int32.Parse(args[i]);
            float scale = dpiPercent / 100f;
            int size = (int)Math.Ceiling(AgentPointerAssets.Size.Width * scale) + 1;
            using (var image = new Bitmap(size, size, PixelFormat.Format32bppPArgb))
            using (var graphics = Graphics.FromImage(image))
            {
                graphics.SmoothingMode = SmoothingMode.AntiAlias;
                graphics.InterpolationMode = InterpolationMode.HighQualityBicubic;
                graphics.ScaleTransform(scale, scale);
                AgentPointerAssets.Draw(graphics);
                image.Save(Path.Combine(args[0], dpiPercent + ".png"), ImageFormat.Png);
            }
        }
    }
}
"""


@pytest.mark.skipif(sys.platform != "win32", reason="requires Windows GDI+")
def test_virtual_pointer_diagonal_has_continuous_coverage(tmp_path):
    compiler = Path(os.environ.get("WINDIR", "C:/Windows")) / "Microsoft.NET/Framework64/v4.0.30319/csc.exe"
    if not compiler.is_file():
        pytest.skip("requires the Windows backend C# compiler")
    preview = ROOT / "os_adapter/windows/Sources/Preview"
    probe = tmp_path / "RenderProbe.cs"
    probe.write_text(RENDER_PROBE, encoding="utf-8")
    executable = tmp_path / "RenderProbe.exe"
    compile_probe = [
        compiler,
        "/nologo",
        "/platform:x64",
        "/reference:System.Drawing.dll",
        f"/out:{executable}",
        probe,
        preview / "AgentPointerArtwork.cs",
        preview / "AgentPointerAssets.cs",
    ]
    scales = (100, 125, 150, 200)
    for command in (compile_probe, [executable, tmp_path, *map(str, scales)]):
        result = subprocess.run(
            command, capture_output=True, text=True, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW, check=False
        )
        assert result.returncode == 0, result.stdout + result.stderr

    # Measure the dark outline across the SVG's straight upper edge, away from the rounded ends.
    for percent in scales:
        scale = percent / 100
        with Image.open(tmp_path / f"{percent}.png") as pointer:
            background = Image.new("RGBA", pointer.size, "white")
            background.alpha_composite(pointer.convert("RGBA"))
        luminance = background.convert("L")
        coverage = []
        for x in range(math.ceil(31 * scale), math.floor(41 * scale) + 1):
            edge_y = 27.0375 * scale + (x + 0.5 - 29.6025 * scale) * (36.375 - 27.0375) / (42.9225 - 29.6025)
            coverage.append(
                sum(max(0, 220 - luminance.getpixel((x, y))) for y in range(round(edge_y) - 3, round(edge_y) + 4))
            )
        assert min(coverage) >= 15, (percent, coverage)
        assert max(abs(a - b) for a, b in pairwise(coverage)) < 110, (percent, coverage)
