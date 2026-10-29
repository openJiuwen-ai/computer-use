# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"Host delivery callbacks; these records do not execute computer operations."

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
                f"\nNext: read the app before continuing; do not replay the action"
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


def observations_invalidated(source):
    if _observation_handler:
        _observation_handler(dict(kind="invalidate", source=source))


def pending_observations_discarded(source):
    if _observation_handler:
        _observation_handler(dict(kind="discard-pending", source=source))
