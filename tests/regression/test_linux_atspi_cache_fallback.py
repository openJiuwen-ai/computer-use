# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Refreshing an accessible never disables libatspi's cache.

On libatspi without clear_cache_single (2.44, Ubuntu 22.04), the old fallback
set the object's cache mask to NONE. libatspi then answered its own
state-change handling over D-Bus while dispatching a pending call, and both
waited on the same connection: application discovery hung until the request
timed out. Recursive clear_cache refreshes the same object without that.
"""

import importlib
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest


@pytest.fixture(scope="module")
def accessibility():
    source = Path(__file__).resolve().parents[2] / "os_adapter/linux/src"
    with patch.object(sys, "path", [str(source), *sys.path]):
        return importlib.import_module("computer_use_linux.objects.native_accessibility")


def _provider(accessible):
    return SimpleNamespace(_a=SimpleNamespace(Accessible=accessible, Cache=SimpleNamespace(NONE=0)))


def test_older_libatspi_clears_the_cache_instead_of_disabling_it(accessibility):
    obj = object()
    accessible = Mock(spec=["clear_cache", "set_cache_mask"])  # no clear_cache_single, as in libatspi 2.44

    accessibility.NativeAccessibility._clear_cache(_provider(accessible), obj)

    accessible.clear_cache.assert_called_once_with(obj)
    accessible.set_cache_mask.assert_not_called()


def test_newer_libatspi_clears_only_the_object(accessibility):
    obj = object()
    accessible = Mock(spec=["clear_cache_single", "clear_cache", "set_cache_mask"])

    accessibility.NativeAccessibility._clear_cache(_provider(accessible), obj)

    accessible.clear_cache_single.assert_called_once_with(obj)
    accessible.clear_cache.assert_not_called()
    accessible.set_cache_mask.assert_not_called()
