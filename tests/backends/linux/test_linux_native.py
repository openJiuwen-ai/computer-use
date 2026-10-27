"""Linux production v5 pipeline with only its native OS provider substituted.

These tests are portable mechanism checks, not Linux desktop acceptance.
"""

import io
import json
import sys
import threading
import time
import unittest
from dataclasses import replace
from unittest.mock import patch

from repository import ROOT as REPO_ROOT

ROOT = REPO_ROOT
sys.path.append(str(ROOT / "tests/fixtures"))
from linux_native_provider import Backend, NativeError, Node, Relation

from computer_use.client.factory import nativeComputer
from computer_use.errors import ComputerUseError
from computer_use.interaction.read.acquisition.directory.service import NativeDirectory as SDKDirectory, select_surface
from computer_use.interaction.read.acquisition.reader import NativeReader
from computer_use.interaction.read.facts.normalize import normalizeFacts
from computer_use.interaction.read.numbering.references import DisplayReferences
from computer_use.native.connection import Connection
from computer_use.native.contracts import HostPolicy
from computer_use.native.semantics import FIELDS, control_capabilities
from computer_use.native.transport import ConnectionPort
from computer_use.protocol.directory import decodeDirectory
from computer_use.protocol.observation.adapters import decode_driver_observation, decode_query_facts
from computer_use.protocol.session import decodeHello
from computer_use_linux.contracts.protocol import Failure, Request
from computer_use_linux.objects.native_accessibility import NativeAccessibility
from computer_use_linux.objects.native_store import NativeStore
from computer_use_linux.objects.registry import Window
from computer_use_linux.runtime.native import CAPABILITIES, NativeExecution
from computer_use_linux.runtime.server import Server
from computer_use_linux.windowing.desktop import AccessibilityDesktop


def context(seconds=10):
    return Request(time.monotonic() + seconds, threading.Event())


def scope(**changes):
    return {**dict(session="session", instance=None, rootWindow=None, surface=None, node=None, capture=None), **changes}


def read_graph(wire):
    """Inspect exact driver query evidence independently of presentation."""
    return decode_query_facts(wire)


def read_relation_graph(wire):
    """Inspect one relationship query through the driver evidence boundary."""
    facts = decode_query_facts(wire)
    if len(facts["relations"]) != 1:
        raise ValueError("Expected one relation response")
    return {key: facts[key] for key in ("session", "platform", "surface", "observed")} | {
        "kind": "relation",
        "relation": facts["relations"][0],
    }


