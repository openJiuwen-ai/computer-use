"""Expose the independent Linux driver sources to native contract tests."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "os_adapter/linux/src"))
