# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Run a native build over SSH; keep its output in the GitCode job workspace."""

import argparse
import base64
import errno
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from backend_artifacts import TARGETS, artifact_name, inspect_archive
from console import write_result

ROOT = Path(__file__).resolve().parents[1]

# stdout is exclusively the resulting archive; build output goes to stderr.
# The source zip is the checked-out Git commit, with no tokens, keys or venvs.
BOOTSTRAP = """
import io, os, pathlib, subprocess, sys, tempfile, zipfile
with tempfile.TemporaryDirectory(prefix='computer-use-native-') as temporary:
    root = pathlib.Path(temporary)
    zipfile.ZipFile(io.BytesIO(sys.stdin.buffer.read())).extractall(root)
    environment = dict(os.environ, COMPUTER_USE_BUILD_COMMIT={commit!r})
    subprocess.run(
        [{uv!r}, 'run', '--python', '3.12', '--no-dev', '--group', 'build',
         'python', 'scripts/distribution.py', 'backend', '--target', {target!r},
         '--output', '.artifacts/backends'],
        cwd=root, env=environment, stdout=sys.stderr, stderr=sys.stderr, check=True, timeout=3600)
    archives = list((root / '.artifacts/backends').glob('*.zip'))
    if len(archives) != 1:
        raise RuntimeError('Native build must produce exactly one archive')
    sys.stdout.buffer.write(archives[0].read_bytes())
"""


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", choices=TARGETS, required=True)
    parser.add_argument("--output", type=Path, default=Path(".artifacts/backends"))
    args = parser.parse_args(argv)
    host = os.environ.get("BUILDER_HOST", "")
    key = os.environ.get("BUILDER_SSH_KEY", "")
    known_hosts = os.environ.get("BUILDER_KNOWN_HOSTS", "")
    valid_host = re.fullmatch(r"[\w@.\[\]:-]+", host) and not host.startswith("-")
    if not valid_host or not key or not known_hosts:
        parser.error("Configure BUILDER_HOST, BUILDER_SSH_KEY and BUILDER_KNOWN_HOSTS in pipeline secrets.")
    python = os.environ.get("BUILDER_PYTHON") or ("python" if args.target.startswith("windows-") else "python3")
    if not re.fullmatch(r"[\w./:-]+", python):
        parser.error("BUILDER_PYTHON must be a command name or path without spaces.")
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    source = subprocess.check_output(["git", "archive", "--format=zip", "HEAD"], cwd=ROOT)
    bootstrap = BOOTSTRAP.format(commit=commit, target=args.target, uv=os.environ.get("BUILDER_UV") or "uv")
    encoded = base64.b64encode(bootstrap.encode()).decode("ascii")
    # A single quoted Python command works with both POSIX and Windows SSH shells.
    command = f"{python} -c \"import base64;exec(base64.b64decode('{encoded}'))\""
    with tempfile.TemporaryDirectory(prefix="computer-use-ssh-") as temporary:
        temporary = Path(temporary)
        identity, hosts = temporary / "identity", temporary / "known_hosts"
        identity.write_text(key.rstrip() + "\n", encoding="utf-8")
        hosts.write_text(known_hosts.rstrip() + "\n", encoding="utf-8")
        identity.chmod(0o600)
        hosts.chmod(0o600)
        ssh = shutil.which("ssh")
        if ssh is None:
            raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), "ssh")
        result = subprocess.run(
            [
                os.path.abspath(ssh),
                "-T",
                "-o",
                "BatchMode=yes",
                "-o",
                "IdentitiesOnly=yes",
                "-o",
                "StrictHostKeyChecking=yes",
                "-o",
                f"UserKnownHostsFile={hosts}",
                "-o",
                "ConnectTimeout=30",
                "-i",
                str(identity),
                "-p",
                str(int(os.environ.get("BUILDER_PORT") or "22")),
                host,
                command,
            ],
            input=source,
            stdout=subprocess.PIPE,
            check=True,
            timeout=3900,
        )
        pending = temporary / "backend.zip"
        pending.write_bytes(result.stdout)
        inspect_archive(pending, root=ROOT, target=args.target)
        args.output.mkdir(parents=True, exist_ok=True)
        output = args.output / (artifact_name(ROOT, args.target) + ".zip")
        output.write_bytes(result.stdout)
        write_result(sys.stdout, str(output))


if __name__ == "__main__":
    main()
