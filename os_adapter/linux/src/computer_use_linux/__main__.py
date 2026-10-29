# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import argparse
import contextlib
import json
import os
import signal
import sys

from computer_use_linux.runtime.resources import check_permissions
from computer_use_linux.runtime.server import Server

"Executable protocol endpoint. stdout contains JSON Lines only."


def native_backend(mode, stopped):
    from computer_use_linux.runtime.native_backend import NativeBackend

    return NativeBackend(mode, stopped)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check-permissions", action="store_true")
    parser.add_argument("--request-permissions", action="store_true")
    parser.add_argument(
        "--protocol-version",
        type=int,
        choices=(6,),
        default=None,
        help="Protocol for resource checks (default: 6). This driver accepts protocol 6 only.",
    )
    args = parser.parse_args()
    if args.protocol_version is not None and not (args.check_permissions or args.request_permissions):
        parser.error(
            ("--protocol-version is only used with a permission check; server sessions negotiate their version")
        )
    output = sys.stdout
    # Platform diagnostics are never protocol frames.
    with contextlib.redirect_stdout(sys.stderr):
        if args.check_permissions or args.request_permissions:
            result = check_permissions(args.protocol_version or 6)
            if args.request_permissions:
                result["details"].append(
                    (
                        "Run in the logged-in user's session D-Bus environment; X11 provides "
                        "additional capture/input. This command does not change "
                        "permissions."
                    )
                )
            output.write(json.dumps(result) + "\n")
            return 0
        # Validate hello before importing or initializing native resources.
        server = Server(native_backend, output=output)
        for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            signal.signal(signum, lambda *_: server.stopped.set())
        # Also stop if the host is killed without a graceful stdin close.
        if sys.platform == "linux":
            import ctypes

            parent = os.getppid()
            ctypes.CDLL(None).prctl(1, signal.SIGTERM, 0, 0, 0)
            if os.getppid() != parent:
                return 1
        return server.run()


if __name__ == "__main__":
    raise SystemExit(main())
