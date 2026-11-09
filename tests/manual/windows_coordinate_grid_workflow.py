# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Measure 50 desktop image points using real SDK and stdio MCP click routes."""

import argparse
import asyncio
import base64
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import timedelta
from io import BytesIO
from pathlib import Path

import computer_use
from computer_use import Computer, ComputerUseError
from computer_use.paths import native_driver
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from PIL import Image
from windows_paste_workflow import read_state, wait_state

FIXTURE = Path(__file__).with_name("windows_coordinate_grid_fixture.cs")
ROOT = FIXTURE.resolve().parents[2]
ROWS, COLUMNS = 5, 10
COUNT = ROWS * COLUMNS
BATCH = 5
RECEIPT = re.compile(r"(?m)^GRID (\d+) (dispatched|verified) (\d+(?:\.\d+)?)$")


def marker_color(index: int) -> tuple[int, int, int]:
    row, col = divmod(index, COLUMNS)
    return 37 + row * 31, 29 + col * 17, 239 - row * 31 - col * 7


def solid_regions(pixels: set[tuple[int, int]]) -> list[tuple[int, int, int, int, int]]:
    """Ignore isolated color collisions introduced by delivery resampling."""
    regions = []
    while pixels:
        first = pixels.pop()
        left = right = first[0]
        top = bottom = first[1]
        count, pending = 0, [first]
        while pending:
            x, y = pending.pop()
            left, top = min(left, x), min(top, y)
            right, bottom = max(right, x), max(bottom, y)
            count += 1
            for neighbor in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
                if neighbor in pixels:
                    pixels.remove(neighbor)
                    pending.append(neighbor)
        regions.append((left, top, right, bottom, count))
    return sorted(regions, key=lambda region: region[4], reverse=True)


def marker_points(png: bytes, *, centered: bool = False) -> tuple[list[tuple[int, int]], tuple[int, int]]:
    """Recover each marker solely from the returned image's solid color."""
    palette = {marker_color(i): i for i in range(COUNT)}
    with Image.open(BytesIO(png)) as original, original.convert("RGB") as image:
        width, height = image.size
        pixels = [set() for _ in range(COUNT)]
        for offset, color in enumerate(image.getdata()):
            index = palette.get(color)
            if index is None:
                continue
            y, x = divmod(offset, width)
            pixels[index].add((x, y))
        points = []
        for index, color_pixels in enumerate(pixels):
            regions = solid_regions(color_pixels)
            if not regions:
                raise RuntimeError(f"Marker {index} is missing in the delivered image")
            left, top, right, bottom, count = regions[0]
            w, h = right - left + 1, bottom - top + 1
            competing = len(regions) > 1 and regions[1][4] >= max(32, count // 10)
            if w < 8 or h < 8 or count < 0.90 * w * h or competing:
                raise RuntimeError(f"Marker {index} is missing, clipped or ambiguous in the delivered image")
            row, col = divmod(index, COLUMNS)
            # Exercise pixels close to visible edges as well as centers.
            x, y = (left + right) // 2, (top + bottom) // 2
            if not centered:
                x = left + 2 if col == 0 else right - 2 if col == COLUMNS - 1 else x
                y = top + 2 if row == 0 else bottom - 2 if row == ROWS - 1 else y
            if image.getpixel((x, y)) != marker_color(index):
                raise RuntimeError(f"Marker {index} does not contain its selected pixel")
            points.append((x, y))
    if len(set(points)) != COUNT:
        raise RuntimeError("Two visual markers resolved to the same pixel")
    return points, (width, height)


def build_fixture(exe: Path) -> None:
    compiler = Path(os.environ["WINDIR"]) / "Microsoft.NET/Framework64/v4.0.30319/csc.exe"
    subprocess.run(
        [
            str(compiler), "/nologo", "/target:winexe", "/platform:x64", "/warnaserror+",
            "/reference:System.Windows.Forms.dll", "/reference:System.Drawing.dll",
            "/reference:System.Web.Extensions.dll", f"/out:{exe}", str(FIXTURE),
        ],
        check=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )


def record_image(report: dict, png: bytes, *, centered: bool = False) -> list[tuple[int, int]]:
    points, size = marker_points(png, centered=centered)
    report["image"] = {"sha256": hashlib.sha256(png).hexdigest(), "size": size, "points": points}
    return points


def sdk_grid(report: dict) -> None:
    with Computer() as computer:
        observation = computer.read(view="image", full=True)
        if observation.screenshot is None:
            raise RuntimeError("SDK returned no desktop screenshot")
        points = record_image(report, bytes(observation.screenshot))
        targets = [computer.point(point) for point in points]
        for index, target in enumerate(targets):
            started = time.perf_counter()
            result = computer.click(target=target)
            report["receipts"].append({
                "id": index, "status": result.status, "ms": round((time.perf_counter() - started) * 1000, 3),
            })


async def mcp_grid(report: dict) -> None:
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "computer_use_mcp.server"],
        cwd=str(ROOT),
        env={"PYTHONSAFEPATH": "1"},
    )
    async with (
        stdio_client(params) as (read, write),
        ClientSession(read, write, read_timeout_seconds=timedelta(seconds=45)) as client,
    ):
        await client.initialize()

        async def call(title, code):
            result = await client.call_tool("computer_use", {"title": title, "code": code})
            text = "\n".join(item.text for item in result.content if item.type == "text")
            if result.isError:
                raise RuntimeError(text)
            images = [item.model_dump(by_alias=True) for item in result.content if item.type == "image"]
            return text, images

        _, images = await call("Observe coordinate grid", 'computer.read(view="image", full=True)')
        if len(images) != 1:
            raise RuntimeError(f"Expected one delivered desktop image, received {len(images)}")
        caption = images[0].get("_meta", {}).get("computer-use", {}).get("caption", "")
        if "Image point: p = computer.point((x, y))" not in caption or "computer.click(target=p)" not in caption:
            raise RuntimeError("The delivered screenshot omitted its point constructor or click route")
        report["caption"] = caption
        points = record_image(report, base64.b64decode(images[0]["data"], validate=True))
        # Retain targets in the MCP execution session before any click can
        # produce a new image. Every coordinate uses this delivered map.
        await call("Retain grid points", f"grid = [computer.point(p) for p in {points!r}]")
        for start in range(0, COUNT, BATCH):
            text, _ = await call(
                "Click delivered grid points",
                "import time\n"
                f"for index in range({start}, {min(start + BATCH, COUNT)}):\n"
                "    started = time.perf_counter()\n"
                "    result = computer.click(target=grid[index])\n"
                "    print(f'GRID {index} {result.status} {(time.perf_counter() - started) * 1000:.3f}')",
            )
            reported = [
                {"id": int(index), "status": status, "ms": float(ms)} for index, status, ms in RECEIPT.findall(text)
            ]
            report["receipts"].extend(reported)
            if [item["id"] for item in reported] != list(range(start, min(start + BATCH, COUNT))):
                raise RuntimeError("MCP did not deliver one receipt for each requested click in this batch")


