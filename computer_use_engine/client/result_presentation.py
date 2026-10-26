"""Client formatting of action/text results; provenance is session-owned."""

from computer_use._identity import FrozenRecord
from computer_use.interaction.read.facts.text_source import textSourceLabel
from computer_use.session.results import (
    actionResult as actionResult,
    resultReceipt as resultReceipt,
    setTextPageHandler as setTextPageHandler,
    textPageCompleted,
)


def capturedText(page, source):
    result = FrozenRecord(page, display=textSourceLabel(source) + "\n" + repr(dict(page)))
    textPageCompleted(source, result)
    return result
