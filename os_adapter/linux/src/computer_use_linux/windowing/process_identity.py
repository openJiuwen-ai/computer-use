# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Process generation evidence shared by directory and explicit launch."""


def process_birth(pid):
    """A live process generation; an exited zombie is not a running instance."""
    # Exact types preserve scalar validation and identity contracts.
    # pylint: disable-next=unidiomatic-typecheck
    if type(pid) is not int or not 0 < pid <= 2147483647:
        return ""
    try:
        with open(f"/proc/{pid}/stat") as stream:
            fields = stream.read(16384).rsplit(")", 1)[1].split()
        return fields[19] if fields[0] not in ("Z", "X", "x") else ""
    except (OSError, IndexError):
        return ""
