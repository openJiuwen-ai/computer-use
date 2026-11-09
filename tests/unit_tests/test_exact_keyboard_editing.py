# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Use native text-entry evidence without inventing AX editability."""

from dataclasses import replace

import pytest
from computer_use.errors import ComputerUseError
from computer_use.interaction.write.engine import Engine
from computer_use.native.contracts import Actuator, Intent, Knowledge
from unit_tests.test_confirmed_text_receiver_evidence import TARGET, TextEvidencePort


@pytest.fixture
def anyio_backend():
    return "asyncio"


class ReadOnlyAccessibilityTextPort(TextEvidencePort):
    """A genuine keyboard editor whose accessibility setters are absent."""

    def __init__(self, *, qualification, preparable=False):
        super().__init__(role="field", editable=None, focus_writable=False)
        self.qualification = qualification
        self.key_state = "preparable" if preparable else "ready"

    async def inspect(self, *args, **kwargs):
        facts = await super().inspect(*args, **kwargs)
        return replace(
            facts,
            primitives=tuple(
                replace(primitive, keyboard_editing=self.qualification, global_input=Knowledge.NO)
                for primitive in facts.primitives
            ),
        )


@pytest.mark.anyio
@pytest.mark.parametrize("kind", ["insert", "replace"])
@pytest.mark.parametrize("preparable", [False, True])
async def test_original_text_entry_uses_its_native_keyboard_recipe(kind, preparable):
    port = ReadOnlyAccessibilityTextPort(qualification=kind, preparable=preparable)
    intent = Intent(kind, {"text": "typed"})
    result = await Engine(port).execute(intent, TARGET)
    assert result["status"] == "dispatched"
    assert port.contents == ("typed" if kind == "replace" else "originaltyped")
    assert [command.prepare for command in port.commands] == ([True, False] if preparable else [False])
    assert all(command.target == TARGET for command in port.commands)
    assert not port.queries


@pytest.mark.anyio
@pytest.mark.parametrize("kind,qualification", [("insert", None), ("replace", None), ("replace", "insert")])
async def test_unqualified_key_sequence_does_not_authorize_another_edit_recipe(kind, qualification):
    port = ReadOnlyAccessibilityTextPort(qualification=qualification)
    with pytest.raises(ComputerUseError) as failure:
        await Engine(port).execute(Intent(kind, {"text": "typed"}), TARGET)
    assert failure.value.code == "input_unavailable" and failure.value.effect == "none"
    assert not port.commands and port.contents == "original"


class ScalarOwnerPort(TextEvidencePort):
    """A text-capable scalar whose provider also reports one editable child."""

    def __init__(self, qualification, ready):
        super().__init__(role="scalar")
        self.qualification, self.ready = qualification, ready
        self.foreground = self.opaque_child = True

    def raw_role(self):
        return "AXIncrementor"

    async def query_nodes(self, operation, target, fields, max_nodes, max_depth, timeout_ms):
        result = await super().query_nodes(operation, target, fields, max_nodes, max_depth, timeout_ms)
        if target.node == "opaque-child":
            node = result["nodes"][0]
            node["values"]["AXRole"] = "AXTextField"
            node["fieldStatus"].pop("AXRole", None)
        return result

    async def inspect(self, *args, **kwargs):
        facts = await super().inspect(*args, **kwargs)
        actuator = kwargs.get("actuator")
        if actuator is not None:
            return replace(facts, native_role="AXTextField", actuator=actuator)
        (keyboard,) = facts.primitives
        keyboard = replace(
            keyboard, global_input=Knowledge.NO, keyboard_editing=self.qualification if self.ready else None,
            readiness=Knowledge.YES if self.ready else Knowledge.NO,
            pending=() if self.ready else ("receiver",),
        )
        return replace(facts, primitives=(keyboard,))


@pytest.mark.anyio
@pytest.mark.parametrize("qualification,ready", [("replace", True), (None, True), ("insert", True), ("replace", False)])
async def test_only_qualified_ready_scalar_retains_its_original_keyboard_recipient(qualification, ready):
    port = ScalarOwnerPort(qualification, ready)
    result = await Engine(port).execute(Intent("replace", {"text": "00:00:00.000"}), TARGET)
    assert result["status"] == "dispatched"
    (command,) = port.commands
    assert command.target == TARGET and not command.prepare and not command.foreground
    if qualification == "replace" and ready:
        assert command.actuator is None and not port.queries
    else:
        assert command.actuator == Actuator("opaque-child")
        assert port.queries == [TARGET.node, "opaque-child"]
