"""Client formatting of action/text results; provenance is session-owned."""

from computer_use._identity import FrozenRecord
from computer_use.interaction.read.facts.text_source import text_source_label
from computer_use.session.results import (
    action_result as action_result,
)
from computer_use.session.results import (
    result_receipt as result_receipt,
)
from computer_use.session.results import (
    set_text_page_handler as set_text_page_handler,
)
from computer_use.session.results import (
    text_page_completed,
)


def captured_text(page, source):
    result = FrozenRecord(page, display=text_source_label(source) + "\n" + repr(dict(page)))
    text_page_completed(source, result)
    return result
