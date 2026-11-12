# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Load the installed Rust accessibility read engine when its first consumer needs it."""

from functools import cache


@cache
def require_read_engine():
    try:
        from . import _ax_read
    except ImportError as error:
        raise RuntimeError("The Rust accessibility read engine is missing. Rebuild the native SDK.") from error
    if getattr(_ax_read, "NATIVE_READ_CONTRACT", None) != 1:
        raise RuntimeError("The Rust accessibility read engine is outdated. Rebuild the native SDK.")
    return _ax_read
