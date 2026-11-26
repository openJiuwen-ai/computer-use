# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Read-only .docx inspector that runs INSIDE the OSWorld guest (shipped as
source text by osworld_tools.inspect_docx over /run_python), so it uses only
the standard library: no python-docx in the guest.

Reports, per body paragraph, what python-docx-style evaluators read from the
file: paragraph style, alignment, list numbering, and for each distinct run
style the font, size (pt), color, bold, italic, underline and hyperlink. It
also reports tables (rows x cols with a cell preview) in body order and notes
headers/footers and section count. It reads the saved file on disk; unsaved
edits in Writer are invisible.

Local check: ``python docx_inspect_guest.py file.docx 1-20``.
"""
import glob
import os
import re
import zipfile
import xml.etree.ElementTree as ET
from typing import NamedTuple, Optional

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
RELS = "http://schemas.openxmlformats.org/package/2006/relationships"


def _q(ns, tag):
    return "{%s}%s" % (ns, tag)


def find_docx(path):
    if path:
        if not os.path.exists(path):
            raise FileNotFoundError("no such file: %s" % path)
        return path
    home = os.path.expanduser("~")
    found = []
    for d in (os.path.join(home, "Desktop"), home, os.path.join(home, "Documents")):
        found.extend(sorted(glob.glob(os.path.join(d, "*.docx"))))
    if len(found) == 1:
        return found[0]
    if not found:
        raise FileNotFoundError("no .docx found under ~/Desktop, ~ or ~/Documents; pass path=")
    raise ValueError("several .docx files, pass path= : " + ", ".join(found))


def parse_selection(spec, count):
    if not spec or not spec.strip():
        return list(range(1, count + 1))
    chosen = set()
    for part in re.split(r"[,\s]+", spec.strip()):
        if not part:
            continue
        if "-" in part:
            lo, hi = part.split("-", 1)
            chosen.update(range(int(lo), int(hi) + 1))
        else:
            chosen.add(int(part))
    return [i for i in sorted(chosen) if 1 <= i <= count]


def read_rels(z, rels_path):
    out = {}
    if rels_path not in z.namelist():
        return out
    try:
        root = ET.fromstring(z.read(rels_path))
    except (KeyError, ET.ParseError):
        return out
    for rel in root.iter(_q(RELS, "Relationship")):
        out[rel.get("Id")] = (rel.get("Target"), rel.get("TargetMode", "Internal"))
    return out


def _bool_prop(rpr, tag):
    # <w:b/>, <w:i/> etc.: present => yes unless w:val is 0/false; absent => inherit.
    el = rpr.find(_q(W, tag))
    if el is None:
        return "inherit"
    val = el.get(_q(W, "val"))
    return "no" if val in ("0", "false", "off") else "yes"


class RunStyle(NamedTuple):
    """A run's distinct formatting; hashable, so it keys the per-style text groups."""

    font: str
    size: str
    color: str
    bold: str
    italic: str
    underline: str
    link: Optional[str]


def run_style(rpr, link):
    if rpr is None:
        return RunStyle("inherit", "inherit", "inherit", "inherit", "inherit", "inherit", link)
    rfonts = rpr.find(_q(W, "rFonts"))
    font = "inherit"
    if rfonts is not None:
        font = rfonts.get(_q(W, "ascii")) or rfonts.get(_q(W, "hAnsi")) or "inherit"
    sz = rpr.find(_q(W, "sz"))
    size = "inherit"
    if sz is not None and sz.get(_q(W, "val")):
        try:
            size = "%gpt" % (int(sz.get(_q(W, "val"))) / 2.0)
        except (TypeError, ValueError):
            size = "inherit"
    clr = rpr.find(_q(W, "color"))
    color = clr.get(_q(W, "val")) if clr is not None and clr.get(_q(W, "val")) else "inherit"
    bold = _bool_prop(rpr, "b")
    italic = _bool_prop(rpr, "i")
    u = rpr.find(_q(W, "u"))
    if u is None:
        underline = "inherit"
    else:
        uval = u.get(_q(W, "val"))
        underline = "no" if uval == "none" else (uval or "yes")
    return RunStyle(font, size, color, bold, italic, underline, link)


def run_text(r):
    parts = []
    for node in r.iter():
        if node.tag == _q(W, "t"):
            parts.append(node.text or "")
        elif node.tag == _q(W, "tab"):
            parts.append("\t")
        elif node.tag in (_q(W, "br"), _q(W, "cr")):
            parts.append(" ")
    return "".join(parts)


