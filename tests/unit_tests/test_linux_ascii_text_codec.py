# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Payload length profiles retain scope without unrelated provider metadata."""

import importlib
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest


class ProviderError(Exception):
    pass


class States(set):
    def contains(self, flag):
        return flag in self


@pytest.fixture(scope="module")
def native_modules():
    source = Path(__file__).resolve().parents[2] / "os_adapter/linux/src"
    with patch.object(sys, "path", [str(source), *sys.path]):
        return SimpleNamespace(
            text=importlib.import_module("computer_use_linux.control.native_text"),
            codec=importlib.import_module("computer_use_linux.control.native_text_codec"),
            store=importlib.import_module("computer_use_linux.objects.native_store"),
            operation=importlib.import_module("computer_use_linux.runtime.native_operation"),
            protocol=importlib.import_module("computer_use_linux.contracts.protocol"),
        )


@pytest.fixture
def scene(native_modules):
    modules = native_modules
    root = SimpleNamespace(role="frame", parent=None, states=States(("ENABLED",)))
    field = SimpleNamespace(
        role="entry",
        parent=root,
        states=States(("ENABLED", "EDITABLE")),
        interfaces=["EditableText", "Text"],
        value="before",
        caret=2,
        selection=None,
    )
    provider = SimpleNamespace(app="app", toolkit="GTK", version="4", failure=None)
    metadata_calls, mutations = [], []

    def metadata(name, value):
        metadata_calls.append(name)
        if provider.failure == name:
            raise ProviderError("Provider metadata is unavailable: " + name)
        return value

    def insert(obj, position, text, length):
        mutations.append(("insert_text", obj, position, text, length))
        # Provider-side units are independent from the client's codec. Match
        # AT-SPI/GTK's byte prefix or Qt's QString UTF-16 prefix so a wrong
        # argument cannot pass merely because the substitute ignored length.
        if isinstance(provider.toolkit, str) and provider.toolkit.strip().casefold() == "qt":
            payload = text.encode("utf-16-le")[: length * 2].decode("utf-16-le", "surrogatepass")
        else:
            payload = text.encode("utf-8")[:length].decode("utf-8")
        obj.value = obj.value[:position] + payload + obj.value[position:]
        obj.caret = position + len(payload)
        return True

    def delete(obj, start, end):
        mutations.append(("delete_text", obj, start, end))
        obj.value = obj.value[:start] + obj.value[end:]
        obj.caret, obj.selection = 0, None
        return True

    atspi = SimpleNamespace(
        StateType=SimpleNamespace(ENABLED="ENABLED", SENSITIVE="SENSITIVE", EDITABLE="EDITABLE", READ_ONLY="READ_ONLY"),
        Accessible=SimpleNamespace(
            get_state_set=lambda obj: obj.states,
            get_interfaces=lambda obj: obj.interfaces,
            get_role_name=lambda obj: obj.role,
            get_parent=lambda obj: obj.parent,
            get_application=lambda _: metadata("app", provider.app),
            get_toolkit_name=lambda _: metadata("toolkit", provider.toolkit),
            get_toolkit_version=lambda _: metadata("version", provider.version),
        ),
        Text=SimpleNamespace(
            get_character_count=lambda obj: len(obj.value),
            get_n_selections=lambda obj: int(obj.selection is not None),
            get_selection=lambda obj, _index: SimpleNamespace(
                start_offset=obj.selection[0], end_offset=obj.selection[1]
            ),
            get_caret_offset=lambda obj: obj.caret,
        ),
        EditableText=SimpleNamespace(insert_text=insert, delete_text=delete),
    )
    ax = SimpleNamespace(
        atspi=atspi,
        glib=SimpleNamespace(Error=ProviderError),
        native_key=id,
        live=lambda _: None,
        call=lambda fn, *args: fn(*args),
        begin=lambda _: None,
    )
    selected = object()
    directory = SimpleNamespace(surfaces={"surface": selected}, validate=lambda *_: None, windows=lambda *_: [])
    backend = SimpleNamespace(
        ax=ax,
        ensure_unlocked=lambda: None,
        desktop=SimpleNamespace(validate=lambda _: SimpleNamespace(accessible=root, key="window")),
    )
    store = modules.store.NativeStore(ax)
    target = {"surface": "surface", "node": store.keep("surface", field)}
    unit = modules.text.NativeText(backend, store, directory)
    ctx = modules.protocol.Request(time.monotonic() + 5, threading.Event())

    def inspect(text):
        operation = modules.operation.NativeOperation(
            target, ctx.deadline, {"kind": "insert", "parameters": {"text": text}}, writable=True
        )
        result = unit.inspect(operation, target, (), ctx)
        return operation, result

    def command(result):
        primitive = result["primitives"][0]
        return {"kind": "native_text_edit", "primitiveId": primitive["id"], "target": target, "foreground": False}

    def execute(operation, result):
        return unit.execute(operation, command(result), ctx)

    return SimpleNamespace(
        modules=modules,
        root=root,
        field=field,
        provider=provider,
        metadata_calls=metadata_calls,
        mutations=mutations,
        inspect=inspect,
        execute=execute,
        command=command,
        ax=ax,
        ctx=ctx,
        target=target,
        unit=unit,
        failure=modules.protocol.Failure,
    )


