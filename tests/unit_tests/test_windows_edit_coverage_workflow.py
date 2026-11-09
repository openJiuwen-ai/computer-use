# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import asyncio
import importlib
from pathlib import Path
from types import SimpleNamespace

import pytest
from computer_use.interaction.read.presentation.headers import (
    InterfaceHeader,
    MemberHeading,
    captured_metadata,
    interface_header,
    member_heading,
)
from computer_use.protocol.validation import validate_key


def test_edit_coverage_select_all_uses_public_key_grammar(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "manual"))
    workflow = importlib.import_module("windows_edit_coverage_workflow")
    validate_key(workflow.SELECT_ALL_KEY)


@pytest.mark.parametrize("multiple_windows", [False, True])
def test_edit_coverage_resolves_exact_title_from_captured_directory(monkeypatch, multiple_windows):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "manual"))
    workflow = importlib.import_module("windows_edit_coverage_workflow")
    target = MemberHeading(9, "CUA Edit Coverage test", "target", True, True, True, None, "none", False, True, True)
    extra = MemberHeading(4, target.title + " longer", "extra", False, False, False, None, "none", False, True, False)
    windows = (extra, target) if multiple_windows else (target,)
    header = InterfaceHeader("Fixture", "application", None, 9, 9, "member", windows, (), {"application": "fixture"})
    visible = interface_header(header, name_in_body=True)
    if multiple_windows:
        visible += "\n" + member_heading(target, name_in_body=True)
    visible += "\n 0 window " + target.title
    assert f'Window 9: "{target.title}"' not in visible

    details = captured_metadata(header)
    obs = SimpleNamespace(text=visible, read_details=lambda **_: SimpleNamespace(text=details, next=None))
    sdk_report = {"title": target.title}
    mcp_report = {"title": target.title}
    assert workflow.sdk_window_ref(sdk_report, obs) == 9
    assert workflow.mcp_window_ref(mcp_report, f"{visible}\n{details}\nAX details follow") == 9
    assert sdk_report == mcp_report
    with pytest.raises(RuntimeError, match="exact window"):
        workflow.mcp_window_ref({"title": target.title + " wrong"}, f"{visible}\n{details}")


def test_tree_edit_finds_unannotated_controls_in_compact_windows_rows(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "manual"))
    workflow = importlib.import_module("windows_edit_coverage_workflow")
    text = "AX: collected; Image: not requested.\n\t3 文本 Draft pending | 4 按钮 Submit draft\n"
    assert workflow.tree_ref(text, "Draft pending") == 3
    assert workflow.tree_ref(text, "Submit draft") == 4
    with pytest.raises(RuntimeError, match="uniquely located"):
        workflow.tree_ref(text.replace("4 按钮 Submit draft", "4 按钮 Other Submit draft"), "Submit draft")
    with pytest.raises(RuntimeError, match="uniquely located"):
        workflow.tree_ref(text.rstrip() + " | 5 按钮 Submit draft", "Submit draft")


def test_tree_edit_wire_guard_refuses_foreign_or_global_writes(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "manual"))
    workflow = importlib.import_module("windows_edit_coverage_workflow")
    forwarded, writes = [], []

    async def delegate(_, method, params, options):
        forwarded.append((method, params))

    monkeypatch.setattr(workflow, "tree_only_request", delegate)
    monkeypatch.setattr(workflow, "_EDIT_ROOT", "owned")
    monkeypatch.setattr(workflow, "_EDIT_WRITES", writes)
    begin = {"mode": "write", "target": {"rootWindow": "owned"}}
    asyncio.run(workflow.tree_edit_request(None, "begin", begin))
    for target in ({"rootWindow": "other"}, {"rootWindow": "owned", "global": True}):
        with pytest.raises(AssertionError, match="outside its exact owned window"):
            asyncio.run(workflow.tree_edit_request(None, "execute", {"command": {"target": target}}))
    assert forwarded == [("begin", begin)] and writes == [{"method": "begin", "root": "owned", "unit": None}]


