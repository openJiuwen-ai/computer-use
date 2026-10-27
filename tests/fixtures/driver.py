"""Protocol fixture only: never touches the desktop or requires Node.js."""

import json
import os
import sys
import threading

mode = sys.argv[1] if len(sys.argv) > 1 else "default"
png = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVR4nGNgAAIAAAUAAXpeqz8AAAAASUVORK5CYII="
changed_png = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVR4nGP4DwQACfsD/fteaysAAAAASUVORK5CYII="
image_reads = count = 0
desktop_mode = "shared"
contexts = {}
last_context = dict(after="fixture-origin", scope="fixture:window", window="1")
presentation_value = "before"
presentation_feedback_unavailable = interaction_popup_open = first_window_opened = False
session = "fixture-session"
capabilities = [
    "structuredObservations",
    "elementInput",
    "windowInput",
    "desktopObservation",
    "desktopInput",
    "cancel",
    "requestDedup",
    "captureMetadata",
    "postActionObservation",
    "retainedCaptures",
    "desktopCoordination",
]


def encoded(value, ending="\n"):
    return (json.dumps(value, ensure_ascii=False, separators=(",", ":")) + ending).encode()


def write(data, fd=1):
    os.write(fd, data)


def later(seconds, callback):
    timer = threading.Timer(seconds, callback)
    timer.daemon = True
    timer.start()


def node(ref, role, properties=None, children=None):
    return dict(ref=ref, role=role, **(properties or {}), children=children or [])


def error(code="fixture_error", target="Fixture", effect="possible", detail="", next="read the app", **extra):
    return dict(code=code, target=target, effect=effect, detail=detail, next=next, **extra)