def unavailable_metadata(scene, missing):
    if missing.startswith("error_"):
        scene.provider.failure = missing.removeprefix("error_")
    elif missing == "app":
        scene.provider.app = None
    elif missing == "toolkit":
        scene.provider.toolkit = ""
    else:
        scene.provider.version = None


@pytest.mark.parametrize("missing", ("app", "toolkit", "version", "error_app", "error_toolkit", "error_version"))
def test_ascii_insert_dispatches_once_without_provider_metadata(scene, missing):
    unavailable_metadata(scene, missing)

    operation, result = scene.inspect("abc")
    assert result["primitives"], "ASCII insertion must retain its native text route."
    receipt = scene.execute(operation, result)

    assert receipt["dispatch"] == "dispatched" and receipt["errorCode"] is None
    assert scene.field.value == "beabcfore"
    assert scene.mutations == [("insert_text", scene.field, 2, "abc", 3)]
    assert not scene.metadata_calls


def test_ascii_metadata_becoming_unreadable_does_not_block_final_dispatch(scene):
    operation, result = scene.inspect("abc")
    scene.provider.failure = "app"

    receipt = scene.execute(operation, result)

    assert receipt["dispatch"] == "dispatched" and receipt["errorCode"] is None
    assert scene.field.value == "beabcfore" and len(scene.mutations) == 1


@pytest.mark.parametrize("missing", ("app", "toolkit", "error_app", "error_toolkit"))
def test_standard_unicode_insert_remains_available_without_provider_metadata(scene, missing):
    unavailable_metadata(scene, missing)

    operation, result = scene.inspect("é😀")
    assert result["primitives"], "EditableText declares the standard UTF-8 contract without toolkit metadata."
    receipt = scene.execute(operation, result)

    assert receipt["dispatch"] == "dispatched" and receipt["errorCode"] is None
    assert scene.field.value == "beé😀fore"
    assert scene.mutations == [("insert_text", scene.field, 2, "é😀", 6)]


@pytest.mark.parametrize("version", ("unknown", "6.12", "7.0"))
def test_qt_unicode_adapter_does_not_require_a_version_whitelist(scene, version):
    scene.provider.toolkit, scene.provider.version = "Qt", version

    operation, result = scene.inspect("é😀")
    assert result["primitives"], "A Qt family adapter must not reject a new or missing version label."
    receipt = scene.execute(operation, result)

    assert receipt["dispatch"] == "dispatched" and receipt["errorCode"] is None
    assert scene.field.value == "beé😀fore"
    assert scene.mutations == [("insert_text", scene.field, 2, "é😀", 3)]
    assert "version" not in scene.metadata_calls


