"""Live scope checks after native provider callbacks, before requested input.

Only native AT-SPI/X11 seams are substituted. No typed-text helper is needed;
these regressions exercise the established key, object and element-pointer units.
"""

import unittest
from unittest.mock import patch

from linux_native_provider import Backend, ElementPointerBackend, KeyboardBackend, Node

from backends.linux.test_linux_native import context, scope
from computer_use.protocol.execution import decodeExecutionResult
from computer_use_linux.runtime.native import NativeExecution


class LinuxNativeTargetBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.driver = None
        self.pid = patch("computer_use_linux.windowing.native_directory.process_start", return_value="generation")
        self.pid.start()

    def tearDown(self):
        if self.driver is not None:
            self.driver.close()
        self.pid.stop()

    def setup_route(self, route, *, within=False):
        self.backend = (
            KeyboardBackend() if route == "key" else ElementPointerBackend() if route == "pointer" else Backend()
        )
        self.driver = NativeExecution(self.backend, "session")
        application = self.driver.directory.applications(context())["applications"][0]["id"]
        row = self.driver.directory.read(application, context())["surfaces"][0]
        self.target = scope(instance=row["instance"], rootWindow=row["window"], surface=row["surface"])
        self.obj = (
            self.backend.button
            if route == "action"
            else self.backend.canvas
            if route == "pointer"
            else self.backend.editor
        )
        if route == "key":
            self.backend.desktop.foreground = self.backend.desktop.keyboard_window = 11
            self.obj.states.add("FOCUSED")
            self.backend.other_editor.states.discard("FOCUSED")
        if route == "pointer":
            self.group = self.backend.group
        else:
            self.group = Node("panel", "Original scope", parent=self.backend.window, children=[self.obj])
            self.obj.parent = self.group
        self.sibling = Node("panel", "Other scope", parent=self.backend.window)
        self.foreign = Node("frame", "Other working window", parent=self.backend.app)
        self.target["node"] = self.driver.store.keep(row["surface"], self.obj)
        ancestors = [self.driver.store.keep(row["surface"], self.group)] if within else []
        kind, parameters, unit = {
            "key": ("key", {"key": "Return"}, "key_sequence"),
            "action": ("click", {}, "native_action"),
            "replace": ("replace", {"text": "new"}, "native_text_edit"),
            "insert": ("insert", {"text": "new"}, "native_text_edit"),
            "pointer": ("click", {}, "pointer_sequence"),
        }[route]
        self.operation = self.driver.perform(
            "begin", dict(mode="write", target=self.target, intent=dict(kind=kind, parameters=parameters)), context()
        )["operation"]
        facts = self.driver.perform(
            "inspect",
            dict(operation=self.operation, target=self.target, query=dict(kind="inputContext", within=ancestors)),
            context(),
        )
        primitive = next(item for item in facts["primitives"] if item["unit"] == unit)
        self.command = dict(kind=unit, primitiveId=primitive["id"], target=self.target, foreground=False)
        self.original_text = self.backend.editor.text
        self.backend.ax.calls.clear()

    def reparent_on(self, method, occurrence, *, within=False):
        """Schedule a provider callback on the final semantic read for a route."""
        seen = 0

        def callback(obj, *args):
            nonlocal seen
            if obj is not self.obj:
                return
            seen += 1
            if seen == occurrence:
                obj.parent = self.sibling if within else self.foreign

        self.backend.ax.hooks[method] = callback

    def send(self):
        return decodeExecutionResult(
            "execute",
            self.driver.perform("execute", dict(operation=self.operation, step=1, command=self.command), context()),
        )

    def assert_stopped_without_input(self):
        result = self.send()
        self.assertEqual((result["dispatch"], result["errorCode"]), ("notDispatched", "stale_target"))
        mutations = {"do_action", "set_text_contents", "insert_text", "delete_text", "grab_focus"}
        self.assertEqual([call[0] for call in self.backend.ax.calls if call[0] in mutations], [])
        self.assertEqual(getattr(self.backend.desktop, "events", []), [])
        self.assertEqual(self.backend.editor.text, self.original_text)
        self.assertEqual(self.backend.button.action_history, [])
        self.assertFalse(self.driver.current.dispatched)

    def test_keyboard_role_callback_cannot_leave_working_window(self):
        self.setup_route("key")
        self.reparent_on("get_role_name", 2)
        self.assert_stopped_without_input()

    def test_keyboard_final_state_callback_cannot_leave_working_window(self):
        self.setup_route("key")
        self.reparent_on("get_state_set", 4)
        self.assert_stopped_without_input()

    def test_keyboard_final_state_callback_cannot_leave_within_scope(self):
        self.setup_route("key", within=True)
        self.reparent_on("get_state_set", 4, within=True)
        self.assert_stopped_without_input()

    def test_replacement_role_callback_cannot_leave_working_window(self):
        self.setup_route("replace")
        self.reparent_on("get_role_name", 1)
        self.assert_stopped_without_input()

    def test_replacement_role_callback_cannot_leave_within_scope(self):
        self.setup_route("replace", within=True)
        self.reparent_on("get_role_name", 1, within=True)
        self.assert_stopped_without_input()

    def test_insertion_final_selection_read_cannot_leave_working_window(self):
        self.setup_route("insert")
        self.reparent_on("get_selection", 2)
        self.assert_stopped_without_input()

    def test_insertion_final_selection_read_cannot_leave_within_scope(self):
        self.setup_route("insert", within=True)
        self.reparent_on("get_selection", 2, within=True)
        self.assert_stopped_without_input()

    def test_action_final_role_read_cannot_leave_working_window(self):
        self.setup_route("action")
        self.reparent_on("get_role_name", 2)
        self.assert_stopped_without_input()

    def test_action_final_index_read_cannot_leave_working_window(self):
        self.setup_route("action")
        self.reparent_on("get_action_name", 2)
        self.assert_stopped_without_input()

    def test_action_final_index_read_cannot_leave_within_scope(self):
        self.setup_route("action", within=True)
        self.reparent_on("get_action_name", 2, within=True)
        self.assert_stopped_without_input()

    def test_pointer_extents_callback_cannot_move_input_outside_scope(self):
        self.setup_route("pointer", within=True)
        self.reparent_on("get_extents", 2, within=True)
        self.assert_stopped_without_input()

    def test_pointer_final_hit_callback_cannot_move_input_to_other_window(self):
        self.setup_route("pointer")
        self.reparent_on("get_accessible_at_point", 2)
        self.assert_stopped_without_input()

    def test_same_window_reparenting_without_within_remains_usable(self):
        self.setup_route("replace")
        self.reparent_on("get_role_name", 1, within=True)
        self.assertEqual(self.send()["dispatch"], "dispatched")
        self.assertEqual(self.backend.editor.text, "new")
        self.assertIs(self.obj.parent, self.sibling)


if __name__ == "__main__":
    unittest.main()
