# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""An isolated Edge profile and a local contenteditable page with an out-of-band oracle."""

import json
import os
import subprocess
import threading
import time
from contextlib import contextmanager
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from computer_use_mcp.windows_worker import WindowsJob, resume_suspended

SCOPE_COLORS = {"target": (13, 237, 101), "companion": (226, 17, 193)}

PAGE = """<!doctype html>
<html lang="en">
<meta charset="utf-8">
<title>{title}</title>
<style>
body {{font: 22px Segoe UI,sans-serif; margin: 28px;}}
[contenteditable] {{border: 2px solid #596678; padding: 14px; min-height: 80px; white-space: pre-wrap;}}
button {{font: inherit; margin-top: 12px;}}
</style>
<h1>Draft worksheet</h1>
{scope_marker}
<p id="label">Draft editor</p>
<div id="draft" role="textbox" aria-multiline="true" aria-labelledby="label"
     contenteditable="true">Starting draft</div>
<p><label>Bystander <input id="other" value="untouched"></label></p>
<button id="apply" type="button">Apply draft</button>
<p role="status" id="status">Not applied</p>
<script>
const draft = document.getElementById("draft"), other = document.getElementById("other");
const status = document.getElementById("status");
let applied = 0, saved = null, sequence = 0;
const events = [];
function snapshot() {{
    fetch("/state", {{method: "POST", body: JSON.stringify({{
        text: draft.innerText, other: other.value, applied, saved, events, sequence: ++sequence
    }})}});
}}
draft.addEventListener("input", event => {{ events.push(event.inputType); snapshot(); }});
other.addEventListener("input", snapshot);
document.getElementById("apply").addEventListener("click", () => {{
    applied++;
    saved = draft.innerText;
    status.textContent = "Applied " + applied + ": " + saved;
    snapshot();
}});
{scope_events}
snapshot();
</script>
</html>
"""


def scope_marker(role: str, nonce: str) -> str:
    return f"CUA_SCOPE_{role.upper()}_{nonce}"


@contextmanager
def browser_fixture(nonce: str, directory: Path, *, title: str | None = None, scope: str | None = None):
    edge = Path(os.environ["ProgramFiles(x86)"]) / "Microsoft/Edge/Application/msedge.exe"
    if not edge.is_file():
        raise FileNotFoundError("Microsoft Edge executable was not found")
    directory.mkdir(parents=True, exist_ok=True)
    state: dict = {}
    lock = threading.Lock()
    title = title or f"CUA Browser Draft {nonce}"
    marker, events = "", ""
    if scope is not None:
        color = "#" + "".join(f"{channel:02x}" for channel in SCOPE_COLORS[scope])
        marker = (
            f'<div style="position:fixed; right:24px; top:24px; width:64px; height:64px; background:{color}"></div>'
            f'<p>{escape(scope_marker(scope, nonce))}</p>'
        )
        events = """
for (const kind of ["pointerdown", "keydown"]) {
    document.addEventListener(kind, event => {
        events.push("scope:" + event.type + ":" + event.target.id);
        snapshot();
    }, true);
}
"""
    page = PAGE.format(title=escape(title), scope_marker=marker, scope_events=events).encode("utf-8")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, _format, *_args):
            return

        def do_GET(self):
            if self.path != "/":
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(page)))
            self.end_headers()
            self.wfile.write(page)

        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            if self.path != "/state" or not 0 < length <= 65536:
                self.send_error(400)
                return
            try:
                value = json.loads(self.rfile.read(length))
            except ValueError:
                self.send_error(400)
                return
            if not isinstance(value, dict) or not isinstance(value.get("sequence"), int):
                self.send_error(400)
                return
            with lock:
                if value["sequence"] > state.get("sequence", 0):
                    state.update(value)
            self.send_response(204)
            self.end_headers()

    def read_state() -> dict:
        with lock:
            return dict(state)

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        job = WindowsJob()
        process = None
        try:
            url = f"http://127.0.0.1:{server.server_port}/"
            process = subprocess.Popen(
                [
                    str(edge), f"--user-data-dir={directory / 'edge-profile'}", "--no-first-run",
                    "--disable-sync", "--disable-background-networking", f"--app={url}",
                ],
                creationflags=4,  # Assign the disposable process tree before it can start children.
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            job.assign(process.pid)
            resume_suspended(process.pid)
            deadline = time.monotonic() + 20
            while not read_state() and time.monotonic() < deadline and process.poll() is None:
                time.sleep(0.05)
            if read_state().get("text") != "Starting draft":
                raise RuntimeError("The local contenteditable page did not reach its initial state")
            yield title, read_state
        finally:
            job.close()
            if process is not None:
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=10)
            server.shutdown()
            server_thread.join(timeout=5)
