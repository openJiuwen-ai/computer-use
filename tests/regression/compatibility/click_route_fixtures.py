# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Synthetic native route offers used by write and permission contract tests."""

from dataclasses import replace

from computer_use.native.contracts import Binding, Channel, ClickOffer, Facts, InputContext, Intent, Knowledge, Primitive

TARGET = Binding("session", "app", "root", "surface", "button")
CLICK = Intent("click", {"button": "left", "count": 1})


def click_route(delivery, admission="ready", preparation=(), *, profile="default", action=None):
    semantic = delivery == "object_action"
    return Primitive(
        delivery,
        "native_action" if semantic else "pointer_sequence",
        Channel.OBJECT if semantic else Channel.POINTER,
        Knowledge.NO if admission == "unavailable" else Knowledge.YES,
        Knowledge.YES if admission == "ready" else Knowledge.NO,
        pending=() if admission == "ready" else ("foreground",) if preparation == ("foreground",) else ("receiver",),
        action=action or ("AXPress" if semantic else None),
        click=ClickOffer(
            "test." + delivery,
            "semantic" if semantic else "pointer",
            delivery,
            admission,
            preparation_options=preparation,
            preference_profile=profile,
        ),
    )

def facts(*routes, **changes):
    return replace(
        Facts(
            TARGET,
            "facts",
            "target",
            Knowledge.YES,
            Knowledge.YES,
            Knowledge.YES,
            (),
            tuple(routes),
            InputContext(),
            platform="macos",
            native_role="AXButton",
            actions_complete=True,
        ),
        **changes,
    )
