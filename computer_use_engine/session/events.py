# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"Host delivery callbacks; these records do not execute computer operations."

from computer_use.identity import WeakIdentityMap

_observation_deliveries = WeakIdentityMap()


def register_observation_delivery(observation, callback):
    """Retain one commitment callback until this exact observation is delivered."""
    _observation_deliveries.set(observation, callback)


def observation_delivered(observation):
    """Commit only a publication actually returned to the SDK or host caller."""
    callback = _observation_deliveries.get(observation)
    if callback is not None:
        _observation_deliveries.delete(observation)
        callback()


# Private variables hold mutable session state.
# pylint: disable-next=invalid-name
_action_handler = None


def set_action_handler(handler):
    global _action_handler
    _action_handler = handler


def action_completed(receipt):
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
            )
        ) from error


# pylint: disable-next=invalid-name
_observation_handler = None


def set_observation_handler(handler):
    global _observation_handler
    _observation_handler = handler


def observation_completed(observation):
    if _observation_handler:
        _observation_handler(dict(kind="observed", observation=observation))
    else:
        observation_delivered(observation["state"])


def explicit_observation_completed(observation):
    """Preserve the capture position requested by one explicit read."""
    observation_completed({**observation, "explicitRead": True})


def action_observation_completed(result):
    """Deliver a read-suffix gap without replacing the original result or error."""
    try:
        if _observation_handler:
            _observation_handler(dict(kind="action-observation", result=result))
    except Exception as error:
        from computer_use.diagnostics import diagnostic
        from computer_use.errors import ComputerUseError

        failure = isinstance(result, ComputerUseError)

        diagnostic(
            dict(
                kind="action_observation_delivery_failed",
                status=result.effect if failure else result["status"],
                observationStatus=result.observation_status if failure else result["observation_status"],
                errorType=type(error).__name__,
                detail="The original action outcome and related observation error remain available to the caller.",
            )
        )


def observations_invalidated(source):
    if _observation_handler:
        _observation_handler(dict(kind="invalidate", source=source))


def observations_before_write(source):
    """Publish earlier captured facts before the next operation can change them.

    Observation delivery is a history of facts, not a cache cleared by a later
    action's failure. A host may buffer reads, but must retain their position
    before this write, including when that write is refused or interrupted.
    """
    if _observation_handler:
        _observation_handler({"kind": "before-write", "source": source})