@pytest.mark.parametrize(
    "toolkit,version,length", (("GTK", "4", 6), ("Qt", "5.15", 3), ("Qt", "6.10", 3), ("Qt", "6.11.0", 3))
)
def test_known_provider_unicode_lengths_remain_correct(scene, toolkit, version, length):
    scene.provider.toolkit, scene.provider.version = toolkit, version
    operation, result = scene.inspect("é😀")
    assert result["primitives"], "The selected provider length unit must retain native insertion."

    receipt = scene.execute(operation, result)

    assert receipt["dispatch"] == "dispatched" and receipt["errorCode"] is None
    assert scene.field.value == "beé😀fore"
    assert scene.mutations == [("insert_text", scene.field, 2, "é😀", length)]


def test_fresh_qt_hint_sets_the_correct_unit_before_first_mutation(scene):
    operation, result = scene.inspect("é")
    scene.provider.toolkit, scene.provider.version = "Qt", "6.10"

    receipt = scene.execute(operation, result)

    assert receipt["dispatch"] == "dispatched" and receipt["errorCode"] is None
    assert scene.field.value == "beéfore"
    assert scene.mutations == [("insert_text", scene.field, 2, "é", 1)]


def test_restored_qt_hint_completes_an_initial_standard_recipe_without_rebinding(scene):
    scene.provider.toolkit, scene.provider.failure = "Qt", "toolkit"
    operation, result = scene.inspect("é😀")
    assert result["primitives"]
    contract = operation.contracts[result["primitives"][0]["id"]]
    initial_codec = contract["codec"]
    assert initial_codec.insertion_length("é😀") == 6
    scene.provider.failure = None

    receipt = scene.execute(operation, result)

    assert receipt["dispatch"] == "dispatched" and receipt["errorCode"] is None
    assert scene.field.value == "beé😀fore"
    assert scene.mutations == [("insert_text", scene.field, 2, "é😀", 3)]
    assert contract["codec"] is initial_codec and initial_codec.insertion_length("é😀") == 6


@pytest.mark.parametrize("change", ("disabled", "not_editable", "readonly", "no_text", "no_editable_text", "scope"))
def test_ascii_insert_retains_target_and_capability_guards(scene, change):
    operation, result = scene.inspect("abc")
    if change == "disabled":
        scene.field.states.discard("ENABLED")
    elif change == "not_editable":
        scene.field.states.discard("EDITABLE")
    elif change == "readonly":
        scene.field.states.add("READ_ONLY")
    elif change == "no_text":
        scene.field.interfaces.remove("Text")
    elif change == "no_editable_text":
        scene.field.interfaces.remove("EditableText")
    else:
        scene.field.parent = SimpleNamespace(role="frame", parent=None, states=States(("ENABLED",)))

    receipt = scene.execute(operation, result)

    assert receipt["dispatch"] == "notDispatched" and receipt["errorCode"] is not None
    assert not scene.mutations and not operation.dispatched


def test_ascii_codec_contract_cannot_be_reused_for_unicode(scene):
    operation, result = scene.inspect("abc")
    operation.intent["parameters"]["text"] = "é"

    receipt = scene.execute(operation, result)

    assert receipt["dispatch"] == "notDispatched" and receipt["errorCode"] is not None
    assert not scene.mutations and not operation.dispatched


def version_unavailable(scene, missing):
    if missing == "absent_getter":
        del scene.ax.atspi.Accessible.get_toolkit_version
    elif missing == "error_version":
        scene.provider.failure = "version"
    else:
        scene.provider.version = None


