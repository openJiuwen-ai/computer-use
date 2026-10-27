"""Expected Python guidance changes relative to the original TypeScript SDK.

Do not normalize node text, references, whitespace, action names or raw DTOs.
Only synthesized API guidance changes from JavaScript to Python syntax, plus
the Python-only content continuation option. Protocol and effects stay exact.
"""

import re


def pythonHints(value):
    if isinstance(value, list):
        return [pythonHints(v) for v in value]
    if isinstance(value, dict):
        return {k: pythonHints(v) for k, v in value.items()}
    if not isinstance(value, str):
        return value
    value = re.sub(
        "(^Tree: (?:diff|unchanged) \\(not full; )read\\(\\{full:true\\}\\)",
        '\\1read({"full": True})',
        value,
        flags=re.M,
    )
    value = re.sub(
        '(^\\s*Continue: element\\("\\d+"\\)\\.read\\()\\{ offset: (\\d+) \\}', '\\1{"offset": \\2}', value, flags=re.M
    )
    translations = {
        "Computer.read expects { full?: boolean }": "Computer.read expects options with an optional bool full",
        "omit options or set full:true": 'omit options or use {"full": True}',
        "Element.typeText expects { replace?: boolean }": "Element.typeText expects options with an optional bool replace",
        "set replace: true to replace the input contents, or omit it to insert": 'use {"replace": True} to replace the input contents, or omit it to insert',
        "Element.read expects { offset?: nonnegative integer }": "Element.read expects an optional offset or cursor, not both",
        ("omit options to inspect this element; use the continuation offset printed in its subtree"): (
            "omit options to inspect this element; copy the continuation printed in its result"
        ),
    }
    # Error.message embeds these exact detail/next sentences.
    for old, new in translations.items():
        value = value.replace(old, new)
    return value
