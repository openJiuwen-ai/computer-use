"""Focus discovery is query-only, scoped, and cannot authorize input by itself."""

import unittest
from copy import deepcopy

from computer_use.native.contracts import Binding, CurrentReceiverEvidence
from computer_use.native.transport import decode_context
from computer_use.protocol.execution import decodeExecutionResult, decodeInspectQuery


def context():
    return dict(
        foregroundSurface=None,
        keyboardSurface=None,
        focusedNode=None,
        focusAncestors=[],
        editor=None,
        editorOwner=None,
        selectionKnown="unknown",
        geometryCurrent="unknown",
        windowRoute="unknown",
    )


def response(evidence):
    return dict(
        kind="inputContext",
        binding=dict(session="s", instance="i", rootWindow="w", surface="s1", node="scope", capture=None),
        observed="observation",
        targetLabel="scope",
        identity="yes",
        withinScope="yes",
        enabled="unknown",
        blockedBy=[],
        primitives=[],
        within=[],
        input={**context(), "currentReceiver": evidence},
    )


class CurrentReceiverContractTests(unittest.TestCase):
    def test_query_explicitly_selects_only_focus_observation(self):
        valid = dict(kind="inputContext", within=[], currentReceiver=True)
        self.assertEqual(decodeInspectQuery(valid), valid)
        for change in (
            dict(currentReceiver=False),
            dict(currentReceiver=None),
            dict(currentReceiver=1),
            dict(actuator=dict(relation="containedEditor", node="editor")),
            dict(receiver=dict(relation="containedFocus", node="child")),
        ):
            with self.subTest(change=change), self.assertRaises(ValueError):
                decodeInspectQuery({**valid, **change})

    def test_positive_parent_path_is_scoped_and_typed(self):
        evidence = dict(state="confirmed", node="editor", ancestors=["wrapper", "scope"])
        decoded = decodeExecutionResult("inspect", response(evidence))
        observed = decode_context(decoded["input"]).current_receiver
        self.assertEqual(observed, CurrentReceiverEvidence("confirmed", "editor", ("wrapper", "scope")))
        self.assertTrue(observed.confirms_scope(Binding("s", "i", "w", "s1", "scope")))
        self.assertFalse(observed.confirms_scope(Binding("s", "i", "w", "s1", "other")))
        self.assertIsNone(decode_context(context()).current_receiver, "Old drivers remain explicitly unsupported")

    def test_absence_unavailability_and_conflict_are_distinct_without_a_selected_node(self):
        for state in ("absent", "unavailable", "conflict"):
            sample = response(dict(state=state, node=None, ancestors=[], detail="local evidence gap"))
            self.assertEqual(
                decode_context(decodeExecutionResult("inspect", sample)["input"]).current_receiver.state, state
            )
            for selected in (dict(node="editor"), dict(ancestors=["scope"])):
                bad = deepcopy(sample)
                bad["input"]["currentReceiver"].update(selected)
                with self.assertRaises(ValueError):
                    decodeExecutionResult("inspect", bad)

    def test_forged_path_and_input_primitives_are_rejected(self):
        sample = response(dict(state="confirmed", node="editor", ancestors=["scope"]))
        for selected in (
            dict(node=None),
            dict(ancestors=[]),
            dict(ancestors=["other"]),
            dict(ancestors=["scope", "other"]),
            dict(ancestors=["scope", "scope"]),
            dict(ancestors=["editor", "scope"]),
        ):
            bad = deepcopy(sample)
            bad["input"]["currentReceiver"].update(selected)
            with self.subTest(selected=selected), self.assertRaises(ValueError):
                decodeExecutionResult("inspect", bad)
        bad = deepcopy(sample)
        bad["primitives"] = [
            dict(
                id="p",
                unit="key_sequence",
                channel="keyboard",
                supported="yes",
                readiness="yes",
                pending=[],
                backgroundPreparation="no",
            )
        ]
        with self.assertRaises(ValueError):
            decodeExecutionResult("inspect", bad)


if __name__ == "__main__":
    unittest.main()
