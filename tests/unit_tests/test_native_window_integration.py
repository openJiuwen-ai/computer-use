"""Native identity selects a retained SDK instance, never a name or list position."""

import asyncio
from copy import deepcopy
from types import SimpleNamespace

import pytest

from computer_use.client.factory import native_computer
from computer_use.errors import ComputerUseError
from computer_use.protocol.directory import decode_directory


def directory(*, identity=True, platform="macos"):
    item = dict(
        window="17",
        instance="working-1",
        surface="surface-1",
        node=None,
        title="Same title",
        kind="window",
        parent=None,
        parentState="none",
        modal=False,
        nativeWindow=True,
        accessibility=False,
    )
    if identity:
        item["nativeIdentity"] = dict(processId=321, windowId=456)
    return dict(
        kind="surfaces",
        session="session-1",
        platform=platform,
        observed=dict(id="capture-1", startedAt=1, completedAt=2),
        stopReasons=[],
        application="instance:app-1",
        surfaces=[item],
        provider=dict(status="available", attribute=None, writeStatus=None),
    )


def test_directory_retains_optional_native_identity():
    raw = directory()
    decoded = decode_directory(raw)
    assert decoded["surfaces"][0]["nativeIdentity"] == {"processId": 321, "windowId": 456}
    raw["surfaces"][0]["nativeIdentity"]["windowId"] = 999
    assert decoded["surfaces"][0]["nativeIdentity"]["windowId"] == 456
    assert "nativeIdentity" not in decode_directory(directory(identity=False))["surfaces"][0]


@pytest.mark.parametrize("field,value", [("processId", True), ("processId", 0), ("windowId", -1), ("windowId", "456")])
def test_directory_rejects_malformed_native_identity(field, value):
    raw = directory()
    raw["surfaces"][0]["nativeIdentity"][field] = value
    with pytest.raises(ValueError):
        decode_directory(raw)


def application_directory():
    return {
        "kind": "applications",
        "session": "session-1",
        "platform": "macos",
        "observed": {"id": "catalogue-1", "startedAt": 1, "completedAt": 2},
        "stopReasons": [],
        "applications": [
            {
                "id": "instance:app-1",
                "displayName": "An app",
                "isRunning": True,
                "path": None,
                "identifier": None,
                "nativeIdentity": {"processId": 321},
            }
        ],
    }


def test_application_directory_retains_optional_process_identity():
    raw = application_directory()
    assert decode_directory(raw)["applications"][0]["nativeIdentity"] == {"processId": 321}
    del raw["applications"][0]["nativeIdentity"]
    assert "nativeIdentity" not in decode_directory(raw)["applications"][0]


@pytest.mark.parametrize("pid", [True, 0, -1, "321"])
def test_application_directory_rejects_malformed_process_identity(pid):
    raw = application_directory()
    raw["applications"][0]["nativeIdentity"]["processId"] = pid
    with pytest.raises(ValueError):
        decode_directory(raw)


def computer_with_directory(monkeypatch, raw, catalogue=None):
    port = SimpleNamespace(
        connection=SimpleNamespace(session="session-1", capabilities=()),
        calls=SimpleNamespace(retired=False),
        platform=raw["platform"],
    )
    computer = native_computer(port)
    directory_reader = computer._controller.reader.directory

    async def applications():
        return deepcopy(catalogue or dict(application_directory(), platform=raw["platform"]))

    async def query(request, timeout):
        assert request["application"] == "instance:app-1"
        return deepcopy(raw)

    monkeypatch.setattr(directory_reader, "applications", applications)
    monkeypatch.setattr(directory_reader, "_query", query)
    return computer


def test_native_window_discovery_never_queries_unrelated_applications(monkeypatch):
    from computer_use.integrations.macos import app_for_window

    catalogue = application_directory()
    catalogue["applications"].insert(
        0, dict(catalogue["applications"][0], id="instance:unrelated", nativeIdentity={"processId": 999})
    )
    computer = computer_with_directory(monkeypatch, directory(), catalogue)
    app = asyncio.run(app_for_window(computer, process_id=321, window_id=456))
    assert computer._controller.state(app).selection.application == "instance:app-1"


@pytest.mark.parametrize("scenario", ["missing", "wrong_process", "duplicate", "not_running"])
def test_process_identity_must_resolve_uniquely_before_window_discovery(monkeypatch, scenario):
    from computer_use.integrations.macos import app_for_window

    catalogue = application_directory()
    entry = catalogue["applications"][0]
    if scenario == "missing":
        del entry["nativeIdentity"]
    elif scenario == "wrong_process":
        entry["nativeIdentity"]["processId"] = 999
    elif scenario == "not_running":
        entry["isRunning"] = False
    else:
        catalogue["applications"].append(dict(entry, id="instance:duplicate"))
    computer = computer_with_directory(monkeypatch, directory(), catalogue)

    async def forbidden_query(*args, **kwargs):
        raise AssertionError("ambiguous or absent process must fail before window discovery")

    monkeypatch.setattr(computer._controller.reader.directory, "_query", forbidden_query)
    with pytest.raises(ComputerUseError):
        asyncio.run(app_for_window(computer, process_id=321, window_id=456))


def test_native_window_maps_through_real_discovery_and_registry(monkeypatch):
    from computer_use.integrations.macos import app_for_window

    raw = directory()
    other = dict(
        raw["surfaces"][0],
        window="18",
        instance="working-2",
        surface="surface-2",
        nativeIdentity=dict(processId=321, windowId=789),
    )
    raw["surfaces"].insert(0, other)
    computer = computer_with_directory(monkeypatch, raw)
    app = asyncio.run(app_for_window(computer, process_id=321, window_id=456))
    selected = computer._controller.state(app).selection
    assert selected.root.root_window == "17"
    assert selected.root.instance == "working-1"
    assert selected.application == "instance:app-1"


@pytest.mark.parametrize("scenario", ["missing", "wrong_process", "duplicate", "windows"])
def test_native_window_never_guesses(monkeypatch, scenario):
    from computer_use.integrations.macos import app_for_window

    raw = directory(identity=scenario != "missing", platform="windows" if scenario == "windows" else "macos")
    if scenario == "wrong_process":
        raw["surfaces"][0]["nativeIdentity"]["processId"] = 999
    if scenario == "duplicate":
        raw["surfaces"].append(dict(raw["surfaces"][0], window="18", instance="working-2", surface="surface-2"))
    computer = computer_with_directory(monkeypatch, raw)
    with pytest.raises(ComputerUseError):
        asyncio.run(app_for_window(computer, process_id=321, window_id=456))


@pytest.mark.parametrize("pid,wid", [(True, 456), (0, 456), (321, "456"), (321, -1)])
def test_invalid_identifiers_rejected_before_discovery(monkeypatch, pid, wid):
    from computer_use.integrations.macos import app_for_window

    with pytest.raises(ValueError):
        asyncio.run(app_for_window(None, process_id=pid, window_id=wid))
