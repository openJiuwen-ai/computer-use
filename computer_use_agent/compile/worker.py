# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Run or validate an exact Compile snapshot inside an existing MCP script."""

import asyncio
import json
import sys
from dataclasses import asdict
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic, sleep

from computer_use.client.execution import session_executor

from . import calc
from .artifact import Artifact, canonical, fingerprint
from .model import Check, Selection, conclude
from .pipeline import execute


def run_worker(computer, operation: str, bundle: dict, parameters: dict):
    """Synchronous entry point used by the normal MCP Python executor."""
    return session_executor().run(lambda: _dispatch(computer, operation, bundle, parameters))


async def _dispatch(computer, operation, bundle, parameters):
    with TemporaryDirectory(prefix="computer-use-program-") as directory:
        path = Path(directory)
        (path / "manifest.json").write_text(canonical(bundle["manifest"]), encoding="utf-8")
        (path / "program.py").write_text(bundle["source"], encoding="utf-8")
        artifact = Artifact.read(path)
        if (
            artifact.definition != bundle["definition"]
            or artifact.definition["id"] != calc.ID
            or artifact.manifest["constraints"] != calc.revision()
            or artifact.source != calc.PROGRAM.encode()
        ):
            return {"status": "refused", "effect": "none", "detail": "Compile implementation differs on the MCP host"}
        parameters = artifact.parameters(parameters)
        if operation == "check":
            return await _check(computer, artifact, parameters)
        if operation == "run":
            return await _run(computer, artifact, parameters)
        raise ValueError("Unknown Compile operation")


def _inspect(parameters):
    try:
        path, cells = calc.workbook_target(parameters)
        book = calc.snapshot(path)
        calc.preflight(book, parameters["sheet"], cells)
    except Exception as error:  # noqa: BLE001 - invalid or unreadable workbook before any input
        # Errors here concern only the saved workbook. No desktop input has run.
        return {"unsupported": f"{type(error).__name__}: {error}"}, None
    already_formatted = all(book[parameters["sheet"]][cell][2] == calc.number_format(parameters) for cell in cells)
    return {"path": path, "cells": cells, "already_formatted": already_formatted}, book


async def _run(computer, artifact, parameters, *, development=False):
    environment, before = await asyncio.to_thread(_inspect, parameters)

    async def observe_environment():
        return environment

    def select(definition):
        allowed = definition == artifact.definition
        return Selection(allowed, "Agent-selected published revision", "published", fingerprint(definition))

    result = await execute(
        artifact.path,
        computer,
        parameters,
        observe_environment,
        emit=lambda _: None,
        checkpoint=lambda: None,
        select=select,
        development=development,
        timeout_s=40,
    )
    detail = (result.failure or {}).get("detail", "")
    if environment.get("unsupported"):
        detail = environment["unsupported"]
    if result.status == "awaiting_acceptance":
        value, detail = await _verify_saved(parameters, environment, before, result.deadline_at_s)
        result = conclude(
            result,
            Check(
                result.acceptance_condition,
                value,
                result.scope,
                {"expected_sha256": result.expected_sha256, "execution_id": result.execution_id, "detail": detail},
                monotonic(),
            ),
        )
    wrote = any(step["kind"] == "write" for step in result.steps)
    effect = "confirmed" if wrote and result.status == "succeeded" else "unknown" if wrote else "none"
    return {
        "status": result.status,
        "effect": effect,
        "detail": detail[:1200],
        "definition": result.program,
        "execution_id": result.execution_id,
        "parameters": parameters,
        "duration_s": result.duration_s,
        "steps": result.steps,
        "failure": result.failure,
        "acceptance": asdict(result.acceptance) if result.acceptance else None,
    }


async def _verify_saved(parameters, environment, before, deadline):
    end = min(deadline, monotonic() + 4)
    while True:
        try:
            after = await asyncio.to_thread(calc.snapshot, environment["path"])
            valid, detail = calc.verify(
                before, after, parameters["sheet"], environment["cells"], calc.number_format(parameters)
            )
            value = "TRUE" if valid else "FALSE"
        except Exception as error:  # noqa: BLE001 - record unreadable saved state as UNKNOWN
            value, detail = "UNKNOWN", f"Saved workbook could not be read: {error}"
        if value == "TRUE" or monotonic() >= end:
            return value, detail
        await asyncio.sleep(0.2)


def make_fixture(path, sheet, cells, *, start_on_control=False):
    """Create known numeric inputs and unchanged neighboring cells for the verifier."""
    from openpyxl import Workbook

    book = Workbook()
    tab = book.active
    tab.title = sheet
    for index, cell in enumerate(sorted(cells), 1):
        tab[cell] = index + 0.234567
    keep = book.create_sheet("Control")
    keep["A1"] = "untouched"
    keep["B1"] = 20.125
    keep["B1"].number_format = "0.00"
    keep["C1"] = "=B1*2"
    if start_on_control:
        book.active = keep
    book.save(path)
    book.close()


