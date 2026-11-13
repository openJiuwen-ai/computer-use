# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Initial Compile profile ported from the LibreOffice Calc program."""

import hashlib
import json
import math
import re
from pathlib import Path
from urllib.parse import unquote, urlsplit

from .artifact import fingerprint

ID = "calc-format-decimals"
ACCEPTANCE = "xlsx-decimal-format-saved"
DESCRIPTION = (
    "LibreOffice Calc on macOS. For an already-open local .xlsx, set the number display format "
    "of a numeric cell range on a named worksheet, preserving values, and save it. "
    "Requires an absolute document path, exact sheet name, A1 range, and 1–10 decimal places."
)
PARAMETERS = {
    "document": {"type": "string", "min_length": 1, "max_length": 4096},
    "sheet": {"type": "string", "min_length": 1, "max_length": 31},
    "range": {"type": "string", "pattern": r"[A-Z]{1,3}[1-9][0-9]{0,6}(?::[A-Z]{1,3}[1-9][0-9]{0,6})?"},
    "decimals": {"type": "integer", "minimum": 1, "maximum": 10},
}
PROGRAM = """from computer_use_agent.compile.calc import format_decimals

async def run(ctx, computer, parameters):
    await format_decimals(ctx, computer, parameters)
"""


class OutOfScope(ValueError):
    """This profile cannot verify the requested workbook before input."""


def revision():
    return fingerprint(
        {
            name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
            for name in ("calc.py", "worker.py")
        }
    )


def number_format(parameters):
    return "0." + "0" * parameters["decimals"]


def workbook_target(parameters):
    from openpyxl.utils.cell import get_column_letter, range_boundaries

    path = Path(parameters["document"])
    if not path.is_absolute() or path.suffix.lower() != ".xlsx":
        raise OutOfScope("An absolute path to a local .xlsx is required")
    path = path.resolve(strict=True)
    c1, r1, c2, r2 = range_boundaries(parameters["range"])
    if not (1 <= c1 <= c2 <= 16384 and 1 <= r1 <= r2 <= 1048576) or (c2 - c1 + 1) * (r2 - r1 + 1) > 64:
        raise OutOfScope("The A1 range must cover 1–64 valid cells")
    return path, frozenset(
        f"{get_column_letter(column)}{row}" for row in range(r1, r2 + 1) for column in range(c1, c2 + 1)
    )


def snapshot(path):
    from openpyxl import load_workbook

    book = load_workbook(path, read_only=True, data_only=False)
    try:
        result = {}
        for sheet in book.worksheets:
            if sheet.max_row is None or sheet.max_column is None or sheet.max_row * sheet.max_column > 200_000:
                raise OutOfScope("The worksheet dimensions exceed the bounded verifier")
            result[sheet.title] = {
                cell.coordinate: (cell.data_type, cell.value, cell.number_format)
                for row in sheet.iter_rows()
                for cell in row
                if cell.value is not None or getattr(cell, "number_format", None) not in (None, "General")
            }
        return result
    finally:
        book.close()


def preflight(book, sheet, cells):
    if sheet not in book:
        raise OutOfScope(f"Worksheet {sheet!r} is absent")
    for address in sorted(cells):
        kind, value, _ = book[sheet].get(address, (None, None, None))
        if kind != "n" or type(value) not in (int, float) or not math.isfinite(value):
            raise OutOfScope(f"Cell {address} must hold a finite number")


def verify(before, after, sheet, cells, code):
    """Check saved business effects independently of the GUI receipts."""
    if tuple(after) != tuple(before):
        return False, "Worksheet names or order changed"
    for name, previous in before.items():
        actual = after[name]
        if set(actual) != set(previous):
            return False, f"Saved cells changed on {name}"
        for address, old in previous.items():
            wanted = (*old[:2], code) if name == sheet and address in cells else old
            if actual[address] != wanted:
                return False, f"Unexpected saved value or number format at {name}!{address}"
    return True, f"Saved {sheet} with number format {code} in {len(cells)} cells; checked values and other formats"


