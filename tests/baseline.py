"""Independent expected results frozen before retiring the TypeScript runtime."""

import gzip
import hashlib
import json
from copy import deepcopy
from functools import cache

from repository import TESTS

DIRECTORY = TESTS / "baselines"


@cache
def load(name):
    filename = name + ".json.gz"
    manifest = json.loads((DIRECTORY / "manifest.json").read_text(encoding="utf-8"))
    data = (DIRECTORY / filename).read_bytes()
    assert hashlib.sha256(data).hexdigest() == manifest["fixtures"][filename]["sha256"], filename
    return json.loads(gzip.decompress(data))


def baseline(name, requests):
    """Unknown inputs fail explicitly; expectations never come from current code."""
    records = load(name)
    results = []
    for request in requests:
        serialized = json.dumps(request, ensure_ascii=True, separators=(",", ":"))
        key = hashlib.sha256(serialized.encode()).hexdigest()
        if key not in records:
            raise AssertionError(
                f"No historical {name} baseline for {key}; add an explicit behavioral assertion for this new case."
            )
        results.append(deepcopy(records[key]["expected"]))
    return results