class LinuxNativeFactsTests(unittest.TestCase):
    def setUp(self):
        self.backend = Backend()
        self.driver = NativeExecution(self.backend, "session")
        self.pid = patch("computer_use_linux.windowing.native_directory.process_start", return_value="generation")
        self.pid.start()

    def tearDown(self):
        self.driver.close()
        self.pid.stop()

    def begin(self, target, ctx=None):
        return self.driver.perform(
            "begin", dict(mode="read", target=target, intent=dict(kind="read", parameters={})), ctx or context()
        )["operation"]

    def inspect(self, target, query):
        operation = self.begin(target)
        try:
            return self.driver.perform("inspect", dict(operation=operation, target=target, query=query), context())
        finally:
            self.driver.perform("finish", dict(operation=operation), context())

    def surfaces(self):
        apps = decodeDirectory(self.inspect(scope(), dict(kind="applications")))
        return decodeDirectory(self.inspect(scope(), dict(kind="surfaces", application=apps["applications"][0]["id"])))

    def target(self):
        surface = self.surfaces()["surfaces"][0]
        return dict(
            session="session",
            instance=surface["instance"],
            rootWindow=surface["window"],
            surface=surface["surface"],
            node=surface["node"],
            capture=None,
        )

    def collect(self, target=None, **changes):
        self.last_wire = self.inspect(
            target or self.target(),
            {**dict(kind="nodes", fields=list(FIELDS["linux"]), maxNodes=1000, maxDepth=40), **changes},
        )
        self.last_graph = decode_driver_observation(self.last_wire)
        return decode_query_facts(self.last_wire)

    def test_native_fields_types_relations_and_actions_survive_sdk_projection(self):
        self.backend.editor.interfaces += ["Value"]
        self.backend.editor.value = 7.5
        self.backend.editor.relations = [Relation("labelled-by", [self.backend.button])]
        result = self.collect()
        editor = next(n for n in result["nodes"] if n["values"]["name"] == "Input")
        self.assertEqual(editor["values"]["Text.text"], "a😀bc")
        self.assertEqual(editor["values"]["Text.characterCount"], 4)
        self.assertEqual(editor["values"]["Text.selections"], [dict(startOffset=1, endOffset=2)])
        self.assertEqual(editor["values"]["Value.currentValue"], 7.5)
        self.assertFalse(editor["values"]["selected"])
        self.assertEqual(editor["values"]["description"], "")
        snapshot = normalizeFacts(
            self.last_graph,
            DisplayReferences("session"),
            driver_features=CAPABILITIES,
            operation_capabilities={n["node"]: control_capabilities("linux", n, CAPABILITIES) for n in result["nodes"]},
        )
        rendered = snapshot.render()["text"]
        self.assertIn("a😀bc", rendered)
        self.assertIn("7.5", rendered, "Independent numeric facts may not be overwritten by Text")
        self.assertIn("click", rendered)
        self.assertIn("clickable", rendered, "A declared click now has an implemented native route")
        unsupported = normalizeFacts(
            self.last_graph,
            DisplayReferences("session"),
            driver_features=[],
            operation_capabilities={n["node"]: control_capabilities("linux", n, []) for n in result["nodes"]},
        ).render()["text"]
        self.assertNotIn("clickable", unsupported, "A raw action alone is not a driver capability")
        self.assertIs(snapshot.native, self.last_graph)
        self.assertFalse(any(name == "get_focused" for name, _, _ in self.backend.ax.calls))

    def test_query_failures_do_not_become_absent_or_a_successful_empty_tree(self):
        self.backend.editor.errors["get_description"] = NativeError("provider timeout")
        self.backend.button.errors["get_action_description"] = 7
        self.backend.long_text.errors["get_interfaces"] = None
        self.backend.window.children.insert(1, None)
        result = self.collect()
        root, editor = result["nodes"]
        self.assertEqual(editor["fieldStatus"]["description"]["state"], "error")
        self.assertEqual(editor["fieldStatus"]["Value.currentValue"]["state"], "unsupported")
        children = next(r for r in result["relations"] if r["source"] == root["node"] and r["property"] == "children")
        self.assertEqual((children["state"], children["total"], len(children["targets"])), ("partial", 4, 1))
        self.assertNotIn("cursor", children, "A hole cannot be skipped with a continuation")
        self.assertIn("unavailable", children["detail"])
        self.backend.window.children.remove(None)
        result = self.collect()
        button = next(n for n in result["nodes"] if n["values"]["name"] == "确认")
        self.assertEqual(button["actions"]["items"][0]["descriptionStatus"]["state"], "error")
        text = next(n for n in result["nodes"] if n["values"]["name"] == "Full text")
        self.assertEqual(text["fieldStatus"]["Text.text"]["state"], "error")

    def test_capture_is_immutable_utf16_and_does_not_require_a_live_window(self):
        result = self.collect()
        node = next(n for n in result["nodes"] if n["values"]["name"] == "Full text")
        capture = node["fieldStatus"]["Text.text"]
        self.assertEqual((capture["state"], capture["totalUnits"]), ("truncated", 9000))
        self.backend.long_text.text = "changed"
        self.backend.window.dead = True
        self.backend.desktop.items.clear()
        with patch.object(self.backend.ax, "begin", side_effect=AssertionError("Capture read must not query AT-SPI")):
            page = self.inspect(scope(), dict(kind="capturedText", textRef=capture["textRef"], offset=1, length=2))[
                "page"
            ]
            self.assertEqual(page, dict(text="😀", offset=1, total=9000, next=3))
        with self.assertRaisesRegex(Failure, "surrogate"):
            self.inspect(scope(), dict(kind="capturedText", textRef=capture["textRef"], offset=2, length=2))

    def test_protected_or_incomplete_text_does_not_get_a_false_full_capture(self):
        self.backend.editor.role = "password text"
        self.backend.long_text.errors["get_character_count"] = 300000
        result = self.collect()
        editor = next(n for n in result["nodes"] if n["values"]["name"] == "Input")
        self.assertEqual(editor["fieldStatus"]["Text.text"]["state"], "unsupported")
        text = next(n for n in result["nodes"] if n["values"]["name"] == "Full text")
        self.assertEqual(text["fieldStatus"]["Text.text"]["state"], "truncated")
        self.assertNotIn("textRef", text["fieldStatus"]["Text.text"])

    def test_provider_text_offsets_are_not_compared_to_python_string_indices(self):
        self.backend.editor.errors["get_character_count"] = len(self.backend.editor.text.encode("utf-16-le")) // 2
        self.backend.long_text.errors["get_character_count"] = 9000
        result = self.collect()
        editor = next(n for n in result["nodes"] if n["values"]["name"] == "Input")
        self.assertEqual(editor["values"]["Text.text"], "a😀bc")
        self.assertNotIn("Text.text", editor["fieldStatus"])
        text = next(n for n in result["nodes"] if n["values"]["name"] == "Full text")
        self.assertEqual(text["fieldStatus"]["Text.text"]["totalUnits"], 9000)
        self.assertIn("textRef", text["fieldStatus"]["Text.text"])

    def test_native_extent_changes_do_not_complete_a_capture_from_later_ui(self):
        original = self.backend.ax.A.Text.get_character_count
        calls = []

        def changed(obj):
            if obj is self.backend.long_text:
                calls.append(obj)
                return 6000 if len(calls) == 1 else 6001
            return original(obj)

        self.backend.ax.A.Text.get_character_count = changed
        text = next(n for n in self.collect()["nodes"] if n["values"].get("name") == "Full text")
        self.assertEqual(text["fieldStatus"]["Text.text"]["state"], "truncated")
        self.assertNotIn("textRef", text["fieldStatus"]["Text.text"])

    def test_child_pages_verify_the_original_prefix_instead_of_reusing_offsets(self):
        self.backend.window.children = [Node("label", f"Child {i}", parent=self.backend.window) for i in range(201)]
        target = self.target()
        query = dict(
            kind="nodes", fields=["role", "name"], maxNodes=130, maxDepth=1, childrenPage=dict(cursor=None, limit=128)
        )
        first = read_graph(self.inspect(target, query))
        children = next(r for r in first["relations"] if r["source"] == target["node"] and r["property"] == "children")
        self.assertEqual((len(first["nodes"]), children["total"], len(children["targets"])), (129, 201, 128))
        second = read_graph(self.inspect(target, {**query, "childrenPage": dict(cursor=children["cursor"], limit=128)}))
        suffix = next(r for r in second["relations"] if r["source"] == target["node"] and r["property"] == "children")
        self.assertEqual((suffix["state"], suffix["offset"], len(suffix["targets"])), ("partial", 128, 73))
        self.assertNotIn("cursor", suffix)
        self.backend.window.children[0] = Node("label", "Replaced child")
        with self.assertRaisesRegex(Failure, "changed"):
            self.inspect(target, {**query, "childrenPage": dict(cursor=children["cursor"], limit=128)})

    def test_graph_preserves_shared_cycles_unknown_relations_and_explicit_read_limits(self):
        self.backend.button.children = [self.backend.editor, self.backend.window]
        target = self.target()
        self.collect(target)
        rendered = normalizeFacts(self.last_graph, DisplayReferences("session")).render()["text"]
        self.assertIn("(shared)", rendered)
        self.assertIn("(cycle)", rendered)
        unknown = read_relation_graph(
            self.inspect(target, dict(kind="relation", property="unknown", cursor=None, limit=128))
        )
        self.assertEqual(unknown["relation"]["state"], "unsupported")
        limited = read_graph(self.inspect(target, dict(kind="nodes", fields=["name"], maxNodes=1, maxDepth=0)))
        self.assertEqual(len(limited["nodes"]), 1)
        self.assertTrue(limited["unread"])
        self.assertTrue(limited["stopReasons"])

    def test_cross_process_modal_keeps_fixed_root_and_unrelated_window_is_refused(self):
        modal = Node("dialog", "Export", pid=99)
        self.backend.desktop.items.append(
            Window(
                12,
                "Export",
                "Helper",
                99,
                (0, 0, 50, 50),
                transient=11,
                modal=True,
                process_start="other",
                accessible=modal,
            )
        )
        directory = self.surfaces()
        selected = select_surface(directory)
        self.assertEqual(selected.root_window, directory["surfaces"][0]["window"])
        self.assertEqual(selected.surface, directory["surfaces"][1]["surface"])
        target = dict(
            session="session",
            instance=selected.instance,
            rootWindow=selected.root_window,
            surface=selected.surface,
            node=selected.node,
            capture=None,
        )
        self.collect(target)
        self.backend.desktop.items[1].transient = None
        with self.assertRaisesRegex(Failure, "no longer belongs"):
            self.collect(target)

    def test_replaced_window_lifetime_never_rebinds_the_old_reference(self):
        target = self.target()
        self.backend.desktop.items[0] = replace(self.backend.desktop.items[0], process_start="successor")
        self.surfaces()
        with self.assertRaises(Failure) as result:
            self.collect(target)
        self.assertEqual(result.exception.code, "stale_target")

    def test_closing_modal_during_discovery_keeps_live_root_and_reports_gap(self):
        window = self.backend.desktop.items[0]
        popup = replace(window, key=22, title="Transient dialog", transient=window.key, modal=True)
        self.backend.desktop.items.append(popup)
        before = self.surfaces()
        root, modal = before["surfaces"]
        validate = self.backend.desktop.validate

        def during_discovery(item):
            if item.window.key == popup.key:
                self.backend.registry.retire(popup.key)
                raise Failure("Working window was closed.", "stale_target")
            return validate(item)

        with patch.object(self.backend.desktop, "validate", side_effect=during_discovery):
            current = self.surfaces()
        self.assertEqual([w["window"] for w in current["surfaces"]], [root["window"]])
        self.assertTrue(any("changed during discovery" in message for message in current["stopReasons"]))
        target = scope(instance=root["instance"], rootWindow=root["window"], surface=modal["surface"])
        with self.assertRaises(Failure) as caught:
            self.driver.directory.validate(target, context())
        self.assertEqual(caught.exception.code, "stale_target")

    def test_discovery_does_not_hide_cancellation_or_nonstale_native_errors(self):
        app = self.driver.directory.applications(context())["applications"][0]["id"]
        for code in ("cancelled", "input_unavailable", "deadline_exceeded"):
            with (
                self.subTest(code=code),
                patch.object(self.backend.desktop, "validate", side_effect=Failure("native failure", code)),
            ):
                with self.assertRaises(Failure) as caught:
                    self.driver.directory.read(app, context())
                self.assertEqual(caught.exception.code, code)
        ctx = context()

        def cancel(item):
            ctx.cancelled.set()
            raise Failure("Closed while cancellation arrived", "stale_target")

        with patch.object(self.backend.desktop, "validate", side_effect=cancel):
            with self.assertRaises(Failure) as caught:
                self.driver.directory.read(app, ctx)
            self.assertEqual(caught.exception.code, "cancelled")

    def test_inaccessible_window_stays_in_the_directory_without_a_fabricated_node(self):
        self.backend.desktop.items[0].accessible = None
        entry = self.surfaces()["surfaces"][0]
        self.assertTrue(entry["nativeWindow"])
        self.assertFalse(entry["accessibility"])
        self.assertIsNone(entry["node"])
        self.assertEqual(entry["title"], "Fixture")

    def test_native_window_relations_preserve_failure_conflict_and_reverse_links(self):
        desktop = AccessibilityDesktop(self.backend.ax, self.backend.registry.retire, "wayland")
        popup = Node("dialog", "Popup")
        windows = [
            replace(self.backend.desktop.items[0]),
            Window("popup", "Popup", "Helper", 99, None, modal=True, accessible=popup),
        ]
        # A non-child POPUP_FOR relation is a valid window association.
        popup.relations = [Relation("popup-for", [self.backend.window])]
        notes = []
        desktop.raw_window_relations(windows, notes)
        self.assertEqual((windows[1].parent_state, windows[1].transient), ("known", 11))
        self.assertEqual(notes, [])
        # A guarded helper would return []; the raw failure must remain unknown.
        popup.errors["get_relation_set"] = NativeError("relation timeout")
        desktop.raw_window_relations(windows, notes)
        self.assertEqual((windows[1].parent_state, windows[1].transient), ("unknown", None))
        self.assertTrue(notes)
        popup.errors.clear()
        popup.relations = []
        self.backend.window.relations = [Relation("parent-window-of", [popup])]
        desktop.raw_window_relations(windows, [])
        self.assertEqual((windows[1].parent_state, windows[1].transient), ("known", 11))
        # Conflicting parent facts cannot become an arbitrary first match.
        popup.relations = [Relation("subwindow-of", [popup])]
        notes = []
        desktop.raw_window_relations(windows, notes)
        self.assertEqual(windows[1].parent_state, "unknown")
        self.assertTrue(any("conflicting" in note for note in notes))

    def test_wire_budget_and_malformed_native_counts_have_explicit_coverage(self):
        with patch("computer_use_linux.read.native_facts.WIRE_BUDGET", 512):
            result = self.collect()
        self.assertEqual(len(result["nodes"]), 1)
        self.assertTrue(result["unread"])
        self.assertIn("Native collection wire budget exhausted.", result["stopReasons"])
        self.backend.window.errors["get_child_count"] = -1
        result = self.collect()
        children = next(r for r in result["relations"] if r["property"] == "children")
        self.assertEqual(children["state"], "error")
        self.assertNotIn("total", children)
        self.assertIn("child count", children["detail"])

    def test_operation_deadline_cancel_and_cleanup_are_shared_across_requests(self):
        target = self.target()
        operation = self.begin(target)
        self.driver.current.deadline = time.monotonic() - 1
        with self.assertRaises(Failure) as error:
            self.driver.perform(
                "inspect",
                dict(
                    operation=operation,
                    target=target,
                    query=dict(kind="nodes", fields=["name"], maxNodes=10, maxDepth=1),
                ),
                context(),
            )
        self.assertEqual(error.exception.code, "deadline_exceeded")
        self.assertIsNone(self.driver.current)
        self.assertEqual(self.driver.perform("finish", dict(operation=operation), context()), dict(cleanup="complete"))
        operation = self.begin(target)
        ctx = context()
        ctx.cancelled.set()
        with self.assertRaises(Failure) as error:
            self.driver.perform(
                "inspect",
                dict(
                    operation=operation,
                    target=target,
                    query=dict(kind="nodes", fields=["name"], maxNodes=10, maxDepth=1),
                ),
                ctx,
            )
        self.assertEqual(error.exception.code, "cancelled")
        self.assertIsNone(self.driver.current)
        self.assertFalse(self.driver.store.pins)

    def test_reference_eviction_is_explicit_and_does_not_reuse_old_aliases(self):
        store = NativeStore(self.backend.ax, node_limit=2)
        root = store.keep("s", self.backend.window)
        store.pins.add(root)
        first = store.keep("s", self.backend.editor)
        store.keep("s", self.backend.button)
        self.assertIs(store.get(root, "s").obj, self.backend.window)
        with self.assertRaises(Failure):
            store.get(first, "s")
        self.assertNotEqual(store.keep("s", self.backend.editor), first)


