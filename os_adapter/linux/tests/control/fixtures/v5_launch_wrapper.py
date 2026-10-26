"""Disposable native launch wrapper with a distinct GTK window-owning child."""

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path


def birth(pid):
    return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19]


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--exit-before-window", action="store_true")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    record = dict(
        pid=os.getpid(), birth=birth(os.getpid()), started=time.time(), exitBeforeWindow=args.exit_before_window
    )
    (args.output / "wrapper.json").write_text(json.dumps(record))
    child = subprocess.Popen(
        [
            sys.executable,
            str(Path(__file__).with_name("v5_launch_fixture.py")),
            "--toolkit",
            "gtk3",
            "--windows",
            "1",
            "--output",
            str(args.output),
        ]
    )
    record.update(child=child.pid, childBirth=birth(child.pid))
    (args.output / "wrapper.json").write_text(json.dumps(record))
    if args.exit_before_window:
        # Gio can confirm exec; the child deliberately creates its GUI later.
        time.sleep(0.2)
    else:
        child.wait()
