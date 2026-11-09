"""Session-owned delivered baselines with bounded memory and lossless spill.

The memory budget is a placement policy, not permission to forget what the
caller has seen. Spill files contain compact comparison facts and are removed
on source invalidation, reset, or worker shutdown.
"""

from collections import OrderedDict
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from zlib import compress, decompress

from computer_use.interaction.read.delivery.diff.baseline import ComparisonDocument
from computer_use.json import dumps, loads, utf8


class DeliveredBaselineStore:
    def __init__(self, max_bytes, max_scopes):
        self.max_bytes, self.max_scopes = max_bytes, max_scopes
        self.memory, self.spilled = OrderedDict(), {}
        self.bytes = 0
        self.directory = None

    def get(self, key):
        if key in self.memory:
            self.memory.move_to_end(key)
            return self.memory[key]
        path = self.spilled.get(key)
        if path is None:
            return None
        baseline = loads(decompress(path.read_bytes()).decode("utf-8"))
        if baseline["document"] is not None:
            baseline["document"] = ComparisonDocument.from_data(baseline["document"])
        return baseline

    def _spill(self, key, baseline):
        if self.directory is None:
            self.directory = TemporaryDirectory(prefix="computer-use-delivered-")
        path = Path(self.directory.name) / sha256(key.encode()).hexdigest()
        data = dict(baseline)
        if data["document"] is not None:
            data["document"] = data["document"].to_data()
        pending = path.with_suffix(".tmp")
        pending.write_bytes(compress(utf8(dumps(data))))
        pending.replace(path)
        self.spilled[key] = path

    def publish(self, key, baseline):
        size = baseline["bytes"]
        if size > self.max_bytes or self.max_scopes < 1:
            self._spill(key, baseline)
            previous = self.memory.pop(key, None)
            if previous is not None:
                self.bytes -= previous["bytes"]
            return
        # Prepare each spill before retiring its in-memory copy. A serialization
        # failure cannot erase the preceding delivered baseline.
        old_size = self.memory.get(key, {}).get("bytes", 0)
        while self.memory and (
            len(self.memory) - (key in self.memory) >= self.max_scopes or self.bytes - old_size + size > self.max_bytes
        ):
            victim = next(item for item in self.memory if item != key)
            self._spill(victim, self.memory[victim])
            self.bytes -= self.memory.pop(victim)["bytes"]
        previous = self.memory.pop(key, None)
        self.memory[key] = baseline
        self.bytes += size - (previous["bytes"] if previous is not None else 0)
        path = self.spilled.pop(key, None)
        if path:
            path.unlink()

    def invalidate(self, source_id=None):
        keys = set(self.memory) | set(self.spilled)
        for key in keys:
            if source_id is not None and loads(key)[0] != source_id:
                continue
            previous = self.memory.pop(key, None)
            if previous is not None:
                self.bytes -= previous["bytes"]
            path = self.spilled.pop(key, None)
            if path:
                path.unlink()
        if not self.spilled and self.directory is not None:
            self.directory.cleanup()
            self.directory = None

    def close(self):
        self.invalidate()