def ready(state: dict) -> bool:
    return bool(state.get("foreground") and state.get("covers_display") and state.get("dpi"))


def run(exe: Path, path: Path, transport: str, display: int) -> dict:
    fixture = subprocess.Popen([str(exe), str(path), uuid.uuid4().hex[:10], str(display)])
    report = {"transport": transport, "display": display, "receipts": [], "pass": False}
    try:
        initial = wait_state(path, ready, 3)
        if initial.get("covers_display") and not initial.get("foreground"):
            with Computer() as setup:
                setup.app(exe.stem).read(view="image", full=True, allow_foreground=True)
            initial = wait_state(path, ready, 4)
        report["initial"] = initial
        if not ready(initial) or initial.get("hit_count") != 0:
            raise RuntimeError(f"Fresh grid did not cover the selected foreground display: {initial}")
        if transport == "sdk":
            sdk_grid(report)
        else:
            asyncio.run(mcp_grid(report))
        ended = wait_state(path, lambda state: state.get("hit_count", 0) >= COUNT, 3)
        final = wait_state(path, lambda state: state.get("sample", 0) >= ended.get("sample", 0) + 2, 1)
        report["actual"] = final
        report["pass"] = (
            ready(final)
            and final["dpi"] == initial["dpi"]
            and final["screen"] == initial["screen"]
            and final["hit_count"] == final["downs"] == final["ups"] == COUNT
            and final["wrong_clicks"] == 0
            and [hit["id"] for hit in final["hits"]] == list(range(COUNT))
            and [receipt["id"] for receipt in report["receipts"]] == list(range(COUNT))
            and all(receipt["status"] in ("dispatched", "verified") for receipt in report["receipts"])
        )
    except (ComputerUseError, ExceptionGroup, OSError, RuntimeError, ValueError) as error:
        report["error"] = {"type": type(error).__name__, "detail": str(error)}
        report["actual"] = read_state(path)
    finally:
        if fixture.poll() is None:
            fixture.terminate()
        fixture.wait(timeout=5)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--transport", choices=("sdk", "mcp", "both"), default="both")
    parser.add_argument("--display", type=int, default=0, help="Existing Windows display index, starting at zero")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.display < 0:
        parser.error("display index cannot be negative")
    driver = Path(native_driver()["command"])
    report = {
        "module": computer_use.__file__,
        "driver": str(driver),
        "driver_sha256": hashlib.sha256(driver.read_bytes()).hexdigest(),
        "head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
    }
    with tempfile.TemporaryDirectory(prefix="cua-coordinate-grid-") as folder:
        directory = Path(folder)
        exe = directory / "CuaCoordinateGrid.exe"
        build_fixture(exe)
        transports = ("sdk", "mcp") if args.transport == "both" else (args.transport,)
        report["runs"] = [run(exe, directory / f"{transport}.json", transport, args.display) for transport in transports]
    report["pass"] = all(item["pass"] for item in report["runs"])
    serialized = json.dumps(report, ensure_ascii=True, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized, encoding="utf-8")
    sys.stdout.write(serialized)
    raise SystemExit(0 if report["pass"] else 1)


if __name__ == "__main__":
    main()
