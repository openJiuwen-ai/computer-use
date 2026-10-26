"Host delivery callbacks; these records do not execute computer operations."

_action_handler = None


def setActionHandler(handler):
    global _action_handler
    _action_handler = handler


def actionCompleted(receipt):
    try:
        if _action_handler:
            _action_handler(receipt)
    except Exception as error:
        raise RuntimeError(
            (
                f"Action completed, but its receipt could not be displayed: "
                f"{error}"
                f"\nEffect: "
                f"{receipt['target']}"
                f": "
                f"{receipt['detail']}"
                f"\nNext: read the app before continuing; do not replay the action"
            )
        ) from error


_observation_handler = None


def setObservationHandler(handler):
    global _observation_handler
    _observation_handler = handler


def observationCompleted(observation):
    if _observation_handler:
        _observation_handler(dict(kind="observed", observation=observation))


def observationsInvalidated(source):
    if _observation_handler:
        _observation_handler(dict(kind="invalidate", source=source))


def pendingObservationsDiscarded(source):
    if _observation_handler:
        _observation_handler(dict(kind="discard-pending", source=source))