async def _ready(computer, name):
    deadline = monotonic() + 12
    while monotonic() < deadline:
        catalogue = await asyncio.to_thread(computer.list_windows, app_name="LibreOffice")
        if any(row.title == name for row in catalogue.windows):
            return
        await asyncio.sleep(0.25)
    raise RuntimeError(f"LibreOffice did not open the disposable workbook {name}")


def _close_fixture(computer, path):
    rows = computer.list_windows(app_name="LibreOffice").windows
    for row in rows:
        if row.title != path.name:
            continue
        app = computer.app(row.app_id)
        observation = app.read(window=row.window_ref, full=True)
        if calc.document_matches(observation.text, path):
            # Close only the generated workbook. Normal SDK handling exposes
            # confirmation UI if a failed test left unsaved changes.
            return app.close_window(row.window_ref)
    return None


async def _open_fixture(computer, path):
    if sys.platform != "darwin":
        raise RuntimeError("Validation needs LibreOffice on the macOS MCP host")
    process = await asyncio.create_subprocess_exec(
        "/usr/bin/open",
        "-g",
        "-a",
        "LibreOffice",
        str(path),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    if await asyncio.wait_for(process.wait(), 5) != 0:
        raise RuntimeError("The macOS MCP host could not open the LibreOffice fixture")
    await _ready(computer, path.name)


def open_fixture(computer, path):
    """Prepare a disposable benchmark workbook outside the timed task."""
    return session_executor().run(lambda: _open_fixture(computer, path))


def close_fixture(computer, path):
    """Close only the matching disposable benchmark workbook."""
    _close_fixture(computer, path)
    deadline = monotonic() + 4
    while monotonic() < deadline:
        rows = computer.list_windows(app_name="LibreOffice").windows
        if not any(row.title == path.name for row in rows):
            return
        sleep(0.15)
    raise RuntimeError(f"Disposable fixture is still open: {path.name}")


async def _fixture_case(computer, artifact, root, name, sheet, cell_range, decimals, *, missing_sheet=False):
    path = root / f"compile-{root.name}-{name}.xlsx"
    parameters = {"document": str(path), "sheet": sheet, "range": cell_range, "decimals": decimals}
    cells = cell_range.split(":")
    await asyncio.to_thread(
        make_fixture, path, "Other" if missing_sheet else sheet, cells, start_on_control=name == "different_parameters"
    )
    if missing_sheet:
        original = path.read_bytes()
        result = await _run(computer, artifact, parameters, development=True)
        return {**result, "case": name, "unchanged": original == path.read_bytes()}

    try:
        await _open_fixture(computer, path)
        result = await _run(computer, artifact, parameters, development=True)
    finally:
        await asyncio.to_thread(close_fixture, computer, path)
    return {**result, "case": name}


async def _check(computer, artifact, parameters):
    environment, _ = await asyncio.to_thread(_inspect, parameters)
    confirmed = environment.get("already_formatted", False)
    report = {
        "definition": artifact.definition,
        "source": {
            "status": "succeeded" if confirmed else "unconfirmed",
            "parameters": parameters,
            "detail": environment.get("unsupported", "")
            or ("Saved number format confirmed" if confirmed else "Saved workbook has a different number format"),
        },
        "cases": [],
    }
    if not confirmed:
        return report
    with TemporaryDirectory(prefix="cua-check-") as directory:
        root = Path(directory).resolve()
        for name, sheet, cells, decimals in (
            ("positive", "Samples", "B2:B3", parameters["decimals"]),
            ("different_parameters", "Other Samples", "D3:E3", 2 if parameters["decimals"] == 3 else 3),
            ("missing_sheet", "Absent", "B2:B3", parameters["decimals"]),
        ):
            try:
                result = await _fixture_case(
                    computer, artifact, root, name, sheet, cells, decimals, missing_sheet=name == "missing_sheet"
                )
            except (OSError, RuntimeError) as error:
                result = {"case": name, "status": "unconfirmed", "detail": str(error)}
            report["cases"].append(result)
            expected = "refused" if name == "missing_sheet" else "succeeded"
            if result["status"] != expected:
                break
    return report


def script(operation, artifact, parameters):
    payload = {"manifest": artifact.manifest, "source": artifact.source.decode(), "definition": artifact.definition}
    return (
        "from computer_use_agent.compile.worker import run_worker as _compile_run\n"
        f"report_result(_compile_run(computer, {operation!r}, "
        f"__import__('json').loads({json.dumps(payload, ensure_ascii=False)!r}), "
        f"__import__('json').loads({json.dumps(parameters, ensure_ascii=False)!r})))"
    )
