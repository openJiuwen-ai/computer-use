# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Run the local CodeCheck checks on Python files supplied by pre-commit."""

import subprocess
import sys


def main(files: list[str]) -> int:
    if not files:
        return 0
    command = [sys.executable, "-I", "-m", "ruff", "check", "--no-fix", "--force-exclude", "--output-format=concise"]
    return subprocess.run([*command, "--", *files], check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
