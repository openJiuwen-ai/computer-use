# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import runpy
from pathlib import Path


def test_unicode_build_tables_preserve_python_and_regex_classification():
    root = Path(__file__).resolve().parents[2]
    generator = runpy.run_path(str(root / "computer_use_engine/interaction/read/accessibility/build_unicode.py"))
    python_data = """0000;<control>;Cc;0;BN
0020;SPACE;Zs;0;WS
0030;DIGIT ZERO;Nd;0;EN
0041;LATIN CAPITAL LETTER A;Lu;0;L
00AD;SOFT HYPHEN;Cf;0;BN
0301;COMBINING ACUTE ACCENT;Mn;230;NSM
05D0;HEBREW LETTER ALEF;Lo;0;R
4E00;<CJK Ideograph, First>;Lo;0;L
4E02;<CJK Ideograph, Last>;Lo;0;L"""
    regex_data = python_data + "\n1C89;CYRILLIC CAPITAL LETTER TJE;Lu;0;L"
    tables = generator["character_tables"](python_data, "4E00..4E02;W # Lo", regex_data)

    def contains(table, point):
        return any(start <= point < stop for start, stop in tables[table])

    assert contains("NON_PRINTABLE", 0)
    assert not contains("NON_PRINTABLE", ord(" "))
    assert tables["RTL_RANGES"] == [(0x05D0, 0x05D1)]
    assert tables["DECIMAL_DIGIT"] == [(ord("0"), ord("1"))]
    assert tables["ALPHABETIC"] == [(0x05D0, 0x05D1), (0x4E00, 0x4E03)]
    assert tables["ZERO"] == [(0x00AD, 0x00AE), (0x0301, 0x0302)]
    assert contains("WIDE", 0x4E01)
    assert not contains("WIDE", ord("A"))
    # A character assigned after Unicode 14 is still unassigned and wide
    # for the Python predicates, while the regex predicate treats it as a letter.
    assert contains("CATEGORY_C", 0x1C89)
    assert contains("WIDE", 0x1C89)
    assert contains("LETTER_OR_NUMBER", 0x1C89)
