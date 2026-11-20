# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Compare two SDK source trees with the same no-desktop macOS protocol-v6 fixture.

Run this file with the project venv's Python and pass --baseline, --candidate,
and a fresh --output directory. The baseline must be the frozen migration source,
with its matching compiled macOS read extension. Both trees run in clean child
working directories. No test invokes the installed platform driver.
"""

import argparse
import asyncio
import json
import math
import os
import subprocess
import sys
from collections.abc import Mapping
from datetime import timedelta
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
DRIVER = FIXTURES / "sdk_migration_replay_driver.py"
CANCELLATION = FIXTURES / "sdk_migration_replay_cancellation.py"
STEPS = {
    "bind": "apps = computer.list_apps()\napp = computer.app(apps[0].id)",
    "full": "before = app.read(view='tree', full=True)",
    "details": "details = before.read_details(element=1, length=16384)\nprint(details.text)",
    "write": "receipt = app.element(1).perform_action('AXPress')",
    "read again": "again = app.read(view='tree')",
    "read a second time": "final = app.read(view='tree')",
}
NEGATIVES = ("reorder", "skip", "duplicate", "early-reuse")


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def lines(path):
    return [json.loads(line) for line in path.read_bytes().splitlines()] if path.exists() else []


def schedule(negative):
    steps = list(STEPS.items())
    if negative == "reorder":
        steps[3], steps[4] = steps[4], steps[3]
    elif negative == "skip":
        steps.pop()
    elif negative == "duplicate":
        steps.append(steps[-1])
    elif negative == "early-reuse":
        steps[-1] = (steps[-1][0], "final = again\nprint(str(final.text))")
    return steps


def startup(source, out, tape):
    args = [str(DRIVER), str(out / "wire.jsonl")]
    if tape is not None:
        args.append(str(tape))
    return (
        "from pathlib import Path\n"
        "import hashlib, json\n"
        "import computer_use, computer_use_mcp\n"
        "from computer_use.native import connection\n"
        "from computer_use.interaction.read.accessibility._native import require_read_engine\n"
        f"root = Path({str(source)!r}).resolve()\n"
        "sdk = Path(computer_use.__file__).resolve().parent\n"
        "mcp = Path(computer_use_mcp.__file__).resolve().parent\n"
        "ext = Path(require_read_engine().__file__).resolve()\n"
        "assert sdk == root / 'computer_use_engine', (sdk, root)\n"
        "assert mcp == root / 'interface_adapter/mcp', (mcp, root)\n"
        "assert ext.is_relative_to(sdk), (ext, sdk)\n"
        "loaded = dict(sdk=str(sdk), mcp=str(mcp), extension=str(ext),\n"
        "              extensionSha256=hashlib.sha256(ext.read_bytes()).hexdigest())\n"
        f"connection.native_driver = lambda: {{'command': {sys.executable!r}, 'args': {args!r}}}\n"
    )


def public_value(value):
    if isinstance(value, Mapping):
        return {key: public_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [public_value(item) for item in value]
    if isinstance(value, bytearray):
        return bytes(value).hex()
    return value


def direct_step(namespace, name):
    if name == "bind":
        return [{"id": item.id, "name": item.display_name, "running": item.is_running} for item in namespace["apps"]]
    if name == "details":
        page = namespace["details"]
        assert page.next is None, "fixture detail page unexpectedly truncated"
        return {"text": page.text, "total": page.total, "next": page.next}
    key = {"full": "before", "write": "receipt", "read again": "again", "read a second time": "final"}[name]
    return public_value(namespace[key])


def direct_child(source, out, tape, negative):
    from computer_use import Computer, ComputerUseError

    namespace = {}
    exec(startup(source, out, tape), namespace)  # noqa: S102 - generated from this fixture and quoted paths
    outputs = []
    with Computer(allow_global_input=False) as computer:
        namespace["computer"] = computer
        for name, code in schedule(negative):
            try:
                exec(code, namespace)  # noqa: S102 - fixed fixture tool calls
                outputs.append({"step": name, "value": direct_step(namespace, name)})
            except ComputerUseError as error:
                outputs.append(
                    {"step": name, "error": {"code": error.code, "detail": error.detail, "effect": error.effect}}
                )
                break
    save(out / "loaded.json", namespace["loaded"])
    save(out / "outputs.json", outputs)


def environment(source, out):
    return {
        **os.environ,
        "PYTHONPATH": str(source),
        "PYTHONHASHSEED": "0",
        "COMPUTER_USE_LOG_DIR": str(out / "logs"),
        "COMPUTER_USE_PACKAGES_DIR": str(out / "packages"),
    }


def canonical_result(result, out):
    value = result.model_dump(mode="json", by_alias=True, exclude_none=True)
    metadata = value["_meta"]["computer-use"]
    journal = Path(metadata["logPath"]).resolve(strict=True)
    assert journal.parent == (out / "logs").resolve() and journal.is_file(), journal
    elapsed = metadata["durationMs"]
    assert type(elapsed) in (int, float) and math.isfinite(elapsed) and elapsed >= 0, elapsed
    metadata.update(logPath="<tool-journal>", durationMs="<elapsed-ms>")
    return value, journal


def stable_events(journal):
    fields = {
        "command_start": ("id", "method", "params"),
        "command_end": ("id", "method", "status"),
        "observation_captured": ("scope", "view", "elementRefs", "observed", "presentation", "presentationPrepared"),
        "observation": ("scope", "view", "elementRefs", "observed", "presentation", "textMaterialized"),
        "observation_member_coverage": ("windows",),
        "observation_member_delivery": ("component", "windows"),
        "observation_delivery_plan": ("scope", "mode", "reason", "trees"),
    }
    return [
        {key: event[key] for key in ("executionId", "kind", *fields[event["kind"]]) if key in event}
        for event in lines(journal)
        if event["kind"] in fields
    ]


async def mcp_transcript(source, out, tape):
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "computer_use_mcp", "--no-allow-global-input"],
        cwd=out / "cwd",
        env=environment(source, out),
    )
    outputs = []
    with (out / "stderr.log").open("w", encoding="utf-8") as errors:
        async with (
            stdio_client(params, errlog=errors) as (read, write),
            ClientSession(read, write, read_timeout_seconds=timedelta(seconds=20)) as client,
        ):
            await client.initialize()
            setup = await client.call_tool(
                "computer_use",
                {
                    "title": "Configure synthetic driver",
                    "code": startup(source, out, tape) + "print(json.dumps(loaded))",
                },
            )
            assert not setup.isError, setup.content
            save(out / "loaded.json", json.loads(setup.content[0].text))
            for name, code in STEPS.items():
                result = await client.call_tool("computer_use", {"title": name, "code": code})
                value, journal = canonical_result(result, out)
                outputs.append({"step": name, "value": value})
                assert not result.isError, (name, result.content)
    save(out / "outputs.json", outputs)
    save(out / "events.json", stable_events(journal))


def run_direct(source, out, tape, negative=None):
    args = [sys.executable, str(Path(__file__).resolve()), "--direct", str(source), str(out)]
    args.extend((str(tape) if tape is not None else "-", negative or "-"))
    with (out / "stderr.log").open("wb") as errors, (out / "stdout.log").open("wb") as output:
        completed = subprocess.run(
            args,
            cwd=out / "cwd",
            env=environment(source, out),
            check=False,
            timeout=30,
            stdout=output,
            stderr=errors,
        )
    assert completed.returncode == 0, (out / "stderr.log").read_text(encoding="utf-8")


def verify_wire(out, expected):
    actual = lines(out / "wire.jsonl")
    if (out / "wire.mismatch").exists():
        request = load(out / "wire.mismatch")
        raise AssertionError(f"Native request mismatch at ID {request.get('id', request.get('cancel'))}")
    if actual != expected:
        raise AssertionError(f"Native request tape did not complete ({len(actual)} / {len(expected)} records).")


def verify_flow(out, layer):
    wire = lines(out / "wire.jsonl")
    assert [request["id"] for request in wire] == list(range(len(wire))), wire
    (execute,) = [request for request in wire if request["method"] == "execute"]
    assert execute["params"]["command"]["kind"] == "native_action", execute
    assert execute["params"]["command"]["target"]["node"] == "button-node", execute
    capture_ids = [
        request["id"]
        for request in wire
        if request["method"] == "inspect" and request["params"]["query"]["kind"] == "nodes"
    ]
    before, post, again, final = capture_ids
    assert before < execute["id"] < post < again < final
    outputs = {item["step"]: item["value"] for item in load(out / "outputs.json")}

    def assert_capture(step, capture_id, writes):
        text = json.dumps(outputs[step], ensure_ascii=False)
        assert f"write {writes}, capture {10 * (capture_id + 1)}" in text, step

    assert_capture("full", before, 0)
    assert_capture("write", post, 1)
    assert_capture("read again", again, 1)
    assert_capture("read a second time", final, 1)
    assert "Fixture action dispatched once." in json.dumps(outputs["write"])
    assert "Run fixture action" in json.dumps(outputs["details"])
    if layer == "sdk":
        receipt = outputs["write"]
        assert receipt["status"] == "dispatched" and receipt["observation_status"] == "available"
        assert receipt["window_lifecycle"] == "observed" and receipt["observation_error"] is None
    else:
        write = json.dumps(outputs["write"])
        assert write.index("Fixture action dispatched once.") < write.index(f"capture {10 * (post + 1)}")
        assert "Tree: full" in json.dumps(outputs["full"])
        for step in ("write", "read again", "read a second time"):
            assert "Tree: diff" in json.dumps(outputs[step]), step
    return len(wire)


def cancel_transcript(source, out, layer):
    args = [sys.executable, str(CANCELLATION)]
    if layer == "sdk":
        args.append("sdk")
    args.extend((str(source), str(out)))
    error_path = out.with_suffix(".stderr.log")
    with error_path.open("wb") as errors:
        completed = subprocess.run(
            args, cwd=out.parent / "cwd", env=environment(source, out), check=False, timeout=40, stderr=errors
        )
    assert completed.returncode == 0, error_path.read_text(encoding="utf-8")
    proof = load(out / "proof.json")
    proof["wire"] = {
        path.name: [{key: value for key, value in item.items() if key != "timeoutMs"} for item in lines(path)]
        for path in sorted(out.glob("*.wire.jsonl"))
    }
    save(out / "canonical.json", proof)


def compare(reference, current):
    if reference.read_bytes() != current.read_bytes():
        raise AssertionError(f"{current} differs from the frozen baseline {reference}.")


def verify(baseline, candidate, output):
    output.mkdir(mode=0o700)
    report = {}
    for layer in ("sdk", "mcp"):
        tape = output / f"{layer}.tape.json"
        artifacts = ["outputs.json", "cancellation/canonical.json"]
        if layer == "mcp":
            artifacts.append("events.json")
        for arm, source in (("a1", baseline), ("a2", baseline), ("b", candidate)):
            out = output / f"{layer}-{arm}"
            (out / "cwd").mkdir(parents=True, mode=0o700)
            replay = tape if arm != "a1" else None
            if layer == "sdk":
                run_direct(source, out, replay)
            else:
                asyncio.run(mcp_transcript(source, out, replay))
            if arm == "a1":
                save(tape, lines(out / "wire.jsonl"))
            verify_wire(out, load(tape))
            report[layer] = verify_flow(out, layer)
            cancel_transcript(source, out / "cancellation", layer)
            if arm != "a1":
                a1 = output / f"{layer}-a1"
                for artifact in artifacts:
                    compare(a1 / artifact, out / artifact)

    tape = output / "sdk.tape.json"
    reference = load(output / "sdk-a1/outputs.json")
    report["negative_controls"] = {}
    for negative in NEGATIVES:
        out = output / f"negative-{negative}"
        (out / "cwd").mkdir(parents=True, mode=0o700)
        run_direct(baseline, out, tape, negative)
        try:
            verify_wire(out, load(tape))
        except AssertionError as error:
            report["negative_controls"][negative] = str(error)
        else:
            raise AssertionError(f"Strict replay accepted {negative}.")
        outputs = load(out / "outputs.json")
        assert outputs != reference, negative
        if negative == "early-reuse":
            assert outputs[-1]["value"] == outputs[-2]["value"] and outputs[-1]["step"] == reference[-1]["step"]
    save(output / "report.json", report)
    sys.stdout.write(json.dumps(report, ensure_ascii=True, indent=2) + "\n")


def main():
    if len(sys.argv) == 6 and sys.argv[1] == "--direct":
        source, out, tape, negative = sys.argv[2:]
        direct_child(
            Path(source), Path(out), None if tape == "-" else Path(tape), None if negative == "-" else negative
        )
        return

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", required=True, type=Path)
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    verify(args.baseline.resolve(strict=True), args.candidate.resolve(strict=True), args.output.resolve())


if __name__ == "__main__":
    main()
