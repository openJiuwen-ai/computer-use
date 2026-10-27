"""EWMH window identity versus toolkit helper windows, with native X11 substituted."""

import unittest
from types import SimpleNamespace as NS

from computer_use_linux.objects.registry import Window
from computer_use_linux.system.x11 import X11


def window(xid, *, mapped=True, popup=False):
    return NS(id=xid, get_attributes=lambda: NS(map_state=2 if mapped else 0, override_redirect=popup))


class LinuxWindowCatalogueTests(unittest.TestCase):
    @staticmethod
    def read(managed, children):
        desktop = X11.__new__(X11)
        desktop.X = NS(IsViewable=2)
        desktop.pump = lambda: None
        desktop.prop = lambda *_: managed
        desktop.root = NS(query_tree=lambda: NS(children=children))
        desktop.retire = lambda _: None
        desktop.info = lambda xid: Window(xid, "Window", "App", 123, (0, 0, 300, 100))
        notes = []
        windows = desktop.windows(notes=notes)
        return [w.key for w in windows], notes

    def test_empty_managed_directory_does_not_turn_hidden_leader_into_working_window(self):
        ids, notes = self.read([], [window(10, mapped=False)])
        self.assertEqual(ids, [])
        self.assertEqual(notes, [])

    def test_mapped_unmanaged_normal_window_does_not_override_wm_catalogue(self):
        self.assertEqual(self.read([], [window(10)])[0], [])

    def test_visible_override_redirect_popup_still_appears_with_empty_directory(self):
        self.assertEqual(self.read([], [window(10, popup=True)])[0], [10])

    def test_minimized_managed_document_remains_in_directory(self):
        ids, notes = self.read([10], [window(10, mapped=False), window(20, mapped=False)])
        self.assertEqual(ids, [10])
        self.assertEqual(notes, [])

    def test_missing_wm_catalogue_returns_mapped_windows_with_explicit_gap(self):
        ids, notes = self.read(None, [window(10), window(20, mapped=False), window(30, popup=True)])
        self.assertEqual(ids, [10, 30])
        self.assertEqual(len(notes), 1)
        self.assertIn("No EWMH client catalogue", notes[0])

    def test_popup_is_not_duplicated_if_wm_already_lists_it(self):
        self.assertEqual(self.read([10], [window(10, popup=True)])[0], [10])


if __name__ == "__main__":
    unittest.main()
