# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import asyncio
import json
from types import SimpleNamespace

import pytest
import windows_save_reopen_workflow as workflow


def test_native_capture_is_vetoed_before_request_and_counted_after_catch(tmp_path, monkeypatch):
    forwarded = []

    async def request(_self, method, params, options=None):
        forwarded.append((method, params, options))
        return "allowed"

    monkeypatch.setattr(workflow.Connection, "request", request)
    audit = tmp_path / "capture.log"
    workflow.install_capture_veto(audit, "sdk")

    async def probe():
        assert await workflow.Connection.request(object(), "inspect", {}) == "allowed"
        assert await workflow.Connection.request(object(), "prepareObservation", {}) == "allowed"
        assert workflow.capture_guard_result(audit, "sdk")["passed"]
        with pytest.raises(RuntimeError, match="vetoed a native capture"):
            await workflow.Connection.request(object(), method="capture", params={"kind": "desktop"})

    asyncio.run(probe())
    assert forwarded == [("inspect", {}, None), ("prepareObservation", {}, None)]
    assert workflow.capture_guard_result(audit, "sdk") == {
        "events": ["sdk installed", "sdk attempt"],
        "attempts": 1,
        "passed": False,
    }


def test_passive_guard_allows_scoped_object_input_and_refuses_preparation_or_raw_input(tmp_path, monkeypatch):
    forwarded = []

    async def request(_self, method, params, options=None):
        forwarded.append((method, params))

    monkeypatch.setattr(workflow.Connection, "request", request)
    audit = tmp_path / "native.log"
    workflow.install_capture_veto(audit, "sdk", passive=True)
    target = {"rootWindow": "owned-root", "capture": None}
    write = {"mode": "write", "target": target, "intent": {"kind": "replace"}}
    action = {"command": {"kind": "native_text_edit", "foreground": False, "target": target}}
    allowed = [("begin", write), ("execute", action)]
    for name in ("Invoke.Invoke", "Window.Close"):
        allowed.extend(
            [
                ("begin", {**write, "intent": {"kind": "action", "parameters": {"action": name}}}),
                ("execute", {"command": {**action["command"], "kind": "native_action"}}),
            ]
        )

    async def probe():
        for method, params in allowed:
            await workflow.Connection.request(object(), method, params)
        assert workflow.capture_guard_result(audit, "sdk", passive=True)["passed"]
        with pytest.raises(RuntimeError, match="foreground preparation"):
            await workflow.Connection.request(object(), "prepareObservation", {})
        with pytest.raises(RuntimeError, match="foreground preparation"):
            await workflow.Connection.request(
                object(), "execute", {"command": {**action["command"], "foreground": True}}
            )
        with pytest.raises(RuntimeError, match="nonsemantic native input"):
            await workflow.Connection.request(object(), "begin", {**write, "intent": {"kind": "paste"}})
        with pytest.raises(RuntimeError, match="nonsemantic native input"):
            await workflow.Connection.request(
                object(), "execute", {"command": {**action["command"], "target": {"rootWindow": None}}}
            )

    asyncio.run(probe())
    assert forwarded == allowed
    result = workflow.capture_guard_result(audit, "sdk", passive=True)
    assert result["attempts"] == 0
    assert result["preparation_attempts"] == result["input_attempts"] == 2
    assert not result["passed"]


