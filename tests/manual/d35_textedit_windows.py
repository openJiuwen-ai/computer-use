# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""D35: two owned TextEdit documents, exact input and no context-switch reads.

Run on macOS with the local driver built. Creates disposable files, opens only
those files, and closes only their observed close buttons after saving. The
independent file contents verify which document actually received each input.
"""

import hashlib
import json
import logging
import re
import subprocess
import tempfile
import time
from pathlib import Path

import computer_use
from artifact_json import encode_artifact
from computer_use import Computer
from computer_use.diagnostics import set_diagnostic_handler
from computer_use.paths import native_driver
from d34_window_workflow import source_fingerprint

ROOT = Path(__file__).resolve().parents[2]
LOG = logging.getLogger(__name__)


def header(observation):
    text, cursor = "", None
    for _ in range(16):
        page = observation.read_details(cursor=cursor, length=16384)
        text += page.text
        marker = "Captured interface metadata: "
        if marker in text:
            try:
                return json.JSONDecoder().raw_decode(text.split(marker, 1)[1])[0]
            except json.JSONDecodeError:
                pass
        cursor = page.next
        if cursor is None:
            break
    raise AssertionError("The actual delivered observation did not identify its windows")


def verify(folder):
    files = {name: folder / f"D35-{folder.name}-{name}.txt" for name in ("A", "B")}
    for name, path in files.items():
        path.write_text(f"Owned D35 document {name}")
    driver = Path(native_driver()["command"])
    report = {
        "passed": False, "sdk": computer_use.__file__, "driver": str(driver),
        "driver_sha256": hashlib.sha256(driver.read_bytes()).hexdigest(),
        "source_before": source_fingerprint(), "calls": [], "windows": {},
    }
    computer, saved, closers = None, False, {}
    journal = (folder / "diagnostics.jsonl").open("w")

    def diagnostic(value):
        journal.write(json.dumps(value, ensure_ascii=False, default=encode_artifact) + "\n")

    def call(label, operation):
        start = time.monotonic()
        result = operation()
        report["calls"].append({"label": label, "seconds": time.monotonic() - start})
        observation = getattr(result, "observation", None)
        if observation is None and hasattr(result, "screenshot"):
            observation = result
        if observation is not None:
            (folder / (label + ".txt")).write_text(observation.text)
            if observation.screenshot:
                (folder / (label + ".png")).write_bytes(observation.screenshot)
        if hasattr(result, "impact"):
            report["calls"][-1]["impact"] = result.impact
        return result

    try:
        subprocess.run(["open", "-a", "TextEdit", *(str(path) for path in files.values())], check=True)
        computer = Computer()
        set_diagnostic_handler(diagnostic)
        app = computer.app("com.apple.TextEdit")
        observed = call("01-directory", app.read)
        windows = header(observed)["windows"]
        for name, path in files.items():
            matches = [item for item in windows if item["title"] in (path.name, path.stem)]
            assert len(matches) == 1, (name, windows)
            report["windows"][name] = matches[0]["ref"]
        a, b = (int(report["windows"][name]) for name in ("A", "B"))
        # A's root is known from the catalogue; no preceding A read is required.
        field_a = call("02-query-A-without-read", lambda: app.element([{"role": "textArea"}], window=a))
        images, fields = {}, {"A": field_a}
        refs = {}
        for name, number in (("A", a), ("B", b)):
            observed = call("03-view-" + name, lambda number=number: app.read(window=number, view="both"))
            images[name] = observed
            matches = re.findall(r"(?m)^\s*(\d+) (?:text area|文本输入区)\b", observed.text)
            closes = re.findall(r"(?m)^\s*(\d+) close button\b", observed.text)
            assert len(matches) == len(closes) == 1, observed.text
            refs[name] = int(matches[0])
            fields[name] = app.element(refs[name], window=number)
            closers[name] = app.element(int(closes[0]))
        assert fields["A"]._snapshot["binding"] == field_a._snapshot["binding"]
        for name in ("A", "B"):
            call("04-replace-" + name, lambda name=name: fields[name].type_text("Saved D35 " + name, replace=True))
        # B was most recently delivered. Each explicit A/B command pins its
        # receiver before preparation, with no read to switch the default.
        call("05-return-A-after-B", lambda: app.press_key("Return", window=a))
        call("06-return-B-after-A", lambda: app.press_key("Return", window=b))
        call("07-save-A-after-B", lambda: app.press_key("super+s", window=a))
        call("08-save-B-after-A", lambda: app.press_key("super+s", window=b))
        expected = {name: "Saved D35 " + name + "\n" for name in ("A", "B")}
        for _ in range(50):
            actual = {name: path.read_text() for name, path in files.items()}
            if actual == expected:
                break
            time.sleep(0.05)  # File persistence oracle, no additional SDK observation/input.
        report["saved_contents"] = actual
        assert actual == expected, (actual, expected)
        saved = True
        assert app.element(refs["A"]).read_text().text == expected["A"]
        report["context_switch_public_reads"] = 0
        report["explicit_keyboard_commands"] = 4
        report["passed"] = True
    except Exception as error:
        report["error"] = {"type": type(error).__name__, "detail": str(error)}
        raise
    finally:
        if saved:
            for name, closer in closers.items():
                call("09-close-owned-" + name, closer.click)
        if computer is not None:
            computer.close()
        set_diagnostic_handler(None)
        journal.close()
        report["source_after"] = source_fingerprint()
        report["source_unchanged"] = report["source_before"] == report["source_after"]
        report["passed"] = report["passed"] and report["source_unchanged"]
        (folder / "result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, default=encode_artifact))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    (ROOT / ".artifacts").mkdir(exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix="d35-textedit-", dir=ROOT / ".artifacts"))
    try:
        verify(directory)
    finally:
        LOG.info("D35 TextEdit evidence: %s", directory)
