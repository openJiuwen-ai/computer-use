# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Verify exact native actions using real AX callbacks in a disposable window.

Run after computer-use-build: uv run python tests/manual/explicit_action_workflow.py
No mouse or keyboard input is permitted. --baseline defaults to the known-blocked
pre-fix subset, rather than the complete matrix; --only selects specific cases.
Each report identifies the loaded Python policy and native driver.
Source hashes describe the workspace at report time. For a retained baseline
driver, its build-source provenance is recorded in baseline_source_manifest.
Standard actions require a successful API receipt. For the legacy provider's
arbitrary raw action, AppKit returns -25205 after running its callback: those
cases require execution_unknown/possible and one callback and effect, with no
replay. They are recorded separately from API successes.
"""

import argparse
import asyncio
import hashlib
import json
import logging
import os
import signal
import tempfile
import uuid
from pathlib import Path

import computer_use
import computer_use.interaction.write.progress as write_progress
import computer_use.interaction.write.routes.policy as write_policy
from artifact_json import encode_artifact
from computer_use import Computer
from computer_use.diagnostics import set_diagnostic_handler
from computer_use.errors import ComputerUseError
from computer_use.paths import native_driver
from input_receiver_workflow import build_fixture, state
from write_stage_workflow import wait_state

ROOT = Path(__file__).resolve().parents[2]
LOG = logging.getLogger(__name__)
CASES = [
    "changing",
    "missing",
    "standard",
    "large",
    "description_vanishes",
    "unique_alias",
    "duplicate_alias",
    "unknown_alias",
    "duplicate_name",
    "disappearance",
    "within",
    "modal",
    "slow",
    "alias_transfer",
    "duplicate_at_dispatch",
    "duplicate_name_alias",
    "standard_missing",
    "standard_large",
    "standard_vanishes",
    "press_duplicate",
]
BASELINE_CASES = [
    "changing",
    "standard",
    "large",
    "description_vanishes",
    "standard_large",
    "standard_vanishes",
]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def native_source_digest():
    digest = hashlib.sha256()
    for path in sorted((ROOT / "os_adapter/macos/Sources").rglob("*.swift")):
        digest.update(str(path.relative_to(ROOT)).encode() + b"\0" + path.read_bytes() + b"\0")
    return digest.hexdigest()


async def verify(folder, baseline, driver=None, cases=None):
    bundle = "local.computer-use.explicit-action." + uuid.uuid4().hex
    build_fixture(folder, bundle, "ExplicitActionProbe")
    initial = await state(folder)
    computer = Computer(allow_global_input=False)
    driver = Path(driver if driver is not None else native_driver()["command"]).resolve()
    computer._controller.transport.driver = {"command": str(driver), "args": []}
    events, actions, unexpected_input = [], [], []
    journal = (folder / "diagnostics.jsonl").open("w", encoding="utf-8")

    def record(value):
        events.append(value)
        journal.write(json.dumps(value, ensure_ascii=False, default=str) + "\n")
        journal.flush()
        effect = value.get("value") if value.get("kind") == "backend_diagnostic" else None
        if (
            isinstance(effect, dict)
            and effect.get("kind") == "input_activity"
            and effect.get("phase") == "willCall"
            and effect.get("device") in ["mouse", "keyboard"]
        ):
            unexpected_input.append(effect)
            computer._controller.transport.close()

    async def command(kind="configure", mode=None):
        identity = uuid.uuid4().hex
        (folder / "command.json").write_text(json.dumps({"id": identity, "kind": kind, "mode": mode}))

        def applied(value):
            if value["lastCommand"] != identity:
                return False
            if kind == "configure":
                return value["inOriginalWindow"] and not value["sheetAttached"]
            if kind == "modal":
                return value["sheetAttached"]
            if kind == "move":
                return not value["inOriginalWindow"]
            return True

        return await wait_state(folder, applied)

    pending_mutation = None
    request = computer._controller.transport.request

    async def mutate_before_dispatch(method, params, options=None):
        nonlocal pending_mutation
        if method == "execute" and params.get("command", {}).get("kind") == "native_action" and pending_mutation:
            mutation, pending_mutation = pending_mutation, None
            await command(mutation)
        return await request(method, params, options)

    computer._controller.transport.request = mutate_before_dispatch
    sources = [
        ROOT / "os_adapter/macos/Sources/Objects/NativeAction.swift",
        ROOT / "os_adapter/macos/Sources/Control/NativeElementControl.swift",
        ROOT / "os_adapter/macos/Sources/Control/NativeActionUnit.swift",
        ROOT / "os_adapter/macos/Sources/Control/InputContext/Passes/SampleNativeInputRoutes.swift",
        ROOT / "os_adapter/macos/Sources/Control/Action/NativeObjectActionPipeline.swift",
        ROOT / "os_adapter/macos/Sources/Control/Action/NativeObjectActionModel.swift",
        ROOT / "os_adapter/macos/Sources/Control/Action/NativeObjectActionPolicy.swift",
        ROOT / "os_adapter/macos/Sources/Control/Action/Passes/ResolveNativeObjectAction.swift",
        ROOT / "os_adapter/macos/Sources/Control/NativeApplicationContextUnit.swift",
        ROOT / "os_adapter/macos/Sources/Control/Prepare/DocumentAction/NativeDocumentActionPreparationPipeline.swift",
        ROOT / "os_adapter/macos/Sources/Control/Prepare/DocumentAction/Passes/PrepareActionDocumentContext.swift",
        ROOT / "os_adapter/macos/Sources/Control/Prepare/DocumentAction/Passes/PrepareActionDocumentForeground.swift",
        ROOT / "os_adapter/macos/Sources/Control/Prepare/DocumentAction/Passes/ConfirmActionDocumentContext.swift",
        ROOT / "os_adapter/macos/Sources/Control/InputContext/NativeActionDocumentContexts.swift",
        ROOT / "os_adapter/macos/Sources/Contracts/NativeActionDocumentContract.swift",
        ROOT / "os_adapter/macos/Sources/Control/Routes/Passes/BuildNativeMenuRoute.swift",
        ROOT / "os_adapter/macos/Sources/Contracts/NativeObjectAccess.swift",
        ROOT / "os_adapter/macos/Sources/Objects/NativeApplicationAccess.swift",
        ROOT / "tests/fixtures/ExplicitActionProbe.swift",
        Path(write_progress.__file__),
        Path(write_policy.__file__),
        Path(__file__).resolve(),
    ]
    report = {
        "sdk": computer_use.__file__,
        "driver": str(driver),
        "driver_sha256": sha(driver),
        "source_sha256": {str(path): sha(path) for path in sources},
        "native_source_tree_sha256": native_source_digest(),
        "baseline": baseline,
        "global_input_allowed": False,
        "source_provenance": "source_sha256 and native_source_tree_sha256 describe the current workspace at report time; they do not establish the source used to build a retained driver. Python hashes identify the loaded runtime sources.",
        "baseline_source_manifest": str(ROOT / ".artifacts/explicit-action-baseline-driver/metadata.json")
        if baseline
        else None,
        "direct_native_comparison": str(ROOT / ".artifacts/explicit-action-direct-pl8l2hdp/result.json"),
        "direct_native_metadata": str(ROOT / ".artifacts/explicit-action-direct-pl8l2hdp/metadata.json"),
        "receipt_classification": {
            "standard": "AXShowAlternateUI must return API success and execute exactly once.",
            "automatic_click_neighbor": "press_duplicate is an existing automatic click regression: one AXPress callback, successful API receipt, no mouse input.",
            "custom_raw": "Legacy AppKit bridge returns AX -25205 after callback; require execution_unknown/possible, one callback, one native effect, no replay.",
            "slow_custom_raw": "Require execution_unknown/possible and one callback/effect; timeout_observed is true only for native AX cannotComplete (-25204).",
        },
        "api_success": [],
        "callback_confirmed": [],
        "native_error_cases": [],
        "actions": actions,
        "unexpected_input": unexpected_input,
    }
    set_diagnostic_handler(record)
    try:
        app = computer.app(bundle)
        for case in cases or (BASELINE_CASES if baseline else CASES):
            mode = {
                "description_vanishes": "stable",
                "disappearance": "stable",
                "within": "stable",
                "modal": "stable",
                "alias_transfer": "unique_alias",
                "duplicate_at_dispatch": "stable",
                "duplicate_name_alias": "duplicate_name",
                "standard_vanishes": "standard_stable",
            }.get(case, case)
            await command(mode=mode)
            observation = await app.read()
            (folder / (case + "-before.txt")).write_text(observation.text)
            target = await app.element("Explicit action target")
            before = await state(folder)
            assert not before["performed"] and before["inOriginalWindow"] and not before["sheetAttached"], before
            pending_mutation = {
                "description_vanishes": "hide_description",
                "disappearance": "disappear",
                "within": "move",
                "modal": "modal",
                "alias_transfer": "alias_transfer",
                "duplicate_at_dispatch": "duplicate_at_dispatch",
                "standard_vanishes": "hide_description",
            }.get(case)
            if case in ["unique_alias", "duplicate_alias", "unknown_alias", "alias_transfer"]:
                action_name = "Run probe"
            elif case == "duplicate_name_alias":
                action_name = "Execute probe"
            elif case == "press_duplicate":
                action_name = "AXPress"
            else:
                action_name = "AXShowAlternateUI" if case.startswith("standard") else "AXCustomExact"
            start = len(events)
            error = None
            try:
                receipt = dict(
                    await target.click() if case == "press_duplicate" else await target.perform_action(action_name)
                )
            except ComputerUseError as caught:
                error = caught
                receipt = {
                    "code": caught.code, "effect": caught.effect, "detail": caught.detail,
                    "impact": caught.impact, "observation": caught.observation,
                    "observation_status": caught.observation_status,
                    "observation_error": caught.observation_error,
                    "window_lifecycle": caught.window_lifecycle,
                }
            await asyncio.sleep(0.1)
            after = await state(folder)
            if case == "slow" and after["performed"]:
                after = await wait_state(folder, lambda value: value["callbackFinished"])
            effects = [
                event["value"]
                for event in events[start:]
                if event.get("kind") == "backend_diagnostic"
                and isinstance(event.get("value"), dict)
                and event["value"].get("kind") == "input_activity"
                and event["value"].get("phase") == "willCall"
            ]
            input_effects = [effect for effect in effects if effect.get("stage") == "input"]
            preparation_effects = [effect for effect in effects if effect.get("stage") == "preparation"]
            cleanup_effects = [effect for effect in effects if effect.get("stage") == "cleanup"]
            unknown_effects = [
                effect for effect in effects if effect.get("stage") not in ("input", "preparation", "cleanup")
            ]
            # A successful write includes a separate postwrite observation.
            # Use the provider's snapshot at its actual action callback to keep
            # presentation reads out of the raw-action dispatch contract.
            callback_reads = after["descriptionReadsAtCallbacks"]
            dispatch_reads = callback_reads[0] if callback_reads else after["descriptionReads"]
            description_delta = {
                name: count - before["descriptionReads"].get(name, 0)
                for name, count in dispatch_reads.items()
                if count != before["descriptionReads"].get(name, 0)
            }
            post_dispatch_description_delta = {
                name: count - dispatch_reads.get(name, 0)
                for name, count in after["descriptionReads"].items()
                if count != dispatch_reads.get(name, 0)
            }
            input_contexts = [
                {
                    "primitives": event["result"].get("primitives", []),
                    "actionsComplete": event["result"].get("actionsComplete"),
                }
                for event in events[start:]
                if event.get("kind") == "command_end"
                and event.get("method") == "inspect"
                and isinstance(event.get("result"), dict)
                and event["result"].get("kind") == "inputContext"
            ]
            item = {
                "case": case,
                "requested": action_name,
                "before": before,
                "result": receipt,
                "after": after,
                "description_reads_during_action": description_delta,
                "description_reads_after_callback": post_dispatch_description_delta,
                "description_measurement": "provider callback boundary" if callback_reads else "no callback dispatched",
                "effects": effects,
                "input_effects": input_effects,
                "preparation_effects": preparation_effects,
                "cleanup_effects": cleanup_effects,
                "unknown_effects": unknown_effects,
                "mutation_applied": pending_mutation is None,
                "api_success": error is None,
                "callback_confirmed": len(after["performed"]) == 1,
                "input_contexts": input_contexts,
            }
            actions.append(item)
            if item["api_success"]:
                report["api_success"].append(case)
            if item["callback_confirmed"]:
                report["callback_confirmed"].append(case)
            if error is not None and receipt.get("effect") == "possible":
                report["native_error_cases"].append({"case": case, "result": receipt})
            assert not unexpected_input and after["other"] == "unchanged", item
            assert after["targetIdentity"] == before["targetIdentity"], item
            if case in [
                "description_vanishes",
                "disappearance",
                "within",
                "modal",
                "alias_transfer",
                "duplicate_at_dispatch",
                "standard_vanishes",
            ]:
                assert pending_mutation is None, item
            if case == "alias_transfer":
                assert not before["aliasTransferred"] and after["aliasTransferred"], item
            if case == "duplicate_at_dispatch":
                assert before["names"] == ["AXCustomExact"] and after["names"] == ["AXCustomExact", "AXCustomExact"], (
                    item
                )
            negatives = [
                "duplicate_alias",
                "unknown_alias",
                "disappearance",
                "within",
                "modal",
                "alias_transfer",
            ]
            pre_fix_refusal = baseline and case in BASELINE_CASES
            if case in negatives or pre_fix_refusal:
                assert error is not None and not after["performed"], item
                assert not effects, item
            else:
                expected = (
                    "AXPress"
                    if case == "press_duplicate"
                    else "AXShowAlternateUI"
                    if case.startswith("standard")
                    else "AXCustomExact"
                )
                assert after["performed"] == [expected], item
                assert not unknown_effects and not cleanup_effects, item
                if case == "press_duplicate":
                    assert len(preparation_effects) <= 1 and all(
                        effect["device"] == "focus"
                        and effect["transport"] == "process"
                        and effect["mechanism"] == "processApplicationActivationNotification"
                        and effect["recipientPID"] == before["pid"]
                        for effect in preparation_effects
                    ), item
                else:
                    assert not preparation_effects, item
                if case == "slow":
                    item["timeout_observed"] = "AX error -25204" in receipt.get("detail", "")
                    assert (
                        error is not None and receipt["code"] == "execution_unknown" and receipt["effect"] == "possible"
                    ), item
                    assert len(input_effects) == 1, item
                elif case.startswith("standard") or case == "press_duplicate":
                    assert error is None and len(input_effects) == 1, item
                else:
                    assert (
                        error is not None and receipt["code"] == "execution_unknown" and receipt["effect"] == "possible"
                    ), item
                    assert "AX error -25205" in receipt["detail"] and len(input_effects) == 1, item
                assert input_effects[0]["mechanism"] == expected, item
                if case == "changing":
                    assert receipt["observation_status"] == "available" and receipt["observation"] is not None, item
                    assert receipt["impact"].attempts[-1].dispatch == "unknown", item
                if not baseline and action_name not in ["Run probe", "Execute probe"] and case != "press_duplicate":
                    assert not description_delta, item
                if not baseline and case in ["large", "standard_large"]:
                    assert input_contexts, item
                    for context in input_contexts:
                        assert context["actionsComplete"] is False and len(context["primitives"]) == 1, item
                        primitive = context["primitives"][0]
                        assert primitive["unit"] == "native_action" and primitive["action"]["name"] == expected, item
        report["passed"] = True
    finally:
        (folder / "result.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2, default=encode_artifact) + "\n"
        )
        computer._controller.transport.close()
        await computer._controller.transport.wait_closed()
        set_diagnostic_handler(None)
        journal.close()
        os.kill(initial["pid"], signal.SIGTERM)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", action="store_true")
    parser.add_argument("--driver", type=Path)
    parser.add_argument("--only", nargs="+", choices=CASES)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    folder = Path(tempfile.mkdtemp(prefix="explicit-action-", dir=ROOT / ".artifacts"))
    try:
        asyncio.run(verify(folder, args.baseline, args.driver, args.only))
    finally:
        LOG.info("Explicit action evidence: %s", folder)


if __name__ == "__main__":
    main()