def cell_text(tc):
    parts = []
    for t in tc.iter(_q(W, "t")):
        parts.append(t.text or "")
    return "".join(parts)


def _snip(text, n=60):
    text = " ".join(text.split())
    return text[:n] + ("..." if len(text) > n else "")


def describe_paragraph(p, n, rels, out):
    ppr = p.find(_q(W, "pPr"))
    style, align, numbering = "Normal", "left", False
    if ppr is not None:
        pstyle = ppr.find(_q(W, "pStyle"))
        if pstyle is not None and pstyle.get(_q(W, "val")):
            style = pstyle.get(_q(W, "val"))
        jc = ppr.find(_q(W, "jc"))
        if jc is not None and jc.get(_q(W, "val")):
            align = jc.get(_q(W, "val"))
        if ppr.find(_q(W, "numPr")) is not None:
            numbering = True
    out.append("paragraph %d: style=%s align=%s%s"
               % (n, style, align, " numbering=list" if numbering else ""))
    styles, order = {}, []
    for node in p:
        runs = []
        if node.tag == _q(W, "r"):
            runs.append((node, None))
        elif node.tag == _q(W, "hyperlink"):
            rid = node.get(_q(R, "id"))
            if rid:
                target = rels.get(rid, ("<missing rel %s>" % rid,))[0]
            else:
                target = "<no target>"
            for r in node.findall(_q(W, "r")):
                runs.append((r, target))
        for r, link in runs:
            text = run_text(r)
            if not text.strip():
                continue
            key = run_style(r.find(_q(W, "rPr")), link)
            if key not in styles:
                styles[key] = []
                order.append(key)
            styles[key].append(text)
    for key in order:
        font, size, color, bold, italic, underline, link = key
        line = "  font=%s size=%s color=%s bold=%s italic=%s underline=%s" \
               % (font, size, color, bold, italic, underline)
        if link:
            line += " link=%s" % link
        line += "  text=%r" % _snip("".join(styles[key]))
        out.append(line)


def describe_table(tbl, para_no, out):
    rows = tbl.findall(_q(W, "tr"))
    grid = tbl.find(_q(W, "tblGrid"))
    if grid is not None and grid.findall(_q(W, "gridCol")):
        cols = len(grid.findall(_q(W, "gridCol")))
    else:
        cols = max((len(r.findall(_q(W, "tc"))) for r in rows), default=0)
    out.append("table @para%d: %d rows x %d cols" % (para_no, len(rows), cols))
    shown = 0
    for r in rows:
        for tc in r.findall(_q(W, "tc")):
            if shown >= 8:
                out.append("    ...(more cells)")
                return
            out.append("    cell: %r" % _snip(cell_text(tc), 30))
            shown += 1


def inspect_docx(path="", paragraphs=""):
    path = find_docx(path)
    out = []
    with zipfile.ZipFile(path) as z:
        try:
            root = ET.fromstring(z.read("word/document.xml"))
        except (KeyError, ET.ParseError) as e:
            return ("cannot read word/document.xml: %s" % e)
        rels = read_rels(z, "word/_rels/document.xml.rels")
        body = root.find(_q(W, "body"))
        if body is None:
            return ("%s: no <w:body> found" % path)
        children = list(body)
        total_paras = sum(1 for c in children if c.tag == _q(W, "p"))
        chosen = parse_selection(paragraphs, total_paras)
        more = 0
        if len(chosen) > 200:
            more = len(chosen) - 200
            chosen = chosen[:200]
        chosen_set = set(chosen)
        out.append("%s: %d paragraphs; showing %s"
                   % (path, total_paras,
                      "all" if len(chosen) == total_paras and not more
                      else ",".join(str(i) for i in chosen)))
        para_no = 0
        for c in children:
            if c.tag == _q(W, "p"):
                para_no += 1
                if para_no in chosen_set:
                    describe_paragraph(c, para_no, rels, out)
            elif c.tag == _q(W, "tbl"):
                describe_table(c, para_no, out)
        if more:
            out.append("...(%d more paragraphs)" % more)
        names = z.namelist()
        headers = [n for n in names if re.match(r"word/header\d*\.xml$", n)]
        footers = [n for n in names if re.match(r"word/footer\d*\.xml$", n)]
        sections = len(list(root.iter(_q(W, "sectPr"))))
        out.append("headers: %d, footers: %d, sections: %d"
                   % (len(headers), len(footers), sections))
    # ASCII-safe: the guest's stdout encoding is not under our control and
    # documents contain non-ASCII glyphs; the text snippets are only locators.
    return "\n".join(out).encode("ascii", "replace").decode("ascii")