@pytest.mark.parametrize("tier", ["full-tree", "partial-tree", "full-tree-event"])
@pytest.mark.parametrize("capture_attempt", [False, True])
def test_tree_edit_requires_exact_delivered_values_and_no_capture(monkeypatch, tier, capture_attempt):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "manual"))
    workflow = importlib.import_module("windows_edit_coverage_workflow")
    title, payload = "CUA Edit Coverage test", "new 中文🙂"
    report = {"tier": tier, "title": title, "payload": payload, "actions": []}
    reads, actions, owned_guards = [], [], []

    def unrelated_foreground(_):
        raise AssertionError("The product must be allowed to prepare its exact bound target")

    monkeypatch.setattr(workflow, "require_foreground", unrelated_foreground)
    monkeypatch.setattr(
        workflow, "require_owned_fixture", lambda fixture, title: owned_guards.append((fixture, title)), raising=False
    )

    async def read(foreground):
        reads.append(foreground)
        index = len(reads)
        draft = workflow.ORIGINAL if index == 1 else payload
        status = "Draft pending" if index < 3 else "Draft submitted 1"
        text = (
            "AX: collected; Image: not requested.\n"
            f"Region: 2 编辑 (text-input) Editable draft, Document text: {draft}\n"
            f"{index + 6} 按钮 (clickable) Submit draft\n"
            f"5 文本 (read-only) {status}\n"
        )
        if tier != "partial-tree":
            text += f"4 编辑 (text-input) Protected companion, Value: {workflow.UNTOUCHED}\n"
        return {
            "text": text,
            "capture_attempts": 0,
            "has_screenshot": False,
            "metadata": {
                "windows": [{"title": title, "ref": 1, "native_window": "fixture"}],
                "directory": {"surfaces": [{"window": "fixture"}], "observed": {"id": index}},
            },
        }

    async def act(ref, action, expected, root=None):
        actions.append((ref, action, expected, root))
        return {
            "status": "dispatched",
            "detail": "fixture",
            "observation_status": "available",
            "observation_error": None,
            "has_screenshot": False,
            "capture_attempts": int(capture_attempt),
            "native_writes": [
                {"method": "begin", "root": root, "unit": None},
                {
                    "method": "execute",
                    "root": root,
                    "unit": "native_text_edit" if action == "replace" else "native_action",
                },
            ],
        }

    run = workflow.tree_edit(read, act, "fixture", report)
    initial_foreground = tier != "full-tree-event"
    if capture_attempt:
        with pytest.raises(RuntimeError, match="attempted native capture"):
            asyncio.run(run)
        assert reads == [initial_foreground] and actions == [(2, "replace", payload, "fixture")]
    else:
        asyncio.run(run)
        assert reads == [initial_foreground, False, False]
        submit = "submit" if initial_foreground else "invoke"
        assert actions == [(2, "replace", payload, "fixture"), (8, submit, payload, "fixture")]
        assert owned_guards == [("fixture", title), ("fixture", title)]


def test_tree_edit_oracle_refuses_duplicate_submit_or_hidden_bystander_click(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "manual"))
    workflow = importlib.import_module("windows_edit_coverage_workflow")
    payload = "new 中文🙂"
    actual = {
        "text": payload,
        "submitted": payload,
        "submits": 1,
        "changes": 1,
        "bystander": workflow.UNTOUCHED,
        "bystanderChanges": 0,
        "paintedBystander": {
            "text": workflow.UNTOUCHED,
            "submittedText": None,
            "clicks": 0,
            "selections": 0,
            "units": 0,
            "submits": 0,
        },
    }
    workflow.score_tree(actual, payload)
    with pytest.raises(RuntimeError, match="one exact semantic submit"):
        workflow.score_tree({**actual, "submits": 2}, payload)
    with pytest.raises(RuntimeError, match="painted companion"):
        workflow.score_tree({**actual, "paintedBystander": {**actual["paintedBystander"], "clicks": 1}}, payload)


@pytest.mark.parametrize("wrong", [{"changes": 0}, {"enChange": 0}, {"wmSetText": 1}, {"emReplaceSel": 2}])
def test_event_edit_oracle_requires_input_and_notifications(monkeypatch, wrong):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "manual"))
    workflow = importlib.import_module("windows_edit_coverage_workflow")
    payload = "new 中文🙂"
    actual = {
        "text": payload,
        "submitted": payload,
        "submits": 1,
        "changes": 2,
        "enChange": 2,
        "emSetSel": 1,
        "emReplaceSel": 1,
        "wmSetText": 0,
        "wmPaste": 0,
        "submitEnabled": True,
        "bystander": workflow.UNTOUCHED,
        "bystanderChanges": 0,
    }
    workflow.score_tree(actual, payload, event_dependent=True)
    with pytest.raises(RuntimeError):
        workflow.score_tree({**actual, **wrong}, payload, event_dependent=True)
