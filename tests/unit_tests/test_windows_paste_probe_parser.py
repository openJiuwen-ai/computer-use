# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import importlib
import json
from pathlib import Path

import pytest


@pytest.fixture
def workflow(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "manual"))
    return importlib.import_module("windows_continuous_workflow")


@pytest.mark.parametrize(("row", "expected"), [
    ('\t1 编辑 (text-input, reports focus) Paste target, Value: ""', ""),
    ('\tRegion: 1 文档 (text-input, reports focus) Paste target, Document text: ""', ""),
    ("\tRegion: 1 文档 (text-input, reports focus) Paste target, Document text: CUA_4b81f8394e_draft",
     "CUA_4b81f8394e_draft"),
    ('\t1 编辑 (text-input, reports focus) Paste target, Value: "CUA, 中文 😀", Read only: false', "CUA, 中文 😀"),
])
def test_exact_paste_value_in_both_delivered_layouts(workflow, row, expected):
    observed = row + "\n\t2 编辑 (text-input) Bystander, Value: untouched"
    assert workflow.delivered_value(observed) == expected


def test_exact_paste_value_rejects_conflicting_targets(workflow):
    original = '\t1 编辑 (text-input) Paste target, Value: ""'
    duplicate = '\tRegion: 1 文档 (text-input) Paste target, Document text: ""'
    assert workflow.delivered_value(original + "\n" + duplicate) == ""
    for conflict in (duplicate.replace(": 1 ", ": 3 "), duplicate.replace('""', '"different"')):
        with pytest.raises(AssertionError, match="unambiguously"):
            workflow.delivered_value(original + "\n" + conflict)


def test_continuous_probe_selects_exact_title_from_retained_metadata(workflow):
    target = {"ref": 0, "title": "CUA Paste Fixture example"}
    other = {"ref": 3, "title": target["title"] + " other"}
    metadata = {"windows": [other, target]}
    assert workflow.fixture_window(metadata, target["title"]) == 0
    catalogue = 'Window 0, App: "CuaPasteFixture"; computer.app("application:test").read(window=0).'
    result = catalogue + "\nCaptured interface metadata: " + json.dumps(metadata) + "\nMore detail"
    report = {}
    assert workflow.mcp_window(result, target["title"], report) == 0
    assert report["catalogue"] == catalogue
    with pytest.raises(AssertionError, match="unambiguously"):
        workflow.fixture_window({"windows": [other]}, target["title"])
    with pytest.raises(AssertionError, match="unambiguously"):
        workflow.fixture_window({"windows": [target, {**target, "ref": 3}]}, target["title"])
