"""Encode validated native observations without the model presentation formatter.

Native observation diagnostics contain only JSON values, already validated by
the connection. Walking every scalar through presentation string/number rules
again can consume seconds of an observation's component deadline. The standard
encoder preserves their values and structure in both IPC and the journal.
Other records retain their existing serializer, including immutable mappings,
exception records, and the established non-finite-number representation.
"""

import json

from computer_use._json import dumps as legacyDumps


def dumps(value):
    event = value.get("diagnostic", value) if isinstance(value, dict) else None
    if (
        isinstance(event, dict)
        and event.get("kind") == "command_end"
        and event.get("method") == "inspect"
        and isinstance(event.get("result"), dict)
        and event["result"].get("kind") == "observation"
    ):
        try:
            return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        except (TypeError, ValueError):
            # Keep the diagnostic callback tolerant of non-native/custom
            # records rather than losing evidence to an optimization.
            pass
    return legacyDumps(value)