def dispatch(request):
    global count, image_reads, desktop_mode, last_context, first_window_opened
    global interaction_popup_open, presentation_value, presentation_feedback_unavailable
    if "cancel" in request:
        return
    method, params, request_id = request["method"], request["params"], request["id"]
    if method == "hello":
        desktop_mode = params["desktopMode"]
        if mode == "hello-hang":
            return
        write(
            encoded(
                dict(
                    version=2 if mode == "hello-version" else 3,
                    session=session,
                    id=0,
                    result=dict(
                        platform="fixture",
                        desktopMode="mismatch" if mode == "hello-mode" else desktop_mode,
                        maxTimeoutMs=120000,
                        capabilities=capabilities[:-1] if mode == "hello-capability" else capabilities,
                    ),
                )
            )
        )
        return
    if mode == "diagnostics" and method == "focusElement":

        def diagnostic(message):
            write(encoded(dict(requestId=request_id, message=message)), 2)

        diagnostic("focus request")
        later(0.08, lambda: diagnostic("delayed focus diagnostic"))
        later(
            0.08,
            lambda: diagnostic(
                dict(
                    kind="native_metrics",
                    version=1,
                    method="focusElement",
                    status="ok",
                    durationMs=8,
                    phases=dict(windowPreparation=dict(count=2, durationMs=5)),
                    counters=dict(cachedWindowPreparation=1),
                )
            ),
        )
    if mode == "preview" and os.environ.get("COMPUTER_USE_PREVIEW_FD") == "3":
        frame = encoded(dict(kind="frame", name="测试应用", png=png))
        at = max(1, frame.find("测".encode()) + 1)
        write(frame[:at], 3)
        write(frame[at:], 3)
    if mode == "hang-action" and method != "readApp":
        return
    count += 1
    response = dict(
        id=request_id, result=dict(target="Fixture", status="dispatched", detail="Input dispatched; read to verify.")
    )
    if mode == "receipts" and method == "focusElement":
        response["result"] = dict(target="Fixture: Input", status="verified", detail="Target input focus verified.")
    if mode == "guarded-input" and method == "typeTextElement":
        exact = params["text"] == "exact"
        response["result"] = dict(
            target="Fixture: Input",
            status="verified" if exact else "dispatched",
            detail="Target text verified."
            if exact
            else "Input transformed by the application; inspect before submitting.",
        )
    if method == "listApps":
        response["result"] = (
            [dict(id=f"fixture.app.{i}", displayName=f"应用 {i}", isRunning=i % 2 == 0) for i in range(343)]
            if mode == "many-apps"
            else [dict(id="test.app", displayName="测试应用", isRunning=False)]
        )
    if method == "readApp":
        root, context, title = node("1", "button", dict(roleDescription="按钮", name="中文")), {}, None
        refs = {"1": "r1"}
        if mode == "fresh-refs":
            root = node(
                "42", "button", dict(roleDescription="按钮", name="中文", value=str(count), identifier="AllClear")
            )
            refs = {"42": f"r{count}"}
        if mode in ("presentation", "presentation-error"):
            root = node(
                "0",
                "window",
                dict(name="Fixture"),
                [
                    node(
                        "1",
                        "textField",
                        dict(name="Name Box", value=str(count) if mode == "presentation" else presentation_value),
                    ),
                    node("2", "text", dict(name=("unchanged " * 200).strip())),
                ],
            )
            context, refs = dict(app="Test"), {"0": "window", "1": f"r{count}", "2": "body"}
        if mode in ("diagnostics", "multi-app"):
            root, refs = node("1", "textField", dict(focusable=True, name="Input", value="")), {"1": f"r{count}"}
            context, title = dict(app="Test"), params["selector"] if mode == "multi-app" else "Fixture"
        scope = f"{params['selector']}:window"
        windows, window, overview = {"1": scope}, "1", None
        observation = dict(
            root=root,
            context=context,
            windows=[dict(ref="1", title=title or "Fixture")],
            current=window,
            screenshot=dict(width=1, height=1),
        )
        if title is not None:
            observation["title"] = title
        if mode == "distinct-views":
            observation["context"] = dict(app="View fixture")
            if params.get("menuPath"):
                observation["context"]["menuQuery"] = True
                observation["menuPath"] = params["menuPath"]
                observation["root"] = node(
                    "20",
                    "menu",
                    dict(name="Edit"),
                    [node("21", "menuItem", dict(name="Menu-only command", value=str(count)))],
                )
                refs = {"20": "distinct-menu", "21": "distinct-menu-item"}
            elif params.get("elementRef"):
                observation["context"]["subtreeRef"] = "1"
                observation["root"] = node(
                    "1",
                    "group",
                    dict(name="Inspect here"),
                    [node("10", "button", dict(name="Subtree-only detail", value=str(count)))],
                )
                refs = {"1": "distinct-target", "10": "distinct-detail"}
            else:
                observation["root"] = node(
                    "0",
                    "window",
                    dict(name="Fixture"),
                    [
                        node("1", "group", dict(name="Inspect here")),
                        node("2", "textField", dict(name="Window-only value", value=str(count))),
                        node("3", "text", dict(name=("Stable whole-window context. " * 100).strip())),
                    ],
                )
                refs = {"0": "distinct-window", "1": "distinct-target", "2": "distinct-value", "3": "distinct-body"}
        if mode == "overview":
            refs, overview, window = {}, png, params.get("window")
            windows["2"] = f"{params['selector']}:second"
            observation.update(
                root=None,
                context=dict(app="Test"),
                title="Unknown",
                current=window,
                windows=[dict(ref="1", title="Document"), dict(ref="2", title="Popup")],
                overviewText="Overview: app layout 1x1 px; context only",
            )
            if not window:
                observation.pop("screenshot", None)
        if mode in ("feedback-windows", "feedback-popup"):
            windows["2"] = f"{params['selector']}:second"
            window = params.get(
                "window",
                ("2" if mode == "feedback-popup" else contexts.get(params["after"], {}).get("window"))
                if params.get("after")
                else "1",
            )
            observation.update(current=window, windows=[dict(ref="1", title="First"), dict(ref="2", title="Second")])
            refs = {"1": f"{params['selector']}:{window}:field"}
        if mode == "feedback-new-window" and params.get("after"):
            windows["2"] = f"{params['selector']}:second"
            observation["windows"].append(dict(ref="2", title="Unrelated or auxiliary popup"))
            observation["overviewText"] = "Overview: two observed windows; relationship unknown"
            overview = png
        if mode == "feedback-explicit-popup" and interaction_popup_open:
            windows["2"] = f"{params['selector']}:second"
            window = params.get("window", "2")
            observation.update(
                current=window,
                title="Current dialog" if window == "2" else "Parent document",
                root=node(
                    "0",
                    "window",
                    children=[
                        node(
                            "1",
                            "textField" if window == "2" else "button",
                            dict(name="Dialog input" if window == "2" else "Parent action", focusable=True),
                        )
                    ],
                ),
                windows=[dict(ref="1", title="Parent document"), dict(ref="2", title="Current dialog", modal=True)],
                overviewText="Overview: parent document and current dialog",
            )
            refs, overview = (
                {"0": f"{params['selector']}:{window}:root", "1": f"{params['selector']}:{window}:field"},
                png,
            )
        image_reads += 1 if len(sys.argv) > 2 and sys.argv[2] == "changing-images" else 0
        response["result"] = dict(
            instance=params["selector"],
            appRef="app1" if mode == "fragment" else f"app{count}",
            scope=windows["2"] if window == "2" else scope,
            windows=windows,
            window=window,
            observation=observation,
            refs=refs,
            screenshot=None
            if mode == "overview" and not window
            else changed_png
            if image_reads and image_reads % 2 == 0
            else png,
        )
        if overview:
            response["result"]["overview"] = overview
        if mode == "feedback-first-window":
            app_scope = "fixture:document" if first_window_opened else "fixture:application"
            current = "1" if first_window_opened else None
            response["result"].update(
                instance="fixture:first-window" if first_window_opened else "fixture",
                scope=app_scope,
                window=current,
                windows={"1": app_scope} if first_window_opened else {},
                screenshot=png if first_window_opened else None,
            )
            observation.update(
                current=current,
                windows=[dict(ref="1", title="New document")] if first_window_opened else [],
                root=node(
                    "1",
                    "textField" if first_window_opened else "menuItem",
                    dict(name="Document input" if first_window_opened else "New window"),
                ),
            )
            if first_window_opened:
                observation["title"] = "New document"
            else:
                observation.pop("title", None)
                observation.pop("screenshot", None)
    if mode == "diagnostics" and method == "typeText":
        response = dict(
            id=request_id, error=error(effect="partial", detail="injected failure", next="read the app before retrying")
        )
    if mode == "wrong-id":
        response["id"] += 1
    if mode == "both":
        response["error"] = "bad"
    if mode == "bad-png":
        response["result"] = png[:-8] + "AAAAAAAA"
    if mode == "error-once" and count == 1:
        response = dict(id=request_id, error="injected action failure")
    if mode == "error-guidance":
        response = dict(id=request_id, error=error(code="input_unavailable", effect="none", detail="unsupported"))
    if mode == "error-guidance-target":
        response = dict(
            id=request_id,
            error=error(
                code="input_unavailable",
                target="Observation Probe",
                effect="none",
                detail="element is disabled",
                next="read the app and choose an enabled control",
            ),
        )
    if mode == "hang":
        return
    if mode == "exit":
        sys.exit(1)
    if mode == "incomplete":
        write(b'{"id":')
        sys.exit(0)
    if mode == "invalid-utf8":
        write(b"\xff\n")
        return
    result = response.get("result")
    if method == "readApp" and isinstance(result, dict) and result.get("appRef"):
        last_context = dict(after=result["appRef"], scope=result["scope"], window=result["window"])
        for token in [result["appRef"], *result["refs"].values()]:
            contexts[token] = last_context
    if isinstance(result, dict) and result.get("target"):
        result["context"] = contexts.get(
            params.get("desktopRef", params.get("appRef", params.get("ref"))), last_context
        )
    if mode == "feedback-first-window" and method == "clickElement":
        first_window_opened = True
    if mode == "feedback-first-window" and params.get("after") == "app1":
        response = dict(
            id=request_id,
            error=error(
                code="stale_target",
                target="old windowless observation",
                effect="none",
                detail="follow-up observation expired",
                next="read the current window",
            ),
        )
    if params.get("after") and mode == "feedback-fail":
        response = dict(id=request_id, error="follow-up capture unavailable")
    if params.get("after") and mode == "feedback-closed":
        response = dict(
            id=request_id,
            error=error(
                code="stale_target",
                target="Fixture dialog",
                effect="none",
                detail="the selected window has closed; it will not switch to another document",
                next="read the remaining application window before continuing",
            ),
        )
    if params.get("after") and mode == "feedback-hang":
        return
    if mode == "feedback-explicit-popup" and method == "openElementMenu":
        interaction_popup_open = True
        response = dict(
            id=request_id,
            error=error(
                code="stale_target",
                target="Parent action",
                effect="none",
                detail="application input window changed to a new window or dialog",
                context=contexts.get(params.get("ref")),
            ),
        )
    if mode == "feedback-explicit-popup" and params.get("after"):
        sys.exit(1)
    if mode == "feedback-action-error" and method == "typeTextElement":
        response = dict(
            id=request_id,
            error=error(
                effect="partial",
                detail="Injected partial input",
                next="read before continuing",
                context=contexts.get(params.get("ref")),
            ),
        )
    if mode == "presentation-error" and method == "typeTextElement":
        if params["text"] == "disconnect":
            sys.exit(1)
        if params["text"] == "partial":
            presentation_value = "partial"
        if params["text"] == "no-feedback":
            presentation_value, presentation_feedback_unavailable = "after failed input", True
        response = dict(
            id=request_id,
            error=error(
                code="input_unavailable",
                effect="none" if params["text"] == "reject" else "partial",
                detail="Injected action failure",
                next="read before continuing",
                context=contexts.get(params.get("ref")),
            ),
        )
        if params["text"] == "malformed":
            response["id"] += 1
    if mode == "presentation-error" and params.get("after") and presentation_feedback_unavailable:
        response = dict(
            id=request_id,
            error=error(
                code="execution_failed", effect="none", detail="follow-up capture unavailable", next="read again"
            ),
        )

    def capture(data, kind, scope):
        return dict(
            png=data,
            kind=kind,
            scope=scope,
            id=f"{kind}-{request_id}",
            width=1,
            height=1,
            startedAt=1001,
            completedAt=1002,
        )

    result = response.get("result")
    if method == "readApp" and isinstance(result, dict) and result.get("appRef"):
        result["observed"] = dict(id=f"observation-{request_id}", startedAt=1000, completedAt=1003)
        if result.get("screenshot"):
            result["screenshot"] = capture(result["screenshot"], "window", result["scope"])
        if result.get("overview"):
            result["overview"] = capture(result["overview"], "overview", "application")
    if method == "readDesktop" and not response.get("error"):
        rect = dict(x=0, y=0, width=1, height=1)
        response["result"] = dict(
            desktopRef=f"desktop-{request_id}",
            scope="desktop",
            observed=dict(id=f"observation-{request_id}", startedAt=1000, completedAt=1003),
            screenshot=capture(png[:-8] + "AAAAAAAA" if mode == "bad-png" else png, "desktop", "desktop"),
            observation=dict(
                mode=("exclusive" if desktop_mode == "shared" else "shared")
                if mode == "desktop-mode-mismatch"
                else desktop_mode,
                canvas=dict(width=1, height=1),
                frontmost=dict(name="Fixture", selector="test.app"),
                displays=[dict(name="Display 1", rect=rect, captured=True)],
                windows=[dict(app="Fixture", title="", rect=rect)],
                omittedWindows=0,
            ),
        )
        last_context = dict(after=response["result"]["desktopRef"], scope="desktop", window=None)
        contexts[response["result"]["desktopRef"]] = last_context
    if isinstance(response.get("error"), str):
        response["error"] = error(detail=response["error"])
    if mode.startswith("failure:"):
        response = dict(
            id=request_id,
            error=error(
                code=mode[8:],
                target="Name field",
                effect="partial",
                detail="The next input event was refused after an attempted prefix",
                next="read the field before continuing",
                context=last_context,
            ),
        )
    response.update(version=3, session="another-session" if mode == "wrong-session" else session)
    data = encoded(response, "\r\n")
    if mode == "duplicate":
        write(data + data)
    elif mode == "fragment":
        at = max(1, data.find("中".encode()) + 1)
        write(data[:at])

        def remainder():
            write(data[at:-1])
            later(0.005, lambda: write(data[-1:]))

        later(0.005, remainder)
    elif mode == "slow-action" and isinstance(response.get("result"), dict) and response["result"].get("target"):
        later(0.08, lambda: write(data))
    else:
        write(data)


if mode == "diagnostics":
    write(encoded(dict(kind="backend", version="0.0.1-test", desktopMode="shared", executable=sys.executable)), 2)
for line in sys.stdin:
    dispatch(json.loads(line))