@pytest.mark.parametrize("toolkit", ("GTK", "Custom toolkit"))
@pytest.mark.parametrize("missing", ("version", "error_version", "absent_getter"))
def test_nonqt_unicode_selection_insert_does_not_depend_on_version(scene, toolkit, missing):
    scene.provider.toolkit = toolkit
    version_unavailable(scene, missing)
    scene.field.selection = (1, 4)

    operation, result = scene.inspect("é😀")
    assert result["primitives"], "A non-Qt UTF-8 insertion has no toolkit-version prerequisite."
    receipt = scene.execute(operation, result)

    assert receipt["dispatch"] == "dispatched" and receipt["errorCode"] is None
    assert scene.field.value == "bé😀re"
    assert scene.mutations == [
        ("delete_text", scene.field, 1, 4),
        ("insert_text", scene.field, 1, "é😀", 6),
    ]
    assert "version" not in scene.metadata_calls


@pytest.mark.parametrize("change", ("version", "error_version", "absent_getter", "new_version"))
def test_nonqt_version_change_after_inspection_does_not_block_dispatch(scene, change):
    operation, result = scene.inspect("中😀é")
    if change == "new_version":
        scene.provider.version = "new-version"
    else:
        version_unavailable(scene, change)

    receipt = scene.execute(operation, result)

    assert receipt["dispatch"] == "dispatched" and receipt["errorCode"] is None
    assert scene.field.value == "be中😀éfore"
    assert len(scene.mutations) == 1 and scene.mutations[0][-1] == 10
    assert "version" not in scene.metadata_calls


def test_nonqt_version_failure_after_selection_deletion_still_inserts_full_payload(scene):
    scene.field.selection = (1, 4)
    operation, result = scene.inspect("é😀")
    native_delete = scene.ax.atspi.EditableText.delete_text

    def delete_then_lose_version(*args):
        accepted = native_delete(*args)
        scene.provider.failure = "version"
        return accepted

    scene.ax.atspi.EditableText.delete_text = delete_then_lose_version
    receipt = scene.execute(operation, result)

    assert receipt["dispatch"] == "dispatched" and scene.field.value == "bé😀re"
    assert [call[0] for call in scene.mutations] == ["delete_text", "insert_text"]
    assert "version" not in scene.metadata_calls


@pytest.mark.parametrize("missing", ("version", "error_version", "absent_getter"))
def test_qt_unicode_adapter_does_not_query_version_metadata(scene, missing):
    scene.provider.toolkit, scene.provider.version = "Qt", "6.10"
    version_unavailable(scene, missing)

    operation, result = scene.inspect("é😀")
    assert result["primitives"]
    receipt = scene.execute(operation, result)

    assert receipt["dispatch"] == "dispatched" and scene.field.value == "beé😀fore"
    assert scene.mutations == [("insert_text", scene.field, 2, "é😀", 3)]
    assert "version" not in scene.metadata_calls


def test_fresh_qt_hint_sets_the_correct_unit_before_selection_deletion(scene):
    scene.field.selection = (1, 4)
    operation, result = scene.inspect("é😀")
    scene.provider.toolkit, scene.provider.version = "Qt", "6.10"

    receipt = scene.execute(operation, result)

    assert receipt["dispatch"] == "dispatched" and receipt["errorCode"] is None
    assert scene.field.value == "bé😀re"
    assert scene.mutations == [
        ("delete_text", scene.field, 1, 4),
        ("insert_text", scene.field, 1, "é😀", 3),
    ]


