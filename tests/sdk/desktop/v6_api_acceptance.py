"""Opt-in macOS v6 API acceptance on two disposable documents and an owned sheet."""

import asyncio
import json

from sdk.desktop.native_acceptance import DIRECTORY, build, terminate

from computer_use import Computer, ComputerUseError, Window
from computer_use.session.observation_data import observationData


async def main():
    fixture = build("V6WindowProbe")
    computer, report = Computer(), {"passed": False, "directory": str(DIRECTORY)}

    async def read(ui, name, view="tree"):
        result = await ui.read(view=view, full=True)
        (DIRECTORY / (name + ".txt")).write_text(result.text)
        if result.screenshot is not None:
            (DIRECTORY / (name + ".png")).write_bytes(result.screenshot)
        return result

    async def state(**expected):
        for _ in range(60):
            value = json.loads((fixture[0] / "state.json").read_text())
            if all(value[k] == v for k, v in expected.items()):
                return value
            await asyncio.sleep(0.05)
        raise AssertionError((value, expected))

    try:
        # Fixture startup is independent of the observed SDK operations.
        for _ in range(80):
            if (fixture[0] / "state.json").exists():
                break
            await asyncio.sleep(0.05)
        pending = computer.app(str(fixture[1]))
        choices = await read(pending, "00-instance-choices")
        assert pending._instance.anchor is None and "computer.app(" in choices.text
        apps = [
            entry
            for entry in await computer.listApps()
            if entry.isRunning
            and entry.displayName.startswith("V6WindowProbe — ")
            and computer._controller.interfaces.references.resolve(
                entry.id, computer._controller.transport.session
            ).selector
            == computer._controller.state(pending).application
        ]
        assert len(apps) == 2, apps
        a = computer.app(next(entry.id for entry in apps if entry.displayName.endswith("V6 Document A")))
        b = computer.app(next(entry.id for entry in apps if entry.displayName.endswith("V6 Document B")))
        observed = await read(a, "01-A", "both")
        field = await a.element([{"identifier": "v6-field-a"}])
        root = await a.element(int(observationData(observed)["roots"][0]["ref"]))
        await field.pressKey("super+Right")
        await root.typeText("!")
        await state(a="root-A!", b="root-B")
        try:
            await root.typeText("forbidden", replace=True)
            raise AssertionError("container replacement was accepted")
        except ComputerUseError as error:
            assert error.effect == "none"
        await (await a.element("Open owned dialog")).click()
        await state(sheet=True)
        combined = await read(a, "02-A-and-dialog", "both")
        assert len(a._instance.members) >= 2
        assert "V6 Document B" not in " ".join(item["title"] for item in a._instance.members.values())
        sheet_ref = next(ref for ref, key in a._instance.windows.items() if a._instance.members[key]["modal"] is True)
        dialog = a.window(sheet_ref)
        assert isinstance(dialog, Window)
        await read(dialog, "03-dialog")
        field_ref = next(ref for ref, snap in a._instance.refs.items() if snap["binding"] == field._snapshot["binding"])
        try:
            await dialog.element(int(field_ref))
            raise AssertionError("Window accepted another member element")
        except ComputerUseError as error:
            assert error.effect == "none"
        child = await dialog.element([{"identifier": "v6-child"}])
        await child.typeText("dialog 中文🙂", replace=True)
        await state(a="root-A!", b="root-B", child="dialog 中文🙂")
        try:
            await field.typeText("must not reach blocked document", replace=True)
            raise AssertionError("modal did not block parent input")
        except ComputerUseError as error:
            assert error.effect == "none"
        await read(dialog, "04-dialog-result", "both")
        assert (await child.readText()).text == "dialog 中文🙂"
        await (await dialog.element("Dismiss owned dialog")).click()
        await state(sheet=False)
        await read(a, "05-A-resumed")
        assert (await field.readText()).text == "root-A!"
        try:
            await dialog.read()
            raise AssertionError("closed Window was reused")
        except ComputerUseError as error:
            assert error.code == "stale_target"
        await (await a.element("Close document A")).click()
        await state(rootClosed=True, b="root-B")
        try:
            await a.read()
            raise AssertionError("closed App root was retargeted")
        except ComputerUseError as error:
            assert error.code == "stale_target"
        await read(b, "06-B-untouched")
        assert (await (await b.element([{"identifier": "v6-field-b"}])).readText()).text == "root-B"
        report.update(
            passed=True,
            independentInstances=True,
            currentReceiverForwarding=True,
            windowScope=True,
            dialogInput=True,
            modalProtection=True,
            retainedElement=True,
            closedWindowInvalid=True,
            closedRootInvalid=True,
            unrelatedDocumentUntouched=True,
            combinedScreenshot=combined.screenshot is not None,
            final=await state(b="root-B"),
        )
    except BaseException as error:
        report["error"] = repr(error)
        raise
    finally:
        computer._controller.transport.close()
        await computer._controller.transport.waitClosed()
        terminate(fixture)
        (DIRECTORY / "v6-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
        print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
