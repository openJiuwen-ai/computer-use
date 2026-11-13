# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Load the installed Rust accessibility read engine when its first consumer needs it."""

from functools import cache

from computer_use.paths import installed_read_engine_unavailable_reason


@cache
def require_read_engine():
    try:
        from . import _ax_read
    except ImportError as error:
        reason = installed_read_engine_unavailable_reason()
        if reason == "native_build_failed":
            detail = "could not be built during installation"
        elif reason == "unsupported_platform":
            detail = "is unavailable for this platform or Python interpreter"
        else:
            detail = "is missing"
        raise RuntimeError(f"The Rust accessibility read engine {detail}. Reinstall the native SDK.") from error
    if getattr(_ax_read, "NATIVE_READ_CONTRACT", None) != 1:
        raise RuntimeError("The Rust accessibility read engine is outdated. Rebuild the native SDK.")
    return _ax_read