@pytest.mark.parametrize("change", ("toolkit", "scope", "disabled"))
def test_target_or_toolkit_change_after_deletion_stops_partial_operation_without_replay(scene, change):
    scene.field.selection = (1, 4)
    operation, result = scene.inspect("é😀")
    native_delete = scene.ax.atspi.EditableText.delete_text

    def delete_then_change_toolkit(*args):
        accepted = native_delete(*args)
        if change == "toolkit":
            scene.provider.toolkit, scene.provider.version = "Qt", "6.10"
        elif change == "scope":
            scene.field.parent = SimpleNamespace(role="frame", parent=None, states=States(("ENABLED",)))
        else:
            scene.field.states.discard("ENABLED")
        return accepted

    scene.ax.atspi.EditableText.delete_text = delete_then_change_toolkit
    command = scene.command(result)
    receipt = operation.step(1, command, lambda: scene.execute(operation, result))

    assert receipt["dispatch"] == "partial" and receipt["retryReason"] is None
    assert scene.field.value == "bre" and [call[0] for call in scene.mutations] == ["delete_text"]
    assert operation.step(1, command, lambda: pytest.fail("must retain partial receipt")) == receipt
    with pytest.raises(scene.failure, match="already ended or dispatched"):
        operation.step(2, command, lambda: scene.execute(operation, result))


@pytest.mark.parametrize("outcome", ("false", "error"))
def test_unicode_insertion_uncertain_reply_is_not_replayed(scene, outcome):
    operation, result = scene.inspect("é😀")
    native_insert = scene.ax.atspi.EditableText.insert_text

    def insert_then_lose_reply(*args):
        native_insert(*args)
        if outcome == "error":
            raise ProviderError("The insertion reply was lost after mutation.")
        return False

    scene.ax.atspi.EditableText.insert_text = insert_then_lose_reply
    command = scene.command(result)
    receipt = operation.step(1, command, lambda: scene.execute(operation, result))

    assert receipt["dispatch"] == "unknown" and receipt["retryReason"] is None
    assert receipt["errorCode"] == "execution_unknown" and scene.field.value == "beé😀fore"
    assert operation.step(1, command, lambda: pytest.fail("must retain unknown receipt")) == receipt
    with pytest.raises(scene.failure, match="already ended or dispatched"):
        operation.step(2, command, lambda: scene.execute(operation, result))
    assert len(scene.mutations) == 1


@pytest.mark.parametrize("toolkit", (None, " ", 17, "New toolkit"))
def test_unknown_toolkit_uses_the_standard_unicode_argument_unit(scene, toolkit):
    scene.provider.toolkit = toolkit
    operation, result = scene.inspect("中😀é")
    assert result["primitives"]

    receipt = scene.execute(operation, result)

    assert receipt["dispatch"] == "dispatched" and scene.field.value == "be中😀éfore"
    assert scene.mutations == [("insert_text", scene.field, 2, "中😀é", 10)]
    assert "version" not in scene.metadata_calls


@pytest.mark.parametrize("missing", ("get_application", "get_toolkit_name"))
def test_missing_optional_metadata_getter_keeps_standard_insertion(scene, missing):
    delattr(scene.ax.atspi.Accessible, missing)
    operation, result = scene.inspect("é😀")
    assert result["primitives"]

    receipt = scene.execute(operation, result)

    assert receipt["dispatch"] == "dispatched" and scene.field.value == "beé😀fore"
    assert scene.mutations == [("insert_text", scene.field, 2, "é😀", 6)]


@pytest.mark.parametrize("change", ("toolkit_label", "toolkit_unknown", "metadata_error", "application_unknown"))
def test_standard_unit_survives_unrelated_metadata_changes(scene, change):
    scene.field.selection = (1, 4)
    operation, result = scene.inspect("é😀")
    if change == "toolkit_label":
        scene.provider.toolkit = "Custom toolkit"
    elif change == "toolkit_unknown":
        scene.provider.toolkit = None
    elif change == "metadata_error":
        scene.provider.failure = "toolkit"
    else:
        scene.provider.app = None

    receipt = scene.execute(operation, result)

    assert receipt["dispatch"] == "dispatched" and scene.field.value == "bé😀re"
    assert scene.mutations == [
        ("delete_text", scene.field, 1, 4),
        ("insert_text", scene.field, 1, "é😀", 6),
    ]


