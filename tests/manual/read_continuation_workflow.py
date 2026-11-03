# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Verify public SDK text, captured-detail and live-child continuations on owned UI.

Run after computer-use-build: uv run python tests/manual/read_continuation_workflow.py
Uses ordinary AppKit document controls and section buttons; no direct AX helper.
Only fixture setup/state and SDK process cleanup are outside the public interface.
"""

import argparse
import asyncio
import hashlib
import json
import logging
import os
import re
import signal
import tempfile
import uuid
from pathlib import Path

import computer_use
from artifact_json import encode_artifact
from computer_use import Computer
from computer_use.diagnostics import set_diagnostic_handler
from computer_use.paths import native_driver
from input_receiver_workflow import build_fixture, state
from write_stage_workflow import wait_state

ROOT = Path(__file__).resolve().parents[2]
LOG = logging.getLogger(__name__)


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def tree_cursor(text):
    header = re.search(r"(?m)^Element (\d+):$", text)
    assert header, text
    ref = header[1]
    patterns = (
        rf'{ref} children, continuation: node = app\.element\({ref}\); node\.read\(cursor="(p[1-9][0-9]*)"\)',
        rf'Children \[{ref}\]: cursor="(p[1-9][0-9]*)" \(live order\)',
    )
    matches = {match for pattern in patterns for match in re.findall(pattern, text)}
    assert len(matches) <= 1, (ref, matches)
    return next(iter(matches), None)


def button_ref(text, number):
    label = f"Review section {number:03}"
    matches = []
    for line in text.splitlines():
        for entry in line.split(" | "):
            match = re.fullmatch(r"\s*(\d+) (?:button(?: \([^)]*\))? )?" + label + r"(?:,.*)?", entry)
            if match:
                matches.append(int(match[1]))
    assert len(matches) == 1, (label, matches, text)
    return matches[0]


async def details(observation, *, length, first=None, expected=None, progress=None):
    page = first if first is not None else await asyncio.to_thread(observation.read_details, length=length)
    chunks, cursors, total = [], set(), page.total
    assert type(total) is int and total >= 0
    if expected is not None:
        assert len(expected) == total
    if progress is not None:
        progress.update(
            total=total,
            consumed=0,
            pages=0,
            unique_cursors=0,
            compared_with_reference=expected is not None,
            page_summaries=[],
        )
    consumed, request_cursor = 0, None
    # A nonterminal page must consume at least one character of this declared
    # immutable document. This gives a total-derived bound without assuming a
    # fixture-wide byte size or requiring every provider page to fill `length`.
    for _ in range(max(1, total)):
        assert page.total == total
        size = len(page.text)
        assert size <= min(length, total - consumed), ("Detail page exceeds remaining text", consumed, size, total)
        assert size > 0 or consumed == total, ("Detail page made no character progress", consumed, total)
        if expected is not None:
            assert page.text == expected[consumed : consumed + size], (
                "Detail page differs from its captured range",
                consumed,
            )
        chunks.append(page.text)
        if progress is not None:
            progress["page_summaries"].append(
                {
                    "cursor": request_cursor,
                    "next": page.next,
                    "start": consumed,
                    "characters": size,
                    "sha256": hashlib.sha256(page.text.encode()).hexdigest(),
                }
            )
        consumed += size
        if progress is not None:
            progress.update(consumed=consumed, pages=len(chunks), last_next=page.next)
        if page.next is None:
            value = "".join(chunks)
            assert consumed == len(value) == total, ("Detail cursor ended before declared total", consumed, total)
            return value, len(chunks)
        assert consumed < total, ("Detail cursor continued beyond declared total", consumed, total)
        assert page.next not in cursors, ("Repeated detail cursor", page.next)
        cursors.add(page.next)
        if progress is not None:
            progress["unique_cursors"] = len(cursors)
        request_cursor = page.next
        page = await asyncio.to_thread(observation.read_details, cursor=request_cursor, length=length)
    raise AssertionError("Captured details exceeded the declared-character bound")


async def verify(folder, selected_driver):
    bundle = "local.computer-use.read-continuation." + uuid.uuid4().hex
    driver = Path(selected_driver or native_driver()["command"]).resolve()
    sources = [Path(__file__).resolve(), ROOT / "tests/fixtures/ReadContinuationProbe.swift"]
    report = {
        "sdk": computer_use.__file__,
        "driver": str(driver),
        "driver_sha256": sha256(driver),
        "source_sha256": {str(path): sha256(path) for path in sources},
        "allow_global_input": False,
        "actions": [],
        "passed": False,
    }
    journal = (folder / "diagnostics.jsonl").open("w", encoding="utf-8")
    events, initial, computer = [], None, None

    def record(value):
        events.append(value)
        journal.write(json.dumps(value, ensure_ascii=False, default=str) + "\n")
        journal.flush()

    set_diagnostic_handler(record)
    try:
        await asyncio.to_thread(build_fixture, folder, bundle, "ReadContinuationProbe")
        initial = await state(folder)
        report["initial"] = initial
        expected = (folder / "source-document.txt").read_text(encoding="utf-8")
        assert len(expected.encode("utf-16-le")) // 2 > 16384 and "🙂" in expected
        report["document"] = {"characters": len(expected), "sha256": hashlib.sha256(expected.encode()).hexdigest()}
        computer = Computer(allow_global_input=False)
        computer._controller.transport.driver = {"command": str(driver), "args": []}
        app = computer.app(bundle)
        original = await asyncio.to_thread(app.read, view="both", full=True)
        (folder / "original.txt").write_text(original.text)
        if original.screenshot is not None:
            (folder / "original.png").write_bytes(original.screenshot)
        title = await asyncio.to_thread(app.element, [{"identifier": "review-title"}])
        document = await asyncio.to_thread(app.element, [{"identifier": "read-document"}])
        sections = await asyncio.to_thread(app.element, [{"identifier": "read-sections"}])
        # Retain a real captured-details cursor across native local reads and writes.
        detail_first = await asyncio.to_thread(original.read_details, length=1021)
        assert detail_first.next is not None, "The real document observation should span detail pages"
        report["detail_reference"] = {}
        detail_reference, _ = await details(original, length=16384, progress=report["detail_reference"])
        assert "Original review title" in detail_reference and "Unrelated draft" in detail_reference
        (folder / "captured-details-reference.txt").write_text(detail_reference)

        first_page = await asyncio.to_thread(sections.read)
        (folder / "sections-1.txt").write_text(first_page.text)
        original_button = await asyncio.to_thread(app.element, button_ref(first_page.text, 1))
        cursor = tree_cursor(first_page.text)
        assert cursor is not None, "140 actual section children must expose a live continuation"
        first_labels = {int(value) for value in re.findall(r"Review section (\d{3})", first_page.text)}
        assert first_labels == set(range(1, len(first_labels) + 1)), first_labels
        section_pages, seen_labels, seen_cursors = [sorted(first_labels)], set(first_labels), set()

        page = await asyncio.to_thread(document.read_text, length=1023)
        chunks, text_pages, text_cursors = [], [], set()
        for _ in range(200):
            assert page.total == len(expected) and len(page.text) <= 1023
            chunks.append(page.text)
            text_pages.append({"characters": len(page.text), "total": page.total, "next": page.next})
            if page.next is None:
                break
            assert page.next not in text_cursors
            text_cursors.add(page.next)
            page = await asyncio.to_thread(document.read_text, cursor=page.next, length=1023)
        else:
            raise AssertionError("Text continuation did not finish within the fixture bound")
        assert len(text_pages) > 1 and "".join(chunks) == expected
        report["text_pages"] = text_pages
        (folder / "read-document.txt").write_text("".join(chunks))

        # The live relation cursor must survive the unrelated document capture.
        last_button = None
        # 128 is a page limit, not a promised page size: provider time/node budgets
        # may return a shorter prefix. Follow the actual public cursor, boundedly.
        for number in range(2, 202):
            assert cursor not in seen_cursors, "A live relation cursor made no progress"
            seen_cursors.add(cursor)
            following = await asyncio.to_thread(sections.read, cursor=cursor)
            (folder / f"sections-{number}.txt").write_text(following.text)
            labels = {int(value) for value in re.findall(r"Review section (\d{3})", following.text)}
            assert labels and not labels.intersection(seen_labels), (seen_labels, labels)
            assert labels == set(range(len(seen_labels) + 1, len(seen_labels) + len(labels) + 1)), labels
            seen_labels.update(labels)
            section_pages.append(sorted(labels))
            if 140 in labels:
                last_button = await asyncio.to_thread(app.element, button_ref(following.text, 140))
            cursor = tree_cursor(following.text)
            if cursor is None:
                break
        else:
            raise AssertionError("Live relation did not finish within the fixture bound")
        assert seen_labels == set(range(1, 141)) and last_button is not None
        report["section_pages"] = section_pages
        after_reads = await state(folder)
        report["after_reads"] = after_reads
        assert after_reads["reviewed"] == {} and after_reads["title"] == initial["title"]
        assert (
            after_reads["guard"] == initial["guard"] and after_reads["documentSHA256"] == report["document"]["sha256"]
        )
        assert after_reads["frontmostPID"] == initial["frontmostPID"] and after_reads["cursor"] == initial["cursor"]
        effects = [
            entry["value"]
            for entry in events
            if entry.get("kind") == "backend_diagnostic"
            and isinstance(entry.get("value"), dict)
            and entry["value"].get("kind") == "input_activity"
            and entry["value"].get("phase") == "willCall"
        ]
        assert not effects, ("Pure read chain invoked an input mechanism", effects)

        # No second lookup of the first button: use the Element retained on page one.
        report["actions"].append(
            {"name": "retained first section", "receipt": dict(await asyncio.to_thread(original_button.click))}
        )
        report["actions"][-1]["state"] = await wait_state(folder, lambda value: value["reviewed"] == {"001": 1})
        report["actions"].append(
            {"name": "new-page last section", "receipt": dict(await asyncio.to_thread(last_button.click))}
        )
        report["actions"][-1]["state"] = await wait_state(
            folder, lambda value: value["reviewed"] == {"001": 1, "140": 1}
        )
        report["actions"].append(
            {
                "name": "retained title",
                "receipt": dict(await asyncio.to_thread(title.type_text, "Updated review title", replace=True)),
            }
        )
        final = await wait_state(folder, lambda value: value["title"] == "Updated review title")
        report["actions"][-1]["state"] = final
        report["detail_continuation"] = {}
        captured, detail_pages = await details(
            original,
            length=16384,
            first=detail_first,
            expected=detail_reference,
            progress=report["detail_continuation"],
        )
        assert captured == detail_reference and "Updated review title" not in captured
        report["detail_pages"] = detail_pages
        report["captured_details_immutable"] = True
        fresh = await asyncio.to_thread(title.read)
        (folder / "title-after.txt").write_text(fresh.text)
        assert "Updated review title" in fresh.text
        for key in (
            "pid",
            "windowNumber",
            "windowObjectID",
            "documentObjectID",
            "sectionGroupObjectID",
            "guard",
            "documentSHA256",
        ):
            assert final[key] == initial[key], (key, initial[key], final[key])
        assert final["reviewed"] == {"001": 1, "140": 1}
        report["final"] = final
        report["passed"] = True
    except BaseException as error:
        report["error"] = {"type": type(error).__name__, "detail": str(error)}
        raise
    finally:
        if computer is not None:
            await asyncio.to_thread(computer.close)
        set_diagnostic_handler(None)
        journal.close()
        if initial is not None:
            os.kill(initial["pid"], signal.SIGTERM)
        report["source_drift"] = [str(path) for path in sources if sha256(path) != report["source_sha256"][str(path)]]
        (folder / "result.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2, default=encode_artifact) + "\n"
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--driver", type=Path)
    options = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    (ROOT / ".artifacts").mkdir(exist_ok=True)
    folder = Path(tempfile.mkdtemp(prefix="read-continuation-", dir=ROOT / ".artifacts"))
    try:
        asyncio.run(verify(folder, options.driver))
    finally:
        LOG.info("Public SDK read continuation evidence: %s", folder)


if __name__ == "__main__":
    main()
