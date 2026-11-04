"""The ordinary MCP client receives initial full views and subsequent deltas."""

import inspect
import os
import sys
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def fixture():
    """Publish immutable native facts through the real worker, without desktop input."""
    from dataclasses import replace

    from computer_use.identity import Record
    from computer_use.interaction.read.facts.normalize import normalize_facts
    from computer_use.interaction.read.numbering.references import DisplayReferences
    from computer_use.interaction.read.presentation.document import DisplayDocument
    from computer_use.interaction.read.presentation.preparation import prepare_snapshot
    from computer_use.protocol.observation import ControlValue, NativeNode, NativeObservation, Relation
    from computer_use.session.events import action_completed, observation_completed
    from computer_use.session.observation import lazy_observation

    source, references = Record(), DisplayReferences("fixture")

    def observe(
        value,
        *,
        scope="window-one",
        full=False,
        short=False,
        context=(),
        stream=None,
        reverse=False,
        extra=None,
        notice="",
    ):
        children = ("field",) if short else ("field", "other")
        if reverse:
            children = children[::-1]
        nodes = {
            "window": NativeNode(
                role="window",
                name="Form",
                relations={
                    "children": Relation(kind="children", targets=children, total=len(children)),
                },
            ),
            "field": NativeNode(role="text_field", name="Subject", control_value=ControlValue(value=value)),
        }
        if not short:
            nodes["other"] = NativeNode(role="button", name="Unchanged unrelated control")
        native = NativeObservation(
            backend="macos",
            session="fixture",
            id=value,
            interval_ns=(1, 2),
            roots=("window",),
            nodes=nodes,
        )
        snapshot = normalize_facts(native, references)
        document = DisplayDocument.tree(prepare_snapshot(snapshot, public_api=True)).scoped((scope,))
        refs = dict(snapshot.refs)
        if extra is not None:
            extra_native = NativeObservation(
                backend="macos",
                session="fixture",
                id=value,
                interval_ns=(1, 2),
                roots=(extra,),
                nodes={extra: NativeNode(role="button", name=extra)},
            )
            extra_snapshot = normalize_facts(extra_native, references)
            extra_document = DisplayDocument.tree(prepare_snapshot(extra_snapshot, public_api=True)).scoped((extra,))
            document = DisplayDocument.join((document, extra_document))
            refs.update(extra_snapshot.refs)
        if notice:
            document = DisplayDocument.join((notice, document))
        document = replace(document, context=context)
        state = lazy_observation(document, None)
        observation_completed(
            {
                "source": source,
                "scope": stream or scope,
                "view": "window",
                "state": state,
                "document": document,
                "refs": refs,
                "full": full,
                "publicApi": True,
            }
        )
        return state

    def write(value):
        action_completed({"target": "Subject", "status": "dispatched", "detail": value})
        return observe(value)

    globals().update(observe=observe, write=write)


class DefaultDiffDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_plain_stdio_client_gets_diffs_across_calls_and_between_writes(self):
        with tempfile.TemporaryDirectory(prefix="mcp-diff-") as directory:
            params = StdioServerParameters(
                command=sys.executable,
                args=["-m", "computer_use_mcp"],
                cwd=Path(__file__).resolve().parents[3],
                env={**os.environ, "COMPUTER_USE_LOG_DIR": directory},
            )
            async with (
                stdio_client(params) as (read, write),
                ClientSession(read, write, read_timeout_seconds=timedelta(seconds=20)) as client,
            ):
                await client.initialize()

                async def call(code):
                    result = await client.call_tool("computer_use", {"code": code})
                    self.assertFalse(result.isError, result)
                    return "\n".join(block.text for block in result.content if block.type == "text")

                setup = inspect.getsource(fixture) + "\nfixture()\n"
                first = await call(setup + "state = observe('Initial')")
                self.assertIn("Unchanged unrelated control", first)
                self.assertNotIn("Tree: diff", first)
                second = await call("state = observe('Changed')")
                self.assertIn("Tree: diff", second)
                self.assertIn("Changed", second)
                self.assertNotIn("Unchanged unrelated control", second)
                unchanged = await call("observe('Changed')")
                self.assertIn("Tree: unchanged", unchanged)

                combined = await call("write('First write')\nlast = write('Second write')")
                self.assertEqual(combined.count("Action [dispatched]"), 2)
                self.assertEqual(combined.count("Tree: diff"), 1)
                self.assertIn("First write", combined)
                self.assertIn("Second write", combined)
                self.assertNotIn("Unchanged unrelated control", combined)
                # Only the final observation becomes the next diff baseline;
                # both action receipts remain in chronological order.
                self.assertLess(combined.index("First write"), combined.index("Second write"))
                self.assertIn("Tree: unchanged", await call("observe('Second write')"))
                pinned = await call("observe('Requested full', full=True)\nwrite('After full')")
                self.assertIn("Requested full", pinned)
                self.assertIn("After full", pinned)
                self.assertEqual(pinned.count("Unchanged unrelated control"), 1)
                distinct = await call(
                    "observe('Parent intermediate')\nobserve('Popup content', scope='popup')\n"
                    "observe('Parent final')"
                )
                self.assertNotIn("Parent intermediate", distinct)
                self.assertIn("Popup content", distinct)
                self.assertIn("Parent final", distinct)
                self.assertLess(distinct.index("Popup content"), distinct.index("Parent final"))
                aliased = await call("observe('Through App', stream='app-tree')")
                self.assertIn("Tree: diff", aliased)
                self.assertNotIn("Unchanged unrelated control", aliased)
                reordered = await call("observe('Through App', reverse=True)")
                self.assertIn("Tree: diff", reordered)
                self.assertNotIn("Tree: unchanged", reordered)

                full = await call("observe('Second write', full=True)")
                self.assertIn("Unchanged unrelated control", full)
                self.assertNotIn("Tree: diff", full)
                # Retained SDK text remains complete, but reprinting the capture
                # does not republish an old full view or roll back the baseline.
                explicit_text = await call("print(last.text)")
                self.assertIn("Retained observation", explicit_text)
                self.assertNotIn("Unchanged unrelated control", explicit_text)
                await call("assert 'Unchanged unrelated control' in last.text")
                other = await call("observe('New window', scope='window-two')")
                self.assertIn("Unchanged unrelated control", other)
                self.assertNotIn("Tree: diff", other)

                # Even a tiny tree remains a delta rather than a size-based full fallback.
                await call("observe('A', scope='tiny', short=True)")
                tiny = await call("observe('B', scope='tiny', short=True)")
                self.assertIn("Tree: diff", tiny)
                changed_context = await call("observe('C', scope='tiny', short=True, context=('focused',))")
                self.assertIn("Tree: diff", changed_context)

                await call("observe('Parent before', extra='Application menu', stream='app-tree')")
                mixed = await call("observe('Parent after', extra='Sharing control', stream='window-both')")
                self.assertIn("Tree: diff", mixed)
                self.assertIn("Parent after", mixed)
                self.assertIn("Sharing control", mixed)
                self.assertIn("Full tree for this scope", mixed)
                self.assertNotIn("Unchanged unrelated control", mixed)
                parent_only = await call(
                    "observe('Parent after', notice='Popup tree unavailable; parent follows', stream='app-tree')"
                )
                self.assertIn("Popup tree unavailable", parent_only)
                self.assertIn("Tree: unchanged", parent_only)
                self.assertNotIn("Unchanged unrelated control", parent_only)
                self.assertNotIn("Full tree for this scope", parent_only)

                reset = await client.call_tool("computer_use_reset", {})
                self.assertFalse(reset.isError, reset)
                restarted = await call(setup + "observe('After reset')")
                self.assertNotIn("Tree: diff", restarted)
                self.assertIn("Unchanged unrelated control", restarted)

    async def test_combined_alias_observations_keep_latest_components_and_explicit_full(self):
        with tempfile.TemporaryDirectory(prefix="mcp-coalesced-") as directory:
            params = StdioServerParameters(
                command=sys.executable,
                args=["-m", "computer_use_mcp"],
                cwd=Path(__file__).resolve().parents[3],
                env={**os.environ, "COMPUTER_USE_LOG_DIR": directory},
            )
            async with (
                stdio_client(params) as (read, write),
                ClientSession(read, write, read_timeout_seconds=timedelta(seconds=20)) as client,
            ):
                await client.initialize()

                async def call(code):
                    result = await client.call_tool("computer_use", {"code": code})
                    self.assertFalse(result.isError, result)
                    return "\n".join(block.text for block in result.content if block.type == "text")

                first = await call(inspect.getsource(fixture) + "\nfixture()\nobserve('Initial')")
                self.assertIn("Unchanged unrelated control", first)
                self.assertNotIn("Tree: diff", first)
                combined = await call(
                    "automatic = write('Automatic intermediate')\n"
                    "latest = observe('Explicit latest', stream='same-native-window-other-view')\n"
                    "print(latest)\nprint(latest.text)"
                )
                self.assertEqual(combined.count("Action [dispatched]"), 1)
                self.assertEqual(combined.count("Tree: diff"), 1)
                self.assertIn("Explicit latest", combined)
                self.assertNotIn("Value: Automatic intermediate", combined)
                self.assertIn("Subject. Automatic intermediate", combined)  # Keep the completed action receipt.
                self.assertNotIn("Unchanged unrelated control", combined)

                distinct = await call(
                    "observe('Parent final')\nobserve('Independent popup', scope='popup')"
                )
                self.assertIn("Parent final", distinct)
                self.assertIn("Independent popup", distinct)
                self.assertIn("Tree: diff", distinct)
                self.assertEqual(distinct.count("Unchanged unrelated control"), 1)  # First popup view remains full.

                full = await call(
                    "observe('Explicit full snapshot', full=True)\n"
                    "observe('Later default snapshot', stream='same-native-window-other-view')"
                )
                self.assertIn("Explicit full snapshot", full)
                self.assertIn("Later default snapshot", full)
                self.assertEqual(full.count("Unchanged unrelated control"), 1)
                self.assertEqual(full.count("Tree: diff"), 1)

                preserved = await call(
                    "observe('Earlier parent', extra='Unique toolbar', stream='application')\n"
                    "observe('Latest parent', stream='narrower-window')"
                )
                self.assertIn("Unique toolbar", preserved)
                self.assertIn("Latest parent", preserved)

    async def test_observation_prints_and_slices_share_automatic_presentation(self):
        with tempfile.TemporaryDirectory(prefix="mcp-print-diff-") as directory:
            params = StdioServerParameters(
                command=sys.executable,
                args=["-m", "computer_use_mcp"],
                cwd=Path(__file__).resolve().parents[3],
                env={**os.environ, "COMPUTER_USE_LOG_DIR": directory},
            )
            async with (
                stdio_client(params) as (read, write),
                ClientSession(read, write, read_timeout_seconds=timedelta(seconds=20)) as client,
            ):
                await client.initialize()

                async def call(code):
                    result = await client.call_tool("computer_use", {"code": code})
                    self.assertFalse(result.isError, result)
                    return "\n".join(block.text for block in result.content if block.type == "text")

                setup = inspect.getsource(fixture) + "\nfixture()\n"
                first = await call(setup + "old = observe('Initial')\nprint(old.text[:10])\nprint(old)")
                self.assertEqual(first.count("Unchanged unrelated control"), 1)
                self.assertEqual(first.count("Initial"), 1)
                for expression in ("state", "state.text", "state.text[:6000]", "state.text[-4000:]"):
                    with self.subTest(expression=expression):
                        changed = await call(
                            f"state = observe({expression!r})\n"
                            "assert state.text[:10] == str(state.text)[:10]\n"
                            "assert state.text[-10:] == str(state.text)[-10:]\n"
                            f"print({expression})\nprint(state.text[::2][:5])"
                        )
                        self.assertEqual(changed.count("Tree: diff"), 1)
                        self.assertNotIn("Unchanged unrelated control", changed)
                        self.assertNotIn("Retained observation", changed)

                full = await call("state = observe('Explicit full', full=True)\nprint(state.text[:5])")
                self.assertEqual(full.count("Unchanged unrelated control"), 1)
                self.assertNotIn("Tree: diff", full)

                # A stale object's print must neither move the live baseline
                # backward nor claim that the old capture has been refreshed.
                retained = await call("print(old.text)\nobserve('Explicit full')")
                self.assertIn("Retained observation", retained)
                self.assertIn("Tree: unchanged", retained)
                self.assertNotIn("Initial", retained)

                # Ordinary strings retain their meaning, even if they happen
                # to equal a captured full view. Provenance is the boundary.
                raw = await call("state = observe('Raw string')\nprint(str(state.text))")
                self.assertEqual(raw.count("Unchanged unrelated control"), 1)
                self.assertEqual(raw.count("Tree: diff"), 1)

                mixed = await call("state = observe('Mixed')\nprint('before', state.text[:5], 'after')")
                self.assertLess(mixed.index("before"), mixed.index("Tree: diff"))
                self.assertLess(mixed.index("Tree: diff"), mixed.index("after"))
                self.assertNotIn("Unchanged unrelated control", mixed)
