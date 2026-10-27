"""Protocol/lifetime fixture; asynchronous cancellation without desktop APIs."""

import json
import os
import signal
import sys
import threading
from pathlib import Path

journal, mode = Path(sys.argv[1]), sys.argv[2]
session = f"deadline-fixture-{os.getpid()}"
prior = [json.loads(line) for line in journal.read_text().splitlines()] if journal.exists() else []
pending = None


def alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def record(event, **fields):
    with journal.open("a") as stream:
        stream.write(json.dumps(dict(event=event, pid=os.getpid(), **fields)) + "\n")


def reply(request, **result):
    try:
        print(json.dumps(dict(version=3, session=session, id=request["id"], **result)), flush=True)
    except BrokenPipeError:
        pass


def release(event):
    record(event)
    if mode != "uncooperative":

        def finish():
            record("released")
            if mode == "diagnostic-cleanup":
                print(json.dumps(dict(kind="fixture_cleanup", pid=os.getpid())), file=sys.stderr, flush=True)
            os._exit(0)

        threading.Timer(0.01, finish).start()


record("start", previousAlive=any(alive(item["pid"]) for item in prior if item["event"] == "start"))
signal.signal(signal.SIGTERM, lambda *_: release("sigterm"))
for line in sys.stdin:
    request = json.loads(line)
    if "cancel" in request:
        record("cancel", id=request["cancel"])
        if pending is None or request["cancel"] != pending["id"] or mode == "uncooperative":
            continue
        operation, pending = pending, None
        if mode == "corrupt-on-cancel":
            print(json.dumps(dict(version=3, session="wrong-session", id=operation["id"], result=[])), flush=True)
            continue

        def cleanup(operation=operation):
            record("cleanup", id=operation["id"])
            if mode == "late-success":
                reply(
                    operation,
                    result=dict(
                        target="Fixture input",
                        status="dispatched",
                        detail="Input completed before cancellation was observed.",
                        context=dict(after="fixture-input", scope="fixture-window", window="1"),
                    ),
                )
            else:
                reply(
                    operation,
                    error=dict(
                        code="deadline_exceeded",
                        target="Fixture",
                        effect="partial" if mode == "partial" else "none",
                        detail="Work deadline reached; native cleanup completed.",
                        next="Observe before another action; do not replay.",
                    ),
                )
            record("terminal", id=operation["id"])

        threading.Timer(0.08, cleanup).start()
        continue
    record(
        "request",
        id=request["id"],
        method=request["method"],
        timeoutMs=request.get("timeoutMs"),
        session=request.get("session"),
    )
    if request["method"] == "hello":
        if mode == "hello-hang":
            continue
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
        reply(
            request,
            result=dict(
                platform="fixture",
                desktopMode=request["params"]["desktopMode"],
                maxTimeoutMs=120000,
                capabilities=[c for c in capabilities if mode != "no-cancel" or c != "cancel"],
            ),
        )
    elif request["method"] == "listApps":
        reply(request, result=[dict(id=f"fixture.{os.getpid()}", displayName="Fixture", isRunning=True)])
    else:
        pending = request
        if request["method"] == "typeText":
            record("input", id=request["id"])
release("eof")
threading.Event().wait()