@pytest.mark.parametrize("change", ("new_version", "version_missing", "version_error", "toolkit_spelling"))
def test_qt_unit_survives_unrelated_metadata_changes(scene, change):
    scene.provider.toolkit, scene.provider.version = "Qt", "6.10"
    operation, result = scene.inspect("é😀")
    if change == "new_version":
        scene.provider.version = "6.12.0"
    elif change == "version_missing":
        scene.provider.version = None
    elif change == "version_error":
        scene.provider.failure = "version"
    else:
        scene.provider.toolkit = " qt "

    receipt = scene.execute(operation, result)

    assert receipt["dispatch"] == "dispatched" and scene.field.value == "beé😀fore"
    assert scene.mutations == [("insert_text", scene.field, 2, "é😀", 3)]
    assert "version" not in scene.metadata_calls


@pytest.mark.parametrize(
    "missing", ("app", "toolkit", "error_app", "error_toolkit", "get_application", "get_toolkit_name")
)
def test_selected_qt_unit_survives_temporarily_missing_metadata(scene, missing):
    scene.provider.toolkit, scene.provider.version = "Qt", "6.12.0"
    operation, result = scene.inspect("é😀")
    if missing.startswith("get_"):
        delattr(scene.ax.atspi.Accessible, missing)
    elif missing == "toolkit":
        scene.ax.atspi.Accessible.get_toolkit_name = lambda _app: None
    else:
        unavailable_metadata(scene, missing)

    receipt = scene.execute(operation, result)

    assert receipt["dispatch"] == "dispatched" and scene.field.value == "beé😀fore"
    assert scene.mutations == [("insert_text", scene.field, 2, "é😀", 3)]


@pytest.mark.parametrize("after_deletion", (False, True))
def test_fresh_standard_unit_updates_before_mutation_but_not_after_deletion(scene, after_deletion):
    scene.provider.toolkit, scene.provider.version = "Qt", "6.12.0"
    if after_deletion:
        scene.field.selection = (1, 4)
    operation, result = scene.inspect("é😀")
    if after_deletion:
        native_delete = scene.ax.atspi.EditableText.delete_text

        def delete_then_change_unit(*args):
            accepted = native_delete(*args)
            scene.provider.toolkit = "GTK"
            return accepted

        scene.ax.atspi.EditableText.delete_text = delete_then_change_unit
    else:
        scene.provider.toolkit = "GTK"
    command = scene.command(result)

    receipt = operation.step(1, command, lambda: scene.execute(operation, result))

    if after_deletion:
        assert receipt["dispatch"] == "partial"
        assert receipt["errorCode"] == "stale_target" and receipt["retryReason"] is None
        assert [call[0] for call in scene.mutations] == ["delete_text"]
        assert operation.step(1, command, lambda: pytest.fail("must retain partial receipt")) == receipt
        with pytest.raises(scene.failure, match="already ended or dispatched"):
            operation.step(2, command, lambda: scene.execute(operation, result))
    else:
        assert receipt["dispatch"] == "dispatched" and receipt["errorCode"] is None
        assert scene.field.value == "beé😀fore"
        assert scene.mutations == [("insert_text", scene.field, 2, "é😀", 6)]


def test_selected_qt_unit_survives_metadata_failure_after_deletion(scene):
    scene.provider.toolkit, scene.provider.version = "Qt", "6.12.0"
    scene.field.selection = (1, 4)
    operation, result = scene.inspect("é😀")
    native_delete = scene.ax.atspi.EditableText.delete_text

    def delete_then_lose_metadata(*args):
        accepted = native_delete(*args)
        scene.provider.failure = "toolkit"
        return accepted

    scene.ax.atspi.EditableText.delete_text = delete_then_lose_metadata
    receipt = scene.execute(operation, result)

    assert receipt["dispatch"] == "dispatched" and scene.field.value == "bé😀re"
    assert scene.mutations == [
        ("delete_text", scene.field, 1, 4),
        ("insert_text", scene.field, 1, "é😀", 3),
    ]