class LinuxNativeProtocolTests(unittest.TestCase):
    def test_only_v5_hello_can_initialize_native_resources(self):
        out = io.StringIO()
        constructed = []

        def factory(*args):
            constructed.append(args)
            return Backend(*args)

        server = Server(factory, output=out)
        for version in (3, True, 6.0, 5):
            server.execute(dict(version=version, id=0, method="hello", params=dict(desktopMode="shared")), context())
            failure = json.loads(out.getvalue().splitlines()[-1])
            self.assertEqual(failure["version"], 6)
            self.assertEqual(failure["error"]["effect"], "none")
            self.assertEqual(constructed, [])
            self.assertIsNone(server.version)
        server.execute(dict(version=6, id=0, method="hello", params=dict(desktopMode="shared")), context())
        self.assertIn("result", json.loads(out.getvalue().splitlines()[-1]))
        self.assertEqual(len(constructed), 1)
        server.native.close()

    def test_native_dispatch_is_deduplicated_and_never_replays_conflicts_or_expired_ids(self):
        out = io.StringIO()
        server = Server(Backend, output=out)
        server.execute(dict(version=6, id=0, method="hello", params=dict(desktopMode="shared")), context())
        self.addCleanup(server.native.close)

        def request(id, **changes):
            return dict(
                version=6, session=server.session, id=id, timeoutMs=1000, method="inspect", params={}, **changes
            )

        def reply():
            return json.loads(out.getvalue().splitlines()[-1])

        with patch.object(server.native, "perform", return_value={"marker": "native"}) as native:
            server.execute(request(1), context())
            first = reply()
            server.execute(request(1), context())
            self.assertEqual(reply(), first)
            self.assertEqual(native.call_count, 1)
            changed = request(1)
            changed["params"] = {"different": True}
            server.execute(changed, context())
            self.assertEqual(reply()["error"]["code"], "request_conflict")
            cancelled = context()
            cancelled.cancelled.set()
            server.execute(request(2), cancelled)
            self.assertEqual(reply()["error"]["code"], "cancelled")
            server.execute(request(2), context())
            self.assertEqual(reply()["error"]["code"], "cancelled")
            server.execute(request(3), context(-1))
            self.assertEqual(reply()["error"]["code"], "deadline_exceeded")
            self.assertEqual(native.call_count, 1)
            for identifier in range(4, 262):
                server.execute(request(identifier), context())
            count = native.call_count
            server.execute(request(1), context())
            self.assertEqual(reply()["error"]["code"], "request_conflict")
            self.assertEqual(native.call_count, count)

    def test_foreign_session_cannot_consume_current_request_id(self):
        out = io.StringIO()
        server = Server(Backend, output=out)
        server.execute(dict(version=6, id=0, method="hello", params=dict(desktopMode="shared")), context())
        self.addCleanup(server.native.close)
        request = dict(version=6, session="another-session", id=1, timeoutMs=1000, method="inspect", params={})
        with patch.object(server.native, "perform", return_value={"marker": "native"}) as native:
            server.execute(request, context())
            native.assert_not_called()
            server.execute({**request, "session": server.session}, context())
            self.assertEqual(json.loads(out.getvalue().splitlines()[-1])["result"], {"marker": "native"})
            native.assert_called_once()

    def test_native_cleanup_error_still_closes_platform_resources(self):
        out = io.StringIO()
        server = Server(Backend, source=io.BytesIO(b""), output=out)
        server.execute(dict(version=6, id=0, method="hello", params=dict(desktopMode="shared")), context())
        self.addCleanup(server.native.close)
        with (
            patch.object(server.native, "close", side_effect=RuntimeError("cleanup fixture")),
            patch.object(server.backend, "close") as backend_close,
        ):
            with self.assertRaisesRegex(RuntimeError, "cleanup fixture"):
                server.run()
            backend_close.assert_called_once()

    def test_working_window_identity_never_rebinds_to_reused_native_id(self):
        from computer_use_linux.objects.registry import Registry

        for replacement in ("same", "pid", "process-generation"):
            with self.subTest(replacement=replacement):
                registry = Registry()
                first = Window(123, "Before", "Fixture", 17, (0, 0, 100, 100), process_start="birth1")
                original = registry.window(first)
                if replacement == "same":
                    registry.retire(123)
                new = registry.window(
                    replace(
                        first,
                        title="After",
                        pid=18 if replacement == "pid" else 17,
                        process_start="birth2" if replacement == "process-generation" else "birth1",
                    )
                )
                self.assertNotEqual(original.selector, new.selector)
                self.assertNotEqual(original.scope, new.scope)
                self.assertNotEqual(original.ref, new.ref)
                with self.assertRaises(Failure):
                    registry.instance(original.selector)
                self.assertIs(registry.instance(new.selector), new)

    def test_raw_native_lifetime_uses_only_single_object_cache_and_events(self):
        from types import SimpleNamespace as NS

        ax = NativeAccessibility.__new__(NativeAccessibility)
        calls = []
        ax.ctx, ax.dead, ax.native_key = None, {}, id
        ax.focus_events = {}
        ax.GLib = NS(Error=NativeError)
        ax._state = "ready"
        ax._A = NS(
            StateType=NS(DEFUNCT="defunct"),
            Role=NS(INVALID="invalid"),
            Accessible=NS(
                clear_cache_single=lambda obj: calls.append(obj),
                get_state_set=lambda obj: NS(contains=lambda _: False),
                get_role=lambda obj: "entry",
            ),
        )
        obj = object()
        ax.live(obj)
        self.assertEqual(calls, [obj])
        ax.event(NS(source=obj, type="window:destroy"))
        with self.assertRaises(Failure) as error:
            ax.live(obj)
        self.assertEqual(error.exception.code, "stale_target")
        self.assertIs(ax.dead[id(obj)], obj, "Keep the original native object alive so its pointer cannot be recycled")

    def test_read_only_hello_and_no_mixed_version_or_legacy_method(self):
        out = io.StringIO()
        server = Server(Backend, output=out)
        server.execute(dict(version=6, id=0, method="hello", params=dict(desktopMode="shared")), context())
        reply = json.loads(out.getvalue().splitlines()[-1])
        hello = decodeHello(reply, "shared", version=6)
        self.assertEqual(set(hello["capabilities"]), set(CAPABILITIES))
        invalid = json.loads(json.dumps(reply))
        invalid["result"]["capabilities"].remove("desktopCoordination")
        with self.assertRaisesRegex(ValueError, "desktopCoordination"):
            decodeHello(invalid, "shared", version=6)
        for version, method in ((3, "listApps"), (5, "readApp"), (5, "execute"), (5, "capture")):
            server.execute(
                dict(
                    version=version,
                    id=1 if version == 3 else {"readApp": 1, "execute": 2, "capture": 3}[method],
                    session=server.session,
                    method=method,
                    params={},
                    timeoutMs=1000,
                ),
                context(),
            )
            failure = json.loads(out.getvalue().splitlines()[-1])["error"]
            self.assertEqual(failure["effect"], "none")
        server.native.close()


class LinuxNativeSDKTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.connection = Connection(
            lambda: None,
            dict(command=sys.executable, args=[str(ROOT / "tests/fixtures/linux_native_provider.py")], timeoutMs=3000),
            protocol_version=6,
        )
        self.port = ConnectionPort(self.connection, HostPolicy())
        self.computer = nativeComputer(self.port)

    async def asyncTearDown(self):
        self.connection.close()
        await self.connection.waitClosed()

    async def test_real_server_public_read_subtree_text_and_unimplemented_write(self):
        apps = await self.computer.listApps()
        self.assertEqual(len(apps), 1)
        app = self.computer.app(apps[0].id)
        state = await app.read(view="both")
        self.assertIn("Input", state.text)
        self.assertIn("确认", state.text)
        self.assertIn("Full text", state.text)
        # Obtain the same native bindings through the real public SDK query.
        field = await app.element([{"role": "textField", "name": "Input"}])
        self.assertEqual((await field.readText()).text, "a😀bc")
        subtree = await field.read()
        self.assertIn("a😀bc", subtree.text)
        full_text = await app.element([{"role": "textField", "name": "Full text"}])
        page = await full_text.readText(length=1)
        self.assertEqual((await full_text.readText(cursor=page.next, length=1)).text, "😀")
        with self.assertRaises(ComputerUseError) as error:
            await field.paste("do not dispatch")
        self.assertEqual(error.exception.effect, "none")
        self.assertEqual(error.exception.code, "mode_unsupported")
        (
            self.assertIn(
                "Input", (await app.read(view="both")).text, "An unsupported write must not poison a read-only session"
            )
        )

    async def test_sdk_directory_and_reader_use_production_atspi_facts(self):
        directory, reader = SDKDirectory(self.port), NativeReader(self.port)
        apps = await directory.applications()
        surfaces = await directory.surfaces(apps["applications"][0]["id"])
        target = select_surface(surfaces)
        snapshot = await reader.read(target)
        self.assertEqual(snapshot.native.backend, "linux")
        editor = next(node for node in snapshot.native.nodes.values() if node.name == "Input")
        self.assertTrue(editor.states.editable, "Reading capabilities cannot depend on write implementation")
        self.assertEqual([(span.start, span.end) for span in editor.text.selections], [(1, 2)])
        self.assertEqual(
            editor.text.unit, "provider", "AT-SPI provider offsets are not assumed to be Unicode codepoints"
        )
