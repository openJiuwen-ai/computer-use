"""Read-only, all-sheet XLSX comparison for the opt-in Calc acceptance test."""

import json
import posixpath
import sys
from decimal import Decimal
from zipfile import ZipFile

from defusedxml.ElementTree import fromstring as parse_xml

NS = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
RID = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"


def read(path):
    with ZipFile(path) as z:
        strings = []
        if "xl/sharedStrings.xml" in z.namelist():
            strings = ["".join(si.itertext()) for si in parse_xml(z.read("xl/sharedStrings.xml"))]
        book = parse_xml(z.read("xl/workbook.xml"))
        rels = {r.get("Id"): r.get("Target") for r in parse_xml(z.read("xl/_rels/workbook.xml.rels"))}
        sheets = {}
        for sheet in book.find("s:sheets", NS):
            target = rels[sheet.get(RID)]
            target = target.lstrip("/") if target.startswith("/") else posixpath.normpath("xl/" + target)
            xml = parse_xml(z.read(target))
            cells = {}
            for c in xml.findall(".//s:sheetData/s:row/s:c", NS):
                value = c.findtext("s:v", namespaces=NS)
                t = c.get("t", "n")
                if t == "s" and value is not None:
                    value = ("text", strings[int(value)])
                elif t == "inlineStr":
                    value = ("text", "".join(c.find("s:is", NS).itertext()))
                elif value is not None:
                    value = (t, str(Decimal(value).normalize()) if t == "n" else value)
                if value == ("text", ""):
                    value = None
                formula = c.find("s:f", NS)
                cells[c.get("r")] = (value, None if formula is None else (formula.text, formula.attrib))
            sheets[sheet.get("name")] = cells
        names = [(n.attrib, n.text) for n in book.findall("s:definedNames/s:definedName", NS)]
        return sheets, names


def compare(before_path, after_path):
    before, names_before = read(before_path)
    after, names_after = read(after_path)
    assert before.keys() == after.keys(), "Sheet set changed"
    assert names_before == names_after, "Defined names changed"
    source = before["Sheet1"]
    expected = {}
    for col in "BCDE":
        above = None
        for row in range(1, 31):
            addr = f"{col}{row}"
            value, formula = source.get(addr, (None, None))
            assert formula is None, "This fixture must not contain formulas in the fill region"
            if value is None:
                assert above is not None, f"Missing source above {addr}"
                expected[("Sheet1", addr)] = (above, None)
            else:
                above = value
    changes = []
    for name in before:
        for addr in sorted(before[name].keys() | after[name].keys()):
            old, new = before[name].get(addr, (None, None)), after[name].get(addr, (None, None))
            wanted = expected.get((name, addr), old)
            assert new == wanted, f"Unexpected cell {name}!{addr}: {new!r}, expected {wanted!r}"
            if old != new:
                changes.append({"sheet": name, "cell": addr, "before": old[0], "after": new[0]})
    assert len(changes) == len(expected), "Unexpected change count"
    return {
        "filled": len(expected),
        "other_cell_contents_unchanged": True,
        "defined_names_unchanged": True,
        "changes": changes,
        "format_note": "Cell values/formulas are checked; LibreOffice may rewrite formatting metadata.",
    }


if __name__ == "__main__":
    print(json.dumps(compare(sys.argv[1], sys.argv[2]), ensure_ascii=False, indent=2))
