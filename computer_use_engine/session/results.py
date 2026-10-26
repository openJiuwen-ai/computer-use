"""Action receipt identity and captured-text delivery callbacks."""

from computer_use._identity import FrozenRecord, WeakIdentityMap

_actions = WeakIdentityMap()
_textHandler = None


def actionResult(receipt):
    result = FrozenRecord(dict(status=receipt["status"], detail=receipt["detail"]))
    _actions.set(result, receipt)
    return result


def resultReceipt(value):
    return _actions.get(value)


def setTextPageHandler(handler):
    global _textHandler
    _textHandler = handler


def textPageCompleted(source, result):
    if _textHandler:
        _textHandler(source, result)