def document_matches(text, path):
    # The macOS AX window root reports the original file URL, including aliases such as /var.
    pattern = r'^\d+ standard window [^\n]+?, Document: ("(?:[^"\\]|\\.)*"|file://[^,\n]+)'
    for match in re.finditer(pattern, text, re.MULTILINE):
        raw = match[1]
        url = urlsplit(json.loads(raw) if raw.startswith('"') else raw.strip())
        if url.scheme == "file" and url.netloc in ("", "localhost") and Path(unquote(url.path)).resolve() == path:
            return True
    return False


async def format_decimals(ctx, computer, parameters):
    """Resolve every reference from the current workbook or its attached dialog."""
    environment = await ctx.observe_environment()
    ctx.check("supported workbook", "FALSE" if environment.get("unsupported") else "TRUE")
    path, cells = environment["path"], environment["cells"]
    if environment["already_formatted"]:
        return

    windows = await ctx.step("find workbook", lambda: computer.list_windows(app_name="LibreOffice"))
    candidates = [row for row in windows.windows if row.title == path.name]
    ctx.check("one workbook window", "TRUE" if len(candidates) == 1 else "UNKNOWN", candidates=len(candidates))
    row = candidates[0]
    app, window = computer.app(row.app_id), row.window_ref

    async def read(ref, name):
        state = await ctx.step(name, lambda: app.read(window=ref, full=True))
        return state.text

    async def act(name, ref, path, method, *args, **kwargs):
        return await ctx.step(name, lambda: getattr(app.element(path, window=ref), method)(*args, **kwargs), write=True)

    text = await read(window, "read workbook")
    ctx.check("exact workbook", "TRUE" if document_matches(text, path) else "UNKNOWN")
    sheet = "Sheet " + parameters["sheet"]
    sheet_label = rf"(?:{re.escape(sheet)}|{re.escape(json.dumps(sheet, ensure_ascii=False))})"
    table = rf"^\s*\d+ table(?: \([^\n]+?\))? {sheet_label}\s*$"
    address = parameters["range"]
    if not re.search(table, text, re.MULTILINE):
        # Calc's Name Box can select the worksheet and range together.
        sheet_name = parameters["sheet"].replace("'", "''")
        address = f"'{sheet_name}'.{address}"

    box = [{"role": "comboBox", "identifier": "pos_window"}]
    await act("enter range in Name Box", window, box, "type_text", address, replace=True)
    await ctx.step("confirm range", lambda: app.press_key("Return", window=window), write=True)
    text = await read(window, "read selected cells")
    ctx.check("selected worksheet", "TRUE" if re.search(table, text, re.MULTILINE) else "UNKNOWN")
    selected = {
        address
        for flags, address in re.findall(
            r"(?:^|\|)\s*\d+ text field \(([^\n)]+)\) ([A-Z]+\d+)(?=[:, \n|]|$)", text, re.MULTILINE
        )
        if "selected" in flags.split(", ")
    }
    ctx.check("selected range", "TRUE" if cells == selected else "UNKNOWN", selected=sorted(selected))

    await ctx.step("open Format Cells", lambda: app.press_key("super+1", window=window), write=True)
    windows = await ctx.step("find Format Cells", lambda: computer.list_windows(app_name="LibreOffice"))
    dialogs = [item for item in windows.windows if item.app_id == row.app_id and item.title == "Format Cells"]
    ctx.check("one format dialog", "TRUE" if len(dialogs) == 1 else "UNKNOWN", candidates=len(dialogs))
    dialog = dialogs[0].window_ref
    text = await read(dialog, "read Format Cells")
    ctx.check(
        "format dialog belongs to workbook",
        "TRUE" if re.search(rf"^Window relation: parent={window}(?:\s|,|$)", text, re.MULTILINE) else "UNKNOWN",
    )
    ctx.check(
        "Numbers tab active",
        "TRUE" if re.search(r"menu item \(selected[^)]*\) Numbers\b", text) else "UNKNOWN",
    )
    field = await ctx.step(
        "locate format code",
        lambda: app.element([{"role": "textArea", "name": "Format Code"}], window=dialog, fresh=True),
    )
    await ctx.step("set format code", lambda: field.type_text(number_format(parameters), replace=True), write=True)
    await act("apply format", dialog, [{"role": "button", "name": "OK"}], "click")
    await ctx.step("save workbook", lambda: app.press_key("super+s", window=window), write=True)
