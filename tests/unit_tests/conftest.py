# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Fixtures shared by tests that exercise the installed Rust read engine."""

from importlib.util import find_spec

import pytest

from computer_use.interaction.read.accessibility._native import require_read_engine


@pytest.fixture
def native_read():
    if find_spec("computer_use.interaction.read.accessibility._ax_read") is None:
        pytest.skip("requires the optional Rust accessibility extension")
    return require_read_engine()
