"""X server resource identity for clipboard requestors without toolkit metadata."""

import unittest
from types import SimpleNamespace as NS

import backends.linux.test_linux_native as test_linux_native  # noqa: F401 - Platform package path.
from computer_use_linux.contracts.protocol import Failure
from computer_use_linux.system.x11 import X11


class Display:
    def __init__(self):
        self.extension = True
        self.version = (1, 2)
        self.pids = {}
        # Different clients can have different server-provided masks. Never use
        # this driver's resource mask to infer some other client's allocation.
        self.clients = [
            NS(resource_base=0x200000, resource_mask=0x0FFFFF),
            NS(resource_base=0x400000, resource_mask=0x1FFFFF),
        ]
        self.calls = []
        self.after_query = None

    def has_extension(self, name):
        return self.extension

    def res_query_version(self):
        return NS(server_major=self.version[0], server_minor=self.version[1])

    def res_query_clients(self):
        self.calls.append(("clients",))
        return NS(clients=self.clients)

    def res_query_client_ids(self, specs):
        self.calls.append(("pids", specs))
        rows = []
        for spec in specs:
            if spec["client"] in self.pids:
                rows.append(NS(spec=NS(**spec), value=[self.pids[spec["client"]]]))
        if self.after_query:
            self.after_query()
        return NS(ids=rows)


class ClipboardConsumerTests(unittest.TestCase):
    def setUp(self):
        self.x = X11.__new__(X11)
        self.x.d = Display()
        self.x.root = NS(id=1)
        self.target, self.auxiliary, self.foreign = 0x200010, 0x200020, 0x400010
        self.w = NS(key=self.target, pid=42, process_start="generation")
        self.closed, self.generation = False, "generation"
        self.properties = {}
        self.windows = {self.target, self.auxiliary, self.foreign}
        self.x.validate = self.validate
        self.x.process_start = lambda pid: self.generation if pid == 42 else "other"
        self.x.native = self.native
        self.x.prop = lambda window, name, default=None: self.properties.get((window.id, name), default)
        self.consumer = self.x.clipboard_consumer(object())

    def validate(self, instance):
        if self.closed:
            raise Failure("Window closed", "stale_target")
        return self.w

    def native(self, xid):
        def exists():
            if xid not in self.windows:
                raise RuntimeError("BadWindow")
            return NS()

        return NS(id=xid, get_attributes=exists, query_tree=lambda: NS(parent=self.x.root))

    def test_auxiliary_root_child_without_pid_or_leader_uses_server_pid(self):
        self.x.d.pids[self.auxiliary] = 42
        self.assertTrue(self.consumer(self.auxiliary))
        self.assertEqual(self.properties, {})
        self.assertEqual(self.x.d.calls, [("pids", [dict(client=self.auxiliary, mask=2)])])

    def test_same_process_other_x_connection_uses_pid_not_same_client_guess(self):
        self.x.d.pids[self.foreign] = 42
        self.assertTrue(self.consumer(self.foreign))

    def test_xres_one_zero_can_prove_same_client_with_each_server_range(self):
        self.x.d.version = (1, 0)
        self.assertTrue(self.consumer(self.auxiliary))
        self.assertFalse(self.consumer(self.foreign))
        self.assertNotIn("pids", [call[0] for call in self.x.d.calls])

    def test_unavailable_local_pid_can_still_use_confirmed_same_x_client(self):
        self.assertTrue(self.consumer(self.auxiliary))
        self.assertFalse(self.consumer(self.foreign))

    def test_conflicting_server_pid_cannot_fall_back_to_claimed_property_or_range(self):
        self.x.d.pids[self.auxiliary] = 84
        self.properties[(self.auxiliary, "_NET_WM_PID")] = [42]
        self.assertFalse(self.consumer(self.auxiliary))
        self.assertNotIn("clients", [call[0] for call in self.x.d.calls])

    def test_pid_reuse_is_rejected_before_and_after_native_query(self):
        self.x.d.pids[self.auxiliary] = 42
        self.generation = "reused"
        self.assertFalse(self.consumer(self.auxiliary))
        self.generation = "generation"
        self.x.d.after_query = lambda: setattr(self, "generation", "reused")
        self.assertFalse(self.consumer(self.auxiliary))

    def test_closed_target_and_destroyed_requestor_are_rejected(self):
        self.x.d.pids[self.auxiliary] = 42
        self.closed = True
        self.assertFalse(self.consumer(self.auxiliary))
        self.closed = False
        self.windows.remove(self.auxiliary)
        self.assertFalse(self.consumer(self.auxiliary))

    def test_target_closing_during_server_query_is_not_accepted(self):
        self.x.d.pids[self.auxiliary] = 42
        self.x.d.after_query = lambda: setattr(self, "closed", True)
        self.assertFalse(self.consumer(self.auxiliary))

    def test_server_root_and_unknown_or_ambiguous_client_ranges_are_not_identity(self):
        self.assertFalse(self.consumer(1))
        self.x.d.clients.append(NS(resource_base=0x200000, resource_mask=0x00FFFF))
        self.assertFalse(self.consumer(self.auxiliary))
        self.x.d.clients = []
        self.assertFalse(self.consumer(self.auxiliary))

    def test_old_server_preserves_client_leader_route_without_name_matching(self):
        self.x.d.extension = False
        self.properties[(self.auxiliary, "WM_CLIENT_LEADER")] = [self.target]
        self.properties[(self.target, "_NET_WM_PID")] = [42]
        self.assertTrue(self.consumer(self.auxiliary))
        self.assertFalse(self.consumer(self.foreign))
        self.assertEqual(self.x.d.calls, [])

    def test_metadata_fallback_rechecks_generation_after_parent_queries(self):
        self.x.d.extension = False
        self.properties[(self.auxiliary, "_NET_WM_PID")] = [42]
        self.generation = "reused"
        self.assertFalse(self.consumer(self.auxiliary))


if __name__ == "__main__":
    unittest.main()