@pytest.mark.parametrize(
    ("route", "fault", "error"),
    [
        ("raw", None, None),
        ("raw", "file", "saved document bytes"),
        ("raw", "reference", "reused the previous"),
        ("raw", "old_rebound", "stale_target none"),
        ("semantic", None, None),
        ("semantic", "clipboard", "Unexpected clipboard change"),
        ("semantic", "shortcut", "Unexpected clipboard change or save source"),
        ("semantic", "local_activation", None),
        ("semantic", "owned_activation", "reached system foreground"),
        ("semantic", "sampled_foreground", "reached system foreground"),
        ("semantic", "unknown_close", "closeWindow dispatched closed"),
    ],
)
def test_save_close_reopen_oracle(tmp_path, route, fault, error):
    semantic = route == "semantic"
    initial_text = workflow.INITIAL_DRAFT if semantic else ""
    title, payload = "CUA Save Reopen fixture", "CUA document fixture 中文 😀"
    document = tmp_path / "test-document.txt"
    driver = str(tmp_path / "computer-use-driver.exe")
    runtime = ["sdk", driver, "hash"]
    report = {
        "title": title,
        "expected": payload,
        "closed": [],
        "edit_route": route,
        "provenance": {
            "module": runtime[0],
            "driver": driver,
            "native_sha256": {"computer-use-driver.exe": runtime[2]},
        },
    }
    states = {}

    def publish(mode, **updates):
        states[mode].update(updates)
        (tmp_path / f"{mode}.json").write_text(json.dumps(states[mode]), encoding="utf-8")

    def start(mode):
        first = mode == "new"
        pid = 41 if first else 42
        # A reused HWND does not make a new process/window lifetime stale.
        states[mode] = {
            "title": title,
            "pid": pid,
            "hwnd": 91,
            "bystander": "untouched",
            "focused": not semantic,
            "foreground": False,
            "foreground_hwnd": 17,
            "ever_foreground": False,
            "activations": 0,
            "activation_foreground_hwnds": [],
            "clipboard_sequence": 71,
            "button_saves": 0,
            "shortcut_saves": 0,
        }
        publish(mode, text=initial_text if first else payload, pastes=0, saves=0, closes=0, closed=False)
        return SimpleNamespace(pid=pid, wait=lambda timeout: 0), tmp_path / f"{mode}.json", states[mode].copy()

    def tree(ref, value=""):
        text = f"Window {ref}: {title}\n  11 (text-input) Paste target, Value: {value}"
        return text + "\n  13 (focusable) Save document" if semantic else text

    async def call(label, code):
        if semantic:
            assert all(name not in code for name in ("fixture_foreground", "computer.paste", "computer.press_key"))
        if label == "record resolved runtime":
            return "CUA_SAVE_RUNTIME " + json.dumps(runtime)
        if label in ("observe the original blank document", "observe the original draft"):
            assert f"window_title={title!r}" in code and 'view="tree"' in code
            return tree(5, initial_text)
        if label == "paste the document text":
            assert code.index("fixture_foreground") < code.index("computer.paste")
            return "CUA_SAVE_INPUT 91 dispatched not_requested"
        if label == "replace the document text":
            assert f"original_app.element('Paste target', window=5).type_text({payload!r}, replace=True)" in code
            return "CUA_SAVE_SEMANTIC replace dispatched"
        if label == "verify the edited document tree":
            return tree(5, payload)
        if label == "save the edited document":
            assert code.index("fixture_foreground") < code.index('computer.press_key("ctrl+s")')
            publish("new", text=payload, pastes=1, saves=1)
            document.write_bytes((payload if fault != "file" else payload[:-1]).encode("utf-8"))
            return "CUA_SAVE_INPUT 91 dispatched not_requested"
        if label == "invoke the Save button":
            assert "original_app.element('Save document', window=5).perform_action(\"Invoke.Invoke\")" in code
            activation_hwnds = {"local_activation": [17, 17], "owned_activation": [91]}.get(fault, [])
            publish(
                "new",
                text=payload,
                pastes=0,
                saves=1,
                clipboard_sequence=72 if fault == "clipboard" else 71,
                button_saves=int(fault != "shortcut"),
                shortcut_saves=int(fault == "shortcut"),
                activations=len(activation_hwnds),
                activation_foreground_hwnds=activation_hwnds,
                ever_foreground=fault == "sampled_foreground",
            )
            document.write_bytes(payload.encode("utf-8"))
            return "CUA_SAVE_SEMANTIC action verified"
        if label == "close the saved document":
            assert "original_app.close_window(5)" in code
            publish("new", closes=1, closed=True)
            if fault == "unknown_close":
                return "CUA_SAVE_CLOSE closeWindow dispatched unknown"
            return "CUA_SAVE_CLOSE closeWindow dispatched closed"
        if label == "read the reopened document":
            assert f"window_title={title!r}" in code and 'view="tree"' in code
            return tree(5 if fault == "reference" else 8, payload)
        if label == "check the original pinned window after reopening":
            assert 'original_app.read(view="tree"' in code
            return tree(8, payload) if fault == "old_rebound" else "CUA_SAVE_STALE stale_target none"
        assert label == "close the reopened fixture for cleanup"
        assert "reopened_app.close_window(8)" in code
        publish("reopen", closes=1, closed=True)
        return "CUA_SAVE_CLOSE closeWindow dispatched closed"

    if error:
        with pytest.raises(RuntimeError, match=error):
            asyncio.run(workflow.exercise(call, start, document, report))
        assert not report.get("original_stale")
    else:
        asyncio.run(workflow.exercise(call, start, document, report))
        assert report["original_stale"] and [row["ref"] for row in report["closed"]] == [5, 8]
