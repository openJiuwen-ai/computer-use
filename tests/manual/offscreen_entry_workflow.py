# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Targeted offscreen entry acceptance using the real SDK and disposable Qt UI.

Observe the launcher, create the form through its original public click, click
the first genuinely offscreen control once, then click its adjacent control.
This does not replace full title/date/menu-chain acceptance. No preparatory
scrolling, private binding, global input or runtime prewarm is performed.
"""

import argparse
import asyncio
import logging
import tempfile
from pathlib import Path

from reliability_latency_workflow import DEFAULT_PYTHON, ROOT, verify

LOG = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", type=Path, default=DEFAULT_PYTHON)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--offscreen-first", choices=("cloud", "mute"), default="mute")
    options = parser.parse_args()
    folder = options.output or Path(tempfile.mkdtemp(prefix="offscreen-entry-", dir=ROOT / ".artifacts"))
    folder.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    try:
        asyncio.run(verify(folder, options.python, offscreen_first=options.offscreen_first, offscreen_only=True))
    finally:
        LOG.info("Targeted offscreen entry evidence: %s", folder)


if __name__ == "__main__":
    main()