def test_metadata_failure_after_deletion_does_not_stop_standard_insertion(scene):
    scene.field.selection = (1, 4)
    operation, result = scene.inspect("é😀")
    native_delete = scene.ax.atspi.EditableText.delete_text

    def delete_then_lose_metadata(*args):
        accepted = native_delete(*args)
        scene.provider.failure = "toolkit"
        return accepted

    scene.ax.atspi.EditableText.delete_text = delete_then_lose_metadata
    receipt = scene.execute(operation, result)

    assert receipt["dispatch"] == "dispatched" and scene.field.value == "bé😀re"
    assert [call[0] for call in scene.mutations] == ["delete_text", "insert_text"]


@pytest.mark.parametrize("change", ("destroyed", "scope", "readonly", "disabled"))
def test_standard_default_keeps_original_native_target_boundaries(scene, change):
    scene.provider.toolkit = None
    operation, result = scene.inspect("é😀")
    assert result["primitives"]
    if change == "destroyed":

        def no_longer_live(obj):
            if obj is scene.field:
                raise scene.failure("Original native accessible was destroyed.", "stale_target")

        scene.ax.live = no_longer_live
    elif change == "scope":
        scene.field.parent = SimpleNamespace(role="frame", parent=None, states=States(("ENABLED",)))
    elif change == "readonly":
        scene.field.states.add("READ_ONLY")
    else:
        scene.field.states.discard("ENABLED")

    receipt = scene.execute(operation, result)

    assert receipt["dispatch"] == "notDispatched" and receipt["errorCode"] is not None
    assert not scene.mutations and scene.field.value == "before"


@pytest.mark.parametrize("change", ("destroyed", "scope"))
def test_metadata_callback_cannot_bypass_the_final_native_target_check(scene, change):
    operation, result = scene.inspect("é😀")
    destroyed = False

    def live(obj):
        if destroyed and obj is scene.field:
            raise scene.failure("The original object was destroyed by a provider callback.", "stale_target")

    def toolkit_with_target_change(_app):
        nonlocal destroyed
        if change == "destroyed":
            destroyed = True
        else:
            scene.field.parent = SimpleNamespace(role="frame", parent=None, states=States(("ENABLED",)))
        return "Custom toolkit"

    scene.ax.live = live
    scene.ax.atspi.Accessible.get_toolkit_name = toolkit_with_target_change
    receipt = scene.execute(operation, result)

    assert receipt["dispatch"] == "notDispatched" and receipt["errorCode"] == "stale_target"
    assert not scene.mutations and scene.field.value == "before"


@pytest.mark.parametrize("code", ("cancelled", "deadline_exceeded"))
def test_metadata_lookup_does_not_swallow_request_failures(scene, code):
    operation, result = scene.inspect("é😀")

    def fail_request(_obj):
        raise scene.failure("The request cannot continue.", code)

    scene.ax.atspi.Accessible.get_toolkit_name = fail_request
    receipt = scene.execute(operation, result)

    assert receipt["dispatch"] == "notDispatched" and receipt["errorCode"] == code
    assert not scene.mutations


@pytest.mark.parametrize("toolkit", (None, "Qt"))
def test_full_replacement_keeps_its_independent_length_free_setter(scene, toolkit):
    scene.provider.toolkit = toolkit
    scene.provider.failure = "app"

    def set_contents(obj, text):
        scene.mutations.append(("set_text_contents", obj, text))
        obj.value = text
        return True

    scene.ax.atspi.EditableText.set_text_contents = set_contents
    operation = scene.modules.operation.NativeOperation(
        scene.target, scene.ctx.deadline, {"kind": "replace", "parameters": {"text": "中😀é"}}, writable=True
    )
    result = scene.unit.inspect(operation, scene.target, (), scene.ctx)
    receipt = scene.execute(operation, result)

    assert receipt["dispatch"] == "dispatched" and scene.field.value == "中😀é"
    assert scene.mutations == [("set_text_contents", scene.field, "中😀é")]
    assert not scene.metadata_calls
