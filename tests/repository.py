"""Filesystem anchor shared by tests regardless of their mirror directory."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TESTS = ROOT / "tests"
FIXTURES = TESTS / "fixtures"
