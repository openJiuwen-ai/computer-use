# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Sign the macOS driver and verify the copy shipped in a release wheel."""

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory


def signing_identity():
    identity = os.environ.get("CUA_MACOS_SIGN_IDENTITY")
    if identity is None:
        try:
            identity = (Path.home() / ".computer-use/macos-signing-identity").read_text(encoding="utf-8")
        except FileNotFoundError:
            return "-"
    if not identity.strip():
        raise ValueError("CUA macOS signing identity is empty. Set it to a keychain identity or '-' for ad hoc.")
    return identity.strip()


def release_requirement(team):
    if not re.fullmatch("[A-Z0-9]{10}", team):
        raise ValueError("Supply the 10-character Apple Developer Team ID for the official release.")
    return (
        '=identifier "com.openjiuwen.cua" and anchor apple generic '
        'and certificate 1[field.1.2.840.113635.100.6.2.6] exists '
        'and certificate leaf[field.1.2.840.113635.100.6.1.13] exists '
        f'and certificate leaf[subject.OU] = "{team}"'
    )


def sign_bundle(bundle):
    team = os.environ.get("CUA_MACOS_RELEASE_TEAM_ID")
    identity = signing_identity()
    options = []
    verify = []
    if team is not None:
        verify = ["-R", release_requirement(team)]
        if identity == "-" or not os.environ.get("CUA_MACOS_SIGN_IDENTITY"):
            raise ValueError("Official builds require an explicit Developer ID Application in CUA_MACOS_SIGN_IDENTITY.")
        options = ["--options", "runtime", "--timestamp"]
    subprocess.run(
        ["/usr/bin/codesign", "--force", "--sign", identity, "--identifier", "com.openjiuwen.cua", *options, bundle],
        check=True,
    )
    subprocess.run(["/usr/bin/codesign", "--verify", "--strict", *verify, bundle], check=True)


def verify_release_wheel(wheel, team):
    if sys.platform != "darwin":
        raise ValueError("Run release signature verification on a Mac.")
    requirement = release_requirement(team)
    with TemporaryDirectory(prefix="cua-verify-wheel-") as temporary:
        subprocess.run(["/usr/bin/ditto", "-x", "-k", wheel, temporary], check=True)
        bundle = Path(temporary) / "computer_use/_resources/os_adapter/macos/.build/Jiuwen CUA.app"
        subprocess.run(["/usr/bin/codesign", "--verify", "--strict", "-R", requirement, bundle], check=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Verify the Developer ID of the macOS driver in each release wheel.")
    parser.add_argument("--team-id", required=True)
    parser.add_argument("wheel", type=Path, nargs="+")
    args = parser.parse_args()
    try:
        for path in args.wheel:
            verify_release_wheel(path, args.team_id)
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"Release wheel verification failed: {error}\n")
